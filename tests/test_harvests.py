import json
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app import create_app, get_db, migrate_activities, migrate_plots


class HarvestTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "harvests.sqlite3"
        self.app = create_app({"TESTING": True, "DATABASE": self.path})
        self.client = self.app.test_client()
        with self.app.app_context(), get_db() as db:
            db.executemany("INSERT INTO coffee_plots (name, area_hectares, variety) VALUES (?, ?, ?)",
                           [("TEST-A", 1, "Arabica"), ("TEST-B", 2, "Robusta")])
        self.today = datetime.now(ZoneInfo("Africa/Kampala")).date()
        self.values = {
            "plot_id": "1", "harvest_date": self.today.isoformat(), "coffee_form": "Cherry",
            "weight_kg": "12.5", "season": "Test season 1", "notes": "Test note",
        }

    def post(self, path, values=None):
        self.assertEqual(self.client.get(path).status_code, 200)
        with self.client.session_transaction() as session:
            token = session["plot_csrf_token"]
        return self.client.post(path, data=dict(values or {}, csrf_token=token), follow_redirects=True)

    def add(self, **overrides):
        response = self.post("/harvests/new", dict(self.values, **overrides))
        self.assertIn(b"Harvest added.", response.data)
        self.assertEqual(response.history[0].status_code, 303)
        return response.data

    def series(self, page):
        match = re.search(rb"data-series='([^']+)'", page)
        return {series["label"]: series["data"] for series in json.loads(match[1])}

    def test_create_list_and_display_selected_plot_variety(self):
        self.assertIn(b'href="/harvests"', self.client.get("/").data)
        self.assertIn(b"No harvests recorded yet.", self.client.get("/harvests").data)
        form = self.client.get("/harvests/new").data
        self.assertIn(b'data-variety="Arabica"', form)
        self.assertIn(b'data-variety="Robusta"', form)
        self.assertNotIn(b'name="variety"', form)
        page = self.add(variety="Robusta")
        for value in (b"TEST-A", b"<td>Arabica</td>", b"<td>Cherry</td>", b"12.5", b"Test season 1",
                      self.today.isoformat().encode()):
            self.assertIn(value, page)
        with self.app.app_context():
            row = get_db().execute("SELECT plot_id, weight_kg, notes FROM harvests").fetchone()
            self.assertEqual(tuple(row), (1, 12.5, "Test note"))

    def test_edit_updates_plot_form_weight_season_and_notes(self):
        self.add()
        url = "/harvests/1/edit"
        self.assertIn(b'>Arabica</output>', self.client.get(url).data)
        page = self.post(url, dict(self.values, plot_id="2", coffee_form="Green Beans",
                                   weight_kg="7.25", season="Test season 2", notes="")).data
        self.assertIn(b"Harvest updated.", page)
        self.assertIn(b"<td>Robusta</td>", page)
        self.assertIn(b'>Robusta</output>', self.client.get(url).data)
        with self.app.app_context():
            row = get_db().execute("SELECT plot_id, coffee_form, weight_kg, season, notes FROM harvests").fetchone()
            self.assertEqual(tuple(row), (2, "Green Beans", 7.25, "Test season 2", None))

    def test_delete_requires_confirmation_and_protects_linked_sales(self):
        self.add()
        self.add()
        with self.app.app_context(), get_db() as db:
            db.execute("INSERT INTO sales (harvest_id, sale_date, weight_kg, revenue_ugx) VALUES (?, ?, ?, ?)",
                       (2, self.today.isoformat(), 1, 10))
        path = "/harvests/1/delete"
        self.assertIn(b"This action is permanent.", self.client.get(path).data)
        self.assertEqual(self.client.post(path).status_code, 400)
        self.assertIn(b"Harvest deleted.", self.post(path).data)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertIn(b"This harvest has linked sales", self.post("/harvests/2/delete").data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT id FROM harvests").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT harvest_id FROM sales").fetchone()[0], 2)

    def test_filters_individually_and_combined(self):
        prior = (self.today - timedelta(days=1)).isoformat()
        day = self.today.isoformat()
        self.add(weight_kg="11")
        self.add(weight_kg="22", plot_id="2")
        self.add(weight_kg="33", coffee_form="Parchment", season="Test season 2", harvest_date=prior)
        cases = [
            ({"plot_id": "1"}, {11, 33}),
            ({"coffee_form": "Cherry"}, {11, 22}),
            ({"season": "Test season 2"}, {33}),
            ({"end_date": prior}, {33}),
            ({"start_date": day}, {11, 22}),
            ({"plot_id": "1", "coffee_form": "Cherry", "season": "Test season 1",
              "start_date": day, "end_date": day}, {11}),
        ]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.get("/harvests", query_string=filters)
                self.assertEqual(response.status_code, 200)
                for harvest_id, weight in enumerate((11, 22, 33), 1):
                    edit_link = f'href="/harvests/{harvest_id}/edit"'.encode()
                    self.assertEqual(edit_link in response.data, weight in expected)
        for filters in ({"start_date": day, "end_date": prior}, {"start_date": "2025-02-29"},
                        {"plot_id": "999"}, {"coffee_form": "other"}, {"season": "' OR 1=1 --"}):
            with self.subTest(invalid_filters=filters):
                self.assertEqual(self.client.get("/harvests", query_string=filters).status_code, 400)

    def test_reject_invalid_harvest_inputs(self):
        cases = {
            "plot_id": ("", "999", "1 OR 1=1"),
            "harvest_date": ("", "2025-02-29", "2024-2-29"),
            "coffee_form": ("", "other"),
            "weight_kg": ("", "0", "-1", "NaN", "Infinity", "1e309", "1e-999"),
            "season": (" ", "x" * 81),
            "notes": ("x" * 2001,),
        }
        for field, invalid_values in cases.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value[:25]):
                    response = self.post("/harvests/new", dict(self.values, **{field: value}))
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(f'id="{field}-error"'.encode(), response.data)
        self.assertIn(b"No harvests recorded yet.", self.client.get("/harvests").data)
        self.add()
        response = self.post("/harvests/1/edit", dict(self.values, weight_kg="0"))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"<td>12.5</td>", self.client.get("/harvests").data)

    def test_summary_and_chart_keep_processing_stages_separate(self):
        home = self.client.get("/").data
        self.assertEqual(home.count(b">0 kg</dd>"), 3)
        self.add()
        self.add(weight_kg="9.5")
        self.add(weight_kg="6", coffee_form="Parchment")
        self.add(weight_kg="4", coffee_form="Green Beans", plot_id="2", season="Test season 2")
        page = self.client.get("/harvests").data
        self.assertRegex(page, rb"TEST-A</th>\s*<td>Test season 1</td>\s*<td>Cherry</td>\s*<td>22</td>")
        self.assertRegex(page, rb"TEST-B</th>\s*<td>Test season 2</td>\s*<td>Green Beans</td>\s*<td>4</td>")
        self.assertEqual(self.series(page), {"Cherry": [22], "Parchment": [6], "Green Beans": [4]})
        filtered = self.client.get("/harvests?coffee_form=Parchment").data
        self.assertEqual(self.series(filtered), {"Cherry": [0], "Parchment": [6], "Green Beans": [0]})
        home = self.client.get("/").data
        self.assertEqual({form: weights[-1] for form, weights in self.series(home).items()},
                         {"Cherry": 22, "Parchment": 6, "Green Beans": 4})
        for weight in (22, 6, 4):
            self.assertIn(f">{weight} kg</dd>".encode(), home)
        self.assertNotIn(b">32 kg", home)

    def test_migrate_existing_database_and_show_unclassified_weights(self):
        path = self.path.with_name("legacy.sqlite3")
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        db.executescript(Path(self.app.root_path, "schema.sql").read_text())
        migrate_plots(db)
        migrate_activities(db)
        with db:
            db.execute("INSERT INTO coffee_plots (id, name, area_hectares) VALUES (?, ?, ?)", (7, "LEGACY", 2))
            db.execute("INSERT INTO harvests VALUES (?, ?, ?, ?)", (9, 7, self.today.isoformat(), 17))
            db.execute("INSERT INTO sales VALUES (?, ?, ?, ?, ?)", (11, 9, self.today.isoformat(), 1, 10))
        config = {"TESTING": True, "DATABASE": path}
        create_app(config)
        upgraded = create_app(config)
        client = upgraded.test_client()
        self.assertIn(b"<td>Unclassified</td>", client.get("/harvests").data)
        self.assertEqual(self.series(client.get("/").data)["Unclassified"][-1], 17)
        with upgraded.app_context():
            copy = get_db()
            self.assertEqual(copy.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(copy.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(tuple(copy.execute("SELECT * FROM harvests WHERE id = 9").fetchone()),
                             (9, 7, self.today.isoformat(), 17, None, None, None))
            self.assertEqual(copy.execute("SELECT harvest_id FROM sales").fetchone()[0], 9)
