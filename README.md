# Coffee Production Management System

A Flask and SQLite dashboard for coffee plots, harvest weight, sales revenue and
farm activities. The harvest chart shows the last seven calendar days in the
Africa/Kampala time zone. The Coffee Plots page lets you add, search, edit and
delete plot records.

## Run on Ubuntu

Use Python 3.9 or later with SQLite 3.37 or later. Ubuntu 24.04 provides both.
From the project folder, run:

```bash
sudo apt update
sudo apt install python3 python3-venv tzdata
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app run
```

Open <http://127.0.0.1:5000/>. Flask creates `instance/coffee.sqlite3` and its
tables on startup. The dashboard displays zero totals until records exist.
Bootstrap 5 and Chart.js load from jsDelivr, so styling and charts use browser
internet access. The daily harvest values are also available as text.

The development server binds to localhost by default with debug mode off.
Stop it with Ctrl+C.

## Manage coffee plots

Select **Coffee Plots** in the navigation and use **Add plot** to register a
plot. Enter a unique plot identification number, choose Arabica or Robusta, and
provide a positive tree count, planting date, location and area in hectares.
Plot IDs are case-sensitive. Search by plot ID, variety or location.

Choose **Edit** to update a plot or **Delete** to review and confirm its removal.
Plots with activities or harvests are protected from deletion, including
harvests linked to sales. The dashboard count reflects saved plots.

On first startup after upgrading, Flask adds the new plot fields to the
existing database. Existing plot names serve as plot IDs. The new fields
appear blank on older records until you edit them.

Set the `SECRET_KEY` environment variable if sessions need to survive restarts
or be shared by multiple application workers. The local default uses an
ephemeral key.

## Manage farm activities

Open **Farm Activities** and select **Add activity**. Choose a registered plot
and an activity type, then enter the date, inputs used, quantity, unit and
cost in whole UGX. Notes are optional. If no inputs were used, enter
`None` for inputs, `0` for quantity and `N/A` for unit. Enter `0` for
a cost-free activity.

Use one or more filters to narrow the list by plot, activity type or exact
date. Use **Edit** to update a record or **Delete** to review and confirm its
removal. The dashboard shows the five latest saved activities by activity date.

On startup the app adds the new activity fields to the existing SQLite
database. Older activities keep their plot, date and original description.
Their new fields stay blank until filled in through the edit form.

## Record coffee production

Open **Production** and select **Add harvest**. Choose a registered plot and
enter the harvest date, coffee form, weight in kilograms and season. Notes are
optional. The variety comes from the plot, and several harvests can be saved for
the same plot and season. Use a consistent season label across related harvests.

Filter by plot, coffee form, production season or an inclusive date range. The
summary groups weights by plot, season and form. The production chart groups
the same filtered records by date and form. The dashboard shows all-time
weights by form and a seven-day trend. Cherry, Parchment and Green Beans can
represent successive processing stages, so their weights are shown separately.

Choose **Edit** to update a harvest or **Delete** to review and confirm its
removal. A harvest with linked sales is protected from deletion. On startup, the
app adds harvest fields to the existing database. Older harvests appear as
**Unclassified** until you record their form and season.

## Manage coffee sales

Open **Sales** and select **Add sale**. Choose a plot, sale date and coffee
form, then enter the buyer, quantity in kg, whole-UGX price per kg, payment method
and payment status. Notes are optional. Search by buyer or plot, and combine
plot, form, payment status and inclusive date filters. The daily sales chart
follows the active filters.

Enter quantities to three decimal places at most. The server multiplies
integer grams by the whole-UGX unit price and rounds the result to the nearest
shilling, with half a shilling rounded up. Pending records zero received.
Partially Paid requires a received amount greater than zero and below the
total. Paid records the full total, including zero for a zero-value sale.
The outstanding balance is the total minus the amount received.

Use **Edit** to update a sale or **Delete** to review and confirm its removal.
Sales are recorded against plots and leave harvest weights unchanged.

On startup, the sales migration preserves the original rows and their harvest
links. Older sales appear with **Unknown** payment status until you supply their
payment details. Their original value contributes to the dashboard sales
total, while paid and outstanding balances cover sales with recorded payments.
When editing an older sale, its linked harvest determines the plot.

## Track market prices

Open **Market Prices** and select **Add market price** to enter a date, variety,
coffee form, whole-UGX price per kg, and market or source. Notes are optional.
Use **Edit** to correct a quote or **Delete** to review and confirm its removal.
Record one price per market, variety, form, and date. Use consistent source
names so observations appear together.

The table and trend chart support inclusive date, variety, form, and source
filters. Each chart line represents one source, variety, and form. The
dashboard shows the latest manually recorded price with its source and date.

The sales comparison uses the variety from the sale's plot and the sale's
coffee form. It selects the most recent matching price on or before the sale
date. Selecting a source limits comparisons to that source. With all sources
selected, prices on the same date are resolved by the most recently added
record. The difference is the sale unit price minus the market price, in
UGX/kg. If the sale lacks a unit price, variety, or form, or no matching
price is available, the comparison displays **No comparable market price**.

The startup migration preserves older price records. They appear as
**Unclassified** and stay out of trends and comparisons until you record their
coffee form and confirm the variety and date.

## Generate, export and print reports

Open **Reports** to view production, farm activities, sales, market prices
and farm performance. Select **Daily** for a single date, **Weekly** for the
Monday–Sunday week containing the selected date, or **Custom range** for
inclusive start and end dates. Daily and weekly default to today's date in
Kampala when the date is blank. **All dates** shows the complete saved history.
The production season filter affects harvest quantities and production charts.

Choose **Download CSV** beside a report heading to export that report with the
active filters. Activity and market-price exports have a **Section** column
because each contains two related tables. Select **Print view** to open the
filtered tables with the reporting period and generation date. Use **Print**
or the browser's print command to print or save as PDF.

Production is grouped by plot, variety, form and season. Charts keep Cherry,
Parchment and Green Beans weights separate. Market trends use manually recorded
observations, with separate lines by source, variety and form. The comparison
table uses the latest matching benchmark up to each sale date. This benchmark
can be older than the selected reporting period.

Payments are attributed to the sale date because separate payment dates are
unavailable. Legacy sales with unknown payments contribute to sales value only.
Activities with unknown costs are counted separately. The **limited operating
margin estimate** is sales value minus recorded activity costs and may omit
other expenses.

## Run tests

From the project folder with the virtual environment active, run:

```bash
python -m unittest discover -s tests -v
```

The tests use temporary SQLite databases, including tests that replay older
schemas and exercise the six modules together. To run just the system
integration checks:

```bash
python -m unittest discover -s tests -p test_integration.py -v
```

See the [testing report](docs/testing_report.md), [issue log](docs/issue_log.md)
and [manual acceptance checklist](docs/manual_acceptance_checklist.md) for the
SYE3104 practical evidence. The checklist uses a separate temporary database
for browser and stakeholder checks.

The app is configured for local examination use. For public deployment,
provide authentication and authorisation, HTTPS with Secure cookies, an
environment-supplied session secret and a production WSGI server.

## Data conventions

- Plot area uses hectares and harvest and sale weights use kilograms.
- Sales revenue and market prices use whole Ugandan shillings (UGX).
- Activity, harvest, sale and price dates use `YYYY-MM-DD`.
- Each activity, harvest and new sale belongs to a plot. Older sales retain their harvest link.
  Market prices are independent observations by market, coffee type and date.
