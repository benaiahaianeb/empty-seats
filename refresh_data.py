#!/usr/bin/env python3
"""
Empty Seats data refresh.

Downloads the latest BTS T-100 Segment data (every US mainline airline plus
the regional flying attributed to it by marketed share), writes one dataset
per airline to data/<code>.json, and embeds Delta's into index.html.

Usage:
    python refresh_data.py                  # refresh T-100 loads (typical monthly run)
    python refresh_data.py --refresh-skywest  # also rebuild the SkyWest->Delta
                                              # attribution table (heavier; run 1-2x/year)

Data window: rolling 36 months ending at the latest reported month.
BTS reports with a ~3 month lag.

Dependencies: pandas, requests
"""
import argparse
import datetime as dt
import io
import re
import sys
import urllib.parse
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent
T100_URL = ("https://www.transtats.bts.gov/DL_SelectFields.aspx"
            "?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4+Pn44vr45")
OTM_URL = ("https://transtats.bts.gov/PREZIP/"
           "On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{y}_{m}.zip")
UA = {"User-Agent": "Mozilla/5.0 (empty-seats refresh script)"}
T100_FIELDS = ["UNIQUE_CARRIER", "ORIGIN", "ORIGIN_CITY_NAME", "ORIGIN_COUNTRY",
               "DEST", "DEST_CITY_NAME", "DEST_COUNTRY",
               "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "YEAR", "MONTH", "CLASS"]
SHARE_CSV = ROOT / "data" / "op_share.csv"      # op,o,d,mkt,share
CARRIERS = {"DL": "Delta", "UA": "United", "AA": "American", "WN": "Southwest",
            "B6": "JetBlue", "AS": "Alaska", "NK": "Spirit", "F9": "Frontier",
            "G4": "Allegiant", "HA": "Hawaiian"}
DATA_DIR = ROOT / "data"
DAYS_CSV = ROOT / "data" / "route_days.csv"
INTL_CSV = ROOT / "data" / "intl_times.csv"
COORDS_CSV = ROOT / "data" / "airport_coords.csv"
IANA_CSV = ROOT / "data" / "airport_iana.csv"
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "index.html"
PLACEHOLDER = "/*__DATA__*/null"


def hidden(html: str, name: str) -> str:
    m = (re.search(r'id="%s" value="([^"]*)"' % name, html)
         or re.search(r'name="%s"[^>]*value="([^"]*)"' % name, html))
    return m.group(1) if m else ""


def download_t100_year(session: requests.Session, year: int) -> pd.DataFrame | None:
    """Download one year of T-100 Segment (All Carriers) via the TranStats form."""
    page = session.get(T100_URL, headers=UA, timeout=120).text
    data = [("__EVENTTARGET", ""), ("__EVENTARGUMENT", ""),
            ("__VIEWSTATE", hidden(page, "__VIEWSTATE")),
            ("__VIEWSTATEGENERATOR", hidden(page, "__VIEWSTATEGENERATOR")),
            ("__EVENTVALIDATION", hidden(page, "__EVENTVALIDATION")),
            ("txtSearch", ""), ("cboGeography", "All"), ("cboYear", str(year)),
            ("cboPeriod", "All"), ("btnDownload", "Download"), ("chkDownloadZip", "on")]
    data += [(f, "on") for f in T100_FIELDS]
    r = session.post(T100_URL, headers={**UA, "Referer": T100_URL,
                                        "Content-Type": "application/x-www-form-urlencoded"},
                     data=urllib.parse.urlencode(data), timeout=900)
    if "zip" not in r.headers.get("Content-Type", ""):
        print(f"  {year}: no zip returned (probably no data yet)")
        return None
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv")
                and "documentation" not in n.lower()][0]
    df = pd.read_csv(zf.open(csv_name))
    print(f"  {year}: {len(df):,} rows")
    return df


