"""Dee's Hair Salon: a small Flask + SQLite scheduling application.

The catalog and opening hours are intentionally easy to edit for a class demo.
All prices, employees, and services are fictional. No payments or email are sent.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

import click
from flask import (
    Flask, current_app, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


SERVICES = [
    {"id": "cut-style", "name": "Cut & Style", "description": "A tailored haircut, shampoo, and finished style.", "category": "Cuts & styling", "duration": 60, "price": 45},
    {"id": "clipper-cut", "name": "Clipper Cut", "description": "A fresh, precise short cut with a clean finish.", "category": "Cuts & styling", "duration": 30, "price": 28},
    {"id": "blowout", "name": "Signature Blowout", "description": "A wash and polished blow-dry, just the way you like it.", "category": "Cuts & styling", "duration": 45, "price": 35},
    {"id": "single-color", "name": "Single-Process Color", "description": "One beautiful color, from root touch-up to all-over refresh.", "category": "Color", "duration": 90, "price": 85},
    {"id": "partial-highlights", "name": "Partial Highlights", "description": "Soft dimension and brightness around your face and crown.", "category": "Color", "duration": 120, "price": 110},
    {"id": "full-highlights", "name": "Full Highlights", "description": "All-over dimension with thoughtfully placed highlights.", "category": "Color", "duration": 150, "price": 150},
    {"id": "deep-conditioning", "name": "Deep Conditioning", "description": "A restorative treatment for softer, more manageable hair.", "category": "Treatments", "duration": 30, "price": 25},
    {"id": "occasion-styling", "name": "Special Occasion Styling", "description": "A beautiful finished look for a day worth celebrating.", "category": "Cuts & styling", "duration": 60, "price": 70},
]

STYLISTS = [
    {"id": "dee", "name": "Dee", "role": "Owner & stylist"},
    {"id": "morgan", "name": "Morgan", "role": "Color specialist"},
    {"id": "jamie", "name": "Jamie", "role": "Stylist"},
]

# Python weekday numbers: Monday = 0, Sunday = 6. None means closed.
OPENING_HOURS = {
    0: None,
    1: ("09:00", "17:00"),
    2: ("09:00", "17:00"),
    3: ("09:00", "19:00"),
    4: ("09:00", "18:00"),
    5: ("09:00", "15:00"),
    6: None,
}
MAX_ADVANCE_DAYS = 90
SLOT_STEP_MINUTES = 30
SALON_TIMEZONE = "America/New_York"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference TEXT NOT NULL UNIQUE,
    service_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    duration INTEGER NOT NULL CHECK (duration > 0),
    price INTEGER NOT NULL CHECK (price >= 0),
    stylist_id TEXT NOT NULL,
    appointment_date TEXT NOT NULL,
    start_minute INTEGER NOT NULL,
    end_minute INTEGER NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (end_minute > start_minute)
);
CREATE INDEX IF NOT EXISTS bookings_schedule
    ON bookings (appointment_date, stylist_id, status, start_minute, end_minute);
"""


class BookingError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def get_db() -> sqlite3.Connection:
    """One SQLite connection per request, with explicit transactions for writes."""
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"], timeout=15, isolation_level=None,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 15000")
    return g.db


def get_setting(key: str) -> str | None:
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def salon_now() -> datetime:
    clock = current_app.config.get("NOW")
    value = clock() if callable(clock) else datetime.now(ZoneInfo(SALON_TIMEZONE))
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(SALON_TIMEZONE))
    return value.astimezone(ZoneInfo(SALON_TIMEZONE))


def csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def public_config() -> dict:
    today = salon_now().date()
    return {
        "services": SERVICES,
        "stylists": STYLISTS,
        "hours": {str(day): hours for day, hours in OPENING_HOURS.items()},
        "timezone": SALON_TIMEZONE,
        "today": today.isoformat(),
        "min_date": today.isoformat(),
        "max_date": (today + timedelta(days=MAX_ADVANCE_DAYS)).isoformat(),
        "slot_step_minutes": SLOT_STEP_MINUTES,
        "csrf_token": csrf_token(),
    }


