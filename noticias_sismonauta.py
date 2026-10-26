#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA | Un solo archivo Python para noticias sísmicas (no reportes de sismos).
Genera una imagen 1080x1350 con titular y publica imagen en Instagram y Facebook.
Instalación: pip install requests pillow
Variables (GitHub secrets): INSTAGRAM_TOKEN, INSTAGRAM_USER_ID, FACEBOOK_PAGE_TOKEN,
FACEBOOK_PAGE_ID, GH_TOKEN, GITHUB_REPOSITORY; branch GITHUB_REF_NAME.
Seguridad: exige una imagen CC0/PD de Wikimedia Commons; nunca toma sin permiso
la imagen con copyright de un artículo. Si no puede verificar fuente/foto, no publica.
"""
from __future__ import annotations
import argparse, base64, hashlib, html, io, json, os, re, sys, time, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

UA='SismonautaNews/1.0 (educational newsroom; contact via Instagram @sismonauta)'
HTTP=requests.Session();HTTP.headers.update({'User-Agent':UA})
VERSION=os.getenv('IG_GRAPH_VERSION','v25.0')
IG_BASE=os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
FB_BASE='https://graph.facebook.com'
STATE_FILE='sismonauta_noticias/state.json'
SOURCES=[
 ('es','terremotos investigación científica tecnología -sismo hoy -temblor hoy -magnitud when:1d'),
 ('es','sismología estudio descubrimiento sensores alerta temprana when:1d'),
 ('es','tsunami investigación científica nueva tecnología when:1d'),
 ('en','earthquake research study technology discovery seismic sensors when:1d'),
 ('en','seismology new research early warning monitoring innovation when:1d'),
 ('en','tsunami research new study technology warning when:1d'),
]
SCIENCE=re.compile(r'(?i)\b(sism|sismic|sísmic|seismic|seismolog|earthquake|terremot|tsunami|tectoni|tectóni|fault|falla|geof[ií]sic|geophysic)')
ADVANCE=re.compile(r'(?i)\b(estudio|investigaci[oó]n|investigador|cient[ií]fic|descubrim|hallazgo|avance|tecnolog|sensor|sat[eé]lit|alerta|sistema|desarroll|m[eé]todo|inteligencia artificial|modelo|research|study|scientist|discovery|breakthrough|technology|sensor|satellite|warning|innovation|develop|model|detect|predict|monitor|mapping|ai\b)')
EVENT=re.compile(r'(?i)\b(sismo de magnitud|temblor de magnitud|terremoto de magnitud|earthquake of magnitude|magnitude [0-9]|m[ .]?[4-9][.,][0-9]|sismo hoy|earthquake strikes|earthquake hits|earthquake struck|temblor hoy|sismo se registr[oó]|earthquake reported|earthquake shakes|terremoto sacude|reportan sismo)')
SPAM=re.compile(r'(?i)\b(horoscope|hor[oó]scopo|astrolog|prediction for today|conspiracy|viral video|tiktok|sensational|apocalipsis)')
STOP=set('a al algo ante con contra de del desde el en entre es esta este estas estos fue han hoy la las lo los más para por que se sin sobre su sus un una unos unas y the of in on to for with from as new how what why at by and scientists study research earthquake seismic tsunami can could may have has reveals reveal'.split())

def clean(t):
 t=html.unescape(re.sub('<[^>]*>',' ',t or ''))
 return re.sub(r'\s+',' ',t).strip()

def get(url,**kw):
 r=HTTP.get(url,timeout=kw.pop('timeout',25),**kw);r.raise_for_status();return r

def feed_items():
 now=datetime.now(timezone.utc); out={}
 for lang,q in SOURCES:
  region=('es-CL','CL','CL:es-419') if lang=='es' else ('en-US','US','US:en')
  url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':q,'hl':region[0],'gl':region[1],'ceid':region[2]})
  try: root=ET.fromstring(get(url).content)
  except Exception as e:
   print('RSS no disponible:',lang,type(e).__name__,str(e)[:110],flush=True);continue
  for node in root.findall('.//item'):
   title=clean(node.findtext('title')); desc=clean(node.findtext('description')); link=(node.findtext('link') or '').strip()
   pub=node.findtext('pubDate') or ''
   try: dt=parsedate_to_datetime(pub).astimezone(timezone.utc)
   except Exception: continue
   if not now-timedelta(hours=24)<=dt<=now+timedelta(minutes=5):continue
   provider=clean(node.findtext('source') or '')
   if ' - ' in title:
    prefix,suffix=title.rsplit(' - ',1)
    if not provider or suffix.lower()==provider.lower():title=prefix.strip()
   content=title+' '+desc
   if not SCIENCE.search(content) or not ADVANCE.search(content) or EVENT.search(title) or SPAM.search(content):continue
   key=hashlib.sha256(re.sub(r'\W+','',title.lower()).encode()).hexdigest()[:20]
   out[key]={'id':key,'title':title,'description':desc,'url':link,'source':provider or urllib.parse.urlparse(link).netloc,'date':dt.isoformat(),'lang':lang}
 return list(out.values())

def priority(a):
 t=(a['title']+' '+a['description']).lower()
 pts=0
 for needle,weight in [('research',4),('investigación',4),('estudio',3),('study',3),('scientists',3),('científic',3),('discovery',4),('descubri',4),('technology',4),('tecnolog',4),('early warning',4),('alerta temprana',4),('sensor',3),('satellite',3),('satélite',3),('monitor',2),('seismic',2),('sísmic',2)]:
  if needle in t:pts+=weight
 recency=(datetime.fromisoformat(a['date'])-(datetime.now(timezone.utc)-timedelta(hours=24))).total_seconds()/3600
 return pts+max(0,recency)*0.12

def tokens(s):
 return [w for w in re.findall(r'[a-záéíóúñü]{4,}',s.lower()) if w not in STOP and not w.endswith('news')][:12]

def commons_photo(article):
 """Conservative: CC0/public domain on Wikimedia, meaningful metadata relevance."""
 title=article['title']; tks=tokens(title)
 core=[w for w in tks if SCIENCE.search(w)]
 queries=[]
 for q in [ ' '.join(tks[:4]), ' '.join((core+tks)[:4]), 'seismology research', 'earthquake fault landscape' ]:
  if q and q not in queries:queries.append(q)
 scored=[]
 for q in queries[:3]:
  try:
   data=get('https://commons.wikimedia.org/w/api.php',params={
    'action':'query','generator':'search','gsrsearch':q+' filetype:bitmap','gsrnamespace':6,'gsrlimit':12,
    'prop':'imageinfo','iiprop':'url|extmetadata|mime','iiurlwidth':1500,'format':'json'},timeout=22).json()
  except Exception as e:
   print('Commons error:',type(e).__name__,str(e)[:80]);continue
  for p in (data.get('query') or {}).get('pages',{}).values():
   info=(p.get('imageinfo') or [{}])[0]; meta=info.get('extmetadata') or {}
   l=(clean(meta.get('LicenseShortName',{}).get('value',''))+' '+clean(meta.get('License',{}).get('value',''))).lower()
   usage=clean(meta.get('UsageTerms',{}).get('value','')).lower()
   if not (re.search(r'\bcc0\b|public domain|publicdomain|pdm',l) or 'public domain' in usage):continue
   name=p.get('title','');desc=clean(meta.get('ImageDescription',{}).get('value',''))
   txt=(name+' '+desc).lower()
   if re.search(r'(?i)\b(moon|lunar|mars|planet|logo|poster|screenshot|diagram|chart|map|drawing|illustration|portrait|people|person|crowd|tourist|lroc|spacecraft|render|ai.generated)\b',txt):continue
   if not SCIENCE.search(txt):continue
   match=sum(1 for w in set(tks[:8]) if w in txt)
   if match<1:continue
   imageurl=info.get('thumburl') or info.get('url')
   if not imageurl or not info.get('mime','').startswith('image/'):continue
   scored.append((match+int(core and any(w in txt for w in core))*3,name,imageurl,desc,l))
  if scored:break
 for _,name,url,desc,lic in sorted(scored,reverse=True):
  try:
   r=get(url,timeout=35)
   if len(r.content)>15_000_000:continue
   im=Image.open(io.BytesIO(r.content)).convert('RGB');w,h=im.size
   if w<600 or h<450:continue
   return im,{'title':name,'url':url,'license':lic,'description':desc[:200],'illustrative':True}
  except Exception:continue
 return None,None

def font(size,bold=False):
 for s in (['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'] if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']):
  if Path(s).exists():return ImageFont.truetype(s,size)
 return ImageFont.load_default()

def wrap_text(draw,txt,ft,width,maxlines=5):
 words=txt.split();lines=[];current=''
 for word in words:
  test=(current+' '+word).strip()
  if draw.textbbox((0,0),test,font=ft)[2]<=width:current=test
  else:
   if current:lines.append(current)
   current=word
 if current:lines.append(current)
 if len(lines)>maxlines:return None
 return lines

def make_card(image,article,path):
 """Foto principal con banda azul y titular completo blanco de borde negro."""
 W,H=1080,1350
 canvas=ImageOps.fit(image,(W,H),Image.Resampling.LANCZOS,centering=(.5,.44)).convert('RGBA')
 overlay=Image.new('RGBA',(W,H),(0,0,0,0))
 d=ImageDraw.Draw(overlay)
 # Identidad visual discreta en la cabecera.
 d.rectangle((0,0,W,100),fill=(4,21,46,235))
 d.text((54,27),'SISMONAUTA  |  ACTUALIDAD',font=font(37,True),fill='white')
 # Franja azul sólida, de alto adaptable al titular; sin puntos suspensivos.
 title=clean(article['title'])
 if not title:raise ValueError('La noticia no tiene titular')
 inset=48;max_w=W-2*inset-18
 selected=None;lines=None;line_h=None;band_h=None
 for size in range(70,23,-2):
  ft=font(size,True)
  candidate=wrap_text(d,title,ft,max_w,12)
  if not candidate:continue
  leading=int(size*1.27)
  height=max(210,len(candidate)*leading+100)
  if height<=690:
   selected=ft;lines=candidate;line_h=leading;band_h=height;break
 if lines is None:
  raise ValueError('Titular demasiado largo para mostrarse completo de forma legible')
 bottom=H-94;top=bottom-band_h
 d.rectangle((0,top,W,bottom),fill=(0,87,202,248))
 d.rectangle((0,top,W,top+9),fill=(36,163,255,255))
 total=len(lines)*line_h
 y=top+(band_h-total)//2
 for line in lines:
  bbox=d.textbbox((0,0),line,font=selected,stroke_width=3)
  text_width=bbox[2]-bbox[0]
  x=(W-text_width)//2
  d.text((x,y),line,font=selected,fill=(255,255,255,255),stroke_width=3,stroke_fill=(0,0,0,255))
  y+=line_h
 d.rectangle((0,H-94,W,H),fill=(3,24,49,245))
 d.text((54,H-67),'CIENCIA  •  SISMOS  •  TECNOLOGÍA',font=font(28,True),fill='white')
 canvas=Image.alpha_composite(canvas,overlay).convert('RGB')
 path.parent.mkdir(parents=True,exist_ok=True)
 canvas.save(path,'JPEG',quality=91,optimize=True)

def caption(a,credit):
 title=a['title'];description=a['description']
 description=re.sub(r'\s*-\s*'+re.escape(a['source'])+r'\s*$','',description,flags=re.I)
 # RSS description can duplicate headlines and contain only article link; avoid inventing claims.
 desc=description if len(description)>40 and description.lower()!=title.lower() else ''
 desc=desc[:400].rsplit(' ',1)[0] if len(desc)>400 else desc
 parts=['🌎 ACTUALIDAD SÍSMICA | SISMONAUTA','',title]
 if desc and not desc.lower().startswith(title.lower()[:30]):parts+=['',desc]
 parts+=['',f'🗞️ Fuente: {a["source"]}',f'🔗 Noticia: {a["url"]}',f'🕒 Publicada: {datetime.fromisoformat(a["date"]).astimezone(ZoneInfo("America/Santiago")).strftime("%d-%m-%Y %H:%M")} (Chile)']
 parts+=['','📷 Imagen ilustrativa con licencia '+credit['license']+' — Wikimedia Commons: '+credit['title'], '', '#SISMONAUTA #Sismologia #Ciencia #Terremotos #Tecnologia #Investigacion #Geologia #Tectonica']
 return '\n'.join(parts)[:2100]

class Github:
 def __init__(self):
  repo=os.environ['GITHUB_REPOSITORY']; self.branch=os.getenv('GITHUB_REF_NAME') or 'main'
  self.base='https://api.github.com/repos/'+repo
  self.s=requests.Session();self.s.headers.update({'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
  r=self.s.get(self.base,timeout=20);r.raise_for_status()
  if r.json().get('private'):raise RuntimeError('El repositorio debe ser público para servir imágenes a Instagram')
 def read(self,path):
  r=self.s.get(self.base+'/contents/'+urllib.parse.quote(path,safe='/'),params={'ref':self.branch},timeout=25)
  if r.status_code==404:return None,None
  r.raise_for_status();v=r.json();return base64.b64decode(v['content']),v['sha']
 def write(self,path,raw):
  for attempt in range(3):
   old,sha=self.read(path)
   body={'message':'SISMONAUTA noticias: '+path,'branch':self.branch,'content':base64.b64encode(raw).decode()}
   if sha:body['sha']=sha
   r=self.s.put(self.base+'/contents/'+urllib.parse.quote(path,safe='/'),json=body,timeout=50)
   if r.status_code in (409,422) and attempt<2:time.sleep(2);continue
   r.raise_for_status();return
 def image_url(self,path):
  # Raw GitHub immutable caching is avoided by unique file names per selected story.
  repo=os.environ['GITHUB_REPOSITORY']
  return 'https://raw.githubusercontent.com/'+repo+'/'+urllib.parse.quote(self.branch,safe='')+'/'+urllib.parse.quote(path,safe='/')

def graph(method,path,token,params=None,base=IG_BASE):
 u=f'{base}/{VERSION}/{path}';payload=dict(params or {});payload['access_token']=token
 r=requests.get(u,params=payload,timeout=55) if method=='GET' else requests.post(u,data=payload,timeout=70)
 try:j=r.json()
 except Exception:raise RuntimeError(f'Meta devolvió HTTP {r.status_code} no JSON')
 if not r.ok or j.get('error'):
  e=j.get('error',{});raise RuntimeError(f'Meta HTTP {r.status_code}: {str(e.get("message",j))[:230]}')
 return j

def ig_check():
 tok=os.environ['INSTAGRAM_TOKEN'];uid=os.environ['INSTAGRAM_USER_ID'];info=graph('GET','me',tok,{'fields':'id,username'})
 if str(info.get('id'))!=uid or info.get('username','').lower()!='sismonauta':raise RuntimeError('Token Instagram no corresponde a @sismonauta')
 print('Instagram @sismonauta verificado');return uid,tok

def fb_check():
 page=os.environ['FACEBOOK_PAGE_ID'];tok=os.environ['FACEBOOK_PAGE_TOKEN'];info=graph('GET',page,tok,{'fields':'id,name,username'},FB_BASE)
 if str(info.get('id'))!=page:raise RuntimeError('Facebook Page ID incorrecto')
 if info.get('username') and info['username'].lower()!='sismonauta':raise RuntimeError('Fanpage incorrecta')
 print('Fanpage validada:',info.get('name'));return page,tok

def publish_ig(uid,tok,url,txt):
 c=graph('POST',uid+'/media',tok,{'image_url':url,'caption':txt})['id']
 for _ in range(18):
  time.sleep(8);s=graph('GET',c,tok,{'fields':'status_code'})['status_code']
  if s=='FINISHED':break
  if s in ('ERROR','EXPIRED'):raise RuntimeError('Instagram no procesó la imagen: '+s)
 else:raise RuntimeError('Estado incierto: contenedor Instagram sin confirmar')
 return graph('POST',uid+'/media_publish',tok,{'creation_id':c})['id']

def publish_fb(page,tok,url,txt):
 # Photo post on Page feed, not a Story/Reel. published=true is default.
 result=graph('POST',page+'/photos',tok,{'url':url,'caption':txt,'published':'true'},FB_BASE)
 return result.get('post_id') or result.get('id')

def load_state(gh):
 raw,_=gh.read(STATE_FILE)
 return json.loads(raw) if raw else {'stories':{}}

def save_state(gh,state):
 gh.write(STATE_FILE,json.dumps(state,ensure_ascii=False,indent=2,sort_keys=True).encode())

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--publish',action='store_true');parser.add_argument('--out',default='output_noticias');args=parser.parse_args()
 out=Path(args.out);out.mkdir(exist_ok=True,parents=True)
 gh=Github() if args.publish else None
 state=load_state(gh) if gh else {'stories':{}}
 articles=feed_items();articles=[a for a in articles if a['lang']=='es'];articles.sort(key=priority,reverse=True)
 print('Noticias EN ESPAÑOL candidatas últimas 24 h:',len(articles),flush=True)
 if not articles:
  print('SIN NOTICIAS verificables del período. No se publica.',flush=True)
  (out/'estado_publicacion.json').write_text(json.dumps({'status':'no_eligible_news','published':False},ensure_ascii=False),encoding='utf8')
  return 2 if args.publish else 0
 chosen=None;img=None;credit=None
 for a in articles[:25]:
  if a['id'] in state['stories']:continue
  print('Evaluando:',a['title'][:110],flush=True)
  photo,meta=commons_photo(a)
  if photo is not None:chosen,img,credit=a,photo,meta;break
 if not chosen:
  print('No existe noticia inédita con fotografía reutilizable y pertinente. No se publica.',flush=True)
  (out/'estado_publicacion.json').write_text(json.dumps({'status':'no_licensed_relevant_photo','published':False,'articles_reviewed':min(len(articles),25)},ensure_ascii=False),encoding='utf8')
  return 3 if args.publish else 0
 file=out/(chosen['id']+'.jpg');make_card(img,chosen,file)
 description=caption(chosen,credit)
 (out/'noticia.json').write_text(json.dumps({'article':chosen,'image':credit,'caption':description},ensure_ascii=False,indent=2),encoding='utf8')
 print('SELECCIONADA:',chosen['title'],'|',chosen['source'],'|',chosen['date'],flush=True)
 print('FOTO:',credit['title'],'|',credit['license'],flush=True)
 if not args.publish:
  print('MODO PRUEBA: imagen creada, sin publicación',file,flush=True)
  (out/'estado_publicacion.json').write_text(json.dumps({'status':'preview_only','published':False,'image':str(file)},ensure_ascii=False),encoding='utf8')
  return 0
 uid,igt=ig_check();page,ft=fb_check()
 remote=f'sismonauta_noticias/images/{chosen["id"]}.jpg'
 gh.write(remote,file.read_bytes());url=gh.image_url(remote)
 # Confirm image URL is publicly accessible to Meta before any POST.
 r=HTTP.get(url,timeout=25)
 if not r.ok or not r.headers.get('Content-Type','').startswith('image/'):
  raise RuntimeError('La imagen no es pública y legible: '+str(r.status_code))
 state['stories'][chosen['id']]={'article':chosen,'image':credit,'targets':{'instagram':{'status':'pending'},'facebook':{'status':'pending'}}}
 save_state(gh,state)
 failures=[]
 for target in ('instagram','facebook'):
  entry=state['stories'][chosen['id']]['targets'][target]
  entry['status']='uncertain';save_state(gh,state) # Nunca hacer retry ciego si Meta acepta pero falla la respuesta.
  try:
   identifier=publish_ig(uid,igt,url,description) if target=='instagram' else publish_fb(page,ft,url,description)
   if not identifier:raise RuntimeError('Meta no devolvió ID de publicación')
   entry.update(status='published',id=str(identifier));save_state(gh,state)
   print('PUBLICADO',target,'ID:',identifier,flush=True)
  except Exception as exc:
   failures.append(target+': '+str(exc));print('ERROR',target,str(exc)[:250],flush=True)
 receipt={'status':'published' if not failures else 'partial_or_uncertain','article_id':chosen['id'],'article_title':chosen['title'],'targets':state['stories'][chosen['id']]['targets']}
 (out/'estado_publicacion.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
 if failures:raise RuntimeError('Publicación parcial o incierta. Revisar antes de reintentar: '+' / '.join(failures))
 print('PUBLICACIÓN VERIFICADA: Instagram y Facebook',flush=True)
 return 0

if __name__=='__main__':
 try:sys.exit(main())
 except Exception as e:print('ERROR:',e,file=sys.stderr,flush=True);sys.exit(1)
