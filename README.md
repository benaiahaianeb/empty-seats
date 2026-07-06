# Empty Seats Atlas

A single-page explorer for finding where Delta's planes fly emptiest — built for
non-rev / standby trip planning from DOT Form 41 T-100 data.

**Coverage:** Delta mainline (DL), Endeavor Air (9E), and Delta-marketed SkyWest (OO)
scheduled passenger service. Rolling ~16-month window, refreshed monthly.

## Hosting on GitHub Pages

1. Push this repo to GitHub.
2. Settings → Pages → Source: *Deploy from a branch* → `main` / root.
3. Your site is live at `https://<username>.github.io/<repo>/`.

## Files

| File | Purpose |
|---|---|
| `index.html` | The site — fully self-contained, data embedded |
| `template.html` | Same page with a data placeholder; `refresh_data.py` fills it |
| `refresh_data.py` | Downloads latest BTS T-100 data and regenerates `index.html` |
| `data/oo_dl_share.csv` | Per-segment share of SkyWest flying marketed as Delta |
| `.github/workflows/refresh.yml` | Monthly auto-refresh via GitHub Actions |

## Refreshing data manually

```bash
pip install pandas requests
python refresh_data.py                   # normal monthly refresh
python refresh_data.py --refresh-skywest # also rebuild SkyWest attribution (1-2x/year)
```

The GitHub Action does the same thing automatically on the 15th of each month and
commits `index.html` if BTS published a new month. You can also trigger it from the
Actions tab ("Run workflow").

## Methodology notes

- **Loads** are historical monthly averages (passengers ÷ seats flown) reported by
  carriers to DOT; there is a ~3-month reporting lag. Verify live loads in TravelNet.
- **Week mode** interpolates between adjacent months — it reads the seasonal curve,
  but cannot see intra-month holiday spikes (Thanksgiving/Christmas/spring break).
- **SkyWest attribution:** T-100 reports operating carrier only, and SkyWest flies for
  DL/UA/AA/AS. Each OO segment is weighted by the share of its flights marketed as
  Delta in BTS Marketing-Carrier On-Time data. Nearly all segments are single-brand.
  Republic's Delta Connection flying cannot be separated and is excluded.
- **Trip finder** ranks a curated list of leisure destinations by open seats on the
  tightest leg, considering nonstops and one-stop connections over Delta hubs
  (ATL, MSP, DTW, SLC, JFK, LGA, BOS, LAX, SEA), requiring ~3×/week or better per leg.

Data: Bureau of Transportation Statistics — T-100 Segment (All Carriers) and
Marketing Carrier On-Time Performance.
