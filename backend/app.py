"""Coil & Crown booking API.

Run it:      flask --app app run --port 5001
Run tests:   python -m unittest -v

The rules here match the ones at the top of script.js (hours, chairs, step, lead time).
The server is the boss: it re-checks everything, so a visitor cannot book a closed day,
a time that is already full, or change the price.
"""
import html
import os
import random
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import Flask, Response, g, jsonify, request

# ---------- Settings (keep in step with script.js) ----------
STUDIO = {
	"chairs": 2,         # clients that can be served at the same time
	"lead_minutes": 60,  # a time can't be booked less than this long from now
	"days_ahead": 14,    # how many days ahead people can book (today counts as day 1)
	"step": 30,          # minutes between start times
	"cancel_hours": 24,  # a client can cancel online up to this many hours before the booking
}

# Opening hours by weekday (0 = Sunday), as (open hour, close hour). None = closed.
HOURS = {
	0: None,
	1: (9, 18), 2: (9, 18), 3: (9, 18), 4: (9, 18), 5: (9, 18),
	6: (8, 19),
}

# The server decides the length and price, never the browser.
SERVICES = {
	"wash":     {"name": "Wash and blow-dry",   "mins": 60,  "price": 8000},
	"silk":     {"name": "Silk press",          "mins": 120, "price": 20000},
	"twist":    {"name": "Twist-out and style", "mins": 120, "price": 15000},
	"cornrows": {"name": "Cornrows",            "mins": 90,  "price": 10000},
	"knotless": {"name": "Knotless braids",     "mins": 240, "price": 35000},
	"kids":     {"name": "Kids braids",         "mins": 90,  "price": 8000},
}

