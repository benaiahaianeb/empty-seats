#!/usr/bin/env node
// Round-trip planner: ranks destinations by total time out and back, for one
// outbound day and one return day, from the same data the site shows.
//
//   node tools/trips.js --from STL --home BOS,PVD --out fri --back sun \
//        --month 10 --region we --exclude-states UT
//
// No dependencies. Reads data/<carrier>.json beside this file, or --data PATH|URL.
// Run with --help for every option. Rules match the site: legs need ~3x/week
// (0.4 flights/day) in the viewed month, routes with no schedule that month are
// skipped, connections take at least 45 minutes on the same day, a routing may
// fly at most 2.5x the straight-line distance, and only US airports connect.
// Times come from the schedule month; loads from the viewed month. A leg with no
// published arrival uses the site's block-time fit and is marked est.
"use strict";
const fs = require("fs");
const path = require("path");

const HELP = `Usage: node tools/trips.js --from STL --home BOS,PVD --out fri --back sun [options]

Required
  --from CODES        Departure airports, comma separated (STL or STL,CPS)
  --out DAY           Outbound day: mon tue wed thu fri sat sun
  --back DAY          Return day
Optional
  --home CODES        Where the return may land (default: the --from airports)
  --month N           Month whose loads and schedule to use, 1-12 (default: this month)
  --span latest|avg   Loads from the latest year of that month, or its 3-year average (default latest)
  --sched seasonal|newest
                      Schedule from the viewed month (default) or the newest month in the data
  --depart-after HH:MM  Earliest outbound departure (default 05:00)
  --arrive-by HH:MM     Latest arrival home, same day (default 23:59)
  --out-arrive-by HH:MM Latest arrival at the destination, same day (default 23:59)
  --back-depart-after HH:MM  Earliest return departure (default 05:00)
  --stops N           Most stops per direction, 0-2 (default 1)
  --mct MIN           Minimum connection in minutes (default 45)
  --to CODES          Only these destinations
  --states XX,YY      Only destinations in these US states
  --exclude-states XX Skip these states
  --region R          ne mw so sw we (US regions), ca, or other
  --exclude CODES     Skip these destinations
  --min-open N        Drop itineraries whose emptiest-leg minimum is below N open seats
  --detail CODE       Every itinerary to and from CODE, both directions
  --limit N           Rows in the ranked table (default all)
  --carrier XX        Dataset to read (default DL)
  --data PATH|URL     Dataset file or URL instead of data/<carrier>.json
  --json              Machine-readable output`;

// ---- args ----
const args = {};
{
  const a = process.argv.slice(2);
  for (let i = 0; i < a.length; i++) {
    if (!a[i].startsWith("--")) fail("Unexpected argument " + a[i]);
    const k = a[i].slice(2);
    if (k === "json" || k === "help") args[k] = true;
    else { if (a[i + 1] == null) fail("--" + k + " needs a value"); args[k] = a[++i]; }
  }
}
function fail(msg) { console.error(msg + "\n\n" + HELP); process.exit(1); }
if (args.help) { console.log(HELP); process.exit(0); }
const list = s => (s || "").split(",").map(x => x.trim().toUpperCase()).filter(Boolean);
const DOWS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DOW_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const dowOf = s => { const i = DOWS.indexOf(String(s || "").slice(0, 3).toLowerCase()); if (i < 0) fail("Bad day: " + s); return i; };
const clock = (s, def) => {
  if (s == null) return def;
  const m = /^(\d{1,2}):?(\d{2})$/.exec(s); if (!m) fail("Bad time: " + s + " (use HH:MM)");
  return +m[1] * 60 + +m[2];
};
const FROM = list(args.from), HOME = args.home ? list(args.home) : FROM;
if (!FROM.length || args.out == null || args.back == null) fail("--from, --out and --back are required");
const OUT_DOW = dowOf(args.out), BACK_DOW = dowOf(args.back);
const MONTH = args.month ? +args.month : new Date().getMonth() + 1;
if (!(MONTH >= 1 && MONTH <= 12)) fail("Bad month: " + args.month);
const SPAN = args.span || "latest", SCHED = args.sched || "seasonal";
const DEP_AFTER = clock(args["depart-after"], 300), ARR_BY = clock(args["arrive-by"], 1439);
const OUT_ARR_BY = clock(args["out-arrive-by"], 1439), BACK_DEP_AFTER = clock(args["back-depart-after"], 300);
const STOPS = args.stops != null ? +args.stops : 1;
const MCT = args.mct != null ? +args.mct : 45;
const MIN_OPEN = args["min-open"] != null ? +args["min-open"] : 0;
const MINF = 0.4, MAX_CIRCUITY = 2.5;
const BLK_BASE = 45.4, BLK_PER_MI = 0.1267; // same fit as template.html

