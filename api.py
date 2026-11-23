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
                                routes = cheapest day per route [date, price, updated], cal = day-by-day prices.
                                "dcal" (same form) = direct-only day-by-day prices, kept as dcal in data/anywhere.json.
                                Calendars without any price are ignored (Google often answers empty although there
                                are flights), so a route keeps its earlier prices.
                                "nod": {"AAL-LHR": {"days": 4, "flights": 120}} = confirmed no direct flights: full flight
                                lists for that many days across the next 6 months had no nonstop flight
                                (confirm_direct.py). Kept as nod; direct prices for a route replace it.
POST   /api/jobs             -> progress of a download job (jobreport.py; header X-Upload-Token):
                                {"job", "run", "state": running|done|failed|stopped, "phase", "done", "total",
                                 "note", "errors", "last_error"}. Written to data/jobs.json for admin.html:
                                jobs = latest report per job, history = the last finished runs.
"""
import json, os, re, uuid, time, hmac, datetime, threading, subprocess
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


def merge_cal(old, new, now, keep_days=7):
    """Merge an uploaded day-by-day calendar into the stored one. The new prices replace the days inside the
    new calendar's windows (new['win'], else its first..last day); days outside them keep the stored prices while
    the upload that brought them is under keep_days old. The result: {start, p, at, seg} where
    seg = [[first day, last day, uploaded at], ...] tells which upload each stretch of days comes from."""
    day = datetime.date.fromisoformat
    s0 = day(new['start'])
    win = [(day(a), day(b)) for a, b in new.get('win') or []] or [(s0, s0 + datetime.timedelta(days=len(new['p']) - 1))]
    prices = {}
    for i, x in enumerate(new['p']):
        d = s0 + datetime.timedelta(days=i)
        if x and any(a <= d <= b for a, b in win):
            prices[d] = x
    segs = [[a, b, now] for a, b in win]
    if not (old and old.get('p')):  # nothing stored yet: keep the calendar as sent
        return {'start': new['start'], 'p': new['p'], 'at': now, 'seg': [[str(a), str(b), t] for a, b, t in segs]}
    o0 = day(old['start'])
    oseg = [[day(a), day(b), t] for a, b, t in old.get('seg') or []] or \
           [[o0, o0 + datetime.timedelta(days=len(old['p']) - 1), old.get('at', now)]]
    limit = (datetime.datetime.fromisoformat(now) - datetime.timedelta(days=keep_days)).isoformat(timespec='seconds')
    one = datetime.timedelta(days=1)
    for a, b, t in oseg:
        if t < limit:
            continue
        pieces = [(a, b)]
        for a2, b2 in win:  # cut out the days the new calendar covers
            cut = []
            for x, y in pieces:
                if y < a2 or x > b2:
                    cut.append((x, y))
                    continue
                if x < a2:
                    cut.append((x, a2 - one))
                if y > b2:
                    cut.append((b2 + one, y))
            pieces = cut
        for x, y in pieces:
            segs.append([x, y, t])
            for i, v in enumerate(old['p']):
                d = o0 + datetime.timedelta(days=i)
                if v and x <= d <= y:
                    prices[d] = v
    today = datetime.date.today()
    segs = sorted([[max(a, today), b, t] for a, b, t in segs if b >= today])
    prices = {d: x for d, x in prices.items() if d >= today}
    if not prices:
        return {'start': new['start'], 'p': [x for x in new['p']], 'at': now}
    first, last = min(prices), max(prices)
    return {'start': str(first), 'p': [prices.get(first + datetime.timedelta(days=i), 0) for i in range((last - first).days + 1)],
            'at': now, 'seg': [[str(a), str(b), t] for a, b, t in segs]}


def load_dests():
    try:
        return json.load(open(DEST_FILE)) or DEFAULT_DESTS
    except Exception:
        return DEFAULT_DESTS


def save_dests(ds):
    json.dump(ds, open(DEST_FILE, 'w'), indent=1, ensure_ascii=False)
    json.dump(ds, open(os.path.join(DATA, 'dests.json'), 'w'), ensure_ascii=False)


# Wrong PINs: at most 5 per address in 15 minutes and 40 in all per hour, then 429 until the window passes.
# The address is the one Caddy adds last to X-Forwarded-For (the real client; earlier entries can be faked).
BAD = []  # (time, address) of wrong PINs
BAD_PER_ADDR, BAD_ADDR_SECS, BAD_ALL, BAD_ALL_SECS = 5, 900, 40, 3600


def pin_blocked(addr, now=None):
    now = now or time.time()
    BAD[:] = [(t, a) for t, a in BAD if now - t < BAD_ALL_SECS]
    return len(BAD) >= BAD_ALL or sum(1 for t, a in BAD if a == addr and now - t < BAD_ADDR_SECS) >= BAD_PER_ADDR


JOB_STATES = ('running', 'done', 'failed', 'stopped')  # 'lost' is set here: a run that never reported its end
JOB_HISTORY = 60  # finished runs kept in data/jobs.json

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
        'min_days': max(1, min(365, int(b.get('min_days', 4)))),
        'max_days': max(1, min(365, int(b.get('max_days', 14)))),
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
        addr = (self.headers.get('X-Forwarded-For') or self.client_address[0]).split(',')[-1].strip()
        with lock:
            if pin_blocked(addr):
                self.reply(429, {'error': 'Too many wrong PINs. Try again in a while.'})
                return False
            if PIN and hmac.compare_digest(self.headers.get('X-Pin', '').strip().encode(), PIN.encode()):
                return True
            BAD.append((time.time(), addr))
        self.reply(403, {'error': 'Wrong PIN'})
        return False

    def ok_token(self):
        if UPLOAD_TOKEN and hmac.compare_digest(self.headers.get('X-Upload-Token', '').encode(), UPLOAD_TOKEN.encode()):
            return True
        self.reply(403, {'error': 'forbidden'})
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
        if self.path.rstrip('/') == '/api/jobs':
            return self.job_report()
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
        if not self.ok_token():
            return
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
        rebuild('--build-only')  # queued behind a running build (flock), so this upload is always built
        self.reply(200, {'saved': len(ok)})

    def upload_anywhere(self):
        if not self.ok_token():
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > 2000000:
                raise ValueError('too big')
            body = json.loads(self.rfile.read(n))
            rows, cals, dcals, nods = body.get('rows') or [], {}, {}, {}
            for k, v in (body.get('nod') or {}).items():
                if not re.fullmatch(r'[A-Z]{3}-[A-Z]{3}', k):
                    raise ValueError('bad route')
                nods[k] = {'days': int(v['days']), 'flights': int(v['flights'])}
            for src, out in (('cal', cals), ('dcal', dcals)):
                for k, c in (body.get(src) or {}).items():
                    if not re.fullmatch(r'[A-Z]{3}-[A-Z]{3}', k):
                        raise ValueError('bad route')
                    s0 = datetime.date.fromisoformat(c['start'])
                    out[k] = {'start': str(s0), 'p': [max(0, round(float(x))) for x in c['p']][:400]}
                    if c.get('win'):
                        out[k]['win'] = [[str(datetime.date.fromisoformat(a)), str(datetime.date.fromisoformat(b))]
                                         for a, b in c['win']][:12]
            for k, c in cals.items():
                s0 = datetime.date.fromisoformat(c['start'])
                rows += [[*k.split('-'), str(s0 + datetime.timedelta(days=i)), x] for i, x in enumerate(c['p']) if x]
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
            cal, dcal, nod = cur.get('cal', {}), cur.get('dcal', {}), cur.get('nod', {})
            for k, v in nods.items():
                nod[k] = {**v, 'at': now}
                dcal.pop(k, None)
            # Google often answers a search with an empty list although the route has flights (seen on half the
            # searches for CPH-LHR, 18 Nov), so an empty calendar proves nothing: it is ignored and the route keeps
            # what it had. Only calendars with prices are stored.
            # A calendar replaces only the days it covers ("win", the windows that came back with prices, or else its
            # own first..last day). Earlier prices for other days are kept for up to a week (segments in "seg"), so a
            # window Google answers empty this time (seen for AAR-PMI, Nov 2026) does not wipe that window's prices.
            for k, c in dcals.items():
                if any(c['p']):
                    dcal[k] = merge_cal(dcal.get(k), c, now)
                    nod.pop(k, None)
            for k, (day, p) in new.items():
                routes[k] = [day, p, now]
            for k, c in cals.items():
                if any(c['p']):
                    cal[k] = merge_cal(cal.get(k), c, now)
            # the cheapest day per route, from the merged calendar (kept days included)
            for k in cals:
                if k in cal:
                    s0 = datetime.date.fromisoformat(cal[k]['start'])
                    best = min(((x, str(s0 + datetime.timedelta(days=i))) for i, x in enumerate(cal[k]['p']) if x and
                                str(s0 + datetime.timedelta(days=i)) >= today), default=None)
                    if best:
                        routes[k] = [best[1], best[0], now]
            # drop what no scan has refreshed for a week (a destination taken off the scan list) and passed calendars
            old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat(timespec='seconds')
            routes = {k: v for k, v in routes.items() if len(v) < 3 or v[2] >= old}
            last_day = lambda c: str(datetime.date.fromisoformat(c['start']) + datetime.timedelta(days=len(c['p']) - 1))
            cal = {k: c for k, c in cal.items() if c.get('at', now) >= old and last_day(c) >= today}
            # direct: only calendars with prices (earlier "no direct flights" entries were often Google's empty answers)
            dcal = {k: c for k, c in dcal.items() if c.get('at', now) >= old and any(c['p']) and last_day(c) >= today}
            nod = {k: v for k, v in nod.items() if v.get('at', now) >= old and k not in dcal}
            json.dump({'updated': now, 'routes': routes, 'cal': cal, 'dcal': dcal, 'nod': nod}, open(path + '.tmp', 'w'), separators=(',', ':'))
            os.replace(path + '.tmp', path)
        self.reply(200, {'saved': len(new), 'routes': len(routes)})

    def job_report(self):
        if not self.ok_token():
            return
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > 5000:
                raise ValueError('too big')
            b = json.loads(self.rfile.read(n))
            job = str(b['job'])
            if not re.fullmatch(r'[a-z][a-z0-9-]{0,30}', job):
                raise ValueError('bad job')
            if b.get('state') not in JOB_STATES:
                raise ValueError('bad state')
            r = {'run': str(datetime.datetime.fromisoformat(str(b['run'])).isoformat(timespec='seconds')),
                 'state': b['state'], 'phase': str(b.get('phase') or '')[:200], 'note': str(b.get('note') or '')[:200],
                 'done': max(0, int(b.get('done') or 0)), 'total': max(0, int(b.get('total') or 0)),
                 'errors': max(0, int(b.get('errors') or 0)), 'last_error': str(b.get('last_error') or '')[:300]}
        except Exception as e:
            return self.reply(400, {'error': str(e)[:100]})
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
        path = os.path.join(DATA, 'jobs.json')
        with lock:
            try:
                cur = json.load(open(path))
            except Exception:
                cur = {}
            jobs, hist = cur.get('jobs', {}), cur.get('history', [])
            prev = jobs.get(job) or {}
            if prev.get('run') != r['run'] and prev.get('state') == 'running':
                # a new run while the last one never said it ended (session killed): record it as lost
                hist.append({**prev, 'job': job, 'state': 'lost', 'ended': prev.get('updated')})
            r['updated'] = now
            if prev.get('run') == r['run']:
                if prev.get('state') != 'running':
                    return self.reply(200, {'ok': True})  # already ended: late reports are ignored
                r['started'] = prev.get('started', now)
            else:
                r['started'] = now
            jobs[job] = r
            if r['state'] != 'running':
                hist.append({**r, 'job': job, 'ended': now})
            json.dump({'updated': now, 'jobs': jobs, 'history': hist[-JOB_HISTORY:]}, open(path + '.tmp', 'w'), indent=1)
            os.replace(path + '.tmp', path)
        self.reply(200, {'ok': True})

    def log_message(self, *a):
        pass  # no access log; fire_google_task prints to the journal


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8092), H).serve_forever()
