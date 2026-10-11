// --------- Settings (change this for a real client) -------- //
const STUDIO = {
  name: "Coil & Crown",
  whatsapp: "2348086987293", // international format, no plus sign or spaces.
  chairs: 2,                // how many client can be served at the same time
  leadMinutes: 60,         // a time can't be booked less than this long from now
  daysAhead: 14,          // how many days ahead people can book
  step: 30               // minutes between start times
};

// Opening hours by weekday (0 = Sunday): [open hour, close hour] on a 24h clock. null = closed.
const HOURS = {
  0: null,
  1: [9, 18], 2: [9, 18], 3: [9, 18], 4: [9, 18], 5: [9, 18],
  6: [8, 19]
};

const SERVICES = [
  {id: "wash",       name: "Wash and blow-dry",       desc:"Deep cleanse, conditioner and a smooth blow-dry.",                   mins: 60,    price:  8000},
  {id: "silk",       name: "Silk press",              desc:"Straight, shiny and heat-protected. Last up to two weeks",           mins: 120,   price:  20000, featured: true},
  {id: "twist",      name: "Twist-out and style",     desc:"Defined twists, set and styled for the week.",                       mins: 120,   price:  15000, featured: true},
  {id: "cornrows",   name: "Cornrows",                desc:"Neat straight-back lines or a simple pattern.",                      mins: 90,    price:  10000},
  {id: "knotless",   name: "Knotless braids",         desc:"Medium size, light on your edges. Braiding hair not included",       mins: 240,   price:  35000, featured: true},
  {id: "kids",       name: "Kids braids",             desc:"Gentle braiding for children under 12.",                             mins: 90,    price:  8000},
];

// ------- Helpers -------- //
const $ = (sel) => document.querySelector(sel);
const byId = (id) => SERVICES.find((s) => s.id === id);
const naira = (n) => "₦" + n.toLocaleString("en-NG");
const pad = (n) => String(n).padStart(2, "0");
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const FULL_DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Dates are plain "YYYY-MM-DD" strings. "Now" is always Lagos time, wherever the visitor is.
function lagosNow() {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Lagos", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12:false
  }).formatToParts(new Date());
  const get = (t) => parts.find((p) => p.type === t).value;
  return {
    date: get("year") + "-" + get("month") + "-" + get("day"),
    minutes: (Number(get("hour")) % 24) * 60 + Number(get("minute"))
  };
}

function addDays(dateStr, n) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.getUTCFullYear() + "-" + pad(t.getUTCMonth() + 1) + "-" + pad(t.getUTCDate());
}

function weekday(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).getUTCDay();
}

function dayLabel(dateStr) {
  return DAY_NAMES[weekday(dateStr)] + " " + Number(dateStr.slice(8)) + " " + MONTHS[Number(dateStr.slice(5, 7)) - 1];
}

// Times are stored as minutes after midnight, so 9:30am is 570.
function fmtTime(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return ((h + 11) % 12 + 1) + (m ? ":" + pad(m) : "") + (h >= 12 ? "pm" : "am");
}

function fmtDuration(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (!h) return m + " min";
  return h + " hr" + (m ? " " + m + " min" : "");
}

// ---------- Availability rules ------------ //
// busy is a list of { start, mins } bookings that already exist on one day.
// A time is free when , in every 30-minute block of the new booking, fewer than STUDIO.chairs chairs are in use.
function isFree(busy, start, mins) {
  for (let t = start; t < start + mins; t += STUDIO.step) {
    let used = 0;
    for (const b of busy) {
      if (b.start <= t && t < b.start + b.mins) used++;
    }
    if (used >= STUDIO.chairs) return false;
  }
  return true;
}

function slotsFor(date, mins, busy) {
  const hours = HOURS[weekday(date)];
  if (!hours) return [];
  const [open, close] = hours.map((h) => h * 60);
  const now = lagosNow();
  const out = []
  for (let start = open; start + mins <= close; start += STUDIO.step) {
    const past = date < now.date || (date === now.date && start < now.minutes + STUDIO.leadMinutes);
    out.push({ start, ok: !past && isFree(busy, start, mins) });
  }
  return out;
}

