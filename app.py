import sqlite3
import math
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from zoneinfo import ZoneInfo

from flask import (
    Flask, abort, current_app, flash, g, redirect, render_template,
    request, session, url_for,
)
from sales import migrate_sales, register_sales_routes, sale_totals
from market_prices import latest_market_price, migrate_market_prices, register_market_price_routes
from reports import register_report_routes


VARIETIES = ("Arabica", "Robusta")
PLOT_FIELDS = ("name", "variety", "tree_count", "planting_date", "location", "area_hectares")
ACTIVITY_TYPES = ("planting", "pruning", "weeding", "fertilising", "pest control", "harvesting")
ACTIVITY_FIELDS = (
    "plot_id", "activity_type", "activity_date", "inputs_used", "quantity", "unit", "cost_ugx", "notes",
)
COFFEE_FORMS = ("Cherry", "Parchment", "Green Beans")
HARVEST_FIELDS = ("plot_id", "harvest_date", "coffee_form", "weight_kg", "season", "notes")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def migrate_plots(db):
    # Legacy plots retain their name and area. The new fields can be filled in later.
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= 1:
            return
        db.execute("""
            ALTER TABLE coffee_plots ADD COLUMN variety TEXT
            CHECK (variety IN ('Arabica', 'Robusta'))
        """)
        db.execute("""
            ALTER TABLE coffee_plots ADD COLUMN tree_count INTEGER CHECK (tree_count > 0)
        """)
        db.execute("""
            ALTER TABLE coffee_plots ADD COLUMN planting_date TEXT CHECK (
                planting_date IS NULL OR date(planting_date, '+0 days') IS planting_date
            )
        """)
        db.execute("""
            ALTER TABLE coffee_plots ADD COLUMN location TEXT CHECK (length(trim(location)) > 0)
        """)
        db.execute("PRAGMA user_version = 1")


