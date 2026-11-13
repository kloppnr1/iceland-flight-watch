"""Price watches: a trip window the user wants to follow day by day.

A watch: {id, name, out:[airports], home:[airports], from, to, min_days, max_days, direct, target, created}
The whole trip (out day and home day) must fall inside from..to, and the stay must be min_days..max_days.
Each price check adds the cheapest matching round trip to the watch's history (data/watches.json).
"""
import json, os, datetime

APTS = ['CPH', 'BLL', 'AAL', 'HAM']


def series(cal, route, kind):
    c = cal.get(route, {}).get(kind)
    if not c:
        return {}
    s = datetime.date.fromisoformat(c['start'])
    return {str(s + datetime.timedelta(days=i)): v for i, v in enumerate(c['p']) if v}


def best_for(w, cal, today=None):
    today = str(today or datetime.date.today())
    kind = 'direct' if w.get('direct') else 'any'
    lo, hi = max(w['from'], today), w['to']
    best = None
    for o in w.get('out') or APTS:
        A = series(cal, f'{o}-KEF', kind)
        for h in w.get('home') or APTS:
            B = sorted((d, p) for d, p in series(cal, f'KEF-{h}', kind).items() if lo <= d <= hi)
            for d1, p1 in A.items():
                if not (lo <= d1 <= hi):
                    continue
                for d2, p2 in B:
                    n = (datetime.date.fromisoformat(d2) - datetime.date.fromisoformat(d1)).days
                    if n < w['min_days']:
                        continue
                    if n > w['max_days']:
                        break
                    if best is None or p1 + p2 < best['total']:
                        best = dict(total=p1 + p2, o=o, d1=d1, p1=p1, h=h, d2=d2, p2=p2, days=n)
    return best


def update(watch_file, data_dir, run_at, cal):
    """Add this check's best price to each watch and write data/watches.json (public)."""
    watches = json.load(open(watch_file)) if os.path.exists(watch_file) else []
    out_path = os.path.join(data_dir, 'watches.json')
    old = {w['id']: w for w in (json.load(open(out_path)) if os.path.exists(out_path) else [])}
    res = []
    for w in watches:
        hist = old.get(w['id'], {}).get('history', [])
        b = best_for(w, cal)
        if run_at and (not hist or hist[-1]['at'] != run_at):
            hist.append({'at': run_at, 'total': b['total'] if b else None})
        res.append({**w, 'best': b, 'history': hist[-400:], 'good': bool(b and w.get('target') and b['total'] <= w['target'])})
    json.dump(res, open(out_path, 'w'), separators=(',', ':'))
    return res
