"""Put a few example clients into the database so the booking grid looks like a real, busy salon.

    python seed.py          adds example bookings for the next 14 days
"""
import random
from datetime import timedelta

import app as studio


def main():
	rng = random.Random(7)
	names = ["Ada", "Tola", "Ngozi", "Funmi", "Chioma", "Zainab", "Bisi", "Amaka", "Kemi", "Halima"]
	today, _ = studio.lagos_now()
	db = studio.connect()
	added = 0
	for i in range(studio.STUDIO["days_ahead"]):
		day = today + timedelta(days=i)
		hours = studio.HOURS[studio.js_weekday(day)]
		if not hours:
			continue
		for _ in range(rng.randint(3, 6)):
			key = rng.choice(list(studio.SERVICES))
			svc = studio.SERVICES[key]
			spots = (hours[1] * 60 - hours[0] * 60 - svc["mins"]) // studio.STUDIO["step"]
			if spots < 0:
				continue
			start = hours[0] * 60 + rng.randint(0, spots) * studio.STUDIO["step"]
			digits = "0803" + "".join(str(rng.randint(0, 9)) for _ in range(7))
			try:
				studio.save_booking(db, {
					"service": key, "date": day.isoformat(), "start": start, "mins": svc["mins"],
					"price": svc["price"], "name": rng.choice(names), "phone": digits,
					"phone_key": digits, "note": "",
					})
				added += 1
			except studio.BookingError:
				pass  # that time was already full, skip it
	db.close()
	print(f"Added {added} example bookings.")


if __name__ == "__main__":
	main()
