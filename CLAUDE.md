# Empty Seats

Static site for Delta non-rev standby planning: which flights have empty seats.
`/` is the Delta site, `/all/` the same tool for ten US airlines, one at a time.
Full history, data semantics and gotchas: `HANDOFF-empty-seats-atlas.md` in the
owner's working folder. This file is the short version.

## Files

- `template.html` is the whole app. Every UI change goes here, nowhere else.
- `index.html` = template with Delta's dataset embedded at `/*__DATA__*/null`.
- `all/index.html` = template with a loader instead of the embed. Generated:
  `node tools/make_all.js`. Never edit it by hand.
- `refresh_data.py` builds everything from BTS + AeroDataBox. No Python on the
  owner's machine; `.github/workflows/pycheck.yml` compiles and unit-tests it on
  every push, and `refresh.yml` (cron 15th, or dispatch) does the real build.
- `data/<code>.json` per airline, `data/carriers.json`, and the CSVs the
  pipeline keeps between runs. Committed by the workflow.

## After any template change, in this order

1. `node tools/make_all.js` (regenerates `all/index.html`).
2. Rebuild `index.html`: `python refresh_data.py --rebuild-only`, or the ten-line
   node equivalent (lift `const DATA = ...;` out of the committed `index.html`,
   replace the placeholder). The data window must not change.
3. `node tools/audit.js` must report zero violations. It drives the page in
   jsdom (`npm i jsdom` once) and checks every schedule label against the banks
   and day pattern beside it. This is an owner invariant.
4. Check 320, 375, 768 and 1280 px: nothing wider than the viewport. Phones are
   the main client.
5. Commit all three pages together. If `template.html` and either built page
   disagree, the built page is wrong.

## Rules the owner enforces

- Honest data over pretty numbers. Show nothing rather than a guess; anything
  estimated says `est.`; the summary line names the month a schedule came from.
- The label never contradicts the data beside it. Banks drive `N×/day`, day
  patterns drive `N×/week`, and `Irregular` means charter flying in the viewed
  month, not "no schedule in the newest one".
- No cryptic abbreviations, no taglines, no tips, no decoration. Sentence case.
  One identity hue, semantic colours only for load data, tabular numerals.
- Lists are uncapped. Every usable routing is a row; nothing is collapsed to a
  winner.
- `ALT_EXTRA` is the owner's manual airport pairing. Treat edits as intentional.
- Copy: short, direct, manual voice. Word budgets are hard limits.

## Known state

- The AeroDataBox key stopped working 2026-07-10. International schedules are a
  July snapshot until the `AERODATABOX_KEY` secret is fixed and the workflow
  dispatched. Only the owner can do that.
- Pipeline changes can only be verified in CI or by a dispatch run. Push to a
  branch first; the workflow commits data back to whatever branch it ran on.
