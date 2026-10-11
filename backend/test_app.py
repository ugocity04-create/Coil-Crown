"""Run with:  python -m unittest -v   (from the backend folder)"""
import base64
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from unittest import mock

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["ADMIN_PASSWORD"] = "secret-pass"
os.environ["ALLOWED_ORIGINS"] = "https://me.github.io"

import app as studio  # noqa: E402  (must come after the settings above)


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def first_ref():
    db = studio.connect()
    try:
        return db.execute("SELECT ref FROM bookings").fetchone()["ref"]
    finally:
        db.close()


def next_open_day(after_days=1, weekday=None):
    """A future day the studio is open (optionally a specific JS weekday)."""
    today, _ = studio.lagos_now()
    for i in range(after_days, studio.STUDIO["days_ahead"]):
        d = today + timedelta(days=i)
        if studio.HOURS[studio.js_weekday(d)] and (weekday is None or studio.js_weekday(d) == weekday):
            return d
    raise AssertionError("no open day found")


class ApiTest(unittest.TestCase):
    def setUp(self):
        studio.app.config["TESTING"] = True
        self.client = studio.app.test_client()
        db = studio.connect()
        db.execute("DELETE FROM bookings")
        db.close()
        self.day = next_open_day().isoformat()
        self.n = 0

    def booking(self, **over):
        self.n += 1
        data = {"service": "wash", "date": self.day, "start": 600, "name": "Ada Obi",
                "phone": f"0803 123 {4000 + self.n}", "note": ""}
        data.update(over)
        return data

    def post(self, **over):
        return self.client.post("/api/bookings", json=self.booking(**over))

    # ----- the happy path -----
    def test_create_and_busy(self):
        self.assertEqual(self.client.get(f"/api/busy?date={self.day}").get_json(), [])
        r = self.post()
        self.assertEqual(r.status_code, 201)
        body = r.get_json()
        self.assertRegex(body["ref"], r"^CC-[A-Z0-9]{4}$")
        self.assertEqual((body["mins"], body["price"]), (60, 8000))
        busy = self.client.get(f"/api/busy?date={self.day}").get_json()
        self.assertEqual(busy, [{"start": 600, "mins": 60}])

    def test_busy_never_leaks_personal_details(self):
        self.post()
        text = self.client.get(f"/api/busy?date={self.day}").get_data(as_text=True)
        self.assertNotIn("Ada", text)
        self.assertNotIn("0803", text)

    def test_server_sets_the_price_and_length(self):
        r = self.post(service="knotless", start=540, price=1, mins=30)
        self.assertEqual((r.get_json()["price"], r.get_json()["mins"]), (35000, 240))

    # ----- chairs -----
    def test_two_chairs_then_full(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 201)
        r = self.post()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["error"], "taken")

    def test_partial_overlap_is_blocked_but_next_hour_is_free(self):
        self.post(service="silk", start=600)   # 10:00 to 12:00
        self.post(service="silk", start=600)
        self.assertEqual(self.post(start=660).status_code, 409)   # 11:00 overlaps
        self.assertEqual(self.post(start=720).status_code, 201)   # 12:00 is free

    def test_cancelled_booking_frees_the_chair(self):
        self.post(); self.post()
        db = studio.connect()
        db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = (SELECT MIN(id) FROM bookings)")
        db.close()
        self.assertEqual(self.post().status_code, 201)

    def test_last_chair_goes_to_exactly_one_person(self):
        self.post()  # one chair already used
        results = []

        def go(i):
            c = studio.app.test_client()
            results.append(c.post("/api/bookings", json=self.booking(phone=f"0805 000 {5000 + i}")).status_code)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(sorted(results), [201, 409, 409, 409, 409, 409])

    # ----- rules -----
    def test_closed_day_is_refused(self):
        today, _ = studio.lagos_now()
        sunday = next(today + timedelta(days=i) for i in range(1, 14)
                      if studio.js_weekday(today + timedelta(days=i)) == 0)
        r = self.post(date=sunday.isoformat())
        self.assertEqual((r.status_code, r.get_json()["error"]), (422, "closed"))

    def test_monday_is_open(self):
        monday = next_open_day(weekday=1).isoformat()
        self.assertEqual(self.post(date=monday).status_code, 201)

    def test_bad_times_are_refused(self):
        for start in (605, 480, 1020 + 30, 1000):   # off the 30-min grid, before open, after close, runs past close
            with self.subTest(start=start):
                self.assertEqual(self.post(start=start, service="silk" if start == 1000 else "wash").status_code, 422)
        self.assertEqual(self.post(start="600").status_code, 422)
        self.assertEqual(self.post(start=True).status_code, 422)

    def test_past_and_lead_time(self):
        today, _ = studio.lagos_now()
        if not studio.HOURS[studio.js_weekday(today)]:
            self.skipTest("studio is closed today")
        with mock.patch.object(studio, "lagos_now", return_value=(today, 12 * 60)):   # pretend it is 12:00
            soon = self.post(date=today.isoformat(), start=12 * 60 + 30)     # inside the 60-minute lead time
            later = self.post(date=today.isoformat(), start=13 * 60 + 30)
        self.assertEqual((soon.status_code, soon.get_json()["error"]), (422, "past"))
        self.assertEqual(later.status_code, 201)

    def test_date_window(self):
        today, _ = studio.lagos_now()
        for d in (today - timedelta(days=1), today + timedelta(days=studio.STUDIO["days_ahead"])):
            self.assertEqual(self.post(date=d.isoformat()).status_code, 422)
        self.assertEqual(self.post(date="not-a-date").status_code, 400)
        self.assertEqual(self.post(date="2026-02-30").status_code, 400)

    def test_bad_details(self):
        self.assertEqual(self.post(service="haircut-of-doom").status_code, 422)
        self.assertEqual(self.post(name="  ").status_code, 422)
        self.assertEqual(self.post(name="x" * 81).status_code, 422)
        self.assertEqual(self.post(phone="123").status_code, 422)
        self.assertEqual(self.post(note="x" * 501).status_code, 422)
        self.assertEqual(self.client.post("/api/bookings", data="nope").status_code, 400)
        self.assertEqual(self.client.post("/api/bookings", data="x" * 20000).status_code, 413)

    def test_one_phone_cannot_hog_the_diary(self):
        for _ in range(studio.MAX_ACTIVE_PER_PHONE):
            self.assertEqual(self.post(phone="0803 999 0000", start=540 + 60 * self.n).status_code, 201)
        r = self.post(phone="0803 999 0000", start=900)
        self.assertEqual((r.status_code, r.get_json()["error"]), (429, "too_many"))

    # ----- clients cancelling their own booking -----
    def cancel(self, **over):
        data = {"ref": self.ref, "phone": self.phone}
        data.update(over)
        return self.client.post("/api/bookings/cancel", json=data)

    def make_booking_for_cancel(self, start=540):
        self.day = next_open_day(after_days=3).isoformat()   # far enough ahead to be cancellable online
        self.phone = "0803 222 3333"
        r = self.post(phone=self.phone, start=start)
        self.assertEqual(r.status_code, 201)
        self.ref = r.get_json()["ref"]

    def test_cancel_frees_the_time(self):
        self.make_booking_for_cancel()
        self.assertEqual(len(self.client.get(f"/api/busy?date={self.day}").get_json()), 1)
        r = self.cancel()
        self.assertEqual((r.status_code, r.get_json()["ok"]), (200, True))
        self.assertEqual(self.client.get(f"/api/busy?date={self.day}").get_json(), [])

    def test_cancelled_booking_lets_someone_else_book(self):
        self.make_booking_for_cancel()
        self.post(start=540)                            # second chair
        self.assertEqual(self.post(start=540).status_code, 409)
        self.cancel()
        self.assertEqual(self.post(start=540).status_code, 201)

    def test_cancel_accepts_the_phone_written_differently(self):
        self.make_booking_for_cancel()
        r = self.cancel(phone="+234 803 222 3333", ref=self.ref.lower())
        self.assertEqual(r.status_code, 200)

    def test_cancel_with_wrong_phone_or_ref_looks_the_same(self):
        self.make_booking_for_cancel()
        wrong_phone = self.cancel(phone="0809 999 9999")
        wrong_ref = self.cancel(ref="CC-ZZZZ")
        self.assertEqual((wrong_phone.status_code, wrong_ref.status_code), (404, 404))
        self.assertEqual(wrong_phone.get_json(), wrong_ref.get_json())
        self.assertEqual(self.cancel(phone="").status_code, 404)
        self.assertEqual(self.client.post("/api/bookings/cancel", data="nope").status_code, 400)
        # the booking is still there
        self.assertEqual(len(self.client.get(f"/api/busy?date={self.day}").get_json()), 1)

    def test_cannot_cancel_twice(self):
        self.make_booking_for_cancel()
        self.assertEqual(self.cancel().status_code, 200)
        r = self.cancel()
        self.assertEqual((r.status_code, r.get_json()["error"]), (409, "already_cancelled"))

    def test_too_late_to_cancel(self):
        self.make_booking_for_cancel(start=540)         # 9:00am on self.day
        day = studio.date.fromisoformat(self.day)
        with mock.patch.object(studio, "lagos_now", return_value=(day - timedelta(days=1), 10 * 60)):   # 23 hours before
            late = self.cancel()
        self.assertEqual((late.status_code, late.get_json()["error"]), (422, "too_late"))
        with mock.patch.object(studio, "lagos_now", return_value=(day - timedelta(days=1), 9 * 60)):    # exactly 24 hours before
            ok = self.cancel()
        self.assertEqual(ok.status_code, 200)

    # ----- CORS -----
    def test_cors_only_for_listed_sites(self):
        ok = self.client.get(f"/api/busy?date={self.day}", headers={"Origin": "https://me.github.io"})
        bad = self.client.get(f"/api/busy?date={self.day}", headers={"Origin": "https://evil.example"})
        self.assertEqual(ok.headers.get("Access-Control-Allow-Origin"), "https://me.github.io")
        self.assertIsNone(bad.headers.get("Access-Control-Allow-Origin"))
        pre = self.client.options("/api/bookings", headers={"Origin": "https://me.github.io"})
        self.assertEqual(pre.headers.get("Access-Control-Allow-Origin"), "https://me.github.io")

    # ----- admin -----
    def test_admin_needs_the_password(self):
        self.assertEqual(self.client.get("/admin").status_code, 401)
        self.assertEqual(self.client.get("/admin", headers=basic("admin", "wrong")).status_code, 401)
        self.assertEqual(self.client.get("/admin", headers=basic("admin", "secret-pass")).status_code, 200)

    def test_admin_is_off_without_a_password(self):
        with mock.patch.object(studio, "ADMIN_PASSWORD", ""):
            self.assertEqual(self.client.get("/admin", headers=basic("admin", "")).status_code, 503)

    def test_admin_lists_escapes_and_cancels(self):
        self.post(name="<script>alert(1)</script>", note="<b>hi</b>")
        page = self.client.get("/admin", headers=basic("admin", "secret-pass")).get_data(as_text=True)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        ref = first_ref()
        self.assertIn(ref, page)
        r = self.client.post(f"/admin/cancel/{ref}", headers=basic("admin", "secret-pass"))
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.client.get(f"/api/busy?date={self.day}").get_json(), [])

    def test_admin_cancel_needs_password_and_same_site(self):
        self.post()
        ref = first_ref()
        self.assertEqual(self.client.post(f"/admin/cancel/{ref}").status_code, 401)
        r = self.client.post(f"/admin/cancel/{ref}", headers={**basic("admin", "secret-pass"), "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(self.client.get(f"/api/busy?date={self.day}").get_json()), 1)

    # ----- done / no-show -----
    def status_of(self, ref):
        db = studio.connect()
        try:
            return db.execute("SELECT status FROM bookings WHERE ref = ?", (ref,)).fetchone()["status"]
        finally:
            db.close()

    def make_past_booking(self):
        """Put a confirmed booking in the database yesterday, as if the appointment already happened."""
        yesterday = (studio.lagos_now()[0] - timedelta(days=1)).isoformat()
        db = studio.connect()
        db.execute(
            "INSERT INTO bookings (ref, service, date, start, mins, price, name, phone, phone_key, created_at)"
            " VALUES ('CC-PAST', 'wash', ?, 600, 60, 8000, 'Old Client', '08031234000', '08031234000', 'x')",
            (yesterday,))
        db.close()
        return "CC-PAST"

    def test_admin_marks_past_booking_done_and_totals_it(self):
        ref = self.make_past_booking()
        auth = basic("admin", "secret-pass")
        self.assertEqual(self.client.post(f"/admin/done/{ref}", headers=auth).status_code, 303)
        self.assertEqual(self.status_of(ref), "completed")
        page = self.client.get("/admin", headers=auth).get_data(as_text=True)
        self.assertIn("8,000", page)
        self.assertIn("Done", page)
        # a finished booking cannot be flipped to cancelled or no-show afterwards
        self.client.post(f"/admin/cancel/{ref}", headers=auth)
        self.client.post(f"/admin/noshow/{ref}", headers=auth)
        self.assertEqual(self.status_of(ref), "completed")

    def test_admin_marks_no_show(self):
        ref = self.make_past_booking()
        self.client.post(f"/admin/noshow/{ref}", headers=basic("admin", "secret-pass"))
        self.assertEqual(self.status_of(ref), "no_show")

    def test_future_booking_cannot_be_marked_done(self):
        self.post()
        ref = first_ref()
        r = self.client.post(f"/admin/done/{ref}", headers=basic("admin", "secret-pass"))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.status_of(ref), "confirmed")

    def test_done_only_after_the_appointment_has_ended(self):
        today, _ = studio.lagos_now()
        db = studio.connect()
        db.execute(
            "INSERT INTO bookings (ref, service, date, start, mins, price, name, phone, phone_key, created_at)"
            " VALUES ('CC-NOW', 'wash', ?, 600, 60, 8000, 'Today Client', '08031234111', '08031234111', 'x')",
            (today.isoformat(),))
        db.close()
        auth = basic("admin", "secret-pass")
        with mock.patch.object(studio, "lagos_now", return_value=(today, 630)):  # 10:30, mid-appointment
            self.assertEqual(self.client.post("/admin/done/CC-NOW", headers=auth).status_code, 422)
            self.assertNotIn("/admin/done/CC-NOW", self.client.get("/admin", headers=auth).get_data(as_text=True))
        self.assertEqual(self.status_of("CC-NOW"), "confirmed")
        with mock.patch.object(studio, "lagos_now", return_value=(today, 660)):  # 11:00, just ended
            self.assertIn("/admin/done/CC-NOW", self.client.get("/admin", headers=auth).get_data(as_text=True))
            self.assertEqual(self.client.post("/admin/done/CC-NOW", headers=auth).status_code, 303)
        self.assertEqual(self.status_of("CC-NOW"), "completed")

    def test_done_needs_password_and_same_site(self):
        ref = self.make_past_booking()
        self.assertEqual(self.client.post(f"/admin/done/{ref}").status_code, 401)
        r = self.client.post(f"/admin/done/{ref}", headers={**basic("admin", "secret-pass"), "Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.status_of(ref), "confirmed")

    def test_completed_booking_cannot_be_cancelled_by_client(self):
        ref = self.make_past_booking()
        self.client.post(f"/admin/done/{ref}", headers=basic("admin", "secret-pass"))
        r = self.client.post("/api/bookings/cancel", json={"ref": ref, "phone": "08031234000"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.status_of(ref), "completed")


if __name__ == "__main__":
    unittest.main()
