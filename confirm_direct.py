"""Confirm which Anywhere routes really have direct flights and which really have none.

Google's calendar search often answers with an empty list although the route has flights (18 Nov: CPH-LHR direct,
which has daily direct flights, came back empty 8 times in a row). So an empty direct calendar proves nothing.
This script checks each route with no confirmed answer on the site the other way: `fli flights` lists every flight
on one day with its number of stops, and a non-empty list can be trusted.

- Sample days: up to 4 days spread over the next 6 months, on days the all-prices calendar has a fare.
- A sampled day with a nonstop flight: the route has direct flights. Its direct-only calendar is then fetched
  (asked again while Google answers empty) and sent as "dcal".
- At least 3 sampled days with full flight lists and no nonstop flight: confirmed no direct flights, sent as "nod".
- Otherwise (Google kept answering empty): left unconfirmed for the next run.

Routes: those in data/anywhere.json with neither direct prices nor a confirmed "no direct"; followed airports first,
then in the scan's destination order. Stops after CONFIRM_MINUTES (default 100). Needs UPLOAD_TOKEN to send results.
Usage: UPLOAD_TOKEN=... python3 confirm_direct.py [ROUTE ...]   (CONFIRM_PART=1/2 and 2/2 split the list between two machines)
"""
import datetime, json, os, subprocess, sys, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from jobreport import Job

FLI = os.environ.get('FLI', 'fli')
MINUTES = float(os.environ.get('CONFIRM_MINUTES', 100))
THREADS = int(os.environ.get('CONFIRM_THREADS', 3))
TOKEN = os.environ.get('UPLOAD_TOKEN', '').strip()
SITES = [os.environ['SITE']] if os.environ.get('SITE') else ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']
HOME = ['CPH', 'BLL', 'AAR', 'AAL', 'HAM', 'KEF']
T0 = time.time()
today = datetime.date.today()


def site_json(path):
    for site in SITES:
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(site + path, headers={'User-Agent': 'flight-watch-confirm'}), timeout=60))
        except Exception as e:
            print('could not read', site + path, e, flush=True)
    return None


def fli(args, tries):
    """Run fli until it answers with a non-empty list (Google's empty answers are not trusted); None = only empty/failed."""
    for i in range(tries):
        try:
            r = subprocess.run([FLI, *args, '--currency', 'DKK', '--format', 'json'], capture_output=True, text=True, timeout=90)
            js = json.loads(r.stdout)
            xs = js.get('flights') if 'flights' in js else js.get('dates')
            if js.get('success') and xs:
                return xs
        except Exception:
            pass
        time.sleep(3 + 3 * i)
    return None


def sample_days(c, n=4):
    s0 = datetime.date.fromisoformat(c['start'])
    days = [str(s0 + datetime.timedelta(days=i)) for i, v in enumerate(c['p']) if v and s0 + datetime.timedelta(days=i) > today]
    if not days:
        return []
    return sorted({days[min(len(days) - 1, int(len(days) * q))] for q in (0.1, 0.35, 0.6, 0.85)})[:n]


def direct_calendar(o, d):
    wins = [(today + datetime.timedelta(days=1 + 61 * i), today + datetime.timedelta(days=61 * (i + 1))) for i in range(3)]
    m = {}
    for a, b in wins:
        xs = fli(['dates', o, d, '--from', str(a), '--to', str(b), '--stops', '0'], 6) or []
        for x in xs:
            if x.get('price'):
                m[x['departure_date']] = round(x['price'])
    if not m:
        return None
    d0 = datetime.date.fromisoformat(min(m))
    n = (datetime.date.fromisoformat(max(m)) - d0).days + 1
    return {'start': str(d0), 'p': [m.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(n)]}


