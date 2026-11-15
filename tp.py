"""Travelpayouts prices for every destination in dests.json, between the home airports and the destination.

Usage: python3 tp.py [DEST ...]
- With destination codes: fetch only those now (used right after a destination is added, so its first
  prices show within a minute). The other destinations keep their last result.
- Without: fetch all destinations.
Writes tp/<DEST>.json per destination and tp.json with all of them together (read by build.py).
- ow: one-way tickets (cheapest per month search + cheapest per day search), both directions
- rt: round trips from each home airport (out month, back the month after)
"""
import json, os, sys, glob, datetime, urllib.request, urllib.error, time
from concurrent.futures import ThreadPoolExecutor

APP = os.path.dirname(os.path.abspath(__file__))
T = os.environ['TP_TOKEN']
DK = ['CPH', 'BLL', 'AAL', 'HAM']
API = 'https://api.travelpayouts.com/aviasales/v3/'


def all_dests():
    p = os.path.join(APP, 'dests.json')
    try:
        return [d['code'] for d in json.load(open(p))] or ['KEF']
    except Exception:
        return ['KEF']


def get(u):
    err = None
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(API + u, headers={'X-Access-Token': T}), timeout=12)).get('data') or []
        except urllib.error.HTTPError as e:
            err = e
            if e.code == 400:
                break
            time.sleep(3 * (i + 1))
        except Exception as e:
            err = e
            time.sleep(3 * (i + 1))
    print('failed', u[:70], repr(err), flush=True)
    return []


today = datetime.date.today()


def mon(i):
    y, m = today.year, today.month + i
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f'{y}-{m:02d}'


ALL = all_dests()
want = [d for d in sys.argv[1:] if d in ALL] if len(sys.argv) > 1 else ALL
jobs = []
for D in want:
    for a in DK:
        for x, y in [(a, D), (D, a)]:
            for i in range(12):
                for direct in ['false', 'true']:
                    jobs.append(('ow', D, x, y, f'prices_for_dates?currency=dkk&sorting=price&origin={x}&destination={y}&departure_at={mon(i)}&one_way=true&direct={direct}&limit=100'))
                    # cheapest ticket for each day of the month: fills the price calendar until Google prices arrive
                    jobs.append(('day', D, x, y, f'grouped_prices?currency=dkk&origin={x}&destination={y}&departure_at={mon(i)}&group_by=departure_at&direct={direct}'))
        for i in range(12):  # API only allows return in the month after departure
            jobs.append(('rt', D, a, D, f'prices_for_dates?currency=dkk&sorting=price&origin={a}&destination={D}&departure_at={mon(i)}&return_at={mon(i + 1)}&one_way=false&limit=30'))

t0 = time.time()
with ThreadPoolExecutor(6) as ex:
    res = list(ex.map(lambda j: get(j[4]), jobs))

out = {D: {'ow': [], 'rt': []} for D in want}
for (kind, D, x, y, _), data in zip(jobs, res):
    rows = list(data.values()) if isinstance(data, dict) else data
    for r in rows:
        if not isinstance(r, dict) or not r.get('price') or not r.get('departure_at'):
            continue
        if r.get('origin_airport', x) != x or r.get('destination_airport', y) != y:
            continue
        L = 'https://www.aviasales.com' + r['link'] if r.get('link') else ''
        if kind in ('ow', 'day'):
            out[D]['ow'].append(dict(o=x, d=y, date=r['departure_at'][:10], dep=r['departure_at'][11:16], al=r.get('airline', ''),
                                     fn=r.get('airline', '') + str(r.get('flight_number', '')), stops=r.get('transfers', 0),
                                     min=r.get('duration_to') or r.get('duration'), p=r['price'], link=L))
        elif r.get('return_at'):
            out[D]['rt'].append(dict(o=x, d=y, out=r['departure_at'][:10], outdep=r['departure_at'][11:16], back=r['return_at'][:10],
                                     backdep=r['return_at'][11:16], al=r.get('airline', ''), stops=r.get('transfers', 0),
                                     rstops=r.get('return_transfers', 0), p=r['price'], link=L))


def dd(l, keys):
    s = {}
    [s.setdefault(tuple(x[k] for k in keys), x) for x in sorted(l, key=lambda z: z['p'])]
    return list(s.values())


os.makedirs(os.path.join(APP, 'tp'), exist_ok=True)
for D, v in out.items():
    v['ow'] = dd(v['ow'], ['o', 'd', 'date', 'dep', 'fn'])
    v['rt'] = dd(v['rt'], ['o', 'out', 'outdep', 'back', 'backdep', 'al'])
    json.dump(v, open(os.path.join(APP, 'tp', D + '.json'), 'w'))
    print(D, len(v['ow']), 'one-way', len(v['rt']), 'round trips')

# all destinations together for build.py
merged = {'ow': [], 'rt': []}
for D in ALL:
    p = os.path.join(APP, 'tp', D + '.json')
    if os.path.exists(p):
        v = json.load(open(p))
        merged['ow'] += v['ow']
        merged['rt'] += [dict(r, d=r.get('d', D)) for r in v['rt']]
json.dump(merged, open(os.path.join(APP, 'tp.json'), 'w'))
print(len(jobs), 'requests in', int(time.time() - t0), 's;', len(merged['ow']), 'one-way', len(merged['rt']), 'round trips in all')