def rebuild_op_share(session: requests.Session, latest_year: int) -> None:
    """Rebuild data/op_share.csv from recent Marketing-Carrier on-time months:
    for each operating carrier and route, the share of its flights marketed by
    each mainline. T-100 reports an operator's segment without the marketer, so
    this is what lets a SkyWest or Republic segment be split between the
    airlines that sold it. Samples a recent January and July for seasonal cover.
    """
    samples = []
    for (y, m) in [(latest_year, 1), (latest_year - 1, 7), (latest_year - 1, 1)]:
        url = OTM_URL.format(y=y, m=m)
        print(f"  trying on-time {y}-{m:02d} ...")
        r = session.get(url, headers=UA, timeout=900)
        if r.status_code != 200 or "zip" not in r.headers.get("Content-Type", ""):
            print("    unavailable, skipping")
            continue
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
        df = pd.read_csv(zf.open(csv_name),
                         usecols=["Marketing_Airline_Network", "Operating_Airline ",
                                  "Origin", "Dest"], dtype=str)
        df.columns = ["mkt", "op", "o", "d"]
        df = df[df.mkt.isin(CARRIERS)]
        samples.append(df.groupby(["op", "o", "d", "mkt"]).size().rename("n"))
        print(f"    {len(df):,} flights")
        if len(samples) >= 2:
            break
    if not samples:
        print("  WARNING: no on-time data retrieved; keeping existing share table")
        return
    n = pd.concat(samples).groupby(level=[0, 1, 2, 3]).sum()
    tot = n.groupby(level=[0, 1, 2]).transform("sum")
    out = (n / tot).rename("share").reset_index()
    out.to_csv(SHARE_CSV, index=False)
    print(f"  wrote {SHARE_CSV} ({len(out)} operator-route-marketer rows)")