def migrate_activities(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
            return
        db.execute("""
            ALTER TABLE farm_activities ADD COLUMN activity_type TEXT CHECK (
                activity_type IN ('planting', 'pruning', 'weeding', 'fertilising', 'pest control', 'harvesting')
            )
        """)
        db.execute("""
            ALTER TABLE farm_activities ADD COLUMN inputs_used TEXT CHECK (length(trim(inputs_used)) > 0)
        """)
        db.execute("ALTER TABLE farm_activities ADD COLUMN quantity REAL CHECK (quantity >= 0)")
        db.execute("ALTER TABLE farm_activities ADD COLUMN unit TEXT CHECK (length(trim(unit)) > 0)")
        db.execute("ALTER TABLE farm_activities ADD COLUMN cost_ugx INTEGER CHECK (cost_ugx >= 0)")
        db.execute("ALTER TABLE farm_activities ADD COLUMN notes TEXT")
        db.execute("PRAGMA user_version = 2")


def migrate_harvests(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= 3:
            return
        db.execute("""
            ALTER TABLE harvests ADD COLUMN coffee_form TEXT
            CHECK (coffee_form IN ('Cherry', 'Parchment', 'Green Beans'))
        """)
        db.execute("ALTER TABLE harvests ADD COLUMN season TEXT CHECK (length(trim(season)) > 0)")
        db.execute("ALTER TABLE harvests ADD COLUMN notes TEXT")
        db.execute("PRAGMA user_version = 3")


def harvest_chart(days, daily_weights):
    forms = COFFEE_FORMS + ((None,) if any(form is None for _, form in daily_weights) else ())
    # JSON and Chart.js require finite numbers. A sum can overflow even when its records are finite.
    chart_weights = {key: weight if math.isfinite(weight) else None for key, weight in daily_weights.items()}
    return [
        {"label": form or "Unclassified", "data": [chart_weights.get((day, form), 0) for day in days]}
        for form in forms
    ]


def validate_harvest(values, plot_ids):
    errors = {}
    parameters = dict(values)
    if values["plot_id"] not in plot_ids:
        errors["plot_id"] = "Choose a registered coffee plot."
    if not valid_date(values["harvest_date"]):
        errors["harvest_date"] = "Enter a valid harvest date."
    if values["coffee_form"] not in COFFEE_FORMS:
        errors["coffee_form"] = "Choose Cherry, Parchment or Green Beans."
    try:
        weight = float(values["weight_kg"])
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError
        parameters["weight_kg"] = weight
    except ValueError:
        errors["weight_kg"] = "Enter a finite weight greater than zero."
    if not values["season"] or len(values["season"]) > 80 or "\0" in values["season"]:
        errors["season"] = "Enter a production season using up to 80 characters."
    if len(values["notes"]) > 2000 or "\0" in values["notes"]:
        errors["notes"] = "Enter notes using up to 2,000 characters."
    parameters["notes"] = values["notes"] or None
    return parameters, errors


def valid_date(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_activity(values, plot_ids):
    errors = {}
    parameters = dict(values)
    if values["plot_id"] not in plot_ids:
        errors["plot_id"] = "Choose a registered coffee plot."
    if values["activity_type"] not in ACTIVITY_TYPES:
        errors["activity_type"] = "Choose an activity type from the list."
    if not valid_date(values["activity_date"]):
        errors["activity_date"] = "Enter a valid activity date in YYYY-MM-DD format."
    for field, label, limit in (("inputs_used", "inputs used", 200), ("unit", "a unit", 40)):
        value = values[field]
        if not value or len(value) > limit or "\0" in value:
            errors[field] = f"Enter {label} using up to {limit} characters."
    try:
        quantity = Decimal(values["quantity"])
        if not quantity.is_finite() or quantity < 0 or not math.isfinite(float(quantity)):
            raise ValueError
        parameters["quantity"] = float(quantity)
    except (InvalidOperation, ValueError):
        errors["quantity"] = "Enter a quantity of zero or more."
    try:
        cost = int(values["cost_ugx"])
        if not values["cost_ugx"].isdecimal() or not 0 <= cost <= 2**63 - 1:
            raise ValueError
        parameters["cost_ugx"] = cost
    except ValueError:
        errors["cost_ugx"] = "Enter a whole UGX amount between 0 and 9,223,372,036,854,775,807."
    if len(values["notes"]) > 2000 or "\0" in values["notes"]:
        errors["notes"] = "Enter notes using up to 2,000 characters."
    parameters["notes"] = values["notes"] or None
    return parameters, errors


def validate_plot(values):
    errors = {}
    if not values["name"] or "\0" in values["name"]:
        errors["name"] = "Enter a valid plot identification number."
    if values["variety"] not in VARIETIES:
        errors["variety"] = "Choose Arabica or Robusta."
    try:
        tree_count = int(values["tree_count"])
        if not values["tree_count"].isdecimal() or not 0 < tree_count <= 2**63 - 1:
            raise ValueError
    except ValueError:
        errors["tree_count"] = "Enter a positive whole number up to 9,223,372,036,854,775,807."
    try:
        if date.fromisoformat(values["planting_date"]).isoformat() != values["planting_date"]:
            raise ValueError
    except ValueError:
        errors["planting_date"] = "Enter a valid planting date in YYYY-MM-DD format."
    if not values["location"] or "\0" in values["location"]:
        errors["location"] = "Enter a valid plot location."
    try:
        area = float(values["area_hectares"])
        if not math.isfinite(area) or area <= 0:
            raise ValueError
    except ValueError:
        errors["area_hectares"] = "Enter a positive plot area in hectares."
    return errors


def plot_csrf_token():
    if "plot_csrf_token" not in session:
        session["plot_csrf_token"] = token_urlsafe(32)
    return session["plot_csrf_token"]


def check_plot_csrf():
    expected = session.get("plot_csrf_token", "")
    submitted = request.form.get("csrf_token", "")
    if not expected or not compare_digest(expected.encode(), submitted.encode()):
        abort(400, description="This form has expired. Reload the page and try again.")


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=Path(app.instance_path) / "coffee.sqlite3",
        SECRET_KEY=os.environ.get("SECRET_KEY") or token_urlsafe(32),
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    app.add_template_global(plot_csrf_token)
    register_sales_routes(app, get_db, check_plot_csrf, valid_date, COFFEE_FORMS)
    register_market_price_routes(app, get_db, check_plot_csrf, valid_date, VARIETIES, COFFEE_FORMS)
    register_report_routes(app, get_db, valid_date, COFFEE_FORMS)

    @app.teardown_appcontext
    def close_db(_error):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.get("/")
    def dashboard():
        db = get_db()
        totals = db.execute("""
            SELECT
                (SELECT COUNT(*) FROM coffee_plots) AS plots,
                (SELECT COUNT(*) FROM farm_activities) AS activities
        """).fetchone()
        production = dict(db.execute("""
            SELECT coffee_form, SUM(weight_kg) FROM harvests GROUP BY coffee_form
        """).fetchall())
        production_by_form = [(form, production.get(form, 0)) for form in COFFEE_FORMS]
        if None in production:
            production_by_form.append(("Unclassified", production[None]))
        activities = db.execute("""
            SELECT COALESCE(a.activity_type, a.description) AS activity_label,
                a.activity_type, a.activity_date, p.name AS plot_name
            FROM farm_activities AS a
            JOIN coffee_plots AS p ON p.id = a.plot_id
            ORDER BY a.activity_date DESC, a.id DESC
            LIMIT ?
        """, (5,)).fetchall()

        today = datetime.now(ZoneInfo("Africa/Kampala")).date()
        first_day = today - timedelta(days=6)
        days = [first_day + timedelta(days=offset) for offset in range(7)]
        harvest_by_day = {
            (row["harvest_date"], row["coffee_form"]): row["weight"]
            for row in db.execute("""
            SELECT harvest_date, coffee_form, SUM(weight_kg) AS weight
            FROM harvests
            WHERE harvest_date BETWEEN ? AND ?
            GROUP BY harvest_date, coffee_form
        """, (first_day.isoformat(), today.isoformat()))
        }

        return render_template(
            "dashboard.html",
            totals=totals,
            sales_totals=sale_totals(db),
            latest_price=latest_market_price(db),
            activities=activities,
            chart_labels=[day.strftime("%d %b") for day in days],
            chart_series=harvest_chart([day.isoformat() for day in days], harvest_by_day),
            chart_has_data=bool(harvest_by_day),
            production_by_form=production_by_form,
        )

    @app.get("/plots")
    def plots():
        search = request.args.get("q", "").strip()
        # Escape LIKE wildcards so the search uses the text entered by the user.
        search_pattern = "%" + search.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        records = get_db().execute("""
            SELECT * FROM coffee_plots
            WHERE name LIKE ? ESCAPE '!' OR variety LIKE ? ESCAPE '!' OR location LIKE ? ESCAPE '!'
            ORDER BY name COLLATE NOCASE, id
        """, (search_pattern, search_pattern, search_pattern)).fetchall()
        return render_template("plots.html", plots=records, search=search)

    @app.route("/plots/new", methods=("GET", "POST"))
    @app.route("/plots/<int:plot_id>/edit", methods=("GET", "POST"))
    def plot_form(plot_id=None):
        db = get_db()
        values = {}
        errors = {}
        if plot_id is not None:
            plot = db.execute("SELECT * FROM coffee_plots WHERE id = ?", (str(plot_id),)).fetchone()
            if plot is None:
                abort(404, description="This coffee plot could not be found.")
            values = dict(plot)

        if request.method == "POST":
            check_plot_csrf()
            values = {field: request.form.get(field, "").strip() for field in PLOT_FIELDS}
            errors = validate_plot(values)
            if not errors:
                parameters = dict(values, tree_count=int(values["tree_count"]),
                                  area_hectares=float(values["area_hectares"]))
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    duplicate = db.execute("""
                        SELECT id FROM coffee_plots WHERE name = ?
                    """, (values["name"],)).fetchone()
                    if duplicate and duplicate["id"] != plot_id:
                        errors["name"] = "This plot identification number is already in use."
                    elif plot_id is None:
                        db.execute("""
                            INSERT INTO coffee_plots
                                (name, variety, tree_count, planting_date, location, area_hectares)
                            VALUES (:name, :variety, :tree_count, :planting_date, :location, :area_hectares)
                        """, parameters)
                    else:
                        result = db.execute("""
                            UPDATE coffee_plots SET name = :name, variety = :variety,
                                tree_count = :tree_count, planting_date = :planting_date,
                                location = :location, area_hectares = :area_hectares
                            WHERE id = :id
                        """, dict(parameters, id=plot_id))
                        if result.rowcount == 0:
                            abort(404, description="This coffee plot could not be found.")
                if not errors:
                    flash("Coffee plot added." if plot_id is None else "Coffee plot updated.", "success")
                    return redirect(url_for("plots"), code=303)

        return render_template(
            "plot_form.html", values=values, errors=errors, varieties=VARIETIES, plot_id=plot_id,
        ), 400 if errors else 200

    @app.route("/plots/<int:plot_id>/delete", methods=("GET", "POST"))
    def delete_plot(plot_id):
        db = get_db()
        plot = db.execute("SELECT name FROM coffee_plots WHERE id = ?", (str(plot_id),)).fetchone()
        if plot is None:
            abort(404, description="This coffee plot could not be found.")
        if request.method == "POST":
            check_plot_csrf()
            try:
                with db:
                    result = db.execute(
                        "DELETE FROM coffee_plots WHERE id = ? AND name = ?",
                        (plot_id, request.form.get("plot_name", "")),
                    )
            except sqlite3.IntegrityError:
                # Activities and harvests reference plots. Sales reference their harvest.
                flash("This plot has related activities, harvests or sales and cannot be deleted.", "danger")
            else:
                if result.rowcount:
                    flash("Coffee plot deleted.", "success")
                else:
                    flash("The plot changed. Review it before confirming deletion again.", "danger")
            return redirect(url_for("plots"), code=303)
        return render_template("plot_delete.html", plot=plot)

    @app.get("/activities")
    def activities():
        db = get_db()
        plot_options = db.execute("SELECT id, name FROM coffee_plots ORDER BY name COLLATE NOCASE").fetchall()
        filters = {
            field: request.args.get(field, "").strip()
            for field in ("plot_id", "activity_type", "activity_date")
        }
        errors = []
        if filters["plot_id"] and filters["plot_id"] not in {str(plot["id"]) for plot in plot_options}:
            errors.append("Choose a registered plot to filter by.")
        if filters["activity_type"] and filters["activity_type"] not in ACTIVITY_TYPES:
            errors.append("Choose an activity type from the list.")
        if filters["activity_date"] and not valid_date(filters["activity_date"]):
            errors.append("Enter a valid filter date.")
        records = [] if errors else db.execute("""
            SELECT a.*, p.name AS plot_name
            FROM farm_activities AS a JOIN coffee_plots AS p ON p.id = a.plot_id
            WHERE (:plot_id = '' OR a.plot_id = :plot_id)
                AND (:activity_type = '' OR a.activity_type = :activity_type)
                AND (:activity_date = '' OR a.activity_date = :activity_date)
            ORDER BY a.activity_date DESC, a.id DESC
        """, filters).fetchall()
        return render_template(
            "activities.html", records=records, filters=filters, errors=errors,
            plot_options=plot_options, activity_types=ACTIVITY_TYPES,
        ), 400 if errors else 200

    @app.route("/activities/new", methods=("GET", "POST"))
    @app.route("/activities/<int:activity_id>/edit", methods=("GET", "POST"))
    def activity_form(activity_id=None):
        db = get_db()
        plot_options = db.execute("SELECT id, name FROM coffee_plots ORDER BY name COLLATE NOCASE").fetchall()
        record = None
        values = {}
        errors = {}
        if activity_id is not None:
            record = db.execute("SELECT * FROM farm_activities WHERE id = ?", (str(activity_id),)).fetchone()
            if record is None:
                abort(404, description="This farm activity could not be found.")
            values = dict(record)
        if request.method == "POST":
            check_plot_csrf()
            values = {field: request.form.get(field, "").strip() for field in ACTIVITY_FIELDS}
            parameters, errors = validate_activity(values, {str(plot["id"]) for plot in plot_options})
            if not errors:
                try:
                    with db:
                        if activity_id is None:
                            db.execute("""
                                INSERT INTO farm_activities (
                                    plot_id, description, activity_type, activity_date,
                                    inputs_used, quantity, unit, cost_ugx, notes
                                ) VALUES (
                                    :plot_id, :activity_type, :activity_type, :activity_date,
                                    :inputs_used, :quantity, :unit, :cost_ugx, :notes
                                )
                            """, parameters)
                        else:
                            result = db.execute("""
                                UPDATE farm_activities SET plot_id = :plot_id, activity_type = :activity_type,
                                    activity_date = :activity_date, inputs_used = :inputs_used,
                                    quantity = :quantity, unit = :unit, cost_ugx = :cost_ugx, notes = :notes
                                WHERE id = :id
                            """, dict(parameters, id=activity_id))
                            if result.rowcount == 0:
                                abort(404, description="This farm activity could not be found.")
                except sqlite3.IntegrityError:
                    # The selected plot may have been removed since the dropdown was loaded.
                    errors["plot_id"] = "Choose a registered coffee plot."
                else:
                    flash("Farm activity added." if activity_id is None else "Farm activity updated.", "success")
                    return redirect(url_for("activities"), code=303)
        return render_template(
            "activity_form.html", record=record, values=values, errors=errors,
            plot_options=plot_options, activity_types=ACTIVITY_TYPES,
        ), 400 if errors else 200

    @app.route("/activities/<int:activity_id>/delete", methods=("GET", "POST"))
    def delete_activity(activity_id):
        db = get_db()
        record = db.execute("""
            SELECT a.*, p.name AS plot_name
            FROM farm_activities AS a JOIN coffee_plots AS p ON p.id = a.plot_id
            WHERE a.id = ?
        """, (str(activity_id),)).fetchone()
        if record is None:
            abort(404, description="This farm activity could not be found.")
        if request.method == "POST":
            check_plot_csrf()
            with db:
                result = db.execute("DELETE FROM farm_activities WHERE id = ?", (activity_id,))
                if result.rowcount == 0:
                    abort(404, description="This farm activity could not be found.")
            flash("Farm activity deleted.", "success")
            return redirect(url_for("activities"), code=303)
        return render_template("activity_delete.html", record=record)

    @app.get("/harvests")
    def harvests():
        db = get_db()
        plots = db.execute("SELECT id, name FROM coffee_plots ORDER BY name COLLATE NOCASE").fetchall()
        seasons = [row[0] for row in db.execute("""
            SELECT DISTINCT season FROM harvests WHERE season IS NOT NULL ORDER BY season COLLATE NOCASE
        """)]
        filters = {
            field: request.args.get(field, "").strip()
            for field in ("plot_id", "coffee_form", "start_date", "end_date", "season")
        }
        errors = []
        if filters["plot_id"] and filters["plot_id"] not in {str(plot["id"]) for plot in plots}:
            errors.append("Choose a registered plot to filter by.")
        if filters["coffee_form"] and filters["coffee_form"] not in COFFEE_FORMS:
            errors.append("Choose a coffee form from the list.")
        start, end = filters["start_date"], filters["end_date"]
        if (start and not valid_date(start)) or (end and not valid_date(end)):
            errors.append("Enter valid start and end dates.")
        elif start and end and start > end:
            errors.append("The end date must be on or after the start date.")
        if filters["season"] and filters["season"] not in seasons:
            errors.append("Choose a saved production season.")
        records = [] if errors else db.execute("""
            SELECT h.*, p.name AS plot_name, p.variety
            FROM harvests AS h JOIN coffee_plots AS p ON p.id = h.plot_id
            WHERE (:plot_id = '' OR h.plot_id = :plot_id)
                AND (:coffee_form = '' OR h.coffee_form = :coffee_form)
                AND (:start_date = '' OR h.harvest_date >= :start_date)
                AND (:end_date = '' OR h.harvest_date <= :end_date)
                AND (:season = '' OR h.season = :season)
            ORDER BY h.harvest_date DESC, h.id DESC
        """, filters).fetchall()

        summaries = {}
        daily_weights = {}
        for record in records:
            form = record["coffee_form"]
            group = (record["plot_name"], record["season"], form)
            day = (record["harvest_date"], form)
            summaries[group] = summaries.get(group, 0) + record["weight_kg"]
            daily_weights[day] = daily_weights.get(day, 0) + record["weight_kg"]
        days = sorted({day for day, _ in daily_weights})
        return render_template(
            "harvests.html", records=records, plots=plots, seasons=seasons, filters=filters,
            coffee_forms=COFFEE_FORMS, errors=errors, summaries=summaries,
            chart_labels=days, chart_series=harvest_chart(days, daily_weights),
            chart_has_data=bool(days),
        ), 400 if errors else 200

    @app.route("/harvests/new", methods=("GET", "POST"))
    @app.route("/harvests/<int:harvest_id>/edit", methods=("GET", "POST"))
    def harvest_form(harvest_id=None):
        db = get_db()
        plots = db.execute("SELECT id, name, variety FROM coffee_plots ORDER BY name COLLATE NOCASE").fetchall()
        values, errors = {}, {}
        if harvest_id is not None:
            record = db.execute("SELECT * FROM harvests WHERE id = ?", (str(harvest_id),)).fetchone()
            if record is None:
                abort(404, description="This harvest could not be found.")
            values = dict(record)
        if request.method == "POST":
            check_plot_csrf()
            values = {field: request.form.get(field, "").strip() for field in HARVEST_FIELDS}
            parameters, errors = validate_harvest(values, {str(plot["id"]) for plot in plots})
            if not errors:
                try:
                    with db:
                        if harvest_id is None:
                            db.execute("""
                                INSERT INTO harvests (plot_id, harvest_date, coffee_form, weight_kg, season, notes)
                                VALUES (:plot_id, :harvest_date, :coffee_form, :weight_kg, :season, :notes)
                            """, parameters)
                        else:
                            result = db.execute("""
                                UPDATE harvests SET plot_id = :plot_id, harvest_date = :harvest_date,
                                    coffee_form = :coffee_form, weight_kg = :weight_kg, season = :season, notes = :notes
                                WHERE id = :id
                                    AND (plot_id = :plot_id OR NOT EXISTS (
                                        SELECT 1 FROM sales WHERE harvest_id = :id
                                    ))
                            """, dict(parameters, id=harvest_id))
                            if not result.rowcount:
                                if not db.execute("SELECT 1 FROM harvests WHERE id = ?", (harvest_id,)).fetchone():
                                    abort(404, description="This harvest could not be found.")
                                errors["plot_id"] = "This harvest has linked sales. Keep its original plot."
                except sqlite3.IntegrityError:
                    errors["plot_id"] = "The selected plot is no longer available. Choose a registered plot."
                if not errors:
                    flash("Harvest added." if harvest_id is None else "Harvest updated.", "success")
                    return redirect(url_for("harvests"), code=303)
        selected_plot = next((plot for plot in plots if str(plot["id"]) == str(values.get("plot_id"))), None)
        return render_template(
            "harvest_form.html", values=values, errors=errors, plots=plots, coffee_forms=COFFEE_FORMS,
            harvest_id=harvest_id, selected_plot=selected_plot,
        ), 400 if errors else 200

    @app.route("/harvests/<int:harvest_id>/delete", methods=("GET", "POST"))
    def delete_harvest(harvest_id):
        db = get_db()
        record = db.execute("""
            SELECT h.*, p.name AS plot_name
            FROM harvests AS h JOIN coffee_plots AS p ON p.id = h.plot_id
            WHERE h.id = ?
        """, (str(harvest_id),)).fetchone()
        if record is None:
            abort(404, description="This harvest could not be found.")
        if request.method == "POST":
            check_plot_csrf()
            try:
                with db:
                    result = db.execute("DELETE FROM harvests WHERE id = ?", (harvest_id,))
                    if not result.rowcount:
                        abort(404, description="This harvest could not be found.")
            except sqlite3.IntegrityError:
                flash("This harvest has linked sales and cannot be deleted.", "danger")
            else:
                flash("Harvest deleted.", "success")
            return redirect(url_for("harvests"), code=303)
        return render_template("harvest_delete.html", record=record)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    with app.app_context():
        with app.open_resource("schema.sql") as schema:
            get_db().executescript(schema.read().decode("utf-8"))
        migrate_plots(get_db())
        migrate_activities(get_db())
        migrate_harvests(get_db())
        migrate_sales(get_db())
        migrate_market_prices(get_db())

    return app
