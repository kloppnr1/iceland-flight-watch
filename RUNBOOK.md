# Iceland Flight Watch – daily run

Goal: find cheap round trips between Denmark/Hamburg (CPH, BLL, AAL, HAM) and Keflavík (KEF), stays of 1–6 months, out and home airports may differ. Direct flights matter most, but all options are kept.

The page is the artifact `2c33bbd4-6671-4e58-a0df-75451bdeaf69` (https://claude.ai/artifact/6TabPgGja1bzhVvDhkTsvY). It reads `site/data/*.json`.

## Steps

1. **Google Flights calendars (Claude in Chrome).** For each file below, open the URL in a new Chrome tab, run `chrome/gf_cal.js` with the javascript tool (it opens the date calendar, steps through ~11 months and stores the result in `window.GFA`; it returns `<start date> n=<days> nonzero=<days with a price>`). Then read the prices in three parts: `window.GFA.a.slice(0,120).join(' ')`, `slice(120,240)`, `slice(240)`. Write the file as: line 1 = start date, then the numbers (price in DKK / 10, 0 = no flight).
   - If `nonzero` is far below `n` for an "any" route, months were skipped: run it again once.
   - If the result is `none` (no flights), do not write a file.
   - Close the tab at the end.

   | file | URL (`q=` part, prefix `https://www.google.com/travel/flights?hl=en&curr=DKK&q=`) |
   |---|---|
   | cal/CPH-KEF-any.txt | one way flights from CPH to KEF on <today+7> |
   | cal/CPH-KEF-direct.txt | nonstop one way flights from CPH to KEF on <today+7> |
   | same for BLL, AAL, HAM → KEF (any + direct) | |
   | cal/KEF-CPH-any.txt | one way flights from KEF to CPH on <today+7> |
   | cal/KEF-CPH-direct.txt | nonstop one way flights from KEF to CPH on <today+7> |
   | same for KEF → BLL, AAL, HAM (any + direct) | |

   That is 16 calendars. Delete old `cal/*.txt` first so a route with no flights today does not reuse old prices.

   The script clicks the calendar's "next month" arrow at viewport point (1088, 414). If the Chrome window has another size and the calendar doesn't advance (`n` around 60), take a screenshot, find the arrow, and change the two coordinates in the script.

2. **Travelpayouts:** `TP_TOKEN=<token> python3 tp.py` → `tp.json` (takes ~20 s).

3. **Build:** `python3 build.py cal tp.json site/data` – updates `latest.json`, `changes.json`, `board.json` (Keflavík flight board: delays/cancellations), `lows.json`, `summary.json`, `index.json`, and archives `site/data/runs/<time>.json`. The last line printed is `SUMMARY {...}`.

4. **Save:** commit and push to `main`.

5. **Publish:** `publish_files` to the artifact above, mode `patch`, files `index.html` (as `index.html`) and `site/data/latest.json, changes.json, board.json, index.json, summary.json` (as `data/<name>`).

6. **Report** in 2–3 short sentences: cheapest round trip (any) and cheapest direct, with dates and airports, and say if either is lower than before (`any_before` / `direct_before` in the summary) or if there are big price drops.
