"""Tiny API for price watches (127.0.0.1:8092, behind Caddy at /api/).

GET    /api/watches          -> data/watches.json (also served as a static file)
POST   /api/watches          -> add a watch   (header X-Pin must match PIN in .env)
DELETE /api/watches/<id>     -> remove a watch (header X-Pin)
GET    /api/dests            -> destinations (also served as data/dests.json)
POST   /api/dests            -> add a destination {code, name, place} (header X-Pin); its first prices
                                (Travelpayouts) are fetched right away, Google prices come with the next cloud run
DELETE /api/dests/<CODE>     -> remove a destination (header X-Pin)
POST   /api/cal              -> Google calendars from the cloud task (header X-Upload-Token);
                                {"merge": true} only adds/replaces the files sent instead of replacing all
POST   /api/anywhere         -> prices to anywhere from the "anywhere" scan (header X-Upload-Token):
                                {"cal": {"CPH-EDI": {"start": "YYYY-MM-DD", "p": [price per day, 0 = none]}}}
                                (older form: {"rows": [[FROM, TO, "YYYY-MM-DD", price], ...]}). Each route sent is
                                replaced; other routes are kept. Written to data/anywhere.json:
                                routes = cheapest day per route [date, price, updated], cal = day-by-day prices
"""
import json, os, re, uuid, datetime, threading, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import watches

APP = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get('DATA_DIR', '/srv/flight-watch/data')
WATCH_FILE = os.path.join(APP, 'watches.json')
PIN = os.environ.get('WATCH_PIN', '').strip()  # .strip(): a .env saved on Windows ends lines with \r
UPLOAD_TOKEN = os.environ.get('UPLOAD_TOKEN', '').strip()
CAL = os.path.join(APP, 'cal')
lock = threading.Lock()
APTS = set(watches.APTS)
DEST_FILE = os.path.join(APP, 'dests.json')
DEFAULT_DESTS = [{'code': 'KEF', 'name': 'Keflavík', 'place': 'Iceland'}]


def load_dests():
    try:
        return json.load(open(DEST_FILE)) or DEFAULT_DESTS
    except Exception:
        return DEFAULT_DESTS


def save_dests(ds):
    json.dump(ds, open(DEST_FILE, 'w'), indent=1, ensure_ascii=False)
    json.dump(ds, open(os.path.join(DATA, 'dests.json'), 'w'), ensure_ascii=False)


GF_FIRE_URL = os.environ.get('GF_FIRE_URL', '').strip()
GF_FIRE_TOKEN = os.environ.get('GF_FIRE_TOKEN', '').strip()


def fire_google_task():
    """Start the Google prices cloud task in its quick "new-only" mode (its API trigger), if set up in .env."""
    if not GF_FIRE_URL or not GF_FIRE_TOKEN:
        return

    def go():
        import urllib.request, urllib.error
        # "new-only" tells the task to fetch only the destinations without Google prices (about 10 minutes)
        req = urllib.request.Request(GF_FIRE_URL, data=b'{"text": "new-only"}', method='POST', headers={
            'Authorization': 'Bearer ' + GF_FIRE_TOKEN, 'anthropic-version': '2023-06-01',
            'anthropic-beta': 'experimental-cc-routine-2026-04-01', 'Content-Type': 'application/json',
            'User-Agent': 'flight-watch/1.0'})
        try:
            print('google task started:', urllib.request.urlopen(req, timeout=30).read().decode()[:200], flush=True)
        except urllib.error.HTTPError as e:
            print('google task could not be started:', e, e.read().decode(errors='replace')[:300], flush=True)
        except Exception as e:
            print('google task could not be started:', e, flush=True)
    threading.Thread(target=go, daemon=True).start()


