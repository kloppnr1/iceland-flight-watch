"""Prices to anywhere: Google Flights calendars (fli) between the home airports and a long list of destinations
worldwide, both ways, for the next 6 months (3 windows of 61 days, the most one fli call returns).
The page shows the cheapest one-way ticket per destination, and round trips (out + home on the same airport)
for a chosen number of days, from these day-by-day one-way prices.

Results are saved to a local file after every lookup, so a stopped run continues where it stopped
(within 20 hours; after that a run starts over).
With UPLOAD_TOKEN set they are also sent to the site (POST /api/anywhere) every 25 lookups, not only at the end.

Usage: python3 anywhere.py [results.jsonl]           scan (and upload as it goes)
       python3 anywhere.py --upload-only [results.jsonl]   send what is already in the file
"""
import json, os, sys, time, datetime, subprocess, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

FLI = os.environ.get('FLI', 'fli')
UPLOAD_ONLY = '--upload-only' in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith('--')]
OUT = args[0] if args else 'anywhere.jsonl'
HOME = ['CPH', 'BLL', 'AAL', 'HAM', 'KEF']
DEST = """KEF LHR STN LGW MAN EDI DUB CDG ORY AMS BRU FRA MUC BER VIE ZRH GVA PRG BUD WAW KRK GDN VNO RIX TLL HEL ARN GOT OSL BGO TRD
CPH BLL AAL HAM
BCN MAD AGP ALC PMI IBZ VLC SVQ BIO LPA TFS ACE FUE LIS OPO FAO FCO MXP LIN VCE NAP BLQ PSA CTA PMO BRI CAG NCE MRS LYS TLS BOD
ATH HER RHO CFU CHQ SKG KGS JTR JMK SPU DBV ZAG LJU BEG SOF VAR OTP TIA MLA LCA PFO IST SAW AYT DLM BJV ADB TBS EVN
RAK AGA CMN TUN CAI HRG SSH RMF DXB AUH DOH AMM TLV MCT JED RUH
BKK HKT CNX KUL SIN SGN HAN DPS CGK MNL HKG TPE ICN NRT HND KIX PEK PVG DEL BOM GOI CMB MLE KTM
JFK EWR BOS IAD ORD MIA FLL MCO LAX SFO SEA YYZ YUL YVR CUN PUJ HAV SJU MEX BOG LIM GRU GIG EZE SCL
NBO ZNZ JNB CPT DAR MRU SEZ ADD ACC LOS DSS SID RAI
SYD MEL AKL""".split()
SITES = [os.environ['SITE']] if os.environ.get('SITE') else ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']
TOKEN = os.environ.get('UPLOAD_TOKEN', '').strip()

today = datetime.date.today()
wins = [(today + datetime.timedelta(days=1 + 61 * i), today + datetime.timedelta(days=61 * (i + 1))) for i in range(3)]
# Danish/German airports to each other are left out; from Keflavík they are real destinations.
# Each pair is looked up both ways (out and home), one pair after the other, so round trips show as the scan goes.
pairs = []
for o in HOME:
    for d in DEST:
        if o != d and not (o != 'KEF' and d in HOME) and (d, o) not in pairs:
            pairs.append((o, d))
jobs = [(x, y, a, b) for o, d in pairs for x, y in [(o, d), (d, o)] for a, b in wins]


def lookup(j):
    o, d, a, b = j
    for _ in range(2):
        try:
            r = subprocess.run([FLI, 'dates', o, d, '--from', str(a), '--to', str(b), '--currency', 'DKK', '--format', 'json'],
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
                res[tuple(x['j'])] = x['r']
            except Exception:
                pass
    return res


def upload(routes):
    """Send the day-by-day prices of each route in `routes`, from everything found so far (all windows)."""
    if not TOKEN or not routes:
        return
    days = {}
    for (o, d, _), r in read_all().items():
        if f'{o}-{d}' in routes:
            for _o, _d, day, p in r or []:
                m = days.setdefault(f'{o}-{d}', {})
                m[day] = min(round(p), m.get(day, 10 ** 9))
    cal = {}
    for k, m in days.items():
        d0 = datetime.date.fromisoformat(min(m))
        n = (datetime.date.fromisoformat(max(m)) - d0).days + 1
        cal[k] = {'start': str(d0), 'p': [m.get(str(d0 + datetime.timedelta(days=i)), 0) for i in range(n)]}
    body = json.dumps({'cal': cal}).encode()
    for site in SITES:
        req = urllib.request.Request(site + '/api/anywhere', data=body, method='POST',
                                     headers={'Content-Type': 'application/json', 'X-Upload-Token': TOKEN, 'User-Agent': 'flight-watch-anywhere'})
        try:
            print('upload', len(cal), 'routes:', urllib.request.urlopen(req, timeout=60).read().decode(), flush=True)
            return
        except urllib.error.HTTPError as e:
            print('upload refused', e.code, e.read()[:200], flush=True)
            return
        except Exception as e:
            print('could not reach', site, e, flush=True)


if UPLOAD_ONLY:
    upload({f'{o}-{d}' for (o, d, _), r in read_all().items()})
    sys.exit(0)

# A results file older than 20 hours belongs to an earlier run: start over so prices are fresh.
meta = OUT + '.started'
if not os.path.exists(meta) or time.time() - float(open(meta).read() or 0) > 20 * 3600:
    for f in (OUT, meta):
        if os.path.exists(f):
            os.remove(f)
    open(meta, 'w').write(str(time.time()))
done = {k for k, r in read_all().items() if r is not None}
todo = [j for j in jobs if (j[0], j[1], str(j[2])) not in done]
lock = threading.Lock()
pending, n, t0 = set(), [0], time.time()
print(len(jobs), 'lookups,', len(todo), 'left', flush=True)


def work(j):
    r = lookup(j)
    with lock:
        if r is not None:
            with open(OUT, 'a') as f:
                f.write(json.dumps({'j': [j[0], j[1], str(j[2])], 'r': r}) + '\n')
            pending.add(f'{j[0]}-{j[1]}')
        n[0] += 1
        if n[0] % 25 == 0 or n[0] == len(todo):
            print(n[0], '/', len(todo), int(time.time() - t0), 's', flush=True)
            upload(set(pending))
            pending.clear()


with ThreadPoolExecutor(4) as ex:
    list(ex.map(work, todo))
print('finished', len(todo), int(time.time() - t0), 's', flush=True)
