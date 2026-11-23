"""Behavior tests for scheduling, privacy, and staff access.

Run from the project folder: python -m unittest discover -s tests -v
The suite uses temporary databases and never touches the demo's saved bookings.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tempfile
import threading
import unittest
from zoneinfo import ZoneInfo

from jinja2 import DictLoader

from app import create_app


class SalonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.configuration = {
            "TESTING": True,
            "SECRET_KEY": "isolated-test-session-key",
            "INSTANCE_PATH": self.directory.name,
            "DATABASE": str(Path(self.directory.name) / "test.sqlite3"),
            "NOW": lambda: datetime(2030, 5, 7, 8, 0, tzinfo=ZoneInfo("America/New_York")),
        }
        self.app = create_app(self.configuration)
        self.app.jinja_loader = DictLoader({
            "index.html": "Dee's Hair Salon",
            "manage.html": "Manage appointment",
            "login.html": "{{ error or 'Staff sign in' }}",
            "staff.html": "Staff schedule",
        })
        self.client = self.app.test_client()
        self.token = self.client.get("/api/config").json["csrf_token"]

    def tearDown(self):
        self.directory.cleanup()

    def post(self, path, data, *, client=None, token=None):
        return (client or self.client).post(
            path, json=data, headers={"X-CSRF-Token": token or self.token},
        )

    def booking_data(self, **changes):
        data = {
            "service_id": "cut-style", "stylist_id": "dee",
            "date": "2030-05-07", "time": "09:00", "name": "Test Guest",
            "email": "guest@example.com", "phone": "555-0100", "notes": "Classroom test",
        }
        data.update(changes)
        return data

    def create_booking(self, **changes):
        response = self.post("/api/bookings", self.booking_data(**changes))
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["booking"]

    def credentials(self, booking):
        return {"reference": booking["reference"], "email": booking["email"]}

    def test_booking_is_persisted_across_application_instances(self):
        booking = self.create_booking(email="Guest@Example.com")
        self.assertEqual(booking["email"], "guest@example.com")
        self.assertEqual(booking["end_time"], "10:00")
        self.assertEqual(booking["price"], 45)
        other_app = create_app(self.configuration)
        client = other_app.test_client()
        token = client.get("/api/config").json["csrf_token"]
        result = self.post("/api/bookings/lookup", self.credentials(booking), client=client, token=token)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["booking"]["reference"], booking["reference"])

    def test_overlap_uses_full_duration_and_allows_touching_boundaries(self):
        self.create_booking(service_id="full-highlights")  # Dee is busy 09:00–11:30.
        overlap = self.post("/api/bookings", self.booking_data(time="11:00", service_id="clipper-cut"))
        self.assertEqual(overlap.status_code, 409)
        boundary = self.post("/api/bookings", self.booking_data(time="11:30"))
        self.assertEqual(boundary.status_code, 201)
        same_time_other_person = self.post("/api/bookings", self.booking_data(stylist_id="morgan"))
        self.assertEqual(same_time_other_person.status_code, 201)

    def test_any_stylist_assigns_an_available_person(self):
        self.create_booking(stylist_id="dee")
        booking = self.create_booking(stylist_id="any")
        self.assertEqual(booking["stylist_id"], "morgan")

    def test_concurrent_requests_cannot_double_book_a_stylist(self):
        barrier = threading.Barrier(2)

        def reserve():
            client = self.app.test_client()
            token = client.get("/api/config").json["csrf_token"]
            barrier.wait(timeout=5)
            return self.post("/api/bookings", self.booking_data(), client=client, token=token).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(sorted(responses), [201, 409])

    def test_closed_past_out_of_window_and_invalid_slots_are_rejected(self):
        invalid = [
            {"date": "2030-05-12"},  # Sunday
            {"date": "2030-05-13"},  # Monday
            {"date": "2030-05-06"},  # Past day
            {"date": "2031-05-07"},  # Beyond 90 days
            {"time": "08:00"},
            {"time": "16:30"},  # A 60-minute service does not fit.
            {"time": "09:15"},  # Not an offered start time.
            {"time": "99:99"},
            {"date": "2030-02-31"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assertEqual(self.post("/api/bookings", self.booking_data(**changes)).status_code, 400)
        self.app.config["NOW"] = lambda: datetime(2030, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(self.post("/api/bookings", self.booking_data(time="09:00")).status_code, 400)

    def test_availability_respects_duration_hours_and_current_time(self):
        response = self.client.get("/api/availability?service_id=full-highlights&stylist_id=dee&date=2030-05-07")
        times = [slot["time"] for slot in response.json["slots"]]
        self.assertIn("14:30", times)
        self.assertNotIn("15:00", times)
        closed = self.client.get("/api/availability?service_id=cut-style&stylist_id=any&date=2030-05-12")
        self.assertEqual(closed.json["slots"], [])
        self.create_booking()
        updated = self.client.get("/api/availability?service_id=cut-style&stylist_id=dee&date=2030-05-07")
        times = [slot["time"] for slot in updated.json["slots"]]
        self.assertNotIn("09:00", times)
        self.assertNotIn("09:30", times)
        self.assertIn("10:00", times)

    def test_mutations_require_csrf_token(self):
        response = self.client.post("/api/bookings", json=self.booking_data())
        self.assertEqual(response.status_code, 400)
        self.assertIn("session", response.json["error"])
        wrong = self.client.post("/api/bookings", json=self.booking_data(), headers={"X-CSRF-Token": "wrong"})
        self.assertEqual(wrong.status_code, 400)

    def test_lookup_requires_both_private_reference_and_matching_email(self):
        booking = self.create_booking()
        wrong_email = self.post("/api/bookings/lookup", {"reference": booking["reference"], "email": "someone@example.com"})
        self.assertEqual(wrong_email.status_code, 404)
        wrong_reference = self.post("/api/bookings/lookup", {"reference": "DEES-UNKNOWN", "email": booking["email"]})
        self.assertEqual(wrong_reference.status_code, 404)
        self.assertEqual(self.client.get("/api/staff/appointments").status_code, 401)
        public = self.client.get("/api/availability?service_id=cut-style&stylist_id=any&date=2030-05-07").get_data(as_text=True)
        self.assertNotIn(booking["email"], public)
        self.assertNotIn(booking["reference"], public)

    def test_cancel_releases_the_time(self):
        booking = self.create_booking()
        response = self.post("/api/bookings/cancel", self.credentials(booking))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["booking"]["status"], "cancelled")
        self.create_booking()  # Same stylist and time are free again.
        second_cancel = self.post("/api/bookings/cancel", self.credentials(booking))
        self.assertEqual(second_cancel.status_code, 409)

    def test_failed_reschedule_preserves_the_original_appointment(self):
        booking = self.create_booking()
        self.create_booking(time="11:00")
        response = self.post("/api/bookings/reschedule", {
            **self.credentials(booking), "date": "2030-05-07", "time": "11:00", "stylist_id": "dee",
        })
        self.assertEqual(response.status_code, 409)
        original = self.post("/api/bookings/lookup", self.credentials(booking)).json["booking"]
        self.assertEqual(original["time"], "09:00")
        slots = self.post("/api/bookings/availability", {
            **self.credentials(booking), "date": "2030-05-07", "stylist_id": "dee",
        })
        self.assertIn("09:00", [slot["time"] for slot in slots.json["slots"]])
        moved = self.post("/api/bookings/reschedule", {
            **self.credentials(booking), "date": "2030-05-07", "time": "13:00", "stylist_id": "jamie",
        })
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json["booking"]["time"], "13:00")
        self.assertEqual(moved.json["booking"]["stylist_id"], "jamie")
        self.assertEqual(moved.json["booking"]["reference"], booking["reference"])

    def test_past_appointments_cannot_be_cancelled_or_rescheduled(self):
        booking = self.create_booking()
        self.app.config["NOW"] = lambda: datetime(2030, 5, 7, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(self.post("/api/bookings/cancel", self.credentials(booking)).status_code, 409)
        response = self.post("/api/bookings/reschedule", {
            **self.credentials(booking), "date": "2030-05-08", "time": "09:00", "stylist_id": "dee",
        })
        self.assertEqual(response.status_code, 409)

    def test_invalid_contact_and_payload_values_are_rejected(self):
        for changes in ({"name": " "}, {"email": "not-email"}, {"name": "A" * 101}, {"phone": "call me"}, {"notes": "x" * 1001}, {"service_id": "fake"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.post("/api/bookings", self.booking_data(**changes)).status_code, 400)
        self.assertEqual(self.post("/api/bookings", ["invalid"]).status_code, 400)

    def test_staff_password_login_logout_and_summary(self):
        self.create_booking()
        cancelled = self.create_booking(time="11:00")
        self.post("/api/bookings/cancel", self.credentials(cancelled))
        runner = self.app.test_cli_runner()
        weak = runner.invoke(args=["set-admin-password"], input="short\nshort\n")
        self.assertNotEqual(weak.exit_code, 0)
        result = runner.invoke(args=["set-admin-password"], input="classroom-test-password\nclassroom-test-password\n")
        self.assertEqual(result.exit_code, 0, result.output)
        wrong = self.client.post("/staff/login", data={"password": "incorrect", "csrf_token": self.token})
        self.assertEqual(wrong.status_code, 401)
        login = self.client.post("/staff/login", data={"password": "classroom-test-password", "csrf_token": self.token})
        self.assertEqual(login.status_code, 302)
        staff = self.client.get("/api/staff/appointments?date=2030-05-07")
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff.json["summary"]["appointments"], 1)
        self.assertEqual(staff.json["summary"]["cancelled"], 1)
        self.assertEqual(staff.json["summary"]["total_value"], 45)
        fresh_token = self.client.get("/api/config").json["csrf_token"]
        self.client.post("/staff/logout", data={"csrf_token": fresh_token})
        self.assertEqual(self.client.get("/api/staff/appointments").status_code, 401)

    def test_changing_staff_password_expires_previous_staff_sessions(self):
        runner = self.app.test_cli_runner()
        runner.invoke(args=["set-admin-password"], input="classroom-test-password\nclassroom-test-password\n")
        self.client.post("/staff/login", data={"password": "classroom-test-password", "csrf_token": self.token})
        self.assertEqual(self.client.get("/api/staff/appointments").status_code, 200)
        runner.invoke(args=["set-admin-password"], input="new-classroom-password\nnew-classroom-password\n")
        self.assertEqual(self.client.get("/api/staff/appointments").status_code, 401)


if __name__ == "__main__":
    unittest.main()