def parse_payload() -> dict:
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise BookingError("Please send a valid JSON object.")
    return value


def text_field(data: dict, key: str, limit: int, required: bool = True) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise BookingError(f"Please enter a valid {key.replace('_', ' ')}.")
    value = value.strip()
    if required and not value:
        raise BookingError(f"Please enter your {key.replace('_', ' ')}.")
    if len(value) > limit:
        raise BookingError(f"The {key.replace('_', ' ')} is too long (maximum {limit} characters).")
    if key != "notes" and ("\n" in value or "\r" in value):
        raise BookingError(f"Please enter a valid {key.replace('_', ' ')}.")
    return value


def valid_email(value: str) -> str:
    value = value.casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise BookingError("Please enter a valid email address.")
    return value


def service_for(service_id: str) -> dict:
    for service in SERVICES:
        if service["id"] == service_id:
            return service
    raise BookingError("Please choose a service from the list.")


def stylist_choices(stylist_id: str) -> list[str]:
    if stylist_id == "any":
        return [person["id"] for person in STYLISTS]
    if any(person["id"] == stylist_id for person in STYLISTS):
        return [stylist_id]
    raise BookingError("Please choose a stylist from the list.")


def parse_date(value: str, *, booking_window: bool = True) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise BookingError("Please choose a valid appointment date.")
    try:
        selected = date.fromisoformat(value)
    except ValueError:
        raise BookingError("Please choose a valid appointment date.") from None
    if booking_window:
        today = salon_now().date()
        if selected < today:
            raise BookingError("Please choose today or a future date.")
        if selected > today + timedelta(days=MAX_ADVANCE_DAYS):
            raise BookingError(f"Appointments can be booked up to {MAX_ADVANCE_DAYS} days ahead.")
    return selected


