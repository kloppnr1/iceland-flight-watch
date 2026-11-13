# Travelpayouts: one-way prices + round trips (out month, back 1-6 months later), same airport
import json,os,datetime,urllib.request,urllib.error,time
from concurrent.futures import ThreadPoolExecutor
T=os.environ['TP_TOKEN'];DK=['CPH','BLL','AAL','HAM']
B='https://api.travelpayouts.com/aviasales/v3/prices_for_dates?currency=dkk&sorting=price&'
def get(u):
    err=None
    for i in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(B+u,headers={'X-Access-Token':T}),timeout=12)).get('data',[])
        except urllib.error.HTTPError as e:
            err=e
            if e.code==400: break
            time.sleep(3*(i+1))
        except Exception as e: err=e; time.sleep(3*(i+1))
    print("failed",u[:60],repr(err),flush=True); return []
today=datetime.date.today()
def mon(i):
    y,m=today.year,today.month+i; y+=(m-1)//12; m=(m-1)%12+1; return f'{y}-{m:02d}'
jobs=[]
for a in DK:
  for x,y in [(a,'KEF'),('KEF',a)]:
    for i in range(12):
      for direct in ['false','true']:
        jobs.append(('ow',x,y,f'origin={x}&destination={y}&departure_at={mon(i)}&one_way=true&direct={direct}&limit=100'))
  for i in range(12):
    for k in range(1,2):  # API only allows return in the month after departure
      jobs.append(('rt',a,'KEF',f'origin={a}&destination=KEF&departure_at={mon(i)}&return_at={mon(i+k)}&one_way=false&limit=30'))
with ThreadPoolExecutor(3) as ex: res=list(ex.map(lambda j:get(j[3]),jobs))
ow=[];rt=[]
for (kind,x,y,_),data in zip(jobs,res):
  for r in data:
    if r.get('origin_airport')!=x or r.get('destination_airport')!=y: continue
    L='https://www.aviasales.com'+r['link']
    if kind=='ow':
      ow.append(dict(o=x,d=y,date=r['departure_at'][:10],dep=r['departure_at'][11:16],al=r['airline'],fn=r['airline']+str(r.get('flight_number','')),stops=r.get('transfers',0),min=r.get('duration_to') or r.get('duration'),p=r['price'],link=L))
    elif r.get('return_at'):
      rt.append(dict(o=x,out=r['departure_at'][:10],outdep=r['departure_at'][11:16],back=r['return_at'][:10],backdep=r['return_at'][11:16],al=r['airline'],stops=r.get('transfers',0),rstops=r.get('return_transfers',0),p=r['price'],link=L))
def dd(l,keys):
    s={};[s.setdefault(tuple(x[k] for k in keys),x) for x in sorted(l,key=lambda z:z['p'])];return list(s.values())
ow=dd(ow,['o','d','date','dep','fn']);rt=dd(rt,['o','out','outdep','back','backdep','al'])
json.dump({'ow':ow,'rt':rt},open('tp.json','w'))
print(len(jobs),'requests',len(ow),'one-way',len(rt),'round trips')
