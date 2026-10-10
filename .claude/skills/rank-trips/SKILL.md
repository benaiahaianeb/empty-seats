---
name: rank-trips
description: Rank non-rev standby destinations by total round-trip time for a given outbound and return day (for example out Friday from STL, home Sunday to BOS or PVD), with the open seats on every leg, from Empty Seats data, then plan the ground side of the top picks - rental cars, lodging, groceries, things to see (Atlas Obscura and similar) and a trip budget. Use when someone asks which destinations get them out and back fastest, wants a weekend trip ranked by travel time, asks for every routing and backup to a destination, or wants to know what a non-rev trip would cost and what to do there.
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

# Plan the ground side

Once the flights are ranked, research the top two or three destinations. The
flight tool cannot do this part: use web search and fetch, and cite every page a
price or place came from. Ask for anything that changes the answer and is not
already known: travellers, budget ceiling, car or no car, cabin, hotel or camping.

Fit everything to the flights. The car is picked up at the outbound arrival time
and returned at least 90 minutes before the return departure; the lodging covers
the nights between. Standby can strand someone a night, so prefer refundable
bookings and say what a missed return flight would cost.

## Rental car

- Check the airport counters first, then off-airport ones and Turo. Quote the
  total for the exact pickup and return times, taxes and airport fees included.
- Say whether a car is needed at all: transit, a shuttle or a rideshare can beat
  it for a city stay. For mountains in October to April, say whether the route
  needs AWD or chains and name the road (I-70 traction law, Teton Pass).
- Note the drive time from the airport to each lodging and point of interest.

## Lodging

- Give two or three options at different prices: a cabin or vacation rental, a
  hotel or motel, and camping or a public-lands cabin (recreation.gov, state
  parks) where they exist. Match the request: "cold river cabin" means on the
  water, not in town.
- Quote the total for the nights with fees and tax, and the cancellation terms.
  Note distance from the airport and from the main things to do.
- Pull the nightly low for the destination's season when live prices are not
  visible, and mark it `est.`.

## Groceries

- Name the nearest full grocery store to the lodging and one on the drive from
  the airport (a Costco, King Soopers, Safeway, Walmart and so on), with hours
  for the arrival day if it lands late. Mention a general store or market if the
  lodging is remote and the big store is far.
- Estimate food for the stay: groceries for cooked meals, plus a meal or two out.

## Things to see

- Start with Atlas Obscura: atlasobscura.com/things-to-do/<city>-<state>, for
  example /things-to-do/bozeman-montana, then search the region around it, since
  the best entries are often an hour out. Add the National Park Service, state
  park pages, the local tourism board and Roadside America for the unusual.
- Give five to eight places, each with a sentence on what it is, the drive time
  from the lodging, cost, and whether it is open in the travel month. Seasonal
  closures (Beartooth Highway, Going-to-the-Sun Road, Trail Ridge Road) matter.
- Lead with what fits the person's stated interests.

## Budget

End each destination with a table, one row per line item and the source beside
it: flights (non-rev fees or taxes, if known; otherwise say so), car, gas,
lodging, groceries, meals out, entry fees and parking. Give a total and a range.
Mark estimates `est.` and name the date the prices were seen, since they move.
Compare the destinations on total cost next to total travel time so the choice
is visible: "Denver: 8.4 hr, about $610. Bozeman: 11.0 hr, about $780."