// --------- Data layer (demo mode) ----------- //
// API_URL = "" runs demo mode: bookings live in this browser only.
// Set it to python backend's address (no slash at the end) to use resl bookings, for example:
//    const API_URL = "http://127.0.0.1:5001";
const API_URL = "";
const CANCEL_HOURS = 24; // keep the same as cancel_hours in backend/app.py
const STORE_KEY = "coil-crown-bookings";
let memory = [];

function savedBookings() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY)) || memory; } catch (e) { return memory }
}

function saveBooking(b) {
  memory = savedBookings().concat([b]);
  try { localStorage.setItem(STORE_KEY, JSON.stringify(memory)); } catch (e) { /* storage blocked: memory is used */ }
}

// Pretend the studio already has a few clients, so the time grid looks like a real week.
function demoBusy(date) {
  const hours = HOURS[weekday(date)];
  if (!hours) return [];
  let seed = 2166136261;
  for (let i = 0; i < date.length; i++) { seed ^= date.charCodeAt(i); seed = Math.imul(seed, 16777619); }
  seed >>>= 0;
  const next = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 4294967296;
  const [open, close] = hours.map((h) => h * 60);
  const out = [];
  const count = 3 + Math.floor(next() * 4); // 3 to 6 bookings
  for (let i = 0; i < count; i++) {
    const mins = [60, 90, 120, 240][Math.floor(next() * 4)];
    const spots = Math.floor((close - open - mins) / STUDIO.step);
    if (spots < 0) continue;
    out.push({ start: open + Math.floor(next() * (spots + 1)) * STUDIO.step, mins });
  }
  return out;
}

async function getBusy(date) {
  if (API_URL) {
    const res = await fetch(API_URL + "/api/busy?date=" + encodeURIComponent(date));
    if (!res.ok) throw new Error("network");
    return res.json();
  }
  const mine = savedBookings().filter((b) => b.date === date).map((b) => ({ start: b.start, mins: b.mins }));
  return demoBusy(date).concat(mine);
}

function makeRef() {
  const chars = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"; // no 0/O or 1/I, so a code is easy to read out
  let s = "";
  for (let i = 0; i < 4; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return "CC-" + s;
}

async function createBooking(b) {
  if (API_URL) {
    const res = await fetch(API_URL + "/api/bookings", {
      method: "POST",
      headers: { "Content-type": "application/json" },
      body: JSON.stringify(b)
    });
    if (res.status === 409) throw new Error("taken");
    if (!res.ok) throw new Error("failed");
    return res.json()
  }
  const busy = await getBusy(b.date);
  if (!isFree(busy, b.start, b.mins)) throw new Error("taken");
  const saved = Object.assign({}, b, { ref: makeRef() });
  saveBooking(saved);
  return saved;
}

// ----------- What the visitor has chosen so far ------------- //
const state = { service: null, day: null, time: null };

// ---------- Cancelling ----------
// A client cancels with the reference and the phone number they booked with.
let lastBooking = null;

// Minutes from now (Lagos) until a booking starts.
function minutesUntil(date, start) {
  const now = lagosNow();
  const days = (Date.parse(date + "T00:00:00Z") - Date.parse(now.date + "T00:00:00Z")) / 86400000;
  return days * 1440 + start - now.minutes;
}

// Two numbers match if their last 10 digits match, so 0803... and +234803... are the same number.
function samePhone(a, b) {
  const x = a.replace(/\D/g, "");
  const y = b.replace(/\D/g, "");
  return x.length >= 10 && y.length >= 10 && x.slice(-10) === y.slice(-10);
}

async function cancelBooking(ref, phone) {
  if (API_URL) {
    const res = await fetch(API_URL + "/api/bookings/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref, phone })
    });
    if (res.status === 404) throw new Error("not_found");
    if (res.status === 409) throw new Error("already");
    if (res.status === 422) throw new Error("too_late");
    if (!res.ok) throw new Error("failed");
    return;
  }
  const all = savedBookings();
  const found = all.find((b) => b.ref === ref && samePhone(b.phone, phone));
  if (!found) throw new Error("not_found");
  if (minutesUntil(found.date, found.start) < CANCEL_HOURS * 60) throw new Error("too_late");
  memory = all.filter((b) => b !== found);
  try { localStorage.setItem(STORE_KEY, JSON.stringify(memory)); } catch (e) { /* storage blocked: memory is used */ }
}

