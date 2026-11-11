"""URL ➜ HTML — website + API + admin panel. Hardcoded on purpose (private deployment)."""
import os

PORT = int(os.environ.get("PORT", 8080))

# ── Admin panel (/admin) ──
ADMIN_PASSWORD = "asraful123"          # change it
SESSION_SECRET = "RyjC1yIy-5Vz0w9XEtWsoy0OyVF1Nt_Axlqq-har9GVl_DX1IRASy-Ye313YUSuJ"           # signs admin cookies + website tokens
ADMIN_SESSION_HOURS = 12
ADMIN_LOGIN_ATTEMPTS = 5             # per IP, per 15 minutes

# ── System keys: always valid, shown in the admin panel as "system" keys ──
# (your Telegram bot's key lives here; the admin panel can edit its limits or disable it)
API_KEYS = ["d9RGoLP4ZOi7_waN7OsxT5xeYDztwmtR9lc8bO83pwI"]

# ── Turso (same database as the bot: users + stats + api keys + usage) ──
TURSO_URL = "libsql://shuvoxmods-shuvoxmods.aws-ap-northeast-1.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3OTQyNDg5MzYsImlkIjoiMDFhMWMxOTEtZTAwMS03NmY1LWIxNTEtODRiYzRkMjcxNTg1Iiwia2lkIjoibGRJQmtuN2N2NTdLU2pDSS1pTmxYeWRXcXlJWU1DWVl0c2Rhcm9nUW1tZyIsInJpZCI6ImUzNDk3MDk2LWY4OGMtNDg2Ny1hZjIzLTdlZTQ5ZTNmNDcwZiJ9.p30d0EiP-ZmkoYpSE08GLd9jP6Xv_KVgcaXFfJOOIzAU881sol6Au5p-gUh4mLW8OUgDe-rssk93uzdSfMtWBw"

# ── Custom key defaults ──
PLAN_DEFAULT_LIMITS = {"free": 20, "paid": None}   # None = unlimited
KEY_PREFIX = "uth_"

# ── Public website limits (visitors have no key) ──
WEB_LIMITS = {"light": (30, 3600), "job": (5, 3600), "poll": (600, 600), "token": (60, 3600)}  # (requests, seconds) per IP
WEB_DAILY_CAP = 500                  # total website requests per day, all visitors
WEB_TOKEN_TTL = 1800                 # seconds a solved captcha stays valid

# ── job engine ──
MAX_RUNNING_JOBS = 3
MAX_QUEUED_JOBS = 100
JOB_TTL_SECONDS = 30 * 60

# ── crawler limits ──
MAX_SITE_PAGES = 150
MAX_SITE_DEPTH = 5
MAX_SITE_ASSETS = 1200
MAX_ASSETS_SINGLE = 500
MAX_PAGE_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_ASSET_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_DOWNLOAD_BYTES = 120 * 1024 * 1024
PAGE_CONCURRENCY = 6
ASSET_CONCURRENCY = 10
MAX_SAFE_REDIRECTS = 5

# ── Cloudflare (hardcoded) ──
CF_ACCOUNT_ID = "eed2ab97e5d19bd69dcf7a234e1fafd3"
CF_API_TOKEN = "cfat_R93CQWeq1Fy7gLE7HeIkCVJOwcCwNueMBi6k93gPf13d4468"
CF_API_BASE = "https://api.cloudflare.com/client/v4"
# R2 object storage (not used by the app yet — kept here for storing ZIP results later)
CF_R2_ACCESS_KEY_ID = "2787ba2f71aa4434adefddb82f321c79"
CF_R2_SECRET_ACCESS_KEY = "fdebeb3194cf0b112c7978ec938dca373e839f2d2b241117ed73712eee352884"
CF_R2_ENDPOINT = "https://eed2ab97e5d19bd69dcf7a234e1fafd3.r2.cloudflarestorage.com"

# ── Captcha: Cloudflare Turnstile on the public website ──
CAPTCHA_ENABLED = True
# Leave both empty: on first start the server creates a Turnstile widget with CF_API_TOKEN
# (needs a token with Turnstile edit permission) and saves its keys in Turso (table kv, key "turnstile").
TURNSTILE_SITEKEY = ""
TURNSTILE_SECRET = ""
TURNSTILE_DOMAINS = ["url-to-html-api.onrender.com"]   # hostnames the widget may run on (add a custom domain here)
TURNSTILE_MODE = "managed"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# False = if the widget cannot be created, the site keeps working (rate limits only) and /admin shows a warning.
# True  = the site refuses to start jobs until the captcha works.
CAPTCHA_REQUIRED = False
