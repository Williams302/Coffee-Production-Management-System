import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app, get_db, migrate_activities, migrate_harvests, migrate_plots
from market_prices import sale_comparisons
from sales import migrate_sales


class MarketPriceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "prices.sqlite3"
        self.app = create_app({"TESTING": True, "DATABASE": self.path})
        self.client = self.app.test_client()
        self.values = {
            "price_date": "2024-02-29", "coffee_type": "Arabica", "coffee_form": "Cherry",
            "price_ugx_per_kg": "1000", "market": "Test Market A", "notes": "Test note",
        }
        with self.app.app_context(), get_db() as db:
            db.executemany("INSERT INTO coffee_plots (name, area_hectares, variety) VALUES (?, ?, ?)",
                           [("TEST-A", 1, "Arabica"), ("TEST-B", 1, "Robusta"), ("TEST-C", 1, None)])

    def post(self, path, values=None):
        self.assertEqual(self.client.get(path).status_code, 200)
        with self.client.session_transaction() as session:
            token = session["plot_csrf_token"]
        return self.client.post(path, data=dict(values or {}, csrf_token=token), follow_redirects=True)

    def add_price(self, **overrides):
        response = self.post("/market-prices/new", dict(self.values, **overrides))
        self.assertIn(b"Market price added.", response.data)
        self.assertEqual(response.history[0].status_code, 303)
        return response.data

    def add_sale(self, plot="1", form="Cherry", day="2024-03-01", unit_price="1100"):
        response = self.post("/sales/new", {
            "plot_id": plot, "sale_date": day, "coffee_form": form, "buyer": "Test buyer",
            "quantity_kg": "1", "unit_price_ugx": unit_price,
            "payment_method": "Cash", "payment_status": "Paid",
        })
        self.assertIn(b"Sale added.", response.data)

    def chart_series(self, page):
        match = re.search(rb"data-series='([^']+)'", page)
        return {item["label"]: item["points"] for item in json.loads(match[1])}

    def test_create_list_and_dashboard(self):
        home = self.client.get("/").data
        self.assertIn(b'href="/market-prices"', home)
        self.assertIn(b"No market prices with a recorded coffee form yet.", home)
        self.assertIn(b"No market prices recorded yet.", self.client.get("/market-prices").data)
        page = self.add_price()
        for value in (b"Arabica", b"Cherry", b"2024-02-29", b"1,000", b"Test Market A", b"Test note"):
            self.assertIn(value, page)
        self.assertEqual(self.chart_series(page), {"Test Market A · Arabica · Cherry": [["2024-02-29", "1000"]]})
        self.add_price(price_date="2024-02-28", price_ugx_per_kg="1250")
        home = self.client.get("/").data
        self.assertIn(b"UGX 1,000 / kg", home)
        self.assertIn(b"2024-02-29", home)
        self.assertIn("Arabica · Cherry · Test Market A".encode(), home)

    def test_edit_and_delete_with_confirmation(self):
        self.add_price()
        path = "/market-prices/1/edit"
        self.assertIn(b"Test note", self.client.get(path).data)
        response = self.post(path, dict(self.values, price_ugx_per_kg="0", coffee_type="Robusta",
                                        coffee_form="Green Beans", market="Test Market B", notes=""))
        self.assertIn(b"Market price updated.", response.data)
        with self.app.app_context():
            row = get_db().execute("SELECT coffee_type, coffee_form, price_ugx_per_kg, market, notes FROM market_prices").fetchone()
            self.assertEqual(tuple(row), ("Robusta", "Green Beans", 0, "Test Market B", None))
        delete = "/market-prices/1/delete"
        self.assertIn(b"This action is permanent.", self.client.get(delete).data)
        self.assertEqual(self.client.post(delete).status_code, 400)
        self.assertIn(b"Market price deleted.", self.post(delete).data)
        self.assertEqual(self.client.get(delete).status_code, 404)
        self.assertIn(b"No market prices recorded yet.", self.client.get("/market-prices").data)

    def test_filters_keep_sources_varieties_and_forms_in_separate_series(self):
        self.add_price()
        self.add_price(market="Test Market B")
        self.add_price(coffee_type="Robusta")
        self.add_price(coffee_form="Green Beans", price_date="2024-03-01")
        self.add_price(price_date="2024-02-28", price_ugx_per_kg="999")
        cases = [
            ({"market": "Test Market A"}, {1, 3, 4, 5}),
            ({"coffee_type": "Robusta"}, {3}),
            ({"coffee_form": "Green Beans"}, {4}),
            ({"start_date": "2024-02-29", "end_date": "2024-02-29"}, {1, 2, 3}),
            ({"market": "Test Market A", "coffee_type": "Arabica", "coffee_form": "Cherry"}, {1, 5}),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.get("/market-prices", query_string=filters)
                self.assertEqual(response.status_code, 200)
                for record_id in range(1, 6):
                    self.assertEqual(f'href="/market-prices/{record_id}/edit"'.encode() in response.data,
                                     record_id in expected)
        series = self.chart_series(self.client.get("/market-prices").data)
        self.assertEqual(len(series), 4)
        self.assertEqual(series["Test Market A · Arabica · Cherry"],
                         [["2024-02-28", "999"], ["2024-02-29", "1000"]])
        for filters in ({"market": "Unknown"}, {"coffee_type": "Other"}, {"coffee_form": "Other"},
                        {"start_date": "2025-02-29"}, {"start_date": "2024-03-01", "end_date": "2024-02-29"}):
            self.assertEqual(self.client.get("/market-prices", query_string=filters).status_code, 400)

    def test_latest_sale_comparison_and_signed_differences(self):
        self.add_price(price_date="2024-02-28", price_ugx_per_kg="900")
        self.add_price()
        self.add_price(market="Test Market B", price_date="2024-03-01", price_ugx_per_kg="1300")
        self.add_price(price_date="2024-03-02", price_ugx_per_kg="1400")
        self.add_price(coffee_type="Robusta", price_date="2024-03-01", price_ugx_per_kg="1500")
        self.add_price(coffee_form="Green Beans", price_date="2024-03-01", price_ugx_per_kg="1600")
        self.add_sale()
        with self.app.app_context():
            selected = sale_comparisons(get_db(), "Test Market A")[0]
            self.assertEqual((selected["price_date"], selected["price_ugx_per_kg"], selected["difference_ugx"]),
                             ("2024-02-29", 1000, 100))
            unrestricted = sale_comparisons(get_db(), "")[0]
            self.assertEqual((unrestricted["market"], unrestricted["price_date"], unrestricted["difference_ugx"]),
                             ("Test Market B", "2024-03-01", -200))
        selected_page = self.client.get("/market-prices?market=Test+Market+A").data
        self.assertRegex(selected_page, rb'text-success">\s*\+100')
        page = self.client.get("/market-prices").data
        self.assertRegex(page, rb'text-danger">\s*-200')

    def test_missing_comparable_prices_use_explicit_empty_state(self):
        self.add_sale()
        self.assertIn(b"No comparable market price", self.client.get("/market-prices").data)
        self.add_price()
        self.add_price(market="Other source", coffee_form="Green Beans")
        self.add_sale(plot="2")
        self.add_sale(plot="3")
        self.add_sale(form="Parchment")
        self.add_sale(day="2024-02-28")
        with self.app.app_context():
            rows = sale_comparisons(get_db(), "")
            self.assertEqual({row["id"] for row in rows if row["difference_ugx"] is not None}, {1})
            self.assertTrue(all(row["difference_ugx"] is None for row in sale_comparisons(get_db(), "Other source")))
        self.assertEqual(self.client.get("/market-prices").data.count(b"No comparable market price"), 4)

    def test_invalid_inputs_and_duplicate_prices(self):
        invalid = {
            "price_date": ("", "2025-02-29"), "coffee_type": ("", "Other"),
            "coffee_form": ("", "Other"), "price_ugx_per_kg": ("", "-1", "1.5", "NaN", "inf", "9223372036854775808"),
            "market": (" ", "x" * 151), "notes": ("x" * 2001,),
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value[:25]):
                    response = self.post("/market-prices/new", dict(self.values, **{field: value}))
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(f'id="{field}-error"'.encode(), response.data)
        self.assertIn(b"No market prices recorded yet.", self.client.get("/market-prices").data)
        self.add_price(price_ugx_per_kg="9223372036854775807")
        self.add_price(coffee_form="Parchment")
        for path in ("/market-prices/new", "/market-prices/2/edit"):
            response = self.post(path, self.values)
            self.assertEqual(response.status_code, 400)
            self.assertIn(b"A price already exists", response.data)

    def test_migration_preserves_unclassified_observations(self):
        path = self.path.with_name("legacy.sqlite3")
        with closing(sqlite3.connect(path)) as db:
            db.executescript(Path(self.app.root_path, "schema.sql").read_text())
            migrate_plots(db)
            migrate_activities(db)
            migrate_harvests(db)
            migrate_sales(db)
            with db:
                db.execute("INSERT INTO market_prices VALUES (?, ?, ?, ?, ?)",
                           (9, "Legacy source", "Arabica", "2024-02-29", 1000))
        config = {"TESTING": True, "DATABASE": path}
        create_app(config)
        upgraded = create_app(config)
        page = upgraded.test_client().get("/market-prices").data
        self.assertIn(b"Legacy source", page)
        self.assertIn(b"<td>Unclassified</td>", page)
        self.assertNotIn(b"id=\"market-price-chart\"", page)
        with upgraded.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(tuple(db.execute("SELECT * FROM market_prices").fetchone()),
                             (9, "Legacy source", "Arabica", "2024-02-29", 1000, None, None))
