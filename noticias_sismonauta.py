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
from html.parser import HTMLParser
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
# Consultas cortas: Google News puede descartar búsquedas con demasiados términos AND.
# Todas conservan el límite temporal estricto de 24 horas.
SOURCES=[
 ('es','sismología investigación when:1d'),
 ('es','terremotos estudio científico when:1d'),
 ('es','tsunami investigación when:1d'),
 ('es','alerta sísmica tecnología when:1d'),
 ('es','fallas tectónicas descubrimiento when:1d'),
 ('es','ondas sísmicas científicos when:1d'),
 ('es','geofísica terremotos investigación when:1d'),
 ('es','sensor sísmico innovación when:1d'),
 ('es','satélite terremotos estudio when:1d'),
 ('en','seismology research when:1d'),
 ('en','earthquake technology when:1d'),
 ('en','tsunami research when:1d'),
 ('en','seismic early warning technology when:1d'),
 ('en','geophysics fault scientific discovery when:1d'),
 ('en','earthquake sensors scientific study when:1d'),
 ('en','tectonic plates new research when:1d'),
 ('en','earthquake satellite monitoring research when:1d'),
 ('es','nueva investigación terremotos when:1d'),
 ('es','estudio científico fallas geológicas when:1d'),
 ('en','seismic imaging research when:1d'),
 ('en','geological survey fault mapping technology when:1d'),
 ('en','earthquake risk researchers new findings when:1d'),
 ('en','earthquake early warning scientists when:1d'),
 ('en','tsunami detection ocean research when:1d'),
 ('es','investigadores placas tectónicas avance when:1d'),
 ('es','nuevo estudio sismología when:1d'),
]

SCIENCE=re.compile(r'(?i)\b(sism\w*|sísm\w*|seism\w*|earthquak\w*|terremot\w*|tsunami\w*|tect[oó]nic\w*|tectoni\w*|fault\w*|fallas?\b|geof[ií]sic\w*|geophysic\w*|subduction|subducci[oó]n|liquefaction|licuefacci[oó]n)')
ADVANCE=re.compile(r'(?i)\b(estudi\w*|investigaci[oó]n|investigador\w*|cient[ií]fic\w*|descubr\w*|hallazgo\w*|avance\w*|tecnolog\w*|sensor\w*|sat[eé]lit\w*|alerta\w*|sistema\w*|desarroll\w*|m[eé]todo\w*|inteligencia artificial|model\w*|research\w*|stud(?:y|ies)|scientis\w*|discover\w*|breakthrough\w*|technolog\w*|satellite\w*|warning\w*|innovation\w*|develop\w*|detect\w*|predict\w*|monitor\w*|mapping|analy\w*|analiz\w*|advanc\w*|publication|published|paper\b)')
EVENT=re.compile(r'(?i)\b(sismo de magnitud|temblor de magnitud|terremoto de magnitud|earthquake of magnitude|magnitude [0-9]|m[ .]?[4-9][.,][0-9]|sismo hoy|earthquake strikes|earthquake hits|earthquake struck|temblor hoy|sismo se registr[oó]|earthquake reported|earthquake shakes|terremoto sacude|reportan sismo)')
SPAM=re.compile(r'(?i)\b(horoscope|hor[oó]scopo|astrolog|prediction for today|conspiracy|viral video|tiktok|sensational|apocalipsis)')
STOP=set('a al algo ante con contra de del desde el en entre es esta este estas estos fue han hoy la las lo los más para por que se sin sobre su sus un una unos unas y the of in on to for with from as new how what why at by and scientists study research earthquake seismic tsunami can could may have has reveals reveal'.split())

def clean(t):
 t=html.unescape(re.sub('<[^>]*>',' ',t or ''))
 return re.sub(r'\s+',' ',t).strip()

def get(url,**kw):
 r=HTTP.get(url,timeout=kw.pop('timeout',25),**kw);r.raise_for_status();return r

