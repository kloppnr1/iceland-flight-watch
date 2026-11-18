"""Prices to anywhere: Google Flights calendars (fli) between the home airports and a long list of destinations
worldwide, both ways, for the next 6 months (3 windows of 61 days, the most one fli call returns).
The page shows the cheapest one-way ticket per destination, and round trips (out + home on the same airport)
for a chosen number of days, from these day-by-day one-way prices.
Each route's direct-only prices (fli --stops 0, for the page's "Direct only" filter) are looked up right after its
all-prices calendar, so they reach the page as the scan goes. A route with no flights at all is not looked up again
for direct flights. Routes the site has no direct-only prices for, or the oldest prices, are scanned first, so a run
that is stopped before the end still moves the whole list forward day by day.

Results are saved to a local file after every lookup, so a stopped run continues where it stopped
(within 20 hours; after that a run starts over).
The airports added on the page (data/dests.json) are always scanned too, first.
With UPLOAD_TOKEN set they are also sent to the site (POST /api/anywhere) every 25 lookups, not only at the end.

Usage: python3 anywhere.py [results.jsonl]           scan (and upload as it goes)
       python3 anywhere.py --upload-only [results.jsonl]   send what is already in the file
"""
import json, os, sys, time, datetime, subprocess, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from jobreport import Job

FLI = os.environ.get('FLI', 'fli')
UPLOAD_ONLY = '--upload-only' in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith('--')]
OUT = args[0] if args else 'anywhere.jsonl'
HOME = ['CPH', 'BLL', 'AAR', 'AAL', 'HAM', 'KEF']
DEST = """KEF LHR STN LGW MAN EDI DUB CDG ORY AMS BRU FRA MUC BER VIE ZRH GVA PRG BUD WAW KRK GDN VNO RIX TLL HEL ARN GOT OSL BGO TRD
CPH BLL AAR AAL HAM
BCN MAD AGP ALC PMI IBZ VLC SVQ BIO LPA TFS ACE FUE LIS OPO FAO FCO MXP LIN VCE NAP BLQ PSA CTA PMO BRI CAG NCE MRS LYS TLS BOD
ATH HER RHO CFU CHQ SKG KGS JTR JMK SPU DBV ZAG LJU BEG SOF VAR OTP TIA MLA LCA PFO IST SAW AYT DLM BJV ADB TBS EVN
RAK AGA CMN TUN CAI HRG SSH RMF DXB AUH DOH AMM TLV MCT JED RUH
BKK HKT CNX KUL SIN SGN HAN DPS CGK MNL HKG TPE ICN NRT HND KIX PEK PVG DEL BOM GOI CMB MLE KTM
JFK EWR BOS IAD ORD MIA FLL MCO LAX SFO SEA YYZ YUL YVR CUN PUJ HAV SJU MEX BOG LIM GRU GIG EZE SCL
NBO ZNZ JNB CPT DAR MRU SEZ ADD ACC LOS DSS SID RAI
SYD MEL AKL""".split()
SITES = [os.environ['SITE']] if os.environ.get('SITE') else ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']
TOKEN = os.environ.get('UPLOAD_TOKEN', '').strip()


def followed():
    """The airports added on the page ("to ..." picker): always part of the scan, first in line."""
    for site in SITES:
        try:
            ds = json.load(urllib.request.urlopen(urllib.request.Request(site + '/data/dests.json', headers={'User-Agent': 'flight-watch-anywhere'}), timeout=20))
            return [d['code'] for d in ds if isinstance(d, dict) and len(d.get('code', '')) == 3]
        except Exception as e:
            print('could not read the followed airports from', site, e, flush=True)
    return []


def site_ages():
    """When the site last got each route's prices: {route: oldest of (all prices, direct-only prices)}, '' = never."""
    for site in SITES:
        try:
            a = json.load(urllib.request.urlopen(urllib.request.Request(site + '/data/anywhere.json', headers={'User-Agent': 'flight-watch-anywhere'}), timeout=60))
            cal, dcal = a.get('cal') or {}, a.get('dcal') or {}
            return {k: min(c.get('at', ''), (dcal.get(k) or {}).get('at', '')) for k, c in cal.items()}
        except Exception as e:
            print('could not read the Anywhere prices from', site, e, flush=True)
    return {}


FOLLOWED = [] if UPLOAD_ONLY else followed()
DEST = FOLLOWED + [d for d in DEST if d not in FOLLOWED]
print('followed airports in the scan:', FOLLOWED, flush=True)
AGES = {} if UPLOAD_ONLY else site_ages()

# A results file older than 20 hours belongs to an earlier run: start over so prices are fresh.
# The run's start day is kept with it, so a restart after midnight continues the same date windows.
meta = OUT + '.started'
try:
    t_start, day_start = open(meta).read().split()
    fresh = time.time() - float(t_start) > 20 * 3600
except Exception:
    fresh = True
if fresh and not UPLOAD_ONLY:
    for f in (OUT, meta):
        if os.path.exists(f):
            os.remove(f)
    day_start = str(datetime.date.today())
    open(meta, 'w').write(f'{time.time()} {day_start}')
today = datetime.date.fromisoformat(day_start) if not fresh or not UPLOAD_ONLY else datetime.date.today()
wins = [(today + datetime.timedelta(days=1 + 61 * i), today + datetime.timedelta(days=61 * (i + 1))) for i in range(3)]
# Danish/German airports to each other are left out; from Keflavík they are real destinations.
# Each pair is looked up both ways (out and home), one pair after the other, so round trips show as the scan goes.
pairs = []
for o in HOME:
    for d in DEST:
        if o != d and not (o != 'KEF' and d in HOME) and (d, o) not in pairs:
            pairs.append((o, d))
