---
name: rank-trips
description: Rank non-rev standby destinations by total round-trip time for a given outbound and return day (for example out Friday from STL, home Sunday to BOS or PVD), with the open seats on every leg, from Empty Seats data. Use when someone asks which destinations get them out and back fastest, wants a weekend trip ranked by travel time, or asks for every routing and backup to a destination.
---

# Rank trips by round-trip time

`tools/trips.js` does the timing. Do not work schedules out by hand: it applies the
site's rules (legs at about 3×/week or more, 45-minute same-day connections,
routings no longer than 2.5× the straight line, US connecting points only) and
reports the schedule and load months it used.

## Get the tool

Inside this repository, run `node tools/trips.js`. Anywhere else, with Node 18 or
later:

```
curl -sSO https://raw.githubusercontent.com/benaiahaianeb/empty-seats/main/tools/trips.js
node trips.js --data https://raw.githubusercontent.com/benaiahaianeb/empty-seats/main/data/DL.json ...
```

Other airlines: `data/UA.json`, `AA`, `WN`, `B6`, `AS`, `NK`, `F9`, `G4`, `HA`.

## Run it

1. Rank destinations:
   `node tools/trips.js --from STL --home BOS,PVD --out fri --back sun --month 10 --region we`
   Narrow with `--states CO,MT,WY`, `--exclude-states UT`, `--to DEN,BZN,RAP`,
   `--exclude SLC`. Regions are `ne mw so sw we ca other`. Add `--json` to parse.
2. For the top few, list every routing and timing both ways:
   `node tools/trips.js --from STL --home BOS,PVD --out fri --back sun --month 10 --detail DEN`
   This gives the backups: other banks and connections that still fit the day.
3. `--help` lists the rest: `--depart-after`, `--arrive-by`, `--stops 0-2`,
   `--span avg` (3-year average loads), `--sched newest`, `--min-open`, `--mct`.

Pick `--month` for the month of travel. The data covers 36 months, so October
reads the latest October in the window, which can be a year old; the header names
it. `--sched newest` reads the newest month's schedule instead.

## Write the answer

- Lead with the winner and its total hours, then a table of the top three or so:
  destination, outbound routing and times, return routing and times, total.
- Give backups from `--detail`: the next bank, another connection, and its open
  seats. A nonstop with fewer open seats than a connection is worth saying.
- Flag thin legs. Under about 15 open seats a flight can fill.
- Name the months: "October 2025 schedule and loads". Times are local.
- Say plainly that loads are historical averages, not live availability, and that
  other non-revs are not counted. Tell them to check both before listing.
- Anything the tool marks `est.` stays marked `est.` in the answer.
- If the person's history matters (places already visited), ask or use what you
  know, and say when a faster option was skipped for that reason.