def feed_items():
 now=datetime.now(timezone.utc);out={}
 audit={'feeds_ok':0,'feeds_failed':0,'rss_items':0,'outside_24h':0,'missing_date':0,
        'not_science':0,'not_advance':0,'event_only':0,'spam':0,'eligible_es':0,'eligible_en':0}
 for lang,q in SOURCES:
  region=('es-CL','CL','CL:es-419') if lang=='es' else ('en-US','US','US:en')
  url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':q,'hl':region[0],'gl':region[1],'ceid':region[2]})
  try:
   root=ET.fromstring(get(url,timeout=30).content)
   audit['feeds_ok']+=1
  except Exception as e:
   audit['feeds_failed']+=1
   print('RSS no disponible:',lang,q,type(e).__name__,str(e)[:110],flush=True)
   continue
  entries=root.findall('.//channel/item')
  print('RSS:',lang,repr(q),'items=',len(entries),flush=True)
  for node in entries:
   audit['rss_items']+=1
   title=clean(node.findtext('title'));desc=clean(node.findtext('description'));link=(node.findtext('link') or '').strip()
   try:dt=parsedate_to_datetime(node.findtext('pubDate') or '').astimezone(timezone.utc)
   except Exception:audit['missing_date']+=1;continue
   if not now-timedelta(hours=24)<=dt<=now+timedelta(minutes=5):
    audit['outside_24h']+=1;continue
   provider=clean(node.findtext('source') or '')
   if ' - ' in title:
    prefix,suffix=title.rsplit(' - ',1)
    if not provider or suffix.lower()==provider.lower():title=prefix.strip()
   content=title+' '+desc
   if not SCIENCE.search(content):audit['not_science']+=1;continue
   if not ADVANCE.search(content):audit['not_advance']+=1;continue
   if EVENT.search(title):audit['event_only']+=1;continue
   if SPAM.search(content):audit['spam']+=1;continue
   key=hashlib.sha256(re.sub(r'\W+','',title.lower()).encode()).hexdigest()[:20]
   if key not in out or (lang=='es' and out[key]['lang']!='es'):
    out[key]={'id':key,'title':title,'description':desc,'url':link,'source':provider or urllib.parse.urlparse(link).netloc,'date':dt.isoformat(),'lang':lang}
 audit['eligible_es']=sum(a['lang']=='es' for a in out.values())
 audit['eligible_en']=sum(a['lang']=='en' for a in out.values())
 print('AUDITORÍA RSS:',json.dumps(audit,ensure_ascii=False),flush=True)
 if audit['feeds_ok']==0:
  raise RuntimeError('Todas las fuentes RSS fallaron: comprobar conexión a Google News')
 return list(out.values())


# La traducción conserva el contenido original; si no hay traducción fiable,
# no se presenta inglés como si fuese una noticia redactada en español.
def translate_es(value):
    value=clean(value)
    if not value:
        return ''
    key=os.getenv('GEMINI_API_KEY','').strip()
    if key:
        try:
            endpoint='https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent'
            r=HTTP.post(endpoint,params={'key':key},json={
                'contents':[{'parts':[{'text':'Traduce fielmente al español de Chile el siguiente texto periodístico. Conserva cifras, nombres y matices; no añadas afirmaciones ni comentarios. Devuelve solo la traducción:\n'+value}]}],
                'generationConfig':{'temperature':0.0}},timeout=25)
            r.raise_for_status()
            data=r.json()
            result=clean(data['candidates'][0]['content']['parts'][0]['text'])
            if len(result)>12:
                return result
        except Exception as exc:
            print('Traducción Gemini no disponible:',type(exc).__name__,flush=True)
    # Servicio de traducción pública: puede limitar solicitudes o cambiar.
    # No se asume su disponibilidad y no se publica si devuelve un fallo.
    try:
        r=get('https://translate.googleapis.com/translate_a/single',params={
            'client':'gtx','sl':'auto','tl':'es','dt':'t','q':value},timeout=18)
        result=clean(''.join(piece[0] for piece in r.json()[0] if piece and piece[0]))
        if len(result)>=12:
            return result
    except Exception as exc:
        print('Traducción pública no disponible:',type(exc).__name__,flush=True)
    return None

