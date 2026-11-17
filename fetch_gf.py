"""Google Flights price calendars via the open-source `fli` tool (pip install flights).

Writes cal/<FROM>-<TO>-<any|direct>.txt for each route: line 1 = start date,
then one number per day = cheapest one-way price in DKK / 10 (0 = no flight).

Routes: the home airports to and from each destination on the page's list (data/dests.json, or DESTS=KEF,AGP).
Routes the server has no Google prices for yet (a destination that was just added) go first, and with
UPLOAD_TOKEN set their calendars are uploaded as soon as they are done, before the slow day-by-day pass.

Time limit GF_MINUTES (default 90): the calendar pass goes route by route (new routes, then the ones
whose calendar on the server is oldest), skips routes whose server calendar is under GF_FRESH_HOURS (default 10)
hours old, uploads every 5 routes (merge: the server keeps the rest) and stops after GF_CAL_MINUTES (default 55)
so time is left for flight times; the routes it did not reach keep their earlier calendars until a later run.
Then, until GF_MINUTES, the day-by-day pass (flight times, days the calendar missed) runs for the next GF_DAY_DAYS days
(default 60), routes with the oldest flight times first,
and uploads each route when it is done. Routes it does not reach keep their earlier flight times and get
them on a later run.

--new-only: only the routes with no Google prices on the server yet, calendars only (a few minutes),
uploaded at once; prints "nothing new" and stops if there are none. For a destination that was just added.
Usage: python3 fetch_gf.py [--new-only] [folder]
"""
import json, os, sys, glob, datetime, subprocess, time, urllib.request

FLI = os.environ.get('FLI', 'fli')
T0 = time.time()
MINUTES = float(os.environ.get('GF_MINUTES', 90))
DAY_DAYS = int(os.environ.get('GF_DAY_DAYS', 60))
CAL_MINUTES = float(os.environ.get('GF_CAL_MINUTES', 55))
FRESH_HOURS = float(os.environ.get('GF_FRESH_HOURS', 10))
NEW_ONLY = '--new-only' in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith('--')]
final = args[0] if args else ('calnew' if NEW_ONLY else 'cal')
out = final + '.new'
os.makedirs(out, exist_ok=True)
# Resume support: progress.json in the work folder remembers what is done, so a restarted session
# continues where it stopped (within 20 hours) instead of starting over.
prog_path = os.path.join(out, 'progress.json')
prog = json.load(open(prog_path)) if os.path.exists(prog_path) else {}
now = datetime.datetime.now(datetime.timezone.utc)
fresh = NEW_ONLY or not prog or (now - datetime.datetime.fromisoformat(prog['started'])).total_seconds() > 20 * 3600
if fresh:
    for f in glob.glob(os.path.join(out, '*.txt')) + glob.glob(os.path.join(out, '*.json')):
        os.remove(f)
    today = datetime.date.today()
    prog = {'started': now.isoformat(), 'start': str(today + datetime.timedelta(days=1)),
            'end': str(today + datetime.timedelta(days=330)), 'cal_done': [], 'day_done': []}
else:
    print('resuming run started', prog['started'], 'done:', prog['cal_done'], prog['day_done'], flush=True)


def save_prog():
    json.dump(prog, open(prog_path, 'w'))


save_prog()
start, end = datetime.date.fromisoformat(prog['start']), datetime.date.fromisoformat(prog['end'])
# The server answers on both names; cloud network settings only accept plain-letter names, so try that first.
SITES = [os.environ['SITE']] if os.environ.get('SITE') else ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']


def pick_site():
    for u in SITES:
        try:
            urllib.request.urlopen(urllib.request.Request(u + '/data/index.json', headers={'User-Agent': 'flight-watch-fetch'}), timeout=20)
            return u
        except Exception as e:
            print('site not reachable:', u, e, flush=True)
    return SITES[-1]


SITE = pick_site()
HOME = ['CPH', 'BLL', 'AAR', 'AAL', 'HAM']


def site_json(path):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(SITE + path, headers={'User-Agent': 'flight-watch-fetch'}), timeout=60))
    except Exception as e:
        print('could not read', path, e, flush=True)
        return None


