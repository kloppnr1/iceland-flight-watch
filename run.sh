#!/bin/bash
# Server side: Travelpayouts prices + Keflavik board + rebuild page data, using the latest Google calendars in cal/
# (Google calendars are sent by the cloud task to /api/cal). Run by flight-watch.timer and after each upload.
cd /opt/flight-watch || exit 1
set -a; . ./.env; set +a
./venv/bin/python tp.py
WATCH_FILE=/opt/flight-watch/watches.json ./venv/bin/python build.py cal tp.json /srv/flight-watch/data
chmod -R a+rX /srv/flight-watch