def spanish_article(article):
    if article['lang']=='es':
        return dict(article)
    title=translate_es(article['title'])
    if not title:
        return None
    desc=translate_es(article['description']) if article['description'] else ''
    if desc is None:
        desc=''
    converted=dict(article)
    converted['original_title']=article['title']
    converted['title']=title
    converted['description']=desc
    converted['translation']='automática; revisar fuente original'
    return converted

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
 """Busca paisajes geológicos verificables, no noticias ajenas ni personas.

 Una imagen genérica de paisaje NO se presenta como foto documental del hallazgo.
 Guarda un indicador ilustrative=True solo en el JSON interno.
 """
 original=(article.get('original_title') or article['title']).lower()
 title=article['title']; words=tokens(title)
 groups=[
  (r'tsunami|coast|ocean|shore|cost|mar|oce[aá]no',
   [('tsunami affected coast',('tsunami','coast','shore','coastal','inundation','wave')),
    ('tsunami shoreline',('tsunami','coast','shore','coastal'))]),
  (r'sensor|detect|warning|alert|sism[oó]graf|seismomet|monitori',
   [('seismic monitoring station landscape',('seismic','seismometer','station','geophysical','monitoring')),
    ('earthquake monitoring volcano',('seismic','monitoring','volcano','station'))]),
  (r'fault|falla|tectonic|tect[oó]nic|subduct|geolog',
   [('San Andreas Fault landscape',('fault','tectonic','geologic','san andreas','scarp')),
    ('earthquake ground rupture landscape',('earthquake','fault','rupture','scarp'))]),
  (r'landslide|deslizamiento|licuefa|liquefaction',
   [('earthquake landslide landscape',('earthquake','landslide','slope')),
    ('earthquake soil liquefaction',('earthquake','liquefaction','soil'))])
 ]
 key=' '.join((title+' '+article.get('description','')+' '+original).lower().split())
 searches=[]
 for pattern,spec in groups:
  if re.search(pattern,key):searches.extend(spec)
 # Prefer relevant earth science landscape, not a random mountain or a person.
 if not searches:
  searches=[('earthquake fault landscape',('earthquake','fault','scarp','rupture'))]
 searches=searches[:4]
 audit={'queries':len(searches),'candidates':0,'rejected_license':0,'rejected_relevance':0,'rejected_media':0}
 candidates=[]
 for q,terms in searches:
  try:
   data=get('https://commons.wikimedia.org/w/api.php',params={
     'action':'query','generator':'search','gsrsearch':q,'gsrnamespace':6,'gsrlimit':45,
     'prop':'imageinfo','iiprop':'url|extmetadata|mime','iiurlwidth':1500,'format':'json'},timeout=24).json()
  except Exception as exc:
   print('FOTO Commons error:',q,type(exc).__name__,str(exc)[:100],flush=True);continue
  for page in (data.get('query') or {}).get('pages',{}).values():
   audit['candidates']+=1
   info=(page.get('imageinfo') or [{}])[0];meta=info.get('extmetadata') or {}
   lic=(clean(meta.get('LicenseShortName',{}).get('value',''))+' '+clean(meta.get('License',{}).get('value',''))).lower()
   usage=clean(meta.get('UsageTerms',{}).get('value','')).lower()
   if not (re.search(r'\bcc0\b|public domain|publicdomain|pdm',lic) or 'public domain' in usage):
    audit['rejected_license']+=1;continue
   name=page.get('title',''); desc=clean(meta.get('ImageDescription',{}).get('value',''))
   info_text=(name+' '+desc).lower()
   if re.search(r'(?i)\b(moon|lunar|mars|lroc|planetary|spacecraft|diagram|map|chart|poster|drawing|illustration|screenshot|portrait|people|person|crowd|tourist|cityscape|building|logo|render|ai.generated)\b',info_text):
    audit['rejected_relevance']+=1;continue
   matched=sum(t in info_text for t in terms)
   if matched<1 or not SCIENCE.search(info_text):
    audit['rejected_relevance']+=1;continue
   url=info.get('thumburl') or info.get('url')
   if not url or info.get('mime','') not in ('image/jpeg','image/png','image/webp'):
    audit['rejected_media']+=1;continue
   candidates.append((matched,name,url,desc,lic))
  if len(candidates)>=3:break
 print('AUDITORIA FOTO:',audit,'aceptadas metadata:',len(candidates),flush=True)
 for _,name,url,desc,lic in sorted(candidates,reverse=True):
  try:
   r=get(url,timeout=30)
   if len(r.content)>15_000_000:continue
   im=Image.open(io.BytesIO(r.content)).convert('RGB')
   if min(im.size)<450 or max(im.size)<600:continue
   return im,{'title':name,'url':url,'license':lic,'description':desc[:200],'illustrative':True}
  except Exception as exc:
   print('FOTO descarga fallida:',name,type(exc).__name__,flush=True)
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

