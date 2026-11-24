# SYE3104 system integration testing report

**Project** Coffee Production Management System  
**Test date** 21 November 2026 (Africa/Kampala)  
**Environment** Python 3.12, Flask test client, SQLite and Node.js syntax checks

Tests use temporary SQLite databases. The live database was opened in read-only mode for schema and integrity inspection. Route smoke tests ran against a temporary copy. The live database file was unchanged during verification.

## Commands and observed results

| Stage | Command | Passed | Failed |
|---|---|---:|---:|
| Baseline | `.venv/bin/python -m unittest discover -s tests -v` | 43 | 0 |
| Integration tests before the fix | `.venv/bin/python -m unittest discover -s tests -p test_integration.py -v` | 9 | 1 |
| Integration tests after the fix | `.venv/bin/python -m unittest discover -s tests -p test_integration.py -v` | 10 | 0 |
| Final regression suite | `.venv/bin/python -m unittest discover -s tests -v` | 53 | 0 |

The initial integration failure was the linked-harvest reassignment test. The form accepted changing the plot of a harvest linked to an older sale. The fix keeps the original plot when a sale references that harvest and still permits other harvest edits.

## Final automated results by test

Test names below omit the common `test_` prefix. Each row records the actual outcome from the final run.

### Coffee plots — `tests/test_plots.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| create_and_view_plot | Register and read a plot | Saved plot appeared and dashboard count increased | Pass |
| search_saved_plots | Check search matching | Matching records returned and literal wildcard behaviour held | Pass |
| edit_plot | Update plot details | Updated values saved under the same internal ID | Pass |
| reject_invalid_fields | Validate form fields | Invalid input was rejected and no plot was saved | Pass |
| reject_duplicate_ids_on_create_and_edit | Prevent duplicate plot IDs | Add and edit rejected duplicate IDs, while saving the same record was allowed | Pass |
| delete_unlinked_plot_after_confirmation | Confirm deletion and dashboard count | Unlinked plot removed and count returned to zero | Pass |
| delete_protects_activity_harvest_and_sales_records | Protect related data | Related activities, harvests and sales remained | Pass |
| reject_post_without_csrf_and_stale_delete | Enforce valid confirmation | Missing token and stale plot name both blocked deletion | Pass |
| upgrade_preserves_legacy_plots_and_dashboard | Check upgrade compatibility | Legacy plot and dashboard activity remained visible | Pass |

### Farm activities — `tests/test_activities.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| create_list_and_dashboard | Save activity against a registered plot | Record, cost and recent activity appeared | Pass |
| edit_activity_and_allow_zero_values | Update activity and distinguish zero | Edit saved zero quantity and cost and blank notes | Pass |
| delete_requires_confirmation_and_csrf | Check activity deletion | Missing token rejected and confirmed deletion removed the record | Pass |
| filters_individually_and_combined | Check plot, type and date filtering | Each filter and combined filters returned the expected subset | Pass |
| reject_invalid_form_values | Validate input including small negative quantities | Malformed values were rejected and the saved row survived an invalid edit | Pass |
| legacy_migration_preserves_plot_and_activity | Preserve old records on upgrade | Legacy description and plot linkage survived migration and edit | Pass |

### Production and harvests — `tests/test_harvests.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| create_list_and_display_selected_plot_variety | Save a harvest and derive variety | Saved plot supplied displayed variety | Pass |
| edit_updates_plot_form_weight_season_and_notes | Update an unlinked harvest | Plot, form, weight, season and notes updated | Pass |
| delete_requires_confirmation_and_protects_linked_sales | Protect harvest-to-sale links | Only the unlinked harvest was deleted | Pass |
| filters_individually_and_combined | Filter by plot, form, dates and season | Filtered rows matched and invalid filters returned HTTP 400 | Pass |
| reject_invalid_harvest_inputs | Validate weight, date and required details | Invalid values rejected and existing weight retained | Pass |
| summary_and_chart_keep_processing_stages_separate | Prevent cross-form aggregation | Chart and summary reported each form separately | Pass |
| migrate_existing_database_and_show_unclassified_weights | Preserve unclassified harvests and sale links | Existing weight and sale reference survived | Pass |

### Sales — `tests/test_sales.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| create_view_exact_calculation_and_dashboard | Check server rounding and saved financial values | Sale displayed 235 UGX total, 80 received and 155 balance. Harvest weight stayed unchanged | Pass |
| edit_updates_sale_and_recalculates_paid_amount | Check sale and payment edits | Edited sale recalculated to 369 UGX, fully paid | Pass |
| delete_confirmation_csrf_and_foreign_keys | Protect related records and require confirmation | Invalid FK operations and missing token were rejected | Pass |
| search_and_combined_filters | Verify buyer, plot, form, status and date filters | Matching records returned and invalid filters rejected | Pass |
| server_validation_rejects_invalid_fields_and_payment_amounts | Reject invalid price, payment, quantity and dates | Invalid records were rejected | Pass |
| zero_value_pending_and_large_amounts_remain_exact | Check zero handling and integer limits | Correct statuses and exact large totals were retained | Pass |
| migration_preserves_legacy_sale_and_unknown_payment | Preserve historical data | Original amount and harvest link survived, and unknown payment remained explicit | Pass |