if os.environ.get('DESTS'):
    DESTS = [x.strip().upper() for x in os.environ['DESTS'].split(',') if x.strip()]
else:
    DESTS = [d['code'] for d in (site_json('/data/dests.json') or [])] or ['KEF']
routes = [(a, D) for D in DESTS for a in HOME] + [(D, a) for D in DESTS for a in HOME]
# routes the server has no Google calendar for yet: first in line
latest = site_json('/data/latest.json') or {}
have = {r for r in latest.get('cal', {}) if latest.get('src', {}).get(r) != 'tp'}
if latest:
    new = [r for r in routes if f'{r[0]}-{r[1]}' not in have]
    st = latest.get('status', {})
    routes = new + sorted([r for r in routes if r not in new], key=lambda r: st.get(f'{r[0]}-{r[1]}', {}).get('at') or '')
else:
    new = []
det_srv = latest.get('det', {})  # flight times/prices per day the server already has
status_srv = latest.get('status', {})
print('destinations', DESTS, '- new routes first:', [f'{o}-{d}' for o, d in new], flush=True)
if NEW_ONLY:
    if not new:
        print('nothing new')
        sys.exit(0)
    if not os.environ.get('UPLOAD_TOKEN'):
        sys.exit('--new-only needs UPLOAD_TOKEN')
    routes = new


def send(rts, what, details=False):
    """Upload the files of these routes now (merge: the server keeps all other files)."""
    if not rts or not os.environ.get('UPLOAD_TOKEN'):
        return
    files = {}
    for o, d in rts:
        for kind in ['any', 'direct'] + (['details'] if details else []):
            p = os.path.join(out, f'{o}-{d}-{kind}.' + ('json' if kind == 'details' else 'txt'))
            if os.path.exists(p):
                files[os.path.basename(p)] = open(p).read()
    if not files:
        return
    req = urllib.request.Request(SITE + '/api/cal', data=json.dumps({'files': files, 'merge': True}).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Upload-Token': os.environ['UPLOAD_TOKEN'],
                                          'User-Agent': 'flight-watch-upload'}, method='POST')
    for attempt in range(3):
        try:
            print(what, urllib.request.urlopen(req, timeout=120).read().decode(), flush=True)
            return
        except Exception as e:
            print(what, 'failed', e, flush=True)
            time.sleep(30)


def quick_upload():
    send(new, 'quick upload of new routes:')


def over(extra=0):
    return time.time() - T0 > MINUTES * 60 + extra


def cal_fresh(r):
    """The server's Google calendar for this route is under FRESH_HOURS old: no need to fetch it again."""
    at = status_srv.get(f'{r[0]}-{r[1]}', {}).get('at')
    if not at or r in new or status_srv.get(f'{r[0]}-{r[1]}', {}).get('src') != 'google':
        return False
    try:
        return (now - datetime.datetime.fromisoformat(at)).total_seconds() < FRESH_HOURS * 3600
    except Exception:
        return False


