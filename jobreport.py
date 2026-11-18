"""Progress of a download job (fetch_gf.py, anywhere.py) sent to the site's admin page (POST /api/jobs).

job = Job('google')                       # one run; needs UPLOAD_TOKEN, otherwise does nothing
job.step('calendars', done, total, note)  # sent at most every 60 s (the page shows the latest)
job.error('upload failed: ...')           # counted, and shown on the admin page
job.finish('done', note)                  # last report; without it the run is reported as failed (exception)
                                          # or stopped (exit without finish) when the script ends
Reporting never stops a job: failures to reach the site are ignored.
"""
import atexit, datetime, json, os, signal, sys, threading, time, traceback, urllib.error, urllib.request

SITES = [os.environ['SITE']] if os.environ.get('SITE') else ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']


class Job:
    def __init__(self, name, every=60):
        self.token = os.environ.get('UPLOAD_TOKEN', '').strip()
        self.s = {'job': name, 'run': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
                  'state': 'running', 'phase': 'starting', 'done': 0, 'total': 0, 'note': '', 'errors': 0, 'last_error': ''}
        self.every, self.last, self.ended, self.exc = every, 0, False, None
        self.lock = threading.Lock()
        prev = sys.excepthook

        def hook(t, v, tb):
            self.exc = ''.join(traceback.format_exception_only(t, v)).strip()
            prev(t, v, tb)
        sys.excepthook = hook
        atexit.register(self._atexit)
        try:  # killed (session ended, timeout): still send "stopped"
            signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
        except ValueError:
            pass  # not the main thread
        self._send(force=True)

    def step(self, phase=None, done=None, total=None, note=None, force=False):
        with self.lock:
            for k, v in (('phase', phase), ('done', done), ('total', total), ('note', note)):
                if v is not None:
                    self.s[k] = str(v)[:200] if k in ('phase', 'note') else int(v)
        self._send(force)

    def error(self, msg):
        with self.lock:
            self.s['errors'] += 1
            self.s['last_error'] = str(msg)[:300]
        self._send()

    def finish(self, state='done', note=None):
        if self.ended:
            return
        self.ended = True
        with self.lock:
            self.s['state'] = state
            if note is not None:
                self.s['note'] = str(note)[:200]
        self._send(force=True)

    def _atexit(self):
        if not self.ended:
            self.finish('failed' if self.exc else 'stopped', self.exc or 'ended before it finished')

    def _send(self, force=False):
        if not self.token or (not force and time.time() - self.last < self.every):
            return
        self.last = time.time()
        with self.lock:
            body = json.dumps(self.s).encode()
        for site in SITES:
            req = urllib.request.Request(site + '/api/jobs', data=body, method='POST', headers={
                'Content-Type': 'application/json', 'X-Upload-Token': self.token, 'User-Agent': 'flight-watch-job'})
            try:
                urllib.request.urlopen(req, timeout=15).read()
                return
            except urllib.error.HTTPError:
                return  # the server answered: do not try the other name
            except Exception:
                pass
