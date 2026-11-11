# URL ➜ HTML — website + API + admin panel (one service)

| URL | What |
|---|---|
| `/` | public website (download site / page ZIP / SEO / tech) |
| `/admin` | admin panel: overview, API keys, users, jobs |
| `/api/*` | key-protected API (header `X-API-Key`) — your Telegram bot uses this |
| `/health` | uptime + queue + database status |

## Run
Local / Termux: `pip install -r requirements.txt && python app.py`  (port 8080)
Render: push this folder → Web Service → start command
`gunicorn app:app --workers 1 --threads 32 --timeout 120 --bind 0.0.0.0:$PORT`
(keep `--workers 1`: key cache, usage counters and the job queue live in this process)

## Configure (`config.py`)
- `ADMIN_PASSWORD`, `SESSION_SECRET` — change both before deploying.
- `API_KEYS` — "system keys" (the bot's key). They appear in the admin panel; limits can be edited there.
- `TURSO_URL`/`TURSO_TOKEN` — same database as the bot (tables: users, kv, api_keys, usage).
- `WEB_LIMITS`, `WEB_DAILY_CAP` — website abuse limits (per IP / per day).

## Custom keys (admin panel → API keys)
Label · plan (free 20/day, paid unlimited — edit in `PLAN_DEFAULT_LIMITS`) · custom daily limit · expiry (days or date) ·
scopes (`html seo tech site zip`) · optional custom key value · note · enable/disable · rotate · reset today's usage · delete.
Only a SHA-256 hash is stored; the full key is shown once. Clients can call `GET /api/me` to see their own limits.
Responses carry `X-RateLimit-Limit` / `X-RateLimit-Remaining`; over the limit → HTTP 429, wrong scope / disabled / expired → 403.

## Captcha (Cloudflare Turnstile)
The website needs a solved Turnstile check before it hands out a 30-minute signed token; every `/web/*` call needs that token.
- `config.py` has your Cloudflare account id + API token. With `TURNSTILE_SITEKEY`/`TURNSTILE_SECRET` empty, the server
  creates a widget on first start (`POST /accounts/{id}/challenges/widgets`, the token needs Turnstile edit permission),
  saves the keys in Turso (`kv` → `turnstile`) and reuses them on every restart.
- `TURNSTILE_DOMAINS` must contain the hostname the site runs on (add a custom domain there; a changed list creates a new widget).
- Check after deploy: `/health` → `"captcha": true`, and `/admin` → Overview shows "Captcha on". If it shows an error the
  server retries every 10 minutes; `CAPTCHA_REQUIRED = True` makes the site refuse jobs until it works (default: keep running, rate limits only).
- Or paste keys from the Cloudflare dashboard into `TURNSTILE_SITEKEY` / `TURNSTILE_SECRET`.
- R2 keys in `config.py` are not used by the app yet.

## Website protection
`python protect.py` rebuilds `static/js/site.js` from `frontend/` (random XOR key per build + anti-DevTools guard).
`python protect.py --no-guard` while you debug. This is a deterrent: the real protection is server-side
(signed 10-minute tokens bound to the visitor IP, per-IP limits, daily cap, key-gated API).
Edit `frontend/site.src.js`, then rebuild. Never edit `static/js/site.js` by hand.

## Bot
The bot keeps calling `/api/*` with its key (config `API_KEYS[0]` here == `API_KEY` in the bot's config.py).
