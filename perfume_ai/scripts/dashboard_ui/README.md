# Dashboard UI checks

These checks render the real Django templates and exercise their real browser scripts with intercepted, synthetic API responses. No application settings, database, credentials, email provider, or live business API are used by the preview server. Third-party requests are limited to font and static-library CDNs.

Requires the project's Python environment, Chrome, and Playwright. If Playwright is installed in a separate tools directory, set `PLAYWRIGHT_MODULE` to its absolute module path; otherwise it is resolved normally by Node. The accessibility check downloads axe-core 4.10.3 from cdnjs.

From the `perfume_ai` project directory, start the template-only server in one terminal:

```powershell
.\.venv\Scripts\python.exe scripts/dashboard_ui/preview.py
```

Run the checks in another terminal:

```powershell
node scripts/dashboard_ui/check_layout.cjs
node scripts/dashboard_ui/check_workflows.cjs
node scripts/dashboard_ui/check_handoff.cjs
node scripts/dashboard_ui/check_accessibility.cjs
node scripts/verify_chat_clients.js
```

The server binds only to `127.0.0.1:8766`. It serves templates and local assets; interactive API responses exist only inside the browser tests. Do not use it as an application server.

Screenshots and reports go to the system temporary directory under `webvitas-dashboard-review`, or to `DASHBOARD_UI_ARTIFACTS` if set. The layout check covers and captures all 13 dashboard sections at 375, 768, 1024 and 1440px, plus account pages at 375 and 1440px. The workflow check covers forms, dialogs, authentication, token refresh, errors, empty states, orders, conversations, imports, and mobile navigation.

The handoff check covers unread persistence, new-message ordering and sound, embedded and standalone views, mobile navigation, visibility, and stale responses. It drives polling explicitly; hidden-tab signals are simulated because headless Chrome reports every tab as visible.

For Django checks and regressions, use `perfume_ai.settings_test` with `TEST_DATABASE_URL=sqlite:///:memory:` to select isolated in-memory SQLite (an unset variable can be loaded from `.env`). Static-asset collection should use a temporary `STATIC_ROOT` when verifying locally.
