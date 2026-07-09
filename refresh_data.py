#!/usr/bin/env python3
"""
Empty Seats Atlas — data refresh script.

Downloads the latest BTS T-100 Segment data (Delta mainline DL + Endeavor 9E +
Delta-marketed SkyWest OO), rebuilds the embedded dataset, and regenerates
index.html from template.html.

Usage:
    python refresh_data.py                  # refresh T-100 loads (typical monthly run)
    python refresh_data.py --refresh-skywest  # also rebuild the SkyWest->Delta
                                              # attribution table (heavier; run 1-2x/year)

Data window: December of (latest_year - 2) through the latest reported month,
mirroring a rolling ~16-month view. BTS reports with a ~3 month lag.

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
UA = {"User-Agent": "Mozilla/5.0 (empty-seats-atlas refresh script)"}
T100_FIELDS = ["UNIQUE_CARRIER", "ORIGIN", "ORIGIN_CITY_NAME", "ORIGIN_COUNTRY",
               "DEST", "DEST_CITY_NAME", "DEST_COUNTRY",
               "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "YEAR", "MONTH", "CLASS"]
SHARE_CSV = ROOT / "data" / "oo_dl_share.csv"
DAYS_CSV = ROOT / "data" / "route_days.csv"
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


def rebuild_skywest_share(session: requests.Session, latest_year: int) -> None:
    """Rebuild data/oo_dl_share.csv from recent Marketing-Carrier on-time months.

    Samples the most recent January and July available (seasonal coverage).
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
        oo = df[df.op == "OO"]
        tot = oo.groupby(["o", "d"]).size().rename("tot")
        dl = oo[oo.mkt == "DL"].groupby(["o", "d"]).size().rename("dl")
        samples.append(pd.concat([tot, dl], axis=1).fillna(0))
        print(f"    {len(oo):,} SkyWest flights")
        if len(samples) >= 2:
            break
    if not samples:
        print("  WARNING: no on-time data retrieved; keeping existing share table")
        return
    alls = pd.concat(samples).groupby(level=[0, 1]).sum()
    alls["share"] = alls.dl / alls.tot
    alls.to_csv(SHARE_CSV)
    print(f"  wrote {SHARE_CSV} ({len(alls)} segments)")


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


