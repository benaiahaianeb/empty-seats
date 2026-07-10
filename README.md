# Empty Seats Atlas

An explorer for finding where Delta's planes fly emptiest — built for
non-rev / standby trip planning from DOT Form 41 T-100 data.

**Coverage:** Delta mainline (DL), Endeavor Air (9E), and Delta-marketed SkyWest (OO)
scheduled passenger service. Rolling ~16-month window, refreshed monthly.




## Refreshing data manually

```bash
pip install pandas requests
python refresh_data.py                   # normal monthly refresh
python refresh_data.py --refresh-skywest # also rebuild SkyWest attribution (1-2x/year)
```

The GitHub Action does the same thing automatically on the 15th of each month and
commits `index.html` if BTS published a new month. You can also trigger it from the
Actions tab ("Run workflow").

Data from Bureau of Transportation Statistics — T-100 Segment (All Carriers) and
Marketing Carrier On-Time Performance.
