"""Server self-check, run by autodeploy.sh every 5 minutes (as root): writes /srv/flight-watch/data/health.json.

- TLS: connects to each of the site's names on port 443 (as a browser would, via DNS) and checks that the
  certificate is valid for that name, and how many days it has left. A name Caddy has no certificate for
  shows the browser error ERR_SSL_VERSION_OR_CIPHER_MISMATCH; here it shows as an error.
- Services: caddy and flight-watch-api active.
- Disk: free space on /.
Read by check_health.py (the cloud health-check task), which reports problems.
"""
import datetime, json, os, shutil, socket, ssl, subprocess, sys

NAMES = ['fly.stromsparer.dk', 'fly.xn--strmsparer-2cb.dk']
OUT = sys.argv[1] if len(sys.argv) > 1 else '/srv/flight-watch/data/health.json'
now = datetime.datetime.now(datetime.timezone.utc)


def tls(name):
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((name, 443), timeout=10) as s:
            with ctx.wrap_socket(s, server_hostname=name) as t:
                c = t.getpeercert()
        exp = datetime.datetime.fromtimestamp(ssl.cert_time_to_seconds(c['notAfter']), datetime.timezone.utc)
        return {'ok': True, 'days_left': (exp - now).days, 'expires': exp.isoformat(timespec='seconds')}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'[:300]}


def active(unit):
    try:
        return subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as e:
        return 'unknown: ' + str(e)[:100]


du = shutil.disk_usage('/')
res = {'checked_at': now.isoformat(timespec='seconds'),
       'tls': {n: tls(n) for n in NAMES},
       'services': {u: active(u) for u in ['caddy', 'flight-watch-api']},
       'disk_free_pct': round(100 * du.free / du.total, 1)}
tmp = OUT + '.tmp'
with open(tmp, 'w') as f:
    json.dump(res, f, indent=1)
os.chmod(tmp, 0o644)
os.replace(tmp, OUT)
print(json.dumps(res))