def rebuild_route_days(session, months: list[tuple[int, int]],
                       limit: int | None = None) -> None:
    """Incrementally build data/route_days.csv: per-route, per-month day-of-week
    operating masks and normal scheduled departure banks for DL-marketed flights,
    from Marketing-Carrier on-time data (domestic routes only). mask bit 0 =
    Monday; a weekday counts if the route saw at least one flight (operated or
    cancelled) on that weekday that month. times is a space-separated list of
    HHMM departure banks (local time).

    Months already present in the CSV are kept, months outside the window are
    dropped, and only missing months are downloaded (typically one per run).
    """
    cols = ["o", "d", "ym", "mask", "times"]
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
                                  "Origin", "Dest", "CRSDepTime"], dtype=str)
        df = df[df.Marketing_Airline_Network == "DL"]
        t = pd.to_numeric(df.CRSDepTime, errors="coerce")
        df = df.assign(dow=pd.to_datetime(df.FlightDate).dt.dayofweek,
                       mins=(t // 100) % 24 * 60 + t % 100)
        masks = df.groupby(["Origin", "Dest"]).dow.apply(
            lambda s: sum(1 << int(w) for w in set(s))).rename("mask")
        tdf = df[df.mins.notna()].copy()
        tdf["mins"] = tdf.mins.astype(int)
        times = tdf.groupby(["Origin", "Dest"]).mins.apply(
            lambda s: " ".join(str(x) for x in _time_banks(list(s)))).rename("times")
        add = pd.concat([masks, times], axis=1).reset_index()
        add.columns = ["o", "d", "mask", "times"]
        add["times"] = add.times.fillna("")
        add["ym"] = f"{y}-{m:02d}"
        frames.append(add[cols])
        done += 1
        print(f"    {len(add)} routes")
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["ym", "o", "d"])
    out.to_csv(DAYS_CSV, index=False)
    left = len(missing) - done
    print(f"  route_days.csv: {len(out)} rows, "
          f"{out.ym.nunique()}/{len(months)} window months"
          + (f", {left} still missing" if left else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-skywest", action="store_true",
                    help="also rebuild the SkyWest->Delta attribution table")
    args = ap.parse_args()

    session = requests.Session()
    today = dt.date.today()

    print("Downloading T-100 years ...")
    years, frames = [today.year - 2, today.year - 1, today.year], []
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

    # rolling window: Dec of (latest_year - 2) .. latest month
    lo_y = latest_year - 2
    df = df[((df.YEAR == lo_y) & (df.MONTH == 12)) | (df.YEAR > lo_y)]

    if args.refresh_skywest:
        print("Rebuilding SkyWest attribution ...")
        rebuild_skywest_share(session, latest_year)

    share = pd.read_csv(SHARE_CSV).set_index(["o", "d"])["share"].to_dict()

    dl9e = df[df.UNIQUE_CARRIER.isin(["DL", "9E"])].copy()
    dl9e["w"] = 1.0
    oo = df[df.UNIQUE_CARRIER == "OO"].copy()
    oo["w"] = [share.get((o, d), 0.0) for o, d in zip(oo.ORIGIN, oo.DEST)]
    oo = oo[oo.w >= 0.05]
    sub = pd.concat([dl9e, oo])
    for c in ["DEPARTURES_PERFORMED", "SEATS", "PASSENGERS"]:
        sub[c] = sub[c] * sub.w

    g = sub.groupby(["UNIQUE_CARRIER", "ORIGIN", "ORIGIN_CITY_NAME", "ORIGIN_COUNTRY",
                     "DEST", "DEST_CITY_NAME", "DEST_COUNTRY", "YEAR", "MONTH"],
                    as_index=False).agg(dep=("DEPARTURES_PERFORMED", "sum"),
                                        seats=("SEATS", "sum"),
                                        pax=("PASSENGERS", "sum"))
    g = g[g.dep >= 0.5]
    print(f"Route-month rows: {len(g):,}")

    ap_lookup: dict[str, tuple[str, str]] = {}
    for _, r in g.iterrows():
        ap_lookup.setdefault(r.ORIGIN, (r.ORIGIN_CITY_NAME, r.ORIGIN_COUNTRY))
        ap_lookup.setdefault(r.DEST, (r.DEST_CITY_NAME, r.DEST_COUNTRY))
    codes = sorted(ap_lookup)
    idx = {c: i for i, c in enumerate(codes)}
    airports = [[c, ap_lookup[c][0], ap_lookup[c][1]] for c in codes]
    car = {"DL": 0, "9E": 1, "OO": 2}
    rows = [[idx[r.ORIGIN], idx[r.DEST], int(r.YEAR), int(r.MONTH),
             int(round(r.dep)), int(round(r.seats)), int(round(r.pax)),
             car[r.UNIQUE_CARRIER]] for _, r in g.iterrows()]

    window_months = sorted({(int(y), int(mo)) for y, mo in zip(g.YEAR, g.MONTH)})
    print("Rebuilding operating-days table ...")
    rebuild_route_days(session, window_months)

    dm = [f"{y}-{mo:02d}" for y, mo in window_months]
    dmi = {s: i for i, s in enumerate(dm)}
    days: dict[str, list[int]] = {}
    times: dict[str, list] = {}
    if DAYS_CSV.exists():
        dd = pd.read_csv(DAYS_CSV)
        route_set = set(zip(g.ORIGIN, g.DEST))
        has_t = "times" in dd.columns
        tmp_d: dict[tuple[str, str], list[int]] = {}
        tmp_t: dict[tuple[str, str], list] = {}
        tvals = dd.times if has_t else [""] * len(dd)
        for o, d, ym, mk, tv in zip(dd.o, dd.d, dd.ym, dd["mask"], tvals):
            if (o, d) not in route_set or ym not in dmi:
                continue
            tmp_d.setdefault((o, d), [0] * len(dm))[dmi[ym]] = int(mk)
            tv = str(tv).strip()
            if tv and tv != "nan":
                tmp_t.setdefault((o, d), [None] * len(dm))[dmi[ym]] = \
                    [int(x) for x in tv.split()]
        days = {f"{o}-{d}": arr for (o, d), arr in tmp_d.items()
                if any(0 < v < 127 for v in arr)}
        times = {f"{o}-{d}": arr for (o, d), arr in tmp_t.items()}
    print(f"Embedded: {len(days)} day-limited routes, {len(times)} routes with times")

    import json
    payload = json.dumps({"airports": airports, "rows": rows,
                          "days": days, "dm": dm, "times": times},
                         separators=(",", ":"))
    template = TEMPLATE.read_text()
    if PLACEHOLDER not in template:
        print("ERROR: template.html missing data placeholder"); return 1
    OUTPUT.write_text(template.replace(PLACEHOLDER, payload))
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size/1e6:.2f} MB), "
          f"{len(airports)} airports, window Dec {lo_y} - {latest_year}-{latest_month:02d}")
    return 0


if __name__ == "__main__":  # entry point
    sys.exit(main())
