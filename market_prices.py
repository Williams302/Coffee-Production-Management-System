from collections import defaultdict

from flask import abort, flash, redirect, render_template, request, url_for

from sales import whole_amount


PRICE_FIELDS = ("price_date", "coffee_type", "coffee_form", "price_ugx_per_kg", "market", "notes")


def migrate_market_prices(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
            return
        db.execute("ALTER TABLE market_prices RENAME TO market_prices_before_v5")
        db.execute("""
            CREATE TABLE market_prices (
                id INTEGER PRIMARY KEY,
                market TEXT NOT NULL CHECK (length(trim(market)) > 0),
                coffee_type TEXT NOT NULL CHECK (length(trim(coffee_type)) > 0),
                price_date TEXT NOT NULL CHECK (length(trim(price_date)) > 0),
                price_ugx_per_kg INTEGER NOT NULL CHECK (price_ugx_per_kg >= 0),
                coffee_form TEXT,
                notes TEXT,
                CHECK (coffee_form IS NULL OR (
                    coffee_form IN ('Cherry', 'Parchment', 'Green Beans')
                    AND coffee_type IN ('Arabica', 'Robusta')
                    AND date(price_date, '+0 days') IS price_date
                )),
                UNIQUE (market, coffee_type, coffee_form, price_date)
            ) STRICT
        """)
        db.execute("""
            INSERT INTO market_prices (id, market, coffee_type, price_date, price_ugx_per_kg)
            SELECT id, market, coffee_type, price_date, price_ugx_per_kg FROM market_prices_before_v5
        """)
        db.execute("DROP TABLE market_prices_before_v5")
        db.execute("""
            CREATE UNIQUE INDEX market_prices_legacy_unique
            ON market_prices (market, coffee_type, price_date) WHERE coffee_form IS NULL
        """)
        db.execute("""
            CREATE INDEX market_prices_comparison
            ON market_prices (coffee_type, coffee_form, price_date DESC, id DESC)
        """)
        db.execute("PRAGMA user_version = 5")


def latest_market_price(db):
    return db.execute("""
        SELECT * FROM market_prices WHERE coffee_form IS NOT NULL
        ORDER BY price_date DESC, id DESC LIMIT 1
    """).fetchone()


def sale_comparisons(db, market, start="", end=""):
    return db.execute("""
        SELECT s.id, s.sale_date, s.buyer, p.name AS plot_name, p.variety,
            COALESCE(s.coffee_form, h.coffee_form) AS coffee_form, s.unit_price_ugx,
            m.market, m.price_date, m.price_ugx_per_kg,
            s.unit_price_ugx - m.price_ugx_per_kg AS difference_ugx
        FROM sales AS s
        LEFT JOIN harvests AS h ON h.id = s.harvest_id
        JOIN coffee_plots AS p ON p.id = COALESCE(s.plot_id, h.plot_id)
        LEFT JOIN market_prices AS m ON m.id = (
            SELECT candidate.id
            FROM market_prices AS candidate
            WHERE candidate.coffee_type = p.variety
                AND candidate.coffee_form = COALESCE(s.coffee_form, h.coffee_form)
                AND candidate.price_date <= s.sale_date
                AND (:market = '' OR candidate.market = :market)
                AND s.unit_price_ugx IS NOT NULL
            ORDER BY candidate.price_date DESC, candidate.id DESC
            LIMIT 1
        )
        WHERE (:start = '' OR s.sale_date >= :start) AND (:end = '' OR s.sale_date <= :end)
        ORDER BY s.sale_date DESC, s.id DESC
    """, {"market": market, "start": start, "end": end}).fetchall()


def validate_price(values, varieties, coffee_forms, valid_date):
    errors = {}
    if not valid_date(values["price_date"]):
        errors["price_date"] = "Enter a valid price date."
    if values["coffee_type"] not in varieties:
        errors["coffee_type"] = "Choose Arabica or Robusta."
    if values["coffee_form"] not in coffee_forms:
        errors["coffee_form"] = "Choose a coffee form from the list."
    price = whole_amount(values["price_ugx_per_kg"])
    if price is None:
        errors["price_ugx_per_kg"] = "Enter a non-negative whole UGX price within the supported range."
    if not values["market"] or len(values["market"]) > 150 or "\0" in values["market"]:
        errors["market"] = "Enter a market or source name using up to 150 characters."
    if len(values["notes"]) > 2000 or "\0" in values["notes"]:
        errors["notes"] = "Enter notes using up to 2,000 characters."
    return dict(values, price_ugx_per_kg=price, notes=values["notes"] or None), errors


def register_market_price_routes(app, get_db, check_csrf, valid_date, varieties, coffee_forms):
    @app.get("/market-prices")
    def market_prices():
        db = get_db()
        markets = [row[0] for row in db.execute("SELECT DISTINCT market FROM market_prices ORDER BY market COLLATE NOCASE")]
        filters = {key: request.args.get(key, "") for key in
                   ("market", "coffee_type", "coffee_form", "start_date", "end_date")}
        errors = []
        for key, options, message in (
            ("market", markets, "Choose a saved market or source."),
            ("coffee_type", varieties, "Choose Arabica or Robusta."),
            ("coffee_form", coffee_forms, "Choose a coffee form from the list."),
        ):
            if filters[key] and filters[key] not in options:
                errors.append(message)
        start, end = filters["start_date"], filters["end_date"]
        if (start and not valid_date(start)) or (end and not valid_date(end)):
            errors.append("Enter valid start and end dates.")
        elif start and end and start > end:
            errors.append("The end date must be on or after the start date.")
        records = [] if errors else db.execute("""
            SELECT * FROM market_prices
            WHERE (:market = '' OR market = :market)
                AND (:coffee_type = '' OR coffee_type = :coffee_type)
                AND (:coffee_form = '' OR coffee_form = :coffee_form)
                AND (:start_date = '' OR price_date >= :start_date)
                AND (:end_date = '' OR price_date <= :end_date)
            ORDER BY price_date DESC, id DESC
        """, filters).fetchall()
        series = defaultdict(list)
        for record in reversed(records):
            if record["coffee_form"] is not None:
                key = (record["market"], record["coffee_type"], record["coffee_form"])
                series[key].append([record["price_date"], str(record["price_ugx_per_kg"])])
        chart_series = [{"label": " · ".join(key), "points": points} for key, points in series.items()]
        return render_template(
            "market_prices.html", records=records, markets=markets, filters=filters, errors=errors,
            varieties=varieties, coffee_forms=coffee_forms, chart_series=chart_series,
            comparisons=[] if errors else sale_comparisons(db, filters["market"]),
        ), 400 if errors else 200

    @app.route("/market-prices/new", methods=("GET", "POST"))
    @app.route("/market-prices/<int:price_id>/edit", methods=("GET", "POST"))
    def market_price_form(price_id=None):
        db = get_db()
        values, errors = {}, {}
        if price_id is not None:
            record = db.execute("SELECT * FROM market_prices WHERE id = ?", (str(price_id),)).fetchone()
            if record is None:
                abort(404, description="This market price could not be found.")
            values = dict(record)
        if request.method == "POST":
            check_csrf()
            values = {field: request.form.get(field, "").strip() for field in PRICE_FIELDS}
            parameters, errors = validate_price(values, varieties, coffee_forms, valid_date)
            if not errors:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    duplicate = db.execute("""
                        SELECT id FROM market_prices
                        WHERE market = :market AND coffee_type = :coffee_type
                            AND coffee_form = :coffee_form AND price_date = :price_date
                    """, parameters).fetchone()
                    if duplicate and duplicate["id"] != price_id:
                        errors["price_date"] = "A price already exists for this market, variety, form and date."
                    elif price_id is None:
                        db.execute("""
                            INSERT INTO market_prices (price_date, coffee_type, coffee_form,
                                price_ugx_per_kg, market, notes)
                            VALUES (:price_date, :coffee_type, :coffee_form, :price_ugx_per_kg, :market, :notes)
                        """, parameters)
                    else:
                        result = db.execute("""
                            UPDATE market_prices SET price_date = :price_date, coffee_type = :coffee_type,
                                coffee_form = :coffee_form, price_ugx_per_kg = :price_ugx_per_kg,
                                market = :market, notes = :notes WHERE id = :id
                        """, dict(parameters, id=price_id))
                        if not result.rowcount:
                            abort(404, description="This market price could not be found.")
                if not errors:
                    flash("Market price added." if price_id is None else "Market price updated.", "success")
                    return redirect(url_for("market_prices"), code=303)
        return render_template(
            "market_price_form.html", values=values, errors=errors, varieties=varieties,
            coffee_forms=coffee_forms, price_id=price_id,
        ), 400 if errors else 200

    @app.route("/market-prices/<int:price_id>/delete", methods=("GET", "POST"))
    def delete_market_price(price_id):
        db = get_db()
        record = db.execute("SELECT * FROM market_prices WHERE id = ?", (str(price_id),)).fetchone()
        if record is None:
            abort(404, description="This market price could not be found.")
        if request.method == "POST":
            check_csrf()
            with db:
                if not db.execute("DELETE FROM market_prices WHERE id = ?", (price_id,)).rowcount:
                    abort(404, description="This market price could not be found.")
            flash("Market price deleted.", "success")
            return redirect(url_for("market_prices"), code=303)
        return render_template("market_price_delete.html", record=record)
