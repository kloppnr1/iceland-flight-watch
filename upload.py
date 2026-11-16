"""Send the Google calendars (cal/*.txt) to the server, which then rebuilds the page.
Usage: UPLOAD_TOKEN=... python3 upload.py cal
Merge upload: the server keeps files not sent (fetch_gf.py may refresh flight times for only some routes per run)."""
import json, os, sys, glob, urllib.request, urllib.error
d = sys.argv[1] if len(sys.argv) > 1 else 'cal'
files = {os.path.basename(f): open(f).read() for f in glob.glob(os.path.join(d, '*.txt')) + glob.glob(os.path.join(d, '*-details.json'))}
body = json.dumps({'files': files, 'merge': True}).encode()
# the server answers on both names; cloud network settings only accept the plain-letter one
for site in ['https://fly.stromsparer.dk', 'https://fly.xn--strmsparer-2cb.dk']:
    req = urllib.request.Request(site + '/api/cal', data=body, method='POST',
                                 headers={'Content-Type': 'application/json', 'X-Upload-Token': os.environ['UPLOAD_TOKEN'],
                                          'User-Agent': 'flight-watch-upload'})
    try:
        print(urllib.request.urlopen(req, timeout=120).read().decode())
        break
    except urllib.error.HTTPError:
        raise  # the server answered: a real error, do not retry on the other name
    except Exception as e:
        print('could not reach', site, e)
else:
    sys.exit('upload failed: server not reachable')
