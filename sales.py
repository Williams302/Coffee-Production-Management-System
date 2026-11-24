import re
from collections import defaultdict

from flask import abort, flash, redirect, render_template, request, url_for


PAYMENT_METHODS = ("Cash", "Mobile Money", "Bank Transfer", "Cheque")
PAYMENT_STATUSES = ("Pending", "Partially Paid", "Paid")
SALE_FIELDS = (
    "sale_date", "plot_id", "coffee_form", "buyer", "quantity_kg", "unit_price_ugx",
    "payment_method", "payment_status", "received_ugx", "notes",
)
MAX_INTEGER = 2**63 - 1
SALE_LIST = """
    SELECT s.*, p.name AS plot_name, p.id AS selected_plot_id, p.variety AS plot_variety,
           COALESCE(s.coffee_form, h.coffee_form) AS display_form,
           s.revenue_ugx - s.received_ugx AS balance,
           CASE WHEN s.received_ugx IS NULL THEN 'Unknown'
                WHEN s.received_ugx = s.revenue_ugx THEN 'Paid'
                WHEN s.received_ugx = 0 THEN 'Pending'
                ELSE 'Partially Paid' END AS payment_status
    FROM sales AS s
    LEFT JOIN harvests AS h ON h.id = s.harvest_id
    JOIN coffee_plots AS p ON p.id = COALESCE(s.plot_id, h.plot_id)
"""


def migrate_sales(db):
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] >= 4:
            return
        db.execute("ALTER TABLE sales RENAME TO sales_before_v4")
        db.execute("""
            CREATE TABLE sales (
                id INTEGER PRIMARY KEY,
                harvest_id INTEGER REFERENCES harvests(id),
                sale_date TEXT NOT NULL CHECK (date(sale_date, '+0 days') IS sale_date),
                weight_kg REAL CHECK (weight_kg > 0),
                revenue_ugx INTEGER NOT NULL CHECK (revenue_ugx >= 0),
                plot_id INTEGER REFERENCES coffee_plots(id),
                coffee_form TEXT CHECK (coffee_form IN ('Cherry', 'Parchment', 'Green Beans')),
                buyer TEXT CHECK (length(trim(buyer)) > 0),
                quantity_grams INTEGER CHECK (quantity_grams > 0),
                unit_price_ugx INTEGER CHECK (unit_price_ugx >= 0),
                payment_method TEXT CHECK (payment_method IN ('Cash', 'Mobile Money', 'Bank Transfer', 'Cheque')),
                received_ugx INTEGER CHECK (received_ugx BETWEEN 0 AND revenue_ugx),
                notes TEXT,
                CHECK (
                    (plot_id IS NULL AND harvest_id IS NOT NULL AND weight_kg IS NOT NULL
                     AND quantity_grams IS NULL AND received_ugx IS NULL)
                    OR
                    (plot_id IS NOT NULL AND coffee_form IS NOT NULL AND buyer IS NOT NULL
                     AND quantity_grams IS NOT NULL AND unit_price_ugx IS NOT NULL
                     AND payment_method IS NOT NULL AND received_ugx IS NOT NULL)
                )
            ) STRICT
        """)
        db.execute("""
            INSERT INTO sales (id, harvest_id, sale_date, weight_kg, revenue_ugx)
            SELECT id, harvest_id, sale_date, weight_kg, revenue_ugx FROM sales_before_v4
        """)
        db.execute("DROP TABLE sales_before_v4")
        db.execute("PRAGMA user_version = 4")


def sale_totals(db):
    return summarize_sales(db.execute("SELECT revenue_ugx, received_ugx FROM sales"))


def summarize_sales(amounts):
    # Python integers keep the sum exact even if it exceeds SQLite's integer range.
    value = received = outstanding = unknown = 0
    for total, paid in amounts:
        value += total
        if paid is None:
            unknown += 1
        else:
            received += paid
            outstanding += total - paid
    return {"value": value, "received": received, "outstanding": outstanding, "unknown": unknown}


def quantity_text(grams, legacy_weight):
    if grams is None:
        return str(legacy_weight)
    kg, fraction = divmod(grams, 1000)
    return f"{kg}.{fraction:03d}".rstrip("0").rstrip(".")


