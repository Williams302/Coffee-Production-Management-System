# SYE3104 issue log

Severity reflects operational impact. The deployment item applies if the app is exposed beyond the local practical environment.

| ID | Date found | Severity | Finding and evidence | Corrective action | Status |
|---|---|---|---|---|---|
| QA-01 | 21 November 2026 | High | An edit could move a harvest linked to an older sale to a different plot. The first integration run returned HTTP 200 instead of rejecting the change. Older sales derive their plot from that harvest. | Guarded the reassignment in the SQLite update and added a regression that checks both the original plot and the linked sale. Other harvest edits still pass. | Fixed and verified |
| QA-02 | 21 November 2026 | High if publicly exposed | The Flask routes have no authentication or authorisation. The default session cookie is suitable for local HTTP and lacks the Secure flag. Static review and unauthenticated Flask-client route checks confirmed the local behaviour. | Documented the boundary for the examination. A public deployment needs access controls, HTTPS with Secure cookies, a stable environment-supplied secret and a production WSGI server. | Open for public deployment |
