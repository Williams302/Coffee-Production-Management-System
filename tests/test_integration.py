import csv
import io
import sqlite3
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from app import create_app, get_db
from market_prices import sale_comparisons


PLOT = {"name": "QA-PLOT", "variety": "Arabica", "tree_count": "40",
        "planting_date": "2024-01-01", "location": "Test location", "area_hectares": "1"}
ACTIVITY = {"plot_id": "1", "activity_type": "pruning", "activity_date": "2024-03-04",
            "inputs_used": "Test input", "quantity": "2", "unit": "kg", "cost_ugx": "400"}
HARVEST = {"plot_id": "1", "harvest_date": "2024-03-04", "coffee_form": "Cherry",
           "weight_kg": "12.5", "season": "Test season"}
SALE = {"plot_id": "1", "sale_date": "2024-03-05", "coffee_form": "Cherry",
        "buyer": "Test buyer", "quantity_kg": "2.345", "unit_price_ugx": "100",
        "payment_method": "Mobile Money", "payment_status": "Partially Paid", "received_ugx": "80"}
PRICE = {"price_date": "2024-03-04", "coffee_type": "Arabica", "coffee_form": "Cherry",
         "price_ugx_per_kg": "90", "market": "Test source"}


class PageMarkup(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.tags = []
        self.feed(page.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def attrs(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]


class SystemIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = {"DATABASE": Path(temp.name) / "integration.sqlite3"}
        self.app = create_app(dict(self.config, TESTING=True))
        self.client = self.app.test_client()

    def submit(self, path, values=None):
        form = self.client.get(path)
        self.assertEqual(form.status_code, 200, path)
        with self.client.session_transaction() as session:
            token = session["plot_csrf_token"]
        return self.client.post(path, data=dict(values or {}, csrf_token=token), follow_redirects=True)

    def add(self, path, values, message):
        response = self.submit(path, values)
        self.assertEqual(response.status_code, 200, path)
        self.assertEqual(response.history[0].status_code, 303, path)
        self.assertIn(message.encode(), response.data)

    def register_plot(self, **overrides):
        self.add("/plots/new", dict(PLOT, **overrides), "Coffee plot added.")

    def save_workflow(self):
        self.register_plot()
        self.add("/activities/new", ACTIVITY, "Farm activity added.")
        for form, weight in (("Cherry", "12.5"), ("Parchment", "5.5"), ("Green Beans", "2.25")):
            self.add("/harvests/new", dict(HARVEST, coffee_form=form, weight_kg=weight), "Harvest added.")
        self.add("/sales/new", SALE, "Sale added.")
        self.add("/market-prices/new", PRICE, "Market price added.")

    def report(self, name, **query):
        result = self.client.get(f"/reports/{name}.csv", query_string=query)
        self.assertEqual(result.status_code, 200)
        return list(csv.DictReader(io.StringIO(result.get_data(as_text=True))))

    def test_full_workflow_and_reports_share_saved_calculations(self):
        self.save_workflow()
        rows = self.report("production", season=HARVEST["season"])
        self.assertEqual({row["Coffee form"]: row["Weight (kg)"] for row in rows},
                         {"Cherry": "12.5", "Parchment": "5.5", "Green Beans": "2.25"})
        performance = {row["Measure"]: row["Value"] for row in self.report("performance")}
        self.assertEqual(performance["Total sales value"], "235")
        self.assertEqual(performance["Payments received"], "80")
        self.assertEqual(performance["Outstanding balances"], "155")
        self.assertEqual(performance["Recorded activity costs"], "400")
        self.assertEqual(performance["Limited operating margin estimate"], "-165")
        matches = [row for row in self.report("market-prices") if row["Section"] == "Comparable sale prices"]
        self.assertEqual([(row["Market or source"], row["Sale minus market (UGX/kg)"]) for row in matches],
                         [("Test source", "10")])
        dashboard = self.client.get("/").data
        for value in (b"12.5 kg", b"5.5 kg", b"2.25 kg", b"UGX 235", b"UGX 80", b"UGX 155", b"Pruning"):
            self.assertIn(value, dashboard)
        self.assertNotIn(b"20.25 kg", dashboard)

    def test_edits_and_deletes_are_reflected_across_modules(self):
        self.save_workflow()
        updates = [
            ("/plots/1/edit", dict(PLOT, name="QA-RENAMED"), b"Coffee plot updated."),
            ("/activities/1/edit", dict(ACTIVITY, cost_ugx="650"), b"Farm activity updated."),
            ("/harvests/1/edit", dict(HARVEST, weight_kg="14"), b"Harvest updated."),
            ("/sales/1/edit", dict(SALE, payment_status="Paid", received_ugx=""), b"Sale updated."),
            ("/market-prices/1/edit", dict(PRICE, price_ugx_per_kg="120"), b"Market price updated."),
        ]
        for path, values, message in updates:
            with self.subTest(path=path):
                self.assertIn(message, self.submit(path, values).data)
        with self.app.app_context():
            db = get_db()
            for query in ("SELECT plot_id FROM farm_activities", "SELECT plot_id FROM harvests", "SELECT plot_id FROM sales"):
                self.assertTrue(all(row[0] == 1 for row in db.execute(query)))
        self.assertEqual({row["Plot"] for row in self.report("production")}, {"QA-RENAMED"})
        self.assertEqual(self.report("sales")[0]["Balance (UGX)"], "0")
        self.assertEqual(self.report("market-prices")[-1]["Sale minus market (UGX/kg)"], "-20")
        self.assertIn(b"cannot be deleted", self.submit("/plots/1/delete", {"plot_name": "QA-RENAMED"}).data)
        for path in ("/activities/1/delete", "/harvests/1/delete", "/harvests/2/delete", "/harvests/3/delete",
                     "/market-prices/1/delete"):
            self.assertIn(b"deleted.", self.submit(path).data)
        # At this point the direct sale alone must protect the plot.
        self.assertIn(b"cannot be deleted", self.submit("/plots/1/delete", {"plot_name": "QA-RENAMED"}).data)
        self.assertIn(b"Sale deleted.", self.submit("/sales/1/delete").data)
        self.assertIn(b"Coffee plot deleted.", self.submit("/plots/1/delete", {"plot_name": "QA-RENAMED"}).data)
        for name in ("production", "activities", "sales", "market-prices"):
            self.assertEqual(self.report(name), [])

    def test_missing_records_and_read_only_methods(self):
        for module in ("plots", "activities", "harvests", "sales", "market-prices"):
            for action in ("edit", "delete"):
                with self.subTest(module=module, action=action):
                    path = f"/{module}/999999999999999999999999/{action}"
                    self.assertEqual(self.client.get(path).status_code, 404)
                    self.assertEqual(self.client.post(path).status_code, 404)
            self.assertEqual(self.client.post(f"/{module}").status_code, 405)
        self.assertEqual(self.client.post("/reports").status_code, 405)
        self.assertEqual(self.client.get("/reports/not-a-report.csv").status_code, 404)

    def test_validation_and_csrf_cover_all_mutating_modules(self):
        self.register_plot()
        for path, values in (
            ("/plots/new", PLOT),
            ("/activities/new", dict(ACTIVITY, plot_id="missing")),
            ("/harvests/new", dict(HARVEST, weight_kg="Infinity")),
            ("/sales/new", dict(SALE, received_ugx="999")),
            ("/market-prices/new", dict(PRICE, price_date="2024-02-30")),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data=values).status_code, 400)
                invalid = self.submit(path, values)
                self.assertEqual(invalid.status_code, 400)
                self.assertIn(b"Review the highlighted fields", invalid.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT COUNT(*) FROM coffee_plots").fetchone()[0], 1)
            for query in ("SELECT COUNT(*) FROM farm_activities", "SELECT COUNT(*) FROM harvests",
                          "SELECT COUNT(*) FROM sales", "SELECT COUNT(*) FROM market_prices"):
                self.assertEqual(db.execute(query).fetchone()[0], 0)

    def test_user_text_remains_literal_in_queries_html_and_csv(self):
        self.register_plot(name="QA O'Brien_%")
        self.register_plot(name="Different plot")
        self.add("/sales/new", dict(SALE, buyer="O'Brien <test> 100%"), "Sale added.")
        query = self.client.get("/plots", query_string={"q": "O'Brien_%"})
        self.assertIn(b"QA O&#39;Brien_%", query.data)
        self.assertNotIn(b"Different plot", query.data)
        page = self.client.get("/sales", query_string={"q": "O'Brien <test>"}).data
        self.assertIn(b"O&#39;Brien &lt;test&gt; 100%", page)
        self.assertNotIn(b"<test>", page)
        self.assertEqual(self.report("sales")[0]["Buyer"], "O'Brien <test> 100%")
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM coffee_plots").fetchone()[0], 2)

    def test_foreign_key_integrity_on_connections_and_failed_updates(self):
        self.save_workflow()
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            for query in ("UPDATE farm_activities SET plot_id = ? WHERE id = 1",
                          "UPDATE harvests SET plot_id = ? WHERE id = 1",
                          "UPDATE sales SET plot_id = ? WHERE id = 1"):
                with self.assertRaises(sqlite3.IntegrityError), db:
                    db.execute(query, (999,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_empty_pages_date_boundaries_and_exports(self):
        for path, message in (
            ("/plots", b"No coffee plots recorded yet"),
            ("/activities", b"No farm activities recorded yet"),
            ("/harvests", b"No harvests recorded yet"),
            ("/sales", b"No sales recorded yet"),
            ("/market-prices", b"No market prices recorded yet"),
            ("/reports", b"No recorded data for this selection"),
        ):
            self.assertIn(message, self.client.get(path).data)
        self.save_workflow()
        query = {"period": "custom", "start_date": "2024-03-04", "end_date": "2024-03-05"}
        self.assertEqual(len(self.report("production", **query)), 3)
        self.assertEqual(len(self.report("sales", **query)), 1)
        self.assertEqual(self.report("sales", period="daily", date="2024-03-04"), [])
        self.assertIn(b"2024-03-04 to 2024-03-05", self.client.get("/reports/print", query_string=query).data)

    def test_responsive_and_accessible_markup_basics(self):
        self.save_workflow()
        for path in ("/", "/plots", "/activities", "/harvests", "/sales", "/market-prices", "/reports",
                     "/plots/new", "/activities/new", "/harvests/new", "/sales/new", "/market-prices/new"):
            with self.subTest(path=path):
                page = PageMarkup(self.client.get(path))
                self.assertTrue(any(meta.get("name") == "viewport" for meta in page.attrs("meta")))
                self.assertEqual(len(page.attrs("h1")), 1)
                self.assertEqual(len(page.attrs("main")), 1)
                self.assertTrue(any(nav.get("aria-label") for nav in page.attrs("nav")))
                self.assertTrue(any("flex-wrap" in nav.get("class", "") for nav in page.attrs("nav")))
                labels = {label.get("for") for label in page.attrs("label")}
                fields = [item for tag in ("input", "select", "textarea") for item in page.attrs(tag)
                          if item.get("type") != "hidden"]
                self.assertTrue(all(field.get("id") in labels for field in fields))
                self.assertTrue(all(canvas.get("aria-label") for canvas in page.attrs("canvas")))
                if page.attrs("table"):
                    self.assertTrue(any("table-responsive" in div.get("class", "") for div in page.attrs("div")))
                    self.assertTrue(any(th.get("scope") == "col" for th in page.attrs("th")))

    def test_local_configuration_and_session_cookie_defaults(self):
        with patch.dict("os.environ", {"SECRET_KEY": "", "FLASK_DEBUG": "0"}):
            app = create_app(self.config)
            other = create_app(self.config)
        self.assertFalse(app.debug)
        self.assertTrue(app.secret_key)
        self.assertNotEqual(app.secret_key, other.secret_key)
        response = app.test_client().get("/plots/new")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])

    def test_linked_harvest_cannot_reassign_legacy_sale_to_another_plot(self):
        self.register_plot()
        self.register_plot(name="QA-OTHER", variety="Robusta")
        self.add("/harvests/new", HARVEST, "Harvest added.")
        with self.app.app_context(), get_db() as db:
            db.execute("INSERT INTO sales (harvest_id, sale_date, weight_kg, revenue_ugx) VALUES (?, ?, ?, ?)",
                       (1, "2024-03-05", 2, 200))
        response = self.submit("/harvests/1/edit", dict(HARVEST, plot_id="2"))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"linked sales", response.data)
        with self.app.app_context():
            db = get_db()
            self.assertEqual(db.execute("SELECT plot_id FROM harvests WHERE id = 1").fetchone()[0], 1)
            self.assertEqual(sale_comparisons(db, "")[0]["plot_name"], PLOT["name"])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertIn(b"Harvest updated.", self.submit("/harvests/1/edit", dict(HARVEST, notes="Reviewed")).data)