def whole_amount(value):
    if not re.fullmatch(r"[0-9]{1,19}", value):
        return None
    amount = int(value)
    return amount if amount <= MAX_INTEGER else None


def validate_sale(values, plot_ids, coffee_forms, valid_date):
    errors = {}
    if values["plot_id"] not in plot_ids:
        errors["plot_id"] = "Choose a registered coffee plot."
    if not valid_date(values["sale_date"]):
        errors["sale_date"] = "Enter a valid sale date."
    if values["coffee_form"] not in coffee_forms:
        errors["coffee_form"] = "Choose a coffee form from the list."
    if not values["buyer"] or len(values["buyer"]) > 150 or "\0" in values["buyer"]:
        errors["buyer"] = "Enter a buyer name using up to 150 characters."
    if values["payment_method"] not in PAYMENT_METHODS:
        errors["payment_method"] = "Choose a payment method from the list."
    if values["payment_status"] not in PAYMENT_STATUSES:
        errors["payment_status"] = "Choose a payment status from the list."
    if len(values["notes"]) > 2000 or "\0" in values["notes"]:
        errors["notes"] = "Enter notes using up to 2,000 characters."

    quantity = re.fullmatch(r"([0-9]{1,19})(?:\.([0-9]{1,3}))?", values["quantity_kg"])
    grams = int(quantity[1]) * 1000 + int((quantity[2] or "").ljust(3, "0")) if quantity else 0
    if not 0 < grams <= MAX_INTEGER:
        errors["quantity_kg"] = "Enter a positive quantity in kg with up to three decimal places."
    price = whole_amount(values["unit_price_ugx"])
    if price is None:
        errors["unit_price_ugx"] = "Enter a non-negative whole UGX price within the supported range."
    if "quantity_kg" in errors or price is None:
        return {}, errors

    total = (grams * price + 500) // 1000
    if total > MAX_INTEGER:
        errors["unit_price_ugx"] = "The calculated total exceeds the supported UGX range."
        return {}, errors

    status = values["payment_status"]
    supplied = values["received_ugx"]
    received = whole_amount(supplied) if supplied else None
    if status == "Partially Paid":
        if received is None or not 0 < received < total:
            errors["received_ugx"] = "Enter an amount greater than zero and less than the total."
    elif status in ("Pending", "Paid"):
        expected = total if status == "Paid" else 0
        if status == "Pending" and total == 0:
            errors["payment_status"] = "Choose Paid for a sale with a zero total."
        if supplied and received != expected:
            errors["received_ugx"] = "The amount received must match the selected payment status."
        received = expected
    return dict(values, quantity_grams=grams, unit_price_ugx=price, revenue_ugx=total,
                received_ugx=received, notes=values["notes"] or None), errors


