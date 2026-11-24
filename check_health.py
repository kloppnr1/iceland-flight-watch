"""Health check for Flight Watch, run by the cloud task "Flight watch – health check".
Prints "OK" and exits 0 when all is well; otherwise prints one line per problem and exits 1.

Checks: the site answers; the server's own check (data/health.json, written every 5 minutes by health.py)
is fresh and shows valid certificates on every name (14+ days left), caddy and the API running and free
disk; the page data was rebuilt recently; Google prices, flight times and Anywhere prices are not stale.
Usage: python3 check_health.py
"""
import datetime, json, os, sys, urllib.request

SITE = os.environ.get('SITE', 'https://fly.stromsparer.dk')
now = datetime.datetime.now(datetime.timezone.utc)
problems = []


def get(path):
    req = urllib.request.Request(SITE + path, headers={'User-Agent': 'flight-watch-health', 'Cache-Control': 'no-cache'})
    return json.load(urllib.request.urlopen(req, timeout=60))


def hours(t):
    return (now - datetime.datetime.fromisoformat(t)).total_seconds() / 3600


def check(path, fn):
    try:
        fn(get(path))
    except Exception as e:
        problems.append(f'{SITE}{path} could not be read: {type(e).__name__}: {str(e)[:150]}')


def health(h):
    age = hours(h['checked_at'])
    if age > 0.5:
        problems.append(f'The server self-check last ran {age:.1f} hours ago (it should run every 5 minutes; auto-deploy may be failing).')
    for name, t in h.get('tls', {}).items():
        if not t.get('ok'):
            problems.append(f'HTTPS fails on {name}: {t.get("error")} (browsers show ERR_SSL_VERSION_OR_CIPHER_MISMATCH or a certificate error).')
        elif t.get('key') and t['key'] != 'RSA':
            problems.append(f'{name} serves an {t["key"]} certificate; company networks with HTTPS inspection may fail with ERR_SSL_VERSION_OR_CIPHER_MISMATCH (Caddy global option key_type rsa2048 missing?).')
        elif t.get('days_left', 99) < 14:
            problems.append(f'The certificate for {name} expires in {t["days_left"]} days and has not been renewed.')
    for unit, st in h.get('services', {}).items():
        if st != 'active':
            problems.append(f'Service {unit} on the server is "{st}".')
    if h.get('disk_free_pct', 100) < 10:
        problems.append(f'The server disk is almost full: {h["disk_free_pct"]} % free.')


def latest(L):
    age = hours(L['run_at'])
    if age > 8:
        problems.append(f'The page data was last rebuilt {age:.0f} hours ago (it should be every 6 hours or less).')
    st = [s for s in L.get('status', {}).values() if s.get('src') == 'google']
    cal = max((s['at'] for s in st if s.get('at')), default=None)
    if not cal:
        problems.append('No Google prices on the server.')
    elif hours(cal) > 30:
        problems.append(f'No new Google prices for {hours(cal):.0f} hours (the price task runs twice a day).')
    tm = max((s['times_at'] for s in st if s.get('times_at')), default=None)
    if tm and hours(tm) > 54:
        problems.append(f'No new flight times for {hours(tm):.0f} hours (some routes should get them on each price run).')


def anywhere(A):
    if not A.get('updated'):
        problems.append('No Anywhere prices on the server.')
    elif hours(A['updated']) > 36:
        problems.append(f'Anywhere prices last updated {hours(A["updated"]):.0f} hours ago (the scan runs daily).')


def ryanair(F):
    if not F.get('updated'):
        problems.append('No Ryanair fares on the server.')
    elif hours(F['updated']) > 18:
        problems.append(f'Ryanair fares last updated {hours(F["updated"]):.0f} hours ago (the server fetches them 4 times a day; see /opt/flight-watch/ryanair.log).')


check('/data/health.json', health)
check('/data/latest.json', latest)
check('/data/anywhere.json', anywhere)
check('/data/fr.json', ryanair)
print('\n'.join(problems) if problems else 'OK')
sys.exit(1 if problems else 0)
