"""Build the data files for the Flight Watch page (home airports <-> each destination in dests.json).

Usage: python3 build.py <cal_dir> <tp.json> <data_dir>
- cal_dir: Google Flights calendar files "<FROM>-<TO>-<any|direct>.txt" (line 1 start date, then prices/10 per day, 0 = none)
- tp.json: Travelpayouts output from tp.py
- data_dir: the page's data folder; previous latest.json / changes.json / board.json / lows.json are read from here and updated
Routes with no Google calendar yet (a destination that was just added) take the Anywhere scan's Google prices
(data/anywhere.json, about 140 destinations, 6 months) when it has the route, so they show at once; latest.json
"src" marks those "anywhere". Otherwise they get a calendar made from the Travelpayouts prices; "src" = "tp".
"""
import json, os, re, sys, glob, datetime, urllib.request

cal_dir, tp_file, data = sys.argv[1:4]
os.makedirs(os.path.join(data, 'runs'), exist_ok=True)
now = datetime.datetime.now(datetime.timezone.utc)
run_at = now.isoformat(timespec='seconds')
today = datetime.date.today()
APP = os.path.dirname(os.path.abspath(__file__))
HOME = ['CPH', 'BLL', 'AAR', 'AAL', 'HAM']
try:
    DESTS = json.load(open(os.path.join(APP, 'dests.json'))) or []
except Exception:
    DESTS = []
DESTS = DESTS or [{'code': 'KEF', 'name': 'Keflavík', 'place': 'Iceland'}]
CODES = [d['code'] for d in DESTS]
ROUTES = [f'{a}-{D}' for D in CODES for a in HOME] + [f'{D}-{a}' for D in CODES for a in HOME]


def load(name, default):
    p = os.path.join(data, name)
    return json.load(open(p)) if os.path.exists(p) else default


# ---- Google Flights calendars ----
cal = {}
for f in sorted(glob.glob(os.path.join(cal_dir, '*.txt'))):
    o, d, kind = os.path.basename(f)[:-4].split('-')
    if f'{o}-{d}' not in ROUTES:
        continue  # a destination that was removed
    lines = open(f).read().split('\n')
    start = lines[0].strip()
    vals = [int(x) * 10 for x in ' '.join(lines[1:]).split()]
    cal.setdefault(f'{o}-{d}', {})[kind] = {'start': start, 'p': vals}


def by_date(c):
    s = datetime.date.fromisoformat(c['start'])
    return {str(s + datetime.timedelta(days=i)): v for i, v in enumerate(c['p']) if v}


# ---- lowest seen + changes vs previous run ----
prev = load('latest.json', {})
lows = load('lows.json', {})
changes = load('changes.json', [])
for route, kinds in cal.items():
    for kind, c in kinds.items():
        k = f'{route}-{kind}'
        cur = by_date(c)
        lo = lows.setdefault(k, {})
        for dt, v in cur.items():
            if dt not in lo or v < lo[dt]:
                lo[dt] = v
        old_c = prev.get('cal', {}).get(route, {}).get(kind)
        if prev.get('src', {}).get(route) == 'tp':
            old_c = None  # last time this route only had Travelpayouts prices: not comparable
        if old_c:
            old = by_date(old_c)
            for dt, v in cur.items():
                ov = old.get(dt)
                if dt < str(today):
                    continue
                if ov is None:
                    if old_c['start'] <= dt:
                        changes.append(dict(seen=run_at, route=route, kind=kind, date=dt, old=None, new=v, what='New flights on sale'))
                elif abs(v - ov) >= 50:
                    changes.append(dict(seen=run_at, route=route, kind=kind, date=dt, old=ov, new=v, what='Price drop' if v < ov else 'Price rise'))
            for dt, ov in old.items():
                if dt >= str(today) and dt not in cur and dt <= max(cur or [dt]):
                    changes.append(dict(seen=run_at, route=route, kind=kind, date=dt, old=ov, new=None, what='No longer offered'))
# trim old lows / changes
for k in lows:
    lows[k] = {d: v for d, v in lows[k].items() if d >= str(today)}
cutoff = str(today - datetime.timedelta(days=180))
changes = [c for c in changes if c['seen'][:10] >= cutoff]

# ---- Travelpayouts ----
tp = json.load(open(tp_file)) if os.path.exists(tp_file) else {'ow': [], 'rt': []}
NAMES = {'SK': 'Scandinavian Airlines', 'FR': 'Ryanair', 'D8': 'Norwegian', 'DY': 'Norwegian', 'U2': 'easyJet',
         'W6': 'Wizz Air', 'FI': 'Icelandair', 'LS': 'Jet2', 'KL': 'KLM', 'HV': 'Transavia', 'AY': 'Finnair',
         'BA': 'British Airways', 'BT': 'Air Baltic', 'EW': 'Eurowings', 'LH': 'Lufthansa', 'AF': 'Air France',
         'CA': 'Air China', 'OG': 'PLAY', 'NY': 'Icelandair', 'WK': 'Edelweiss', 'LX': 'Swiss', 'SN': 'Brussels Airlines',
         'DE': 'Condor', 'TO': 'Transavia France', 'EI': 'Aer Lingus', 'LO': 'LOT', 'X3': 'TUIfly', 'DX': 'DAT'}
