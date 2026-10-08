# Empty Seats

Which flights have empty seats. Built for Delta non-rev standby planning.

- `/` Delta: mainline plus regional flying sold as Delta (Endeavor, SkyWest, Republic and others).
- `/all/` The same tool for ten US airlines, one at a time.

A regional airline that flies for one airline counts in full. One that flies for several, like SkyWest or Republic, is split by each airline's share of the route in the on-time data.

## Data

A rolling 36-month window, updated monthly.

| Source | Used for |
| --- | --- |
| BTS T-100 Segment (All Carriers) | Seats and passengers |
| BTS Marketing Carrier On-Time Performance | Schedules, day patterns |
| AeroDataBox | International departure times |
| OurAirports | Airport coordinates |
| OpenFlights | Airport time zones |

Anything estimated is marked `est.`. The summary line names the month a schedule came from, and the month international times were collected.

Loads are historical averages, not live availability. Check the airline before you travel.
