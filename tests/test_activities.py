import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app, get_db


class ActivityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "coffee.sqlite3"
        self.app = create_app({"TESTING": True, "DATABASE": self.database})
        self.client = self.app.test_client()
        with self.app.app_context(), get_db() as db:
            db.executemany("INSERT INTO coffee_plots (name, area_hectares) VALUES (?, ?)",
                           [("TEST-A", 1), ("TEST-B", 2)])
        self.values = {
            "plot_id": "1", "activity_type": "fertilising", "activity_date": "2024-02-29",
            "inputs_used": "Test input", "quantity": "2.5", "unit": "kg",
            "cost_ugx": "1200", "notes": "Test note",
        }

    def post(self, path, **values):
        self.assertEqual(self.client.get(path).status_code, 200)
        with self.client.session_transaction() as session:
            token = session["plot_csrf_token"]
        return self.client.post(path, data=dict(values, csrf_token=token), follow_redirects=True)

    def add(self, **overrides):
        response = self.post("/activities/new", **dict(self.values, **overrides))
        self.assertIn(b"Farm activity added.", response.data)
        self.assertEqual(response.history[0].status_code, 303)
        return response

    def test_create_list_and_dashboard(self):
        self.assertIn(b'href="/activities"', self.client.get("/").data)
        self.assertIn(b"No farm activities recorded yet.", self.client.get("/activities").data)
        form = self.client.get("/activities/new").data
        self.assertIn(b'<option value="1">TEST-A</option>', form)
        self.assertIn(b'<option value="2">TEST-B</option>', form)
        response = self.add()
        for displayed in (b"TEST-A", b"Fertilising", b"2024-02-29", b"Test input", b"2.5 kg", b"1,200"):
            self.assertIn(displayed, response.data)
        home = self.client.get("/").data
        self.assertIn(b"Fertilising", home)
        self.assertIn(b'class="metric-value mb-0">1</p>', home)
        with self.app.app_context():
            saved = get_db().execute("SELECT plot_id, quantity, cost_ugx, notes FROM farm_activities").fetchone()
        self.assertEqual(tuple(saved), (1, 2.5, 1200, "Test note"))

    def test_edit_activity_and_allow_zero_values(self):
        self.add()
        self.assertIn(b"Test note", self.client.get("/activities/1/edit").data)
        response = self.post("/activities/1/edit", **dict(
            self.values, plot_id="2", activity_type="pruning", inputs_used="None",
            quantity="0", unit="N/A", cost_ugx="0", notes="",
        ))
        self.assertIn(b"Farm activity updated.", response.data)
        self.assertIn(b"Pruning", self.client.get("/").data)
        with self.app.app_context():
            saved = get_db().execute("""
                SELECT plot_id, activity_type, quantity, cost_ugx, notes FROM farm_activities
            """).fetchone()
        self.assertEqual(tuple(saved), (2, "pruning", 0, 0, None))

    def test_delete_requires_confirmation_and_csrf(self):
        self.add()
        path = "/activities/1/delete"
        self.assertIn(b"This action is permanent.", self.client.get(path).data)
        self.assertEqual(self.client.post(path).status_code, 400)
        self.assertIn(b"Test input", self.client.get("/activities").data)
        response = self.post(path)
        self.assertIn(b"Farm activity deleted.", response.data)
        self.assertIn(b"No farm activities recorded yet.", response.data)
        self.assertIn(b"No farm activities recorded yet.", self.client.get("/").data)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_filters_individually_and_combined(self):
        self.add(inputs_used="MATCH-ONE")
        self.add(plot_id="2", inputs_used="MATCH-TWO")
        self.add(activity_type="weeding", inputs_used="MATCH-THREE")
        self.add(activity_date="2024-03-01", inputs_used="MATCH-FOUR")
        cases = [
            ({"plot_id": "1"}, {1, 3, 4}),
            ({"activity_type": "fertilising"}, {1, 2, 4}),
            ({"activity_date": "2024-02-29"}, {1, 2, 3}),
            ({"plot_id": "1", "activity_type": "fertilising", "activity_date": "2024-02-29"}, {1}),
            ({"plot_id": "2", "activity_type": "weeding"}, set()),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.get("/activities", query_string=filters)
                self.assertEqual(response.status_code, 200)
                for index, label in enumerate((b"ONE", b"TWO", b"THREE", b"FOUR"), 1):
                    self.assertEqual(b"MATCH-" + label in response.data, index in expected)
        invalid = self.client.get("/activities", query_string={"activity_type": "' OR 1=1 --"})
        self.assertEqual(invalid.status_code, 400)
        self.assertNotIn(b"MATCH-ONE", invalid.data)

    def test_reject_invalid_form_values(self):
        cases = {
            "plot_id": ("", "999", "1 OR 1=1"),
            "activity_type": ("", "other"),
            "activity_date": ("", "2025-02-29", "2024-2-29"),
            "inputs_used": ("", "x" * 201),
            "quantity": ("", "-1", "nan", "inf", "-1e-999"),
            "unit": ("", "x" * 41),
            "cost_ugx": ("", "-1", "1.5", "9223372036854775808"),
            "notes": ("x" * 2001,),
        }
        for field, invalid_values in cases.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value[:25]):
                    response = self.post("/activities/new", **dict(self.values, **{field: value}))
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(f'id="{field}-error"'.encode(), response.data)
        self.assertIn(b"No farm activities recorded yet.", self.client.get("/activities").data)
        self.add()
        response = self.post("/activities/1/edit", **dict(self.values, plot_id="999"))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Test input", self.client.get("/activities").data)

    def test_legacy_migration_preserves_plot_and_activity(self):
        path = self.database.with_name("legacy.sqlite3")
        with sqlite3.connect(path) as db:
            db.executescript(Path(self.app.root_path, "schema.sql").read_text())
            db.executescript("""
                ALTER TABLE coffee_plots ADD COLUMN variety TEXT;
                ALTER TABLE coffee_plots ADD COLUMN tree_count INTEGER;
                ALTER TABLE coffee_plots ADD COLUMN planting_date TEXT;
                ALTER TABLE coffee_plots ADD COLUMN location TEXT;
                PRAGMA user_version = 1;
            """)
            db.execute("INSERT INTO coffee_plots VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (7, "LEGACY-TEST", 1, "Robusta", 12, "2024-01-01", "Test location"))
            db.execute("INSERT INTO farm_activities VALUES (?, ?, ?, ?)",
                       (9, 7, "Legacy test activity", "2024-02-01"))
        db.close()
        config = {"TESTING": True, "DATABASE": path}
        create_app(config)
        upgraded = create_app(config)
        self.client = upgraded.test_client()
        self.assertIn(b"Legacy test activity", self.client.get("/activities").data)
        self.assertIn(b"Legacy test activity", self.client.get("/").data)
        edited = self.post("/activities/9/edit", **dict(self.values, plot_id="7"))
        self.assertIn(b"Farm activity updated.", edited.data)
        with upgraded.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            plot = db.execute("SELECT id, name, tree_count FROM coffee_plots").fetchone()
            record = db.execute("SELECT id, plot_id, description, activity_type FROM farm_activities").fetchone()
        self.assertEqual(tuple(plot), (7, "LEGACY-TEST", 12))
        self.assertEqual(tuple(record), (9, 7, "Legacy test activity", "fertilising"))