for x in tp['ow'] + tp['rt']:
    x['al'] = NAMES.get(x['al'], x['al'])
tp['ow'] = [x for x in tp['ow'] if x['date'] >= str(today)]
tp['rt'] = [x for x in tp['rt'] if x['out'] >= str(today)]

# ---- Keflavik flight board (cancellations / delays), only while Keflavík is a destination ----
board = load('board.json', [])
CITY = {'Copenhagen': 'CPH', 'Billund': 'BLL', 'Aarhus': 'AAR', 'Aalborg': 'AAL', 'Hamburg': 'HAM'}
try:
    if 'KEF' not in CODES:
        raise RuntimeError('Keflavík is not a destination, board not read')
    s = urllib.request.urlopen(urllib.request.Request('https://www.kefairport.com/flights/departures',
                                                      headers={'User-Agent': 'curl/8.5'}), timeout=30).read().decode()
    seen = {}
    for r in re.findall(r'<tr[^>]*>(.*?)</tr>', s, re.S)[1:]:
        t = [x.strip() for x in re.sub(r'<[^>]+>', '\x00', r).split('\x00') if x.strip()]
        if '→' not in t:
            continue
        i = t.index('→')
        city = t[i + 1]
        if city not in CITY:
            continue
        flight, status = t[i + 2], t[-1]
        j = len(t) - 1 - t[::-1].index(flight)
        airline = NAMES.get(t[j - 1], {'SAS': 'Scandinavian Airlines'}.get(t[j - 1], t[j - 1]))
        st = status.lower()
        if any(k in st for k in ['landed', 'bags', 'arrived']):
            direction = 'to KEF'
        elif any(k in st for k in ['departed', 'boarding', 'gate', 'final call']):
            direction = 'from KEF'
        else:
            n = re.sub(r'\D', '', flight)
            direction = 'to KEF' if n and int(n) % 2 else 'from KEF'
        est = t[2] if len(t) > 2 and re.match(r'\d\d:\d\d$', t[2]) else ''
        seen[flight] = dict(day=str(today), dir=direction, apt=CITY[city], flight=flight, airline=airline,
                            sched=t[0], est=est, status=status)
    keep = {(b['day'], b['flight']): b for b in board}
    for b in seen.values():
        keep[(b['day'], b['flight'])] = b  # latest status of the day wins
    board = sorted(keep.values(), key=lambda b: (b['day'], b['sched']))
except Exception as e:
    print('board error', e)

# flight times / airlines per day (from the day-by-day search), {route: {day: {any:{p,dep,arr,arr_day,min,stops,al}, direct:{...}}}}
det = {}
for f in glob.glob(os.path.join(cal_dir, '*-details.json')):
    o, d, _ = os.path.basename(f).split('-')
    if f'{o}-{d}' not in ROUTES:
        continue
    det[f'{o}-{d}'] = {k: v for k, v in json.load(open(f)).items() if k >= str(today)}

# ---- Travelpayouts calendars for routes with no Google calendar yet ----
gdet = set(det)  # routes with flight times from Google's day-by-day search
gcal = cal  # Google only: stored in runs/ for the patterns
cal = dict(gcal)
src = {}
any_at = {}
try:
    A = json.load(open(os.path.join(data, 'anywhere.json')))
except Exception:
    A = {}
for route in ROUTES:
    c = (A.get('cal') or {}).get(route)
    if route in gcal or not c or not any(c.get('p') or []):
        continue
    src[route], any_at[route] = 'anywhere', c.get('at')
    cal[route] = {'any': {'start': c['start'], 'p': c['p']}}
    dc = (A.get('dcal') or {}).get(route)
    if dc and any(dc.get('p') or []):
        cal[route]['direct'] = {'start': dc['start'], 'p': dc['p']}
for route in ROUTES:
    o, d = route.split('-')
    rows = [x for x in tp['ow'] if x['o'] == o and x['d'] == d]
    if route in cal or not rows:
        continue
    src[route] = 'tp'
    cal[route] = {}
    det.setdefault(route, {})
    for kind in ['any', 'direct']:
        best = {}
        for x in rows:
            if kind == 'direct' and x.get('stops'):
                continue
            if x['date'] not in best or x['p'] < best[x['date']]['p']:
                best[x['date']] = x
        if not best:
            continue
        ks = sorted(best)
        d0 = datetime.date.fromisoformat(ks[0])
        n = (datetime.date.fromisoformat(ks[-1]) - d0).days + 1
        cal[route][kind] = {'start': ks[0], 'p': [round(best[str(d0 + datetime.timedelta(days=i))]['p'] / 10) * 10
                                                   if str(d0 + datetime.timedelta(days=i)) in best else 0 for i in range(n)]}
        for day, x in best.items():
            det[route].setdefault(day, {})[kind] = {'p': x['p'], 'dep': '', 'arr': '', 'arr_day': '', 'min': x.get('min'),
                                                    'stops': x.get('stops'), 'al': x.get('al', '')}
    if not cal[route]:
        del cal[route], src[route]

