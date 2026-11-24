import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Response, abort, render_template, request

from market_prices import sale_comparisons
from sales import SALE_LIST, quantity_text, summarize_sales


REPORT_TITLES = {
    "production": "Production Report",
    "activities": "Farm Activity Report",
    "sales": "Sales Report",
    "market-prices": "Market Price Report",
    "performance": "Farm Performance Report",
}


def report_filters(args, today, seasons, valid_date):
    filters = {key: args.get(key, "").strip() for key in
               ("period", "date", "start_date", "end_date", "season")}
    filters["period"] = filters["period"] or "all"
    period = filters["period"]
    errors = []
    if period not in ("all", "daily", "weekly", "custom"):
        errors.append("Choose a reporting period from the list.")
    if filters["season"] and filters["season"] not in seasons:
        errors.append("Choose a saved production season.")

    date_fields = {"daily": ("date",), "weekly": ("date",), "custom": ("start_date", "end_date")}
    active = date_fields.get(period, ())
    for field in ("date", "start_date", "end_date"):
        if field not in active:
            filters[field] = ""

    if period in ("daily", "weekly"):
        filters["date"] = filters["date"] or today.isoformat()
    start = end = ""
    if active and not all(valid_date(filters[field]) for field in active):
        errors.append("Enter valid dates for the selected period.")
    elif period == "custom":
        start, end = filters["start_date"], filters["end_date"]
        if start > end:
            errors.append("The end date must be on or after the start date.")
    elif period in ("daily", "weekly"):
        selected = date.fromisoformat(filters["date"])
        if period == "daily":
            start = end = selected.isoformat()
        else:
            monday = selected - timedelta(days=selected.weekday())
            start = monday.isoformat()
            end = (monday + timedelta(days=min(6, (date.max - monday).days))).isoformat()
    return filters, {"start": start, "end": end}, errors


def report_rows(db, bounds, season):
    harvests = db.execute("""
        SELECT p.name AS plot_name, p.variety, h.coffee_form, h.season, h.weight_kg
        FROM harvests AS h JOIN coffee_plots AS p ON p.id = h.plot_id
        WHERE (:start = '' OR h.harvest_date >= :start) AND (:end = '' OR h.harvest_date <= :end)
            AND (:season = '' OR h.season = :season)
        ORDER BY p.name COLLATE NOCASE, h.season, h.coffee_form, h.id
    """, dict(bounds, season=season)).fetchall()
    activities = db.execute("""
        SELECT p.name AS plot_name, a.activity_date, a.activity_type, a.description,
            a.inputs_used, a.quantity, a.unit, a.cost_ugx
        FROM farm_activities AS a JOIN coffee_plots AS p ON p.id = a.plot_id
        WHERE (:start = '' OR a.activity_date >= :start) AND (:end = '' OR a.activity_date <= :end)
        ORDER BY p.name COLLATE NOCASE, a.activity_date DESC, a.id DESC
    """, bounds).fetchall()
    sales = db.execute(SALE_LIST + """
        WHERE (:start = '' OR s.sale_date >= :start) AND (:end = '' OR s.sale_date <= :end)
        ORDER BY s.sale_date DESC, s.id DESC
    """, bounds).fetchall()
    prices = db.execute("""
        SELECT * FROM market_prices
        WHERE (:start = '' OR (price_date >= :start AND date(price_date, '+0 days') IS price_date))
          AND (:end = '' OR (price_date <= :end AND date(price_date, '+0 days') IS price_date))
        ORDER BY coffee_type, coffee_form, market COLLATE NOCASE, price_date, id
    """, bounds).fetchall()
    comparisons = [row for row in sale_comparisons(db, "", **bounds) if row["difference_ugx"] is not None]
    return harvests, activities, sales, prices, comparisons


def table(title, columns, rows):
    return {"title": title, "columns": columns, "rows": rows}