class _ArticleParagraphs(HTMLParser):
 """Collect visible article paragraphs, excluding navigation and scripts."""
 def __init__(self):
  super().__init__();self.block=0;self.in_p=False;self.parts=[];self.paragraphs=[]
 def handle_starttag(self,tag,attrs):
  if tag in ('script','style','nav','footer','header','aside','noscript'):
   self.block+=1
  elif tag=='p' and not self.block:
   self.in_p=True;self.parts=[]
 def handle_endtag(self,tag):
  if tag in ('script','style','nav','footer','header','aside','noscript'):
   self.block=max(0,self.block-1)
  elif tag=='p' and self.in_p:
   value=clean(' '.join(self.parts))
   if len(value)>65:self.paragraphs.append(value)
   self.in_p=False;self.parts=[]
 def handle_data(self,data):
  if self.in_p and not self.block:self.parts.append(data)


def _full_article_text(article):
 """Try the actual publisher article; never treat the RSS headline as full reporting."""
 url=article.get('url','')
 if not url.startswith('https://'):return ''
 try:
  response=get(url,timeout=18,headers={'Accept':'text/html,application/xhtml+xml'})
  if 'html' not in response.headers.get('Content-Type','').lower():return ''
  if len(response.content)>3_000_000:return ''
  parser=_ArticleParagraphs();parser.feed(response.text)
  paragraphs=[];seen=set()
  for paragraph in parser.paragraphs:
   fingerprint=paragraph.casefold()[:90]
   if fingerprint in seen:continue
   seen.add(fingerprint)
   if re.search(r'(?i)cookies|privacy policy|sign up|subscribe to|advertisement|copyright|all rights reserved',paragraph):continue
   paragraphs.append(paragraph)
  content=' '.join(paragraphs)
  return content[:16000] if len(content.split())>=160 else ''
 except Exception as exc:
  print('ARTICULO: no se pudo consultar texto completo:',type(exc).__name__,str(exc)[:100],flush=True)
  return ''


def _ai_explanation(article,body):
 """Grounded, original Spanish summary; no external facts added."""
 title=article['title']
 prompt=(
  'Eres redactor científico de SISMONAUTA. Escribe una explicación periodística original '
  'en español de Chile, de entre 160 y 240 palabras y 3 o 4 párrafos. '
  'Explica: qué se investigó o anunció; quiénes y qué método emplearon SI están descritos; '
  'qué resultados concretos se comunican; por qué importan, y limitaciones si existen. '
  'Usa SOLO los hechos presentes en el texto proporcionado. No inventes cifras, nombres, '
  'conclusiones, fechas, instituciones o aplicaciones. NO incluyas citas textuales extensas, '
  'referencias, enlaces, créditos de fotos, títulos, emojis, hashtags ni llamada a la acción. '
  'No afirmes que una hipótesis está demostrada. Si la evidencia es insuficiente, responde SOLO INSUFICIENTE. '
  'La explicación debe ser una paráfrasis original y no copiar frases del artículo.\n\n'
  f'TITULAR: {title}\nMEDIO: {article.get("source","")}\nTEXTO DEL ARTÍCULO:\n{body[:14500]}'
 )
 key=os.getenv('GEMINI_API_KEY','').strip()
 if key:
  try:
   model=os.getenv('GEMINI_NEWS_MODEL','gemini-2.5-flash')
   endpoint=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
   r=HTTP.post(endpoint,params={'key':key},json={'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'temperature':0.1,'maxOutputTokens':1200}},timeout=50)
   r.raise_for_status();data=r.json()
   result='\n\n'.join(part.get('text','') for part in data['candidates'][0]['content']['parts']).strip()
   if 'INSUFICIENTE' not in result.upper() and len(result.split())>=115:
    return result[:3500]
  except Exception as exc:
   print('REDACCION GEMINI NO DISPONIBLE:',type(exc).__name__,str(exc)[:150],flush=True)
 key=os.getenv('OPENROUTER_API_KEY','').strip()
 if key:
  try:
   model=os.getenv('OPENROUTER_NEWS_MODEL','openai/gpt-4o-mini')
   r=HTTP.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'temperature':0.1,'max_tokens':850,'messages':[{'role':'user','content':prompt}]},timeout=55)
   r.raise_for_status();result=(r.json()['choices'][0]['message']['content'] or '').strip()
   if 'INSUFICIENTE' not in result.upper() and len(result.split())>=115:return result[:3500]
  except Exception as exc:
   print('REDACCION OPENROUTER NO DISPONIBLE:',type(exc).__name__,str(exc)[:150],flush=True)
 key=os.getenv('GROQ_API_KEY','').strip()
 if key:
  try:
   model=os.getenv('GROQ_NEWS_MODEL','llama-3.3-70b-versatile')
   r=HTTP.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'temperature':0.1,'max_tokens':850,'messages':[{'role':'user','content':prompt}]},timeout=55)
   r.raise_for_status();result=(r.json()['choices'][0]['message']['content'] or '').strip()
   if 'INSUFICIENTE' not in result.upper() and len(result.split())>=115:return result[:3500]
  except Exception as exc:
   print('REDACCION GROQ NO DISPONIBLE:',type(exc).__name__,str(exc)[:150],flush=True)
 return None


