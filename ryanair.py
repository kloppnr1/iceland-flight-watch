"""Ryanair's own fares: the cheapest Ryanair fare for every day on every Ryanair route from the home airports, both
ways, for the next 12 months, with departure and arrival times. Ryanair only flies direct, so these are direct fares.

Source: the public feeds Ryanair's website uses (no key): the route list per airport
(www.ryanair.com/api/views/locate/searchWidget/routes/en/airport/<CODE>) and the cheapest fare per day for one month
(services-api.ryanair.com/farfnd/v4/oneWayFares/<FROM>/<TO>/cheapestPerDay). They are not an official API, so the
requests are slow and few (one per route, direction and month, about 1,200 in all, ~10 minutes) and a failed request
leaves that month out (the route keeps the rest).

Output (data/fr.json on the server, read by build.py and the page):
  {"updated": time, "routes": {"AAR-PMI": {"start": "YYYY-MM-DD", "f": [0 | [price, "HH:MM" dep, "HH:MM" arr, days to arrival]]}}}
Usage: python3 ryanair.py --out data/fr.json        write the file (server, run.sh)
       UPLOAD_TOKEN=... python3 ryanair.py           send it to the site (POST /api/fr) instead
"""
import datetime, json, os, sys, time, urllib.error, urllib.request

HOME = ['CPH', 'BLL', 'AAR', 'AAL', 'HAM', 'KEF']
MONTHS = int(os.environ.get('FR_MONTHS', 12))
PAUSE = float(os.environ.get('FR_PAUSE', 0.4))
SITES = [os.environ['SITE']] if os.environ.get('SITE') else ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']
UA = {'User-Agent': 'flight-watch (price watch for a few Danish airports)', 'Accept': 'application/json'}
failed = [0]


def get(url):
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None  # no such airport / route at Ryanair
            time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(5 * (attempt + 1))
    failed[0] += 1
    return None


def routes():
    """Ryanair's direct routes from the home airports, as (from, to) both ways."""
    out = []
    for a in HOME:
        for r in get(f'https://www.ryanair.com/api/views/locate/searchWidget/routes/en/airport/{a}') or []:
            b = (r.get('arrivalAirport') or {}).get('code')
            if b and not r.get('connectingAirport') and b not in HOME:
                out += [(a, b), (b, a)]
        time.sleep(PAUSE)
    return out


def fares(o, d, today):
    days = {}
    first = today.replace(day=1)
    empty_after = 0
    for i in range(MONTHS):
        m = (first.month - 1 + i) % 12 + 1
        y = first.year + (first.month - 1 + i) // 12
        js = get(f'https://services-api.ryanair.com/farfnd/v4/oneWayFares/{o}/{d}/cheapestPerDay'
                 f'?outboundMonthOfDate={y}-{m:02d}-01&currency=DKK')
        time.sleep(PAUSE)
        got = 0
        for x in ((js or {}).get('outbound') or {}).get('fares') or []:
            p = (x.get('price') or {}).get('value')
            if not p or x.get('unavailable') or x.get('soldOut') or x['day'] <= str(today):
                continue
            dep, arr = x.get('departureDate') or '', x.get('arrivalDate') or ''
            plus = (datetime.date.fromisoformat(arr[:10]) - datetime.date.fromisoformat(dep[:10])).days if dep and arr else 0
            days[x['day']] = [round(p), dep[11:16], arr[11:16], plus]
            got += 1
        # past the last month on sale: two empty months in a row after fares were found, stop asking
        empty_after = empty_after + 1 if (not got and days) else 0
        if empty_after >= 2:
            break
    return days


def main():
    today = datetime.date.today()
    rs = routes()
    print(len(rs), 'Ryanair routes (both ways)', flush=True)
    if not rs:
        sys.exit('no Ryanair routes found (feed not reachable?)')
    out = {}
    for n, (o, d) in enumerate(rs, 1):
        days = fares(o, d, today)
        if days:
            d0 = datetime.date.fromisoformat(min(days))
            span = (datetime.date.fromisoformat(max(days)) - d0).days + 1
            out[f'{o}-{d}'] = {'start': str(d0), 'f': [days.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(span)]}
        print(n, '/', len(rs), f'{o}-{d}', len(days), 'days', flush=True)
    body = {'updated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'routes': out,
            'failed': failed[0]}
    if '--out' in sys.argv:
        path = sys.argv[sys.argv.index('--out') + 1]
        json.dump(body, open(path + '.tmp', 'w'), separators=(',', ':'))
        os.replace(path + '.tmp', path)
        print('written', path, len(out), 'routes,', failed[0], 'requests failed', flush=True)
        return
    token = os.environ.get('UPLOAD_TOKEN', '').strip()
    if not token:
        sys.exit('give --out FILE or UPLOAD_TOKEN')
    for site in SITES:
        req = urllib.request.Request(site + '/api/fr', data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Upload-Token': token, 'User-Agent': UA['User-Agent']})
        try:
            print('upload:', urllib.request.urlopen(req, timeout=120).read().decode(), flush=True)
            return
        except Exception as e:
            print('upload to', site, 'failed:', e, flush=True)
    sys.exit('upload failed')


if __name__ == '__main__':
    main()