def _time_banks(minutes: list[int]) -> list[int]:
    """Cluster a month of scheduled departure minutes into 'normal' daily banks.

    Times within 40 minutes chain into one cluster; clusters with fewer than
    40% of the busiest cluster's flights (extra sections, one-offs) are
    dropped. Returns HHMM ints, one per bank, rounded to 5 minutes.
    """
    ts = sorted(minutes)
    clusters, cur = [], [ts[0]]
    for t in ts[1:]:
        if t - cur[-1] > 40:
            clusters.append(cur)
            cur = [t]
        else:
            cur.append(t)
    clusters.append(cur)
    maxc = max(len(c) for c in clusters)
    out = []
    for c in clusters:
        if len(c) < max(1, 0.4 * maxc):
            continue
        med = c[len(c) // 2]
        med = int(round(med / 5.0) * 5) % 1440
        out.append(med // 60 * 100 + med % 60)
    return sorted(set(out))


def _dep_arr_banks(pairs: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """Cluster (departure, arrival) minute pairs into normal daily banks.

    Clusters on departure exactly like _time_banks; each surviving bank also
    reports the median scheduled arrival of the flights in it, so departure
    and arrival lists stay index-aligned. Both are local clock times.
    """
    ps = sorted(pairs)
    clusters, cur = [], [ps[0]]
    for x in ps[1:]:
        if x[0] - cur[-1][0] > 40:
            clusters.append(cur)
            cur = [x]
        else:
            cur.append(x)
    clusters.append(cur)
    maxc = max(len(c) for c in clusters)
    deps, arrs = [], []
    for c in clusters:
        if len(c) < max(1, 0.4 * maxc):
            continue
        d = int(round(c[len(c) // 2][0] / 5.0) * 5) % 1440
        a = sorted(x[1] for x in c)[len(c) // 2]
        a = int(round(a / 5.0) * 5) % 1440
        deps.append(d // 60 * 100 + d % 60)
        arrs.append(a // 60 * 100 + a % 60)
    order = sorted(range(len(deps)), key=lambda i: deps[i])
    return [deps[i] for i in order], [arrs[i] for i in order]


def rebuild_route_days(session, months: list[tuple[int, int]],
                       ops_by_mkt: dict[str, set[str]],
                       limit: int | None = None) -> None:
    """Incrementally build data/route_days.csv: per-route, per-month day-of-week
    operating masks and normal scheduled departure banks for DL-marketed flights,
    from Marketing-Carrier on-time data (domestic routes only). mask bit 0 =
    Monday; a weekday counts only if the route flew on at least half of that
    weekday's dates in the month, so one-off extra sections don't hide a clean
    pattern. times is a space-separated list of HHMM departure banks (local).

    Months already present in the CSV are kept, months outside the window are
    dropped, and only missing months are downloaded (typically one per run).
    """
    cols = ["mkt", "o", "d", "ym", "mask", "times", "arrs"]
    have = pd.DataFrame(columns=cols)
    if DAYS_CSV.exists():
        old = pd.read_csv(DAYS_CSV)
        if all(c in old.columns for c in cols):
            have = old[cols]
    want = {f"{y}-{m:02d}" for y, m in months}
    have = have[have.ym.isin(want)]
    missing = [(y, m) for y, m in months
               if f"{y}-{m:02d}" not in set(have.ym)]
    frames, done = [have], 0
    for y, m in missing:
        if limit is not None and done >= limit:
            break
        print(f"  on-time {y}-{m:02d} ...", flush=True)
        try:
            r = session.get(OTM_URL.format(y=y, m=m), headers=UA, timeout=900)
        except requests.RequestException as e:
            print(f"    error: {e}")
            continue
        if r.status_code != 200 or "zip" not in r.headers.get("Content-Type", ""):
            print("    unavailable")
            continue
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
        df = pd.read_csv(zf.open(csv_name),
                         usecols=["FlightDate", "Marketing_Airline_Network",
                                  "Operating_Airline ", "Origin", "Dest",
                                  "CRSDepTime", "CRSArrTime"], dtype=str)
        import calendar
        occ = [0] * 7
        for dnum in range(1, calendar.monthrange(y, m)[1] + 1):
            occ[calendar.weekday(y, m, dnum)] += 1
        adds = []
        for mkt, ops in ops_by_mkt.items():
            # match the T-100 load coverage: marketed by this airline AND flown
            # by it or a regional the share table attributes to it
            sub = df[(df.Marketing_Airline_Network == mkt)
                     & (df["Operating_Airline "].isin(ops))]
            if not len(sub):
                continue
            t = pd.to_numeric(sub.CRSDepTime, errors="coerce")
            ta = pd.to_numeric(sub.CRSArrTime, errors="coerce")
            dts = pd.to_datetime(sub.FlightDate)
            sub = sub.assign(dow=dts.dt.dayofweek, day=dts.dt.day,
                             mins=(t // 100) % 24 * 60 + t % 100,
                             amins=(ta // 100) % 24 * 60 + ta % 100)
            srv = sub.groupby(["Origin", "Dest", "dow"]).day.nunique().reset_index()
            srv = srv[[c >= 0.5 * occ[w] for c, w in zip(srv.day, srv.dow)]]
            masks = srv.groupby(["Origin", "Dest"]).dow.apply(
                lambda s_: sum(1 << int(w) for w in set(s_))).rename("mask")
            tdf = sub[sub.mins.notna() & sub.amins.notna()].copy()
            tdf["mins"] = tdf.mins.astype(int)
            tdf["amins"] = tdf.amins.astype(int)
            banks = tdf.groupby(["Origin", "Dest"]).apply(
                lambda g_: _dep_arr_banks(list(zip(g_.mins, g_.amins))))
            times = banks.apply(lambda b: " ".join(str(x) for x in b[0])).rename("times")
            arrs = banks.apply(lambda b: " ".join(str(x) for x in b[1])).rename("arrs")
            add = pd.concat([masks, times, arrs], axis=1).reset_index()
            add.columns = ["o", "d", "mask", "times", "arrs"]
            add["mask"] = add["mask"].fillna(0).astype(int)
            add["times"] = add.times.fillna("")
            add["arrs"] = add.arrs.fillna("")
            add["ym"] = f"{y}-{m:02d}"
            add["mkt"] = mkt
            adds.append(add[cols])
        add = pd.concat(adds, ignore_index=True) if adds else pd.DataFrame(columns=cols)
        frames.append(add[cols])
        done += 1
        print(f"    {len(add)} routes")
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["mkt", "ym", "o", "d"])
    out.to_csv(DAYS_CSV, index=False)
    left = len(missing) - done
    print(f"  route_days.csv: {len(out)} rows, "
          f"{out.ym.nunique()}/{len(months)} window months"
          + (f", {left} still missing" if left else ""))


def rebuild_intl_times(nets: dict, ap_lookup: dict) -> None:
    """Snapshot international schedules from AeroDataBox airport boards.

    DOT on-time data covers domestic flights only, so international times come
    from the boards at each airline's busiest international gateways (API key
    in AERODATABOX_KEY; RapidAPI or API.market). One call per gateway per
    twelve-hour window returns both boards with both ends of every flight, for
    every airline: the departures board gives gateway-to-abroad legs with their
    arrival times, the arrivals board gives abroad-to-gateway legs with their
    departure times. Times barely move within a season and rows carry forward,
    so a normal month samples two days plus two far probes for opposite-season
    routes; every third month samples a full week for day-of-week masks.
    Skips gracefully without a key.
    """
    import os
    key = os.environ.get("AERODATABOX_KEY", "").strip()
    if not key:
        print("  AERODATABOX_KEY not set; keeping existing intl_times.csv")
        return
    intl = {c for c, (_, ctry) in ap_lookup.items() if ctry != "US"}
    us = {c for c, (_, ctry) in ap_lookup.items() if ctry == "US"}
    gateways: set[str] = set()
    for g in nets.values():
        gi = g[g.ORIGIN.isin(us) & g.DEST.isin(intl)]
        if len(gi):
            gateways |= set(gi.groupby("ORIGIN").seats.sum()
                            .sort_values(ascending=False).head(10).index)
    if not gateways:
        print("  no international routes found")
        return
    hosts = [("aerodatabox.p.rapidapi.com",
              {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "aerodatabox.p.rapidapi.com"}),
             ("prod.api.market/api/v1/aedbx/aerodatabox",
              {"x-api-market-key": key})]
    today = dt.date.today()
    full = today.month % 3 == 0
    block = [today + dt.timedelta(days=k) for k in range(1, 8 if full else 3)]
    probes = [today + dt.timedelta(days=k) for k in (120, 180)]
    windows = [("00:00", "11:59"), ("12:00", "23:59")]

    def leg(f, side):  # (airport iata, local time string) for one end of a flight
        m = f.get(side) or {}
        return ((m.get("airport") or {}).get("iata"),
                (m.get("scheduledTime") or {}).get("local", ""))

    mins: dict[tuple, list[int]] = {}
    amins: dict[tuple, list[int]] = {}
    dows: dict[tuple, set[int]] = {}
    hi, ok_calls = 0, 0
    for gw in sorted(gateways):
        for day in block + probes:
            for t1, t2 in windows:
                data = None
                while hi < len(hosts):
                    host, hdrs = hosts[hi]
                    url = (f"https://{host}/flights/airports/iata/{gw}/"
                           f"{day}T{t1}/{day}T{t2}"
                           "?direction=Both&withLeg=true&withCodeshared=false"
                           "&withCargo=false&withPrivate=false")
                    try:
                        r = requests.get(url, headers=hdrs, timeout=60)
                    except requests.RequestException as e:
                        print(f"    {gw} {day}: {e}")
                        break
                    if r.status_code in (401, 403) and ok_calls == 0:
                        print(f"    host {host} rejected key; trying next")
                        hi += 1
                        continue
                    if r.status_code != 200:
                        print(f"    {gw} {day} {t1}: HTTP {r.status_code}")
                        break
                    data = r.json()
                    break
                if data is None:
                    continue
                ok_calls += 1
                for board in ("departures", "arrivals"):
                    for f in data.get(board, []):
                        num = (f.get("number") or "").replace(" ", "")
                        m = re.match(r"^([A-Z0-9]{2})\d", num)
                        if not m or m.group(1) not in CARRIERS:
                            continue
                        mkt = m.group(1)
                        # with withLeg both ends are present; without it only
                        # "movement" (the far end) is
                        if "departure" in f or "arrival" in f:
                            o, dt_ = leg(f, "departure")
                            d, at_ = leg(f, "arrival")
                            if board == "departures" and not o: o = gw
                            if board == "arrivals" and not d: d = gw
                        else:
                            far, ft = leg(f, "movement")
                            o, d = (gw, far) if board == "departures" else (far, gw)
                            dt_, at_ = (ft, "") if board == "departures" else ("", ft)
                        if not o or not d or (o not in intl and d not in intl):
                            continue
                        md = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", dt_)
                        if not md:
                            continue
                        ma = re.search(r"[ T](\d{2}):(\d{2})", at_)
                        k = (mkt, o, d)
                        mins.setdefault(k, []).append(int(md.group(4)) * 60 + int(md.group(5)))
                        amins.setdefault(k, []).append(
                            int(ma.group(1)) * 60 + int(ma.group(2)) if ma else -1)
                        if full and day not in probes:
                            dows.setdefault(k, set()).add(
                                dt.date(int(md.group(1)), int(md.group(2)),
                                        int(md.group(3))).weekday())
    if not mins:
        print(f"  no international times retrieved ({ok_calls} calls); "
              "keeping existing intl_times.csv")
        return
    cols = ["mkt", "o", "d", "mask", "times", "arrs", "asof"]
    old = pd.DataFrame(columns=cols)
    if INTL_CSV.exists():
        prev = pd.read_csv(INTL_CSV)
        if "mkt" not in prev.columns: prev["mkt"] = "DL"     # pre-fork file
        if "mask" not in prev.columns: prev["mask"] = 0
        if "arrs" not in prev.columns: prev["arrs"] = ""
        old = prev[cols]
    sampled = set(mins)
    keep = old[[(m_, o, d) not in sampled for m_, o, d in zip(old.mkt, old.o, old.d)]]
    rows = []
    for k in sorted(sampled):
        pairs = [(dm_, am_) for dm_, am_ in zip(mins[k], amins[k]) if am_ >= 0]
        if len(pairs) == len(mins[k]):
            deps, arrs = _dep_arr_banks(pairs)
        else:  # some arrivals unknown: bank departures alone, no arrivals
            deps, arrs = _time_banks(mins[k]), []
        mask = sum(1 << w for w in dows.get(k, set()))
        prevmask = old[(old.mkt == k[0]) & (old.o == k[1]) & (old.d == k[2])]["mask"]
        if not full and len(prevmask):   # a light month keeps last quarter's mask
            mask = int(prevmask.iloc[0])
        rows.append((k[0], k[1], k[2], mask,
                     " ".join(str(x) for x in deps), " ".join(str(x) for x in arrs),
                     str(today)))
    out = pd.concat([keep, pd.DataFrame(rows, columns=cols)],
                    ignore_index=True).sort_values(["mkt", "o", "d"])
    out.to_csv(INTL_CSV, index=False)
    print(f"  wrote {INTL_CSV} ({len(rows)} sampled + {len(keep)} carried, "
          f"{len(gateways)} gateways, {'full week' if full else 'light'}, "
          f"{ok_calls} API calls)")


# Airports whose IATA code postdates the OpenFlights dump (it stopped being
# updated in 2017). Checked by hand against the airport's published local time.
IANA_EXTRA = {
    "BER": "Europe/Berlin",     # Brandenburg, opened 2020
    "DSS": "Africa/Dakar",      # Blaise Diagne, opened 2017
    "TQO": "America/Cancun",    # Tulum, opened 2023
    "XWA": "America/Chicago",   # Williston Basin, opened 2019
}


def rebuild_iana(session, codes: set[str]) -> None:
    """Refresh data/airport_iana.csv (IANA time zone per airport) from the
    OpenFlights dump; keeps the existing file on failure.

    airport_tz.csv derives a zone *label* for domestic airports out of BTS block
    times, which is all the schedule column needs. Estimating an arrival time
    needs a real UTC offset for a given date, including the destination's own DST
    rules, and no foreign airport gets a label from that derivation at all. The
    IANA name gives the browser both, for every airport. Where the two sources
    overlap they agree on all 234 domestic airports, so this is a widening of the
    derived table rather than a replacement for it.
    """
    url = ("https://raw.githubusercontent.com/jpatokal/openflights/master"
           "/data/airports.dat")
    try:
        r = session.get(url, headers=UA, timeout=300)
        df = pd.read_csv(io.BytesIO(r.content), header=None,
                         names=["id", "name", "city", "country", "iata", "icao",
                                "lat", "lon", "alt", "utc", "dst", "tz",
                                "type", "source"],
                         usecols=["iata", "tz"])
    except Exception as e:
        print(f"  IANA zone refresh failed ({e}); keeping existing file")
        return
    found = {str(c): str(z) for c, z in zip(df.iata, df.tz)
             if isinstance(c, str) and len(str(c)) == 3
             and isinstance(z, str) and "/" in str(z)}
    found.update(IANA_EXTRA)
    rows = sorted((c, found[c]) for c in codes if c in found)
    if not rows:
        print("  IANA zone refresh returned nothing; keeping existing file")
        return
    pd.DataFrame(rows, columns=["code", "tz"]).to_csv(IANA_CSV, index=False)
    missing = sorted(codes - {c for c, _ in rows})
    print(f"  wrote {IANA_CSV} ({len(rows)}/{len(codes)} airports)"
          + (f"; no zone for {', '.join(missing[:8])}" if missing else ""))


def rebuild_coords(session, codes: set[str]) -> None:
    """Refresh data/airport_coords.csv (lat/lon per airport in the dataset)
    from the public OurAirports dump; keeps the existing file on failure."""
    url = "https://davidmegginson.github.io/ourairports-data/airports.csv"
    try:
        r = session.get(url, headers=UA, timeout=300)
        df = pd.read_csv(io.BytesIO(r.content),
                         usecols=["iata_code", "type", "scheduled_service",
                                  "latitude_deg", "longitude_deg"])
    except Exception as e:
        print(f"  coords refresh failed ({e}); keeping existing file")
        return
    df = df[df.iata_code.isin(codes)
            & df.type.isin(["large_airport", "medium_airport", "small_airport"])]
    df["pref"] = ((df.scheduled_service == "yes").astype(int) * 3
                  + df.type.map({"large_airport": 2, "medium_airport": 1}).fillna(0))
    df = df.sort_values("pref", ascending=False).drop_duplicates("iata_code")
    out = df[["iata_code", "latitude_deg", "longitude_deg"]].copy()
    out.columns = ["code", "lat", "lon"]
    out["lat"] = out.lat.round(3)
    out["lon"] = out.lon.round(3)
    out.sort_values("code").to_csv(COORDS_CSV, index=False)
    print(f"  wrote {COORDS_CSV} ({len(out)}/{len(codes)} airports)")


def rebuild_only() -> int:
    """Re-render index.html from template.html, reusing the embedded payload.

    For code-only changes: no downloads, no BTS parsing, no API quota spent.
    The payload is lifted straight out of the committed index.html, so the data
    window is preserved exactly as the last data refresh left it.
    """
    if not OUTPUT.exists():
        print("ERROR: index.html missing, nothing to reuse"); return 1
    built = OUTPUT.read_text()
    m = re.search(r"^const DATA = (.*);$", built, re.M)
    if not m:
        print("ERROR: no data payload found in index.html"); return 1
    payload = m.group(1)
    template = TEMPLATE.read_text()
    if PLACEHOLDER not in template:
        print("ERROR: template.html missing data placeholder"); return 1
    out = template.replace(PLACEHOLDER, payload)
    if PLACEHOLDER in out or not out.rstrip().endswith("</html>"):
        print("ERROR: rebuilt index.html looks malformed"); return 1
    OUTPUT.write_text(out)
    print(f"Rebuilt {OUTPUT} ({OUTPUT.stat().st_size/1e6:.2f} MB) "
          f"from template.html; data payload unchanged ({len(payload)/1e6:.2f} MB)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-share", "--refresh-skywest", action="store_true",
                    help="also rebuild the operator->marketer attribution table")
    ap.add_argument("--rebuild-only", action="store_true",
                    help="re-render index.html from template.html, reusing the data "
                         "already embedded in index.html; downloads nothing")
    args = ap.parse_args()

    if args.rebuild_only:
        return rebuild_only()

    session = requests.Session()
    today = dt.date.today()

    print("Downloading T-100 years ...")
    years, frames = [today.year - 3, today.year - 2,
                     today.year - 1, today.year], []
    for y in years:
        df = download_t100_year(session, y)
        if df is not None:
            frames.append(df)
    if not frames:
        print("ERROR: no T-100 data downloaded"); return 1
    df = pd.concat(frames)

    df = df[(df.CLASS == "F") & (df.DEPARTURES_PERFORMED > 0) & (df.SEATS > 0)]
    latest_year = int(df.YEAR.max())
    latest_month = int(df[df.YEAR == latest_year].MONTH.max())
    print(f"Latest reported month: {latest_year}-{latest_month:02d}")

    # rolling window: 36 months ending at the latest reported month
    lo_key = latest_year * 12 + latest_month - 35
    df = df[(df.YEAR * 12 + df.MONTH) >= lo_key]

    if args.refresh_share or not SHARE_CSV.exists():
        print("Rebuilding operator attribution ...")
        rebuild_op_share(session, latest_year)
    share = pd.read_csv(SHARE_CSV)
    ops_by_mkt = {c: set(share[share.mkt == c].op) | {c} for c in CARRIERS}

    # each airline's network: its own metal at full weight plus every regional
    # segment weighted by the share of it that airline marketed
    nets: dict[str, pd.DataFrame] = {}
    for c in CARRIERS:
        own = df[df.UNIQUE_CARRIER == c].copy()
        own["w"] = 1.0
        sh = share[(share.mkt == c) & (share.op != c)]
        reg = df[df.UNIQUE_CARRIER.isin(set(sh.op))].merge(
            sh[["op", "o", "d", "share"]].rename(columns={"share": "w"}),
            left_on=["UNIQUE_CARRIER", "ORIGIN", "DEST"], right_on=["op", "o", "d"])
        reg = reg[reg.w >= 0.05].drop(columns=["op", "o", "d"])
        sub = pd.concat([own, reg])
        if not len(sub):
            continue
        for col in ["DEPARTURES_PERFORMED", "SEATS", "PASSENGERS"]:
            sub[col] = sub[col] * sub.w
        g = sub.groupby(["ORIGIN", "ORIGIN_CITY_NAME", "ORIGIN_COUNTRY",
                         "DEST", "DEST_CITY_NAME", "DEST_COUNTRY", "YEAR", "MONTH"],
                        as_index=False).agg(dep=("DEPARTURES_PERFORMED", "sum"),
                                            seats=("SEATS", "sum"),
                                            pax=("PASSENGERS", "sum"))
        g = g[g.dep >= 0.5]
        if len(g):
            nets[c] = g
        print(f"  {c}: {len(g):,} route-month rows")
    if "DL" not in nets:
        print("ERROR: no Delta rows"); return 1

    ap_lookup: dict[str, tuple[str, str]] = {}
    for g in nets.values():
        for _, r in g.iterrows():
            ap_lookup.setdefault(r.ORIGIN, (r.ORIGIN_CITY_NAME, r.ORIGIN_COUNTRY))
            ap_lookup.setdefault(r.DEST, (r.DEST_CITY_NAME, r.DEST_COUNTRY))
    all_codes = set(ap_lookup)
    us_codes = {c_ for c_, (_, ctry) in ap_lookup.items() if ctry == "US"}

    window_months = sorted({(int(y), int(mo)) for g in nets.values()
                            for y, mo in zip(g.YEAR, g.MONTH)})
    print("Rebuilding operating-days table ...")
    rebuild_route_days(session, window_months, ops_by_mkt)

    print("Refreshing airport coordinates ...")
    rebuild_coords(session, all_codes)

    print("Refreshing IANA time zones ...")
    try:
        rebuild_iana(session, all_codes)
    except Exception as e:
        print(f"  IANA zone refresh failed: {e}")

    print("Refreshing international departure times ...")
    try:
        rebuild_intl_times(nets, ap_lookup)
    except Exception as e:  # never let intl times break the build
        print(f"  intl times failed: {e}")

    dm = [f"{y}-{mo:02d}" for y, mo in window_months]
    dmi = {s_: i for i, s_ in enumerate(dm)}
    dd = pd.read_csv(DAYS_CSV) if DAYS_CSV.exists() else pd.DataFrame(
        columns=["mkt", "o", "d", "ym", "mask", "times", "arrs"])
    ii = pd.read_csv(IANA_CSV) if IANA_CSV.exists() else pd.DataFrame(columns=["code", "tz"])
    cc = pd.read_csv(COORDS_CSV) if COORDS_CSV.exists() else pd.DataFrame(columns=["code", "lat", "lon"])
    it = pd.read_csv(INTL_CSV) if INTL_CSV.exists() else pd.DataFrame(
        columns=["mkt", "o", "d", "mask", "times", "arrs", "asof"])
    if "mkt" not in it.columns: it["mkt"] = "DL"
    if "arrs" not in it.columns: it["arrs"] = ""

    import json
    written = []
    for c, g in nets.items():
        codes = sorted(set(g.ORIGIN) | set(g.DEST))
        idx = {code: i for i, code in enumerate(codes)}
        airports = [[code, ap_lookup[code][0], ap_lookup[code][1]] for code in codes]
        rows = [[idx[r.ORIGIN], idx[r.DEST], int(r.YEAR), int(r.MONTH),
                 int(round(r.dep)), int(round(r.seats)), int(round(r.pax)), 0]
                for _, r in g.iterrows()]
        route_set = set(zip(g.ORIGIN, g.DEST))

        days: dict[str, list[int]] = {}
        times: dict[str, list] = {}
        arrivals: dict[str, list] = {}
        tmp_d: dict = {}; tmp_t: dict = {}; tmp_a: dict = {}
        sub = dd[dd.mkt == c] if "mkt" in dd.columns else dd.iloc[0:0]
        for o, d, ym, mk, tv, av in zip(sub.o, sub.d, sub.ym, sub["mask"],
                                        sub.times, sub.arrs):
            if (o, d) not in route_set or ym not in dmi:
                continue
            tmp_d.setdefault((o, d), [0] * len(dm))[dmi[ym]] = int(mk)
            tv = str(tv).strip()
            if tv and tv != "nan":
                tmp_t.setdefault((o, d), [None] * len(dm))[dmi[ym]] = \
                    [int(x) for x in tv.split()]
            av = str(av).strip()
            if av and av != "nan":
                tmp_a.setdefault((o, d), [None] * len(dm))[dmi[ym]] = \
                    [int(x) for x in av.split()]
        days = {f"{o}-{d}": arr for (o, d), arr in tmp_d.items()
                if any(0 < v < 127 for v in arr)}
        times = {f"{o}-{d}": arr for (o, d), arr in tmp_t.items()}
        arrivals = {f"{o}-{d}": arr for (o, d), arr in tmp_a.items()}

        intl_times: dict[str, list[int]] = {}
        intl_arrs: dict[str, list[int]] = {}
        intl_meta = {"label": "", "sampled": []}
        itc = it[it.mkt == c]
        if len(itc):
            imasks = itc["mask"] if "mask" in itc.columns else [0] * len(itc)
            for o, d, tv, av, mk in zip(itc.o, itc.d, itc.times, itc.arrs, imasks):
                tv, av = str(tv).strip(), str(av).strip()
                if (o, d) in route_set and tv and tv != "nan":
                    try:
                        mkv = int(mk)
                    except (TypeError, ValueError):
                        mkv = 0
                    deps = [int(x) for x in tv.split()]
                    intl_times[f"{o}-{d}"] = [mkv] + deps
                    if av and av != "nan":
                        arrs = [int(x) for x in av.split()]
                        if len(arrs) == len(deps):
                            intl_arrs[f"{o}-{d}"] = arrs
            latest = str(itc["asof"].max())
            try:
                ad = dt.date.fromisoformat(latest)
                mons = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                        "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
                intl_meta["label"] = f"{mons[ad.month-1]} \u2019{str(ad.year)[2:]}"
                intl_meta["sampled"] = sorted(set(itc[(itc["asof"] == latest)
                                                     & itc.o.isin(us_codes)].o))
            except ValueError:
                pass

        iana = {code: str(z) for code, z in zip(ii.code, ii.tz)
                if code in idx and isinstance(z, str) and "/" in z}
        coords = {code: [float(la), float(lo)]
                  for code, la, lo in zip(cc.code, cc.lat, cc.lon) if code in idx}

        payload = json.dumps({"carrier": {"code": c, "name": CARRIERS[c]},
                              "airports": airports, "rows": rows, "coords": coords,
                              "days": days, "dm": dm, "times": times, "arrs": arrivals,
                              "intlTimes": intl_times, "intlArrs": intl_arrs,
                              "intlMeta": intl_meta, "tzn": iana},
                             separators=(",", ":"))
        (DATA_DIR / f"{c}.json").write_text(payload)
        written.append({"code": c, "name": CARRIERS[c], "airports": len(airports),
                        "routeMonths": len(rows)})
        print(f"  wrote data/{c}.json ({len(payload)/1e6:.2f} MB, "
              f"{len(airports)} airports, {len(days)} day-limited routes, "
              f"{len(times)} with times)")
        if c == "DL":
            template = TEMPLATE.read_text()
            if PLACEHOLDER not in template:
                print("ERROR: template.html missing data placeholder"); return 1
            OUTPUT.write_text(template.replace(PLACEHOLDER, payload))
            print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size/1e6:.2f} MB)")
    (DATA_DIR / "carriers.json").write_text(json.dumps(written, separators=(",", ":")))
    print(f"36-month window ending {latest_year}-{latest_month:02d}; "
          f"{len(written)} airlines")
    return 0


if __name__ == "__main__":  # entry point
    sys.exit(main())
