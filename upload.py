"""Send the Google calendars (cal/*.txt) to the server, which then rebuilds the page.
Usage: UPLOAD_TOKEN=... python3 upload.py cal"""
import json, os, sys, glob, urllib.request
d = sys.argv[1] if len(sys.argv) > 1 else 'cal'
files = {os.path.basename(f): open(f).read() for f in glob.glob(os.path.join(d, '*.txt')) + glob.glob(os.path.join(d, '*-details.json'))}
req = urllib.request.Request('https://fly.xn--strmsparer-2cb.dk/api/cal', data=json.dumps({'files': files}).encode(),
                             headers={'Content-Type': 'application/json', 'X-Upload-Token': os.environ['UPLOAD_TOKEN'],
                                      'User-Agent': 'flight-watch-upload'}, method='POST')
print(urllib.request.urlopen(req, timeout=120).read().decode())