def make_reports(harvests, activities, sales, prices, comparisons, coffee_forms):
    production_groups = defaultdict(Decimal)
    production_by_form = dict.fromkeys(coffee_forms, Decimal(0))
    production_by_plot = defaultdict(lambda: defaultdict(Decimal))
    for row in harvests:
        form = row["coffee_form"] or "Unclassified"
        weight = Decimal(str(row["weight_kg"]))
        production_groups[(row["plot_name"], row["variety"], form, row["season"])] += weight
        production_by_form[form] = production_by_form.get(form, Decimal(0)) + weight
        production_by_plot[row["plot_name"]][form] += weight

    activity_counts = Counter()
    activity_costs = defaultdict(int)
    unknown_costs = 0
    for row in activities:
        activity_counts[row["plot_name"], row["activity_type"] or "Unclassified"] += 1
        if row["cost_ugx"] is None:
            unknown_costs += 1
        else:
            activity_costs[row["plot_name"]] += row["cost_ugx"]

    amounts = summarize_sales((row["revenue_ugx"], row["received_ugx"]) for row in sales)
    costs = sum(activity_costs.values())
    margin = amounts["value"] - costs
    summary = {"production": production_by_form, "sales": amounts, "costs": costs,
               "unknown_costs": unknown_costs, "margin": margin}

    sections = {
        "production": [table("Harvest quantities by plot, variety, form and season",
            ("Plot", "Variety", "Coffee form", "Production season", "Weight (kg)"),
            [(*group, weight) for group, weight in production_groups.items()])],
        "activities": [
            table("Activity counts by plot and type", ("Plot", "Activity type", "Count"),
                  [(*group, count) for group, count in activity_counts.items()]),
            table("Recorded activities", ("Plot", "Activity date", "Activity type", "Description",
                                         "Inputs used", "Quantity", "Unit", "Cost (UGX)"),
                  [(row["plot_name"], row["activity_date"], row["activity_type"], row["description"],
                    row["inputs_used"], row["quantity"], row["unit"], row["cost_ugx"]) for row in activities]),
        ],
        "sales": [table("Recorded sales", ("Sale date", "Plot", "Buyer", "Variety", "Coffee form", "Quantity (kg)",
                                 "Unit price (UGX/kg)", "Total (UGX)", "Received (UGX)", "Balance (UGX)", "Status"),
            [(row["sale_date"], row["plot_name"], row["buyer"], row["plot_variety"], row["display_form"],
              quantity_text(row["quantity_grams"], row["weight_kg"]), row["unit_price_ugx"], row["revenue_ugx"],
              row["received_ugx"], row["balance"], row["payment_status"]) for row in sales])],
        "market-prices": [
            table("Manually recorded price history", ("Price date", "Variety", "Coffee form", "Market or source",
                                                      "Market price (UGX/kg)"),
                  [(row["price_date"], row["coffee_type"], row["coffee_form"] or "Unclassified",
                    row["market"], row["price_ugx_per_kg"]) for row in prices]),
            table("Comparable sale prices", ("Sale date", "Plot", "Buyer", "Variety", "Coffee form",
                                             "Sale price (UGX/kg)", "Market or source", "Price date",
                                             "Market price (UGX/kg)", "Sale minus market (UGX/kg)"),
                  [(row["sale_date"], row["plot_name"], row["buyer"], row["variety"], row["coffee_form"],
                    row["unit_price_ugx"], row["market"], row["price_date"],
                    row["price_ugx_per_kg"], row["difference_ugx"]) for row in comparisons]),
        ],
        "performance": [table("Farm performance indicators", ("Measure", "Value", "Unit", "Basis"), [
            ("Total sales value", amounts["value"], "UGX", "Includes sales with unknown payments."),
            ("Payments received", amounts["received"], "UGX", "Recorded payments for sales dated in the period."),
            ("Outstanding balances", amounts["outstanding"], "UGX", "Sales with recorded payment information."),
            ("Recorded activity costs", costs, "UGX", "Activities with a recorded cost."),
            ("Limited operating margin estimate", margin, "UGX", "Sales value minus recorded activity costs. Other expenses may be missing."),
            ("Sales with unknown payments", amounts["unknown"], "sales", ""),
            ("Activities with unknown costs", unknown_costs, "activities", ""),
        ])],
    }
    return summary, sections, production_by_plot, activity_costs


