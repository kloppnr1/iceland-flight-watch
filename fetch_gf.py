"""Google Flights price calendars via the open-source `fli` tool (pip install flights).

Writes cal/<FROM>-<TO>-<any|direct>.txt for each route: line 1 = start date,
then one number per day = cheapest one-way price in DKK / 10 (0 = no flight).
"""
import json, os, sys, glob, datetime, subprocess, time

FLI = os.environ.get('FLI', 'fli')
final = sys.argv[1] if len(sys.argv) > 1 else 'cal'
out = final + '.new'
os.makedirs(out, exist_ok=True)
for f in glob.glob(os.path.join(out, '*.txt')):
    os.remove(f)

today = datetime.date.today()
start, end = today + datetime.timedelta(days=1), today + datetime.timedelta(days=330)
routes = [(a, 'KEF') for a in ['CPH', 'BLL', 'AAL', 'HAM']] + [('KEF', a) for a in ['CPH', 'BLL', 'AAL', 'HAM']]
for o, d in routes:
    for kind, stops in [('any', 'ANY'), ('direct', '0')]:
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
            print('fli failed', o, d, kind, (r.stderr or r.stdout)[:200], flush=True)
        time.sleep(5)
        if not prices:
            print(o, d, kind, 'no flights')
            continue
        ks = sorted(prices)
        d0 = datetime.date.fromisoformat(ks[0])
        n = (datetime.date.fromisoformat(ks[-1]) - d0).days + 1
        vals = [prices.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(n)]
        with open(os.path.join(out, f'{o}-{d}-{kind}.txt'), 'w') as f:
            f.write(ks[0] + '\n' + ' '.join(map(str, vals)) + '\n')
        print(o, d, kind, ks[0], n, 'days', sum(1 for v in vals if v), 'priced')

# Only replace the old calendars if this run got most routes (a Google rate limit can make a run fail)
got = glob.glob(os.path.join(out, '*-any.txt'))
if len(got) >= 6:
    os.makedirs(final, exist_ok=True)
    for f in glob.glob(os.path.join(final, '*.txt')):
        os.remove(f)
    for f in glob.glob(os.path.join(out, '*.txt')):
        os.replace(f, os.path.join(final, os.path.basename(f)))
    print('calendars updated')
else:
    print('too few routes this time, keeping the previous calendars')