function setupCancel() {
  const msg = $("#cancel-msg");
  const say = (text, good) => {
    msg.textContent = text;
    msg.className = "cancel-msg " + (good ? "is-ok" : "error");
    msg.hidden = false;
    msg.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  $("#cancel-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ref = $("#cancel-ref").value.trim().toUpperCase();
    const phone = $("#cancel-phone").value.trim();
    if (!ref || !phone) { say("Enter your reference and the phone number you booked with.", false); return; }

    const button = $("#cancel-go");
    button.disabled = true;
    try {
      await cancelBooking(ref, phone);
      $("#cancel-form").reset();
      if (!$("#confirmation").hidden) {   // the receipt on screen is no longer true
        $("#confirmation").hidden = true;
        $("#book-grid").hidden = false;
      }
      lastBooking = null;
      renderTimes();                      // the time is free again
      say("Your booking " + ref + " is cancelled. We hope to see you another time.", true);
    } catch (err) {
      if (err.message === "not_found") say("We could not find a booking with that reference and phone number. Check both and try again.", false);
      else if (err.message === "already") say("That booking is already cancelled.", false);
      else if (err.message === "too_late") say("Bookings can be cancelled online up to " + CANCEL_HOURS + " hours before. Please call or WhatsApp the studio.", false);
      else say("The cancellation did not go through. Check your connection and try again.", false);
    } finally {
      button.disabled = false;
    }
  });

  // On the receipt: fill in the form for them, so cancelling is one more tap.
  $("#c-cancel").addEventListener("click", () => {
    if (lastBooking) {
      $("#cancel-ref").value = lastBooking.ref;
      $("#cancel-phone").value = lastBooking.phone;
    }
    msg.hidden = true;
    $("#cancel-box").open = true;
    $("#cancel").scrollIntoView({ behavior: "smooth", block: "center" });
  });
}


// ---------- Rendering ---------- //
function renderStatus() {
  const now = lagosNow();
  const el = $("#status");
  const today = HOURS[weekday(now.date)];
  if (today && now.minutes >= today[0] * 60 && now.minutes < today[1] * 60) {
    el.textContent = "Open now until " + fmtTime(today[1] * 60) + ".";
    el.classList.add("is-open");
    return;
  }
  for (let i = 0; i < 8; i++) {
    const d = addDays(now.date, i);
    const h =HOURS[weekday(d)];
    if (!h) continue;
    if (i === 0 && now.minutes >= h[1] * 60) continue;
    const when = i === 0 ? "today" : i === 1 ? "tomorrow" : FULL_DAYS[weekday(d)];
    el.textContent = "Closed now. Opens " + when + " at " + fmtTime(h[0] * 60) + ".";
    return;
  }
}

function renderHours() {
  $("#hours").innerHTML = [1, 2, 3, 4, 5, 6, 0].map((d) => {
    const h = HOURS[d];
    return "<tr><th scope=\"row\">" + FULL_DAYS[d] + "</th><td>" +
      (h ? fmtTime(h[0] * 60) + " to " + fmtTime(h[1] * 60) : "Closed") + "</td></tr>";
  }).join("");
}

function renderTickets() {
  $("#tickets").innerHTML = SERVICES.filter((s) => s.featured).map((s) => 
    '<li><a class="ticket" href="#book" data-service="' + s.id + '">' + 
    '<span class="t-main"><span class="t-name">' + s.name + '</span><span class="t-meta">' + fmtDuration(s.mins) + '</span></span>' +
    '<span class="t-stub"><span class="t-price">' + naira(s.price) + '</span><span class="t-go">Book this</span></span>' +
    '</a></li>'
  ).join("");
}

function renderServices() {
  $("#services").innerHTML = SERVICES.map((s) =>
    '<label class="svc"><input type="radio" name="service" value="' + s.id + '">' +
    '<span class="svc-main"><span class="svc-name">' + s.name + '</span><span class="svc-desc">' + s.desc + '</span></span>' +
    '<span class="svc-meta"><span>' + fmtDuration(s.mins) + '</span><strong>' + naira(s.price) + '</strong></span></label>'
  ).join("");
}