// ---- data ----
async function load() {
  const src = args.data || path.join(__dirname, "..", "data", (args.carrier || "DL").toUpperCase() + ".json");
  if (/^https?:/.test(src)) {
    const r = await fetch(src);
    if (!r.ok) { console.error("Could not fetch " + src + ": HTTP " + r.status); process.exit(1); }
    return r.json();
  }
  return JSON.parse(fs.readFileSync(src, "utf8"));
}

load().then(run);

function run(DATA) {
  const A = DATA.airports, COORDS = DATA.coords || {}, DAYS = DATA.days || {};
  const TIMES = DATA.times || {}, ARRS = DATA.arrs || {}, ITIMES = DATA.intlTimes || {}, IARRS = DATA.intlArrs || {};
  const TZN = DATA.tzn || {}, DM = DATA.dm || [];
  const ISAMPLED = new Set((DATA.intlMeta || {}).sampled || []);
  const CODE_IDX = new Map(A.map((a, i) => [a[0], i]));
  for (const c of FROM.concat(HOME, list(args.to), args.detail ? [args.detail.toUpperCase()] : []))
    if (!CODE_IDX.has(c)) fail("Unknown airport " + c + " for " + DATA.carrier.name);
  const isUS = c => { const i = CODE_IDX.get(c); return i != null && A[i][2] === "US"; };
  const stateOf = c => { const m = /,\s*([A-Z]{2})$/.exec(A[CODE_IDX.get(c)][1]); return isUS(c) && m ? m[1] : null; };
  const US_REGION = {
    CT: "ne", ME: "ne", MA: "ne", NH: "ne", RI: "ne", VT: "ne", NJ: "ne", NY: "ne", PA: "ne",
    IL: "mw", IN: "mw", IA: "mw", KS: "mw", MI: "mw", MN: "mw", MO: "mw", NE: "mw", ND: "mw", OH: "mw", SD: "mw", WI: "mw",
    AL: "so", AR: "so", DC: "so", DE: "so", FL: "so", GA: "so", KY: "so", LA: "so", MD: "so", MS: "so", NC: "so", SC: "so", TN: "so", VA: "so", WV: "so",
    AZ: "sw", NM: "sw", OK: "sw", TX: "sw",
    AK: "we", CA: "we", CO: "we", HI: "we", ID: "we", MT: "we", NV: "we", OR: "we", UT: "we", WA: "we", WY: "we"
  };
  const areaOf = c => { const a = A[CODE_IDX.get(c)]; if (a[2] === "CA") return "ca"; if (a[2] !== "US") return "other"; return US_REGION[stateOf(c)] || "other"; };

  // months: loads from the viewed month, schedule from it or the newest
  const months = [...new Set(DATA.rows.map(r => r[2] * 100 + r[3]))].sort((a, b) => a - b);
  const years = months.filter(k => k % 100 === MONTH).map(k => Math.floor(k / 100));
  if (!years.length) fail("No " + MONTH_FULL[MONTH - 1] + " in the data window");
  const YEAR = Math.max(...years);
  const ym = (y, m) => y + "-" + String(m).padStart(2, "0");
  const dmi = DM.indexOf(ym(YEAR, MONTH));            // decides whether a route exists
  const sdi = SCHED === "newest" ? DM.length - 1 : dmi; // decides days and times
  const daysIn = (y, m) => new Date(y, m, 0).getDate();

  // per-route loads for the viewed month
  const agg = new Map();
  for (const r of DATA.rows) {
    if (r[3] !== MONTH || (SPAN !== "avg" && r[2] !== YEAR)) continue;
    const k = A[r[0]][0] + "-" + A[r[1]][0];
    const a = agg.get(k) || { dep: 0, seats: 0, pax: 0, days: new Set() };
    a.dep += r[4]; a.seats += r[5]; a.pax += r[6]; a.days.add(r[2]);
    agg.set(k, a);
  }
  const metric = k => {
    const a = agg.get(k);
    if (!a || a.dep < 1 || a.seats < 1) return null;
    const days = [...a.days].reduce((s, y) => s + daysIn(y, MONTH), 0);
    return { fpd: a.dep / days, lf: a.pax / a.seats * 100, open: (a.seats - a.pax) / a.dep };
  };

  function distMi(a, b) {
    const c1 = COORDS[a], c2 = COORDS[b]; if (!c1 || !c2) return null;
    const p = Math.PI / 180;
    const h = Math.sin((c2[0] - c1[0]) * p / 2) ** 2 + Math.cos(c1[0] * p) * Math.cos(c2[0] * p) * Math.sin((c2[1] - c1[1]) * p / 2) ** 2;
    return 2 * 3959 * Math.asin(Math.sqrt(h));
  }
  const offCache = new Map();
  function utcOff(code) {
    const z = TZN[code], mo = DM[sdi]; if (!z || !mo) return null;
    if (offCache.has(code)) return offCache.get(code);
    let v = null;
    try {
      const d = new Date(Date.UTC(+mo.slice(0, 4), +mo.slice(5, 7) - 1, 15, 18));
      const part = new Intl.DateTimeFormat("en-US", { timeZone: z, timeZoneName: "longOffset" }).formatToParts(d).find(x => x.type === "timeZoneName");
      const m = part && /GMT([+-])(\d{2}):(\d{2})/.exec(part.value);
      v = m ? (m[1] === "-" ? -1 : 1) * (+m[2] * 60 + +m[3]) : part && part.value === "GMT" ? 0 : null;
    } catch (e) { v = null; }
    offCache.set(code, v);
    return v;
  }
  const hm = t => Math.floor(t / 100) * 60 + t % 100;

  // a usable leg on a given weekday: enough service, still scheduled, flies that day
  const legCache = new Map();
  function leg(f, t, dow) {
    const k = f + "-" + t + "|" + dow;
    if (legCache.has(k)) return legCache.get(k);
    let out = null;
    const m = metric(f + "-" + t);
    if (m && m.fpd >= MINF && !dead(m.fpd, f, t) && flies(f, t, dow)) {
      const key = f + "-" + t;
      let dep = sdi >= 0 && TIMES[key] ? TIMES[key][sdi] : null, arr = sdi >= 0 && ARRS[key] ? ARRS[key][sdi] : null;
      if (!(dep && dep.length) && ITIMES[key] && ITIMES[key].length > 1) { dep = ITIMES[key].slice(1); arr = IARRS[key]; }
      if (dep && dep.length) {
        const of = utcOff(f), ot = utcOff(t);
        const est = !(arr && arr.length === dep.length);
        const blk = Math.round(BLK_BASE + BLK_PER_MI * (distMi(f, t) || 0));
        if (of != null && ot != null) {
          // each bank as absolute minutes in the origin's day, departure and arrival in UTC
          const banks = dep.map((d, i) => {
            const dl = hm(d), du = dl - of;
            let au;
            if (!est) { au = hm(arr[i]) - ot; while (au <= du) au += 1440; }
            else au = du + blk;
            return { dep: d, depLocal: dl, du, au };
          }).sort((a, b) => a.du - b.du);
          out = { f, t, m, banks, est };
        }
      }
    }
    legCache.set(k, out);
    return out;
  }
  function flies(f, t, dow) {
    const key = f + "-" + t;
    let mask = sdi >= 0 && DAYS[key] ? DAYS[key][sdi] : 0;
    if (!mask && ITIMES[key] && ITIMES[key].length > 1) mask = ITIMES[key][0];
    if (!mask || mask === 127) return true;
    return !!(mask >> dow & 1);
  }
  function dead(fpd, f, t) {
    const key = f + "-" + t;
    if (isUS(f) && isUS(t)) {
      if (fpd >= 0.9 || dmi < 0) return false;
      const mk = DAYS[key] ? DAYS[key][dmi] : 0, tl = TIMES[key] ? TIMES[key][dmi] : null;
      return !mk && !(tl && tl.length);
    }
    return ISAMPLED.has(f) && !(ITIMES[key] && ITIMES[key].length > 1);
  }
  function circuityOK(codes) {
    const dd = distMi(codes[0], codes[codes.length - 1]); if (dd == null || dd <= 0) return true;
    let s = 0;
    for (let i = 0; i + 1 < codes.length; i++) { const l = distMi(codes[i], codes[i + 1]); if (l == null) return true; s += l; }
    return s <= dd * MAX_CIRCUITY;
  }

  // routes out of / into each airport, from the viewed month's rows
  const outOf = new Map(), into = new Map();
  for (const k of agg.keys()) {
    const [f, t] = k.split("-");
    (outOf.get(f) || outOf.set(f, []).get(f)).push(t);
    (into.get(t) || into.set(t, []).get(t)).push(f);
  }

  // every routing from any of `froms` to any of `tos` on weekday dow, each with
  // its fastest same-day timing inside the window
  function itineraries(froms, tos, dow, depAfter, arrBy) {
    const res = [];
    const tset = new Set(tos), fset = new Set(froms);
    const via = c => isUS(c) && !fset.has(c) && !tset.has(c);
    const paths = [];
    for (const o of froms) for (const d of tos) {
      if (o === d) continue;
      paths.push([o, d]);
      if (STOPS >= 1) for (const x of outOf.get(o) || []) if (via(x) && (into.get(d) || []).includes(x)) paths.push([o, x, d]);
      if (STOPS >= 2) for (const x1 of outOf.get(o) || []) {
        if (!via(x1)) continue;
        for (const x2 of outOf.get(x1) || []) if (x2 !== x1 && via(x2) && (into.get(d) || []).includes(x2)) paths.push([o, x1, x2, d]);
      }
    }
    for (const p of paths) {
      if (!circuityOK(p)) continue;
      const legs = [];
      for (let i = 0; i + 1 < p.length; i++) { const l = leg(p[i], p[i + 1], dow); if (!l) { legs.length = 0; break; } legs.push(l); }
      if (!legs.length) continue;
      const oOff = utcOff(p[0]), dOff = utcOff(p[p.length - 1]);
      // all same-day timings, fastest first; the best one leads, the rest are backups
      const timings = [];
      for (const b0 of legs[0].banks) {
        if (b0.depLocal < depAfter) continue;
        const chosen = [b0];
        let cur = b0.au, ok = true;
        for (let L = 1; L < legs.length; L++) {
          // each later bank is in its own origin's local day; shift to the day it can be caught
          const hit = legs[L].banks.find(b => b.du >= cur + MCT && b.du < cur + MCT + 1440)
            || legs[L].banks.map(b => ({ ...b, du: b.du + 1440, au: b.au + 1440 })).find(b => b.du >= cur + MCT);
          if (!hit) { ok = false; break; }
          chosen.push(hit); cur = hit.au;
        }
        if (!ok) continue;
        const arrLocal = cur + dOff; // minutes after local midnight of the departure day, in destination time
        const startLocal = b0.du + oOff;
        // must land the same calendar day, by the cutoff
        if (Math.floor(arrLocal / 1440) !== Math.floor(startLocal / 1440) || (arrLocal % 1440) > arrBy) continue;
        timings.push({ dep: b0.dep, arr: ((arrLocal % 1440) + 1440) % 1440, elapsed: cur - b0.du,
          flights: chosen.map((b, i) => ({ from: legs[i].f, to: legs[i].t, dep: b.dep, arr: ((b.au + utcOff(legs[i].t)) % 1440 + 1440) % 1440, est: legs[i].est })) });
      }
      if (!timings.length) continue;
      timings.sort((a, b) => a.elapsed - b.elapsed);
      const minOpen = Math.min(...legs.map(l => l.m.open));
      if (minOpen < MIN_OPEN) continue;
      res.push({ route: p.join("-"), from: p[0], to: p[p.length - 1], stops: p.length - 2,
        best: timings[0], timings, minOpen, est: legs.some(l => l.est),
        legs: legs.map(l => ({ from: l.f, to: l.t, open: Math.round(l.m.open), loadPct: Math.round(l.m.lf), perDay: +l.m.fpd.toFixed(1) })) });
    }
    return res.sort((a, b) => a.best.elapsed - b.best.elapsed || b.minOpen - a.minOpen);
  }

  // candidate destinations
  const exclStates = new Set(list(args["exclude-states"])), states = new Set(list(args.states));
  const only = new Set(list(args.to)), excl = new Set(list(args.exclude).concat(FROM, HOME));
  const dests = A.map(a => a[0]).filter(c => {
    if (excl.has(c) || !outOf.has(c)) return false;
    if (only.size) return only.has(c);
    if (states.size && !states.has(stateOf(c))) return false;
    if (exclStates.has(stateOf(c))) return false;
    if (args.region && areaOf(c) !== args.region) return false;
    return true;
  });

  const fmt = t => String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0");
  const fmtB = t => String(Math.floor(t / 100)).padStart(2, "0") + ":" + String(t % 100).padStart(2, "0");
  const hrs = m => (m / 60).toFixed(1);
  const desc = it => it.route + " " + fmtB(it.best.dep) + "→" + fmt(it.best.arr) + (it.est ? " est." : "");
  const schedMonth = DM[sdi] ? MONTH_FULL[+DM[sdi].slice(5, 7) - 1] + " " + DM[sdi].slice(0, 4) : "none";
  const loadsFrom = SPAN === "avg" ? MONTH_FULL[MONTH - 1] + ", " + years.length + "-year average" : MONTH_FULL[MONTH - 1] + " " + YEAR;
  const meta = { carrier: DATA.carrier.name, from: FROM, home: HOME, out: DOW_FULL[OUT_DOW], back: DOW_FULL[BACK_DOW],
    schedule: schedMonth, loads: loadsFrom, departAfter: fmt(DEP_AFTER), arriveHomeBy: fmt(ARR_BY), maxStops: STOPS, connectMin: MCT,
    note: "Loads are historical averages from BTS, not live availability. Other non-revs are not counted. Times are local; est. marks a leg timed from the block-time fit." };

  if (args.detail) {
    const d = args.detail.toUpperCase();
    const out = itineraries(FROM, [d], OUT_DOW, DEP_AFTER, OUT_ARR_BY);
    const back = itineraries([d], HOME, BACK_DOW, BACK_DEP_AFTER, ARR_BY);
    if (args.json) { console.log(JSON.stringify({ ...meta, destination: d, outbound: out, return: back }, null, 1)); return; }
    header(d + " · " + A[CODE_IDX.get(d)][1]);
    for (const [name, its] of [["Out " + meta.out, out], ["Home " + meta.back, back]]) {
      console.log("\n" + name + ": " + its.length + " routing" + (its.length === 1 ? "" : "s"));
      for (const it of its) {
        console.log("  " + it.route + "  min " + Math.round(it.minOpen) + " open/leg  (" + it.legs.map(l => l.from + "-" + l.to + " " + l.open + " open, " + l.loadPct + "%, " + l.perDay + "/day").join("; ") + ")");
        for (const t of it.timings) console.log("    " + hrs(t.elapsed) + " hr  " + t.flights.map(f => f.from + " " + fmtB(f.dep) + "→" + f.to + " " + fmt(f.arr) + (f.est ? " est." : "")).join(", "));
      }
    }
    return;
  }

  const rows = [];
  for (const d of dests) {
    const out = itineraries(FROM, [d], OUT_DOW, DEP_AFTER, OUT_ARR_BY);
    if (!out.length) continue;
    const back = itineraries([d], HOME, BACK_DOW, BACK_DEP_AFTER, ARR_BY);
    if (!back.length) continue;
    rows.push({ code: d, city: A[CODE_IDX.get(d)][1], totalHr: +hrs(out[0].best.elapsed + back[0].best.elapsed),
      out: out[0], back: back[0], outRoutings: out.length, backRoutings: back.length });
  }
  rows.sort((a, b) => a.totalHr - b.totalHr);
  const shown = args.limit ? rows.slice(0, +args.limit) : rows;
  if (args.json) {
    const slim = it => ({ route: it.route, stops: it.stops, ...it.best, elapsedHr: +hrs(it.best.elapsed), dep: fmtB(it.best.dep), arr: fmt(it.best.arr),
      flights: it.best.flights.map(f => ({ ...f, dep: fmtB(f.dep), arr: fmt(f.arr) })), minOpen: Math.round(it.minOpen), legs: it.legs, est: it.est });
    console.log(JSON.stringify({ ...meta, destinations: shown.map(r => ({ ...r, out: slim(r.out), back: slim(r.back) })) }, null, 1));
    return;
  }
  header(rows.length + " destinations, fastest round trip first");
  console.log("\n| Destination | Out " + meta.out + " | Home " + meta.back + " | Total hr | Min open/leg out, back | Routings out, back |");
  console.log("| --- | --- | --- | --- | --- | --- |");
  for (const r of shown)
    console.log("| " + r.code + " " + r.city + " | " + desc(r.out) + " (" + hrs(r.out.best.elapsed) + ") | " + desc(r.back) + " (" + hrs(r.back.best.elapsed) + ") | "
      + r.totalHr.toFixed(1) + " | " + Math.round(r.out.minOpen) + ", " + Math.round(r.back.minOpen) + " | " + r.outRoutings + ", " + r.backRoutings + " |");

  function header(title) {
    console.log(meta.carrier + " · " + FROM.join("/") + " out " + meta.out + " after " + meta.departAfter + ", home to " + HOME.join("/") + " " + meta.back + " by " + meta.arriveHomeBy);
    console.log("Schedule from " + schedMonth + " · loads from " + loadsFrom + " · up to " + STOPS + (STOPS === 1 ? " stop" : " stops") + " each way · " + MCT + "-minute connections");
    console.log(meta.note);
    console.log("\n" + title);
  }
}