MAX_ACTIVE_PER_PHONE = 3  # upcoming bookings one phone number may hold (stops spam)
REF_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bookings.db"))
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")  # empty = admin page switched off
ALLOWED_ORIGINS = {
    o.strip() for o in os.environ.get(
			"ALLOWED_ORIGINS",
			"http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500",
    ).split(",") if o.strip()
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10_000  # bytes. A booking is tiny.
app.json.sort_keys = False

DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class BookingError(Exception):
	"""A booking that cannot be accepted. status is the HTTP code, code is a short machine word."""

	def __init__(self, status, code, message):
		super().__init__(message)
		self.status, self.code, self.message = status, code, message


# ---------- Time (Lagos is UTC+1 all year, no daylight saving) ----------
LAGOS = timezone(timedelta(hours=1))


def lagos_now():
	now = datetime.now(LAGOS)
	return now.date(), now.hour * 60 + now.minute


def js_weekday(d):
	"""JavaScript's getDay(): Sunday is 0. Python's weekday() has Monday as 0."""
	return (d.weekday() + 1) % 7


# ---------- Database ----------
SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	ref        TEXT NOT NULL UNIQUE,
	service    TEXT NOT NULL,
	date       TEXT NOT NULL,          -- YYYY-MM-DD, Lagos
	start      INTEGER NOT NULL,       -- minutes after midnight
	mins       INTEGER NOT NULL,
	price      INTEGER NOT NULL,
	name       TEXT NOT NULL,
	phone      TEXT NOT NULL,
	phone_key  TEXT NOT NULL,          -- digits only, used to count one person's bookings
	note       TEXT NOT NULL DEFAULT '',
	status     TEXT NOT NULL DEFAULT 'confirmed',   -- confirmed or cancelled
	created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings (date, status);
"""


def connect():
	# isolation_level=None means we control transactions ourselves (BEGIN IMMEDIATE below).
	db = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
	db.row_factory = sqlite3.Row
	return db


def init_db():
	db = connect()
	db.executescript(SCHEMA)
	db.close()


def get_db():
	if "db" not in g:
		g.db = connect()
	return g.db


@app.teardown_appcontext
def close_db(_err):
	db = g.pop("db", None)
	if db is not None:
		db.close()


# ---------- Availability rules (same as isFree in script.js) ----------
def is_free(busy, start, mins):
	for t in range(start, start + mins, STUDIO["step"]):
		used = sum(1 for b in busy if b["start"] <= t < b["start"] + b["mins"])
		if used >= STUDIO["chairs"]:
			return False
	return True


def busy_on(db, date_str):
	rows = db.execute(
		"SELECT start, mins FROM bookings WHERE date = ? AND status = 'confirmed'", (date_str,)
	).fetchall()
	return [{"start": r["start"], "mins": r["mins"]} for r in rows]


# ---------- Checking what the browser sent ----------
def parse_date(value):
	if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
		raise BookingError(400, "bad_date", "Date must look like 2026-10-12.")
	try:
		return date.fromisoformat(value)
	except ValueError:
		raise BookingError(400, "bad_date", "That date does not exist.")


def clean_booking(data):
	"""Return a clean dict, or raise BookingError. Does not touch the database."""
	if not isinstance(data, dict):
		raise BookingError(400, "bad_request", "Send the booking as JSON.")

	service = SERVICES.get(data.get("service"))
	if not service:
		raise BookingError(422, "bad_service", "Unknown service.")

	day = parse_date(data.get("date"))
	today, now_minutes = lagos_now()
	if not (today <= day < today + timedelta(days=STUDIO["days_ahead"])):
		raise BookingError(422, "out_of_range", "That day is not open for booking.")

	hours = HOURS[js_weekday(day)]
	if not hours:
		raise BookingError(422, "closed", "The studio is closed that day.")

	start = data.get("start")
	if isinstance(start, bool) or not isinstance(start, int):
		raise BookingError(422, "bad_time", "Start time must be a whole number of minutes.")
	opens, closes = hours[0] * 60, hours[1] * 60
	if start % STUDIO["step"] or start < opens or start + service["mins"] > closes:
		raise BookingError(422, "bad_time", "That time is outside opening hours.")
	if day == today and start < now_minutes + STUDIO["lead_minutes"]:
		raise BookingError(422, "past", "That time has passed.")

	name = str(data.get("name", "")).strip()
	if not name or len(name) > 80:
		raise BookingError(422, "bad_name", "Enter your name.")

	phone = str(data.get("phone", "")).strip()
	digits = re.sub(r"\D", "", phone)
	if not 10 <= len(digits) <= 15 or len(phone) > 30:
		raise BookingError(422, "bad_phone", "Enter a phone number we can reach.")

	note = str(data.get("note", "")).strip()
	if len(note) > 500:
		raise BookingError(422, "bad_note", "Keep the note under 500 characters.")

	return {
		"service": data["service"], "date": day.isoformat(), "start": start,
		"mins": service["mins"], "price": service["price"],   # from the server, not the browser
		"name": name, "phone": phone, "phone_key": digits, "note": note,
	}


def new_ref():
	return "CC-" + "".join(random.SystemRandom().choice(REF_CHARS) for _ in range(4))


def save_booking(db, b):
	"""Check the slot is still free and save, in one step so two people cannot take the last chair."""
	today, _ = lagos_now()
	db.execute("BEGIN IMMEDIATE")  # takes the write lock: other bookings wait their turn
	try:
		if not is_free(busy_on(db, b["date"]), b["start"], b["mins"]):
			raise BookingError(409, "taken", "That time was just taken.")
		active = db.execute(
			"SELECT COUNT(*) FROM bookings WHERE phone_key = ? AND status = 'confirmed' AND date >= ?",
			(b["phone_key"], today.isoformat()),
		).fetchone()[0]
		if active >= MAX_ACTIVE_PER_PHONE:
			raise BookingError(429, "too_many", "This phone number already has the maximum number of bookings.")
		for _ in range(20):
			ref = new_ref()
			if not db.execute("SELECT 1 FROM bookings WHERE ref = ?", (ref,)).fetchone():
				break
		else:
			raise BookingError(500, "ref", "Could not make a reference. Try again.")
		db.execute(
			"INSERT INTO bookings (ref, service, date, start, mins, price, name, phone, phone_key, note, created_at)"
			" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
			(ref, b["service"], b["date"], b["start"], b["mins"], b["price"], b["name"], b["phone"],
				b["phone_key"], b["note"], datetime.now(LAGOS).isoformat(timespec="seconds")),
		)
		db.execute("COMMIT")
	except BaseException:
		db.execute("ROLLBACK")
		raise
	return ref


# ---------- Public API ----------
@app.errorhandler(BookingError)
def booking_error(err):
	return jsonify(error=err.code, message=err.message), err.status


@app.errorhandler(413)
def too_big(_err):
	return jsonify(error="too_big", message="That request is too large."), 413


@app.get("/api/health")
def health():
	return jsonify(ok=True)


@app.get("/api/busy")
def busy():
	"""Which bookings exist on one day. Only times, never names or phone numbers."""
	day = parse_date(request.args.get("date"))
	return jsonify(busy_on(get_db(), day.isoformat()))


@app.post("/api/bookings")
def create_booking():
	if (request.content_length or 0) > app.config["MAX_CONTENT_LENGTH"]:
		raise BookingError(413, "too_big", "That request is too large.")
	clean = clean_booking(request.get_json(silent=True))
	ref = save_booking(get_db(), clean)
	return jsonify(
		ref=ref, service=clean["service"], date=clean["date"], start=clean["start"],
		mins=clean["mins"], price=clean["price"], name=clean["name"],
	), 201


def same_phone(a, b):
	"""Two numbers match if their last 10 digits match, so 0803... and +234803... are the same person."""
	return len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:]


@app.post("/api/bookings/cancel")
def cancel_booking():
	"""A client cancels their own booking with the reference and the phone number they booked with."""
	data = request.get_json(silent=True)
	if not isinstance(data, dict):
		raise BookingError(400, "bad_request", "Send the cancellation as JSON.")
	ref = str(data.get("ref", "")).strip().upper()
	digits = re.sub(r"\D", "", str(data.get("phone", "")))

	db = get_db()
	row = db.execute("SELECT * FROM bookings WHERE ref = ?", (ref,)).fetchone()
	# One answer for "no such booking" and "wrong phone", so nobody can guess other people's references.
	if not row or not same_phone(row["phone_key"], digits):
		raise BookingError(404, "not_found", "No booking matches that reference and phone number.")
	if row["status"] == "cancelled":
		raise BookingError(409, "already_cancelled", "That booking is already cancelled.")

	today, now_minutes = lagos_now()
	minutes_until = (date.fromisoformat(row["date"]) - today).days * 1440 + row["start"] - now_minutes
	if minutes_until < STUDIO["cancel_hours"] * 60:
		raise BookingError(422, "too_late", f"Bookings can be cancelled online up to {STUDIO['cancel_hours']} hours before.")

	db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ? AND status = 'confirmed'", (row["id"],))
	return jsonify(ok=True, ref=ref)


@app.after_request
def cors(resp):
	"""Lets the website (served from another address) call this API. Only listed sites are allowed."""
	origin = request.headers.get("Origin")
	if request.path.startswith("/api/") and origin in ALLOWED_ORIGINS:
		resp.headers["Access-Control-Allow-Origin"] = origin
		resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
		resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
		resp.headers["Vary"] = "Origin"
	return resp


# ---------- Admin page for the salon owner ----------
def admin_ok():
	auth = request.authorization
	return bool(
		ADMIN_PASSWORD and auth and auth.username == ADMIN_USER
		and secrets.compare_digest(auth.password or "", ADMIN_PASSWORD)
	)


def admin_gate():
	"""Returns a response if the visitor may not be here, otherwise None."""
	if not ADMIN_PASSWORD:
		return Response("Admin is switched off. Set the ADMIN_PASSWORD setting to turn it on.", 503)
	if not admin_ok():
		return Response("Sign in.", 401, {"WWW-Authenticate": 'Basic realm="Coil & Crown admin"'})
	return None


def pretty_day(date_str):
	d = date.fromisoformat(date_str)
	return f"{DAY_NAMES[js_weekday(d)]} {d.day} {MONTHS[d.month - 1]}"


def pretty_time(mins):
	h, m = divmod(mins, 60)
	return f"{(h + 11) % 12 + 1}{':%02d' % m if m else ''}{'pm' if h >= 12 else 'am'}"


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Bookings | Coil &amp; Crown</title>
<style>
body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#fbf1f4;color:#2a1226}
header{background:#4a1942;color:#fff;padding:14px 20px;font-weight:700}
main{padding:20px;max-width:1100px;margin:0 auto}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #ead7de;vertical-align:top}
th{background:#f4e3ea;font-size:.85rem}
.x{opacity:.5;text-decoration:line-through}
button{font:inherit;padding:6px 12px;border:1px solid #a8231b;background:#fff;color:#a8231b;border-radius:6px;cursor:pointer}
.table-wrap{overflow-x:auto}
</style></head><body><header>Coil &amp; Crown bookings</header><main>
<p>Upcoming bookings, soonest first. {{count}} confirmed.</p>
<div class="table-wrap"><table><thead><tr><th>Ref</th><th>When</th><th>Service</th><th>Client</th><th>Note</th><th></th></tr></thead>
<tbody>{{rows}}</tbody></table></div></main></body></html>"""


@app.get("/admin")
def admin():
	blocked = admin_gate()
	if blocked:
		return blocked
	today, _ = lagos_now()
	rows = get_db().execute(
		"SELECT * FROM bookings WHERE date >= ? ORDER BY date, start", (today.isoformat(),)
	).fetchall()
	out = []
	for r in rows:
		cancelled = r["status"] == "cancelled"
		action = "Cancelled" if cancelled else (
			f'<form method="post" action="/admin/cancel/{html.escape(r["ref"])}"'
			' onsubmit="return confirm(\'Cancel this booking?\')"><button>Cancel</button></form>')
		out.append(
			f'<tr class="{"x" if cancelled else ""}"><td>{html.escape(r["ref"])}</td>'
			f'<td>{pretty_day(r["date"])}<br>{pretty_time(r["start"])} to {pretty_time(r["start"] + r["mins"])}</td>'
			f'<td>{html.escape(SERVICES.get(r["service"], {"name": r["service"]})["name"])}<br>&#8358;{r["price"]:,}</td>'
			f'<td>{html.escape(r["name"])}<br><a href="tel:{html.escape(r["phone_key"])}">{html.escape(r["phone"])}</a></td>'
			f'<td>{html.escape(r["note"])}</td><td>{action}</td></tr>')
	count = sum(1 for r in rows if r["status"] == "confirmed")
	rows_html = "".join(out) or '<tr><td colspan="6">No upcoming bookings.</td></tr>'
	body = PAGE.replace("{{count}}", str(count)).replace("{{rows}}", rows_html)
	return Response(body, mimetype="text/html")


@app.post("/admin/cancel/<ref>")
def admin_cancel(ref):
	blocked = admin_gate()
	if blocked:
		return blocked
	# Browsers re-send saved sign-ins to any site, so refuse forms posted from other websites.
	origin = request.headers.get("Origin")
	if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
		return Response("Blocked.", 403)
	get_db().execute("UPDATE bookings SET status = 'cancelled' WHERE ref = ?", (ref,))
	return Response(status=303, headers={"Location": "/admin"})


init_db()

if __name__ == "__main__":
  app.run(port=int(os.environ.get("PORT", 5001)), debug=bool(os.environ.get("FLASK_DEBUG")))