function renderDays() {
  const now = lagosNow();
  const shortest = Math.min.apply(null, SERVICES.map((s) => s.mins));
  const tiles = [];
  for (let i = 0; i < STUDIO.daysAhead; i++) {
    const d = addDays(now.date, i);
    const h = HOURS[weekday(d)];
    // A day is unavailable if the studio is closed, or if today is nearly over.
    let open = !!h;
    if (open && i === 0 && now.minutes + STUDIO.leadMinutes + shortest > h[1] * 60) open = false;
    tiles.push(
      '<label class="day' + (open ? "" : " is-closed") + '">' +
      '<input type="radio" name="day" value="' + d + '"' + (open ? "" : " disabled") + '>' +
      '<span class="d-name">' + (i === 0 ? "Today" : DAY_NAMES[weekday(d)]) + '</span>' +
      '<span class="d-num">' + Number(d.slice(8)) + '</span>' +
      '<span class="d-month">' + (open ? MONTHS[Number(d.slice(5, 7)) - 1] : "Closed") + '</span></label>'
    );
  }
  $("#days").innerHTML = tiles.join("");
}

let timesRun = 0; // lets a slow answer be ignored if the visitor has already changed their mind
async function renderTimes() {
  const box = $("#times");
  const hint = $("#times-hint");
  const run = ++timesRun;
  state.time = null;
  box.innerHTML = "";

  if (!state.service || !state.day) {
    hint.textContent = "Choose a service and a day to see free times.";
    hint.hidden = false;
    renderSummary();
    return;
  }

  hint.textContent = "Loading free times...";
  hint.hidden = false;
  let busy;
  try {
    busy = await getBusy(state.day);
  } catch (err) {
    if (run === timesRun) hint.textContent = "Could not load free times. Check your connection and try again.";
    return;
  }
  if (run !== timesRun) return;

  const svc = byId(state.service);
  const slots = slotsFor(state.day, svc.mins, busy);
  if (!slots.some((s) => s.ok)) {
    hint.textContent = "No free times on this day for " + svc.name.toLowerCase() + ". Try another day.";
    renderSummary();
    return;
  }
  hint.hidden = true;
  box.innerHTML = slots.map((s) =>
    '<label class="slot' + (s.ok ? "" : " is-taken") + '"><input type="radio" name="time" value="' + s.start + '"' +
    (s.ok ? "" : " disabled") + '><span>' + fmtTime(s.start) + '</span></label>'
  ).join("");
  renderSummary();
}

function setSummary(id, text) {
  const el = $(id);
  el.textContent = text || "Not chosen yet";
  el.classList.toggle("is-empty", !text);
}

function renderSummary() {
  const svc = state.service ? byId(state.service) : null;
  setSummary("#sum-service", svc ? svc.name + " (" + fmtDuration(svc.mins) + ")" : "");
  setSummary("#sum-day", state.day ? dayLabel(state.day) : "");
  setSummary("#sum-time", svc && state.time !== null ? fmtTime(state.time) + " to " + fmtTime(state.time + svc.mins) : "");
  $("#sum-total").textContent = naira(svc ? svc.price : 0);
}

// ---------- Errors ----------
function clearErrors() {
  ["form-error", "name-error", "phone-error"].forEach((id) => { $("#" + id).hidden = true; });
}

function showGeneral(message) {
  const el = $("#form-error");
  el.textContent = message;
  el.hidden = false;
}

// ---------- Choosing things ----------
function chooseService(id) {
  const radio = document.querySelector('input[name="service"][value="' + id + '"]');
  if (!radio) return;
  radio.checked = true;
  radio.dispatchEvent(new Event("change", { bubbles: true }));
}

function setupChoices() {
  $("#services").addEventListener("change", (e) => { state.service = e.target.value; clearErrors(); renderTimes(); });
  $("#days").addEventListener("change", (e) => { state.day = e.target.value; clearErrors(); renderTimes(); });
  $("#times").addEventListener("change", (e) => { state.time = Number(e.target.value); clearErrors(); renderSummary(); });
  $("#tickets").addEventListener("click", (e) => {
    const link = e.target.closest("[data-service]");
    if (link) chooseService(link.dataset.service);
  });
}

