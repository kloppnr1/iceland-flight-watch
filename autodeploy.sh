#!/bin/bash
# Auto-deploy: pull the GitHub repo (read-only deploy key) and install changed code and page.
# Run by flight-watch-deploy.timer every 5 minutes on the Hetzner server.
set -e
SRC=/opt/flight-watch-src
APP=/opt/flight-watch
WEB=/srv/flight-watch
export GIT_SSH_COMMAND="ssh -i /root/.ssh/flight_watch_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

# server self-check (certificates, services, disk) -> data/health.json, read by the cloud health-check task
[ -f "$APP/health.py" ] && timeout 60 python3 "$APP/health.py" "$WEB/data/health.json" >/dev/null 2>&1 || true

if [ ! -d "$SRC/.git" ]; then
  git clone -q git@github.com:kloppnr1/iceland-flight-watch.git "$SRC"
fi
cd "$SRC"
git fetch -q origin main
# done only when the deployed commit is this one (after a self-update the repo is already reset, so compare
# with what was last deployed, not only with origin/main)
if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && [ "$(cat "$APP/.deployed" 2>/dev/null)" = "$(git rev-parse HEAD)" ]; then
  exit 0
fi
git reset -q --hard origin/main
# a new version of this script: run that one instead (it then deploys this commit itself)
if ! cmp -s autodeploy.sh "$APP/autodeploy.sh"; then
  install -m 755 autodeploy.sh "$APP/autodeploy.sh"
  exec "$APP/autodeploy.sh"
fi

changed_api=0
for f in api.py watches.py build.py tp.py patterns.py run.sh health.py; do
  [ -f "$f" ] || continue
  if ! cmp -s "$f" "$APP/$f"; then
    cp "$f" "$APP/$f"
    if [ "$f" = api.py ] || [ "$f" = watches.py ]; then changed_api=1; fi
  fi
done
chmod 755 "$APP/run.sh"
cmp -s site/index.html "$WEB/index.html" || install -m 644 site/index.html "$WEB/index.html"
cmp -s site/admin.html "$WEB/admin.html" || install -m 644 site/admin.html "$WEB/admin.html"
cmp -s site/data/bags.json "$WEB/data/bags.json" || install -m 644 site/data/bags.json "$WEB/data/bags.json"
cmp -s site/airports.json "$WEB/airports.json" || install -m 644 site/airports.json "$WEB/airports.json"
if [ $changed_api = 1 ]; then systemctl restart flight-watch-api; fi
git rev-parse HEAD > "$APP/.deployed"
echo "deployed $(git rev-parse --short HEAD)"