# ---- status per route, for the page's airport list ----
def mtime(path):
    return datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).isoformat(timespec='seconds') if os.path.exists(path) else None


status = {}
for route in ROUTES:
    if route not in cal:
        status[route] = {'src': None}
        continue
    days = {k: sum(1 for v in c['p'] if v) for k, c in cal[route].items()}
    D = route.split('-')[1] if route.split('-')[0] in HOME else route.split('-')[0]
    at = any_at.get(route) if src.get(route) == 'anywhere' else mtime(os.path.join(APP, 'tp', D + '.json')) if route in src \
        else mtime(os.path.join(cal_dir, route + '-any.txt'))
    status[route] = {'src': src.get(route, 'google'), 'any': days.get('any', 0), 'direct': days.get('direct', 0),
                     'times': route in gdet, 'times_at': mtime(os.path.join(cal_dir, route + '-details.json')) if route in gdet else None,
                     'at': at}

latest = {'run_at': run_at, 'cal': cal, 'det': det, 'src': src, 'status': status, 'dests': DESTS, 'tp_ow': tp['ow'], 'tp_rt': tp['rt'],
          'prev_run_at': prev.get('run_at'),
          # previous prices, only where they came from the same source (Google vs Travelpayouts)
          'prev_cal': {r: v for r, v in prev.get('cal', {}).items() if prev.get('src', {}).get(r) == src.get(r)}}


def dump(name, obj):
    json.dump(obj, open(os.path.join(data, name), 'w'), separators=(',', ':'))


dump('latest.json', latest)
dump('lows.json', lows)
dump('changes.json', changes)
dump('board.json', board)
dump('dests.json', DESTS)
# Archive this check only when the Google prices differ from the last archived check: the server also
# rebuilds between uploads (timer, new destination), and identical copies would grow the archive forever.
runs = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(data, 'runs', '*.json')))
try:
    last_cal = json.load(open(os.path.join(data, 'runs', runs[-1] + '.json'))).get('cal') if runs else None
except Exception:
    last_cal = None
if last_cal != gcal:
    dump(f'runs/{now.strftime("%Y-%m-%dT%H%M")}.json', {'run_at': run_at, 'cal': gcal, 'tp': tp})
    runs = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(data, 'runs', '*.json')))
dump('index.json', {'runs': runs, 'last': run_at})
print('ok', run_at, {k: list(v) + (['tp'] if k in src else []) for k, v in cal.items()}, len(tp['ow']), 'tp one-way', len(tp['rt']), 'tp round trips',
      len(changes), 'changes', len(board), 'board rows')


# ---- summary for the daily notification ----
def cheapest_trip(kind, D):
    best = None
    for o in HOME:
        A = cal.get(f'{o}-{D}', {}).get(kind)
        if not A:
            continue
        A = by_date(A)
        for h in HOME:
            B = cal.get(f'{D}-{h}', {}).get(kind)
            if not B:
                continue
            B = sorted(by_date(B).items())
            for d1, p1 in A.items():
                if d1 < str(today):
                    continue
                for d2, p2 in B:
                    n = (datetime.date.fromisoformat(d2) - datetime.date.fromisoformat(d1)).days
                    if 30 <= n <= 183 and (best is None or p1 + p2 < best['total']):
                        best = dict(total=p1 + p2, out=o, d1=d1, home=h, d2=d2)
    return best


old_sum = load('summary.json', {})
# the first destination on the list (Keflavík unless it was changed)
summ = {'run_at': run_at, 'dest': CODES[0], 'any': cheapest_trip('any', CODES[0]), 'direct': cheapest_trip('direct', CODES[0]),
        'drops': sorted([c for c in changes if c['seen'] == run_at and c['what'] == 'Price drop' and CODES[0] in c['route']],
                        key=lambda c: c['new'] - c['old'])[:10]}
for k in ['any', 'direct']:
    o = (old_sum.get(k) or {}).get('total') if old_sum.get('dest', 'KEF') == CODES[0] else None
    summ[k + '_before'] = o
dump('summary.json', summ)
print('SUMMARY', json.dumps(summ))

# ---- price watches ----
import watches
ws = watches.update(os.environ.get('WATCH_FILE', 'watches.json'), data, run_at, cal)
print('watches', len(ws))

# ---- price patterns ----
import patterns
pt = patterns.build(data, cal, CODES)
print('patterns from', pt['checks'], 'checks')