def rebuild(*codes):
    # run.sh waits for a running price check (flock), fetches Travelpayouts for the given codes and rebuilds
    subprocess.Popen([os.path.join(APP, 'run.sh'), *codes], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def load():
    return json.load(open(WATCH_FILE)) if os.path.exists(WATCH_FILE) else []


def refresh(ws):
    json.dump(ws, open(WATCH_FILE, 'w'), indent=1)
    latest = json.load(open(os.path.join(DATA, 'latest.json')))
    return watches.update(WATCH_FILE, DATA, None, latest['cal'])


def clean(b):
    d = lambda s: str(datetime.date.fromisoformat(str(s)))
    codes = [x['code'] for x in load_dests()]
    dest = b.get('dest') or codes[0]
    if dest not in codes:
        raise ValueError('unknown destination')
    w = {
        'id': uuid.uuid4().hex[:10],
        'name': str(b.get('name') or '').strip()[:60],
        'dest': dest,
        'out': [a for a in b.get('out', []) if a in APTS] or sorted(APTS),
        'home': [a for a in b.get('home', []) if a in APTS] or sorted(APTS),
        'from': d(b['from']), 'to': d(b['to']),
        'min_days': max(1, min(365, int(b.get('min_days', 30)))),
        'max_days': max(1, min(365, int(b.get('max_days', 60)))),
        'direct': bool(b.get('direct', True)),
        'target': int(b['target']) if b.get('target') not in (None, '') else None,
        'created': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
    }
    if w['to'] < w['from'] or w['max_days'] < w['min_days']:
        raise ValueError('bad range')
    if not w['name']:
        w['name'] = f"{w['min_days']}-{w['max_days']} days, {w['from']} to {w['to']}"
    return w


class H(BaseHTTPRequestHandler):
    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def ok_pin(self):
        if PIN and self.headers.get('X-Pin', '').strip() == PIN:
            return True
        self.reply(403, {'error': 'Wrong PIN'})
        return False

    def do_GET(self):
        if self.path.rstrip('/') == '/api/watches':
            p = os.path.join(DATA, 'watches.json')
            return self.reply(200, json.load(open(p)) if os.path.exists(p) else [])
        if self.path.rstrip('/') == '/api/dests':
            return self.reply(200, load_dests())
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        if self.path.rstrip('/') == '/api/cal':
            return self.upload_cal()
        if self.path.rstrip('/') == '/api/dests':
            return self.add_dest()
        if self.path.rstrip('/') == '/api/anywhere':
            return self.upload_anywhere()
        if self.path.rstrip('/') != '/api/watches':
            return self.reply(404, {'error': 'not found'})
        if not self.ok_pin():
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > 5000:
                raise ValueError('too big')
            w = clean(json.loads(self.rfile.read(n)))
        except Exception as e:
            return self.reply(400, {'error': 'Invalid watch: ' + str(e)[:80]})
        with lock:
            ws = load()
            if len(ws) >= 50:
                return self.reply(400, {'error': 'Too many watches'})
            ws.append(w)
            self.reply(200, refresh(ws))

    def add_dest(self):
        if not self.ok_pin():
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > 2000:
                raise ValueError('too big')
            b = json.loads(self.rfile.read(n))
            code = str(b.get('code') or '').strip().upper()
            if not re.fullmatch(r'[A-Z]{3}', code):
                raise ValueError('use a 3-letter airport code, like AGP')
            if code in APTS:
                raise ValueError(code + ' is one of the home airports')
            d = {'code': code, 'name': str(b.get('name') or '').strip()[:40] or code,
                 'place': str(b.get('place') or '').strip()[:40] or str(b.get('name') or '').strip()[:40] or code}
        except Exception as e:
            return self.reply(400, {'error': 'Invalid airport: ' + str(e)[:80]})
        with lock:
            ds = load_dests()
            if any(x['code'] == code for x in ds):
                return self.reply(400, {'error': code + ' is already on the list'})
            if len(ds) >= 12:
                return self.reply(400, {'error': 'Too many destinations (12 at most)'})
            ds.append(d)
            save_dests(ds)
        rebuild(code)
        fire_google_task()
        self.reply(200, ds)

    def do_DELETE(self):
        m = re.fullmatch(r'/api/dests/([A-Z]{3})', self.path)
        if m:
            if not self.ok_pin():
                return
            with lock:
                ds = load_dests()
                rest = [x for x in ds if x['code'] != m.group(1)]
                if not rest:
                    return self.reply(400, {'error': 'Keep at least one destination'})
                save_dests(rest)
            rebuild('-')  # rebuild only: '-' is no destination, so no Travelpayouts requests
            return self.reply(200, rest)
        m = re.fullmatch(r'/api/watches/([0-9a-f]{10})', self.path)
        if not m:
            return self.reply(404, {'error': 'not found'})
        if not self.ok_pin():
            return
        with lock:
            ws = [w for w in load() if w['id'] != m.group(1)]
            self.reply(200, refresh(ws))

    def upload_cal(self):
        # Google calendars from the cloud task: {"files": {"CPH-KEF-any.txt": "<start>\n<numbers>"}}
        if not UPLOAD_TOKEN or self.headers.get('X-Upload-Token', '') != UPLOAD_TOKEN:
            return self.reply(403, {'error': 'forbidden'})
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > 4000000:
                raise ValueError('too big')
            body = json.loads(self.rfile.read(n))
            files, merge = body['files'], bool(body.get('merge'))
            ok = {}
            for name, text in files.items():
                if re.fullmatch(r'[A-Z]{3}-[A-Z]{3}-details\.json', name):
                    json.loads(text)  # flight times/airlines per day
                    ok[name] = text
                    continue
                if not re.fullmatch(r'[A-Z]{3}-[A-Z]{3}-(any|direct)\.txt', name):
                    raise ValueError('bad name ' + name[:30])
                lines = text.strip().split('\n')
                datetime.date.fromisoformat(lines[0].strip())
                if not all(x.isdigit() for x in ' '.join(lines[1:]).split()):
                    raise ValueError('bad numbers in ' + name)
                ok[name] = text.strip() + '\n'
            if not merge and sum(1 for k in ok if k.endswith('-any.txt')) < 6:
                raise ValueError('too few routes')
        except Exception as e:
            return self.reply(400, {'error': str(e)[:100]})
        with lock:
            os.makedirs(CAL, exist_ok=True)
            if not merge:
                for f in os.listdir(CAL):
                    os.remove(os.path.join(CAL, f))
            for name, text in ok.items():
                open(os.path.join(CAL, name), 'w').write(text)
        subprocess.Popen(['systemctl', 'start', '--no-block', 'flight-watch.service'])
        self.reply(200, {'saved': len(ok)})

    def upload_anywhere(self):
        if not UPLOAD_TOKEN or self.headers.get('X-Upload-Token', '') != UPLOAD_TOKEN:
            return self.reply(403, {'error': 'forbidden'})
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > 2000000:
                raise ValueError('too big')
            body = json.loads(self.rfile.read(n))
            rows, cals = body.get('rows') or [], {}
            for k, c in (body.get('cal') or {}).items():
                if not re.fullmatch(r'[A-Z]{3}-[A-Z]{3}', k):
                    raise ValueError('bad route')
                s0 = datetime.date.fromisoformat(c['start'])
                ps = [max(0, round(float(x))) for x in c['p']][:400]
                cals[k] = {'start': str(s0), 'p': ps}
                rows += [[*k.split('-'), str(s0 + datetime.timedelta(days=i)), x] for i, x in enumerate(ps) if x]
            new = {}
            for o, d, day, p in rows:
                if not (re.fullmatch(r'[A-Z]{3}', o) and re.fullmatch(r'[A-Z]{3}', d)):
                    raise ValueError('bad airport')
                datetime.date.fromisoformat(day)
                p = round(float(p))
                k = f'{o}-{d}'
                if k not in new or p < new[k][1]:
                    new[k] = [day, p]
        except Exception as e:
            return self.reply(400, {'error': str(e)[:100]})
        path = os.path.join(DATA, 'anywhere.json')
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
        with lock:
            try:
                cur = json.load(open(path))
            except Exception:
                cur = {'routes': {}}
            today = str(datetime.date.today())
            routes = {k: v for k, v in cur.get('routes', {}).items() if v[0] >= today}  # drop passed dates
            cal = cur.get('cal', {})
            for k, (day, p) in new.items():
                routes[k] = [day, p, now]
            for k, c in cals.items():
                cal[k] = c
                if k not in new:  # no prices at all now: the route has no flights in the next 6 months
                    routes.pop(k, None)
            json.dump({'updated': now, 'routes': routes, 'cal': cal}, open(path + '.tmp', 'w'), separators=(',', ':'))
            os.replace(path + '.tmp', path)
        self.reply(200, {'saved': len(new), 'routes': len(routes)})

    def log_message(self, *a):
        pass  # no access log; fire_google_task prints to the journal


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8092), H).serve_forever()
