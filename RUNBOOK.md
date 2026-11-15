# Iceland Flight Watch

Public page: https://fly.strømsparer.dk (Hetzner server 89.167.101.11, Caddy, `/srv/flight-watch`).

## How data is updated
- **Cloud task (twice a day):** runs `fetch_gf.py cal` (Google Flights calendars via the open-source `fli` tool, `pip install flights click typer rich`), then `UPLOAD_TOKEN=... python3 upload.py cal`, which posts the calendars to `https://fly.xn--strmsparer-2cb.dk/api/cal`. Google blocks these requests from the Hetzner server (HTTP 429), so this part runs in the cloud.
- **Server (`/opt/flight-watch`):** `api.py` (systemd `flight-watch-api`, 127.0.0.1:8092, Caddy `/api/*`) stores uploads and watches. `run.sh` (systemd `flight-watch.service`, timer 4×/day and after each upload) runs `tp.py` (Travelpayouts) and `build.py` (Keflavík board, changes, watches) into `/srv/flight-watch/data`.
- **Destinations:** the page's airport picker ("to …") lists the destinations in `/opt/flight-watch/dests.json` (public copy `data/dests.json`, default Keflavík). "Add or remove airports…" on the page adds one with the watch PIN (`POST /api/dests`). The API then runs `run.sh <CODE>`, which fetches Travelpayouts prices for that airport only and rebuilds, so first prices show within about a minute (marked as Travelpayouts on the page). `fetch_gf.py` reads the list from the site, does routes without Google prices first and uploads them right away (`{"merge": true}`), then the usual full run. Adding an airport also starts the cloud task "Iceland flight watch – Google prices" with the text "new-only" (it then runs only `fetch_gf.py --new-only`, about 10 minutes) through its API trigger, when `GF_FIRE_URL` and `GF_FIRE_TOKEN` are in `.env` (Fire URL and token from claude.ai/code/routines → the task → Edit → Trigger → + → API). The Delays tab only shows for Keflavík (it reads Keflavík Airport's board).
- Secrets live in `/opt/flight-watch/.env` (TP_TOKEN, WATCH_PIN, UPLOAD_TOKEN), not in git.

## Deploy code changes
Push to `main`. The server pulls the repo every 5 minutes (`flight-watch-deploy.timer` → `/opt/flight-watch/autodeploy.sh`, read-only GitHub deploy key `/root/.ssh/flight_watch_deploy`) and installs changed scripts and the page by itself. New systemd units or Caddy changes still need SSH (deploy key in `Downloads\sport-tv-deploy` on Martin's PC).