def chart_data(production, plots, sales, costs, prices):
    def chart(title, x_title, y_title, labels, datasets, kind="bar"):
        return {"title": title, "xTitle": x_title, "yTitle": y_title, "labels": list(labels),
                "datasets": [{"label": name, "data": [str(value) for value in values]}
                             for name, values in datasets], "type": kind}

    daily_sales = defaultdict(int)
    for row in sales:
        daily_sales[row["sale_date"]] += row["revenue_ugx"]
    days = sorted(daily_sales)
    market_series = defaultdict(list)
    for row in prices:
        if row["coffee_form"] is not None:
            key = (row["market"], row["coffee_type"], row["coffee_form"])
            market_series[key].append([row["price_date"], str(row["price_ugx_per_kg"])])

    charts = [
        chart("Production by form", "Coffee form", "Recorded weight (kg)", production if plots else [],
              [("Recorded weight (kg)", production.values())]),
        chart("Production by plot", "Coffee plot", "Recorded weight (kg)", plots,
              [(form, [weights.get(form, 0) for weights in plots.values()]) for form in production]),
        chart("Sales trends", "Sale date", "Sales value (UGX)", days,
              [("Daily sales value (UGX)", [daily_sales[day] for day in days])], "line"),
        chart("Activity costs by plot", "Coffee plot", "Recorded activity cost (UGX)", costs,
              [("Recorded cost (UGX)", costs.values())]),
    ]
    return charts, [{"label": " · ".join(key), "points": points} for key, points in market_series.items()]


def csv_cell(value):
    if isinstance(value, str):
        # Keep actual numeric cells numeric. Guard user text even after leading whitespace/BOM.
        first = next((char for char in value if not char.isspace() and char not in "\ufeff\u200b"), "")
        if value.startswith(("\t", "\r", "\n")) or first in ("=", "+", "-", "@", "＝", "＋", "－", "＠"):
            return "'" + value
    return value


def csv_report(report_name, tables):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    if len(tables) == 1:
        report_table = tables[0]
        writer.writerow(report_table["columns"])
        writer.writerows([csv_cell(cell) for cell in row] for row in report_table["rows"])
    else:
        columns = list(dict.fromkeys(column for item in tables for column in item["columns"]))
        writer.writerow(["Section", *columns])
        for item in tables:
            for row in item["rows"]:
                cells = dict(zip(item["columns"], row))
                writer.writerow([item["title"], *(csv_cell(cells.get(column)) for column in columns)])
    return Response(output.getvalue(), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{report_name}-report.csv"',
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


def register_report_routes(app, get_db, valid_date, coffee_forms):
    @app.get("/reports")
    @app.get("/reports/print", endpoint="print_reports")
    @app.get("/reports/<report_name>.csv", endpoint="export_report")
    def reports(report_name=None):
        if report_name is not None and report_name not in REPORT_TITLES:
            abort(404)
        printing = request.endpoint == "print_reports"
        today = datetime.now(ZoneInfo("Africa/Kampala")).date()
        db = get_db()
        # Read one snapshot so tables, charts and summaries reflect the same records.
        with db:
            db.execute("BEGIN")
            seasons = [row[0] for row in db.execute("""
                SELECT DISTINCT season FROM harvests WHERE season IS NOT NULL ORDER BY season COLLATE NOCASE
            """)]
            filters, bounds, errors = report_filters(request.args, today, seasons, valid_date)
            if not errors:
                harvests, activities, sales, prices, comparisons = report_rows(db, bounds, filters["season"])
        context = dict(filters=filters, seasons=seasons, errors=errors, printing=printing, generated=today.isoformat())
        if errors:
            if report_name is not None:
                abort(400, description=" ".join(errors))
            return render_template("reports.html", **context), 400

        summary, sections, plots, costs = make_reports(harvests, activities, sales, prices, comparisons, coffee_forms)
        if report_name is not None:
            return csv_report(report_name, sections[report_name])
        charts, market_series = ([], []) if printing else chart_data(summary["production"], plots, sales, costs, prices)
        start, end = bounds["start"], bounds["end"]
        period_label = "All saved dates" if not start else start if start == end else f"{start} to {end}"
        return render_template(
            "reports.html", **context, period_label=period_label, titles=REPORT_TITLES,
            sections=sections, summary=summary, charts=charts, market_series=market_series,
        )
