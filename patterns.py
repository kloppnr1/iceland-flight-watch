"""Price patterns from the archive of price checks (data/runs/*.json).

Writes data/patterns.json:
- moves_by_days_before: for each "days before departure" bucket, how often the price of a flight day went
  down / up / stayed between two checks, and the average change in kr and %.
- moves_by_check_day: on which weekday (of the check) prices went down or up.
- by_travel_weekday: average cheapest price by weekday of travel, now (to / from the destination, any / direct).
The top level covers every route; by_dest has the same three parts for each destination.
Only checks with new Google prices count (the server also rebuilds between uploads with the same prices).
"""
import json, os, glob, datetime
from collections import defaultdict

BUCKETS = [(0, 7, '0–7 days'), (8, 14, '1–2 weeks'), (15, 30, '2–4 weeks'), (31, 60, '1–2 months'),
           (61, 90, '2–3 months'), (91, 180, '3–6 months'), (181, 999, '6+ months')]
WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def by_date(c):
    s = datetime.date.fromisoformat(c['start'])
    return {str(s + datetime.timedelta(days=i)): v for i, v in enumerate(c['p']) if v}


def bucket(n):
    for a, b, name in BUCKETS:
        if a <= n <= b:
            return name
    return None


def build(data_dir, latest_cal, codes=None):
    runs = []
    for f in sorted(glob.glob(os.path.join(data_dir, 'runs', '*.json'))):
        try:
            r = json.load(open(f))
        except Exception:
            continue
        runs.append(r)
    # keep only runs whose Google prices differ from the run before (new upload)
    uniq, last_sig = [], None
    for r in runs:
        sig = json.dumps(r.get('cal', {}), sort_keys=True)
        if sig != last_sig:
            uniq.append(r)
            last_sig = sig
    codes = codes or ['KEF']
    res = {'checks': len(uniq), 'since': uniq[0]['run_at'] if uniq else None,
           **stats(uniq, latest_cal, codes),
           'by_dest': {D: stats(uniq, latest_cal, [D]) for D in codes}}
    json.dump(res, open(os.path.join(data_dir, 'patterns.json'), 'w'), separators=(',', ':'))
    return res


def stats(uniq, latest_cal, codes):
    """The three patterns for the routes to and from the destinations in codes."""
    keep = lambda o, d: o in codes or d in codes
    moves = {k: defaultdict(lambda: {'n': 0, 'down': 0, 'up': 0, 'same': 0, 'sum': 0, 'pct': 0.0}) for k in ['any', 'direct']}
    by_check = {k: [{'down': 0, 'up': 0, 'n': 0} for _ in WD] for k in ['any', 'direct']}
    for prev, cur in zip(uniq, uniq[1:]):
        day = datetime.date.fromisoformat(cur['run_at'][:10])
        for route, kinds in cur.get('cal', {}).items():
            if not keep(*route.split('-')):
                continue
            for kind, c in kinds.items():
                old_c = prev.get('cal', {}).get(route, {}).get(kind)
                if not old_c or kind not in moves:
                    continue
                old, new = by_date(old_c), by_date(c)
                for d, v in new.items():
                    o = old.get(d)
                    if o is None:
                        continue
                    n = (datetime.date.fromisoformat(d) - day).days
                    b = bucket(n)
                    if b is None:
                        continue
                    m = moves[kind][b]
                    m['n'] += 1
                    diff = v - o
                    if abs(diff) < 50:
                        m['same'] += 1
                    elif diff < 0:
                        m['down'] += 1
                    else:
                        m['up'] += 1
                    m['sum'] += diff
                    m['pct'] += diff / o * 100
                    w = by_check[kind][day.weekday()]
                    w['n'] += 1
                    if diff <= -50:
                        w['down'] += 1
                    elif diff >= 50:
                        w['up'] += 1

    out_moves = {}
    for kind, bs in moves.items():
        rows = []
        for _, _, name in BUCKETS:
            m = bs.get(name)
            if not m or not m['n']:
                rows.append({'bucket': name, 'n': 0})
                continue
            rows.append({'bucket': name, 'n': m['n'], 'down': m['down'], 'up': m['up'], 'same': m['same'],
                         'avg_kr': round(m['sum'] / m['n']), 'avg_pct': round(m['pct'] / m['n'], 2)})
        out_moves[kind] = rows

    # cheapest price by weekday of travel, from the latest prices
    wk = {}
    for direction in ['to', 'from']:
        for kind in ['any', 'direct']:
            sums = [[0, 0] for _ in WD]
            for route, kinds in latest_cal.items():
                o, d = route.split('-')
                if not keep(o, d) or (direction == 'to') != (d in codes) or kind not in kinds:
                    continue
                for day, v in by_date(kinds[kind]).items():
                    w = datetime.date.fromisoformat(day).weekday()
                    sums[w][0] += v
                    sums[w][1] += 1
            wk[f'{direction}_{kind}'] = [{'wd': WD[i], 'avg': round(s / n) if n else None, 'n': n} for i, (s, n) in enumerate(sums)]

    return {'moves_by_days_before': out_moves,
            'moves_by_check_day': {k: [{'wd': WD[i], **v} for i, v in enumerate(vs)] for k, vs in by_check.items()},
            'by_travel_weekday': wk}