# followed airports first, then the pairs whose prices on the site are missing or oldest (either way)
age = lambda o, d: min(AGES.get(f'{o}-{d}', ''), AGES.get(f'{d}-{o}', ''))
pairs.sort(key=lambda p: (not (p[0] in FOLLOWED or p[1] in FOLLOWED), age(*p)))
# one job = one route and one 61-day window: all prices, then direct-only prices
jobs = [(x, y, a, b) for o, d in pairs for x, y in [(o, d), (d, o)] for a, b in wins]


def lookup(j, k):
    o, d, a, b = j
    stops = ['--stops', '0'] if k == 'direct' else []
    for _ in range(2):
        try:
            r = subprocess.run([FLI, 'dates', o, d, '--from', str(a), '--to', str(b), *stops, '--currency', 'DKK', '--format', 'json'],
                               capture_output=True, text=True, timeout=40)
            js = json.loads(r.stdout)
            if js.get('success'):
                return [(o, d, x['departure_date'], x['price']) for x in js.get('dates', []) if x.get('price')]
        except Exception:
            pass
        time.sleep(5)
    return None  # failed: not saved, so a rerun tries again


def read_all():
    res = {}
    if os.path.exists(OUT):
        for line in open(OUT):
            try:
                x = json.loads(line)
                j = x['j'] + ['any'] * (4 - len(x['j']))  # lines from before the direct pass have no kind
                res[tuple(j)] = x['r']
            except Exception:
                pass
    return res


def upload(routes):
    """Send the day-by-day prices of each (route, kind) in `routes`, from everything found so far (all windows).
    Direct routes are sent even with no prices: that tells the page the route has no direct flights."""
    if not TOKEN or not routes:
        return
    days = {}
    for (o, d, _, k), r in read_all().items():
        if (f'{o}-{d}', k) in routes:
            m = days.setdefault((f'{o}-{d}', k), {})
            for _o, _d, day, p in r or []:
                m[day] = min(round(p), m.get(day, 10 ** 9))
    out = {'cal': {}, 'dcal': {}}
    for (rk, k), m in days.items():
        if not m:
            if k == 'direct':
                out['dcal'][rk] = {'start': str(today), 'p': []}
            continue
        d0 = datetime.date.fromisoformat(min(m))
        n = (datetime.date.fromisoformat(max(m)) - d0).days + 1
        out['cal' if k == 'any' else 'dcal'][rk] = {'start': str(d0), 'p': [m.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(n)]}
    body = json.dumps(out).encode()
    for site in SITES:
        req = urllib.request.Request(site + '/api/anywhere', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Upload-Token': TOKEN, 'User-Agent': 'flight-watch-anywhere'})
        try:
            print('upload', len(out['cal']), 'routes,', len(out['dcal']), 'direct:', urllib.request.urlopen(req, timeout=60).read().decode(), flush=True)
            return
        except urllib.error.HTTPError as e:
            print('upload refused', e.code, e.read()[:200], flush=True)
            if job:
                job.error(f'upload refused: HTTP {e.code}')
            return
        except Exception as e:
            print('could not reach', site, e, flush=True)
    if job:
        job.error('upload failed: server not reachable')


job = None
if UPLOAD_ONLY:
    upload({(f'{o}-{d}', k) for (o, d, _, k), r in read_all().items()})
    sys.exit(0)

found = {k: r for k, r in read_all().items() if r is not None}
key = lambda j, k: (j[0], j[1], str(j[2]), k)
todo = [j for j in jobs if key(j, 'any') not in found or key(j, 'direct') not in found]
lock = threading.Lock()
pending, n, t0 = set(), [0], time.time()
failed = [0]
print(len(jobs), 'lookups,', len(todo), 'left', flush=True)
job = Job('anywhere')  # progress for the admin page (admin.html)
job.step('scan', 0, len(todo), f'{len(jobs)} lookups in all, {len(jobs) - len(todo)} done in an earlier session', force=True)


def save(j, k, r):
    with lock:
        found[key(j, k)] = r
        with open(OUT, 'a') as f:
            f.write(json.dumps({'j': list(key(j, k)), 'r': r}) + '\n')
        pending.add((f'{j[0]}-{j[1]}', k))


def work(j):
    r = found.get(key(j, 'any'))
    if r is None:
        r = lookup(j, 'any')
        if r is not None:
            save(j, 'any', r)
        else:
            with lock:
                failed[0] += 1
    if r is not None and key(j, 'direct') not in found:
        # no flights at all in this window: no direct ones either, so no need to ask
        rd = [] if r == [] else lookup(j, 'direct')
        if rd is not None:
            save(j, 'direct', rd)
    with lock:
        n[0] += 1
        if n[0] % 25 == 0 or n[0] == len(todo):
            print(n[0], '/', len(todo), int(time.time() - t0), 's', flush=True)
            upload(set(pending))
            pending.clear()
        note = f'{j[0]}-{j[1]}' + (f', {failed[0]} lookups failed' if failed[0] else '')
    job.step('scan', n[0], len(todo), note)


with ThreadPoolExecutor(4) as ex:
    list(ex.map(work, todo))
print('finished', len(todo), int(time.time() - t0), 's', flush=True)
job.finish('done', f'{len(todo)} lookups in {(time.time() - t0) / 3600:.1f} h' + (f', {failed[0]} failed (tried again next run)' if failed[0] else ''))
