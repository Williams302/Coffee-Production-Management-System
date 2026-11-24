import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app, get_db


PLOT = {
    "name": "TEST-01",
    "variety": "Arabica",
    "tree_count": "120",
    "planting_date": "2024-02-29",
    "location": "Test location",
    "area_hectares": "1.25",
}


class PlotTests(unittest.TestCase):
    def setUp(self):
        directory = self.enter_temp_directory()
        self.config = {"TESTING": True, "DATABASE": directory / "test.sqlite3"}
        self.app = create_app(self.config)
        self.client = self.app.test_client()

    def enter_temp_directory(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def post_form(self, path, data):
        self.assertEqual(self.client.get(path).status_code, 200)
        with self.client.session_transaction() as session:
            token = session["plot_csrf_token"]
        return self.client.post(path, data=dict(data, csrf_token=token), follow_redirects=True)

    def add_plot(self, **overrides):
        response = self.post_form("/plots/new", dict(PLOT, **overrides))
        self.assertIn(b"Coffee plot added.", response.data)
        return response

    def plot_id(self, name="TEST-01"):
        with self.app.app_context():
            return get_db().execute("SELECT id FROM coffee_plots WHERE name = ?", (name,)).fetchone()[0]

    def test_create_and_view_plot(self):
        home = self.client.get("/")
        self.assertIn(b'href="/plots"', home.data)
        self.assertIn(b'class="metric-value mb-0">0</p>', home.data)
        self.assertIn(b"No coffee plots recorded yet.", self.client.get("/plots").data)
        response = self.add_plot()
        for value in PLOT.values():
            self.assertIn(value.encode(), response.data)
        self.assertEqual(response.history[0].status_code, 303)
        self.assertIn(b'class="metric-value mb-0">1</p>', self.client.get("/").data)

    def test_search_saved_plots(self):
        self.add_plot(location="Test 100% North")
        self.add_plot(name="TEST-02", location="Test 100 South", variety="Robusta")
        for query in ("test-01", "Arabica", "100%", "north"):
            with self.subTest(query=query):
                response = self.client.get("/plots", query_string={"q": query})
                self.assertIn(b"TEST-01", response.data)
                self.assertNotIn(b"TEST-02", response.data)
        response = self.client.get("/plots", query_string={"q": "' OR 1=1 --"})
        self.assertIn(b"No plots match your search.", response.data)

    def test_edit_plot(self):
        self.add_plot()
        plot_id = self.plot_id()
        path = f"/plots/{plot_id}/edit"
        self.assertIn(b'value="Test location"', self.client.get(path).data)
        response = self.post_form(path, dict(PLOT, name="TEST-UPDATED", variety="Robusta", tree_count="150"))
        self.assertIn(b"Coffee plot updated.", response.data)
        self.assertIn(b"TEST-UPDATED", response.data)
        self.assertNotIn(b"TEST-01", response.data)
        self.assertEqual(self.plot_id("TEST-UPDATED"), plot_id)
        with self.app.app_context():
            changed = get_db().execute("SELECT variety, tree_count FROM coffee_plots").fetchone()
            self.assertEqual(tuple(changed), ("Robusta", 150))

    def test_reject_invalid_fields(self):
        invalid_inputs = [
            ("name", " ", "Enter a valid plot identification number."),
            ("name", "\0TEST", "Enter a valid plot identification number."),
            ("variety", "Other", "Choose Arabica or Robusta."),
            ("tree_count", "0", "Enter a positive whole number"),
            ("tree_count", "2.5", "Enter a positive whole number"),
            ("tree_count", "9223372036854775808", "Enter a positive whole number"),
            ("planting_date", "2025-02-29", "Enter a valid planting date"),
            ("planting_date", "", "Enter a valid planting date"),
            ("location", "", "Enter a valid plot location."),
            ("area_hectares", "0", "Enter a positive plot area"),
            ("area_hectares", "inf", "Enter a positive plot area"),
        ]
        for field, value, error in invalid_inputs:
            with self.subTest(field=field, value=value):
                response = self.post_form("/plots/new", dict(PLOT, **{field: value}))
                self.assertEqual(response.status_code, 400)
                self.assertIn(error.encode(), response.data)
        self.assertIn(b"No coffee plots recorded yet.", self.client.get("/plots").data)

    def test_reject_duplicate_ids_on_create_and_edit(self):
        self.add_plot()
        self.add_plot(name="TEST-02")
        for path in ("/plots/new", f"/plots/{self.plot_id('TEST-02')}/edit"):
            with self.subTest(path=path):
                response = self.post_form(path, PLOT)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"This plot identification number is already in use.", response.data)
        response = self.post_form(f"/plots/{self.plot_id()}/edit", PLOT)
        self.assertIn(b"Coffee plot updated.", response.data)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM coffee_plots").fetchone()[0], 2)

    def test_delete_unlinked_plot_after_confirmation(self):
        self.add_plot()
        path = f"/plots/{self.plot_id()}/delete"
        self.assertIn(b"TEST-01", self.client.get(path).data)
        self.assertIn(b"TEST-01", self.client.get("/plots").data)
        response = self.post_form(path, {"plot_name": "TEST-01"})
        self.assertIn(b"Coffee plot deleted.", response.data)
        self.assertIn(b"No coffee plots recorded yet.", response.data)
        self.assertIn(b'class="metric-value mb-0">0</p>', self.client.get("/").data)
        self.assertEqual(self.client.get(path).status_code, 404)

    def test_delete_protects_activity_harvest_and_sales_records(self):
        for record in ("activity", "harvest", "sale"):
            with self.subTest(record=record):
                name = f"TEST-{record}"
                self.add_plot(name=name)
                plot_id = self.plot_id(name)
                with self.app.app_context(), get_db() as db:
                    if record == "activity":
                        db.execute("""
                            INSERT INTO farm_activities (plot_id, description, activity_date)
                            VALUES (?, ?, ?)
                        """, (plot_id, "Test activity", "2024-03-01"))
                    else:
                        harvest = db.execute("""
                            INSERT INTO harvests (plot_id, harvest_date, weight_kg)
                            VALUES (?, ?, ?)
                        """, (plot_id, "2024-03-01", 10)).lastrowid
                        if record == "sale":
                            db.execute("""
                                INSERT INTO sales (harvest_id, sale_date, weight_kg, revenue_ugx)
                                VALUES (?, ?, ?, ?)
                            """, (harvest, "2024-03-02", 5, 1000))
                response = self.post_form(f"/plots/{plot_id}/delete", {"plot_name": name})
                self.assertIn(b"has related activities, harvests or sales", response.data)
                self.assertEqual(self.plot_id(name), plot_id)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM farm_activities").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM harvests").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 1)

    def test_reject_post_without_csrf_and_stale_delete(self):
        self.add_plot()
        path = f"/plots/{self.plot_id()}/delete"
        response = self.client.post(path, data={"plot_name": "TEST-01"})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"This form has expired", response.data)
        response = self.post_form(path, {"plot_name": "TEST-OLD"})
        self.assertIn(b"The plot changed", response.data)
        self.assertIn(b"TEST-01", self.client.get("/plots").data)

    def test_upgrade_preserves_legacy_plots_and_dashboard(self):
        path = self.enter_temp_directory() / "legacy.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript("""
                CREATE TABLE coffee_plots (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
                    area_hectares REAL NOT NULL CHECK (area_hectares > 0)
                ) STRICT;
                CREATE TABLE farm_activities (
                    id INTEGER PRIMARY KEY,
                    plot_id INTEGER NOT NULL REFERENCES coffee_plots(id),
                    description TEXT NOT NULL,
                    activity_date TEXT NOT NULL
                ) STRICT;
                INSERT INTO coffee_plots VALUES (7, 'LEGACY-TEST', 2.0);
                INSERT INTO farm_activities VALUES (1, 7, 'Test legacy activity', '2024-03-01');
            """)
        db.close()
        config = dict(self.config, DATABASE=path)
        create_app(config)
        upgraded = create_app(config)
        client = upgraded.test_client()
        self.assertIn(b"LEGACY-TEST", client.get("/plots").data)
        self.assertIn(b'Test legacy activity', client.get("/").data)
        self.assertIn(b'value="LEGACY-TEST"', client.get("/plots/7/edit").data)
        with upgraded.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            plot = db.execute("SELECT name, area_hectares, variety FROM coffee_plots WHERE id = 7").fetchone()
            self.assertEqual(tuple(plot), ("LEGACY-TEST", 2.0, None))
