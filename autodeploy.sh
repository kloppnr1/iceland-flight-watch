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

# RSA certificates: some company networks (TLS inspection) cannot handle ECDSA and show
# ERR_SSL_VERSION_OR_CIPHER_MISMATCH. Add the global option key_type rsa2048 to the server's Caddyfile if it is
# missing (merged into an existing global block), validate, and restart Caddy with fresh certificates for the
# flight-watch names (a reload would keep serving the cached ECDSA ones). Old certificates are kept in a backup.
CF=/etc/caddy/Caddyfile
if [ -f "$CF" ] && ! grep -q 'key_type rsa2048' "$CF" && [ ! -f "$APP/.rsa-failed" ]; then
  cp "$CF" "$CF.bak-before-rsa"
  if [ "$(grep -v '^\s*#' "$CF" | grep -v '^\s*$' | head -1 | tr -d '[:space:]')" = "{" ]; then
    awk 'BEGIN{d=0} !d && /^[[:space:]]*\{[[:space:]]*$/ {print; print "\tkey_type rsa2048"; d=1; next} {print}' "$CF.bak-before-rsa" > "$CF"
  else
    { printf '{\n\tkey_type rsa2048\n}\n\n'; cat "$CF.bak-before-rsa"; } > "$CF"
  fi
  # syntax check (adapt only parses), then a reload, which keeps the running config if the new one is bad
  if err=$(caddy adapt --config "$CF" --adapter caddyfile 2>&1 >/dev/null) && err=$(systemctl reload caddy 2>&1); then
    BK=/var/lib/caddy/ecdsa-backup-$(date +%Y%m%d%H%M); mkdir -p "$BK"
    for d in /var/lib/caddy/.local/share/caddy/certificates/*/fly.stromsparer.dk /var/lib/caddy/.local/share/caddy/certificates/*/fly.xn--strmsparer-2cb.dk; do
      [ -d "$d" ] && mv "$d" "$BK/$(basename "$(dirname "$d")")-$(basename "$d")"
    done
    systemctl restart caddy
    echo "caddy: key_type rsa2048 added, restarted (old fly certificates in $BK)" > "$WEB/data/caddy-change.txt"
  else
    cp "$CF.bak-before-rsa" "$CF"
    systemctl reload caddy >/dev/null 2>&1 || true
    { echo "caddy: adding key_type rsa2048 failed; Caddyfile left as it was. Error:"; echo "$err" | tail -8; } > "$WEB/data/caddy-change.txt"
    touch "$APP/.rsa-failed"  # do not retry every 5 minutes; remove this file to try again
  fi
  chmod 644 "$WEB/data/caddy-change.txt"
fi

# One time: re-issue every other ECDSA certificate on the server as RSA now, not at its next renewal.
# A name that has no new certificate after 3 minutes (for example one no longer served here) gets its old one back.
MARK="$APP/.rsa-all-done"
if [ ! -f "$MARK" ] && grep -q 'key_type rsa2048' "$CF" 2>/dev/null; then
  ST=/var/lib/caddy/.local/share/caddy/certificates
  BK=/var/lib/caddy/ecdsa-backup-all-$(date +%Y%m%d%H%M); mkdir -p "$BK"
  moved=""
  for d in "$ST"/*/*/; do
    d=${d%/}; n=$(basename "$d")
    grep -q "EC PRIVATE KEY" "$d/$n.key" 2>/dev/null || continue
    mv "$d" "$BK/$(basename "$(dirname "$d")")__$n"; moved="$moved $n"
  done
  bad=""
  if [ -n "$moved" ]; then
    systemctl restart caddy
    sleep 180
    for n in $moved; do
      echo | timeout 15 openssl s_client -connect 127.0.0.1:443 -servername "$n" 2>/dev/null | grep -q "BEGIN CERTIFICATE" || bad="$bad $n"
    done
    if [ -n "$bad" ]; then
      for n in $bad; do for b in "$BK"/*__"$n"; do iss=$(basename "$b"); iss=${iss%__*}; mkdir -p "$ST/$iss"; mv "$b" "$ST/$iss/$n"; done; done
      systemctl restart caddy
    fi
  fi
  echo "caddy: RSA for all: re-issued:${moved:- none}; old certificate put back:${bad:- none} (backup $BK)" >> "$WEB/data/caddy-change.txt"
  chmod 644 "$WEB/data/caddy-change.txt"
  touch "$MARK"
fi

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
cmp -s site/data/bags.json "$WEB/data/bags.json" || install -m 644 site/data/bags.json "$WEB/data/bags.json"
cmp -s site/airports.json "$WEB/airports.json" || install -m 644 site/airports.json "$WEB/airports.json"
if [ $changed_api = 1 ]; then systemctl restart flight-watch-api; fi
git rev-parse HEAD > "$APP/.deployed"
echo "deployed $(git rev-parse --short HEAD)"