def register_sales_routes(app, get_db, check_csrf, valid_date, coffee_forms):
    app.add_template_global(quantity_text)

    @app.get("/sales")
    def sales():
        db = get_db()
        plots = db.execute("SELECT id, name FROM coffee_plots ORDER BY name COLLATE NOCASE").fetchall()
        statuses = (*PAYMENT_STATUSES, "Unknown")
        filters = {key: request.args.get(key, "").strip() for key in
                   ("q", "plot_id", "coffee_form", "payment_status", "start_date", "end_date")}
        errors = []
        if filters["plot_id"] and filters["plot_id"] not in {str(plot["id"]) for plot in plots}:
            errors.append("Choose a registered plot to filter by.")
        if filters["coffee_form"] and filters["coffee_form"] not in coffee_forms:
            errors.append("Choose a coffee form from the list.")
        if filters["payment_status"] and filters["payment_status"] not in statuses:
            errors.append("Choose a payment status from the list.")
        start, end = filters["start_date"], filters["end_date"]
        if (start and not valid_date(start)) or (end and not valid_date(end)):
            errors.append("Enter valid start and end dates.")
        elif start and end and start > end:
            errors.append("The end date must be on or after the start date.")
        records = [] if errors else db.execute("""
            SELECT * FROM (""" + SALE_LIST + """) AS records
            WHERE (:q = '' OR instr(lower(buyer), lower(:q)) > 0
                           OR instr(lower(plot_name), lower(:q)) > 0)
                AND (:plot_id = '' OR selected_plot_id = :plot_id)
                AND (:coffee_form = '' OR display_form = :coffee_form)
                AND (:payment_status = '' OR payment_status = :payment_status)
                AND (:start_date = '' OR sale_date >= :start_date)
                AND (:end_date = '' OR sale_date <= :end_date)
            ORDER BY sale_date DESC, id DESC
        """, filters).fetchall()
        daily = defaultdict(int)
        for record in records:
            daily[record["sale_date"]] += record["revenue_ugx"]
        trend = sorted(daily.items())
        return render_template(
            "sales.html", records=records, plots=plots, coffee_forms=coffee_forms, statuses=statuses,
            filters=filters, errors=errors, trend=trend,
            chart_values=[(day, str(amount)) for day, amount in trend],
        ), 400 if errors else 200

    @app.route("/sales/new", methods=("GET", "POST"))
    @app.route("/sales/<int:sale_id>/edit", methods=("GET", "POST"))
    def sale_form(sale_id=None):
        db = get_db()
        plots = db.execute("SELECT id, name FROM coffee_plots ORDER BY name COLLATE NOCASE").fetchall()
        values, errors, record = {}, {}, None
        if sale_id is not None:
            record = db.execute(SALE_LIST + " WHERE s.id = ?", (str(sale_id),)).fetchone()
            if record is None:
                abort(404, description="This sale could not be found.")
            values = dict(record, plot_id=record["selected_plot_id"], coffee_form=record["display_form"],
                          quantity_kg=quantity_text(record["quantity_grams"], record["weight_kg"]))
        if request.method == "POST":
            check_csrf()
            values = {field: request.form.get(field, "").strip() for field in SALE_FIELDS}
            parameters, errors = validate_sale(values, {str(plot["id"]) for plot in plots},
                                               coffee_forms, valid_date)
            if record is not None and record["harvest_id"] is not None:
                if values["plot_id"] != str(record["selected_plot_id"]):
                    errors["plot_id"] = "Keep the plot associated with the original harvest."
            if not errors:
                with db:
                    # Lock the selected plot in place for the duration of the write.
                    db.execute("BEGIN IMMEDIATE")
                    plot = db.execute("SELECT id FROM coffee_plots WHERE id = ?", (parameters["plot_id"],)).fetchone()
                    if not plot:
                        errors["plot_id"] = "The selected plot is no longer available."
                    elif sale_id is None:
                        db.execute("""
                            INSERT INTO sales (sale_date, plot_id, coffee_form, buyer, quantity_grams,
                                               unit_price_ugx, revenue_ugx, payment_method, received_ugx, notes)
                            VALUES (:sale_date, :plot_id, :coffee_form, :buyer, :quantity_grams,
                                    :unit_price_ugx, :revenue_ugx, :payment_method, :received_ugx, :notes)
                        """, parameters)
                    else:
                        updated = db.execute("""
                            UPDATE sales SET sale_date = :sale_date, plot_id = :plot_id,
                                coffee_form = :coffee_form, buyer = :buyer, quantity_grams = :quantity_grams,
                                unit_price_ugx = :unit_price_ugx, revenue_ugx = :revenue_ugx,
                                payment_method = :payment_method, received_ugx = :received_ugx, notes = :notes
                            WHERE id = :id
                        """, dict(parameters, id=sale_id))
                        if not updated.rowcount:
                            abort(404, description="This sale could not be found.")
                if not errors:
                    flash("Sale added." if sale_id is None else "Sale updated.", "success")
                    return redirect(url_for("sales"), code=303)
        return render_template(
            "sale_form.html", plots=plots, coffee_forms=coffee_forms, methods=PAYMENT_METHODS,
            statuses=PAYMENT_STATUSES, values=values, errors=errors, record=record,
        ), 400 if errors else 200

    @app.route("/sales/<int:sale_id>/delete", methods=("GET", "POST"))
    def delete_sale(sale_id):
        db = get_db()
        record = db.execute(SALE_LIST + " WHERE s.id = ?", (str(sale_id),)).fetchone()
        if record is None:
            abort(404, description="This sale could not be found.")
        if request.method == "POST":
            check_csrf()
            with db:
                if not db.execute("DELETE FROM sales WHERE id = ?", (sale_id,)).rowcount:
                    abort(404, description="This sale could not be found.")
            flash("Sale deleted.", "success")
            return redirect(url_for("sales"), code=303)
        return render_template("sale_delete.html", record=record)
