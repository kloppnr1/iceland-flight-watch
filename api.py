"""Tiny API for price watches (127.0.0.1:8092, behind Caddy at /api/).

GET    /api/watches          -> data/watches.json (also served as a static file)
POST   /api/watches          -> add a watch   (header X-Pin must match PIN in .env)
DELETE /api/watches/<id>     -> remove a watch (header X-Pin)
"""
import json, os, re, uuid, datetime, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import watches

APP = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get('DATA_DIR', '/srv/flight-watch/data')
WATCH_FILE = os.path.join(APP, 'watches.json')
PIN = os.environ.get('WATCH_PIN', '')
UPLOAD_TOKEN = os.environ.get('UPLOAD_TOKEN', '')
CAL = os.path.join(APP, 'cal')
lock = threading.Lock()
APTS = set(watches.APTS)


def load():
    return json.load(open(WATCH_FILE)) if os.path.exists(WATCH_FILE) else []


def refresh(ws):
    json.dump(ws, open(WATCH_FILE, 'w'), indent=1)
    latest = json.load(open(os.path.join(DATA, 'latest.json')))
    return watches.update(WATCH_FILE, DATA, None, latest['cal'])


def clean(b):
    d = lambda s: str(datetime.date.fromisoformat(str(s)))
    w = {
        'id': uuid.uuid4().hex[:10],
        'name': str(b.get('name') or '').strip()[:60],
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
        if PIN and self.headers.get('X-Pin', '') == PIN:
            return True
        self.reply(403, {'error': 'Wrong PIN'})
        return False

    def do_GET(self):
        if self.path.rstrip('/') == '/api/watches':
            p = os.path.join(DATA, 'watches.json')
            return self.reply(200, json.load(open(p)) if os.path.exists(p) else [])
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        if self.path.rstrip('/') == '/api/cal':
            return self.upload_cal()
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

    def do_DELETE(self):
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
            files = json.loads(self.rfile.read(n))['files']
            ok = {}
            for name, text in files.items():
                if re.fullmatch(r'(CPH|BLL|AAL|HAM|KEF)-(CPH|BLL|AAL|HAM|KEF)-details\.json', name):
                    json.loads(text)  # flight times/airlines per day
                    ok[name] = text
                    continue
                if not re.fullmatch(r'(CPH|BLL|AAL|HAM|KEF)-(CPH|BLL|AAL|HAM|KEF)-(any|direct)\.txt', name):
                    raise ValueError('bad name ' + name[:30])
                lines = text.strip().split('\n')
                datetime.date.fromisoformat(lines[0].strip())
                if not all(x.isdigit() for x in ' '.join(lines[1:]).split()):
                    raise ValueError('bad numbers in ' + name)
                ok[name] = text.strip() + '\n'
            if sum(1 for k in ok if k.endswith('-any.txt')) < 6:
                raise ValueError('too few routes')
        except Exception as e:
            return self.reply(400, {'error': str(e)[:100]})
        with lock:
            os.makedirs(CAL, exist_ok=True)
            for f in os.listdir(CAL):
                os.remove(os.path.join(CAL, f))
            for name, text in ok.items():
                open(os.path.join(CAL, name), 'w').write(text)
        import subprocess
        subprocess.Popen(['systemctl', 'start', '--no-block', 'flight-watch.service'])
        self.reply(200, {'saved': len(ok)})

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 8092), H).serve_forever()