unsent = []
for i_route, (o, d) in enumerate(routes):
    if i_route == len(new) and not prog.get('quick_sent'):
        quick_upload(); prog['quick_sent'] = True; save_prog()
    if not NEW_ONLY and time.time() - T0 > CAL_MINUTES * 60:
        left = [f'{a}-{b}' for a, b in routes[i_route:] if not cal_fresh((a, b))]
        print(f'calendar time ({CAL_MINUTES:.0f} min) used; {len(left)} routes keep their earlier calendars:', left, flush=True)
        break
    if not NEW_ONLY and cal_fresh((o, d)):
        print(o, d, f'calendar under {FRESH_HOURS:.0f} h old on the server, skipped', flush=True)
        continue
    if not NEW_ONLY and i_route >= len(new):
        unsent.append((o, d))
        if len(unsent) > 5:
            send(unsent[:-1], 'calendars sent:'); unsent = unsent[-1:]
    for kind, stops in [('any', 'ANY'), ('direct', '0')]:
        if f'{o}-{d}-{kind}' in prog['cal_done']:
            continue
        prices = {}
        js = None
        for attempt in range(4):
            r = subprocess.run([FLI, 'dates', o, d, '--from', str(start), '--to', str(end), '--stops', stops,
                                '--currency', 'DKK', '--format', 'json'], capture_output=True, text=True, timeout=300)
            try:
                js = json.loads(r.stdout)
            except Exception:
                js = None
            if js and js.get('success'):
                break
            time.sleep(30 * (attempt + 1))  # Google rate limit (HTTP 429): wait and retry
        if js and js.get('success'):
            for x in js.get('dates', []):
                if x.get('price'):
                    prices[x['departure_date']] = round(x['price'] / 10)
        else:
            print('fli failed', o, d, kind, (r.stderr or r.stdout)[:200], '- the server keeps its earlier calendar', flush=True)
            prog['cal_done'].append(f'{o}-{d}-{kind}'); save_prog()
            continue
        time.sleep(float(os.environ.get("GF_PAUSE", 5)))
        # days the calendar search skipped: use the price from the server's last day-by-day search
        for day, v in det_srv.get(f'{o}-{d}', {}).items():
            if kind in v and day not in prices and str(start) <= day <= str(end):
                prices[day] = round(v[kind]['p'] / 10)
        if not prices:
            print(o, d, kind, 'no flights')
            prog['cal_done'].append(f'{o}-{d}-{kind}'); save_prog()
            continue
        ks = sorted(prices)
        d0 = datetime.date.fromisoformat(ks[0])
        n = (datetime.date.fromisoformat(ks[-1]) - d0).days + 1
        vals = [prices.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(n)]
        with open(os.path.join(out, f'{o}-{d}-{kind}.txt'), 'w') as f:
            f.write(ks[0] + '\n' + ' '.join(map(str, vals)) + '\n')
        print(o, d, kind, ks[0], n, 'days', sum(1 for v in vals if v), 'priced')
        prog['cal_done'].append(f'{o}-{d}-{kind}'); save_prog()
if len(new) == len(routes) and not prog.get('quick_sent'):
    quick_upload(); prog['quick_sent'] = True; save_prog()

if NEW_ONLY:
    print('new routes done')
    sys.exit(0)
send(unsent, 'calendars sent:')

# ---- Fill gaps day by day for the smaller airports ----
# fli's calendar search skips many days on thin routes (Billund, Aalborg, Hamburg),
# so ask Google about each day: one search gives both the cheapest price and the cheapest direct price.
from concurrent.futures import ThreadPoolExecutor


def pick(f):
    legs = f.get('legs') or []
    al = []
    for l in legs:
        n = (l.get('airline') or {}).get('name') or (l.get('airline') or {}).get('code')
        if n and n not in al:
            al.append(n)
    return {'p': round(f['price']), 'dep': (legs[0].get('departure_time') or '')[11:16] if legs else '',
            'arr': (legs[-1].get('arrival_time') or '')[11:16] if legs else '',
            'arr_day': (legs[-1].get('arrival_time') or '')[:10] if legs else '',
            'min': f.get('duration'), 'stops': f.get('stops', len(legs) - 1), 'al': ', '.join(al)}


def day_search(args):
    o, d, day = args
    for attempt in range(4):
        if over(15 * 60):
            return day, 'stop', None  # well past the time limit: give up this route
        r = subprocess.run([FLI, 'flights', o, d, day, '--stops', 'ANY', '--currency', 'DKK', '--format', 'json'],
                           capture_output=True, text=True, timeout=120)
        try:
            js = json.loads(r.stdout)
        except Exception:
            js = None
        if js and js.get('success'):
            fl = [f for f in js.get('flights', []) if f.get('price')]
            if not fl and attempt < 1:
                time.sleep(8)  # Google sometimes answers with an empty list; ask again before trusting it
                continue
            best = min(fl, key=lambda f: f['price'], default=None)
            bestd = min((f for f in fl if f.get('stops', 9) == 0), key=lambda f: f['price'], default=None)
            return day, (pick(best) if best else None), (pick(bestd) if bestd else None)
        time.sleep(20 * (attempt + 1))
    return day, None, None