def detailed_explanation(article):
 """Only publish a substantial explanation grounded in an accessible article."""
 body=_full_article_text(article)
 if not body:
  print('SIN TEXTO COMPLETO: no es posible describir fielmente la noticia.',flush=True)
  return None
 text=_ai_explanation(article,body)
 if not text:
  print('SIN REDACCION VERIFICABLE: configura GEMINI_API_KEY, OPENROUTER_API_KEY o GROQ_API_KEY.',flush=True)
 return text


def caption(a,credit):
 """Long, original, evidence-grounded caption; credits remain only in internal JSON."""
 explanation=re.sub(r'[^\S\n]+',' ',a.get('explanation','')).strip()
 if len(explanation.split())<115:
  raise ValueError('La explicación es demasiado breve para publicar: exige el artículo completo y redacción verificable')
 parts=['🌎 SISMONAUTA | ACTUALIDAD SÍSMICA','',clean(a['title']),'',explanation,'',
        '¿Qué opinas de este descubrimiento? Te leemos en los comentarios.','',
        '#SISMONAUTA #Sismologia #Ciencia #Terremotos #Tecnologia #Investigacion #Geologia #Tectonica']
 text='\n'.join(parts)
 if len(text)>2200:raise ValueError('La explicación excede el límite de Instagram; reducir el texto antes de publicar')
 return text

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
 articles=feed_items();articles.sort(key=priority,reverse=True)
 print('Noticias candidatas ES + EN (últimas 24 h):',len(articles),flush=True)
 if not articles:
  print('SIN NOTICIAS verificables del período. No se publica.',flush=True)
  (out/'estado_publicacion.json').write_text(json.dumps({'status':'no_eligible_news','published':False},ensure_ascii=False),encoding='utf8')
  return 0  # Sin noticia elegible no es error técnico ni publicación exitosa
 chosen=None;img=None;credit=None
 reasons={'already_seen':0,'no_photo':0,'no_translation':0,'no_article_or_ai':0}
 for a in articles[:35]:
  if a['id'] in state['stories']:
   reasons['already_seen']+=1;continue
  print('Evaluando:',a['title'][:110],'idioma:',a['lang'],flush=True)
  # Buscar imagen usando los términos ORIGINALES de la noticia; para los
  # estudios en inglés, Wikimedia Commons suele estar catalogada en inglés.
  photo,meta=commons_photo(a)
  if photo is None:
   reasons['no_photo']+=1
   print('OMITIDA: ninguna fotografía CC0/PD pertinente para',a['title'][:75],flush=True)
   continue
  translated=spanish_article(a)
  if translated is None:
   reasons['no_translation']+=1
   print('OMITIDA: fallo al traducir titular inglés, sin publicar inglés',flush=True)
   continue
  explanation=detailed_explanation(translated)
  if not explanation:
   reasons['no_article_or_ai']+=1
   print('OMITIDA: no hay suficientes datos comprobables para explicación extensa',flush=True)
   continue
  translated['explanation']=explanation
  chosen,img,credit=translated,photo,meta
  break
 if not chosen:
  print('AUDITORIA SELECCION:',json.dumps(reasons,ensure_ascii=False),flush=True)
  print('No existe noticia inédita con fotografía reutilizable y pertinente. No se publica.',flush=True)
  (out/'estado_publicacion.json').write_text(json.dumps({'status':'no_licensed_relevant_photo','published':False,'articles_reviewed':min(len(articles),35),'reasons':reasons},ensure_ascii=False),encoding='utf8')
  return 0  # Sin foto con licencia verificada: omitir publicación
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
