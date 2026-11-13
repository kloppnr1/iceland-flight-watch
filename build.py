"""Build the data files for the Iceland Flight Watch page.

Usage: python3 build.py <cal_dir> <tp.json> <data_dir>
- cal_dir: Google Flights calendar files "<FROM>-<TO>-<any|direct>.txt" (line 1 start date, then prices/10 per day, 0 = none)
- tp.json: Travelpayouts output from tp.py
- data_dir: the page's data folder; previous latest.json / changes.json / board.json / lows.json are read from here and updated
"""
import json, os, re, sys, glob, datetime, urllib.request

cal_dir, tp_file, data = sys.argv[1:4]
os.makedirs(os.path.join(data, 'runs'), exist_ok=True)
now = datetime.datetime.now(datetime.timezone.utc)
run_at = now.isoformat(timespec='seconds')
today = datetime.date.today()


def load(name, default):
    p = os.path.join(data, name)
    return json.load(open(p)) if os.path.exists(p) else default


# ---- Google Flights calendars ----
cal = {}
for f in sorted(glob.glob(os.path.join(cal_dir, '*.txt'))):
    o, d, kind = os.path.basename(f)[:-4].split('-')
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

# ---- Keflavik flight board (cancellations / delays) ----
board = load('board.json', [])
CITY = {'Copenhagen': 'CPH', 'Billund': 'BLL', 'Aalborg': 'AAL', 'Hamburg': 'HAM'}
try:
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

latest = {'run_at': run_at, 'cal': cal, 'tp_ow': tp['ow'], 'tp_rt': tp['rt'],
          'prev_run_at': prev.get('run_at'), 'prev_cal': prev.get('cal', {})}


def dump(name, obj):
    json.dump(obj, open(os.path.join(data, name), 'w'), separators=(',', ':'))


dump('latest.json', latest)
dump('lows.json', lows)
dump('changes.json', changes)
dump('board.json', board)
dump(f'runs/{now.strftime("%Y-%m-%dT%H%M")}.json', {'run_at': run_at, 'cal': cal, 'tp': tp})
runs = sorted(os.path.basename(p)[:-5] for p in glob.glob(os.path.join(data, 'runs', '*.json')))
dump('index.json', {'runs': runs, 'last': run_at})
print('ok', run_at, {k: list(v) for k, v in cal.items()}, len(tp['ow']), 'tp one-way', len(tp['rt']), 'tp round trips',
      len(changes), 'changes', len(board), 'board rows')


# ---- summary for the daily notification ----
def cheapest_trip(kind):
    best = None
    for o in ['CPH', 'BLL', 'AAL', 'HAM']:
        A = cal.get(f'{o}-KEF', {}).get(kind)
        if not A:
            continue
        A = by_date(A)
        for h in ['CPH', 'BLL', 'AAL', 'HAM']:
            B = cal.get(f'KEF-{h}', {}).get(kind)
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
summ = {'run_at': run_at, 'any': cheapest_trip('any'), 'direct': cheapest_trip('direct'),
        'drops': sorted([c for c in changes if c['seen'] == run_at and c['what'] == 'Price drop'],
                        key=lambda c: c['new'] - c['old'])[:10]}
for k in ['any', 'direct']:
    o, n = (old_sum.get(k) or {}).get('total'), (summ[k] or {}).get('total')
    summ[k + '_before'] = o
dump('summary.json', summ)
print('SUMMARY', json.dumps(summ))
