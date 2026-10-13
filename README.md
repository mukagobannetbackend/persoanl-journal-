# TradeLog Pro

A deployable Flask trading journal and analytics dashboard. Log every trade in roughly 60 seconds, then use the dashboard to identify which setups, market conditions, exits, and rule-following behaviors drive your results.

## Features
- Fast trade-entry form covering setup criteria, market condition, planned vs realised R:R, rules followed, exit reason, timing, and P&L.
- SQLite persistence with a private journal per authenticated trader.
- Registration, login, logout, password hashing, and per-user data isolation.
- CSV export of the signed-in trader's journal.
- PDF export with a formatted journal table and rule-break summary.
- Automated mistake finder based on `rules_followed = No`, showing loss from rule breaks, repeated manual/breakeven exits, and common setups/conditions/exit reasons.
- KPI cards for total P&L, win rate, average realised R:R, and trade count.
- Equity curve and breakdown by market condition.
- Interactive Chart.js equity curve with hover tooltips and account-growth values by trade.
- Configurable initial account balance with current equity shown as initial balance plus realised P&L.
- Optional screenshots per trade for 4H context, 15M setup, and 5M entry, with thumbnail links in the journal.
- Full calendar: profitable trades appear green, losing trades red, flat trades amber.
- Rule-following comparison to expose the cost of breaking the plan.
- Responsive dark dashboard UI with client-side filtering.

## Run locally
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SECRET_KEY="replace-with-a-long-random-secret"
python app.py
```
Open http://localhost:5000 and create an account.

## Authentication design
- Users are stored in the `users` table with a Werkzeug password hash; plaintext passwords are never saved.
- Every trade includes a `user_id` foreign key.
- All journal, analytics, delete, CSV, and PDF queries are scoped to the signed-in user's ID.
- A migration adds `user_id` to an existing `trades` table. Orphaned legacy rows are assigned once to the first account created, so an existing single-user journal is not lost. Review this policy before using the app with sensitive multi-user data.
- For a larger production system, move SQLite to PostgreSQL, add CSRF protection (for example Flask-WTF), use secure cookies behind HTTPS, add email verification/password reset, and consider rate limiting login attempts.

## Screenshot storage
Screenshots are saved under `static/uploads/` with user/trade-specific filenames and are limited to PNG, JPG, JPEG, and WEBP. The current app limits each request to 15 MB. For production, use object storage such as S3 or Cloudflare R2 and store private signed URLs rather than serving uploads directly from the Flask process.

## Deploy
Works with any Flask host (Render, Railway, Fly.io, Heroku-style hosts, or a VPS). Use the included `Procfile` with Gunicorn. Set `FLASK_DEBUG=0`, configure a strong `SECRET_KEY`, enable HTTPS, and use a persistent volume for `trades.db` in production. For horizontally scaled deployments, use a managed PostgreSQL database instead of local SQLite.