### Market prices — `tests/test_market_prices.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| create_list_and_dashboard | Save and display a market observation | Saved record and latest dated dashboard card appeared | Pass |
| edit_and_delete_with_confirmation | Verify corrections, zero price and deletion | Zero saved, missing token rejected and confirmed deletion succeeded | Pass |
| filters_keep_sources_varieties_and_forms_in_separate_series | Verify filters and trend grouping | Correct rows and separate chart series were returned | Pass |
| invalid_inputs_and_duplicate_prices | Validate monetary and identification fields | Invalid values and duplicate combinations rejected | Pass |
| latest_sale_comparison_and_signed_differences | Verify benchmark date, source and sign | Selected source gave +100 UGX/kg and all-source selection gave −200 UGX/kg | Pass |
| missing_comparable_prices_use_explicit_empty_state | Handle absent and mismatched prices | Missing comparisons displayed explicitly | Pass |
| migration_preserves_unclassified_observations | Preserve older observations | Legacy row remained visible and stayed out of the chart | Pass |

### Reports — `tests/test_reports.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| summary_payments_and_operating_margin | Check totals, balances and estimate label | Sales, receipts, costs, outstanding and limited margin matched fixture values | Pass |
| production_grouping_charts_and_season_filter | Check grouped harvests and season scope | Separate forms retained and season filter left sales unchanged | Pass |
| daily_weekly_and_custom_date_filters | Check date boundaries | Daily, Monday–Sunday and inclusive custom results matched | Pass |
| invalid_filters_and_unsupported_export | Check invalid input and unknown routes | Invalid filters returned HTTP 400, unknown export returned 404 | Pass |
| csv_headers_and_formula_safeguards | Verify export headers and spreadsheet safeguards | Attachment header and text safeguards passed, numeric negative value stayed numeric | Pass |
| market_matching_respects_form_variety_and_sale_date | Check report match rules | Correct earlier comparable price returned and unmatched rows omitted | Pass |
| empty_reports_and_print_view | Check no-data and printable output | Empty tables and zero weights rendered, and print view preserved period and headings | Pass |

### System integration — `tests/test_integration.py`

| Test | Purpose | Actual result | Status |
|---|---|---|---|
| full_workflow_and_reports_share_saved_calculations | Save all operational modules through forms and reconcile outputs | Separate weights of 12.5, 5.5 and 2.25 kg were retained. Sales totalled 235 UGX, with 80 received, 155 outstanding, 400 in costs, −165 limited margin and +10 UGX/kg price difference | Pass |
| edits_and_deletes_are_reflected_across_modules | Verify end-to-end CRUD and relationship safety | Plot rename retained related IDs, updated entries reached reports, a direct sale still blocked plot deletion, and cleanup cleared reports | Pass |
| missing_records_and_read_only_methods | Check missing and out-of-range record IDs and unsupported methods | Missing records and export returned 404. POST to read-only lists returned 405 | Pass |
| validation_and_csrf_cover_all_mutating_modules | Validate forms and duplicate ID behaviour with CSRF | Each unwanted write returned 400 and record counts stayed unchanged | Pass |
| user_text_remains_literal_in_queries_html_and_csv | Verify parameter binding and presentation | Apostrophes and wildcard characters stayed literal, HTML-sensitive text was escaped, and CSV preserved plain data | Pass |
| foreign_key_integrity_on_connections_and_failed_updates | Verify SQLite constraints and rollback | Orphan writes were rejected, integrity was OK and FK check returned no violations | Pass |
| empty_pages_date_boundaries_and_exports | Check defaults and filtered exports across modules | Empty messages rendered, inclusive export boundaries matched, and print retained the range | Pass |
| responsive_and_accessible_markup_basics | Inspect rendered markup | Viewport, wrapping navigation, headings, landmarks, label associations, chart names and table wrappers were present | Pass |
| local_configuration_and_session_cookie_defaults | Verify local defaults without reading or printing secrets | Debug was off, fallback keys differed between app instances, and the cookie had HttpOnly and SameSite=Lax | Pass |
| linked_harvest_cannot_reassign_legacy_sale_to_another_plot | Keep the original relationship on edit | Reassignment returned 400, original plot and related sale stayed intact, and a notes edit succeeded | Pass after fix |

## Additional verification

| Check | Actual result | Status |
|---|---|---|
| `for file in static/js/*.js; do node --check "$file" \|\| exit 1; done` | All six JavaScript assets passed syntax checking | Pass |
| `.venv/bin/python -m pip check` | No broken requirements found. Pip reported a local cache permissions warning | Pass |
| SQLite integrity and 13 read-only route checks | Integrity check was OK, no FK violations were reported, and all route requests on a temporary copy returned HTTP 200. The copy and live database file stayed byte-for-byte unchanged during the checks | Pass |
| Static configuration and SQL review | The checked application files use an environment-provided or generated session key, bound request values in SQL, and CSRF checks on write forms. Local HTTP defaults remain in place | Reviewed |

## Limitations and outstanding checks

- The runtime checks used Flask's test client. Actual reflow, touch targets, keyboard focus, screen-reader announcements, chart rendering and print pagination still need browser verification.
- The production-readiness items in the [issue log](issue_log.md) remain open. The tests verify local settings and do not certify a public deployment.
- Plot variety is read from the current plot in harvest and sale reports. An intentional variety correction changes displayed classifications for linked records. The app does not store historical snapshots of plot variety.
- Tester usability feedback and stakeholder acceptance have not been collected. Use the [manual acceptance checklist](manual_acceptance_checklist.md) to record those results.
