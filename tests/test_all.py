"""Tests for the server API, the watch maths and the archive rule. Run: python3 -m unittest discover tests"""
import datetime, json, os, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp()
os.environ.update(DATA_DIR=os.path.join(TMP, 'data'), WATCH_PIN='1234', UPLOAD_TOKEN='tok')
os.makedirs(os.environ['DATA_DIR'])
import api, watches  # noqa: E402

api.WATCH_FILE = os.path.join(TMP, 'watches.json')
api.DEST_FILE = os.path.join(TMP, 'dests.json')
api.CAL = os.path.join(TMP, 'cal')
api.rebuild = lambda *a: None
api.fire_google_task = lambda: None
started = []
api.subprocess = type('S', (), {'Popen': staticmethod(lambda *a, **k: started.append(a))})
srv = ThreadingHTTPServer(('127.0.0.1', 0), api.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f'http://127.0.0.1:{srv.server_port}'
DAY = lambda n: str(datetime.date.today() + datetime.timedelta(days=n))


def call(method, path, body=None, **headers):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def cal(start, prices):
    return {'start': start, 'p': prices}


class Watches(unittest.TestCase):
    C = {'CPH-KEF': {'any': cal(DAY(1), [500, 300, 900, 0, 400]), 'direct': cal(DAY(1), [0, 700])},
         'KEF-CPH': {'any': cal(DAY(1), [800, 800, 200, 600, 100, 50])},
         'KEF-BLL': {'any': cal(DAY(4), [150])}}
    W = {'dest': 'KEF', 'out': ['CPH'], 'home': ['CPH', 'BLL'], 'from': DAY(0), 'to': DAY(30), 'min_days': 2, 'max_days': 3, 'direct': False}

    def test_cheapest_trip_inside_stay(self):
        b = watches.best_for(self.W, self.C)
        # out day 2 (300) + home day 5 (100), 3 days; home day 6 (50) would be 4 days = too long
        self.assertEqual((b['total'], b['d1'], b['h'], b['d2'], b['days']), (400, DAY(2), 'CPH', DAY(5), 3))

    def test_window_and_direct(self):
        self.assertIsNone(watches.best_for({**self.W, 'direct': True}, self.C))  # no direct flights home
        b = watches.best_for({**self.W, 'to': DAY(3)}, self.C)  # the home day must be inside the window too
        self.assertEqual((b['total'], b['d1'], b['d2']), (700, DAY(1), DAY(3)))

    def test_no_prices(self):
        self.assertIsNone(watches.best_for(self.W, {}))


class Api(unittest.TestCase):
    def setUp(self):
        api.BAD.clear()
        p = os.path.join(os.environ['DATA_DIR'], 'anywhere.json')
        if os.path.exists(p):
            os.remove(p)

    def test_anywhere_upload(self):
        self.assertEqual(call('POST', '/api/anywhere', {'cal': {}}, **{'X-Upload-Token': 'no'})[0], 403)
        self.assertEqual(call('POST', '/api/anywhere', {'cal': {'CPH-../x': cal(DAY(1), [1])}}, **{'X-Upload-Token': 'tok'})[0], 400)
        self.assertEqual(call('POST', '/api/anywhere', {'cal': {'CPH-EDI': cal('soon', [1])}}, **{'X-Upload-Token': 'tok'})[0], 400)
        body = {'cal': {'CPH-EDI': cal(DAY(1), [400, 0, 113.4, 250]), 'EDI-CPH': cal(DAY(2), [90]), 'CPH-XXX': cal(DAY(1), [0, 0])}}
        code, r = call('POST', '/api/anywhere', body, **{'X-Upload-Token': 'tok'})
        self.assertEqual((code, r['saved']), (200, 2))
        d = json.load(open(os.path.join(os.environ['DATA_DIR'], 'anywhere.json')))
        self.assertEqual(d['routes']['CPH-EDI'][:2], [DAY(3), 113])  # cheapest day
        self.assertNotIn('CPH-XXX', d['cal'])  # an empty answer is not stored
        self.assertEqual(d['cal']['CPH-EDI']['p'], [400, 0, 113, 250])
        # a later upload of another route keeps this one; the older rows form still works
        call('POST', '/api/anywhere', {'rows': [['BLL', 'GDN', DAY(5), 119]]}, **{'X-Upload-Token': 'tok'})
        d = json.load(open(os.path.join(os.environ['DATA_DIR'], 'anywhere.json')))
        self.assertEqual(sorted(d['routes']), ['BLL-GDN', 'CPH-EDI', 'EDI-CPH'])
        # direct-only calendars: kept apart; an empty one is ignored (Google often answers empty), the earlier one stays
        code, r = call('POST', '/api/anywhere', {'dcal': {'CPH-EDI': cal(DAY(1), [0, 450]), 'CPH-SYD': cal(DAY(1), [])}}, **{'X-Upload-Token': 'tok'})
        self.assertEqual(code, 200)
        d = json.load(open(os.path.join(os.environ['DATA_DIR'], 'anywhere.json')))
        self.assertEqual(d['dcal']['CPH-EDI']['p'], [0, 450])
        self.assertNotIn('CPH-SYD', d['dcal'])
        call('POST', '/api/anywhere', {'dcal': {'CPH-EDI': cal(DAY(1), [])}, 'cal': {'CPH-EDI': cal(DAY(1), [0])}}, **{'X-Upload-Token': 'tok'})
        d = json.load(open(os.path.join(os.environ['DATA_DIR'], 'anywhere.json')))
        self.assertEqual((d['dcal']['CPH-EDI']['p'], d['cal']['CPH-EDI']['p']), ([0, 450], [400, 0, 113, 250]))
        # confirmed "no direct flights" is kept apart; direct prices found later replace it
        call('POST', '/api/anywhere', {'nod': {'AAL-EDI': {'days': 4, 'flights': 90}}}, **{'X-Upload-Token': 'tok'})
        d = json.load(open(os.path.join(os.environ['DATA_DIR'], 'anywhere.json')))
        self.assertEqual(d['nod']['AAL-EDI']['days'], 4)
        call('POST', '/api/anywhere', {'dcal': {'AAL-EDI': cal(DAY(1), [300])}}, **{'X-Upload-Token': 'tok'})
        d = json.load(open(os.path.join(os.environ['DATA_DIR'], 'anywhere.json')))
        self.assertEqual((list(d['nod']), d['dcal']['AAL-EDI']['p']), ([], [300]))
        self.assertEqual(d['routes']['CPH-EDI'][:2], [DAY(3), 113])  # direct prices do not change the cheapest day

    def test_anywhere_cleanup(self):
        p = os.path.join(os.environ['DATA_DIR'], 'anywhere.json')
        old = '2020-01-01T00:00:00+00:00'
        json.dump({'routes': {'AAA-BBB': [DAY(9), 5, old], 'CCC-DDD': [DAY(-1), 5, old]},
                   'cal': {'AAA-BBB': {**cal(DAY(1), [5]), 'at': old}, 'EEE-FFF': cal(DAY(-9), [5, 5])}}, open(p, 'w'))
        call('POST', '/api/anywhere', {'cal': {'HAM-PMI': cal(DAY(1), [210])}}, **{'X-Upload-Token': 'tok'})
        d = json.load(open(p))
        self.assertEqual((list(d['routes']), list(d['cal'])), (['HAM-PMI'], ['HAM-PMI']))

    def test_anywhere_keeps_other_windows(self):
        """An upload with prices for one window only keeps the earlier prices of the other windows (AAR-PMI, Nov 2026)."""
        p = os.path.join(os.environ['DATA_DIR'], 'anywhere.json')
        if os.path.exists(p):
            os.remove(p)
        T = {'X-Upload-Token': 'tok'}
        full = [0] * 140
        full[2], full[70], full[127] = 1800, 1500, 583  # one price in each 61-day window
        call('POST', '/api/anywhere', {'cal': {'AAR-PMI': cal(DAY(1), full)}}, **T)
        # next scan: only the first window came back with prices (the others empty), and day 3 got cheaper
        call('POST', '/api/anywhere', {'cal': {'AAR-PMI': {**cal(DAY(1), [0, 0, 1700]), 'win': [[DAY(1), DAY(61)]]}}}, **T)
        c = json.load(open(p))['cal']['AAR-PMI']
        days = {str(datetime.date.fromisoformat(c['start']) + datetime.timedelta(days=i)): x for i, x in enumerate(c['p']) if x}
        self.assertEqual(days, {DAY(3): 1700, DAY(71): 1500, DAY(128): 583})
        self.assertEqual(json.load(open(p))['routes']['AAR-PMI'][:2], [DAY(128), 583])
        # a window in the middle replaces only its own days; a day there without a price now is dropped
        call('POST', '/api/anywhere', {'cal': {'AAR-PMI': {**cal(DAY(62), [0, 999]), 'win': [[DAY(62), DAY(122)]]}}}, **T)
        c = json.load(open(p))['cal']['AAR-PMI']
        days = {str(datetime.date.fromisoformat(c['start']) + datetime.timedelta(days=i)): x for i, x in enumerate(c['p']) if x}
        self.assertEqual(days, {DAY(3): 1700, DAY(63): 999, DAY(128): 583})
        # prices from an upload over a week old are not kept
        old = api.merge_cal(c, {**cal(DAY(1), [5]), 'win': [[DAY(1), DAY(61)]]},
                            (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=8)).isoformat(timespec='seconds'))
        self.assertEqual((old['start'], old['p']), (DAY(1), [5]))

    def test_calendar_upload(self):
        files = {f'{a}-KEF-any.txt': DAY(1) + '\n50 0 61\n' for a in ['CPH', 'BLL', 'AAL', 'HAM', 'OSL', 'ARN']}
        self.assertEqual(call('POST', '/api/cal', {'files': files}, **{'X-Upload-Token': 'bad'})[0], 403)
        self.assertEqual(call('POST', '/api/cal', {'files': {'../x.txt': 'a'}, 'merge': True}, **{'X-Upload-Token': 'tok'})[0], 400)
        self.assertEqual(call('POST', '/api/cal', {'files': {'CPH-KEF-any.txt': DAY(1) + '\n5 x'}, 'merge': True}, **{'X-Upload-Token': 'tok'})[0], 400)
        self.assertEqual(call('POST', '/api/cal', {'files': dict(list(files.items())[:2])}, **{'X-Upload-Token': 'tok'})[0], 400)  # too few
        self.assertEqual(call('POST', '/api/cal', {'files': files}, **{'X-Upload-Token': 'tok'}), (200, {'saved': 6}))
        self.assertEqual(call('POST', '/api/cal', {'files': {'CPH-AGP-any.txt': DAY(1) + '\n9'}, 'merge': True}, **{'X-Upload-Token': 'tok'})[0], 200)
        self.assertEqual(len(os.listdir(api.CAL)), 7)  # merge kept the other six
        self.assertTrue(started)  # the rebuild was started

    def test_job_reports(self):
        path = os.path.join(os.environ['DATA_DIR'], 'jobs.json')
        if os.path.exists(path):
            os.remove(path)
        T = {'X-Upload-Token': 'tok'}
        r1 = {'job': 'google', 'run': '2026-11-18T05:00:00+00:00', 'state': 'running', 'phase': 'calendars', 'done': 3, 'total': 50}
        self.assertEqual(call('POST', '/api/jobs', r1, **{'X-Upload-Token': 'no'})[0], 403)
        self.assertEqual(call('POST', '/api/jobs', {**r1, 'job': '../x'}, **T)[0], 400)
        self.assertEqual(call('POST', '/api/jobs', {**r1, 'state': 'odd'}, **T)[0], 400)
        self.assertEqual(call('POST', '/api/jobs', r1, **T)[0], 200)
        call('POST', '/api/jobs', {**r1, 'done': 9}, **T)
        d = json.load(open(path))
        self.assertEqual((d['jobs']['google']['done'], d['history']), (9, []))
        # a new run while the last one never ended: the old one goes to the history as lost
        r2 = {**r1, 'run': '2026-11-18T17:00:00+00:00', 'done': 0}
        call('POST', '/api/jobs', r2, **T)
        call('POST', '/api/jobs', {**r2, 'state': 'done', 'note': 'all done'}, **T)
        call('POST', '/api/jobs', {**r2, 'done': 5}, **T)  # late report after the end: ignored
        d = json.load(open(path))
        self.assertEqual([(h['run'], h['state']) for h in d['history']], [(r1['run'], 'lost'), (r2['run'], 'done')])
        self.assertEqual((d['jobs']['google']['state'], d['jobs']['google']['note']), ('done', 'all done'))

    def test_pin_lockout(self):
        w = {'from': DAY(1), 'to': DAY(60), 'min_days': 3, 'max_days': 7}
        for _ in range(5):
            self.assertEqual(call('POST', '/api/watches', w, **{'X-Pin': '0000', 'X-Forwarded-For': '9.9.9.9, 1.1.1.1'})[0], 403)
        # the sixth try is refused even with the right PIN; another address is not locked out
        self.assertEqual(call('POST', '/api/watches', w, **{'X-Pin': '1234', 'X-Forwarded-For': '8.8.8.8, 1.1.1.1'})[0], 429)
        self.assertEqual(call('DELETE', '/api/watches/0123456789', **{'X-Pin': '0000', 'X-Forwarded-For': '2.2.2.2'})[0], 403)
        self.assertFalse(api.pin_blocked('1.1.1.1', now=__import__('time').time() + 901))  # over after 15 minutes

    def test_watch_add_and_delete(self):
        json.dump({'cal': Watches.C}, open(os.path.join(os.environ['DATA_DIR'], 'latest.json'), 'w'))
        w = {'out': ['CPH'], 'from': DAY(0), 'to': DAY(30), 'min_days': 2, 'max_days': 3, 'direct': False, 'target': 500}
        self.assertEqual(call('POST', '/api/watches', {**w, 'to': DAY(-5)}, **{'X-Pin': '1234'})[0], 400)
        code, ws = call('POST', '/api/watches', w, **{'X-Pin': '1234'})
        self.assertEqual((code, ws[0]['best']['total'], ws[0]['good']), (200, 400, True))
        self.assertEqual(call('DELETE', '/api/watches/' + ws[0]['id'], **{'X-Pin': '1234'}), (200, []))


