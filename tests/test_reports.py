import csv
import io
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app import create_app, get_db


class ReportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "reports.sqlite3"
        self.app = create_app({"TESTING": True, "DATABASE": self.database})
        self.client = self.app.test_client()

    def seed(self):
        with self.app.app_context(), get_db() as db:
            db.executemany("INSERT INTO coffee_plots (id, name, area_hectares, variety) VALUES (?, ?, ?, ?)",
                           [(1, "TEST-A", 1, "Arabica"), (2, "TEST-B", 1, "Robusta")])
            db.executemany("""
                INSERT INTO harvests (id, plot_id, harvest_date, weight_kg, coffee_form, season)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [(1, 1, "2024-03-04", 10, "Cherry", "Season A"),
                  (2, 1, "2024-03-04", 5, "Parchment", "Season A"),
                  (3, 1, "2024-03-05", 2, "Green Beans", "Season B"),
                  (4, 2, "2024-03-10", 7, "Cherry", "Season B"),
                  (5, 2, "2024-03-11", 3, "Cherry", "Season B")])
            db.executemany("""
                INSERT INTO farm_activities (plot_id, activity_date, activity_type, description, inputs_used, cost_ugx)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [(1, "2024-03-04", "pruning", "pruning", "  =1+1", 400),
                  (1, "2024-03-05", "pruning", "pruning", "Test input", 100),
                  (2, "2024-03-10", None, "Legacy activity", None, None)])
            db.executemany("""
                INSERT INTO sales (plot_id, sale_date, coffee_form, buyer, quantity_grams,
                    unit_price_ugx, revenue_ugx, payment_method, received_ugx)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [(1, "2024-03-04", "Cherry", "=TEST,A\nnext", 1500, 1000, 1500, "Cash", 500),
                  (2, "2024-03-10", "Cherry", "Test buyer B", 3000, 1000, 3000, "Cash", 3000),
                  (2, "2024-03-11", "Green Beans", "Test buyer C", 1000, 2000, 2000, "Cash", 0)])
            db.execute("INSERT INTO sales (harvest_id, sale_date, weight_kg, revenue_ugx) VALUES (?, ?, ?, ?)",
                       (1, "2024-03-05", 1, 800))
            db.executemany("""
                INSERT INTO market_prices (market, coffee_type, coffee_form, price_date, price_ugx_per_kg)
                VALUES (?, ?, ?, ?, ?)
            """, [("Test market A", "Arabica", "Cherry", "2024-03-01", 900),
                  ("Test market A", "Arabica", "Parchment", "2024-03-04", 100),
                  ("Test market B", "Arabica", "Cherry", "2024-03-05", 1100),
                  ("Test market B", "Robusta", "Cherry", "2024-03-10", 1200)])

    def export(self, name, **filters):
        response = self.client.get(f"/reports/{name}.csv", query_string=filters)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        return list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))

    def test_summary_payments_and_operating_margin(self):
        self.seed()
        metrics = {row["Measure"]: row for row in self.export("performance")}
        for metric, amount in {
            "Total sales value": "7300", "Payments received": "3500", "Outstanding balances": "3000",
            "Recorded activity costs": "500", "Limited operating margin estimate": "6800",
            "Sales with unknown payments": "1", "Activities with unknown costs": "1",
        }.items():
            self.assertEqual(metrics[metric]["Value"], amount)
        self.assertIn("Other expenses may be missing", metrics["Limited operating margin estimate"]["Basis"])
        sales = self.export("sales")
        legacy = next(row for row in sales if row["Status"] == "Unknown")
        self.assertEqual((legacy["Total (UGX)"], legacy["Received (UGX)"], legacy["Balance (UGX)"]), ("800", "", ""))
        partial = next(row for row in sales if row["Status"] == "Partially Paid")
        self.assertEqual((partial["Quantity (kg)"], partial["Received (UGX)"], partial["Balance (UGX)"]),
                         ("1.5", "500", "1000"))
        page = self.client.get("/reports").data
        for amount in (b"UGX 7,300", b"UGX 3,500", b"UGX 3,000", b"UGX 500"):
            self.assertIn(amount, page)
        self.assertNotIn(b"net profit", page.lower())

    def test_production_grouping_charts_and_season_filter(self):
        self.seed()
        rows = self.export("production")
        groups = {(r["Plot"], r["Variety"], r["Coffee form"], r["Production season"]): r["Weight (kg)"] for r in rows}
        self.assertEqual(groups, {
            ("TEST-A", "Arabica", "Cherry", "Season A"): "10.0",
            ("TEST-A", "Arabica", "Parchment", "Season A"): "5.0",
            ("TEST-A", "Arabica", "Green Beans", "Season B"): "2.0",
            ("TEST-B", "Robusta", "Cherry", "Season B"): "10.0",
        })
        page = self.client.get("/reports").data
        charts = {chart["title"]: chart for chart in
                  map(json.loads, re.findall(rb"data-report-chart='([^']+)'", page))}
        self.assertEqual(len(charts), 4)
        by_form = charts["Production by form"]
        self.assertEqual(by_form["labels"], ["Cherry", "Parchment", "Green Beans"])
        self.assertEqual(by_form["datasets"][0]["data"], ["20.0", "5.0", "2.0"])
        by_plot = charts["Production by plot"]
        self.assertEqual({d["label"]: d["data"] for d in by_plot["datasets"]},
                         {"Cherry": ["10.0", "10.0"], "Parchment": ["5.0", "0"], "Green Beans": ["2.0", "0"]})
        self.assertNotIn(b">27 kg<", page)
        rows = self.export("production", season="Season A")
        self.assertEqual({r["Coffee form"] for r in rows}, {"Cherry", "Parchment"})
        self.assertEqual(self.export("sales", season="Season A"), self.export("sales"))
        self.assertIn(b'id="market-price-chart"', page)

    def test_daily_weekly_and_custom_date_filters(self):
        self.seed()
        day = self.export("sales", period="daily", date="2024-03-04")
        self.assertEqual([row["Sale date"] for row in day], ["2024-03-04"])
        week = self.export("sales", period="weekly", date="2024-03-06")
        self.assertEqual({row["Sale date"] for row in week}, {"2024-03-04", "2024-03-05", "2024-03-10"})
        custom = self.export("sales", period="custom", start_date="2024-03-05", end_date="2024-03-10")
        self.assertEqual([row["Sale date"] for row in custom], ["2024-03-10", "2024-03-05"])
        home = self.client.get("/reports?period=weekly&date=2024-03-06").data
        self.assertIn(b"2024-03-04 to 2024-03-10", home)

    def test_invalid_filters_and_unsupported_export(self):
        cases = [
            {"period": "other"}, {"period": "daily", "date": "2024-02-30"},
            {"period": "custom", "start_date": "2024-02-02"},
            {"period": "custom", "start_date": "2024-03-02", "end_date": "2024-03-01"},
            {"season": "' OR 1=1 --"},
        ]
        for query in cases:
            with self.subTest(query=query):
                for route in ("/reports", "/reports/print", "/reports/sales.csv"):
                    self.assertEqual(self.client.get(route, query_string=query).status_code, 400)
        self.assertEqual(self.client.get("/reports/unsupported.csv").status_code, 404)

    def test_csv_headers_and_formula_safeguards(self):
        self.seed()
        response = self.client.get("/reports/sales.csv")
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="sales-report.csv"')
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        rows = self.export("sales")
        buyer = next(row["Buyer"] for row in rows if row["Sale date"] == "2024-03-04")
        self.assertEqual(buyer, "'=TEST,A\nnext")
        activity = self.export("activities")
        self.assertTrue(any(row["Inputs used"] == "'  =1+1" for row in activity))
        self.assertTrue(any(row["Count"] == "2" and row["Activity type"] == "pruning" for row in activity))
        market = self.export("market-prices")
        negative = next(row for row in market if row["Sale minus market (UGX/kg)"] == "-200")
        self.assertEqual(negative["Section"], "Comparable sale prices")

    def test_market_matching_respects_form_variety_and_sale_date(self):
        self.seed()
        market = self.export("market-prices", period="daily", date="2024-03-04")
        history = [row for row in market if row["Section"] == "Manually recorded price history"]
        matches = [row for row in market if row["Section"] == "Comparable sale prices"]
        self.assertEqual([(row["Coffee form"], row["Price date"]) for row in history], [("Parchment", "2024-03-04")])
        self.assertEqual(len(matches), 1)
        self.assertEqual((matches[0]["Price date"], matches[0]["Market price (UGX/kg)"],
                          matches[0]["Sale minus market (UGX/kg)"]), ("2024-03-01", "900", "100"))
        page = self.client.get("/reports?period=daily&date=2024-03-11").data
        self.assertIn(b"No comparable market prices for sales in this period.", page)

    def test_empty_reports_and_print_view(self):
        page = self.client.get("/reports").data
        self.assertIn(b"No recorded data for this selection.", page)
        self.assertEqual(page.count(b">0 kg</p>"), 3)
        self.assertNotIn(b"data-report-chart=", page)
        self.assertEqual(self.export("production"), [])
        self.assertEqual(self.export("activities"), [])
        self.seed()
        response = self.client.get("/reports/print?period=daily&date=2024-03-04&season=Season+A")
        self.assertEqual(response.status_code, 200)
        for heading in (b"Coffee Production Management System", b"Production Report", b"Farm Activity Report",
                        b"Sales Report", b"Market Price Report", b"Farm Performance Report"):
            self.assertIn(heading, response.data)
        today = datetime.now(ZoneInfo("Africa/Kampala")).date().isoformat()
        self.assertIn(f"Generated {today}".encode(), response.data)
        self.assertIn(b"2024-03-04", response.data)
        self.assertIn(b"Season A", response.data)
        self.assertNotIn(b"2024-03-11", response.data)
        self.assertNotIn(b"Download CSV", response.data)
        self.assertNotIn(b"data-report-chart=", response.data)