def write(o, d, kind, prices):
    if not prices:
        return
    ks = sorted(prices)
    d0 = datetime.date.fromisoformat(ks[0])
    n = (datetime.date.fromisoformat(ks[-1]) - d0).days + 1
    vals = [prices.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(n)]
    with open(os.path.join(out, f'{o}-{d}-{kind}.txt'), 'w') as f:
        f.write(ks[0] + '\n' + ' '.join(map(str, vals)) + '\n')


days = [str(start + datetime.timedelta(days=i)) for i in range(min(DAY_DAYS, (end - start).days + 1))]


def read_cal(o, d, kind):
    p = os.path.join(out, f'{o}-{d}-{kind}.txt')
    if not os.path.exists(p):  # no calendar this run (search failed): start from the server's
        c = latest.get('cal', {}).get(f'{o}-{d}', {}).get(kind) if latest.get('src', {}).get(f'{o}-{d}') != 'tp' else None
        if not c:
            return {}
        s0 = datetime.date.fromisoformat(c['start'])
        return {str(s0 + datetime.timedelta(days=i)): round(v / 10) for i, v in enumerate(c['p'])  # server keeps DKK, files DKK / 10
                if v and str(start) <= str(s0 + datetime.timedelta(days=i)) <= str(end)}
    lines = open(p).read().split('\n')
    s0 = datetime.date.fromisoformat(lines[0].strip())
    return {str(s0 + datetime.timedelta(days=i)): int(v) for i, v in enumerate(' '.join(lines[1:]).split()) if int(v)}


def times_age(r):
    s = status_srv.get(f'{r[0]}-{r[1]}', {})
    return (s.get('times_at') or '0') if s.get('times') else ''  # no flight times first, then the oldest


day_routes = sorted(routes, key=times_age)
for o, d in day_routes:
    if f'{o}-{d}' in prog['day_done']:
        continue
    if over():
        left = [f'{a}-{b}' for a, b in day_routes if f'{a}-{b}' not in prog['day_done']]
        print(f'time limit ({MINUTES:.0f} min) reached; flight times for {len(left)} routes left for the next runs:', left, flush=True)
        break
    t0 = time.time()
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(day_search, [(o, d, day) for day in days]))
    if any(a == 'stop' for _, a, _ in res):
        print(o, d, 'day by day stopped at the time limit; left for the next runs', flush=True)
        break
    det = {day: {k: v for k, v in (('any', a), ('direct', x)) if v} for day, a, x in res if a or x}
    failed = sum(1 for _, a, x in res if a is None and x is None)
    # prices: day-by-day result, with the calendar price as fallback for days the day search missed
    for kind in ['any', 'direct']:
        prices = read_cal(o, d, kind)
        for day, v in det.items():
            if kind in v:
                prices[day] = round(v[kind]['p'] / 10)
        if prices:
            write(o, d, kind, prices)
    # keep the server's flight times for the days after the searched ones
    old = {k: v for k, v in det_srv.get(f'{o}-{d}', {}).items() if k > days[-1] and k <= str(end)}
    with open(os.path.join(out, f'{o}-{d}-details.json'), 'w') as f:
        json.dump({**old, **det}, f, separators=(',', ':'), sort_keys=True)
    print(o, d, 'day by day:', len(det), 'days with flights,', sum(1 for v in det.values() if 'direct' in v), 'direct,',
          failed, 'empty/failed,', int(time.time() - t0), 's', flush=True)
    prog['day_done'].append(f'{o}-{d}'); save_prog()
    send([(o, d)], f'{o}-{d} sent:', details=True)

# Everything was uploaded as a merge already; cal/ holds this run's files for upload.py.
os.makedirs(final, exist_ok=True)
for f in glob.glob(os.path.join(final, '*.txt')):
    os.remove(f)
for f in glob.glob(os.path.join(final, '*.json')):
    os.remove(f)
os.remove(prog_path)  # finished: next run starts fresh
for f in glob.glob(os.path.join(out, '*.txt')) + glob.glob(os.path.join(out, '*-details.json')):
    os.replace(f, os.path.join(final, os.path.basename(f)))
print('calendars updated')
