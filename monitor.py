#!/usr/bin/env python3
"""Monitor CSN -> aceleración horizontal observada -> MP4 -> X + Instagram Reel + publicación independiente en feed.
Python 3.11+. Un solo archivo; --init-state crea el estado de GitHub.
Dependencias: requests requests-oauthlib beautifulsoup4 numpy scipy obspy
matplotlib cartopy; Node + twitter-text@3.1.0; ffmpeg y ffprobe.
Instagram Login requiere token de cuenta profesional y repositorio público para videos/fotos HTTPS.
Configurar INSTAGRAM_TOKEN, INSTAGRAM_USER_ID, IG_EXPECTED_USERNAME, PUBLISH_TO_INSTAGRAM.
Los archivos de ig_media quedan públicos en STATE_BRANCH. NO usar repositorio privado.
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
from pathlib import Path
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
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests_oauthlib import OAuth1

UTC = timezone.utc
CSN = 'https://www.sismologia.cl'
SECRET_NAMES = SECRETS = ('X_API_KEY','X_API_SECRET','X_ACCESS_TOKEN','X_ACCESS_TOKEN_SECRET',
           'GITHUB_TOKEN','IG_ACCESS_TOKEN','INSTAGRAM_TOKEN')
STATES = {'pendiente','procesando','enviando','publicado','simulado',
          'resultado_incierto','expirado'}
IG_STATES = {'pendiente','procesando','publicando','publicado','resultado_incierto'}


def utcnow(): return datetime.now(UTC)
def iso(d): return d.astimezone(UTC).isoformat().replace('+00:00','Z')
def date(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('Fecha sin zona horaria')
    return d.astimezone(UTC)
def safe(x):
    s=str(x)
    for name in SECRETS:
        v=os.getenv(name,'')
        if v: s=s.replace(v,'[REDACTADO]')
    return s.replace('\n',' ').replace('\r',' ')[:400]
def log(s): print(iso(utcnow()),safe(s),flush=True)
def flag(name,default=False):
    s=os.getenv(name,str(default)).lower().strip()
    if s not in ('true','false','1','0','yes','no'): raise ValueError('Booleano inválido: '+name)
    return s in ('true','1','yes')
def atomic(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf8',dir=p.parent,delete=False) as f:
        tmp=f.name;json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)

@dataclass
class Config:
    mag:float=4.0
    lookback:float=24.0
    pre:float=60.0
    post:float=120.0
    margin:float=30.0
    latency:float=180.0
    fps:int=4
    step:float=1.0
    workers:int=4
    timeout:float=30.0
    max_events:int=2
    budget:float=2100.0
    event_budget:float=800.0
    x_wait:float=900.0
    radius:float=250.0
    stations:int=15
    candidates:int=30
    support:float=100.0
    triangle:float=150.0
    max_attempts:int=12
    backoff:float=600.0
    publish_x:bool=False
    @property
    def publish(self):
        return self.publish_x
    @publish.setter
    def publish(self, value):
        self.publish_x = bool(value)

    publish_ig:bool=False
    reconcile:bool=False
    state_dir:str='.state'
    output:str='output'
    branch:str='monitor-state'
    fdsn:str='https://eew.csn.uchile.cl'
    graph_version:str='v25.0'
    @classmethod
    def env(cls):
        c=cls()
        mapping=dict(mag='MAG_MIN',lookback='LOOKBACK_HOURS',pre='PRE_SEG',post='POST_SEG',
          margin='MARGEN_SEG',latency='DATA_LATENCY_SEC',fps='FPS',step='FRAME_STEP_SEC',
          workers='DOWNLOAD_WORKERS',timeout='HTTP_TIMEOUT',max_events='MAX_EVENTS_PER_RUN',
          budget='MAX_RUNTIME_SEC',event_budget='EVENT_TIMEOUT_SEC',x_wait='X_PROCESS_TIMEOUT_SEC',
          radius='MAX_DISTANCE_KM',stations='MAX_STATIONS',candidates='MAX_CANDIDATES',
          support='SUPPORT_KM',triangle='MAX_TRIANGLE_KM',max_attempts='MAX_ATTEMPTS',
          backoff='RETRY_BASE_SEC',state_dir='STATE_DIR',output='OUTPUT_DIR',
          branch='STATE_BRANCH',fdsn='FDSN_BASE',graph_version='IG_GRAPH_VERSION')
        for field,envname in mapping.items():
            annotation=cls.__annotations__[field]
            typ={'float':float,'int':int,'str':str}.get(annotation,str)
            setattr(c,field,typ(os.getenv(envname,str(getattr(c,field)))))
        c.publish_x=flag('PUBLISH_TO_X');c.publish_ig=flag('PUBLISH_TO_INSTAGRAM');c.reconcile=flag('RECONCILE_X')
        for k in mapping:
            v=getattr(c,k)
            if isinstance(v,(int,float)) and (not math.isfinite(v) or v<=0):
                raise ValueError('Valor positivo requerido: '+k)
        if c.margin<30 or c.budget<180 or c.x_wait>900: raise ValueError('Presupuestos/margen inválidos')
        if c.stations>30 or c.candidates<c.stations or c.workers>8: raise ValueError('Límites estaciones/workers')
        n=(c.pre+c.post)/c.step
        if abs(n-round(n))>1e-6 or not .5<=n/c.fps<=140: raise ValueError('Duración de video inválida')
        if not re.fullmatch(r'v\d+\.\d+',c.graph_version): raise ValueError('Versión Graph inválida')
        return c

class Failure(RuntimeError): pass
class PersistenceError(Failure): pass
class BudgetError(Failure): pass
class SourceError(Failure): pass
class StructureError(SourceError): pass
class PublishError(Failure):
    def __init__(self,msg,status=0,uncertain=False,reset=None):
        super().__init__(msg);self.status=status;self.uncertain=uncertain;self.reset=reset
        self.global_stop=status in (401,402,403,429) or status>=500 or uncertain

class XError(PublishError):
    """Error específico de X; interfaz pública compatible con test_monitor.py."""


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
                              headers={'User-Agent':'CSN-observed-motion/2.0'}) as r:
                if r.status_code==204:return b''
                if r.status_code in (429,500,502,503,504) and attempt<2:
                    raw=r.headers.get('Retry-After','2')
                    try: delay=float(raw)
                    except ValueError:
                        try:delay=(parsedate_to_datetime(raw)-utcnow()).total_seconds()
                        except Exception:delay=2
                    delay=max(2**attempt,delay)
                    if delay>30 or delay+5>=clock.left():raise SourceError('HTTP diferido '+str(r.status_code))
                    time.sleep(delay);continue
                if r.status_code!=200:raise SourceError(f'GET {urlparse(url).path}: HTTP {r.status_code}')
                parts=[];total=0
                for chunk in r.iter_content(65536):
                    clock.check(2);total+=len(chunk)
                    if total>100*1024*1024:raise SourceError('Descarga excede 100MiB')
                    parts.append(chunk)
                return b''.join(parts)
        except requests.RequestException as exc:
            if attempt==2:raise SourceError(type(exc).__name__) from None
            time.sleep(2**attempt)
    raise SourceError('Descarga agotada')

def norm(s):
    return ''.join(x for x in unicodedata.normalize('NFD',str(s).lower())
                   if unicodedata.category(x)!='Mn').strip()
def number(s):
    m=re.search(r'[-+]?\d+(?:[.,]\d+)?',str(s))
    if not m:raise StructureError('Número ausente')
    x=float(m.group().replace(',','.'))
    if not math.isfinite(x):raise StructureError('Número inválido')
    return x

def validate_event(e):
    if not isinstance(e.get('id'),str) or not re.fullmatch(r'\d+',e['id']):raise ValueError('ID CSN inválido')
    date(e['origin'])
    for k in ('mag_display','reference','url'):
        if not isinstance(e.get(k),str) or not e[k]:raise ValueError('Falta '+k)
    for k,a,b in (('lat',-90,90),('lon',-180,180),('mag',-2,10)):
        v=e.get(k)
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not a<=v<=b:
            raise ValueError('Evento inválido: '+k)
    d=e.get('depth')
    if d is not None and (not isinstance(d,(int,float)) or not math.isfinite(d) or not 0<=d<=800):
        raise ValueError('Profundidad inválida')
    u=urlparse(e['url'])
    if u.scheme!='https' or u.hostname!='www.sismologia.cl' or not re.fullmatch(
      r'/sismicidad/informes/\d{4}/\d{2}/'+re.escape(e['id'])+r'\.html',u.path):
        raise ValueError('URL de informe inválida')
    return e

def leer_evento(html,url):
    soup=BeautifulSoup(html,'html.parser');fields={}
    for row in soup.select('tr'):
        cells=row.find_all(['td','th'])
        if len(cells)==2:fields[norm(cells[0].get_text(' ',strip=True))]=cells[1].get_text(' ',strip=True)
    try:
        origin=datetime.strptime(fields['hora utc'],'%H:%M:%S %d/%m/%Y').replace(tzinfo=UTC)
        depth=fields.get('profundidad','')
        return validate_event(dict(id=Path(urlparse(url).path).stem,origin=iso(origin),
           mag=number(fields['magnitud']),mag_display=f"{number(fields['magnitud']):.1f}",
           reference=fields['referencia'],lat=number(fields['latitud']),lon=number(fields['longitud']),
           depth=number(depth) if re.search(r'\d',depth) else None,url=url))
    except (KeyError,ValueError,TypeError) as exc:raise StructureError('Informe incompatible '+type(exc).__name__) from None

def catalogue_links(html,url,daily,c):
    soup=BeautifulSoup(html,'html.parser')
    tables=[t for t in soup.find_all('table') if any('magnitud' in norm(h.get_text()) for h in t.find_all('th'))]
    if not tables or (daily and 'utc' not in norm(soup.get_text(' '))):
        raise StructureError('Tabla de catálogo/UTC incompatible')
    links=set()
    for table in tables:
        headers=[norm(h.get_text(' ',strip=True)) for h in table.find_all('th')]
        mi=next(i for i,h in enumerate(headers) if 'magnitud' in h)
        if daily and not any('fecha utc' in h for h in headers):raise StructureError('Falta Fecha UTC')
        for row in table.find_all('tr'):
            cells=row.find_all('td',recursive=False)
            if not cells:continue
            if len(cells)!=len(headers):raise StructureError('Fila de catálogo incompatible')
            anchors=[urljoin(url,a['href']) for a in row.find_all('a',href=True)
                     if re.search(r'/sismicidad/informes/\d{4}/\d{2}/\d+\.html$',urljoin(url,a['href']))]
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
            if i==len(urls)-1 and homepage is not None and re.search(r'HTTP (403|404)\b',str(exc)) and homepage_covers(homepage,begin):
                warnings.append(detail)
            else:errors.append(detail)
    for url in sorted(links):
        try:
            e=leer_evento(obtener(url,c,clock),url)
            if start<=date(e['origin'])<=end and e['mag']>=c.mag:events[e['id']]=e
        except (SourceError,BudgetError) as exc:errors.append(dict(url=url,type=type(exc).__name__,error=safe(exc)))
    status='valida' if not errors else 'parcial' if valid else ('incompatible' if any(x['type']=='StructureError' for x in errors) else 'inaccesible')
    return list(events.values()),dict(status=status,pages_ok=valid,pages=len(urls),errors=errors,warnings=warnings)

def ig_default():return dict(status='pendiente',container_id=None,media_id=None,caption='',error='',attempts=0,updated=None)
def record(e, when=None):
    n=iso(when if when is not None else utcnow());return dict(event=e,status='pendiente',attempts=0,next_attempt=n,
       created=n,updated=n,error='',media_id=None,tweet_id=None,text='',
       instagram=ig_default(),instagram_feed=ig_default())
def empty_state(mode):
    return dict(schema=1,mode=mode,account_id=None,ig_account_id=None,
                revision=str(uuid.uuid4()),updated=iso(utcnow()),events={})
def validate_state(s,mode):
    try:
        if s['schema']!=1 or s['mode']!=mode or not isinstance(s['events'],dict):raise ValueError('Esquema')
        date(s['updated'])
        for k in ('account_id','ig_account_id'):
            v=s.get(k)
            if v is not None and not re.fullmatch(r'\d+',str(v)):raise ValueError(k)
        for eid,r in s['events'].items():
            validate_event(r['event'])
            if r['event']['id']!=eid or r['status'] not in STATES:raise ValueError('Estado evento')
            if not isinstance(r['attempts'],int) or r['attempts']<0:raise ValueError('Intentos')
            for k in ('next_attempt','updated','created'):date(r[k])
            for k in ('media_id','tweet_id','text','error'):
                if k not in r:raise ValueError(k)
            if r['status']=='publicado' and not re.fullmatch(r'\d+',str(r['tweet_id'])):raise ValueError('X sin ID')
            for field in ('instagram','instagram_feed'):
                ig=r.get(field)
                if ig is not None:
                    if ig['status'] not in IG_STATES or not isinstance(ig['attempts'],int) or ig['attempts']<0:raise ValueError(field)
                    if ig['status']=='publicado' and not re.fullmatch(r'\d+',str(ig['media_id'])):raise ValueError(field+' sin ID')
        return s
    except (KeyError,TypeError,ValueError) as exc:raise PersistenceError('Estado inválido: '+safe(exc)) from None

class Store:
    def __init__(self,c):
        self.c=c;self.mode='live' if (c.publish_x or c.publish_ig) else 'simulation'
        self.path=Path(c.state_dir)/(self.mode+'.json')
        self.repo=os.getenv('GITHUB_REPOSITORY','');self.token=os.getenv('GITHUB_TOKEN','')
        self.remote=bool(self.repo and self.token);self.sha=None
        if self.mode=='live' and not self.remote:raise PersistenceError('GitHub token/repo obligatorios para publicación')
        if self.repo and not re.fullmatch(r'[\w.-]+/[\w.-]+',self.repo):raise PersistenceError('GITHUB_REPOSITORY inválido')
        self.base='https://api.github.com/repos/'+self.repo
        self.file='/contents/state/'+self.mode+'.json'
    def api(self,method,path,**kwargs):
        try:return requests.request(method,self.base+path,timeout=self.c.timeout,headers={
          'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json',
          'X-GitHub-Api-Version':'2022-11-28'},**kwargs)
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
                    if ref.status_code!=200:raise PersistenceError('Rama predeterminada ilegible')
                    cr=self.api('POST','/git/refs',json={'ref':'refs/heads/'+self.c.branch,'sha':ref.json()['object']['sha']})
                    if cr.status_code!=201:raise PersistenceError('No se creó rama de estado')
                elif branch.status_code!=200:raise PersistenceError('Rama estado inaccesible')
                s=empty_state(self.mode);self.save(s)
            elif r.status_code==200:
                try:
                    b=r.json();self.sha=b['sha'];s=json.loads(base64.b64decode(b['content']))
                except Exception:raise PersistenceError('Estado remoto ilegible') from None
            else:raise PersistenceError(f'Leer estado HTTP {r.status_code}; use --init-state si es nuevo')
        # Validar estructura base ANTES de migrar: un estado corrupto nunca se repara silenciosamente.
        if not isinstance(s, dict) or not isinstance(s.get('events'), dict):
            raise PersistenceError('Estado corrupto: events ausente o inválido')
        # Migración compatible con estado live anterior que solo tenía X.
        s.setdefault('ig_account_id',None)
        for r in s['events'].values():
            for field in ('instagram','instagram_feed'):
                value=r.setdefault(field,ig_default())
                if not isinstance(value,dict):
                    raise PersistenceError('Registro Instagram inválido')
                for key,default in ig_default().items():
                    value.setdefault(key,default)
                if value['status'] in ('enviando','publicando'):
                    value['status']='resultado_incierto'
                    value['error']='Posible publicación previa; comprobar manualmente, NO reenviar'
        validate_state(s,self.mode);atomic(self.path,s);return s
    def save(self,s):
        s['updated']=iso(utcnow());s['revision']=str(uuid.uuid4())
        validate_state(s,self.mode);atomic(self.path,s)
        if not self.remote:return
        raw=json.dumps(s,ensure_ascii=False,allow_nan=False).encode()
        if len(raw)>900000:raise PersistenceError('Estado remoto muy grande')
        body=dict(message='monitor state [skip ci]',branch=self.c.branch,content=base64.b64encode(raw).decode())
        if self.sha:body['sha']=self.sha
        r=self.api('PUT',self.file,json=body)
        if r.status_code not in (200,201):raise PersistenceError(f'CAS HTTP {r.status_code}; no continuar')
        try:
            b=r.json();commit=b['commit']['sha'];blob=b['content']['sha']
            if not all(re.fullmatch('[0-9a-f]{40,64}',x) for x in (commit,blob)):raise ValueError()
        except Exception:raise PersistenceError('PUT potencialmente confirmado sin SHA; no repetir') from None
        for attempt in range(3):
            if attempt:time.sleep(attempt)
            try:confirm=self.api('GET',self.file,params={'ref':commit})
            except PersistenceError:continue
            if confirm.status_code==200:
                try:
                    b=confirm.json();verified=b['sha']==blob and json.loads(base64.b64decode(b['content']))==s
                except Exception:verified=None
                if verified:self.sha=blob;return
                if verified is False:raise PersistenceError('Estado confirmado difiere; detener')
                continue
            if confirm.status_code not in (404,500,502,503,504):
                raise PersistenceError(f'Confirmación HTTP {confirm.status_code}; no repetir PUT')
        raise PersistenceError('Persistencia sin confirmación; no repetir PUT')

def recover(s,start,now=None):
    now = now if now is not None else utcnow()
    counts = {"recovered": 0, "expired": 0}
    for r in s['events'].values():
        if r['status']=='enviando':r.update(status='resultado_incierto',error='Interrupción POST X')
        elif r['status']=='procesando':r.update(status='pendiente',next_attempt=iso(now));counts['recovered']+=1
        if r['status']=='pendiente' and date(r['event']['origin'])<start:r.update(status='expirado');counts['expired']+=1
        for field in ('instagram','instagram_feed'):
            ig=r.setdefault(field,ig_default())
            if ig['status']=='publicando':ig.update(status='resultado_incierto',error='Interrupción media_publish Meta')
            elif ig['status']=='procesando':ig.update(status='pendiente',error='Trabajo recuperado')
    return counts

def eligible(r, c, start, end):
    return (r['status'] == 'pendiente' and r['attempts'] < c.max_attempts
            and start <= date(r['event']['origin']) <= end
            and date(r['next_attempt']) <= end)


def defer_recent(r, c, now):
    ready = date(r['event']['origin']) + timedelta(seconds=c.post+c.margin+c.latency)
    if now < ready:
        r.update(next_attempt=iso(ready), error='Esperando ventana POST, margen y latencia')
        return True
    return False

def retry(r,c,exc):
    delay=min(3600,c.backoff*2**min(max(r['attempts']-1,0),6))
    r.update(status='pendiente',next_attempt=iso(utcnow()+timedelta(seconds=delay)),error=safe(exc),updated=iso(utcnow()))

def tweet_text(e):
    ref=unicodedata.normalize('NFC',' '.join(e['reference'].split()))[:2000]
    local=date(e['origin']).astimezone(ZoneInfo('America/Santiago'))
    depth='no informada' if e['depth'] is None else f"{e['depth']:g} km"
    suffix=(f"\n{local:%d/%m/%Y %H:%M:%S}\nProfundidad: {depth if e['depth'] is None else depth}.\n"
            f"Epicentro: lat {e['lat']:.4f}°, lon {e['lon']:.4f}°.\n{e['url']}")
    prefix=f"Aceleración del Terreno\nSismo M {e['mag_display']} | "
    texts=[prefix+ref+suffix]+[prefix+ref[:i].rstrip()+'…'+suffix for i in range(min(len(ref)-1,280),-1,-1)]
    js="const t=require('twitter-text');const fs=require('fs');const a=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(a.find(s=>t.parseTweet(s).valid)||null));"
    p=subprocess.run(['node','-e',js],input=json.dumps(texts),text=True,capture_output=True,timeout=30)
    if p.returncode:raise Failure('Instalar npm twitter-text@3.1.0')
    result=json.loads(p.stdout)
    if not result:raise Failure('Texto X excede límite')
    return result

class XClient:
    def __init__(self,c,clock):
        self.c=c;self.clock=clock
        credentials=[os.getenv(k,'') for k in SECRETS[:4]]
        if not all(credentials):raise XError('Faltan credenciales X',401)
        self.auth=OAuth1(*credentials)
    def call(self,method,path,empty=False,**kwargs):
        self.clock.check(90);posting=method=='POST' and path=='/tweets'
        try:r=requests.request(method,'https://api.x.com/2'+path,auth=self.auth,
           timeout=max(2,min(self.c.timeout,self.clock.left()-60)),allow_redirects=False,**kwargs)
        except requests.RequestException as exc:raise XError('X '+type(exc).__name__,uncertain=posting) from None
        if not 200<=r.status_code<300:
            try:
                body=r.json()
                detail=safe(json.dumps({k:body[k] for k in ('title','detail','code','message','errors') if k in body},ensure_ascii=False))
            except (ValueError, TypeError, AttributeError):
                detail='Respuesta no JSON'
            reset=r.headers.get('x-rate-limit-reset','')
            raise XError(f'X {path} HTTP {r.status_code}: {detail}',r.status_code,
                         uncertain=posting and (r.status_code>=500 or r.status_code in (408,409)),
                         reset=float(reset) if str(reset).isdigit() else None)
        if empty and (r.status_code==204 or not r.content):return {}
        try:
            b=r.json()
            if not isinstance(b,dict) or b.get('errors'):raise ValueError()
            return b
        except ValueError:raise XError('Respuesta X incompatible',uncertain=posting) from None
    def identity(self):
        b=self.call('GET','/users/me').get('data',{})
        if not re.fullmatch(r'\d+',str(b.get('id',''))):raise XError('Identidad X inválida',401)
        return str(b['id'])
    def upload(self,video):
        data=self.call('POST','/media/upload/initialize',json=dict(media_type='video/mp4',
          media_category='tweet_video',total_bytes=Path(video).stat().st_size)).get('data',{})
        mid=str(data.get('id',''))
        if not re.fullmatch(r'\d+',mid):raise XError('X media ID inválido')
        with open(video,'rb') as f:
            idx=0
            while True:
                chunk=f.read(4*1024*1024)
                if not chunk:break
                self.call('POST',f'/media/upload/{mid}/append',empty=True,data={'segment_index':str(idx)},
                          files={'media':('segment.mp4',chunk,'application/octet-stream')});idx+=1
        data=self.call('POST',f'/media/upload/{mid}/finalize').get('data',{})
        if str(data.get('id',''))!=mid:raise XError('X finalize inválido')
        until=min(time.monotonic()+self.c.x_wait,self.clock.end-100)
        while data.get('processing_info'):
            info=data['processing_info'];status=info.get('state')
            if status=='succeeded':break
            if status=='failed':raise XError('X video rechazado')
            if status not in ('pending','in_progress'):raise XError('X estado video inválido')
            delay=max(1,float(info.get('check_after_secs',5)))
            if time.monotonic()+delay>=until:raise XError('Timeout procesamiento X')
            time.sleep(delay)
            data=self.call('GET','/media/upload',params={'command':'STATUS','media_id':mid}).get('data',{})
        return mid
    def create(self,text,mid):
        b=self.call('POST','/tweets',json={'text':text,'media':{'media_ids':[mid]}}).get('data',{})
        tid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',tid):raise XError('Tweet ID no confirmado',uncertain=True)
        return tid
    def reconcile(self,state):
        """Inspección de candidatos; nunca autoriza reenvíos automáticos."""
        unsure=[r for r in state['events'].values() if r['status']=='resultado_incierto']
        if not unsure:return
        posts=[];page=None
        for _ in range(5):
            params={'max_results':100,'tweet.fields':'created_at,entities,attachments'}
            if page:params['pagination_token']=page
            response=self.call('GET','/users/'+state['account_id']+'/tweets',params=params)
            posts.extend(response.get('data',[]));page=response.get('meta',{}).get('next_token')
            if not page:break
        for r in unsure:
            found=[]
            for item in posts:
                msg=item.get('text','')
                for u in item.get('entities',{}).get('urls',[]):
                    msg=msg.replace(u.get('url',''),u.get('expanded_url',u.get('url','')))
                try:
                    fits=(msg==r['text'] and item.get('attachments',{}).get('media_keys')
                      and date(item['created_at'])>=date(r.get('sent_at',r['created']))-timedelta(seconds=10))
                except (ValueError,KeyError):fits=False
                if fits:found.append(str(item['id']))
            log('X reconciliación '+r['event']['id']+': candidatos '+','.join(found)+
                '; verificar manualmente video y autoría; estado continúa bloqueado')

def instagram_caption(e):
    local=date(e['origin']).astimezone(ZoneInfo('America/Santiago'))
    depth='no informada' if e['depth'] is None else f"{e['depth']:g} km"
    text=(f"Aceleración horizontal observada | Sismo M {e['mag_display']}\n\n"
      f"Referencia: {' '.join(e['reference'].split())}\n"
      f"Fecha (Chile): {local:%d/%m/%Y %H:%M:%S}\nProfundidad: {depth}\n"
      f"Epicentro: {e['lat']:.4f}°, {e['lon']:.4f}°\n"
      f"Fuente: Centro Sismológico Nacional (CSN)\nInforme: {e['url']}\n\n"
      "Visualización instrumental; no es una predicción.\n#SismosChile #Sismología #AceleraciónDelSuelo")
    if len(text)>2200:raise Failure('Texto de Instagram demasiado largo')
    return text

class InstagramClient:
    def __init__(self,c,clock):
        self.c=c;self.clock=clock
        self.uid=(os.getenv('IG_USER_ID') or os.getenv('INSTAGRAM_USER_ID') or '').strip();self.token=(os.getenv('IG_ACCESS_TOKEN') or os.getenv('INSTAGRAM_TOKEN') or '').strip()
        self.expected_username=os.getenv('IG_EXPECTED_USERNAME','groundshake_cl').strip().lstrip('@').lower()
        if not re.fullmatch(r'\d+',self.uid) or not self.token:raise PublishError('Faltan IG_USER_ID/INSTAGRAM_USER_ID o IG_ACCESS_TOKEN/INSTAGRAM_TOKEN')
        self.base=f'https://graph.instagram.com/{c.graph_version}'
    def call(self,method,path,publish=False,**kwargs):
        self.clock.check(20)
        try:r=requests.request(method,self.base+path,headers={'Authorization':'Bearer '+self.token},
           timeout=max(2,min(self.c.timeout,self.clock.left()-5)),allow_redirects=False,**kwargs)
        except requests.RequestException as exc:raise PublishError('Instagram '+type(exc).__name__,uncertain=publish) from None
        if not 200<=r.status_code<300:
            raise PublishError(f'Meta HTTP {r.status_code} {path}',r.status_code,
                               uncertain=publish and r.status_code in (408,409,429,500,502,503,504))
        try:
            b=r.json()
            if not isinstance(b,dict) or b.get('error'):raise ValueError()
            return b
        except ValueError:raise PublishError('Respuesta Meta inválida',uncertain=publish) from None
    def identity(self):
        b=self.call('GET','/'+self.uid,params={'fields':'id,username'})
        if str(b.get('id'))!=self.uid or str(b.get('username','')).lower()!=self.expected_username:
            raise PublishError('Cuenta Instagram distinta de @'+self.expected_username+'; se bloquea el envío')
        return self.uid
    def container(self,caption,video_url):
        """Instagram Login: Meta descarga el MP4 de una URL HTTPS pública.
        La carga resumable/rupload no se presupone compatible con Instagram Login.
        """
        u=urlparse(video_url)
        if u.scheme!='https' or not u.hostname:
            raise PublishError('Reel requiere URL HTTPS pública')
        b=self.call('POST','/'+self.uid+'/media',data={'media_type':'REELS',
          'video_url':video_url,'caption':caption,'share_to_feed':'false'})
        cid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',cid):raise PublishError('Contenedor Meta sin ID')
        return cid
    def image_container(self,caption,image_url):
        """Crea una publicación fotográfica distinta del Reel."""
        parsed=urlparse(image_url)
        if parsed.scheme!='https' or not parsed.hostname:
            raise PublishError('La imagen de feed requiere URL HTTPS pública')
        b=self.call('POST','/'+self.uid+'/media',data={
            'image_url':image_url,'caption':caption})
        cid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',cid):raise PublishError('Contenedor imagen sin ID')
        return cid

    def wait(self,cid):
        end=min(time.monotonic()+240,self.clock.end-40)
        while time.monotonic()<end:
            b=self.call('GET','/'+cid,params={'fields':'status_code,status'})
            status=b.get('status_code')
            if status=='FINISHED':return
            if status in ('ERROR','EXPIRED','PUBLISHED'):raise PublishError('Contenedor IG '+status)
            if status not in ('IN_PROGRESS','PROCESSING','PENDING'):raise PublishError('Estado Instagram '+str(status))
            time.sleep(5)
        raise PublishError('Timeout procesando Reel')
    def publish(self,cid):
        b=self.call('POST','/'+self.uid+'/media_publish',publish=True,data={'creation_id':cid})
        mid=str(b.get('id',''))
        if not re.fullmatch(r'\d+',mid):raise PublishError('Reel sin media ID',uncertain=True)
        return mid

def reel_video(video,clock):
    src=Path(video);target=src.with_name('instagram_reel.mp4')
    clock.check(130)
    command=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(src),
      '-f','lavfi','-i','anullsrc=channel_layout=stereo:sample_rate=48000',
      '-map','0:v:0','-map','1:a:0','-vf',
      'scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,fps=30,format=yuv420p',
      '-c:v','libx264','-preset','veryfast','-crf','22','-c:a','aac','-b:a','128k','-ar','48000',
      '-shortest','-movflags','+faststart',str(target)]
    subprocess.run(command,check=True,capture_output=True,timeout=min(120,max(30,clock.left()-20)))
    if not target.exists() or target.stat().st_size==0:raise Failure('Reel vacío')
    return target

def public_github_media(store,event_id,local_path,clock,kind):
    """Alojamiento HTTPS estable, público, direccionado por SHA256, sin túnel.

    El archivo queda accesible públicamente en la rama STATE_BRANCH del repositorio.
    No subir información sensible. GitHub Contents API limita archivos a 100 MiB;
    se exige un margen conservador de 90 MiB.
    """
    path=Path(local_path)
    raw=path.read_bytes()
    if not raw or len(raw)>90*1024*1024:
        raise Failure('Archivo público ausente o mayor que 90 MiB')
    digest=hashlib.sha256(raw).hexdigest()
    ext=path.suffix.lower()
    if (kind=='reel' and ext!='.mp4') or (kind=='feed' and ext!='.jpg'):
        raise Failure('Extensión multimedia no admitida')
    repo=store.repo
    branch=store.c.branch
    info=store.api('GET','')
    if info.status_code!=200 or info.json().get('private') is not False:
        raise Failure('Instagram requiere repositorio GitHub público para archivos HTTPS; no se sube material a repositorios privados')
    destination=f'ig_media/{event_id}/{kind}-{digest[:24]}{ext}'
    endpoint='/contents/'+destination
    existing=store.api('GET',endpoint,params={'ref':branch})
    if existing.status_code==200:
        try:
            meta=existing.json()
            content_b64=meta.get('content')
            if content_b64 and meta.get('encoding')=='base64':
                if hashlib.sha256(base64.b64decode(content_b64)).hexdigest()!=digest:
                    raise PersistenceError('Ruta pública existente con contenido diferente')
            elif meta.get('size')!=len(raw):
                raise PersistenceError('Multimedia remota tiene tamaño inesperado')
            # Un GET público por commit verificará la huella SHA256 más abajo.
            ref=store.api('GET','/git/ref/heads/'+quote(branch,safe=''))
            if ref.status_code!=200:raise PersistenceError('No se pudo resolver rama de contenido')
            commit=ref.json()['object']['sha']
        except (KeyError,ValueError) as exc:
            raise PersistenceError('Imagen/video remoto ilegible: '+type(exc).__name__) from None
    elif existing.status_code==404:
        clock.check(55)
        payload={'message':f'IG public {kind} {event_id} [skip ci]',
                 'branch':branch,'content':base64.b64encode(raw).decode()}
        written=store.api('PUT',endpoint,json=payload)
        if written.status_code not in (200,201):
            raise PersistenceError(f'PUT multimedia HTTP {written.status_code}: comprobar antes de reintentar')
        try:commit=written.json()['commit']['sha']
        except (KeyError,ValueError,TypeError):
            raise PersistenceError('PUT multimedia aceptado sin commit confirmable') from None
    else:raise PersistenceError(f'Consultar multimedia pública HTTP {existing.status_code}')
    if not re.fullmatch('[0-9a-f]{40,64}',commit):
        raise PersistenceError('Commit multimedia inválido')
    url=f'https://raw.githubusercontent.com/{repo}/{commit}/{destination}'
    for attempt in range(5):
        clock.check(10)
        try:
            r=requests.get(url,timeout=min(20,max(2,clock.left()-5)),stream=True)
            try:
                if r.status_code==200:
                    h=hashlib.sha256()
                    size=0
                    for chunk in r.iter_content(1024*1024):
                        h.update(chunk);size+=len(chunk)
                        if size>len(raw):break
                    if size==len(raw) and h.hexdigest()==digest:return url
            finally:r.close()
        except (requests.RequestException,ValueError):pass
        time.sleep(2+attempt)
    raise Failure('Multimedia aún no disponible públicamente para Meta; reintentar después')


def public_feed_photo(store,event_id,folder,clock):
    preview=folder/'preview.png'
    if not preview.is_file():raise Failure('No existe preview.png')
    photo=folder/'instagram_feed.jpg'
    clock.check(65)
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(preview),
                    '-frames:v','1','-q:v','3',str(photo)],check=True,capture_output=True,
                   timeout=min(30,max(5,clock.left()-25)))
    if not photo.read_bytes().startswith(b'\xff\xd8') or photo.stat().st_size>8_000_000:
        raise Failure('Imagen JPEG inválida o demasiado grande')
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
                az=ch.azimuth%180
                axis='N' if min(az,180-az)<=1 else ('E' if abs(az-90)<=1 else None)
                if axis is None:continue
                d=distance(e['lat'],e['lon'],ch.latitude,ch.longitude)
                if d>c.radius:continue
                item=dict(id=sid,net=net.code,sta=sta.code,loc=ch.location_code,cha=ch.code,
                          lat=ch.latitude,lon=ch.longitude,distance=d)
                pairs.setdefault((net.code,sta.code,ch.location_code,ch.code[:-1]),{}).setdefault(axis,item)
    found=[]
    for key,p in pairs.items():
        if set(p)!=set(('N','E')):continue
        n,east=p['N'],p['E']
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
    if np.ma.is_masked(a) or len(a)<100 or not np.isfinite(a).all() or np.ptp(a.astype(float))==0:
        raise Failure('Muestras sin calidad suficiente')
    extreme=(a==np.max(a))|(a==np.min(a))
    if np.any(extreme[:-2]&extreme[1:-1]&extreme[2:]):raise Failure('Saturación probable')
    if np.issubdtype(a.dtype,np.integer):
        info=np.iinfo(a.dtype)
        if np.any((a==info.min)|(a==info.max)):raise Failure('Saturación digital')
    return tr

def process_component(s,e,c,clock):
    import numpy as np
    from obspy import read,read_inventory,UTCDateTime
    t=UTCDateTime(e['origin']);start=t-c.pre-c.margin;end=t+c.post+c.margin
    params=dict(net=s['net'],sta=s['sta'],loc=s['loc'] or '--',cha=s['cha'],starttime=str(start),endtime=str(end))
    base=c.fdsn.rstrip('/')+'/fdsnws/'
    metadata=obtener(base+'station/1/query',c,clock,dict(params,level='response',format='xml'))
    if not metadata:raise Failure('Sin metadatos instrumentales')
    inv=read_inventory(io.BytesIO(metadata));response=inv.get_response(s['id'],t)
    sens=response.instrument_sensitivity
    units=sens.input_units.upper().replace(' ','') if sens else ''
    if units not in ('M/S**2','M/S^2','M/S/S') or not sens or sens.value<=0 or not response.response_stages:
        raise Failure('Sin calibración de acelerómetro')
    raw=obtener(base+'dataselect/1/query',c,clock,params)
    if not raw:raise Failure('Sin miniseed')
    tr=check_raw(read(io.BytesIO(raw),format='MSEED').select(id=s['id']),start,end)
    if tr.stats.sampling_rate<50:raise Failure('Muestreo insuficiente')
    tr.data=tr.data.astype(np.float64);tr.detrend('linear')
    tr.remove_response(inventory=inv,output='ACC',pre_filt=(.1,.2,20,24),
                       water_level=None,zero_mean=True,taper=True,taper_fraction=.05)
    tr.filter('bandpass',freqmin=.2,freqmax=20,corners=4,zerophase=True)
    tr.trim(t-c.pre,t+c.post,nearest_sample=False)
    if not np.isfinite(tr.data).all():raise Failure('Aceleración no finita')
    return dict(s,times=tr.times()+float(tr.stats.starttime-t),values=tr.data)

def process_station(s,e,c,clock):
    return dict(s,components=[process_component(p,e,c,clock) for p in s['components']])
def collect_stations(candidates,e,c,clock):
    valid=[];rejected=[]
    with cf.ThreadPoolExecutor(max_workers=c.workers) as pool:
        tasks={pool.submit(process_station,s,e,c,clock):s for s in candidates}
        for f in cf.as_completed(tasks):
            try:valid.append(f.result())
            except Exception as exc:rejected.append(dict(station=tasks[f]['id'],reason=safe(exc)))
    unique=[];seen=set()
    for s in sorted(valid,key=lambda x:(x['distance'],x['id'])):
        k=(s['net'],s['sta'])
        if k not in seen:unique.append(s);seen.add(k)
    return unique[:c.stations],rejected

def collect(candidates, processor, workers):
    """Procesa candidatos en paralelo; evita estaciones físicas repetidas."""
    valid, rejected = [], []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(processor, station): station for station in candidates}
        for task in cf.as_completed(tasks):
            try:
                valid.append(task.result())
            except Exception as exc:
                rejected.append({'station': tasks[task]['id'], 'reason': safe(exc)})
    unique, seen = [], set()
    for station in sorted(valid, key=lambda st: (st['distance'], st['id'])):
        k = (station.get('net', ''), station.get('sta', station['id']))
        if k in seen:
            rejected.append({'station': station['id'], 'reason': 'Estación duplicada'})
        else:
            unique.append(station)
            seen.add(k)
    return unique, rejected

def station_peaks(s,centers,step):
    import numpy as np
    if len(s['components'])!=2:raise Failure('Faltan ejes N/E')
    a=np.empty(len(centers))
    for j,t in enumerate(centers):
        m=[]
        for p in s['components']:
            values=p['values'][(p['times']>=t-step/2)&(p['times']<t+step/2)]
            if not len(values) or not np.isfinite(values).all():raise Failure('Intervalo horizontal incompleto; no se rellena')
            m.append(float(np.max(np.abs(values))))
        a[j]=max(m)*100/9.80665
    return a

def spatial_weights(lons,lats,gx,gy,e,c):
    import numpy as np
    from scipy.spatial import Delaunay,QhullError
    def xy(lon,lat):return np.column_stack(((np.ravel(lon)-e['lon'])*111.19*math.cos(math.radians(e['lat'])),(np.ravel(lat)-e['lat'])*111.19))
    pts=xy(lons,lats);grid=xy(gx,gy);w=np.zeros((len(grid),len(pts)));mask=np.zeros(len(grid),bool)
    if len(pts)<3:return w,mask,'Menos de tres estaciones'
    try:
        tri=Delaunay(pts);simp=tri.find_simplex(grid);v=tri.simplices[np.maximum(simp,0)]
        edge=np.max([np.linalg.norm(pts[v[:,i]]-pts[v[:,j]],axis=1) for i,j in ((0,1),(1,2),(2,0))],axis=0)
        d=np.linalg.norm(grid[:,None,:]-pts[None,:,:],axis=2);near=d<=c.support
        mask=(simp>=0)&(near.sum(axis=1)>=3)&(edge<=c.triangle)
        w=np.where(near,1/np.maximum(d,.001)**2,0);w/=np.maximum(w.sum(axis=1,keepdims=True),1e-30);w[~mask]=0
    except QhullError:return w,mask,'Geometría degenerada'
    return w,mask,'IDW local, soporte y triángulos limitados' if mask.any() else 'Sin soporte de interpolación'

def prepare_map(c,clock):
    import cartopy
    root=Path(cartopy.config['data_dir'])/'shapefiles'/'natural_earth'
    for category,name in (('physical','land'),('physical','ocean'),('physical','coastline'),
                          ('cultural','admin_0_boundary_lines_land')):
        stem='ne_110m_'+name;dest=root/category
        if all((dest/(stem+x)).is_file() for x in ('.shp','.shx','.dbf')):continue
        data=obtener(f'https://naturalearth.s3.amazonaws.com/110m_{category}/{stem}.zip',c,clock)
        dest.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for ext in ('.shp','.shx','.dbf','.prj','.cpg'):
                if stem+ext in z.namelist():(dest/(stem+ext)).write_bytes(z.read(stem+ext))
        if not all((dest/(stem+x)).is_file() for x in ('.shp','.shx','.dbf')):raise Failure('Cartografía incompleta')

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
    extent=[min(lons.min(),e['lon'])-.5,max(lons.max(),e['lon'])+.5,
            min(lats.min(),e['lat'])-.5,max(lats.max(),e['lat'])+.5]
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
    mesh=ax.pcolormesh(gx,gy,np.ma.masked_invalid(field(0)),cmap=cmap,norm=normal,alpha=.85,
                       shading='auto',edgecolors='none',zorder=2)
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
        p.plot(times,np.ma.masked_less_equal(amps[i],0),color=colors[i],linewidth=1,alpha=.85,
               label=f"{s['net']}.{s['sta']} · {s['distance']:.0f} km")
    p.set_yscale('log');p.axvline(0,color='#ef6351',linestyle='--',linewidth=1)
    cursor=p.axvline(times[0],color='#263449',linewidth=1.5)
    cursor_label=p.text(times[0],.96,'',transform=p.get_xaxis_transform(),ha='center',va='top',fontsize=8,
                        color='white',bbox={'facecolor':'#263449','edgecolor':'none','pad':2})
    p.set(xlim=(-c.pre,c.post),ylim=(vmin,vmax),xlabel='Tiempo respecto al origen [s]',
          ylabel='Pico horizontal [% g]')
    p.set_title('ACELERACIÓN DE LAS ESTACIONES',fontsize=10,pad=28);p.grid(alpha=.25)
    p.legend(loc='upper center',bbox_to_anchor=(.5,-.13),fontsize=6,frameon=False,ncol=3)
    depth='no informada' if e['depth'] is None else f"{e['depth']:g} km"
    local=date(e['origin']).astimezone(ZoneInfo('America/Santiago'))
    fig.suptitle(f"Sismo M {e['mag_display']} | {e['reference'][:85]}\n{local:%d/%m/%Y %H:%M:%S} · Profundidad: {depth}",fontsize=12,y=.96)
    label=fig.text(.06,.855,'',fontsize=10)
    fig.text(.04,.022,'Fuente: Centro Sismológico Nacional de la Universidad de Chile',fontsize=8)
    video=folder/'video.mp4'
    writer=FFMpegWriter(fps=c.fps,codec='libx264',bitrate=5000,extra_args=[
       '-vf','fps=30,format=yuv420p','-profile:v','high','-g','60','-movflags','+faststart'])
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
    p=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(video)],
                      check=True,capture_output=True,text=True,timeout=30)
    b=json.loads(p.stdout);stream=next(s for s in b['streams'] if s['codec_type']=='video')
    duration=float(b['format']['duration'])
    if (stream['codec_name']!='h264' or stream['pix_fmt']!='yuv420p' or
        (stream['width'],stream['height'])!=(1280,720) or abs(duration-n/c.fps)>.15 or
        not .5<=duration<=140 or video.stat().st_size>512*1024*1024):raise Failure('ffprobe rechazó MP4')
    return dict(duration_seconds=duration,physical_seconds=c.pre+c.post,
      frame_step_seconds=c.step,interpolation=area,interpolation_reason=reason,
      stations_used=len(stations),grid_cells_with_support=int(mask.sum()),
      metric='horizontal_component_peak_percent_g',band_hz=[.2,20])

def render_worker(event_path,c):
    e=validate_event(json.loads(Path(event_path).read_text(encoding='utf8')))
    folder=Path(event_path).parent;clock=Clock(c.event_budget)
    quality={'event':e,'metric':'horizontal peak % g, 0.2-20 Hz',
             'clipping_test':'heuristic; no prueba ausencia de saturación'}
    try:
        candidates,excluded=stations_for(e,c,clock)
        valid,rejected=collect_stations(candidates,e,c,clock)
        quality.update(attempted=len(candidates),valid=len(valid),rejected=excluded+rejected)
        if not valid:raise Failure('Sin estaciones válidas')
        quality['used']=[{k:v for k,v in s.items() if k!='components'} for s in valid]
        quality['video']=render_video(e,valid,c,clock,folder)
        atomic(folder/'quality.json',quality);return 0
    except Exception as exc:
        quality['error']=safe(exc);atomic(folder/'quality.json',quality)
        log('Render falló: '+safe(exc));return 1

def build_video(e,c,clock):
    clock.check(180);folder=Path(c.output)/e['id'];folder.mkdir(parents=True,exist_ok=True)
    atomic(folder/'event.json',e)
    for name in ('video.mp4','preview.png','quality.json','instagram_reel.mp4'):(folder/name).unlink(missing_ok=True)
    env=dict(os.environ)
    for k in SECRETS:env.pop(k,None)
    limit=min(c.event_budget,clock.left()-120);env['EVENT_TIMEOUT_SEC']=str(limit)
    try:r=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--render',str((folder/'event.json').resolve())],
                         env=env,timeout=limit,check=False)
    except subprocess.TimeoutExpired:raise Failure('Timeout procesando evento') from None
    video=folder/'video.mp4'
    if r.returncode or not video.is_file():raise Failure('No se generó video; revisar quality.json')
    return video

def send_transaction(store, s, r, x, text, mid):
    """Persistencia de intención antes de POST. Nunca repetir resultado incierto."""
    r.update(status='enviando', text=text, media_id=mid,
             sent_at=iso(utcnow()), updated=iso(utcnow()))
    store.save(s)
    try:
        tid=x.create(text,mid)
    except Exception as exc:
        if isinstance(exc,XError) and not exc.uncertain:
            retry(r,store.c,exc)
        else:
            r.update(status='resultado_incierto',error=safe(exc),updated=iso(utcnow()))
        store.save(s)
        raise
    r.update(status='publicado',tweet_id=tid,error='',updated=iso(utcnow()))
    log('X confirmado: https://x.com/i/web/status/'+str(tid))
    store.save(s)
    return tid

def publish_x_record(store,s,r,x,c,clock):
    text=tweet_text(r['event'])
    video=build_video(r['event'],c,clock)
    mid=x.upload(video)
    return send_transaction(store,s,r,x,text,mid)

def process_instagram(store,s,c,clock,report):
    if not c.publish_ig:return
    ig=InstagramClient(c,clock);uid=ig.identity()
    if s.get('ig_account_id') not in (None,uid):raise PersistenceError('Instagram asociado a otra cuenta')
    if s.get('ig_account_id') is None:s['ig_account_id']=uid;store.save(s)
    section=report.setdefault('instagram',dict(reels_published=0,feed_published=0,uncertain=0,errors=[]))
    processed=0
    for r in sorted(s['events'].values(),key=lambda a:a['event']['origin']):
        eid=r['event']['id']
        if c.publish_x and r['status']!='publicado':continue
        if date(r['event']['origin'])<utcnow()-timedelta(hours=c.lookback):continue
        ready=date(r['event']['origin'])+timedelta(seconds=c.post+c.margin+c.latency)
        if utcnow()<ready:continue
        caption=instagram_caption(r['event'])
        for field in ('instagram','instagram_feed'):
            item=r.setdefault(field,ig_default())
            if item['status']=='resultado_incierto':section['uncertain']+=1;continue
            if item['status']=='publicado' or item['attempts']>=c.max_attempts:continue
            if processed>=c.max_events*2 or clock.left()<180:return
            processed+=1
            item.update(status='procesando',attempts=item['attempts']+1,updated=iso(utcnow()))
            store.save(s)
            try:
                folder=Path(c.output)/eid
                if field=='instagram':
                    original=folder/'video.mp4'
                    if not original.exists():original=build_video(r['event'],c,clock)
                    reel=reel_video(original,clock)
                    video_url=public_github_media(store,eid,reel,clock,'reel')
                    cid=ig.container(caption,video_url)
                    item.update(container_id=cid,caption=caption,updated=iso(utcnow()));store.save(s)
                else:
                    if not (folder/'preview.png').is_file():
                        build_video(r['event'],c,clock)
                    image_url=public_feed_photo(store,eid,folder,clock)
                    cid=ig.image_container(caption,image_url)
                    item.update(container_id=cid,caption=caption,updated=iso(utcnow()));store.save(s)
                ig.wait(cid)
                item.update(status='publicando',updated=iso(utcnow()));store.save(s)
                # Solo esta etapa es incierta; nunca reenviar automáticamente.
                try:mid=ig.publish(cid)
                except Exception as exc:
                    item.update(status='resultado_incierto',error=safe(exc),updated=iso(utcnow()))
                    store.save(s);raise
                item.update(status='publicado',media_id=mid,error='',updated=iso(utcnow()))
                store.save(s)
                k='reels_published' if field=='instagram' else 'feed_published'
                section[k]+=1
                log(f'Instagram {field} publicado: evento {eid}, media {mid}')
            except PersistenceError:raise
            except Exception as exc:
                if item['status']=='procesando':
                    item.update(status='pendiente',error=safe(exc),updated=iso(utcnow()));store.save(s)
                if item['status']=='resultado_incierto':section['uncertain']+=1
                section['errors'].append(f'{eid} {field}: {type(exc).__name__}: {safe(exc)}')
                log(f'ERROR Instagram {field} {eid}: {safe(exc)}')
                if isinstance(exc,PublishError) and exc.global_stop:return

def run(c,init=False):
    end=utcnow();start=end-timedelta(hours=c.lookback);clock=Clock(c.budget)
    report=dict(start_utc=iso(start),end_utc=iso(end),mode='live' if c.publish_x or c.publish_ig else 'simulation',
                found=0,new=0,pending=0,results=[],errors=[],warnings=[])
    code=0;s=None;Path(c.output).mkdir(parents=True,exist_ok=True)
    try:
        log(f'Ventana {iso(start)} - {iso(end)}; M >= {c.mag}')
        store=Store(c);s=store.load(init);recover(s,start);store.save(s)
        x=XClient(c,clock) if c.publish_x else None
        if x:
            uid=x.identity()
            if s['account_id'] not in (None,uid):raise PersistenceError('Cuenta X distinta')
            if s['account_id'] is None:s['account_id']=uid;store.save(s)
            if c.reconcile:x.reconcile(s)
        events,source=discover(c,clock,start,end)
        report['source']=source;report['found']=len(events)
        if source['status']!='valida':code=1;report['warnings'].append('Catálogo CSN parcial')
        for e in events:
            if e['id'] not in s['events']:s['events'][e['id']]=record(e,end);report['new']+=1
        store.save(s)
        processed=0
        # Si solo IG está habilitado, X queda pendiente: Instagram se procesa aparte.
        if c.publish_x or not c.publish_ig:
            for r in sorted(s['events'].values(),key=lambda a:a['event']['origin']):
                if r['status']=='resultado_incierto':report['warnings'].append(r['event']['id']+' X incierto; NO reenviar');continue
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
        if c.publish_ig:
            process_instagram(store,s,c,clock,report)
            if report.get('instagram',{}).get('errors'):code=1
    except Exception as exc:
        code=1;report['errors'].append(type(exc).__name__+': '+safe(exc));log('ERROR '+safe(exc))
    finally:
        if s is not None:
            report['pending']=sum(r['status']=='pendiente' for r in s['events'].values())
            report['uncertain_x']=sum(r['status']=='resultado_incierto' for r in s['events'].values())
            report['uncertain_instagram']=sum(r[k]['status']=='resultado_incierto' for r in s['events'].values() for k in ('instagram','instagram_feed'))
        report['exit_code']=code;atomic(Path(c.output)/'summary.json',report)
        summary='## CSN → aceleración → X e Instagram\n\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n'
        (Path(c.output)/'summary.md').write_text(summary,encoding='utf8')
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf8') as f:f.write(summary)
        log(f"Ciclo terminado exit={code}; nuevos={report['new']}; pendientes={report['pending']}")
    return code

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--init-state',action='store_true')
    parser.add_argument('--render')
    args=parser.parse_args()
    try:
        c=Config.env()
        return render_worker(args.render,c) if args.render else run(c,args.init_state)
    except Exception as exc:log('CONFIGURACIÓN '+type(exc).__name__+': '+safe(exc));return 1

if __name__=='__main__':sys.exit(main())
