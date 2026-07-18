# Empty Seats Atlas

Finds the Delta routes with the most empty seats, for non-rev standby planning.

Covers scheduled passenger service on Delta mainline (DL), Endeavor Air (9E), and the SkyWest (OO) flying marketed as Delta. Republic is excluded because T-100 data can't attribute its flying to one airline. Rolling 36-month window, refreshed monthly.

## Refreshing data manually

```bash
pip install pandas requests
python refresh_data.py                   # normal monthly refresh
python refresh_data.py --refresh-skywest # also rebuild SkyWest attribution (1-2x/year)
```

A GitHub Action runs the same refresh on the 15th of each month and commits `index.html` when BTS publishes a new month. The Actions tab ("Run workflow") triggers it on demand.

International departure times come from the AeroDataBox API. Set the `AERODATABOX_KEY` repository secret to enable them. Without the key the refresh still succeeds and international rows show frequency only.

Sources: Bureau of Transportation Statistics T-100 Segment (All Carriers) and Marketing Carrier On-Time Performance; OurAirports for airport coordinates.