def minutes(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise BookingError("Please choose a valid appointment time.")
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        raise BookingError("Please choose a valid appointment time.")
    return hour * 60 + minute


def time_string(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def time_label(value: int) -> str:
    hour, minute = divmod(value, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def appointment_datetime(day: date, start: int) -> datetime:
    return datetime(day.year, day.month, day.day, start // 60, start % 60, tzinfo=ZoneInfo(SALON_TIMEZONE))


def validate_slot(day: date, start: int, duration: int) -> None:
    hours = OPENING_HOURS[day.weekday()]
    if hours is None:
        raise BookingError("The salon is closed on Sundays and Mondays. Please choose another day.")
    opening, closing = map(minutes, hours)
    if start < opening or start + duration > closing:
        raise BookingError("Please choose a time that allows the full service before closing.")
    if (start - opening) % SLOT_STEP_MINUTES:
        raise BookingError("Please choose one of the available appointment times.")
    if appointment_datetime(day, start) <= salon_now():
        raise BookingError("That appointment time has already passed. Please choose a later time.")


def available_stylists(day: date, start: int, duration: int, choices: list[str], exclude_id: int | None = None) -> list[str]:
    rows = get_db().execute(
        """SELECT stylist_id FROM bookings
           WHERE appointment_date = ? AND status = 'confirmed'
             AND start_minute < ? AND end_minute > ?
             AND (? IS NULL OR id != ?)""",
        (day.isoformat(), start + duration, start, exclude_id, exclude_id),
    ).fetchall()
    busy = {row["stylist_id"] for row in rows}
    return [stylist_id for stylist_id in choices if stylist_id not in busy]


def find_slots(day: date, duration: int, choices: list[str], exclude_id: int | None = None) -> list[dict]:
    hours = OPENING_HOURS[day.weekday()]
    if hours is None:
        return []
    opening, closing = map(minutes, hours)
    now = salon_now()
    slots = []
    for start in range(opening, closing - duration + 1, SLOT_STEP_MINUTES):
        if appointment_datetime(day, start) <= now:
            continue
        available = available_stylists(day, start, duration, choices, exclude_id)
        if available:
            slots.append({"time": time_string(start), "label": time_label(start), "stylist_ids": available})
    return slots


def booking_json(row: sqlite3.Row) -> dict:
    stylist = next((p["name"] for p in STYLISTS if p["id"] == row["stylist_id"]), row["stylist_id"])
    return {
        "reference": row["reference"],
        "service_id": row["service_id"],
        "service_name": row["service_name"],
        "duration": row["duration"],
        "price": row["price"],
        "stylist_id": row["stylist_id"],
        "stylist_name": stylist,
        "date": row["appointment_date"],
        "time": time_string(row["start_minute"]),
        "end_time": time_string(row["end_minute"]),
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "notes": row["notes"],
        "status": row["status"],
    }


def limit_attempts(bucket: str, limit: int, window: int, *, failure: bool = False, clear: bool = False) -> None:
    """Small single-process demo throttle, not a production rate-limit service."""
    store, lock = current_app.extensions["attempt_limits"]
    key = (bucket, request.remote_addr or "unknown")
    now = time.monotonic()
    with lock:
        if clear:
            store.pop(key, None)
            return
        attempts = store[key]
        while attempts and attempts[0] < now - window:
            attempts.popleft()
        if len(attempts) >= limit:
            raise BookingError("Too many unsuccessful attempts. Please wait a few minutes and try again.", 429)
        if failure:
            attempts.append(now)


def lookup_booking(data: dict) -> sqlite3.Row:
    limit_attempts("booking-lookup", 20, 300)
    reference = text_field(data, "reference", 64).upper()
    email = valid_email(text_field(data, "email", 254))
    row = get_db().execute(
        "SELECT * FROM bookings WHERE reference = ? AND email = ?", (reference, email),
    ).fetchone()
    if row is None:
        limit_attempts("booking-lookup", 20, 300, failure=True)
        raise BookingError("We could not find an appointment with that reference and email address.", 404)
    return row


def ensure_changeable(row: sqlite3.Row) -> None:
    if row["status"] != "confirmed":
        raise BookingError("This appointment has already been cancelled.", 409)
    day = date.fromisoformat(row["appointment_date"])
    if appointment_datetime(day, row["start_minute"]) <= salon_now():
        raise BookingError("Past appointments can no longer be changed online.", 409)


def staff_logged_in() -> bool:
    version = get_setting("admin_version")
    return bool(version and session.get("staff_version") == version)


def staff_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        if not staff_logged_in():
            if request.path.startswith("/api/"):
                raise BookingError("Please sign in to view the staff schedule.", 401)
            return redirect(url_for("staff_login"))
        return function(*args, **kwargs)
    return decorated


def create_app(test_config: dict | None = None) -> Flask:
    default_instance = Path(__file__).resolve().parent / "instance"
    instance_path = os.environ.get("SALON_INSTANCE_PATH", str(default_instance))
    if test_config and test_config.get("INSTANCE_PATH"):
        instance_path = str(test_config["INSTANCE_PATH"])
    application = Flask(__name__, instance_path=str(Path(instance_path).resolve()), instance_relative_config=True)
    application.config.from_mapping(
        DATABASE=os.environ.get("DATABASE_PATH", str(Path(application.instance_path) / "salon.sqlite3")),
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        MAX_CONTENT_LENGTH=32 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if test_config:
        application.config.update(test_config)
    Path(application.instance_path).mkdir(parents=True, exist_ok=True)
    Path(application.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    if not application.config["SECRET_KEY"]:
        secret_file = Path(application.instance_path) / "secret.key"
        try:
            descriptor = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        application.config["SECRET_KEY"] = secret_file.read_text(encoding="utf-8").strip()
        if not application.config["SECRET_KEY"]:
            raise RuntimeError("The saved session secret is empty. Restore or remove instance/secret.key and restart.")
    application.extensions["attempt_limits"] = (defaultdict(deque), threading.Lock())

    @application.teardown_appcontext
    def close_db(_exception=None):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    with application.app_context():
        get_db().executescript(SCHEMA)

    @application.before_request
    def csrf_protection():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8")):
            raise BookingError("Your session expired. Refresh the page and try again.", 400)
        return None

    @application.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if not request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.errorhandler(BookingError)
    def handle_booking_error(error):
        if request.path.startswith("/api/"):
            return jsonify(error=error.message), error.status
        return render_template("login.html", error=error.message), error.status

    @application.errorhandler(413)
    def too_large(_error):
        return jsonify(error="That request is too large. Please shorten your notes and try again."), 413

    @application.errorhandler(sqlite3.OperationalError)
    def database_busy(error):
        current_app.logger.exception("SQLite operation failed")
        return jsonify(error="The schedule is busy right now. Please try again in a moment."), 503

    @application.context_processor
    def template_context():
        return {
            "config": public_config(),
            "csrf_token": csrf_token(),
            "year": salon_now().year,
            "staff_enabled": bool(get_setting("admin_password_hash")),
            "staff_authenticated": staff_logged_in(),
        }

    @application.get("/")
    def home():
        return render_template("index.html")

    @application.get("/manage")
    def manage():
        return render_template("manage.html")

    @application.get("/api/config")
    def configuration():
        return jsonify(public_config())

    @application.get("/api/availability")
    def availability():
        service = service_for(request.args.get("service_id", ""))
        choices = stylist_choices(request.args.get("stylist_id", "any"))
        day = parse_date(request.args.get("date", ""))
        return jsonify(slots=find_slots(day, service["duration"], choices))

    @application.post("/api/bookings")
    def create_booking():
        data = parse_payload()
        service = service_for(text_field(data, "service_id", 64))
        choices = stylist_choices(text_field(data, "stylist_id", 64))
        day = parse_date(data.get("date", ""))
        start = minutes(data.get("time", ""))
        validate_slot(day, start, service["duration"])
        name = text_field(data, "name", 100)
        email = valid_email(text_field(data, "email", 254))
        phone = text_field(data, "phone", 40, required=False)
        notes = text_field(data, "notes", 1000, required=False)
        if phone and not re.fullmatch(r"[0-9+(). \-xX]+", phone):
            raise BookingError("Please enter a valid phone number or leave it blank.")
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            available = available_stylists(day, start, service["duration"], choices)
            if not available:
                raise BookingError("That time was just booked. Please choose another available time.", 409)
            reference = "DEES-" + secrets.token_hex(6).upper()
            timestamp = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                """INSERT INTO bookings (
                    reference, service_id, service_name, duration, price, stylist_id,
                    appointment_date, start_minute, end_minute, name, email, phone, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (reference, service["id"], service["name"], service["duration"], service["price"],
                 available[0], day.isoformat(), start, start + service["duration"], name, email,
                 phone, notes, timestamp, timestamp),
            )
            row = db.execute("SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)).fetchone()
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify(booking=booking_json(row)), 201

    @application.post("/api/bookings/lookup")
    def booking_lookup():
        return jsonify(booking=booking_json(lookup_booking(parse_payload())))

    @application.post("/api/bookings/cancel")
    def cancel_booking():
        data = parse_payload()
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = lookup_booking(data)
            ensure_changeable(row)
            db.execute(
                "UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
            updated = db.execute("SELECT * FROM bookings WHERE id = ?", (row["id"],)).fetchone()
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify(booking=booking_json(updated))

    @application.post("/api/bookings/availability")
    def reschedule_availability():
        data = parse_payload()
        row = lookup_booking(data)
        ensure_changeable(row)
        day = parse_date(data.get("date", ""))
        choices = stylist_choices(data.get("stylist_id", row["stylist_id"]))
        return jsonify(slots=find_slots(day, row["duration"], choices, row["id"]))

    @application.post("/api/bookings/reschedule")
    def reschedule_booking():
        data = parse_payload()
        day = parse_date(data.get("date", ""))
        start = minutes(data.get("time", ""))
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            row = lookup_booking(data)
            ensure_changeable(row)
            choices = stylist_choices(data.get("stylist_id", row["stylist_id"]))
            validate_slot(day, start, row["duration"])
            available = available_stylists(day, start, row["duration"], choices, row["id"])
            if not available:
                raise BookingError("That time was just booked. Your original appointment is still reserved.", 409)
            db.execute(
                """UPDATE bookings SET stylist_id = ?, appointment_date = ?,
                   start_minute = ?, end_minute = ?, updated_at = ? WHERE id = ?""",
                (available[0], day.isoformat(), start, start + row["duration"],
                 datetime.now(timezone.utc).isoformat(), row["id"]),
            )
            updated = db.execute("SELECT * FROM bookings WHERE id = ?", (row["id"],)).fetchone()
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify(booking=booking_json(updated))

    @application.route("/staff/login", methods=["GET", "POST"])
    def staff_login():
        if staff_logged_in():
            return redirect(url_for("staff"))
        error = None
        if request.method == "POST":
            limit_attempts("staff-login", 10, 600)
            stored_hash = get_setting("admin_password_hash")
            password = request.form.get("password", "")
            if stored_hash and len(password) <= 1024 and check_password_hash(stored_hash, password):
                version = get_setting("admin_version")
                session.clear()
                session.permanent = True
                session["staff_version"] = version
                limit_attempts("staff-login", 10, 600, clear=True)
                return redirect(url_for("staff"))
            limit_attempts("staff-login", 10, 600, failure=True)
            error = "The staff password is incorrect. Please try again." if stored_hash else "Staff access has not been set up yet."
        return render_template("login.html", error=error), 401 if error else 200

    @application.get("/staff")
    @staff_required
    def staff():
        return render_template("staff.html")

    @application.post("/staff/logout")
    def staff_logout():
        session.clear()
        return redirect(url_for("home"))

    @application.get("/api/staff/appointments")
    @staff_required
    def staff_appointments():
        selected = parse_date(request.args.get("date", salon_now().date().isoformat()), booking_window=False)
        stylist_id = request.args.get("stylist_id", "all")
        if stylist_id != "all":
            stylist_choices(stylist_id)
            if stylist_id == "any":
                raise BookingError("Please choose a specific stylist or all stylists.")
        rows = get_db().execute(
            """SELECT * FROM bookings WHERE appointment_date = ?
               AND (? = 'all' OR stylist_id = ?) ORDER BY start_minute, stylist_id, id""",
            (selected.isoformat(), stylist_id, stylist_id),
        ).fetchall()
        confirmed = [row for row in rows if row["status"] == "confirmed"]
        return jsonify(
            bookings=[booking_json(row) for row in rows],
            summary={
                "appointments": len(confirmed),
                "cancelled": len(rows) - len(confirmed),
                "total_value": sum(row["price"] for row in confirmed),
                "total_minutes": sum(row["duration"] for row in confirmed),
            },
        )

    @application.cli.command("set-admin-password")
    @click.password_option(confirmation_prompt=True, help="New staff password (at least 10 characters).")
    def set_admin_password(password):
        """Create or change the password for /staff. Existing staff sessions expire."""
        if len(password) < 10:
            raise click.ClickException("Please choose a password with at least 10 characters.")
        if len(password) > 1024:
            raise click.ClickException("Please choose a password with no more than 1,024 characters.")
        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                       ("admin_password_hash", generate_password_hash(password)))
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                       ("admin_version", secrets.token_hex(16)))
            db.commit()
        except Exception:
            db.rollback()
            raise
        click.echo("Staff password saved. Sign in at /staff. Any previous staff sessions are now signed out.")

    return application


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
