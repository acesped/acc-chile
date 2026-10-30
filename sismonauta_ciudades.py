#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA | Sismos cerca de 15 ciudades, EMSC-CSEM.

Consulta retrospectiva ampliada (300 min por defecto), ejecución sugerida cada
10 min, magnitud 4.0 <= M < 7.0, radio 100 km. Registro por evento/plataforma
para no reenviar publicaciones confirmadas ni resultados inciertos.

Instalar: pip install requests pillow
Uso: python sismonauta_ciudades.py [--out output_ciudades] [--publish]
     python sismonauta_ciudades.py --test
El catálogo EMSC puede incorporar o modificar eventos con demora.
NO constituye alerta sísmica oficial ni confirma que el movimiento se sintió.
"""
from __future__ import annotations
import argparse, base64, io, json, math, os, re, sys, time, urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont, ImageEnhance

UTC=timezone.utc
EMSC_API='https://www.seismicportal.eu/fdsnws/event/1/query'
SATELLITE_API='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export'
MIN_MAG=4.0; MAX_MAG=7.0; RADIUS_KM=100.0
LOOKBACK_HOURS=0; LOOKBACK_MINUTES=10
EMSC_RECOVERY_MINUTES=max(10,int(os.getenv('EMSC_RECOVERY_MINUTES','300')))
MAX_EVENTS_PER_RUN=30; WIDTH=1080; HEIGHT=1350; MAP_HALF_WIDTH_M=135000
OUT=Path('output_ciudades'); STATE_PATH='sismonauta_ciudades/state.json'; IMAGE_DIR='sismonauta_ciudades/images'
GRAPH_VERSION=os.getenv('META_GRAPH_VERSION','v25.0')
IG_BASE=os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
FB_BASE='https://graph.facebook.com'; PLATFORMS=('instagram','facebook')
HTTP=requests.Session(); HTTP.headers.update({'User-Agent':'Sismonauta-EMSC/5.1','Accept':'application/json'})
CITIES=[
 ('Santiago','Chile','🇨🇱',-33.4489,-70.6693,'America/Santiago'),
 ('Coquimbo','Chile','🇨🇱',-29.9533,-71.3395,'America/Santiago'),
 ('Concepción','Chile','🇨🇱',-36.8201,-73.0444,'America/Santiago'),
 ('Iquique','Chile','🇨🇱',-20.2307,-70.1357,'America/Santiago'),
 ('Lima','Perú','🇵🇪',-12.0464,-77.0428,'America/Lima'),
 ('Arequipa','Perú','🇵🇪',-16.4090,-71.5375,'America/Lima'),
 ('Ciudad de México','México','🇲🇽',19.4326,-99.1332,'America/Mexico_City'),
 ('Guadalajara','México','🇲🇽',20.6597,-103.3496,'America/Mexico_City'),
 ('Cali','Colombia','🇨🇴',3.4516,-76.5320,'America/Bogota'),
 ('Bogotá','Colombia','🇨🇴',4.7110,-74.0721,'America/Bogota'),
 ('Caracas','Venezuela','🇻🇪',10.4806,-66.9036,'America/Caracas'),
 ('Maracaibo','Venezuela','🇻🇪',10.6427,-71.6125,'America/Caracas'),
 ('Quito','Ecuador','🇪🇨',-0.1807,-78.4678,'America/Guayaquil'),
 ('La Habana','Cuba','🇨🇺',23.1136,-82.3666,'America/Havana'),
 ('Los Ángeles','Estados Unidos','🇺🇸',34.0522,-118.2437,'America/Los_Angeles'),
]

def log(*values): print(*values,flush=True)
def now_utc(): return datetime.now(UTC)
def iso(value): return value.astimezone(UTC).isoformat()
def safe_name(value): return re.sub(r'[^a-zA-Z0-9_-]','_',str(value))[:100]
def save_json(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp'); temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); temp.replace(path)
def short_error(exc):
    message=str(exc)
    for key in ('GH_TOKEN','INSTAGRAM_TOKEN','FACEBOOK_PAGE_TOKEN'):
        secret=os.getenv(key,'')
        if secret: message=message.replace(secret,'***')
    return message[:350]
def parse_time(value):
    if not value: raise ValueError('Hora de origen no disponible')
    s=str(value).strip().replace('Z','+00:00')
    # El servicio puede entregar milisegundos o un desplazamiento horario.
    d=datetime.fromisoformat(s)
    if d.tzinfo is None: d=d.replace(tzinfo=UTC)
    return d.astimezone(UTC)
def depth_text(value): return 'No disponible' if value is None else f'{value:.1f} km'

def haversine(lat1,lon1,lat2,lon2):
    lat1,lon1,lat2,lon2=map(math.radians,(lat1,lon1,lat2,lon2))
    a=math.sin((lat2-lat1)/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371.0088*2*math.asin(math.sqrt(min(1.,max(0.,a))))
def nearest_cities(lat,lon): return sorted(((haversine(lat,lon,c[3],c[4]),c) for c in CITIES),key=lambda item:item[0])
def matching_cities(lat,lon): return [(d,c) for d,c in nearest_cities(lat,lon) if d<=RADIUS_KM]

def fetch_events():
    """Recupera eventos con margen por retraso de incorporación en EMSC.

    --lookback-minutes conserva la frecuencia de referencia de 10 minutos;
    NO limita la consulta retrospectiva, configurable con EMSC_RECOVERY_MINUTES.
    """
    end=now_utc(); start=end-timedelta(minutes=EMSC_RECOVERY_MINUTES)
    duration=(end-start).total_seconds()
    log('='*52,'\nCONSULTA SISMICA EMSC-CSEM\nINICIO UTC:',iso(start),'\nFIN UTC:',iso(end))
    log('VENTANA DE RECUPERACIÓN:',duration/60,'minutos | CICLO NOMINAL:',LOOKBACK_MINUTES,'minutos\n'+'='*52)
    parameters={'format':'json','starttime':start.strftime('%Y-%m-%dT%H:%M:%S'), 'endtime':end.strftime('%Y-%m-%dT%H:%M:%S'), 'minmagnitude':MIN_MAG,'orderby':'time','limit':20000,'nodata':204}
    response=HTTP.get(EMSC_API,params=parameters,timeout=75)
    log('URL CONSULTADA:',response.url,'HTTP:',response.status_code)
    if response.status_code==204:
        log('EMSC: sin registros en la ventana consultada'); return []
    response.raise_for_status(); payload=response.json()
    if not isinstance(payload,dict): raise RuntimeError('Respuesta EMSC inválida')
    features=[payload] if payload.get('type')=='Feature' else payload.get('features',[])
    if not isinstance(features,list): raise RuntimeError('Respuesta sin lista de eventos')
    if len(features)>=20000: raise RuntimeError('Resultados EMSC truncados')
    counters={'received':len(features),'invalid':0,'outside_time':0,'outside_magnitude':0,'outside_radius':0,'duplicate':0,'accepted':0}
    accepted=[]; seen=set(); log('EVENTOS RECIBIDOS:',len(features))
    for number,feature in enumerate(features,1):
        try:
            props=feature.get('properties') or {}; geometry=feature.get('geometry') or {}; coords=geometry.get('coordinates') or []
            magnitude=float(props['mag']); latv=props.get('lat'); lonv=props.get('lon')
            if latv is None: latv=coords[1]
            if lonv is None: lonv=coords[0]
            latitude=float(latv); longitude=float(lonv); event_time=parse_time(props['time'])
            raw_id=str(props.get('unid') or feature.get('id') or props.get('source_id') or '').strip()
            if not raw_id: raise ValueError('Evento sin ID EMSC')
            unique_id='emsc_'+raw_id
            depth=None; depth_value=props.get('depth')
            if depth_value is not None:
                value=float(depth_value)
                if math.isfinite(value): depth=value
            place=str(props.get('flynn_region') or 'Región no especificada')
            if not all(math.isfinite(v) for v in (magnitude,latitude,longitude)): raise ValueError('Valor numérico no finito')
            if not (-90<=latitude<=90 and -180<=longitude<=180): raise ValueError('Coordenadas inválidas')
        except (KeyError,ValueError,TypeError,IndexError,AttributeError) as exc:
            counters['invalid']+=1; log('EVENTO INVÁLIDO:',number,short_error(exc)); continue
        distance,city=nearest_cities(latitude,longitude)[0]
        age_minutes=(end-event_time).total_seconds()/60
        within_time=start<=event_time<=end; within_magnitude=MIN_MAG<=magnitude<MAX_MAG; within_radius=distance<=RADIUS_KM
        log('-'*45,'\nID:',raw_id,'\nMAGNITUD:',magnitude,'\nHORA UTC:',iso(event_time),'\nANTIGÜEDAD:',round(age_minutes,1),'min\nCOORDENADAS:',latitude,longitude,'\nREGIÓN:',place)
        log('CIUDAD MÁS CERCANA:',city[0],city[1],'| DISTANCIA:',round(distance,1),'km | TIEMPO:',within_time,'| MAG:',within_magnitude,'| RADIO:',within_radius)
        if not within_time: counters['outside_time']+=1; log('DESCARTADO: tiempo'); continue
        if not within_magnitude: counters['outside_magnitude']+=1; log('DESCARTADO: magnitud'); continue
        if not within_radius: counters['outside_radius']+=1; log('DESCARTADO: distancia'); continue
        if unique_id in seen: counters['duplicate']+=1; log('DESCARTADO: duplicado'); continue
        seen.add(unique_id); matches=matching_cities(latitude,longitude); distance,city=matches[0]
        accepted.append({'id':unique_id,'emsc_id':raw_id,'source':'EMSC-CSEM','mag':magnitude,'magtype':str(props.get('magtype') or ''),'lat':latitude,'lon':longitude,'depth':depth,'place':place,'time':event_time,'city':city,'distance_km':distance,'url':EMSC_API+'?'+urllib.parse.urlencode({'format':'json','eventid':raw_id}), 'all_cities':[{'city':c[0],'country':c[1],'distance_km':round(d,1)} for d,c in matches]})
        counters['accepted']+=1; log('EVENTO ACEPTADO:',unique_id,'| EDAD:',round(age_minutes,1),'min')
    accepted.sort(key=lambda e:e['time']); log('RESUMEN DE FILTROS:',json.dumps(counters,ensure_ascii=False,indent=2))
    return accepted

def magnitude_colors(magnitude):
    if 4<=magnitude<5: return '#168A45','#FFFFFF','VERDE'
    if 5<=magnitude<6: return '#FFE100','#151515','AMARILLO'
    if 6<=magnitude<7: return '#FA8615','#151515','NARANJO'
    raise ValueError('Magnitud fuera del rango')
def get_font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'):
        if Path(path).exists(): return ImageFont.truetype(path,size)
    return ImageFont.load_default()
def draw_centered(draw,text,center_y,max_width,max_height,max_size,color):
    selected=get_font(20)
    for size in range(max_size,19,-2):
        ft=get_font(size); bb=draw.textbbox((0,0),text,font=ft)
        if bb[2]-bb[0]<=max_width and bb[3]-bb[1]<=max_height: selected=ft; break
    bb=draw.textbbox((0,0),text,font=selected)
    x=(WIDTH-(bb[2]-bb[0]))/2-bb[0]; y=center_y-(bb[3]-bb[1])/2-bb[1]
    draw.text((int(x),int(y)),text,font=selected,fill=color)
def mercator_xy(lat,lon):
    r=6378137.0; lat=min(85.,max(-85.,lat))
    return r*math.radians(lon),r*math.log(math.tan(math.pi/4+math.radians(lat)/2))
def map_bounds(event):
    x,y=mercator_xy(event['lat'],event['lon']); hw=MAP_HALF_WIDTH_M; hh=hw*HEIGHT/WIDTH
    return x-hw,y-hh,x+hw,y+hh
def pixel_location(lat,lon,bounds):
    xmin,ymin,xmax,ymax=bounds; x,y=mercator_xy(lat,lon)
    return int((x-xmin)/(xmax-xmin)*WIDTH),int((ymax-y)/(ymax-ymin)*HEIGHT)
def load_satellite(event):
    bounds=map_bounds(event)
    params={'bbox':','.join(map(str,bounds)),'bboxSR':3857,'imageSR':3857,'size':f'{WIDTH},{HEIGHT}','format':'jpg','f':'image','dpi':160}
    response=requests.get(SATELLITE_API,params=params,timeout=45,headers={'User-Agent':'Sismonauta-EMSC/5.1'})
    response.raise_for_status()
    if not response.headers.get('Content-Type','').lower().startswith('image/'): raise RuntimeError('No se recibió imagen satelital')
    with Image.open(io.BytesIO(response.content)) as src: image=src.convert('RGB')
    if image.size!=(WIDTH,HEIGHT): raise RuntimeError('Dimensiones satelitales inesperadas')
    return ImageEnhance.Color(ImageEnhance.Contrast(image).enhance(1.10)).enhance(1.13),bounds,'satellite'
def fallback_map(event):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cf
    bounds=map_bounds(event); projection=ccrs.epsg(3857)
    fig=plt.figure(figsize=(9,11.25),dpi=120,facecolor='#10273C'); ax=fig.add_axes([0,0,1,1],projection=projection)
    ax.set_extent(list(bounds),crs=projection); ax.set_facecolor('#16496D')
    for feat,kw in ((cf.OCEAN.with_scale('10m'),{'facecolor':'#16496D','zorder':0}), (cf.LAND.with_scale('10m'),{'facecolor':'#52714D','zorder':1}), (cf.COASTLINE.with_scale('10m'),{'edgecolor':'#E7EBE4','linewidth':1.2,'zorder':2}), (cf.BORDERS.with_scale('10m'),{'edgecolor':'#E4E8E1','linewidth':.8,'zorder':2})):
        ax.add_feature(feat,**kw)
    buffer=io.BytesIO(); fig.savefig(buffer,format='png',dpi=120,facecolor='#10273C'); plt.close(fig); buffer.seek(0)
    with Image.open(buffer) as src:
        image=src.convert('RGB')
        if image.size!=(WIDTH,HEIGHT): image=image.resize((WIDTH,HEIGHT),Image.Resampling.LANCZOS)
    return image,bounds,'fallback'
def draw_epicenter(draw,x,y):
    for r,color,width in ((95,'#FF4141',5),(65,'#FF8A8A',4),(36,'#FFFFFF',3)):
        draw.ellipse((x-r,y-r,x+r,y+r),outline=color,width=width)
    draw.ellipse((x-14,y-14,x+14,y+14),fill='#FF152D',outline='#FFFFFF',width=4)
def build_map(event):
    try: image,bounds,kind=load_satellite(event); log('MAPA: satelital')
    except Exception as exc:
        log('MAPA SATELITAL NO DISPONIBLE:',short_error(exc)); image,bounds,kind=fallback_map(event); log('MAPA: cartografía de respaldo')
    draw=ImageDraw.Draw(image); ex,ey=pixel_location(event['lat'],event['lon'],bounds); draw_epicenter(draw,ex,ey)
    city=event['city']; cx,cy=pixel_location(city[3],city[4],bounds)
    if 0<=cx<WIDTH and 0<=cy<HEIGHT:
        draw.ellipse((cx-8,cy-8,cx+8,cy+8),fill='#FFFFFF',outline='#121212',width=3)
        label=city[0].upper(); ft=get_font(30); bb=draw.textbbox((0,0),label,font=ft); lw=bb[2]-bb[0]
        tx=max(15,min(WIDTH-lw-15,cx+20)); ty=max(300,min(940,cy-12))
        draw.text((tx,ty),label,font=ft,fill='white',stroke_width=4,stroke_fill='#101820')
    return image,kind

def event_age_minutes(event,at=None):
    return ((at or now_utc())-event['time']).total_seconds()/60

def is_recent_event(event,at=None):
    age=((at or now_utc())-event['time']).total_seconds()/60
    return 0<=age<=LOOKBACK_MINUTES

def make_image(event,path):
    image,kind=build_map(event); draw=ImageDraw.Draw(image); city=event['city']
    banner,foreground,category=magnitude_colors(event['mag'])
    draw.rounded_rectangle((22,24,1058,268),radius=25,fill=banner,outline='#FFFFFF',width=4)
    draw_centered(draw,'ALERTA' if is_recent_event(event) else 'SISMO DETECTADO',90,940,94,99 if is_recent_event(event) else 76,foreground)
    draw.line((83,148,997,148),fill=foreground,width=3)
    if is_recent_event(event):
        draw_centered(draw,'ÚLTIMO MINUTO',207,965,85,74,foreground)
    draw.rounded_rectangle((18,1000,1062,1328),radius=25,fill=banner,outline='#FFFFFF',width=4)
    draw_centered(draw,f"MAGNITUD {event['mag']:.1f}",1057,985,76,64,foreground)
    line_two=('SE HA PERCIBIDO EN ' if event.get('felt_confirmed',False) else 'CERCA DE ')+city[0].upper()
    draw_centered(draw,line_two,1160,985,72,54,foreground)
    draw_centered(draw,'¿LO SENTISTE?',1262,970,75,67,foreground)
    attribution='Imagen: Esri / proveedores' if kind=='satellite' else 'Mapa: Natural Earth'
    draw.rounded_rectangle((15,965,410,995),radius=5,fill='#102030'); draw.text((24,968),attribution,font=get_font(17),fill='white')
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); image.save(path,'JPEG',quality=95,optimize=True)
    log('IMAGEN CREADA:',path,'| BANNER:',category,'| MAPA:',kind,'| OPORTUNIDAD:', 'reciente' if is_recent_event(event) else 'recuperado')

def make_caption(event):
    city=event['city']; local=event['time'].astimezone(ZoneInfo(city[5])); age=(now_utc()-event['time']).total_seconds()/60
    description=(f"Un sismo de magnitud {event['mag']:.1f} se ha percibido en las cercanías de {city[0]}, {city[1]}." if event.get('felt_confirmed',False) else f"Un sismo de magnitud {event['mag']:.1f} se ha registrado en las cercanías de {city[0]}, {city[1]}.")
    if 0<=age<=10: description+=' Hace instantes.'
    city_tag=re.sub(r'[^A-Za-z]','',city[0]); country_tag=re.sub(r'[^A-Za-z]','',city[1])
    heading='🚨 ¡ÚLTIMO MINUTO!' if 0<=age<=10 else '📍 SISMO DETECTADO'
    caption=(f"{heading} | SISMO DE MAGNITUD {event['mag']:.1f} CERCA DE {city[0].upper()}, {city[1].upper()} {city[2]}\n\n"
             f"{description}\n\n🕒 Hora de origen: {local:%d/%m/%Y %H:%M:%S} ({city[5]}).\n"
             f"📏 Distancia aproximada a {city[0]}: {event['distance_km']:.1f} km.\n"
             f"📍 Profundidad: {depth_text(event.get('depth'))}. Fuente: EMSC-CSEM.\n"
             '💬 ¿Sentiste el movimiento? ¡Coméntanos!\n\n'
             '📲 Sigue a @sismonauta para conocer los últimos registros sísmicos.\n\n'
             f'#SISMONAUTA #Sismo #Terremoto #{country_tag} #{city_tag} #EMSC #Sismologia')
    return caption[:2200]

class Github:
    def __init__(self):
        self.repo=os.environ['GITHUB_REPOSITORY']; self.branch=os.getenv('GITHUB_REF_NAME','main'); self.base='https://api.github.com/repos/'+self.repo
        self.session=requests.Session(); self.session.headers.update({'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        r=self.session.get(self.base,timeout=30); r.raise_for_status()
        if r.json().get('private'): raise RuntimeError('Se necesita un repositorio público para servir imágenes a Meta')
    def endpoint(self,path): return self.base+'/contents/'+urllib.parse.quote(path,safe='/')
    def read(self,path):
        r=self.session.get(self.endpoint(path),params={'ref':self.branch},timeout=30)
        if r.status_code==404: return None
        r.raise_for_status(); return json.loads(base64.b64decode(r.json()['content']).decode('utf-8'))
    def write_bytes(self,path,content):
        endpoint=self.endpoint(path)
        for attempt in range(5):
            previous=self.session.get(endpoint,params={'ref':self.branch},timeout=30)
            if previous.status_code==404: sha=None
            else: previous.raise_for_status(); sha=previous.json()['sha']
            payload={'message':'SISMONAUTA EMSC: '+path,'branch':self.branch,'content':base64.b64encode(content).decode('ascii')}
            if sha: payload['sha']=sha
            r=self.session.put(endpoint,json=payload,timeout=60)
            if r.ok: return
            if r.status_code in (409,422) and attempt<4: time.sleep(2+attempt); continue
            r.raise_for_status()
    def write_json(self,path,data): self.write_bytes(path,json.dumps(data,ensure_ascii=False,indent=2,default=str).encode('utf-8'))
    def public_url(self,path): return 'https://raw.githubusercontent.com/'+self.repo+'/'+urllib.parse.quote(self.branch,safe='')+'/'+urllib.parse.quote(path,safe='/')

def graph(method,path,token,parameters=None,base=None):
    endpoint=f"{base or IG_BASE}/{GRAPH_VERSION}/{path}"; values=dict(parameters or {}); values['access_token']=token
    response=requests.get(endpoint,params=values,timeout=60) if method=='GET' else requests.post(endpoint,data=values,timeout=80)
    try: data=response.json()
    except ValueError: raise RuntimeError('Meta no devolvió JSON')
    if not response.ok or data.get('error'):
        err=data.get('error') or {}; raise RuntimeError('Meta API: '+str(err.get('message',data))[:250])
    return data

def validate_accounts():
    ig_token=os.environ['INSTAGRAM_TOKEN']; ig_id=os.environ['INSTAGRAM_USER_ID']; fb_token=os.environ['FACEBOOK_PAGE_TOKEN']; fb_id=os.environ['FACEBOOK_PAGE_ID']
    instagram=graph('GET','me',ig_token,{'fields':'id,username'})
    if str(instagram.get('id'))!=str(ig_id) or str(instagram.get('username','')).lower()!='sismonauta': raise RuntimeError('La cuenta Instagram no es @sismonauta')
    facebook=graph('GET',fb_id,fb_token,{'fields':'id,name'},FB_BASE)
    if str(facebook.get('id'))!=str(fb_id) or 'sismonauta' not in str(facebook.get('name','')).lower(): raise RuntimeError('La fanpage no es SISMONAUTA')
    log('INSTAGRAM:',instagram.get('username'),'FACEBOOK:',facebook.get('name'))
    return ig_id,ig_token,fb_id,fb_token

def publish_instagram(user_id,token,image_url,caption):
    container=graph('POST',f'{user_id}/media',token,{'image_url':image_url,'caption':caption}); cid=container.get('id')
    if not cid: raise RuntimeError('Instagram no creó contenedor')
    finished=False
    for _ in range(25):
        time.sleep(5); result=graph('GET',cid,token,{'fields':'status_code'}); status=result.get('status_code')
        if status=='FINISHED': finished=True; break
        if status in ('ERROR','EXPIRED'): raise RuntimeError('Contenedor Instagram: '+str(status))
    if not finished: raise RuntimeError('Instagram no terminó de procesar la imagen')
    published=graph('POST',f'{user_id}/media_publish',token,{'creation_id':cid}); identifier=published.get('id')
    if not identifier: raise RuntimeError('Instagram no confirmó el post')
    return str(identifier)
def publish_facebook(page_id,token,image_url,caption):
    result=graph('POST',f'{page_id}/photos',token,{'url':image_url,'caption':caption,'published':'true'},FB_BASE)
    identifier=result.get('post_id') or result.get('id')
    if not identifier: raise RuntimeError('Facebook no confirmó la foto')
    return str(identifier)
def wait_public_image(url):
    for _ in range(12):
        try:
            r=HTTP.get(url,timeout=25)
            if r.ok and r.headers.get('Content-Type','').lower().startswith('image/'): return
        except requests.RequestException: pass
        time.sleep(5)
    raise RuntimeError('Imagen pública no disponible')

def new_state(): return {'events':{}}
def ensure_entry(state,event):
    entry=state['events'].setdefault(event['id'],{'magnitude':event['mag'],'place':event['place'],'time':iso(event['time']),'source':'EMSC-CSEM','targets':{}})
    for platform in PLATFORMS: entry.setdefault('targets',{}).setdefault(platform,{'status':'pending'})
    return entry
def is_finished(entry): return all(entry.get('targets',{}).get(p,{}).get('status')=='published' for p in PLATFORMS)
def has_uncertain(entry): return any(entry.get('targets',{}).get(p,{}).get('status')=='uncertain' for p in PLATFORMS)

def process_event(event,github,state,publish,credentials):
    event_id=event['id']; entry=ensure_entry(state,event)
    if is_finished(entry): log('YA PUBLICADO:',event_id); return 'already_published'
    if has_uncertain(entry): log('REVISIÓN MANUAL:',event_id); return 'partial_or_uncertain'
    filename=safe_name(event_id)+'.jpg'; image_path=OUT/filename; make_image(event,image_path); caption=make_caption(event)
    save_json(OUT/(safe_name(event_id)+'.json'),{'event':event,'caption':caption})
    if not publish: log('VISTA PREVIA:',image_path); return 'preview_only'
    remote_path=IMAGE_DIR+'/'+filename; github.write_json(STATE_PATH,state); github.write_bytes(remote_path,image_path.read_bytes())
    image_url=github.public_url(remote_path); wait_public_image(image_url); entry['image_url']=image_url; github.write_json(STATE_PATH,state)
    ig_id,ig_token,fb_id,fb_token=credentials
    for platform in PLATFORMS:
        target=entry['targets'][platform]
        if target.get('status')=='published': continue
        # El estado incierto se escribe ANTES del envío para evitar duplicados.
        target['status']='uncertain'; target.pop('error',None); github.write_json(STATE_PATH,state)
        try:
            identifier=(publish_instagram(ig_id,ig_token,image_url,caption) if platform=='instagram' else publish_facebook(fb_id,fb_token,image_url,caption))
            target['status']='published'; target['id']=identifier; github.write_json(STATE_PATH,state)
            log('PUBLICADO:',event_id,platform,identifier)
        except Exception as exc:
            target['error']=short_error(exc); github.write_json(STATE_PATH,state); log('ERROR META:',platform,short_error(exc))
    return 'published' if is_finished(entry) else 'partial_or_uncertain'

def test_recovery():
    assert len(CITIES)==15 and EMSC_RECOVERY_MINUTES>=10
    iquique=next(c for c in CITIES if c[0]=='Iquique')
    d=haversine(-20.1237,-69.9368,iquique[3],iquique[4])
    assert d<100,(d,iquique)
    e={'time':datetime(2026,10,30,12,57,48,tzinfo=UTC)}
    assert is_recent_event(e,datetime(2026,10,30,13,4,tzinfo=UTC))
    assert not is_recent_event(e,datetime(2026,10,30,13,15,tzinfo=UTC))
    assert is_finished({'targets':{'instagram':{'status':'published'},'facebook':{'status':'published'}}})
    assert has_uncertain({'targets':{'instagram':{'status':'uncertain'}}})
    log('PRUEBAS APROBADAS: ciudades, distancia Iquique, oportunidad y antiduplicados; ventana recuperación:',EMSC_RECOVERY_MINUTES)

def main():
    parser=argparse.ArgumentParser(description='SISMONAUTA EMSC - consulta retrospectiva con recuperación')
    parser.add_argument('--publish',action='store_true'); parser.add_argument('--out',default='output_ciudades')
    parser.add_argument('--lookback-minutes',type=int,choices=[10],default=10,help='Ciclo nominal; la consulta recupera 90 min por defecto')
    parser.add_argument('--test',action='store_true',help='Pruebas locales sin consultar EMSC ni publicar')
    args=parser.parse_args()
    if args.test: test_recovery(); return 0
    global OUT; OUT=Path(args.out); OUT.mkdir(parents=True,exist_ok=True)
    receipt_path=OUT/'estado_publicacion.json'
    receipt={'status':'starting','source':'EMSC-CSEM','checked_at':iso(now_utc()),'publish_requested':args.publish,'lookback_hours':0,'lookback_minutes':LOOKBACK_MINUTES,'recovery_minutes':EMSC_RECOVERY_MINUTES,'radius_km':RADIUS_KM,'min_magnitude':MIN_MAG,'max_magnitude_exclusive':MAX_MAG,'events':[]}
    try:
        log('='*52,'\nSISMONAUTA - EMSC-CSEM\nCIUDADES:',len(CITIES),'\nCICLO: 10 MINUTOS\nRECUPERACIÓN:',EMSC_RECOVERY_MINUTES,'MINUTOS\nRADIO:',RADIUS_KM,'KM\nMAGNITUDES: M4.0 A M6.9\nPUBLICAR:',args.publish,'\n'+'='*52)
        github=Github() if args.publish else None; state=(github.read(STATE_PATH) if github else None) or new_state(); state.setdefault('events',{})
        events=fetch_events(); pending=[e for e in events if not is_finished(state['events'].get(e['id'],{}))]
        log('EVENTOS COINCIDENTES:',len(events),'EVENTOS PENDIENTES:',len(pending))
        if not pending:
            receipt['status']='no_new_event'; save_json(receipt_path,receipt); log('SIN NUEVOS EVENTOS'); return 0
        pending=pending[:MAX_EVENTS_PER_RUN]; credentials=validate_accounts() if args.publish else None; statuses=[]
        for event in pending:
            log('PROCESANDO:',event['id'],f"M{event['mag']:.1f}",event['city'][0])
            try: result=process_event(event,github,state,args.publish,credentials)
            except Exception as exc: result='partial_or_uncertain'; log('ERROR DE EVENTO:',event['id'],short_error(exc))
            entry=ensure_entry(state,event); statuses.append(result)
            receipt['events'].append({'id':event['id'],'magnitude':event['mag'],'city':event['city'][0],'country':event['city'][1],'distance_km':round(event['distance_km'],1),'status':result,'targets':entry['targets']}); save_json(receipt_path,receipt)
        if all(v=='preview_only' for v in statuses): receipt['status']='preview_only'
        elif any(v=='partial_or_uncertain' for v in statuses): receipt['status']='partial_or_uncertain'
        elif all(v in ('published','already_published') for v in statuses): receipt['status']='published'
        else: receipt['status']='error'
        save_json(receipt_path,receipt); log('ESTADO FINAL:',receipt['status'])
        return 0 if receipt['status'] in ('published','preview_only','no_new_event') else 1
    except Exception as exc:
        receipt['status']='error'; receipt['error']=short_error(exc); save_json(receipt_path,receipt); log('ERROR GENERAL:',short_error(exc)); return 1

if __name__=='__main__': sys.exit(main())
