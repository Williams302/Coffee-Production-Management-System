# manual acceptance checklist

**Status** Pending. No manual usability session, stakeholder feedback or stakeholder acceptance has been recorded.

## Prepare an isolated local session

From the project root after setup, start Flask against a new temporary database.

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from app import create_app

with TemporaryDirectory() as directory:
    app = create_app({"DATABASE": Path(directory) / "acceptance.sqlite3"})
    app.run(host="127.0.0.1", port=5001, debug=False)
PY
```

Open `http://127.0.0.1:5001/`. Use temporary practice records only in this session. Keep it running until the checks are complete. Record the actual device, browser, date, result and evidence for each applicable item.

| Check | Expected result | Result or evidence |
|---|---|---|
| Desktop, tablet and phone widths | Navigation wraps cleanly. Forms and charts fit. Wide tables scroll within their section without forcing the page to scroll sideways. | Pending |
| 200% browser zoom and mobile orientation | Headings, filters, totals and action buttons remain readable and reachable. | Pending |
| Keyboard only | Tab order follows page order. Focus remains visible. Filters, row actions, confirmations, chart data alternatives and tables can be reached and operated. | Pending |
| Screen reader | Page titles, headings, labels, validation errors and status messages are announced. Charts have useful names and the related values are accessible as text. | Pending |
| Visual contrast | Text, focus indicators, links, field errors and chart legends remain distinguishable in the actual browser. | Pending |
| Plot and activity workflow | Register a plot, add an activity, confirm the dashboard update, rename the plot and verify that the same activity remains linked. | Pending |
| Harvest, sale and price workflow | Record different coffee forms and a partial sale. Add a comparable and a mismatched price. Check the separate production figures, receipt, balance and benchmark difference across pages and reports. | Pending |
| Confirmation and cancellation | Cancel a delete, then confirm on a disposable record. Attempt to delete the plot while a related activity, harvest or sale still exists and confirm it remains. | Pending |
| Validation and recovery | Try a duplicate plot ID, an invalid date and an excessive partial payment. Check that feedback is clear and valid field values remain available for correction. | Pending |
| Chart rendering and unavailable CDN | With network access, check axes, legends, form separation, and market-series labels. With the CDN blocked, confirm the corresponding table and totals remain readable. | Pending |
| Report filters and CSV in a spreadsheet | Verify inclusive dates, production-season scope and one download per report. Check that data containing commas or line breaks stays in its original cell and formula-like text is inert in the chosen spreadsheet. | Pending |
| Print and PDF preview | Print view shows title, period and generated date. Wide tables fit, headers repeat where supported and rows remain legible across pages. | Pending |

## Acceptance record

| Field | Entry |
|---|---|
| Tester and role | |
| Date, browser and device | |
| Passed, failed or deferred checks | |
| Evidence and follow-up issue IDs | |
| Stakeholder decision | Pending |