// ---------- Booking ----------
function readForm() {
  clearErrors();
  const svc = state.service ? byId(state.service) : null;
  const name = $("#name").value.trim();
  const phone = $("#phone").value.trim();
  const digits = phone.replace(/\D/g, "");
  const nameBad = !name;
  const phoneBad = digits.length < 10 || digits.length > 15;

  let general = "";
  let target = null;
  if (!svc) { general = "Choose a service first."; target = $("#services"); }
  else if (!state.day) { general = "Pick a day."; target = $("#days"); }
  else if (state.time === null) { general = "Pick a time."; target = $("#times-hint").hidden ? $("#times") : $("#times-hint"); }

  if (general) showGeneral(general);
  $("#name-error").hidden = !nameBad;
  $("#phone-error").hidden = !phoneBad;

  if (general) { target.scrollIntoView({ behavior: "smooth", block: "center" }); return null; }
  if (nameBad) { $("#name").focus(); return null; }
  if (phoneBad) { $("#phone").focus(); return null; }

  return {
    service: svc.id, date: state.day, start: state.time, mins: svc.mins, price: svc.price,
    name, phone, note: $("#note").value.trim()
  };
}

function showConfirmation(b) {
  const svc = byId(b.service);
  const when = dayLabel(b.date) + ", " + fmtTime(b.start) + " to " + fmtTime(b.start + b.mins);
  $("#c-name").textContent = b.name.split(" ")[0];
  $("#c-ref").textContent = b.ref;
  $("#c-service").textContent = svc.name;
  $("#c-when").textContent = when;
  $("#c-total").textContent = naira(b.price) + ", paid at the studio";
  const text = "Hello " + STUDIO.name + ", I'm " + b.name + ". I booked " + svc.name + " on " + when + ". Reference " + b.ref + ".";
  $("#c-wa").href = "https://wa.me/" + STUDIO.whatsapp + "?text=" + encodeURIComponent(text);
  $("#c-demo").hidden = !!API_URL;
  $("#book-grid").hidden = true;
  $("#confirmation").hidden = false;
  $("#confirmation").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#c-title").focus({ preventScroll: true });
}

function setupForm() {
  $("#booking-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const booking = readForm();
    if (!booking) return;

    const button = $("#confirm");
    button.disabled = true;
    button.textContent = "Booking...";
    try {
      const saved = await createBooking(booking);
      lastBooking = { ref: saved.ref, phone: booking.phone };
      showConfirmation(saved);
    } catch (err) {
      if (err.message === "taken") {
        showGeneral("That time was just taken. Pick another time.");
        await renderTimes();
      } else {
        showGeneral("The booking did not go through. Check your connection and try again.");
      }
    } finally {
      button.disabled = false;
      button.textContent = "Book appointment";
    }
  });

  $("#c-again").addEventListener("click", () => {
    $("#booking-form").reset();
    state.service = null;
    state.day = null;
    state.time = null;
    clearErrors();
    renderDays();
    renderTimes();
    $("#confirmation").hidden = true;
    $("#book-grid").hidden = false;
    $("#book").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

// ---------- Optional photos ----------
// A photo that has no file yet is removed, so the page never shows a broken image.
function setupOptionalPhotos() {
  // The hero photo is decorative. If it is missing, remove it and the plum background shows.
  const hero = document.querySelector(".hero-photo");
  if (hero) {
    const dropHero = () => hero.remove();
    if (hero.complete && hero.naturalWidth === 0) dropHero();
    else hero.addEventListener("error", dropHero);
  }
  document.querySelectorAll("figure[data-optional]").forEach((fig) => {
    const img = fig.querySelector("img");
    const drop = () => {
      const section = fig.closest(".gallery");
      fig.remove();
      if (section && !section.querySelector("figure")) section.remove();
    };
    if (img.complete && img.naturalWidth === 0) drop();
    else img.addEventListener("error", drop);
  });
}


// -------- Start -------- //
renderStatus();
renderHours();
renderTickets();
renderServices();
renderDays();
renderTimes();
renderSummary();
setupChoices();
setupForm();
setupOptionalPhotos();
setupCancel();