class Archive(unittest.TestCase):
    def test_same_prices_are_archived_once(self):
        d = tempfile.mkdtemp()
        app, data, cald = os.path.join(d, 'app'), os.path.join(d, 'data'), os.path.join(d, 'cal')
        os.makedirs(app), os.makedirs(cald)
        for f in ['build.py', 'watches.py', 'patterns.py']:
            open(os.path.join(app, f), 'w').write(open(os.path.join(ROOT, f)).read())
        json.dump([{'code': 'AGP', 'name': 'Málaga', 'place': 'Spain'}], open(os.path.join(app, 'dests.json'), 'w'))  # not KEF: no board fetch
        runs = lambda: len(os.listdir(os.path.join(data, 'runs')))

        def build(prices, n):
            open(os.path.join(cald, 'CPH-AGP-any.txt'), 'w').write(DAY(1) + '\n' + prices + '\n')
            # a different clock minute per build, as on the server
            code = f"import datetime as d\nclass T(d.datetime):\n    @classmethod\n    def now(c, tz=None): return d.datetime(2030, 1, 1, 12, {n}, tzinfo=tz)\nd.datetime = T\nimport runpy, sys\nsys.argv = ['build.py', {cald!r}, 'none.json', {data!r}]\nsys.path.insert(0, {app!r})\nrunpy.run_path({os.path.join(app, 'build.py')!r}, run_name='__main__')"
            r = subprocess.run([sys.executable, '-c', code], cwd=app, capture_output=True, text=True, env={**os.environ, 'WATCH_FILE': os.path.join(app, 'w.json')})
            self.assertEqual(r.returncode, 0, r.stderr[-500:])

        build('50 60', 1); self.assertEqual(runs(), 1)
        build('50 60', 2); self.assertEqual(runs(), 1)  # a rebuild with the same prices adds nothing
        build('50 40', 3); self.assertEqual(runs(), 2)  # new prices are archived
        ch = json.load(open(os.path.join(data, 'changes.json')))
        self.assertEqual([(c['what'], c['old'], c['new']) for c in ch], [('Price drop', 600, 400)])
        self.assertEqual(json.load(open(os.path.join(data, 'patterns.json')))['checks'], 2)


if __name__ == '__main__':
    unittest.main()
