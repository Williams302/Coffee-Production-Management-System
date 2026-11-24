import json
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app, get_db, migrate_activities, migrate_harvests, migrate_plots
from sales import sale_totals


class SalesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sales.sqlite3"
        self.app = create_app({"TESTING": True, "DATABASE": self.path})
        self.client = self.app.test_client()
        with self.app.app_context(), get_db() as db:
            db.executemany("INSERT INTO coffee_plots(name, area_hectares) VALUES (?, ?)",
                           [("TEST-A", 1), ("TEST-B", 1)])
            db.execute("INSERT INTO harvests(plot_id, harvest_date, weight_kg) VALUES (?, ?, ?)",
                       (1, "2024-02-20", 100))
        self.values = {
            "sale_date": "2024-02-29", "plot_id": "1", "coffee_form": "Cherry",
            "buyer": "Test Buyer A", "quantity_kg": "2.345", "unit_price_ugx": "100",
            "payment_method": "Mobile Money", "payment_status": "Partially Paid",
            "received_ugx": "80", "notes": "Test note",
        }

    def post(self, path, data=None):
        self.assertEqual(self.client.get(path).status_code, 200)
        with self.client.session_transaction() as session:
            token = session["plot_csrf_token"]
        return self.client.post(path, data=dict(data or {}, csrf_token=token), follow_redirects=True)

    def add(self, **overrides):
        response = self.post("/sales/new", dict(self.values, **overrides))
        self.assertIn(b"Sale added.", response.data)
        self.assertEqual(response.history[0].status_code, 303)
        return response.data

    def test_create_view_exact_calculation_and_dashboard(self):
        self.assertIn(b'href="/sales"', self.client.get("/").data)
        self.assertIn(b"No sales recorded yet.", self.client.get("/sales").data)
        page = self.add(revenue_ugx="1", balance="1", harvest_id="1")
        for value in (b"TEST-A", b"Cherry", b"Test Buyer A", b"2.345", b"<td>235</td>",
                      b"<td>80</td>", b"<td>155</td>", b"<td>Partially Paid</td>"):
            self.assertIn(value, page)
        trend = re.search(rb"data-trend='([^']+)'", page)
        self.assertEqual(json.loads(trend[1]), [["2024-02-29", "235"]])
        dashboard = self.client.get("/").data
        for amount in (235, 80, 155):
            self.assertIn(f">UGX {amount}</dd>".encode(), dashboard)
        with self.app.app_context():
            db = get_db()
            sale = db.execute("SELECT harvest_id, quantity_grams, revenue_ugx, received_ugx FROM sales").fetchone()
            self.assertEqual(tuple(sale), (None, 2345, 235, 80))
            self.assertEqual(db.execute("SELECT weight_kg FROM harvests").fetchone()[0], 100)

    def test_edit_updates_sale_and_recalculates_paid_amount(self):
        self.add()
        path = "/sales/1/edit"
        self.assertIn(b"Test note", self.client.get(path).data)
        response = self.post(path, dict(self.values, plot_id="2", buyer="Updated Buyer",
                                       quantity_kg="3", unit_price_ugx="123", coffee_form="Parchment",
                                       payment_status="Paid", received_ugx="", notes=""))
        self.assertIn(b"Sale updated.", response.data)
        self.assertIn(b"Updated Buyer", response.data)
        self.assertIn(b"<td>Paid</td>", response.data)
        with self.app.app_context():
            row = get_db().execute("SELECT plot_id, quantity_grams, revenue_ugx, received_ugx, notes FROM sales").fetchone()
            self.assertEqual(tuple(row), (2, 3000, 369, 369, None))

    def test_delete_confirmation_csrf_and_foreign_keys(self):
        self.add()
        path = "/sales/1/delete"
        self.assertIn(b"This action is permanent.", self.client.get(path).data)
        self.assertEqual(self.client.post(path).status_code, 400)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute("DELETE FROM coffee_plots WHERE id = ?", (1,))
            with self.assertRaises(sqlite3.IntegrityError), db:
                db.execute("UPDATE sales SET plot_id = ? WHERE id = ?", (999, 1))
        response = self.post(path)
        self.assertIn(b"Sale deleted.", response.data)
        self.assertIn(b"No sales recorded yet.", response.data)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_search_and_combined_filters(self):
        self.add()
        self.add(plot_id="2", buyer="Test Buyer B", coffee_form="Green Beans",
                 sale_date="2024-03-01", payment_status="Paid", received_ugx="")
        cases = [
            ({"q": "buyer a"}, {1}),
            ({"q": "test-b"}, {2}),
            ({"plot_id": "2"}, {2}),
            ({"coffee_form": "Cherry"}, {1}),
            ({"payment_status": "Paid"}, {2}),
            ({"start_date": "2024-02-29", "end_date": "2024-02-29"}, {1}),
            ({"q": "buyer", "plot_id": "2", "payment_status": "Paid", "coffee_form": "Green Beans"}, {2}),
            ({"q": "%"}, set()),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.get("/sales", query_string=filters)
                self.assertEqual(response.status_code, 200)
                for sale_id in (1, 2):
                    self.assertEqual(f'href="/sales/{sale_id}/edit"'.encode() in response.data,
                                     sale_id in expected)
        for filters in ({"start_date": "2025-02-29"}, {"start_date": "2024-03-01", "end_date": "2024-02-29"},
                        {"plot_id": "999"}, {"payment_status": "invalid"}, {"coffee_form": "invalid"}):
            with self.subTest(invalid=filters):
                self.assertEqual(self.client.get("/sales", query_string=filters).status_code, 400)

    def test_server_validation_rejects_invalid_fields_and_payment_amounts(self):
        cases = {
            "plot_id": ("999", "1 OR 1=1"), "sale_date": ("2025-02-29", ""),
            "coffee_form": ("Other",), "buyer": (" ", "X" * 151),
            "quantity_kg": ("0", "-1", "nan", "0.0001"), "unit_price_ugx": ("-1", "2.5", "9223372036854775807"),
            "payment_method": ("Other",), "payment_status": ("Other",),
            "received_ugx": ("", "-1", "0", "235", "236", "1.5"), "notes": ("X" * 2001,),
        }
        for field, invalid in cases.items():
            for value in invalid:
                with self.subTest(field=field, value=value[:25]):
                    response = self.post("/sales/new", dict(self.values, **{field: value}))
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(f'id="{field}-error"'.encode(), response.data)
        for status in ("Pending", "Paid"):
            response = self.post("/sales/new", dict(self.values, payment_status=status))
            self.assertEqual(response.status_code, 400)
            self.assertIn(b'id="received_ugx-error"', response.data)
        response = self.post("/sales/new", dict(self.values, payment_status="Pending",
                                                unit_price_ugx="0", received_ugx=""))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b'id="payment_status-error"', response.data)
        self.assertIn(b"No sales recorded yet.", self.client.get("/sales").data)

    def test_zero_value_pending_and_large_amounts_remain_exact(self):
        self.add(payment_status="Paid", unit_price_ugx="0", received_ugx="")
        self.add(payment_status="Pending", received_ugx="")
        maximum = 2**63 - 1
        for _ in range(2):
            self.add(quantity_kg="1", unit_price_ugx=str(maximum), payment_status="Paid", received_ugx="")
        with self.app.app_context():
            self.assertEqual(sale_totals(get_db()), {
                "value": 2 * maximum + 235, "received": 2 * maximum, "outstanding": 235, "unknown": 0,
            })
        page = self.client.get("/").data
        self.assertIn(f"UGX {2 * maximum + 235:,}".encode(), page)

    def test_migration_preserves_legacy_sale_and_unknown_payment(self):
        path = self.path.with_name("legacy.sqlite3")
        with closing(sqlite3.connect(path)) as db:
            db.executescript(Path(self.app.root_path, "schema.sql").read_text())
            migrate_plots(db)
            migrate_activities(db)
            migrate_harvests(db)
            with db:
                db.execute("INSERT INTO coffee_plots(id, name, area_hectares) VALUES (7, 'LEGACY', 1)")
                db.execute("INSERT INTO harvests(id, plot_id, harvest_date, weight_kg, coffee_form) "
                           "VALUES (8, 7, '2024-02-20', 10, 'Parchment')")
                db.execute("INSERT INTO sales VALUES (9, 8, '2024-02-25', 2.5, 1000)")
        config = {"TESTING": True, "DATABASE": path}
        create_app(config)
        upgraded = create_app(config)
        self.client = upgraded.test_client()
        page = self.client.get("/sales?payment_status=Unknown").data
        for value in (b"LEGACY", b"Parchment", b"<td>Unknown</td>", b"<td>1,000</td>"):
            self.assertIn(value, page)
        self.assertIn(b"Sales with unknown payments (1)", self.client.get("/").data)
        with upgraded.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            row = db.execute("SELECT id, harvest_id, sale_date, weight_kg, revenue_ugx, received_ugx FROM sales").fetchone()
            self.assertEqual(tuple(row), (9, 8, "2024-02-25", 2.5, 1000, None))
        result = self.post("/sales/9/edit", dict(self.values, plot_id="7", coffee_form="Parchment",
                                                quantity_kg="2.5", unit_price_ugx="400",
                                                payment_status="Paid", received_ugx=""))
        self.assertIn(b"Sale updated.", result.data)
        with upgraded.app_context():
            row = get_db().execute("SELECT plot_id, harvest_id, revenue_ugx, received_ugx FROM sales").fetchone()
            self.assertEqual(tuple(row), (7, 8, 1000, 1000))
