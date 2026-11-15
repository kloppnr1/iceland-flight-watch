#!/bin/bash
# Server side: Travelpayouts prices + Keflavik board + rebuild page data, using the latest Google calendars in cal/
# (Google calendars are sent by the cloud task to /api/cal). Run by flight-watch.timer and after each upload.
# Optional arguments: destination codes to fetch Travelpayouts prices for right now (api.py passes the
# new code when a destination is added); without arguments all destinations are fetched.
cd /opt/flight-watch || exit 1
exec 9>/opt/flight-watch/.run.lock
flock 9
set -a; . ./.env; set +a
./venv/bin/python tp.py "$@"
WATCH_FILE=/opt/flight-watch/watches.json ./venv/bin/python build.py cal tp.json /srv/flight-watch/data
chmod -R a+rX /srv/flight-watch
