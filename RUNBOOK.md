# Iceland Flight Watch

Public page: https://fly.strømsparer.dk (Hetzner server 89.167.101.11, Caddy, `/srv/flight-watch`).

## How data is updated
- **Cloud task (twice a day):** runs `fetch_gf.py cal` (Google Flights calendars via the open-source `fli` tool, `pip install flights click typer rich`), then `UPLOAD_TOKEN=... python3 upload.py cal`, which posts the calendars to `https://fly.xn--strmsparer-2cb.dk/api/cal`. Google blocks these requests from the Hetzner server (HTTP 429), so this part runs in the cloud.
- **Server (`/opt/flight-watch`):** `api.py` (systemd `flight-watch-api`, 127.0.0.1:8092, Caddy `/api/*`) stores uploads and watches. `run.sh` (systemd `flight-watch.service`, timer 4×/day and after each upload) runs `tp.py` (Travelpayouts) and `build.py` (Keflavík board, changes, watches) into `/srv/flight-watch/data`.
- Secrets live in `/opt/flight-watch/.env` (TP_TOKEN, WATCH_PIN, UPLOAD_TOKEN), not in git.

## Deploy code changes
Push to `main`. The server pulls the repo every 5 minutes (`flight-watch-deploy.timer` → `/opt/flight-watch/autodeploy.sh`, read-only GitHub deploy key `/root/.ssh/flight_watch_deploy`) and installs changed scripts and the page by itself. New systemd units or Caddy changes still need SSH (deploy key in `Downloads\sport-tv-deploy` on Martin's PC).