def check(route, c):
    """('direct', dcal) | ('none', {days, flights}) | ('unknown', reason)"""
    o, d = route.split('-')
    seen, flights = 0, 0
    for day in sample_days(c):
        fl = fli(['flights', o, d, day, '--stops', 'ANY'], 4)
        if fl is None:
            continue
        seen += 1
        flights += len(fl)
        if any(f.get('stops') == 0 for f in fl):
            cal = direct_calendar(o, d)
            return ('direct', cal) if cal else ('unknown', f'nonstop flight on {day}, but no direct calendar from Google')
    if seen >= 3:
        return 'none', {'days': seen, 'flights': flights}
    return 'unknown', f'only {seen} days answered'


def upload(body):
    if not TOKEN:
        return
    for site in SITES:
        req = urllib.request.Request(site + '/api/anywhere', data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Upload-Token': TOKEN, 'User-Agent': 'flight-watch-confirm'})
        try:
            print('upload', len(body['dcal']), 'direct,', len(body['nod']), 'no direct:', urllib.request.urlopen(req, timeout=60).read().decode(), flush=True)
            return
        except urllib.error.HTTPError as e:
            print('upload refused', e.code, flush=True)
            job.error(f'upload refused: HTTP {e.code}')
            return
        except Exception as e:
            print('could not reach', site, e, flush=True)
    job.error('upload failed: server not reachable')


A = site_json('/data/anywhere.json') or {}
cal, dcal, nod = A.get('cal', {}), A.get('dcal', {}), A.get('nod', {})
args = [a.upper() for a in sys.argv[1:] if not a.startswith('--')]
if args:
    todo = [r for r in args if r in cal]
else:
    followed = [x['code'] for x in (site_json('/data/dests.json') or [])]
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'anywhere.py')).read()
    order = src.split('DEST = """')[1].split('"""')[0].split()
    pos = {x: i for i, x in enumerate(followed + [x for x in order if x not in followed])}
    dest = lambda r: r.split('-')[1] if r.split('-')[0] in HOME else r.split('-')[0]
    todo = sorted((r for r in cal if r not in dcal and r not in nod), key=lambda r: (pos.get(dest(r), 9999), r))
if os.environ.get('CONFIRM_PART'):  # "1/2": every second route from the first, so two machines can share the list
    k, n = map(int, os.environ['CONFIRM_PART'].split('/'))
    todo = todo[k - 1::n]
print(len(todo), 'routes to confirm', flush=True)
job = Job('confirm' + ('-' + os.environ['CONFIRM_PART'].split('/')[0] if os.environ.get('CONFIRM_PART') else ''))
res = {'direct': 0, 'none': 0, 'unknown': 0, 'skipped': 0}
pend = {'dcal': {}, 'nod': {}}
lock = threading.Lock()


def work(r):
    global pend
    if time.time() - T0 > MINUTES * 60:
        with lock:
            res['skipped'] += 1
        return
    kind, v = check(r, cal[r])
    with lock:
        res[kind] += 1
        n = res['direct'] + res['none'] + res['unknown']
        print(n, '/', len(todo), r, kind, v if kind != 'direct' else f"{sum(1 for x in v['p'] if x)} direct days", flush=True)
        if kind == 'direct':
            pend['dcal'][r] = v
        elif kind == 'none':
            pend['nod'][r] = v
        if len(pend['dcal']) + len(pend['nod']) >= 10:
            upload(pend)
            pend = {'dcal': {}, 'nod': {}}
    job.step('confirm', n, len(todo), f"direct {res['direct']}, no direct {res['none']}, not confirmed {res['unknown']}")


with ThreadPoolExecutor(THREADS) as ex:
    list(ex.map(work, todo))
if pend['dcal'] or pend['nod']:
    upload(pend)
if res['skipped']:
    print(f"time limit ({MINUTES:.0f} min): {res['skipped']} routes left for the next run", flush=True)
print('confirmed:', res, flush=True)
job.finish('done', f"direct {res['direct']}, no direct {res['none']}, not confirmed {res['unknown']}, left for the next run {res['skipped']}, of {len(todo)} routes")
