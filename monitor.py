#!/usr/bin/env python3
"""CSN -> aceleración horizontal -> MP4 -> X + Instagram Reel musical + fotografía.
Python >=3.11. Dependencias: requests requests-oauthlib beautifulsoup4 numpy scipy
obspy matplotlib cartopy; Node twitter-text@3.1.0; ffmpeg/ffprobe.
Requiere repo GitHub público para alojar archivos usados por Meta.
Las pistas musicales deben ser propias o tener licencia apropiada.
"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests_oauthlib import OAuth1

UTC = timezone.utc
CSN = 'https://www.sismologia.cl'
SECRETS = ('X_API_KEY','X_API_SECRET','X_ACCESS_TOKEN','X_ACCESS_TOKEN_SECRET',
           'GITHUB_TOKEN','IG_ACCESS_TOKEN','INSTAGRAM_TOKEN')
SECRET_NAMES = SECRETS  # Compatibilidad con test_monitor.py y validacion del workflow
STATES = {'pendiente','procesando','enviando','publicado','simulado','resultado_incierto','expirado'}
IG_STATES = {'pendiente','procesando','publicando','publicado','resultado_incierto'}


def utcnow(): return datetime.now(UTC)
def iso(t): return t.astimezone(UTC).isoformat().replace('+00:00','Z')
def date(s):
    t = datetime.fromisoformat(s.replace('Z','+00:00'))
    if t.tzinfo is None: raise ValueError('Fecha sin zona horaria')
    return t.astimezone(UTC)
def safe(v):
    message = str(v)
    for key in SECRETS:
        if os.getenv(key): message = message.replace(os.environ[key], '[REDACTADO]')
    return message.replace('\n',' ').replace('\r',' ')[:500]
def log(v): print(iso(utcnow()), safe(v), flush=True)
def flag(key,default=False):
    value = os.getenv(key,str(default)).strip().lower()
    if value not in ('true','false','yes','no','0','1'): raise ValueError('Booleano inválido: '+key)
    return value in ('true','yes','1')
def atomic(path,value):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=path.parent,delete=False) as f:
        name=f.name; json.dump(value,f,indent=2,ensure_ascii=False,allow_nan=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(name,path)

@dataclass
class Config:
    mag:float=4.; lookback:float=24.; pre:float=60.; post:float=120.; margin:float=30.; latency:float=180.
    fps:int=4; step:float=1.; workers:int=4; timeout:float=30.; max_events:int=2
    budget:float=2700.; event_budget:float=800.; x_wait:float=900.
    radius:float=250.; stations:int=15; candidates:int=30; support:float=100.; triangle:float=150.
    max_attempts:int=12; backoff:float=600.; publish_x:bool=False; publish_ig:bool=False; reconcile:bool=False
    state_dir:str='.state'; output:str='output'; branch:str='monitor-state'
    fdsn:str='https://eew.csn.uchile.cl'; graph_version:str='v25.0'
    ig_age:float=72.; ig_reserve:float=600.; music_file:str='assets/music/fondo.mp3'; music_volume:float=.18
    music_url:str='https://commons.wikimedia.org/wiki/Special:Redirect/file/Promise.ogg'
    ig_login_type:str='instagram'
    @property
    def publish(self):return self.publish_x
    @publish.setter
    def publish(self,value):self.publish_x=bool(value)
    @classmethod
    def env(cls):
        c=cls()
        names=dict(mag='MAG_MIN',lookback='LOOKBACK_HOURS',pre='PRE_SEG',post='POST_SEG',margin='MARGEN_SEG',latency='DATA_LATENCY_SEC',
           fps='FPS',step='FRAME_STEP_SEC',workers='DOWNLOAD_WORKERS',timeout='HTTP_TIMEOUT',max_events='MAX_EVENTS_PER_RUN',
           budget='MAX_RUNTIME_SEC',event_budget='EVENT_TIMEOUT_SEC',x_wait='X_PROCESS_TIMEOUT_SEC',radius='MAX_DISTANCE_KM',
           stations='MAX_STATIONS',candidates='MAX_CANDIDATES',support='SUPPORT_KM',triangle='MAX_TRIANGLE_KM',
           max_attempts='MAX_ATTEMPTS',backoff='RETRY_BASE_SEC',state_dir='STATE_DIR',output='OUTPUT_DIR',
           branch='STATE_BRANCH',fdsn='FDSN_BASE',graph_version='IG_GRAPH_VERSION',ig_age='IG_PENDING_MAX_AGE_HOURS',
           ig_reserve='IG_RESERVED_SEC',music_file='REEL_MUSIC_FILE',music_volume='REEL_MUSIC_VOLUME',music_url='REEL_MUSIC_URL',
           ig_login_type='IG_LOGIN_TYPE')
        for field,key in names.items():
            typ=cls.__annotations__[field]
            setattr(c,field,{'float':float,'int':int,'str':str}[typ](os.getenv(key,str(getattr(c,field)))))
        c.publish_x=flag('PUBLISH_TO_X'); c.publish_ig=flag('PUBLISH_TO_INSTAGRAM'); c.reconcile=flag('RECONCILE_X')
        for field in names:
            value=getattr(c,field)
            if isinstance(value,(int,float)) and (not math.isfinite(value) or (value<=0 and field!='music_volume')):
                raise ValueError('Configuración numérica inválida: '+field)
        if not 0<=c.music_volume<=1 or c.margin<30 or c.budget<180 or c.x_wait>900:raise ValueError('Presupuestos/audio inválidos')
        if c.stations>30 or c.candidates<c.stations or c.workers>8:raise ValueError('Límites de estaciones/workers')
        n=(c.pre+c.post)/c.step
        if abs(n-round(n))>1e-6 or not 3<=n/c.fps<=140:raise ValueError('Duración de Reel inválida (3-140 s)')
        if not re.fullmatch(r'v\d+\.\d+',c.graph_version):raise ValueError('Versión Graph inválida')
        c.ig_login_type=c.ig_login_type.strip().lower()
        if c.ig_login_type not in ('instagram','facebook'):raise ValueError('IG_LOGIN_TYPE debe ser instagram o facebook')
        return c

class Failure(RuntimeError):pass
class PersistenceError(Failure):pass
class BudgetError(Failure):pass
class SourceError(Failure):pass
class StructureError(SourceError):pass
class PublishError(Failure):
    def __init__(self,msg,status=0,uncertain=False,reset=None):
        super().__init__(msg);self.status=status;self.uncertain=uncertain;self.reset=reset
        self.global_stop=status in (401,402,403,429) or status>=500 or uncertain
class XError(PublishError):pass
class Clock:
    def __init__(self,seconds):self.end=time.monotonic()+seconds
    def left(self):return self.end-time.monotonic()
    def check(self,reserve=0):
        if self.left()<=reserve:raise BudgetError('Presupuesto agotado')

def obtener(url,c,clock,params=None):
    for attempt in range(3):
        clock.check(5)
        try:
            with requests.get(url,params=params,stream=True,timeout=max(2,min(c.timeout,clock.left()-2)),
                              headers={'User-Agent':'CSN-observed-motion/3.1'}) as r:
                if r.status_code==204:return b''
                if r.status_code in (429,500,502,503,504) and attempt<2:
                    raw=r.headers.get('Retry-After','2')
                    try:delay=float(raw)
                    except ValueError:
                        try:delay=(parsedate_to_datetime(raw)-utcnow()).total_seconds()
                        except Exception:delay=2
                    delay=max(2**attempt,delay)
                    if delay>30 or delay+5>=clock.left():raise SourceError('HTTP diferido '+str(r.status_code))
                    time.sleep(delay);continue
                if r.status_code!=200:raise SourceError(f'GET {urlparse(url).path}: HTTP {r.status_code}')
                chunks=[];total=0
                for chunk in r.iter_content(65536):
                    clock.check(2); total+=len(chunk)
                    if total>100*1024*1024:raise SourceError('Descarga excede 100 MiB')
                    chunks.append(chunk)
                return b''.join(chunks)
        except requests.RequestException as exc:
            if attempt==2:raise SourceError(type(exc).__name__) from None
            time.sleep(2**attempt)
    raise SourceError('Descarga agotada')

def norm(s):return ''.join(x for x in unicodedata.normalize('NFD',str(s).lower()) if unicodedata.category(x)!='Mn').strip()
def number(s):
    m=re.search(r'[-+]?\d+(?:[.,]\d+)?',str(s))
    if not m:raise StructureError('Número ausente')
    x=float(m.group().replace(',','.'))
    if not math.isfinite(x):raise StructureError('Número inválido')
    return x

def validate_event(e):
    if not isinstance(e.get('id'),str) or not re.fullmatch(r'\d+',e['id']):raise ValueError('ID CSN inválido')
    date(e['origin'])
    for key in ('mag_display','reference','url'):
        if not isinstance(e.get(key),str) or not e[key]:raise ValueError('Falta '+key)
    for key,lo,hi in (('lat',-90,90),('lon',-180,180),('mag',-2,10)):
        x=e.get(key)
        if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or not lo<=x<=hi:raise ValueError('Evento inválido: '+key)
    dep=e.get('depth')
    if dep is not None and (not isinstance(dep,(float,int)) or not math.isfinite(dep) or not 0<=dep<=800):raise ValueError('Profundidad inválida')
    u=urlparse(e['url'])
    if u.scheme!='https' or u.hostname!='www.sismologia.cl' or not re.fullmatch(r'/sismicidad/informes/\d{4}/\d{2}/'+re.escape(e['id'])+r'\.html',u.path):
        raise ValueError('URL informe inválida')
    return e

def leer_evento(html,url):
    soup=BeautifulSoup(html,'html.parser');fields={}
    for row in soup.select('tr'):
        cells=row.find_all(['td','th'])
        if len(cells)==2:fields[norm(cells[0].get_text(' ',strip=True))]=cells[1].get_text(' ',strip=True)
    try:
        origin=datetime.strptime(fields['hora utc'],'%H:%M:%S %d/%m/%Y').replace(tzinfo=UTC)
        dep=fields.get('profundidad','')
        return validate_event(dict(id=Path(urlparse(url).path).stem,origin=iso(origin),mag=number(fields['magnitud']),
            mag_display=f"{number(fields['magnitud']):.1f}",reference=fields['referencia'],lat=number(fields['latitud']),
            lon=number(fields['longitud']),depth=number(dep) if re.search(r'\d',dep) else None,url=url))
    except (KeyError,ValueError,TypeError) as exc:raise StructureError('Informe incompatible '+type(exc).__name__) from None

def catalogue_links(html,url,daily,c):
    soup=BeautifulSoup(html,'html.parser')
    tables=[t for t in soup.find_all('table') if any('magnitud' in norm(h.get_text()) for h in t.find_all('th'))]
    if not tables or (daily and 'utc' not in norm(soup.get_text(' '))):raise StructureError('Tabla catálogo/UTC incompatible')
    links=set()
    for table in tables:
        headers=[norm(h.get_text(' ',strip=True)) for h in table.find_all('th')]
        mi=next(i for i,h in enumerate(headers) if 'magnitud' in h)
        if daily and not any('fecha utc' in h for h in headers):raise StructureError('Falta Fecha UTC')
        for row in table.find_all('tr'):
            cells=row.find_all('td',recursive=False)
            if not cells:continue
            if len(cells)!=len(headers):raise StructureError('Fila catálogo incompatible')
            anchors=[urljoin(url,a['href']) for a in row.find_all('a',href=True) if re.search(r'/sismicidad/informes/\d{4}/\d{2}/\d+\.html$',urljoin(url,a['href']))]
            if not anchors:raise StructureError('Fila sin informe')
            if number(cells[mi].get_text(' ',strip=True))>=c.mag:
                for a in anchors:
                    u=urlparse(a)
                    if u.hostname not in ('www.sismologia.cl','sismologia.cl'):raise StructureError('Enlace externo')
                    links.add(CSN+u.path)
    return links

def homepage_covers(html,beginning):
    soup=BeautifulSoup(html,'html.parser');zone=ZoneInfo('America/Santiago')
    tables=[t for t in soup.find_all('table') if any('magnitud' in norm(h.get_text()) for h in t.find_all('th'))]
    if len(tables)!=1:return False
    stamps=[]
    for row in tables[0].find_all('tr'):
        cells=row.find_all('td',recursive=False)
        if not cells:continue
        m=re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',cells[0].get_text(' ',strip=True))
        if not m:return False
        naive=datetime.strptime(m.group(),'%Y-%m-%d %H:%M:%S')
        first=naive.replace(tzinfo=zone,fold=0);second=naive.replace(tzinfo=zone,fold=1)
        if first.utcoffset()!=second.utcoffset():return False
        instant=first.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None)!=naive:return False
        stamps.append(instant)
    return bool(stamps and stamps[-1]<=beginning and all(a>=b for a,b in zip(stamps,stamps[1:])))

def discover(c,clock,start,end):
    urls=[CSN+'/'];d=start.date()
    while d<=end.date():urls.append(CSN+d.strftime('/sismicidad/catalogo/%Y/%m/%Y%m%d.html'));d+=timedelta(days=1)
    links=set();errors=[];warnings=[];valid=0;homepage=None;events={}
    for i,url in enumerate(urls):
        try:
            b=obtener(url,c,clock);links.update(catalogue_links(b,url,i>0,c));valid+=1
            if i==0:homepage=b
        except (SourceError,BudgetError) as exc:
            detail=dict(url=url,type=type(exc).__name__,error=safe(exc))
            begin=max(start,end.replace(hour=0,minute=0,second=0,microsecond=0))
            if i==len(urls)-1 and homepage is not None and re.search(r'HTTP (403|404)\b',str(exc)) and homepage_covers(homepage,begin):warnings.append(detail)
            else:errors.append(detail)
    for url in sorted(links):
        try:
            e=leer_evento(obtener(url,c,clock),url)
            if start<=date(e['origin'])<=end and e['mag']>=c.mag:events[e['id']]=e
        except (SourceError,BudgetError) as exc:errors.append(dict(url=url,type=type(exc).__name__,error=safe(exc)))
    status='valida' if not errors else 'parcial' if valid else ('incompatible' if any(x['type']=='StructureError' for x in errors) else 'inaccesible')
    return list(events.values()),dict(status=status,pages_ok=valid,pages=len(urls),errors=errors,warnings=warnings)

def ig_default():return dict(status='pendiente',container_id=None,media_id=None,caption='',error='',attempts=0,updated=None,post_started=False)
def record(e,when=None):
    now=iso(when or utcnow())
    return dict(event=e,status='pendiente',attempts=0,next_attempt=now,created=now,updated=now,error='',media_id=None,tweet_id=None,text='',instagram=ig_default(),instagram_feed=ig_default())
def empty_state(mode):return dict(schema=1,mode=mode,account_id=None,ig_account_id=None,revision=str(uuid.uuid4()),updated=iso(utcnow()),events={})
def validate_state(s,mode):
    try:
        if s['schema']!=1 or s['mode']!=mode or not isinstance(s['events'],dict):raise ValueError('Esquema')
        date(s['updated'])
        for key in ('account_id','ig_account_id'):
            if s.get(key) is not None and not re.fullmatch(r'\d+',str(s[key])):raise ValueError(key)
        for eid,r in s['events'].items():
            validate_event(r['event'])
            if r['event']['id']!=eid or r['status'] not in STATES:raise ValueError('Estado evento')
            if not isinstance(r['attempts'],int) or r['attempts']<0:raise ValueError('Intentos')
            for key in ('created','updated','next_attempt'):date(r[key])
            for key in ('media_id','tweet_id','text','error'):
                if key not in r:raise ValueError(key)
            if r['status']=='publicado' and not re.fullmatch(r'\d+',str(r['tweet_id'])):raise ValueError('X sin ID')
            for field in ('instagram','instagram_feed'):
                ig=r[field]
                if ig['status'] not in IG_STATES or not isinstance(ig['attempts'],int) or ig['attempts']<0:raise ValueError(field)
                if ig['status']=='publicado' and not re.fullmatch(r'\d+',str(ig['media_id'])):raise ValueError(field+' sin ID')
        return s
    except (KeyError,ValueError,TypeError) as exc:raise PersistenceError('Estado inválido: '+safe(exc)) from None

class Store:
    def __init__(self,c):
        self.c=c;self.mode='live' if (c.publish_x or c.publish_ig) else 'simulation'
        self.path=Path(c.state_dir)/(self.mode+'.json');self.repo=os.getenv('GITHUB_REPOSITORY','');self.token=os.getenv('GITHUB_TOKEN','')
        self.remote=bool(self.repo and self.token);self.sha=None
        if self.mode=='live' and not self.remote:raise PersistenceError('GITHUB_TOKEN y GITHUB_REPOSITORY obligatorios')
        if self.repo and not re.fullmatch(r'[\w.-]+/[\w.-]+',self.repo):raise PersistenceError('GITHUB_REPOSITORY inválido')
        self.base='https://api.github.com/repos/'+self.repo;self.file='/contents/state/'+self.mode+'.json'
    def api(self,method,path,**kwargs):
        try:return requests.request(method,self.base+path,timeout=self.c.timeout,headers={
            'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'},**kwargs)
        except requests.RequestException as exc:raise PersistenceError('GitHub '+type(exc).__name__) from None
    def load(self,init=False):
        if not self.remote:
            try:s=json.loads(self.path.read_text()) if self.path.exists() else empty_state(self.mode)
            except Exception:raise PersistenceError('Estado local ilegible') from None
        else:
            r=self.api('GET',self.file,params={'ref':self.c.branch})
            if r.status_code==404 and init:
                branch=self.api('GET','/git/ref/heads/'+quote(self.c.branch,safe=''))
                if branch.status_code==404:
                    repo=self.api('GET','')
                    if repo.status_code!=200:raise PersistenceError('Repositorio inaccesible')
                    ref=self.api('GET','/git/ref/heads/'+quote(repo.json()['default_branch'],safe=''))
                    if ref.status_code!=200:raise PersistenceError('Rama principal inaccesible')
                    cr=self.api('POST','/git/refs',json={'ref':'refs/heads/'+self.c.branch,'sha':ref.json()['object']['sha']})
                    if cr.status_code!=201:raise PersistenceError('No se creó rama de estado')
                elif branch.status_code!=200:raise PersistenceError('Rama de estado inaccesible')
                s=empty_state(self.mode);self.save(s)
            elif r.status_code==200:
                try:
                    body=r.json();self.sha=body['sha'];s=json.loads(base64.b64decode(body['content']))
                except Exception:raise PersistenceError('Estado remoto ilegible') from None
            else:raise PersistenceError(f'Leer estado HTTP {r.status_code}; use --init-state en repositorio nuevo')
        if not isinstance(s,dict) or not isinstance(s.get('events'),dict):raise PersistenceError('Estado corrupto')
        s.setdefault('ig_account_id',None)
        for r in s['events'].values():
            for field in ('instagram','instagram_feed'):
                item=r.setdefault(field,ig_default())
                if not isinstance(item,dict):raise PersistenceError('Estado Instagram inválido')
                for key,value in ig_default().items():item.setdefault(key,value)
                if item['status']=='publicando':
                    item.update(status='resultado_incierto',error='Posible envío Meta; verificar manualmente, NO reenviar')
        validate_state(s,self.mode);atomic(self.path,s);return s
    def save(self,s):
        s['updated']=iso(utcnow());s['revision']=str(uuid.uuid4());validate_state(s,self.mode);atomic(self.path,s)
        if not self.remote:return
        raw=json.dumps(s,ensure_ascii=False,allow_nan=False).encode()
        if len(raw)>900000:raise PersistenceError('Estado remoto demasiado grande')
        payload=dict(message='monitor state [skip ci]',branch=self.c.branch,content=base64.b64encode(raw).decode())
        if self.sha:payload['sha']=self.sha
        r=self.api('PUT',self.file,json=payload)
        if r.status_code not in (200,201):raise PersistenceError(f'CAS HTTP {r.status_code}; detener')
        try:
            b=r.json();commit=b['commit']['sha'];blob=b['content']['sha']
            if not all(re.fullmatch('[0-9a-f]{40,64}',v) for v in (commit,blob)):raise ValueError()
        except Exception:raise PersistenceError('PUT posiblemente confirmado sin SHA; NO repetir') from None
        for attempt in range(3):
            if attempt:time.sleep(attempt)
            try:confirmed=self.api('GET',self.file,params={'ref':commit})
            except PersistenceError:continue
            if confirmed.status_code==200:
                try:
                    body=confirmed.json();correct=body['sha']==blob and json.loads(base64.b64decode(body['content']))==s
                except Exception:correct=None
                if correct:self.sha=blob;return
                if correct is False:raise PersistenceError('Estado remoto confirmado difiere')
                continue
            if confirmed.status_code not in (404,500,502,503,504):raise PersistenceError('Confirmación HTTP '+str(confirmed.status_code))
        raise PersistenceError('Persistencia sin confirmación; NO repetir PUT')

def recover(s,start,now=None):
    now=now or utcnow();counts={'recovered':0,'expired':0}
    for r in s['events'].values():
        if r['status']=='enviando':r.update(status='resultado_incierto',error='Interrupción POST X')
        elif r['status']=='procesando':r.update(status='pendiente',next_attempt=iso(now));counts['recovered']+=1
        if r['status']=='pendiente' and date(r['event']['origin'])<start:r.update(status='expirado');counts['expired']+=1
        for field in ('instagram','instagram_feed'):
            item=r.setdefault(field,ig_default())
            if item['status']=='publicando' or (item['status']=='procesando' and item.get('post_started')):
                item.update(status='resultado_incierto',error='Interrupción posible media_publish; no reenviar')
            elif item['status']=='procesando':item.update(status='pendiente',error='Trabajo recuperado')
    return counts

def eligible(r,c,start,end):return r['status']=='pendiente' and r['attempts']<c.max_attempts and start<=date(r['event']['origin'])<=end and date(r['next_attempt'])<=end
def defer_recent(r,c,now):
    ready=date(r['event']['origin'])+timedelta(seconds=c.post+c.margin+c.latency)
    if now<ready:r.update(next_attempt=iso(ready),error='Esperando ventana POST y latencia');return True
    return False
def retry(r,c,exc):
    delay=min(3600,c.backoff*2**min(max(r['attempts']-1,0),6))
    r.update(status='pendiente',next_attempt=iso(utcnow()+timedelta(seconds=delay)),error=safe(exc),updated=iso(utcnow()))

def tweet_text(e):
    reference=unicodedata.normalize('NFC',' '.join(e['reference'].split()))[:2000]
    local=date(e['origin']).astimezone(ZoneInfo('America/Santiago'))
    depth='no informada' if e['depth'] is None else f"{e['depth']:g} km"
    suffix=f"\n{local:%d/%m/%Y %H:%M:%S}\nProfundidad: {depth}.\nEpicentro: lat {e['lat']:.4f}°, lon {e['lon']:.4f}°.\n{e['url']}"
    prefix=f"Aceleracion del Terreno\nSismo M {e['mag_display']} | "
    texts=[prefix+reference+suffix]+[prefix+reference[:i].rstrip()+'…'+suffix for i in range(min(len(reference)-1,280),-1,-1)]
    js="const t=require('twitter-text');const fs=require('fs');const a=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(a.find(s=>t.parseTweet(s).valid)||null));"
    p=subprocess.run(['node','-e',js],input=json.dumps(texts),text=True,capture_output=True,timeout=30)
    if p.returncode:raise Failure('Instalar npm twitter-text@3.1.0')
    result=json.loads(p.stdout)
    if not result:raise Failure('Texto X excede límite')
    return result

class XClient:
    def __init__(self,c,clock):
        self.c=c;self.clock=clock;keys=[os.getenv(k,'') for k in SECRETS[:4]]
        if not all(keys):raise XError('Credenciales X incompletas',401)
        self.auth=OAuth1(*keys)
    def call(self,method,path,empty=False,**kwargs):
        self.clock.check(90);posting=method=='POST' and path=='/tweets'
        try:r=requests.request(method,'https://api.x.com/2'+path,auth=self.auth,
             timeout=max(2,min(self.c.timeout,self.clock.left()-60)),allow_redirects=False,**kwargs)
        except requests.RequestException as exc:raise XError('X '+type(exc).__name__,uncertain=posting) from None
        if not 200<=r.status_code<300:
            try:
                b=r.json();details=safe(json.dumps({k:b[k] for k in ('title','detail','code','message','errors') if k in b},ensure_ascii=False))
            except (ValueError,TypeError,AttributeError):details='No JSON'
            reset=r.headers.get('x-rate-limit-reset','')
            raise XError(f'X {path} HTTP {r.status_code}: {details}',r.status_code,
                uncertain=posting and (r.status_code>=500 or r.status_code in (408,409)),
                reset=float(reset) if str(reset).isdigit() else None)
        if empty and (r.status_code==204 or not r.content):return {}
        try:
            b=r.json()
            if not isinstance(b,dict) or b.get('errors'):raise ValueError()
            return b
        except ValueError:raise XError('Respuesta X incompatible',uncertain=posting) from None
    def identity(self):
        b=self.call('GET','/users/me').get('data',{});uid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',uid):raise XError('Identidad X inválida',401)
        return uid
    def upload(self,video):
        data=self.call('POST','/media/upload/initialize',json={'media_type':'video/mp4','media_category':'tweet_video','total_bytes':Path(video).stat().st_size}).get('data',{})
        mid=str(data.get('id',''))
        if not re.fullmatch(r'\d+',mid):raise XError('X media ID inválido')
        with open(video,'rb') as f:
            index=0
            while chunk:=f.read(4*1024*1024):
                self.call('POST',f'/media/upload/{mid}/append',empty=True,data={'segment_index':str(index)},
                    files={'media':('segment.mp4',chunk,'application/octet-stream')});index+=1
        data=self.call('POST',f'/media/upload/{mid}/finalize').get('data',{})
        if str(data.get('id',''))!=mid:raise XError('X finalize inválido')
        until=min(time.monotonic()+self.c.x_wait,self.clock.end-100)
        while data.get('processing_info'):
            info=data['processing_info'];status=info.get('state')
            if status=='succeeded':break
            if status=='failed':raise XError('X rechazó vídeo')
            if status not in ('pending','in_progress'):raise XError('X estado vídeo desconocido')
            delay=max(1,float(info.get('check_after_secs',5)))
            if time.monotonic()+delay>=until:raise XError('Timeout procesamiento X')
            time.sleep(delay);data=self.call('GET','/media/upload',params={'command':'STATUS','media_id':mid}).get('data',{})
        return mid
    def create(self,text,mid):
        data=self.call('POST','/tweets',json={'text':text,'media':{'media_ids':[mid]}}).get('data',{})
        tid=str(data.get('id',''))
        if not re.fullmatch(r'\d+',tid):raise XError('Tweet ID no confirmado',uncertain=True)
        return tid
    def reconcile(self,state):
        unsure=[r for r in state['events'].values() if r['status']=='resultado_incierto']
        if not unsure:return
        posts=[];page=None
        for _ in range(5):
            params={'max_results':100,'tweet.fields':'created_at,entities,attachments'}
            if page:params['pagination_token']=page
            result=self.call('GET','/users/'+state['account_id']+'/tweets',params=params)
            posts.extend(result.get('data',[]));page=result.get('meta',{}).get('next_token')
            if not page:break
        for r in unsure:
            found=[]
            for item in posts:
                msg=item.get('text','')
                for u in item.get('entities',{}).get('urls',[]):msg=msg.replace(u.get('url',''),u.get('expanded_url',u.get('url','')))
                try:fits=msg==r['text'] and item.get('attachments',{}).get('media_keys') and date(item['created_at'])>=date(r.get('sent_at',r['created']))-timedelta(seconds=10)
                except (ValueError,KeyError):fits=False
                if fits:found.append(str(item['id']))
            log('X conciliación '+r['event']['id']+': candidatos '+','.join(found)+'; comprobar manualmente; sin reenvío')

def instagram_caption(e):
    local=date(e['origin']).astimezone(ZoneInfo('America/Santiago'))
    depth='no informada' if e['depth'] is None else f"{e['depth']:g} km"
    caption=(f"Aceleración horizontal observada | Sismo M {e['mag_display']}\n\nReferencia: {' '.join(e['reference'].split())}\n"
       f"Fecha (Chile): {local:%d/%m/%Y %H:%M:%S}\nProfundidad: {depth}\n"
       f"Epicentro: {e['lat']:.4f}°, {e['lon']:.4f}°\nFuente: Centro Sismológico Nacional (CSN)\n"
       f"Informe: {e['url']}\n\nVisualización instrumental; no es una predicción.\n#SismosChile #Sismología #AceleraciónDelSuelo")
    if len(caption)>2200:raise Failure('Caption Instagram excede 2200 caracteres')
    return caption

class InstagramClient:
    def __init__(self,c,clock):
        self.c=c;self.clock=clock
        self.uid=(os.getenv('IG_USER_ID') or os.getenv('INSTAGRAM_USER_ID') or '').strip()
        self.token=(os.getenv('IG_ACCESS_TOKEN') or os.getenv('INSTAGRAM_TOKEN') or '').strip()
        self.expected_username=os.getenv('IG_EXPECTED_USERNAME','').strip().lstrip('@').lower()
        if not re.fullmatch(r'\d+',self.uid) or not self.token:raise PublishError('Faltan IG_USER_ID / IG_ACCESS_TOKEN')
        if not self.expected_username:raise PublishError('IG_EXPECTED_USERNAME obligatorio para proteger cuenta destino')
        host='graph.instagram.com' if c.ig_login_type=='instagram' else 'graph.facebook.com'
        self.base=f'https://{host}/{c.graph_version}'
        log(f'INSTAGRAM API: login={c.ig_login_type}, graph={c.graph_version}')
    def call(self,method,path,publish=False,**kwargs):
        self.clock.check(20)
        try:r=requests.request(method,self.base+path,headers={'Authorization':'Bearer '+self.token},
               timeout=max(2,min(self.c.timeout,self.clock.left()-5)),allow_redirects=False,**kwargs)
        except requests.RequestException as exc:raise PublishError('Instagram '+type(exc).__name__,uncertain=publish) from None
        try:body=r.json()
        except ValueError:body={}
        if not 200<=r.status_code<300 or (isinstance(body,dict) and body.get('error')):
            err=body.get('error',{}) if isinstance(body,dict) else {}
            if not isinstance(err,dict):err={}
            detail={k:err.get(k) for k in ('message','type','code','error_subcode','fbtrace_id')}
            raise PublishError(f'Meta HTTP {r.status_code} {path}: '+safe(json.dumps(detail,ensure_ascii=False)),r.status_code,
                  uncertain=publish and (r.status_code in (408,409,429) or r.status_code>=500))
        if not isinstance(body,dict):raise PublishError('Respuesta Meta incompatible',uncertain=publish)
        return body
    def identity(self):
        body=self.call('GET','/'+self.uid,params={'fields':'id,username'})
        if str(body.get('id'))!=self.uid or str(body.get('username','')).lower()!=self.expected_username:
            raise PublishError('Cuenta Instagram distinta de @'+self.expected_username+'; envío bloqueado')
        return self.uid
    def container(self,caption,video_url):
        p=urlparse(video_url)
        if p.scheme!='https' or not p.hostname:raise PublishError('URL Reel no HTTPS')
        b=self.call('POST','/'+self.uid+'/media',data={'media_type':'REELS','video_url':video_url,'caption':caption,'share_to_feed':'false'})
        cid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',cid):raise PublishError('Contenedor Reel sin ID')
        return cid
    def image_container(self,caption,image_url):
        p=urlparse(image_url)
        if p.scheme!='https' or not p.hostname:raise PublishError('URL imagen no HTTPS')
        b=self.call('POST','/'+self.uid+'/media',data={'image_url':image_url,'caption':caption})
        cid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',cid):raise PublishError('Contenedor foto sin ID')
        return cid
    def status(self,cid):return self.call('GET','/'+cid,params={'fields':'status_code,status'}).get('status_code')
    def wait(self,cid):
        end=min(time.monotonic()+240,self.clock.end-40)
        while time.monotonic()<end:
            b=self.call('GET','/'+cid,params={'fields':'status_code,status'})
            status=b.get('status_code')
            if status=='FINISHED':return
            if status in ('ERROR','EXPIRED','PUBLISHED'):raise PublishError('Contenedor IG '+str(status)+': '+safe(b.get('status','')))
            if status not in ('IN_PROGRESS','PROCESSING','PENDING'):raise PublishError('Estado IG inesperado '+str(status))
            time.sleep(5)
        raise PublishError('Timeout procesando contenedor IG')
    def publish(self,cid):
        b=self.call('POST','/'+self.uid+'/media_publish',publish=True,data={'creation_id':cid})
        mid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',mid):raise PublishError('media_publish sin ID',uncertain=True)
        return mid

def internet_music(c,clock):
    """Pista local o Wikimedia Commons, con validación y respaldo silencioso."""
    local=Path(c.music_file)
    if local.is_file() and local.stat().st_size>0:return local
    url=c.music_url.strip()
    if not url:return None
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in ('commons.wikimedia.org','upload.wikimedia.org'):
        raise Failure('REEL_MUSIC_URL debe apuntar a Wikimedia Commons por HTTPS')
    cache=Path(c.output)/'_music_cache'/'ambient_internet.ogg'
    if cache.is_file() and cache.stat().st_size>1000:return cache
    cache.parent.mkdir(parents=True,exist_ok=True);clock.check(45)
    temp=cache.with_suffix('.part')
    try:
        with requests.get(url,stream=True,timeout=(10,min(30,max(5,clock.left()-15))),
                          headers={'User-Agent':'CSN-observed-motion/3.1'}) as response:
            response.raise_for_status();total=0
            with temp.open('wb') as dest:
                for chunk in response.iter_content(65536):
                    clock.check(12);total+=len(chunk)
                    if total>12*1024*1024:raise Failure('Audio remoto excede 12 MiB')
                    dest.write(chunk)
        if total<1000 or temp.read_bytes()[:4]!=b'OggS':raise Failure('Audio remoto no es OGG válido')
        probe=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=codec_type',
             '-of','default=nw=1:nk=1',str(temp)],capture_output=True,text=True,timeout=12,check=True)
        if 'audio' not in probe.stdout:raise Failure('Archivo descargado no contiene audio')
        os.replace(temp,cache);log('Música descargada de Wikimedia Commons');return cache
    except (requests.RequestException,subprocess.SubprocessError,OSError,Failure) as exc:
        log('No se pudo descargar música: '+safe(exc));return None
    finally:temp.unlink(missing_ok=True)

def reel_video(video,clock,c=None):
    c=c or Config.env();src=Path(video);target=src.with_name('instagram_reel.mp4');clock.check(130)
    metadata=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of',
        'default=noprint_wrappers=1:nokey=1',str(src)],capture_output=True,text=True,check=True,timeout=15)
    duration=float(metadata.stdout.strip())
    if not math.isfinite(duration) or duration<3 or duration>140:raise Failure('Duración Reel fuera de rango')
    music=internet_music(c,clock);cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(src)]
    if music is not None and music.is_file():
        cmd+=['-stream_loop','-1','-i',str(music)]
        af=f'[1:a:0]volume={c.music_volume:g},aresample=48000,atrim=duration={duration:g},asetpts=PTS-STARTPTS[aout]'
        log('Audio Reel: '+music.name)
    else:
        cmd+=['-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000']
        af=f'[1:a:0]atrim=duration={duration:g},asetpts=PTS-STARTPTS[aout]'
        log('Audio Reel: pista silenciosa')
    cmd+=['-filter_complex',af,'-map','0:v:0','-map','[aout]',
       '-vf','scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p',
       '-c:v','libx264','-preset','veryfast','-crf','22','-c:a','aac','-b:a','128k','-ar','48000','-ac','2',
       '-t',str(duration),'-movflags','+faststart',str(target)]
    try:subprocess.run(cmd,check=True,capture_output=True,timeout=min(120,max(30,clock.left()-20)))
    except subprocess.CalledProcessError as exc:raise Failure('ffmpeg Reel: '+safe(exc.stderr.decode(errors='replace')[-350:])) from None
    if not target.is_file() or not target.stat().st_size:raise Failure('Reel vacío')
    return target

def check_public_media(url,kind,clock):
    """Añade inspección MIME, además de la verificación SHA-256 de public_github_media."""
    expected='video/mp4' if kind=='reel' else 'image/jpeg'
    clock.check(15)
    try:
        with requests.get(url,stream=True,timeout=min(20,max(2,clock.left()-5)),allow_redirects=True) as r:
            r.raise_for_status();mime=r.headers.get('Content-Type','').split(';')[0].strip().lower()
            if mime!=expected:raise Failure(f'MIME público IG incorrecto: {mime}; esperado {expected}')
            log(f'IG {kind}: MIME válido {mime}')
    except requests.RequestException as exc:raise Failure('URL multimedia IG inaccesible: '+type(exc).__name__) from None

def public_github_media(store,event_id,local_path,clock,kind):
    """Archivo público inmutable por SHA y commit. No alojar datos sensibles."""
    path=Path(local_path);raw=path.read_bytes()
    if not raw or len(raw)>90*1024*1024:raise Failure('Archivo público >90 MiB o vacío')
    digest=hashlib.sha256(raw).hexdigest();ext=path.suffix.lower()
    if (kind=='reel' and ext!='.mp4') or (kind=='feed' and ext!='.jpg'):raise Failure('Extensión multimedia no permitida')
    info=store.api('GET','')
    if info.status_code!=200 or info.json().get('private') is not False:raise Failure('Repositorio público obligatorio para IG')
    target=f'ig_media/{event_id}/{kind}-{digest[:24]}{ext}';endpoint='/contents/'+target
    existing=store.api('GET',endpoint,params={'ref':store.c.branch})
    if existing.status_code==200:
        meta=existing.json()
        if meta.get('size')!=len(raw):raise PersistenceError('Archivo GitHub existente con tamaño distinto')
        branch=store.api('GET','/git/ref/heads/'+quote(store.c.branch,safe=''))
        if branch.status_code!=200:raise PersistenceError('No se pudo resolver rama de medios')
        commit=branch.json()['object']['sha']
    elif existing.status_code==404:
        clock.check(55)
        payload={'message':f'IG public {kind} {event_id} [skip ci]','branch':store.c.branch,'content':base64.b64encode(raw).decode()}
        written=store.api('PUT',endpoint,json=payload)
        if written.status_code not in (200,201):raise PersistenceError(f'PUT multimedia HTTP {written.status_code}; revisar antes de reintentar')
        try:commit=written.json()['commit']['sha']
        except (KeyError,TypeError,ValueError):raise PersistenceError('PUT multimedia sin SHA confirmable') from None
    else:raise PersistenceError('Consulta multimedia HTTP '+str(existing.status_code))
    if not re.fullmatch(r'[0-9a-f]{40,64}',commit):raise PersistenceError('SHA multimedia inválido')
    url=f'https://raw.githubusercontent.com/{store.repo}/{commit}/{target}'
    for attempt in range(5):
        clock.check(10)
        try:
            with requests.get(url,stream=True,timeout=min(20,max(2,clock.left()-5))) as response:
                if response.status_code==200:
                    h=hashlib.sha256();size=0
                    for chunk in response.iter_content(1024*1024):
                        h.update(chunk);size+=len(chunk)
                        if size>len(raw):break
                    if size==len(raw) and h.hexdigest()==digest:
                        check_public_media(url,kind,clock)
                        return url
        except (requests.RequestException,ValueError):pass
        time.sleep(2+attempt)
    raise Failure('Archivo aún inaccesible públicamente; revisar GitHub/CDN')

def public_feed_photo(store,event_id,folder,clock):
    preview=folder/'preview.png'
    if not preview.is_file():raise Failure('Falta preview.png')
    photo=folder/'instagram_feed.jpg';clock.check(65)
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(preview),'-frames:v','1','-q:v','3',str(photo)],
         check=True,capture_output=True,timeout=min(30,max(5,clock.left()-25)))
    if not photo.read_bytes().startswith(b'\xff\xd8') or photo.stat().st_size>8_000_000:raise Failure('JPEG inválido')
    return public_github_media(store,event_id,photo,clock,'feed')

def distance(lat1,lon1,lat2,lon2):
    a,b=math.radians(lat1),math.radians(lat2)
    h=math.sin((b-a)/2)**2+math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 12742*math.asin(min(1,math.sqrt(h)))

def stations_for(e,c,clock):
    from obspy import read_inventory,UTCDateTime
    t=UTCDateTime(e['origin']);start=t-c.pre-c.margin;end=t+c.post+c.margin
    data=obtener(c.fdsn.rstrip('/')+'/fdsnws/station/1/query',c,clock,{
        'latitude':e['lat'],'longitude':e['lon'],'maxradius':c.radius/111.19,'channel':'*N?',
        'level':'channel','format':'xml','starttime':str(start),'endtime':str(end)})
    if not data:return [],[]
    inv=read_inventory(io.BytesIO(data));pairs={};rejected=[]
    for net in inv:
        for sta in net:
            for ch in sta:
                sid=f'{net.code}.{sta.code}.{ch.location_code}.{ch.code}'
                sens=ch.response.instrument_sensitivity if ch.response else None
                units=(sens.input_units if sens else '').upper().replace(' ','')
                try:ok=all(math.isfinite(float(v)) for v in (ch.latitude,ch.longitude,ch.dip,ch.azimuth,ch.sample_rate))
                except (TypeError,ValueError):ok=False
                if units not in ('M/S**2','M/S^2','M/S/S') or not ok or abs(ch.dip)>1 or ch.sample_rate<50:
                    rejected.append({'station':sid,'reason':'Metadatos/unidades/muestreo no válidos'});continue
                if (ch.start_date and ch.start_date>start) or (ch.end_date and ch.end_date<end):continue
                az=ch.azimuth%180;axis='N' if min(az,180-az)<=1 else ('E' if abs(az-90)<=1 else None)
                if axis is None:continue
                dist=distance(e['lat'],e['lon'],ch.latitude,ch.longitude)
                if dist>c.radius:continue
                item=dict(id=sid,net=net.code,sta=sta.code,loc=ch.location_code,cha=ch.code,lat=ch.latitude,lon=ch.longitude,distance=dist)
                pairs.setdefault((net.code,sta.code,ch.location_code,ch.code[:-1]),{}).setdefault(axis,item)
    found=[]
    for key,pair in pairs.items():
        if set(pair)!={'N','E'}:continue
        n,east=pair['N'],pair['E']
        if distance(n['lat'],n['lon'],east['lat'],east['lon'])>.1:continue
        found.append(dict(n,id='.'.join(key)+'[N,E]',components=[n,east]))
    return sorted(found,key=lambda s:(s['distance'],s['id']))[:c.candidates],rejected

def check_raw(st,start,end):
    import numpy as np
    if not st or st.get_gaps():raise Failure('Señal ausente/con huecos')
    st.merge(method=0,fill_value=None)
    if len(st)!=1:raise Failure('Trazas incompatibles')
    tr=st[0]
    if tr.stats.starttime>start+tr.stats.delta or tr.stats.endtime<end-tr.stats.delta:raise Failure('Cobertura incompleta')
    a=tr.data
    if np.ma.is_masked(a) or len(a)<100 or not np.isfinite(a).all() or np.ptp(a.astype(float))==0:raise Failure('Muestras sin calidad suficiente')
    extreme=(a==np.max(a))|(a==np.min(a))
    if np.any(extreme[:-2]&extreme[1:-1]&extreme[2:]):raise Failure('Saturación probable')
    if np.issubdtype(a.dtype,np.integer):
        limits=np.iinfo(a.dtype)
        if np.any((a==limits.min)|(a==limits.max)):raise Failure('Saturación digital')
    return tr

def process_component(s,e,c,clock):
    import numpy as np
    from obspy import read,read_inventory,UTCDateTime
    t=UTCDateTime(e['origin']);start=t-c.pre-c.margin;end=t+c.post+c.margin
    params=dict(net=s['net'],sta=s['sta'],loc=s['loc'] or '--',cha=s['cha'],starttime=str(start),endtime=str(end))
    base=c.fdsn.rstrip('/')+'/fdsnws/'
    meta=obtener(base+'station/1/query',c,clock,dict(params,level='response',format='xml'))
    if not meta:raise Failure('Sin metadatos instrumentales')
    inv=read_inventory(io.BytesIO(meta));response=inv.get_response(s['id'],t);sens=response.instrument_sensitivity
    units=sens.input_units.upper().replace(' ','') if sens else ''
    if units not in ('M/S**2','M/S^2','M/S/S') or not sens or sens.value<=0 or not response.response_stages:raise Failure('Sin calibración acelerómetro')
    raw=obtener(base+'dataselect/1/query',c,clock,params)
    if not raw:raise Failure('Sin miniSEED')
    tr=check_raw(read(io.BytesIO(raw),format='MSEED').select(id=s['id']),start,end)
    if tr.stats.sampling_rate<50:raise Failure('Muestreo insuficiente')
    tr.data=tr.data.astype(np.float64);tr.detrend('linear')
    tr.remove_response(inventory=inv,output='ACC',pre_filt=(.1,.2,20,24),water_level=None,zero_mean=True,taper=True,taper_fraction=.05)
    tr.filter('bandpass',freqmin=.2,freqmax=20,corners=4,zerophase=True)
    tr.trim(t-c.pre,t+c.post,nearest_sample=False)
    if not np.isfinite(tr.data).all():raise Failure('Aceleración no finita')
    return dict(s,times=tr.times()+float(tr.stats.starttime-t),values=tr.data)

def process_station(s,e,c,clock):return dict(s,components=[process_component(p,e,c,clock) for p in s['components']])
def collect_stations(candidates,e,c,clock):
    valid=[];rejected=[]
    with cf.ThreadPoolExecutor(max_workers=c.workers) as pool:
        tasks={pool.submit(process_station,s,e,c,clock):s for s in candidates}
        for future in cf.as_completed(tasks):
            try:valid.append(future.result())
            except Exception as exc:rejected.append(dict(station=tasks[future]['id'],reason=safe(exc)))
    unique=[];seen=set()
    for s in sorted(valid,key=lambda x:(x['distance'],x['id'])):
        key=(s['net'],s['sta'])
        if key not in seen:unique.append(s);seen.add(key)
    return unique[:c.stations],rejected

def collect(candidates,processor,workers):
    valid=[];rejected=[]
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        tasks={pool.submit(processor,station):station for station in candidates}
        for f in cf.as_completed(tasks):
            try:valid.append(f.result())
            except Exception as exc:rejected.append({'station':tasks[f]['id'],'reason':safe(exc)})
    unique=[];seen=set()
    for s in sorted(valid,key=lambda st:(st['distance'],st['id'])):
        key=(s.get('net',''),s.get('sta',s['id']))
        if key in seen:rejected.append({'station':s['id'],'reason':'Estación duplicada'})
        else:unique.append(s);seen.add(key)
    return unique,rejected

def station_peaks(s,centers,step):
    import numpy as np
    if len(s['components'])!=2:raise Failure('Faltan ejes horizontales N/E')
    amps=np.empty(len(centers))
    for j,t in enumerate(centers):
        peaks=[]
        for component in s['components']:
            values=component['values'][(component['times']>=t-step/2)&(component['times']<t+step/2)]
            if not len(values) or not np.isfinite(values).all():raise Failure('Intervalo horizontal incompleto')
            peaks.append(float(np.max(np.abs(values))))
        amps[j]=max(peaks)*100/9.80665
    return amps

def spatial_weights(lons,lats,gx,gy,e,c):
    import numpy as np
    from scipy.spatial import Delaunay,QhullError
    def xy(lon,lat):return np.column_stack(((np.ravel(lon)-e['lon'])*111.19*math.cos(math.radians(e['lat'])),(np.ravel(lat)-e['lat'])*111.19))
    pts=xy(lons,lats);grid=xy(gx,gy);w=np.zeros((len(grid),len(pts)));mask=np.zeros(len(grid),bool)
    if len(pts)<3:return w,mask,'Menos de tres estaciones'
    try:
        tri=Delaunay(pts);simp=tri.find_simplex(grid);vertices=tri.simplices[np.maximum(simp,0)]
        edge=np.max([np.linalg.norm(pts[vertices[:,i]]-pts[vertices[:,j]],axis=1) for i,j in ((0,1),(1,2),(2,0))],axis=0)
        d=np.linalg.norm(grid[:,None,:]-pts[None,:,:],axis=2);near=d<=c.support
        mask=(simp>=0)&(near.sum(axis=1)>=3)&(edge<=c.triangle)
        w=np.where(near,1/np.maximum(d,.001)**2,0);w/=np.maximum(w.sum(axis=1,keepdims=True),1e-30);w[~mask]=0
    except QhullError:return w,mask,'Geometría degenerada'
    return w,mask,'IDW local, soporte y triángulos limitados' if mask.any() else 'Sin soporte de interpolación'

def prepare_map(c,clock):
    import cartopy
    root=Path(cartopy.config['data_dir'])/'shapefiles'/'natural_earth'
    for category,name in (('physical','land'),('physical','ocean'),('physical','coastline'),('cultural','admin_0_boundary_lines_land')):
        stem='ne_110m_'+name;dest=root/category
        if all((dest/(stem+ext)).is_file() for ext in ('.shp','.shx','.dbf')):continue
        data=obtener(f'https://naturalearth.s3.amazonaws.com/110m_{category}/{stem}.zip',c,clock)
        dest.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for ext in ('.shp','.shx','.dbf','.prj','.cpg'):
                if stem+ext in archive.namelist():(dest/(stem+ext)).write_bytes(archive.read(stem+ext))
        if not all((dest/(stem+ext)).is_file() for ext in ('.shp','.shx','.dbf')):raise Failure('Cartografía incompleta')

def render_video(e,stations,c,clock,folder):
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    from matplotlib.colors import LogNorm
    from matplotlib.cm import ScalarMappable
    import cartopy.crs as ccrs
    import cartopy.feature as feat
    prepare_map(c,clock)
    n=round((c.pre+c.post)/c.step);times=-c.pre+(np.arange(n)+.5)*c.step
    amps=np.stack([station_peaks(s,times,c.step) for s in stations]);positive=amps[amps>0]
    if not len(positive) or not np.isfinite(amps).all():raise Failure('Aceleración inutilizable')
    vmin=float(10**np.floor(np.log10(positive.min())));vmax=float(10**np.ceil(np.log10(positive.max())))
    if vmax<=vmin:vmax=vmin*10
    lons=np.array([s['lon'] for s in stations]);lats=np.array([s['lat'] for s in stations])
    extent=[min(lons.min(),e['lon'])-.5,max(lons.max(),e['lon'])+.5,min(lats.min(),e['lat'])-.5,max(lats.max(),e['lat'])+.5]
    gx,gy=np.meshgrid(np.linspace(*extent[:2],180),np.linspace(*extent[2:],180))
    w,mask,reason=spatial_weights(lons,lats,gx,gy,e,c);area=bool(mask.any())
    def field(j):
        a=np.full(gx.size,np.nan)
        if area:a[mask]=w[mask]@amps[:,j]
        return a.reshape(gx.shape)
    fig=plt.figure(figsize=(12.8,7.2),dpi=100)
    gs=fig.add_gridspec(1,2,left=.06,right=.97,bottom=.25,top=.80,width_ratios=[1,1.15],wspace=.30)
    ax=fig.add_subplot(gs[0,0],projection=ccrs.PlateCarree());ax.set_extent(extent)
    ax.set_title('MAPA DE ACELERACIÓN HORIZONTAL',fontsize=10,pad=15)
    ax.add_feature(feat.LAND.with_scale('110m'),facecolor='#eee8da',zorder=0)
    ax.add_feature(feat.OCEAN.with_scale('110m'),facecolor='#eef5fa',zorder=0)
    ax.coastlines('110m',linewidth=.6,zorder=3);ax.add_feature(feat.BORDERS.with_scale('110m'),linewidth=.4,zorder=3)
    gl=ax.gridlines(draw_labels=True,linewidth=.3,alpha=.5);gl.top_labels=gl.right_labels=False
    gl.xlabel_style=gl.ylabel_style={'size':8}
    cmap=plt.get_cmap('jet');normal=LogNorm(vmin,vmax,clip=True)
    mesh=ax.pcolormesh(gx,gy,np.ma.masked_invalid(field(0)),cmap=cmap,norm=normal,alpha=.85,shading='auto',edgecolors='none',zorder=2)
    colors=[plt.get_cmap('tab20')(i%20) for i in range(len(stations))]
    if area:dots=ax.scatter(lons,lats,facecolors='white',edgecolors=colors,marker='^',linewidths=1.6,s=55,zorder=5)
    else:dots=ax.scatter(lons,lats,c=amps[:,0],cmap=cmap,norm=normal,edgecolors='black',s=55,zorder=5)
    ax.scatter([e['lon']],[e['lat']],marker='*',c='magenta',edgecolors='black',s=180,zorder=6)
    ax.text(e['lon']+.03,e['lat']-.05,'Epicentro',fontsize=8,color='purple',zorder=7)
    for s in stations:ax.text(s['lon']+.025,s['lat']+.025,s['sta'],fontsize=7,zorder=7)
    cb=fig.colorbar(ScalarMappable(norm=normal,cmap=cmap),ax=ax,orientation='horizontal',pad=.07,fraction=.05)
    cb.set_label(f'Pico horizontal por {c.step:g} s [% g] · escala logarítmica',fontsize=8)
    p=fig.add_subplot(gs[0,1])
    for i,s in enumerate(stations):
        p.plot(times,np.ma.masked_less_equal(amps[i],0),color=colors[i],linewidth=1,alpha=.85,label=f"{s['net']}.{s['sta']} · {s['distance']:.0f} km")
    p.set_yscale('log');p.axvline(0,color='#ef6351',linestyle='--',linewidth=1)
    cursor=p.axvline(times[0],color='#263449',linewidth=1.5)
    cursor_label=p.text(times[0],.96,'',transform=p.get_xaxis_transform(),ha='center',va='top',fontsize=8,
       color='white',bbox={'facecolor':'#263449','edgecolor':'none','pad':2})
    p.set(xlim=(-c.pre,c.post),ylim=(vmin,vmax),xlabel='Tiempo respecto al origen [s]',ylabel='Pico horizontal [% g]')
    p.set_title('ACELERACIÓN DE LAS ESTACIONES',fontsize=10,pad=28);p.grid(alpha=.25)
    p.legend(loc='upper center',bbox_to_anchor=(.5,-.13),fontsize=6,frameon=False,ncol=3)
    depth='no informada' if e['depth'] is None else f"{e['depth']:g} km"
    local=date(e['origin']).astimezone(ZoneInfo('America/Santiago'))
    fig.suptitle(f"Sismo M {e['mag_display']} | {e['reference'][:85]}\n{local:%d/%m/%Y %H:%M:%S} · Profundidad: {depth}",fontsize=12,y=.96)
    label=fig.text(.06,.855,'',fontsize=10);fig.text(.04,.022,'Fuente: Centro Sismológico Nacional de la Universidad de Chile',fontsize=8)
    video=folder/'video.mp4'
    writer=FFMpegWriter(fps=c.fps,codec='libx264',bitrate=5000,extra_args=['-vf','fps=30,format=yuv420p','-profile:v','high','-g','60','-movflags','+faststart'])
    peak=int(np.argmax(amps.max(axis=0)))
    try:
        with writer.saving(fig,str(video),dpi=100):
            for j,t in enumerate(times):
                clock.check(10)
                if not area:dots.set_array(amps[:,j])
                mesh.set_array(np.ma.masked_invalid(field(j)).ravel())
                cursor.set_xdata([t,t]);cursor_label.set_position((t,.96));cursor_label.set_text(f'{t:+.1f} s')
                label.set_text(f't = {t:+.1f} s · reproducción ×{c.step*c.fps:g}')
                if j==peak:fig.savefig(folder/'preview.png',dpi=100)
                writer.grab_frame()
                if j%30==0:log(f'Render {j+1}/{n}')
    finally:plt.close(fig)
    p=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],check=True,capture_output=True,text=True,timeout=30)
    b=json.loads(p.stdout);stream=next(s for s in b['streams'] if s['codec_type']=='video');duration=float(b['format']['duration'])
    if stream['codec_name']!='h264' or stream['pix_fmt']!='yuv420p' or (stream['width'],stream['height'])!=(1280,720) or abs(duration-n/c.fps)>.15 or not 3<=duration<=140 or video.stat().st_size>512*1024*1024:
        raise Failure('ffprobe rechazó MP4')
    return dict(duration_seconds=duration,physical_seconds=c.pre+c.post,frame_step_seconds=c.step,interpolation=area,
        interpolation_reason=reason,stations_used=len(stations),grid_cells_with_support=int(mask.sum()),
        metric='horizontal_component_peak_percent_g',band_hz=[.2,20])

def render_worker(event_path,c):
    e=validate_event(json.loads(Path(event_path).read_text(encoding='utf8')))
    folder=Path(event_path).parent;clock=Clock(c.event_budget)
    quality={'event':e,'metric':'horizontal peak % g, 0.2-20 Hz','clipping_test':'heuristic; no prueba ausencia de saturación'}
    try:
        candidates,excluded=stations_for(e,c,clock);valid,rejected=collect_stations(candidates,e,c,clock)
        quality.update(attempted=len(candidates),valid=len(valid),rejected=excluded+rejected)
        if not valid:raise Failure('Sin estaciones válidas')
        quality['used']=[{k:v for k,v in s.items() if k!='components'} for s in valid]
        quality['video']=render_video(e,valid,c,clock,folder)
        atomic(folder/'quality.json',quality);return 0
    except Exception as exc:
        quality['error']=safe(exc);atomic(folder/'quality.json',quality);log('Render falló: '+safe(exc));return 1

def build_video(e,c,clock):
    clock.check(180);folder=Path(c.output)/e['id'];folder.mkdir(parents=True,exist_ok=True)
    video=folder/'video.mp4'
    # Reutilizar en la misma ejecución, pero solo cuando existen vídeo y preview.
    if video.is_file() and video.stat().st_size>0 and (folder/'preview.png').is_file():
        log('VIDEO: reutilizando render existente '+e['id']);return video
    atomic(folder/'event.json',e)
    for name in ('video.mp4','preview.png','quality.json','instagram_reel.mp4'):(folder/name).unlink(missing_ok=True)
    env=dict(os.environ)
    for key in SECRETS:env.pop(key,None)
    limit=min(c.event_budget,clock.left()-120);env['EVENT_TIMEOUT_SEC']=str(limit)
    try:p=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--render',str((folder/'event.json').resolve())],env=env,timeout=limit,check=False)
    except subprocess.TimeoutExpired:raise Failure('Timeout procesando evento') from None
    if p.returncode or not video.is_file():raise Failure('No se generó vídeo; revisar quality.json')
    return video

def send_transaction(store,s,r,x,text,mid):
    r.update(status='enviando',text=text,media_id=mid,sent_at=iso(utcnow()),updated=iso(utcnow()));store.save(s)
    try:tid=x.create(text,mid)
    except Exception as exc:
        if isinstance(exc,XError) and not exc.uncertain:retry(r,store.c,exc)
        else:r.update(status='resultado_incierto',error=safe(exc),updated=iso(utcnow()))
        store.save(s);raise
    r.update(status='publicado',tweet_id=tid,error='',updated=iso(utcnow()))
    log('X confirmado: https://x.com/i/web/status/'+tid);store.save(s);return tid

def publish_x_record(store,s,r,x,c,clock):
    text=tweet_text(r['event']);video=build_video(r['event'],c,clock);mid=x.upload(video)
    return send_transaction(store,s,r,x,text,mid)

def instagram_diagnostics(s,c):
    result=[];now=utcnow()
    for eid,r in sorted(s['events'].items()):
        age=(now-date(r['event']['origin'])).total_seconds()/3600
        ready=date(r['event']['origin'])+timedelta(seconds=c.post+c.margin+c.latency)
        for field in ('instagram','instagram_feed'):
            item=r.get(field,{})
            if item.get('status')=='publicado':reason='ya_publicado'
            elif item.get('status')=='resultado_incierto':reason='resultado_incierto_no_reenviar'
            elif age>c.ig_age:reason='evento_fuera_de_ventana_ig'
            elif now<ready:reason='esperando_datos'
            elif item.get('attempts',0)>=c.max_attempts:reason='intentos_agotados'
            else:reason='elegible'
            result.append(dict(event_id=eid,type=field,status=item.get('status'),attempts=item.get('attempts'),
                container_id=item.get('container_id'),media_id=item.get('media_id'),reason=reason,error=item.get('error','')))
    return result

def process_instagram(store,s,c,clock,report):
    if not c.publish_ig:
        log('INSTAGRAM: desactivado (PUBLISH_TO_INSTAGRAM != true)');return
    section=report.setdefault('instagram',dict(reels_published=0,feed_published=0,uncertain=0,errors=[],skipped=[]))
    log(f'INSTAGRAM: iniciando; eventos_estado={len(s["events"])}; tiempo={clock.left():.0f}s')
    ig=InstagramClient(c,clock);uid=ig.identity()
    log('INSTAGRAM: identidad confirmada @'+ig.expected_username)
    if s.get('ig_account_id') not in (None,uid):raise PersistenceError('Instagram asociado a otra cuenta')
    if s.get('ig_account_id') is None:s['ig_account_id']=uid;store.save(s)
    processed=0
    for r in sorted(s['events'].values(),key=lambda v:v['event']['origin']):
        eid=r['event']['id'];age=(utcnow()-date(r['event']['origin'])).total_seconds()/3600
        if age>c.ig_age:
            log(f'IG OMITIDO {eid}: antigüedad {age:.1f} h > {c.ig_age} h');section['skipped'].append(f'{eid}: fuera de ventana');continue
        ready=date(r['event']['origin'])+timedelta(seconds=c.post+c.margin+c.latency)
        if utcnow()<ready:
            log(f'IG OMITIDO {eid}: esperando datos hasta {iso(ready)}');section['skipped'].append(f'{eid}: esperando datos');continue
        caption=instagram_caption(r['event'])
        for field in ('instagram','instagram_feed'):
            item=r.setdefault(field,ig_default())
            if item['status']=='resultado_incierto':
                section['uncertain']+=1;log(f'IG BLOQUEADO {eid} {field}: resultado incierto, NO reenviar');continue
            if item['status']=='publicado':
                log(f'IG OMITIDO {eid} {field}: ya publicado');continue
            if item['attempts']>=c.max_attempts:
                log(f'IG OMITIDO {eid} {field}: intentos agotados');continue
            if processed>=c.max_events*2:
                log('IG: máximo de trabajos alcanzado');return
            if clock.left()<180:
                detail=f'{eid} {field}: tiempo insuficiente ({clock.left():.0f}s)'
                section['errors'].append(detail);log('IG PENDIENTE: '+detail);return
            processed+=1
            item.update(status='procesando',attempts=item['attempts']+1,updated=iso(utcnow()));store.save(s)
            log(f'IG INTENTO {eid} {field}: intento {item["attempts"]}/{c.max_attempts}')
            try:
                folder=Path(c.output)/eid
                cid=item.get('container_id')
                if cid:
                    # Solo reciclar contenedor fallido si está probado que jamás arrancó media_publish.
                    status=ig.status(cid)
                    log(f'IG CONTENEDOR {eid} {field}: {cid} estado={status}')
                    if status in ('ERROR','EXPIRED'):
                        if item.get('post_started'):
                            item.update(status='resultado_incierto',error='Contenedor fallido tras posible media_publish; revisión manual')
                            store.save(s);section['uncertain']+=1;continue
                        item['container_id']=None;cid=None;store.save(s)
                    elif status=='PUBLISHED':
                        item.update(status='resultado_incierto',error='Contenedor ya publicado, falta media_id: verificar manualmente')
                        store.save(s);section['uncertain']+=1;continue
                if not cid:
                    if field=='instagram':
                        original=build_video(r['event'],c,clock)
                        reel=reel_video(original,clock,c)
                        url=public_github_media(store,eid,reel,clock,'reel')
                        log(f'IG REEL {eid}: URL pública validada')
                        cid=ig.container(caption,url)
                    else:
                        if not (folder/'preview.png').is_file():build_video(r['event'],c,clock)
                        url=public_feed_photo(store,eid,folder,clock)
                        log(f'IG FEED {eid}: URL pública validada')
                        cid=ig.image_container(caption,url)
                    item.update(container_id=cid,caption=caption,updated=iso(utcnow()));store.save(s)
                    log(f'IG CONTENEDOR CREADO {eid} {field}: {cid}')
                ig.wait(cid)
                # Es fundamental persistir antes de media_publish; cualquier interrupción posterior es incierta.
                item.update(status='publicando',post_started=True,updated=iso(utcnow()));store.save(s)
                log(f'IG PUBLICANDO {eid} {field}: contenedor={cid}')
                try:mid=ig.publish(cid)
                except Exception as exc:
                    item.update(status='resultado_incierto',error=safe(exc),updated=iso(utcnow()))
                    store.save(s);raise
                item.update(status='publicado',media_id=mid,error='',updated=iso(utcnow()));store.save(s)
                section['reels_published' if field=='instagram' else 'feed_published']+=1
                log(f'IG PUBLICADO {eid} {field}: media_id={mid}')
            except PersistenceError:raise
            except Exception as exc:
                if item['status']=='procesando':
                    item.update(status='pendiente',error=safe(exc),updated=iso(utcnow()));store.save(s)
                if item['status']=='resultado_incierto':section['uncertain']+=1
                section['errors'].append(f'{eid} {field}: {type(exc).__name__}: {safe(exc)}')
                log(f'ERROR Instagram {field} {eid}: {safe(exc)}')
                if isinstance(exc,PublishError) and exc.global_stop:return

def config_diagnostics(c):
    def present(*keys):return 'OK' if any(os.getenv(k) for k in keys) else 'FALTA'
    return dict(x_enabled=c.publish_x,instagram_enabled=c.publish_ig,ig_login_type=c.ig_login_type,
         ig_user_id=present('IG_USER_ID','INSTAGRAM_USER_ID'),
         ig_token=present('IG_ACCESS_TOKEN','INSTAGRAM_TOKEN'),
         ig_expected_username=present('IG_EXPECTED_USERNAME'),
         github_token=present('GITHUB_TOKEN'),github_repository=present('GITHUB_REPOSITORY'))

def run(c,init=False):
    end=utcnow();start=end-timedelta(hours=c.lookback);clock=Clock(c.budget)
    report=dict(start_utc=iso(start),end_utc=iso(end),mode='live' if c.publish_x or c.publish_ig else 'simulation',
       config=config_diagnostics(c),found=0,new=0,pending=0,results=[],errors=[],warnings=[])
    code=0;s=None;Path(c.output).mkdir(parents=True,exist_ok=True)
    try:
        log(f'Ventana {iso(start)} - {iso(end)}; M >= {c.mag}')
        log(f'CONFIG CANALES: X={"ON" if c.publish_x else "OFF"} | INSTAGRAM={"ON" if c.publish_ig else "OFF"}')
        log('CONFIG INSTAGRAM: IG_USER_ID='+report['config']['ig_user_id']+' | IG_TOKEN='+report['config']['ig_token']+
            ' | IG_USERNAME='+report['config']['ig_expected_username']+' | LOGIN='+c.ig_login_type)
        store=Store(c);s=store.load(init);recover(s,start);store.save(s)
        x=None
        if c.publish_x:
            try:
                x=XClient(c,clock);uid=x.identity()
                if s['account_id'] not in (None,uid):raise PersistenceError('Cuenta X distinta')
                if s['account_id'] is None:s['account_id']=uid;store.save(s)
                if c.reconcile:x.reconcile(s)
            except PersistenceError:raise
            except Exception as exc:
                code=1;report['errors'].append('Inicialización X: '+safe(exc));x=None
        events,source=discover(c,clock,start,end)
        report['source']=source;report['found']=len(events)
        if source['status']!='valida':code=1;report['warnings'].append('Catálogo CSN parcial')
        for e in events:
            if e['id'] not in s['events']:s['events'][e['id']]=record(e,end);report['new']+=1
        store.save(s)
        # Instagram primero: el tiempo consumido por X ya no impide arrancar Meta.
        if c.publish_ig:
            log(f'ANTES INSTAGRAM: activado=True | eventos_estado={len(s["events"])} | tiempo_restante={clock.left():.0f}s')
            try:process_instagram(store,s,c,clock,report)
            except PersistenceError:raise
            except Exception as exc:code=1;report['errors'].append('Instagram: '+type(exc).__name__+': '+safe(exc));log('ERROR Instagram general: '+safe(exc))
            if report.get('instagram',{}).get('errors'):code=1
        else:log('INSTAGRAM NO SE EJECUTA: PUBLISH_TO_INSTAGRAM=false')
        processed=0
        if (c.publish_x and x is not None) or (not c.publish_x and not c.publish_ig):
            for r in sorted(s['events'].values(),key=lambda a:a['event']['origin']):
                if r['status']=='resultado_incierto':report['warnings'].append(r['event']['id']+' X incierto: NO reenviar');continue
                if not eligible(r,c,start,end):continue
                if defer_recent(r,c,utcnow()):store.save(s);continue
                if processed>=c.max_events or clock.left()<180:break
                processed+=1;r.update(status='procesando',attempts=r['attempts']+1,updated=iso(utcnow()));store.save(s)
                try:
                    if x:publish_x_record(store,s,r,x,c,clock)
                    else:
                        tweet_text(r['event']);build_video(r['event'],c,clock)
                        r.update(status='simulado',updated=iso(utcnow()));store.save(s)
                    report['results'].append(dict(id=r['event']['id'],status=r['status'],tweet_id=r['tweet_id']))
                except PersistenceError:raise
                except Exception as exc:
                    code=1;report['errors'].append(r['event']['id']+': '+safe(exc))
                    if r['status']=='procesando':retry(r,c,exc);store.save(s)
                    if r['status'] in ('enviando','resultado_incierto','publicado'):break
                    if isinstance(exc,PublishError) and exc.global_stop:break
    except Exception as exc:
        code=1;report['errors'].append(type(exc).__name__+': '+safe(exc));log('ERROR '+safe(exc))
    finally:
        if s is not None:
            report['pending']=sum(r['status']=='pendiente' for r in s['events'].values())
            report['uncertain_x']=sum(r['status']=='resultado_incierto' for r in s['events'].values())
            report['uncertain_instagram']=sum(r[k]['status']=='resultado_incierto' for r in s['events'].values() for k in ('instagram','instagram_feed'))
            report['instagram_diagnostics']=instagram_diagnostics(s,c)
        report['exit_code']=code;atomic(Path(c.output)/'summary.json',report)
        summary='## CSN → aceleración → X e Instagram\n\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n'
        (Path(c.output)/'summary.md').write_text(summary,encoding='utf8')
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf8') as f:f.write(summary)
        log(f"Ciclo terminado exit={code}; nuevos={report['new']}; pendientes={report['pending']}")
    return code

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--init-state',action='store_true');parser.add_argument('--render')
    parser.add_argument('--check-config',action='store_true',help='Mostrar diagnóstico de configuración sin iniciar descargas/publicaciones')
    args=parser.parse_args()
    try:
        c=Config.env()
        if args.check_config:
            print(json.dumps(config_diagnostics(c),ensure_ascii=False,indent=2));return 0
        return render_worker(args.render,c) if args.render else run(c,args.init_state)
    except Exception as exc:log('CONFIGURACION '+type(exc).__name__+': '+safe(exc));return 1

if __name__=='__main__':sys.exit(main())
