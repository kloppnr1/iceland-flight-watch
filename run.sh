#!/bin/bash
# Server side: Travelpayouts prices + Keflavik board + rebuild page data, using the latest Google calendars in cal/
# (Google calendars are sent by the cloud task to /api/cal). Run by flight-watch.timer and after each upload.
# Optional arguments: destination codes to fetch Travelpayouts prices for right now (api.py passes the
# new code when a destination is added); without arguments all destinations are fetched.
# --build-only: no Travelpayouts fetch, just rebuild from the stored prices (api.py after each Google upload; the
# lock queues it behind a running build, so no upload is left out).
cd /opt/flight-watch || exit 1
# Ryanair's own fares (ryanair.py, ~10 minutes) on the scheduled runs, when data/fr.json is over 5 hours old. Its own
# lock and before the build lock, so uploads keep being built meanwhile; a failure keeps the earlier fares.
FR=/srv/flight-watch/data/fr.json
if [ $# -eq 0 ] && { [ ! -f "$FR" ] || [ -n "$(find "$FR" -mmin +300)" ]; }; then
  flock -n /opt/flight-watch/.fr.lock timeout 1800 ./venv/bin/python ryanair.py --out "$FR" > /opt/flight-watch/ryanair.log 2>&1 || true
fi
exec 9>/opt/flight-watch/.run.lock
flock 9
set -a; . ./.env; set +a
if [ "$1" != "--build-only" ]; then ./venv/bin/python tp.py "$@"; fi
WATCH_FILE=/opt/flight-watch/watches.json ./venv/bin/python build.py cal tp.json /srv/flight-watch/data
chmod -R a+rX /srv/flight-watch
