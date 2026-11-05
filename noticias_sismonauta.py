#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA | Noticias científicas verificables y publicación multiplataforma.

Python 3.11+ | pip install requests pillow
Opcional: pip install trafilatura googlenewsdecoder

python noticias_sismonauta.py --test-filter
python noticias_sismonauta.py --test-publication
python noticias_sismonauta.py --out output_noticias
python noticias_sismonauta.py --publish --out output_noticias

Publica una fotografía en Instagram, una fotografía en la fanpage de Facebook
Y un post público independiente de texto en el feed de la misma fanpage.
Cada publicación tiene estado propio. No se reintentan envíos inciertos.
"""
from __future__ import annotations

import argparse, base64, hashlib, html, io, json, math, os, random, re, sys, time
import unicodedata, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

HTTP=requests.Session()
HTTP.headers.update({'User-Agent':'SismonautaNews/6.0 (educational; https://www.nasa.gov/)', 'Accept':'*/*'})
VERSION=os.getenv('IG_GRAPH_VERSION','v25.0')
IG_BASE=os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
FB_BASE='https://graph.facebook.com'
STATE_FILE='sismonauta_noticias/state.json'
ACCOUNT='sismonauta'
LOOKBACK_HOURS=max(60,int(os.getenv('NEWS_LOOKBACK_HOURS','336')))
MAX_ARTICLES=max(1,int(os.getenv('NEWS_MAX_ARTICLES','350')))
W,H=1080,1350
MIN_SOURCE_WORDS=85
FALLBACK_SOURCE_WORDS=35
FALLBACK_DEVELOPMENT_WORDS=35
MIN_VERIFIED_BRIEF_WORDS=18
MIN_HEADLINE_DIGEST_WORDS=14
MIN_DEVELOPMENT_WORDS=80
MIN_EDITORIAL_RELEVANCE=int(os.getenv('NEWS_MIN_EDITORIAL_RELEVANCE','3'))
NEWS_ALLOW_HEADLINE_ONLY=os.getenv('NEWS_ALLOW_HEADLINE_ONLY','false').lower()=='true'
HASHTAGS='#SISMONAUTA #Ciencia #Actualidad #Naturaleza #Investigacion #PlanetaTierra #DivulgacionCientifica'
CATEGORY_HASHTAGS={
 'sismos':'#Sismos #Terremotos #Sismologia #Geofisica #Tectonica',
 'volcanes':'#Volcanes #Erupciones #Volcanologia #Geologia',
 'meteorologia':'#Meteorologia #Tiempo #Tormentas #Atmosfera',
 'climatologia':'#Climatologia #Clima #CambioClimatico #Glaciares',
 'oceanos':'#Oceanos #Oceanografia #Tsunamis #CienciaDelMar',
 'espacio':'#Espacio #Astronomia #NASA #ExploracionEspacial',
 'desastres':'#DesastresNaturales #Prevencion #RiesgosNaturales',
 'geologia':'#Geologia #PlanetaTierra #Geociencias #Investigacion'}
SEARCH_GROUPS={
 'sismos':[('es','sismología investigación'),('es','fallas tectónicas'),('es','alerta sísmica tecnología'),('en','earthquake seismology research'),('en','seismic early warning research'),('en','tectonic fault science')],
 'volcanes':[('es','volcanes erupciones'),('es','actividad volcánica'),('es','vigilancia volcánica'),('en','volcano eruption'),('en','volcanic activity'),('en','volcano monitoring')],
 'meteorologia':[('es','meteorología tormentas'),('es','huracanes ciclones'),('es','fenómenos meteorológicos'),('en','meteorology weather research'),('en','hurricane cyclone'),('en','atmospheric science')],
 'climatologia':[('es','clima investigación'),('es','cambio climático'),('es','glaciares científicos'),('en','climate science'),('en','climate research'),('en','glacier research')],
 'oceanos':[('es','océanos investigación'),('es','tsunami océano'),('es','corrientes oceánicas'),('en','ocean science'),('en','oceanography discovery'),('en','sea level research')],
 'espacio':[('es','astronomía descubrimiento'),('es','NASA espacio'),('es','satélites observación terrestre'),('en','NASA space science'),('en','astronomy discovery'),('en','Earth observation satellite')],
 'desastres':[('es','desastres naturales'),('es','deslizamientos de tierra'),('es','inundaciones prevención'),('en','natural hazards'),('en','landslide flooding'),('en','disaster risk science')],
 'geologia':[('es','geología descubrimiento'),('es','geofísica investigación'),('es','formación de montañas'),('en','geology discovery'),('en','geophysics research'),('en','geological research')]
}
CATEGORY_PATTERNS={k:re.compile(v,re.I) for k,v in {
 'sismos':r'sism\w*|s[ií]smic\w*|seism\w*|earthquak\w*|terremot\w*|tect[oó]nic\w*|tectoni\w*|falla geol[oó]gica|fault zone|subduction|subducci[oó]n',
 'volcanes':r'volc[aá]n\w*|volcan\w*|volcanic\w*|erupci[oó]n\w*|eruption\w*|lava\b|magma\w*',
 'meteorologia':r'meteorolog\w*|weather\b|storm\w*|torment\w*|hurac[aá]n\w*|hurricane\w*|cicl[oó]n\w*|cyclone\w*|tornado\w*|atmosf[eé]ric\w*|atmospher\w*|rainfall|forecast\w*',
 'climatologia':r'climat\w*|climate\w*|calentamiento global|global warming|glaciar\w*|glacier\w*|criosfera|cryosphere|deshielo',
 'oceanos':r'oc[eé]an\w*|ocean\w*|oceanograf\w*|oceanograph\w*|tsunami\w*|maremot\w*|nivel del mar|sea level|seafloor|arrecife\w*',
 'espacio':r'astronom\w*|astronomy|espacio exterior|outer space|exploraci[oó]n espacial|space exploration|NASA\b|ESA\b|sat[eé]lit\w*|satellite\w*|planet\w*|galaxi\w*|galaxy|telescop\w*|asteroid\w*',
 'desastres':r'desastre\w* natural\w*|natural disaster\w*|deslizamiento\w*|landslide\w*|inundaci[oó]n\w*|flood\w*|alud\w*|avalanche\w*|incendio\w* forestal\w*|wildfire\w*|natural hazard\w*',
 'geologia':r'geolog\w*|ge[oó]log\w*|geophysic\w*|geof[ií]sic\w*|corteza terrestre|earth crust|rock formation\w*|geological\w*|geomorfolog\w*|continental drift'
}.items()}
SPAM=re.compile(r'hor[oó]scop\w*|astrolog\w*|conspiracy|conspiraci[oó]n\w*|apocalipsis|lottery|apuesta\w*|oferta comercial',re.I)
MISLEADING=re.compile(r'predicen el pr[oó]ximo terremoto|exact earthquake prediction|fecha exacta del pr[oó]ximo sismo',re.I)
QUAKE_TERM=re.compile(r'\b(?:sismos?|tembl(?:or|ores)|terremotos?|earthquakes?|quakes?|seismic\s+(?:event|tremor|shock))\b',re.I)
QUAKE_MAGNITUDE=re.compile(r'(?<![a-z0-9])(?:mag(?:nitud|nitude)?\.?\s*(?:de\s+|of\s+)?[:=]?\s*[0-9](?:[.,][0-9]+)?|m(?:w|l|b|s)?\s*[:=]?\s*[3-9](?:[.,][0-9]+)?)\b',re.I)
QUAKE_REPORT=re.compile(r'\b(?:sacud\w*|remec\w*|ocurr\w*|registr\w*|report\w*|detect\w*|sentid\w*|sinti\w*|strik\w*|struck|hits?|shak\w*|shook|felt|occurred|recorded|detected|reported|hoy|today|ayer|yesterday)\b',re.I)
QUAKE_LOCATION=re.compile(r'\b(?:epicentro|hipocentro|profundidad|depth|km|kilometros?|near|offshore|cerca de|frente a|noreste|noroeste|sureste|suroeste|north|south|east|west|latitud|longitud|latitude|longitude)\b',re.I)
QUAKE_HEADLINE=re.compile(r'\b(?:nuevo|fuerte|moderado|leve|strong|moderate|minor|major)\s+(?:sismo|temblor|terremoto|earthquake|quake)\b|\b(?:sismo|temblor|terremoto|earthquake|quake)\s+(?:en|cerca|frente|near|off|in|at)\b',re.I)
QUAKE_RESEARCH=re.compile(r'\b(?:investig\w*|estudi\w*|cientific\w*|descubr\w*|hallazgo\w*|analis\w*|analiz\w*|alerta temprana|research\w*|stud(?:y|ies)|scientist\w*|discover\w*|finding\w*|early warning|new model|historic\w*|historico\w*)\b',re.I)
QUAKE_CATALOG_TITLE=re.compile(r'\b(?:moderado|moderate|fuerte|strong|leve|minor|light|weak)\b.{0,55}(?:\bmag\.?\s*[0-9]|\bmw\s*[0-9]|\bml\s*[0-9])',re.I)
QUAKE_DIRECT=re.compile(r'\b(?:sismo|temblor|terremoto|earthquake|quake)\b.{0,90}\b(?:sacude|remece|registrado|ocurre|ocurrio|reported|strikes|struck|hits|shakes)\b|\b(?:sacude|remece|registran|reportan|strikes|hits|shakes)\b.{0,90}\b(?:sismo|temblor|terremoto|earthquake|quake)\b',re.I)
QUAKE_CATALOG_SOURCES=('volcanodiscovery','earthquake track','earthquake tracker','earthquake monitor','recent earthquakes','latest earthquakes')
EDITORIAL_CORE=re.compile(r'sism|seism|earthquake|terremot|tect[oó]nic|subducci[oó]n|fault|falla geol|volc[aá]n|volcan|erupci[oó]n|eruption|magma|lava|geof[ií]sic|geophys|geolog|ge[oó]log|oceanograf|oceanograph|tsunami|maremoto|deslizamiento|landslide|alud|inundaci[oó]n|flood|tornado|hurac[aá]n|hurricane|cyclone|cicl[oó]n|glaciar|glacier',re.I)
EDITORIAL_SCIENCE=re.compile(r'investig|research|estudio|study|studies|cient[ií]fic|scientist|descubr|discover|observatori|observatory|medici[oó]n|measurement|modelo|model|instrument|sat[eé]lit|satellite|monitore|monitor|alerta temprana|early warning|nueva escala|new scale|predicci[oó]n|prediction|evidencia|evidence',re.I)
EDITORIAL_LOW_VALUE=re.compile(r'\b(clima en|weather in|hor[oó]scopo|pron[oó]stico del d[ií]a|sports?|f[uú]tbol|stock market|finance|fundraising|appointments?|designan|nombran|galas?|seminar|webinar|review:|op-ed:|opin[iioó]n|elections?|candidates?)\b',re.I)

def clean(s): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]*>',' ',str(s or '')))).strip()
def normalized(s): return ''.join(c for c in unicodedata.normalize('NFKD',clean(s).lower()) if not unicodedata.combining(c))
def words(s): return len(clean(s).split())
def log(*parts): print(*parts,flush=True)
def now(): return datetime.now(timezone.utc)
def write_json(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf-8');tmp.replace(path)
def safe_error(exc):
    message=str(exc)
    for name in ('GH_TOKEN','INSTAGRAM_TOKEN','FACEBOOK_PAGE_TOKEN','GEMINI_API_KEY','OPENROUTER_API_KEY','GROQ_API_KEY'):
        value=os.getenv(name,'')
        if value:message=message.replace(value,'***')
    return message[:300]
def get(url,**kwargs):
    response=HTTP.get(url,timeout=kwargs.pop('timeout',25),**kwargs)
    response.raise_for_status();return response

def is_earthquake_occurrence(title,description='',source=''):
    title,description,source=normalized(title),normalized(description),normalized(source)
    combined=title+' '+description; has_quake=bool(QUAKE_TERM.search(combined))
    scientific_title=bool(QUAKE_RESEARCH.search(title))
    if any(x in source for x in QUAKE_CATALOG_SOURCES) and (has_quake or QUAKE_CATALOG_TITLE.search(title)) and not scientific_title:return True
    if QUAKE_TERM.search(title) and QUAKE_MAGNITUDE.search(title) and not scientific_title:return True
    if QUAKE_CATALOG_TITLE.search(title) and QUAKE_LOCATION.search(title) and not scientific_title:return True
    if QUAKE_TERM.search(title) and QUAKE_REPORT.search(title) and not scientific_title:return True
    if QUAKE_DIRECT.search(title) and not scientific_title:return True
    if QUAKE_HEADLINE.search(title) and not scientific_title:return True
    if scientific_title:return False
    return bool(QUAKE_TERM.search(title) and (QUAKE_MAGNITUDE.search(description) or QUAKE_REPORT.search(description)) and (QUAKE_LOCATION.search(combined) or QUAKE_MAGNITUDE.search(combined)))

def test_earthquake_filter():
    blocked=['Terremoto moderado mag. 4.5 - Océano Pacífico Norte, 230 km al noreste de Kamchatka','Sismo magnitud 5.2 cerca de Santiago','Terremoto Mw 6.1 frente a Japón','Sismo M4.3 en el norte de Chile','Temblor de magnitud 3,8 sacude Perú','Earthquake M5.6 near Tokyo','Moderate earthquake strikes California','Sismo de magnitud 4.9 registrado en México','Fuerte temblor en el norte de Chile','Earthquake near Japan','Terremoto de magnitud 5,0 con epicentro en Perú']
    allowed=['Científicos estudian el terremoto de Valdivia de 1960','Investigadores analizan un terremoto de magnitud 7','Nuevo estudio explica las fallas tectónicas','NASA descubre nuevas características de Marte','Volcán presenta actividad eruptiva','Investigadores analizan corrientes oceánicas','Huracán se intensifica sobre el océano','Nuevo sistema de alerta temprana para terremotos']
    for item in blocked:assert is_earthquake_occurrence(item),item
    for item in allowed:assert not is_earthquake_occurrence(item),item
    log('FILTRO SÍSMICO VALIDADO:',len(blocked),'bloqueados;',len(allowed),'permitidos')

def classify(title,description,source=''):
    if is_earthquake_occurrence(title,description,source):return None
    content=clean(title)+' '+clean(description)
    if SPAM.search(content) or MISLEADING.search(content):return None
    matches=[key for key,pattern in CATEGORY_PATTERNS.items() if pattern.search(content)]
    for category in ('sismos','volcanes','oceanos','desastres','meteorologia','climatologia','geologia','espacio'):
        if category in matches:return category
    return None

DIRECT_FEEDS=(
 ('NASA publicaciones','https://www.nasa.gov/feed/'),
 ('NASA comunicados','https://www.nasa.gov/news-release/feed/'),
 ('NASA Tierra','https://www.nasa.gov/earth/feed/'),
 ('NASA ciencia','https://science.nasa.gov/feed/'),
 ('NASA tecnología','https://www.nasa.gov/technology/feed/'),
 ('NOAA noticias','https://www.noaa.gov/rss.xml'),
 ('ESA noticias','https://www.esa.int/rssfeed/Our_Activities/Space_News'),
 ('ESA ciencia espacial','https://www.esa.int/rssfeed/ESC'),
 ('ScienceDaily Tierra y Clima','https://www.sciencedaily.com/rss/earth_climate.xml'),
 ('ScienceDaily Ambiente','https://www.sciencedaily.com/rss/top/environment.xml'),
 ('Phys.org Ciencias de la Tierra','https://phys.org/rss-feed/earth-news/earth-sciences/'),
 ('Phys.org Medio Ambiente','https://phys.org/rss-feed/earth-news/environment/'))

def parse_date(raw):
    try:date=parsedate_to_datetime(raw)
    except Exception:
        try:date=datetime.fromisoformat(raw.replace('Z','+00:00'))
        except Exception:return None
    return (date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date).astimezone(timezone.utc)

def direct_feed_items(cutoff,current):
    results=[];seen=set()
    for publisher,url in DIRECT_FEEDS:
        try:root=ET.fromstring(get(url,timeout=20).content)
        except Exception as exc:log('RSS DIRECTO ERROR:',publisher,safe_error(exc));continue
        nodes=root.findall('.//channel/item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
        for node in nodes[:70]:
            def nt(*tags):
                for tag in tags:
                    value=node.findtext(tag)
                    if value:return value.strip()
                return ''
            title=clean(nt('title','{http://www.w3.org/2005/Atom}title'))
            link=nt('link')
            if not link:
                n=node.find('{http://www.w3.org/2005/Atom}link');link=n.get('href','') if n is not None else ''
            date=parse_date(nt('pubDate','{http://www.w3.org/2005/Atom}updated','{http://www.w3.org/2005/Atom}published'))
            if date is None or not cutoff<=date<=current+timedelta(minutes=5):continue
            description=clean(nt('description','{http://www.w3.org/2005/Atom}summary'))
            body=clean(nt('{http://purl.org/rss/1.0/modules/content/}encoded','{http://www.w3.org/2005/Atom}content'))
            if not title or not link.startswith('https://'):continue
            category=classify(title,description+' '+body[:650],publisher)
            if not category:continue
            identifier=hashlib.sha256(re.sub('[^a-z0-9]','',normalized(title)).encode()).hexdigest()[:20]
            if identifier in seen:continue
            seen.add(identifier)
            results.append(dict(id=identifier,title=title,description=description,url=link,source=publisher,date=date.isoformat(),lang='en',category=category,search_category='direct',source_body=body))
    log('FUENTES DIRECTAS: noticias elegibles:',len(results));return results

def feed_items():
    current=now();cutoff=current-timedelta(hours=LOOKBACK_HOURS)
    output={};audit={'feeds_ok':0,'feeds_failed':0,'rss_items':0,'outside_window':0,'earthquake_occurrence':0,'not_relevant':0,'accepted':0}
    for search_category,queries in SEARCH_GROUPS.items():
        for lang,query in queries:
            hl,gl,ceid=('es-CL','CL','CL:es-419') if lang=='es' else ('en-US','US','US:en')
            url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':query+' when:14d','hl':hl,'gl':gl,'ceid':ceid})
            try:root=ET.fromstring(get(url,timeout=20).content);audit['feeds_ok']+=1
            except Exception as exc:audit['feeds_failed']+=1;log('RSS ERROR:',query,safe_error(exc));continue
            for node in root.findall('.//channel/item')[:80]:
                audit['rss_items']+=1
                title=clean(node.findtext('title'));description=clean(node.findtext('description'))
                link=clean(node.findtext('link'));provider=clean(node.findtext('source'))
                if ' - ' in title:
                    prefix,suffix=title.rsplit(' - ',1)
                    if not provider or normalized(suffix)==normalized(provider):title=prefix.strip()
                date=parse_date(node.findtext('pubDate') or '')
                if date is None or not cutoff<=date<=current+timedelta(minutes=5):audit['outside_window']+=1;continue
                if is_earthquake_occurrence(title,description,provider):audit['earthquake_occurrence']+=1;continue
                category=classify(title,description,provider)
                if not category or words(title)<4 or not link.startswith('https://'):audit['not_relevant']+=1;continue
                identifier=hashlib.sha256(re.sub('[^a-z0-9]','',normalized(title)).encode()).hexdigest()[:20]
                if identifier in output:continue
                output[identifier]=dict(id=identifier,title=title,description=description,url=link,source=provider,date=date.isoformat(),lang=lang,category=category,search_category=search_category)
                audit['accepted']+=1
    for article in direct_feed_items(cutoff,current):
        old=output.get(article['id'])
        if not old or len(article.get('source_body',''))>len(old.get('source_body','')):output[article['id']]=article
    log('AUDITORÍA RSS:',json.dumps(audit,ensure_ascii=False))
    if not audit['feeds_ok'] and not output:raise RuntimeError('Ninguna fuente RSS disponible')
    return list(output.values())

def editorial_relevance(article):
    title=clean(article.get('original_title') or article.get('title',''));category=article.get('category','')
    if is_earthquake_occurrence(title,article.get('description',''),article.get('source','')):return -100
    if EDITORIAL_LOW_VALUE.search(title) or SPAM.search(title) or MISLEADING.search(title):return -100
    core=bool(EDITORIAL_CORE.search(title));science=bool(EDITORIAL_SCIENCE.search(title))
    if not core and not (science and category in ('sismos','volcanes','geologia','oceanos','desastres')):return 0
    score=2 if core else 1
    if science:score+=2
    if category in ('sismos','volcanes','geologia','oceanos','desastres'):score+=2
    if category in ('meteorologia','climatologia') and not science:score-=1
    return score

def editorial_eligible(article):
    relevance=editorial_relevance(article)
    if relevance>=MIN_EDITORIAL_RELEVANCE:return True
    if relevance<0 or article.get('search_category')!='direct':return False
    title=clean(article.get('title',''));description=clean(article.get('description',''))
    return (words(title)>=4 and article.get('category') in SEARCH_GROUPS and source_text_sufficient(article.get('source_body',''))
            and any(p.search(title+' '+description) for p in CATEGORY_PATTERNS.values())
            and not SPAM.search(title+' '+description) and not MISLEADING.search(title+' '+description))

def priority(article):
    category=article.get('category');score={'sismos':9,'volcanes':8,'oceanos':7,'desastres':7,'meteorologia':6,'climatologia':6,'geologia':6,'espacio':5}.get(category,0)
    score+=editorial_relevance(article)*12
    text=normalized(article.get('title','')+' '+article.get('description',''))
    for term,value in [('investigacion',5),('research',5),('descubrimiento',5),('discovery',5),('nasa',4),('noaa',4),('tecnologia',4),('technology',4),('estudio',4),('study',4),('volcan',3),('eruption',3),('huracan',3),('tsunami',3)]:
        if term in text:score+=value
    try:age=max(0,(now()-datetime.fromisoformat(article['date'])).total_seconds()/3600)
    except Exception:age=LOOKBACK_HOURS
    return score+max(0,LOOKBACK_HOURS-age)*.18+min(5,words(article.get('description',''))/12)+(7 if source_text_sufficient(article.get('source_body','')) else 0)

_AI_DISABLED=set();_GEMINI_MODEL_CACHE=None

def gemini_models(key):
    global _GEMINI_MODEL_CACHE
    if _GEMINI_MODEL_CACHE is not None:return _GEMINI_MODEL_CACHE
    names=[]
    try:
        response=HTTP.get('https://generativelanguage.googleapis.com/v1beta/models',headers={'x-goog-api-key':key},timeout=18)
        response.raise_for_status()
        names=[m['name'].removeprefix('models/') for m in response.json().get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[])]
    except Exception as exc:log('Gemini modelos:',safe_error(exc))
    preferred=os.getenv('GEMINI_NEWS_MODEL','gemini-2.5-flash').removeprefix('models/')
    names.sort(key=lambda x:(x!=preferred,'flash' not in x,x));_GEMINI_MODEL_CACHE=names[:6]
    return _GEMINI_MODEL_CACHE

def ai_text(prompt,max_tokens=1300):
    key=os.getenv('GEMINI_API_KEY','').strip()
    if key and 'gemini' not in _AI_DISABLED:
        models=gemini_models(key)
        for model in models:
            try:
                response=HTTP.post('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',headers={'x-goog-api-key':key},json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'temperature':.15,'maxOutputTokens':max_tokens}},timeout=48)
                if response.status_code in (400,404):continue
                response.raise_for_status()
                result='\n\n'.join(p.get('text','') for p in response.json()['candidates'][0]['content']['parts']).strip()
                if result:return result
            except Exception as exc:log('Gemini:',safe_error(exc)[:115])
        _AI_DISABLED.add('gemini')
    for env,url,model in [('OPENROUTER_API_KEY','https://openrouter.ai/api/v1/chat/completions',os.getenv('OPENROUTER_NEWS_MODEL','openrouter/free')),('GROQ_API_KEY','https://api.groq.com/openai/v1/chat/completions',os.getenv('GROQ_NEWS_MODEL','llama-3.3-70b-versatile'))]:
        key=os.getenv(env,'').strip()
        if not key:continue
        try:
            r=HTTP.post(url,headers={'Authorization':'Bearer '+key},json={'model':model,'temperature':.15,'max_tokens':max_tokens,'messages':[{'role':'user','content':prompt}]},timeout=65);r.raise_for_status()
            value=r.json()['choices'][0]['message']['content']
            if isinstance(value,str) and value.strip():return value.strip()
        except Exception as exc:log('IA:',env,safe_error(exc)[:115])
    return None

def translate_es(value):
    value=clean(value)
    if not value:return ''
    translated=ai_text('Traduce fielmente al español de Chile sin agregar datos ni cambiar nombres o cifras. Devuelve solo la traducción:\n\n'+value,650)
    if translated and len(translated)>10:return clean(translated)
    try:
        response=get('https://translate.googleapis.com/translate_a/single',params={'client':'gtx','sl':'auto','tl':'es','dt':'t','q':value},timeout=18)
        result=clean(''.join(p[0] for p in response.json()[0] if p and p[0]))
        if len(result)>10:return result
    except Exception:pass
    return None

class ArticleParser(HTMLParser):
    SKIP={'script','style','nav','footer','header','aside','noscript'}
    def __init__(self):super().__init__();self.block=0;self.in_p=False;self.parts=[];self.paragraphs=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.SKIP:self.block+=1
        elif tag=='p' and not self.block:self.in_p=True;self.parts=[]
    def handle_endtag(self,tag):
        if tag in self.SKIP:self.block=max(0,self.block-1)
        elif tag=='p' and self.in_p:
            p=clean(' '.join(self.parts))
            if len(p)>55:self.paragraphs.append(p)
            self.in_p=False;self.parts=[]
    def handle_data(self,data):
        if self.in_p and not self.block:self.parts.append(data)

_ARTICLE_CACHE={};_GOOGLE_NEWS_HOSTS={'news.google.com','news.googleusercontent.com'}
def _safe_article_url(url):
    try:
        parsed=urlparse(str(url));return parsed.scheme=='https' and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:return False

def resolve_article_url(url):
    if not _safe_article_url(url):return ''
    if urlparse(url).hostname not in _GOOGLE_NEWS_HOSTS:return url
    try:
        from googlenewsdecoder import gnewsdecoder
        decoded=gnewsdecoder(url,interval=0)
        decoded=decoded.get('decoded_url') if isinstance(decoded,dict) else None
        if decoded and _safe_article_url(decoded) and urlparse(decoded).hostname not in _GOOGLE_NEWS_HOSTS:return decoded
    except Exception:pass
    try:
        response=HTTP.get(url,timeout=12,allow_redirects=True)
        if _safe_article_url(response.url) and urlparse(response.url).hostname not in _GOOGLE_NEWS_HOSTS:return response.url
    except requests.RequestException:pass
    return ''

def _jsonld_articles(page):
    chunks=[]
    for raw in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',page,re.I|re.S):
        try:obj=json.loads(html.unescape(raw))
        except (ValueError,TypeError):continue
        stack=obj if isinstance(obj,list) else [obj]
        while stack:
            item=stack.pop()
            if not isinstance(item,dict):continue
            stack.extend(item.get('@graph') if isinstance(item.get('@graph'),list) else [])
            typ=item.get('@type','');types=typ if isinstance(typ,list) else [typ]
            if any('Article' in str(k) or 'Report' in str(k) for k in types):
                body=item.get('articleBody') or item.get('text')
                if isinstance(body,str):chunks.append(clean(body))
    return ' '.join(chunks)

def extract_article_html(page):
    choices=[]
    try:
        import trafilatura
        result=trafilatura.extract(page,include_comments=False,include_tables=False,favor_recall=True,deduplicate=True)
        if result:choices.append(clean(result))
    except Exception:pass
    ld=_jsonld_articles(page)
    if ld:choices.append(ld)
    match=re.search(r'<article(?:\s[^>]*)?>(.*?)</article>',page,re.I|re.S)
    for markup in ([match.group(1)] if match else [])+[page]:
        try:
            parser=ArticleParser();parser.feed(markup);seen=set();paragraphs=[]
            for p in parser.paragraphs:
                if re.search(r'cookies|privacy policy|subscribe|advertisement',p,re.I):continue
                key=normalized(p)
                if key not in seen:seen.add(key);paragraphs.append(p)
            if paragraphs:choices.append(' '.join(paragraphs))
        except Exception:continue
    choices.sort(key=lambda t:(source_text_sufficient(t),words(t)),reverse=True)
    return choices[0][:18000] if choices else ''

def full_article_text(article):
    direct=article.get('source_body','')
    if source_text_sufficient(direct):return direct[:18000]
    url=article.get('url','')
    if not _safe_article_url(url):return ''
    if url in _ARTICLE_CACHE:return _ARTICLE_CACHE[url]
    final=resolve_article_url(url)
    if not final:_ARTICLE_CACHE[url]='';return ''
    try:
        response=get(final,timeout=15,headers={'Accept':'text/html,application/xhtml+xml'})
        result=extract_article_html(response.text) if 'html' in response.headers.get('Content-Type','').lower() and len(response.content)<=3_000_000 else ''
        if source_text_sufficient(result):article['resolved_source_url']=response.url
        _ARTICLE_CACHE[url]=result;return result
    except Exception as exc:log('ARTÍCULO NO ACCESIBLE:',type(exc).__name__,urlparse(final).hostname);_ARTICLE_CACHE[url]='';return ''

def source_text_sufficient(text):
    if words(text)<MIN_SOURCE_WORDS:return False
    sentences=[s for s in re.split(r'(?<=[.!?])\s+',clean(text)) if words(s)>=8]
    return len({normalized(s) for s in sentences})>=4

def short_source_sufficient(text):
    if words(text)<FALLBACK_SOURCE_WORDS:return False
    sentences=[s for s in re.split(r'(?<=[.!?])\s+',clean(text)) if words(s)>=7]
    return len({normalized(s) for s in sentences})>=2

def brief_development(article,text):
    if not short_source_sufficient(text):return None
    selected=[];seen=set()
    for raw in re.split(r'(?<=[.!?])\s+',clean(text)):
        sentence=clean(raw);key=normalized(sentence)
        if words(sentence)<7 or key in seen or re.search(r'cookie|subscribe|advertis|privacy|suscr[ií]b',sentence,re.I):continue
        selected.append(sentence);seen.add(key)
        if len(selected)>=8 or sum(words(s) for s in selected)>=140:break
    result=' '.join(selected)
    return result if words(result)>=FALLBACK_DEVELOPMENT_WORDS and len(selected)>=2 else None

def source_for_fallback(article,recovered):
    if short_source_sufficient(recovered):return recovered,'article_short'
    if short_source_sufficient(article.get('source_body','')):return article['source_body'],'direct_rss_body'
    if article.get('search_category')=='direct' and short_source_sufficient(article.get('description','')):return article['description'],'direct_rss_summary'
    return '',''

def deterministic_development(text):
    paragraphs=[];seen=set();total=0
    for raw in re.split(r'(?<=[.!?])\s+|\n+',clean(text)):
        sentence=clean(raw)
        if not 8<=words(sentence)<=85 or normalized(sentence) in seen or re.search(r'cookies|subscribe|advertisement|privacy policy',sentence,re.I):continue
        seen.add(normalized(sentence));paragraphs.append(sentence);total+=words(sentence)
        if total>=165:break
    if total<MIN_DEVELOPMENT_WORDS:return None
    return '\n\n'.join(' '.join(paragraphs[i:i+2]) for i in range(0,len(paragraphs),2))

def min_caption_words(article):
    mode=article.get('editorial_mode')
    if mode=='headline_digest':return MIN_HEADLINE_DIGEST_WORDS
    if mode=='verified_summary':return MIN_VERIFIED_BRIEF_WORDS
    if mode=='documented_brief':return FALLBACK_DEVELOPMENT_WORDS
    return MIN_DEVELOPMENT_WORDS

def validate_development(article,source_text):
    title=normalized(article.get('title',''));explanation=clean(article.get('explanation',''))
    sufficient=source_text_sufficient(source_text) or (article.get('editorial_mode')=='documented_brief' and short_source_sufficient(source_text))
    if not sufficient:return False,'Fuente insuficiente'
    if words(explanation)<min_caption_words(article):return False,'Desarrollo demasiado corto'
    if normalized(explanation)==title:return False,'Repite el titular'
    sentences=[s for s in re.split(r'(?<=[.!?])\s+',explanation) if words(s)>=5]
    if len({normalized(s) for s in sentences})<(2 if article.get('editorial_mode')=='documented_brief' else 3):return False,'Desarrollo insuficiente'
    if 'INSUFICIENTE' in explanation.upper():return False,'Contenido insuficiente'
    return True,'Desarrollo válido'

def obtain_development(article,source_text):
    if not source_text_sufficient(source_text):return None
    prompt=('Eres periodista científico de SISMONAUTA. Redacta en español de Chile 120 a 190 palabras y varios párrafos. '
     'Explica resultados y consecuencias SOLO si están documentados. No inventes cifras, fechas ni contexto. '
     'No reproduzcas párrafos extensos ni añadas enlaces o hashtags. Ante dudas responde INSUFICIENTE.\n\n'
     f'TITULAR: {article["title"]}\nCATEGORIA: {article["category"]}\nTEXTO DOCUMENTAL: {source_text[:15500]}')
    response=ai_text(prompt,1350)
    if response and 'INSUFICIENTE' not in response.upper() and validate_development(dict(article,explanation=response),source_text)[0]:return response.strip()
    fallback=deterministic_development(source_text)
    if fallback and validate_development(dict(article,explanation=fallback),source_text)[0]:return fallback
    return None

PHOTO_QUERIES={
 'sismos':['earthquake fault landscape','San Andreas Fault','mountain rock landscape'],
 'volcanes':['volcano landscape','volcanic eruption','volcanic mountain'],
 'meteorologia':['storm clouds landscape','hurricane satellite','thunderstorm clouds'],
 'climatologia':['glacier landscape','polar ice landscape','mountain glacier'],
 'oceanos':['ocean coastline','sea waves shore','coastal landscape'],
 'espacio':['NASA Earth from space','Hubble galaxy','planet astronomy'],
 'desastres':['landslide mountain','flooded river landscape','forest wildfire'],
 'geologia':['rock formations landscape','mountain geology','geological fault']}

def commons_license_accepted(meta):
    values=[clean((meta.get(k) or {}).get('value','')).lower() for k in ('LicenseShortName','License','UsageTerms')]
    return any(v in ' '.join(values) for v in ('cc0','public domain','publicdomain','pdm','pd-old','pd-us','pd-self'))

def used_photo_keys(state):
    used=set()
    for entry in (state.get('stories') or {}).values():
        if not isinstance(entry,dict):continue
        targets=entry.get('targets') or {}
        if not any((targets.get(k) or {}).get('status') in ('published','uncertain','enviando') for k in ('instagram','facebook')):continue
        meta=entry.get('image') or {}
        if not isinstance(meta,dict) or meta.get('generated'):continue
        for field in ('source','title','sha256'):
            value=str(meta.get(field) or '').strip().casefold()
            if value:used.add(field+':'+value)
    return used

def photo_is_used(meta,used):
    return any(field+':'+str(meta.get(field) or '').strip().casefold() in used for field in ('source','title','sha256') if meta.get(field))

def commons_photo(article,state=None):
    candidates=[];seen=set();used=used_photo_keys(state or {})
    for query in PHOTO_QUERIES.get(article['category'],PHOTO_QUERIES['geologia']):
        params={'action':'query','format':'json','formatversion':2,'generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':40,'prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1500}
        continuation={}
        for _ in range(2):
            try:data=get('https://commons.wikimedia.org/w/api.php',params={**params,**continuation},timeout=25).json()
            except Exception as exc:log('COMMONS ERROR:',query,safe_error(exc));break
            pages=(data.get('query') or {}).get('pages',[])
            if isinstance(pages,dict):pages=list(pages.values())
            for page in pages:
                info=(page.get('imageinfo') or [None])[0]
                if not info or not commons_license_accepted(info.get('extmetadata') or {}):continue
                title=clean(page.get('title',''))
                if not re.search(r'\.(?:jpg|jpeg|png|webp)$',title,re.I):continue
                if info.get('mime') not in ('image/jpeg','image/png','image/webp'):continue
                if min(int(info.get('width') or 0),int(info.get('height') or 0))<450:continue
                if re.search('diagram|infographic|schematic|drawing|illustration|logo|poster|chart|presentation|screenshot',title,re.I):continue
                url=info.get('thumburl') or info.get('url')
                if not url or url in seen:continue
                seen.add(url);meta=info.get('extmetadata') or {}
                candidates.append({'title':title,'url':url,'source':info.get('descriptionurl',''),'license':clean((meta.get('LicenseShortName') or {}).get('value','')),'generated':False,'query':query})
            continuation=data.get('continue')
            if not continuation:break
    for item in candidates:
        if photo_is_used(item,used):continue
        try:
            response=get(item['url'],timeout=30)
            if len(response.content)>15_000_000:continue
            with Image.open(io.BytesIO(response.content)) as im:image=im.convert('RGB')
            if min(image.size)<450:continue
            item['sha256']=hashlib.sha256(response.content).hexdigest()
            if photo_is_used(item,used):continue
            log('FOTO COMMONS:',item['title'][:90]);return image,item
        except Exception:continue
    return None,None

def original_background(article):
    rng=random.Random(int(hashlib.sha256(article['id'].encode()).hexdigest()[:12],16))
    im=Image.new('RGB',(W,H),(5,27,53));draw=ImageDraw.Draw(im)
    for y in range(H):
        p=y/H;draw.line((0,y,W,y),fill=(int(8+17*p),int(42+32*p),int(83+30*p)))
    for layer in range(7):
        phase=rng.uniform(0,math.tau);baseline=425+layer*116
        pts=[(x,int(baseline+65*math.sin(x/160+phase)+22*math.sin(x/37+phase/2))) for x in range(-20,W+40,16)]
        draw.polygon(pts+[(W,H),(0,H)],fill=(10+layer*8,55+layer*8,87+layer*4))
    if article['category'] in ('espacio','meteorologia','climatologia'):
        for _ in range(75):
            x=rng.randrange(W);y=rng.randrange(100,H-150);r=rng.randint(1,4)
            draw.ellipse((x-r,y-r,x+r,y+r),fill=(170,210,240))
    return im,{'title':'Arte original SISMONAUTA','url':'','license':'Obra original','generated':True}

def font(size,bold=False):
    choices=(['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'] if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    for path in choices:
        if Path(path).exists():return ImageFont.truetype(path,size)
    return ImageFont.load_default()

def wrap(draw,text,ft,width):
    lines=[];current=''
    for word in clean(text).split():
        new=(current+' '+word).strip()
        if draw.textbbox((0,0),new,font=ft)[2]<=width:current=new
        else:
            if current:lines.append(current)
            current=word
    if current:lines.append(current)
    return lines

def sanitize_explanation(text,title=''):
    text=clean(text)
    text=re.sub(r'^\*?\*?\s*TITULAR\s*:\s*','',text,flags=re.I)
    text=re.sub(r'\*\*','',text)
    if title:text=re.sub(r'^(?:TITULAR\s*:\s*)?'+re.escape(clean(title))+r'[\s:–—.\-]*','',text,flags=re.I)
    return clean(text)

HEADLINE_FORBIDDEN=re.compile(r'\b(?:no\s+vas\s+a\s+creer|incre[ií]ble|impactante|shock|aterrador|apocalipsis|la\s+verdad\s+oculta|nadie\s+te\s+cuenta|te\s+dejar[aá]\s+sin\s+palabras)\b',re.I)
def headline_numbers(text):return set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?(?!\w)',clean(text)))
def headline_valid(candidate,article,source_text):
    candidate=clean(candidate).strip('"“”«»*# ')
    if not 5<=words(candidate)<=19 or len(candidate)>128:return False
    if HEADLINE_FORBIDDEN.search(candidate) or 'TITULAR:' in candidate.upper():return False
    if re.search(r'https?://|#[A-Za-z]|\b(?:urgente|última hora)\b',candidate,re.I):return False
    evidence=' '.join((article.get('original_title') or article.get('title') or '',article.get('description',''),source_text))
    if not headline_numbers(candidate).issubset(headline_numbers(evidence)):return False
    return not is_earthquake_occurrence(candidate,article.get('description',''),article.get('source',''))

def factual_headline(article,source_text):
    original=clean(article.get('title',''))
    prompt=('Reescribe este titular científico como una frase informativa de 7 a 16 palabras, máximo 128 caracteres. '
      'Di qué ocurrió y su importancia documentada; no inventes cifras, no exageres ni pronostiques terremotos. '
      'Una sola línea sin comillas ni hashtags.\nTITULAR: '+original+'\nDESCRIPCIÓN: '+clean(article.get('description',''))[:850]+'\nEVIDENCIA: '+clean(source_text)[:4300])
    result=ai_text(prompt,170)
    if result:
        candidate=re.sub(r'^(?:titular|título|headline)\s*:\s*','',clean(result.splitlines()[0]).strip('"“”«»*# '),flags=re.I)
        if headline_valid(candidate,article,source_text):return candidate
    return original

def make_card(image,article,path):
    canvas=ImageOps.fit(image,(W,H),Image.Resampling.LANCZOS,centering=(.5,.44)).convert('RGBA')
    veil=Image.new('RGBA',(W,H),(0,0,0,0));draw=ImageDraw.Draw(veil)
    draw.rectangle((0,0,W,112),fill=(4,21,46,245))
    draw.text((54,32),'SISMONAUTA  |  ACTUALIDAD',font=font(36,True),fill='white')
    title=clean(article.get('image_headline') or article['title']);top=640;bottom=H-90
    draw.rectangle((0,top,W,bottom),fill=(3,29,58,244));draw.rectangle((0,top,W,top+10),fill=(36,163,255,255))
    chosen=None
    for size in range(72,28,-2):
        ft=font(size,True);lines=wrap(draw,title,ft,W-120);step=int(size*1.2)
        if len(lines)<=6 and len(lines)*step<=(bottom-top-90):chosen=(ft,lines,step);break
    if chosen is None:raise ValueError('Titular demasiado largo para tarjeta')
    ft,lines,step=chosen;y=top+(bottom-top-len(lines)*step)//2
    for line in lines:
        bb=draw.textbbox((0,0),line,font=ft,stroke_width=3)
        draw.text(((W-(bb[2]-bb[0]))/2,y),line,font=ft,fill='white',stroke_width=3,stroke_fill='black');y+=step
    draw.rectangle((0,H-90,W,H),fill=(3,24,49,245))
    label={'sismos':'CIENCIA SÍSMICA','volcanes':'VOLCANES','meteorologia':'METEOROLOGÍA','climatologia':'CLIMA','oceanos':'OCÉANOS','espacio':'ESPACIO','desastres':'NATURALEZA','geologia':'GEOLOGÍA'}.get(article['category'],'CIENCIA')
    draw.text((54,H-63),'CIENCIA  •  '+label,font=font(27,True),fill='white')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    Image.alpha_composite(canvas,veil).convert('RGB').save(path,'JPEG',quality=91,optimize=True)

def caption(article,credit):
    title=clean(article.get('image_headline') or article['title'])
    explanation=sanitize_explanation(article.get('explanation',''),article.get('title',''))
    explanation=sanitize_explanation(explanation,title)
    if words(explanation)<min_caption_words(article):raise ValueError('Falta desarrollo documental')
    label={'sismos':'CIENCIA SÍSMICA','volcanes':'ACTUALIDAD VOLCÁNICA','meteorologia':'METEOROLOGÍA','climatologia':'CLIMATOLOGÍA','oceanos':'CIENCIAS DEL OCÉANO','espacio':'CIENCIA ESPACIAL','desastres':'RIESGOS NATURALES','geologia':'CIENCIAS DE LA TIERRA'}.get(article['category'],'ACTUALIDAD CIENTÍFICA')
    header='🌎 SISMONAUTA | '+label+'\n\n'+title+'\n\n'
    footer='\n\n💬 ¿Qué opinas? Te leemos en los comentarios.\n\n'+HASHTAGS+' '+CATEGORY_HASHTAGS.get(article['category'],'')
    available=2200-len(header)-len(footer)
    if available<300:raise ValueError('Titular demasiado largo')
    if len(explanation)>available:explanation=explanation[:available].rsplit(' ',1)[0].rstrip(',;:')+'.'
    if words(explanation)<min_caption_words(article):raise ValueError('Descripción demasiado corta')
    return header+explanation+footer

# El texto de Facebook no necesita ajustarse al límite de Instagram.
def facebook_feed_text(article,instagram_caption):
    title=clean(article.get('image_headline') or article['title'])
    explanation=sanitize_explanation(article.get('explanation',''),article.get('title',''))
    explanation=sanitize_explanation(explanation,title)
    if words(explanation)<min_caption_words(article):raise ValueError('Texto del post sin desarrollo')
    return ('🌎 SISMONAUTA | ACTUALIDAD CIENTÍFICA\n\n'+title+'\n\n'+explanation+
            '\n\n💬 ¿Qué opinas? Te leemos en los comentarios.\n\n'+HASHTAGS+' '+CATEGORY_HASHTAGS.get(article['category'],''))

class Github:
    def __init__(self):
        self.repo=os.environ['GITHUB_REPOSITORY'];self.branch=os.getenv('GITHUB_REF_NAME') or 'main'
        self.base='https://api.github.com/repos/'+self.repo
        self.session=requests.Session()
        self.session.headers.update({'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        r=self.session.get(self.base,timeout=20);r.raise_for_status()
        if r.json().get('private'):raise RuntimeError('Repositorio público requerido para alojar imágenes')
    def endpoint(self,path):return self.base+'/contents/'+urllib.parse.quote(path,safe='/')
    def read(self,path):
        r=self.session.get(self.endpoint(path),params={'ref':self.branch},timeout=25)
        if r.status_code==404:return None,None
        r.raise_for_status();j=r.json()
        return base64.b64decode(j['content']),j['sha']
    def write(self,path,raw,expected_sha=None):
        """CAS si se conoce SHA. No reintentar una escritura remota incierta."""
        path=str(path);raw=bytes(raw)
        previous,sha=self.read(path)
        if previous==raw:return {'content':{'sha':sha},'unchanged':True}
        if expected_sha is not None and sha!=expected_sha:
            raise RuntimeError('GitHub: estado modificado por otra ejecución; no sobrescribir')
        body={'message':'SISMONAUTA noticias: '+path,'branch':self.branch,'content':base64.b64encode(raw).decode('ascii')}
        if sha:body['sha']=sha
        r=self.session.put(self.endpoint(path),json=body,timeout=60)
        if r.status_code in (409,422):raise RuntimeError('GitHub: conflicto de escritura; revisar concurrencia')
        if not r.ok:
            # Una escritura de estado puede haberse completado aunque la API responda error.
            # Nunca repetir automáticamente sin leer el estado remoto.
            raise RuntimeError(f'GitHub escritura HTTP {r.status_code}; comprobar archivo remoto')
        return r.json()
    def image_url(self,path):
        return 'https://raw.githubusercontent.com/'+self.repo+'/'+urllib.parse.quote(self.branch,safe='')+'/'+urllib.parse.quote(path,safe='/')

def graph(method,path,token,params=None,base=None):
    url=f'{base or IG_BASE}/{VERSION}/{path}'
    fields={**(params or {}),'access_token':token}
    r=requests.get(url,params=fields,timeout=55) if method=='GET' else requests.post(url,data=fields,timeout=75)
    try:data=r.json()
    except ValueError:raise RuntimeError('Meta no devolvió JSON')
    if not r.ok or data.get('error'):
        err=data.get('error') or {}
        raise RuntimeError(f'Meta HTTP {r.status_code}: '+str(err.get('message','Error no especificado'))[:240])
    return data

def ig_check():
    token=os.environ['INSTAGRAM_TOKEN'];user=os.environ['INSTAGRAM_USER_ID']
    info=graph('GET','me',token,{'fields':'id,username'})
    if str(info.get('id'))!=str(user) or str(info.get('username','')).lower()!=ACCOUNT:
        raise RuntimeError('Cuenta Instagram incorrecta')
    log('Instagram @sismonauta verificado');return user,token

def fb_check():
    page=os.environ['FACEBOOK_PAGE_ID'];token=os.environ['FACEBOOK_PAGE_TOKEN']
    info=graph('GET',page,token,{'fields':'id,name,username'},FB_BASE)
    if str(info.get('id'))!=str(page) or (info.get('username') and str(info['username']).lower()!=ACCOUNT):
        raise RuntimeError('Fanpage Facebook incorrecta')
    log('Facebook validado:',info.get('name'));return page,token

def publish_ig(user,token,image_url,text):
    container=graph('POST',user+'/media',token,{'image_url':image_url,'caption':text})['id']
    for _ in range(18):
        time.sleep(8)
        status=graph('GET',str(container),token,{'fields':'status_code'}).get('status_code')
        if status=='FINISHED':break
        if status in ('ERROR','EXPIRED'):raise RuntimeError('Instagram rechazó imagen: '+str(status))
    else:raise RuntimeError('Instagram no confirmó contenedor')
    result=graph('POST',user+'/media_publish',token,{'creation_id':container})
    if not result.get('id'):raise RuntimeError('Instagram no devolvió ID')
    return str(result['id'])

def publish_fb(page,token,image_url,text):
    """Publicación de foto, visible en fanpage (mantener formato original)."""
    result=graph('POST',page+'/photos',token,{'url':image_url,'caption':text,'published':'true'},FB_BASE)
    identifier=result.get('post_id') or result.get('id')
    if not identifier:raise RuntimeError('Facebook foto: no devolvió identificador')
    return str(identifier)

def publish_fb_feed(page,token,text):
    """Publicación pública de TEXTO, sin foto, independiente de /photos."""
    text=str(text or '').strip()
    if not text:raise RuntimeError('Facebook feed: mensaje vacío')
    result=graph('POST',page+'/feed',token,{'message':text,'published':'true'},FB_BASE)
    identifier=str(result.get('id') or '')
    if not re.fullmatch(r'\d+_\d+',identifier):
        raise RuntimeError('Facebook feed: no devolvió un ID de post válido')
    return identifier

DESTINATIONS=('instagram','facebook','facebook_feed')

def targets_of(entry):
    targets=entry.setdefault('targets',{})
    for name in DESTINATIONS:targets.setdefault(name,{'status':'pending'})
    return targets

def is_confirmed(entry):
    targets=entry.get('targets',{})
    return all(targets.get(name,{}).get('status')=='published' for name in DESTINATIONS)

def has_uncertain(entry):
    targets=entry.get('targets',{})
    return any(targets.get(name,{}).get('status') in ('uncertain','enviando') for name in DESTINATIONS)

def load_state(gh):
    raw,sha=gh.read(STATE_FILE)
    state=json.loads(raw) if raw else {'stories':{}}
    state.setdefault('stories',{})
    return state,sha

def save_state(gh,state,sha=None):
    result=gh.write(STATE_FILE,json.dumps(state,ensure_ascii=False,indent=2,sort_keys=True).encode(),expected_sha=sha)
    return (result.get('content') or {}).get('sha')

def recent_published_categories(state,limit=12):
    rows=[]
    for entry in (state.get('stories') or {}).values():
        if not isinstance(entry,dict) or not is_confirmed(entry):continue
        article=entry.get('article') or {};category=article.get('category')
        if category in SEARCH_GROUPS:
            rows.append((str(entry.get('published_at') or entry.get('checked_at') or article.get('date') or ''),category))
    rows.sort(reverse=True)
    return [category for _,category in rows[:limit]]

def diverse_order(articles,state):
    recent=recent_published_categories(state);counts={name:recent.count(name) for name in SEARCH_GROUPS}
    last=recent[0] if recent else None
    def rank(article):
        category=article.get('category');score=priority(article)-9*counts.get(category,0)
        if article.get('search_category')=='direct':score+=55+(35 if words(article.get('source_body',''))>=35 else 22 if words(article.get('description',''))>=18 else 0)
        if category==last:score-=18
        if category not in recent:score+=16
        return score
    result=sorted(articles,key=lambda a:(a.get('search_category')=='direct',source_text_sufficient(a.get('source_body','')),short_source_sufficient(a.get('source_body','')),rank(a)),reverse=True)
    log('PRIORIZACIÓN:',len(result),'noticias, directas:',sum(a.get('search_category')=='direct' for a in result))
    return result

# Reutilizar un candidato pendiente: no seleccionar otra noticia si falta
# completar un destino previo conocido. Nunca reintentar estados inciertos.
def pending_story_ids(state):
    ids=[]
    for key,entry in (state.get('stories') or {}).items():
        if not isinstance(entry,dict) or has_uncertain(entry) or is_confirmed(entry):continue
        targets=targets_of(entry)
        if any(targets[d].get('status')=='published' for d in DESTINATIONS):ids.append(key)
    return ids

def select_news(articles,state):
    reasons={'earthquake_occurrence':0,'already_published':0,'uncertain_previous':0,'source_too_short':0,'no_development':0,'invalid_caption':0,'generated_backgrounds':0}
    pending=set(pending_story_ids(state))
    for original in articles[:MAX_ARTICLES]:
        if pending and original['id'] not in pending:continue
        if not editorial_eligible(original):continue
        if is_earthquake_occurrence(original['title'],original.get('description',''),original.get('source','')):reasons['earthquake_occurrence']+=1;continue
        entry=(state.get('stories') or {}).get(original['id'],{})
        if is_confirmed(entry):reasons['already_published']+=1;continue
        if has_uncertain(entry):reasons['uncertain_previous']+=1;continue
        article=dict(original)
        # Reusar exactamente el contenido ya guardado en el primer envío.
        if entry.get('article',{}).get('explanation'):
            article=dict(entry['article'])
            credit=entry.get('image') or {'generated':True}
            return article,None,credit,entry.get('caption') or caption(article,credit),reasons
        if not source_text_sufficient(source:=full_article_text(article)):
            reasons['source_too_short']+=1;continue
        if article.get('lang')=='en':
            translated=translate_es(article['title'])
            if not translated:continue
            article['original_title']=article['title'];article['title']=translated
            article['description']=translate_es(article['description']) or article['description']
        development=obtain_development(article,source)
        if development and original['lang']=='en' and any(s in normalized(development) for s in (' the ',' and ',' with ',' researchers ')):
            development=translate_es(development)
        if not development:reasons['no_development']+=1;continue
        article['explanation']=development
        if not validate_development(article,source)[0]:reasons['no_development']+=1;continue
        article['image_headline']=factual_headline(article,source)
        photo,credit=commons_photo(original,state)
        if photo is None:photo,credit=original_background(article);reasons['generated_backgrounds']+=1
        try:description=caption(article,credit)
        except ValueError:reasons['invalid_caption']+=1;continue
        return article,photo,credit,description,reasons
    return None,None,None,None,reasons

def _article_from_original(original,mode,evidence,state,score=None):
    article=dict(original)
    if article.get('lang')=='en':
        title=translate_es(article['title']);evidence=translate_es(evidence)
        if not title or not evidence:return None
        article['original_title']=article['title'];article['title']=title
    article['editorial_mode']=mode;article['explanation']=clean(evidence)
    if score is not None:article['evidence_score']=score
    article['image_headline']=article['title']
    photo,credit=commons_photo(original,state)
    if photo is None:photo,credit=original_background(article)
    try:description=caption(article,credit)
    except ValueError:return None
    return article,photo,credit,description

def select_documented_brief(articles,state):
    pending=set(pending_story_ids(state))
    for original in articles[:MAX_ARTICLES]:
        if pending and original['id'] not in pending:continue
        if not editorial_eligible(original):continue
        previous=(state.get('stories') or {}).get(original['id'],{})
        if is_confirmed(previous) or has_uncertain(previous):continue
        if previous.get('article',{}).get('explanation'):continue
        recovered=full_article_text(dict(original));evidence,_=source_for_fallback(original,recovered)
        if not evidence:continue
        explanation=brief_development(original,evidence)
        if not explanation:continue
        result=_article_from_original(original,'documented_brief',explanation,state)
        if result:return result
    return None,None,None,None

def verified_summary_candidate(article):
    if not editorial_eligible(article) or article.get('search_category')!='direct':return None
    description=clean(article.get('description',''))
    if words(description)<MIN_VERIFIED_BRIEF_WORDS or not article.get('url','').startswith('https://'):return None
    if is_earthquake_occurrence(article.get('title',''),description,article.get('source','')):return None
    if SPAM.search(description) or MISLEADING.search(description) or normalized(description)==normalized(article.get('title','')):return None
    return description

def select_verified_brief(articles,state):
    pending=set(pending_story_ids(state))
    for original in articles[:MAX_ARTICLES]:
        if pending and original['id'] not in pending:continue
        previous=(state.get('stories') or {}).get(original['id'],{})
        if is_confirmed(previous) or has_uncertain(previous):continue
        evidence=verified_summary_candidate(original)
        if not evidence:continue
        result=_article_from_original(original,'verified_summary',evidence,state)
        if result:return result
    return None,None,None,None

def evidence_score(article):
    direct=article.get('search_category')=='direct'
    score=max(-100,editorial_relevance(article))*30+(45 if direct else 0)
    score+=(min(90,words(article.get('source_body','')))*1.3 if direct else 0)
    score+=min(65,words(article.get('description','')))*(1.1 if direct else .35)
    score+={'sismos':24,'volcanes':22,'geologia':22,'oceanos':19,'desastres':16,'climatologia':12,'meteorologia':10,'espacio':8}.get(article.get('category'),0)
    if EDITORIAL_SCIENCE.search(clean(article.get('title',''))):score+=28
    score+=min(20,max(0,priority(article))/10)
    return round(score,2)

def select_best_available(articles,state):
    pending=set(pending_story_ids(state));ranked=[]
    for original in articles:
        if pending and original['id'] not in pending:continue
        if original.get('search_category')!='direct' or not editorial_eligible(original):continue
        previous=(state.get('stories') or {}).get(original['id'],{})
        if is_confirmed(previous) or has_uncertain(previous):continue
        if is_earthquake_occurrence(original.get('title',''),original.get('description',''),original.get('source','')):continue
        evidence=clean(original.get('source_body',''))
        if words(evidence)<12:evidence=clean(original.get('description',''))
        if words(evidence)<MIN_VERIFIED_BRIEF_WORDS or normalized(evidence)==normalized(original['title']):continue
        if SPAM.search(evidence) or MISLEADING.search(evidence):continue
        ranked.append((evidence_score(original),original,evidence))
    ranked.sort(key=lambda x:x[0],reverse=True)
    for score,original,evidence in ranked:
        if words(evidence)>120:evidence=' '.join(evidence.split()[:120]).rstrip(',;:')+'…'
        result=_article_from_original(original,'verified_summary',evidence,state,score)
        if result:return result
    return None,None,None,None

def select_headline_digest(articles,state):
    pending=set(pending_story_ids(state))
    for original in articles[:MAX_ARTICLES]:
        if pending and original['id'] not in pending:continue
        if not editorial_eligible(original):continue
        previous=(state.get('stories') or {}).get(original['id'],{})
        if is_confirmed(previous) or has_uncertain(previous):continue
        title=clean(original.get('title',''));evidence=clean(original.get('source_body') or original.get('description'))
        if not original.get('source') or not 9<=words(title) or len(title)>190:continue
        if words(evidence)<MIN_HEADLINE_DIGEST_WORDS or normalized(evidence)==normalized(title):continue
        if is_earthquake_occurrence(title,original.get('description',''),original.get('source','')):continue
        if SPAM.search(evidence) or MISLEADING.search(evidence):continue
        result=_article_from_original(original,'headline_digest',evidence,state)
        if result:return result
    return None,None,None,None

def publish_article(gh,state,article,image_file,credit,description,output):
    """Instagram photo, Facebook photo and independent public Facebook text post.

    Remote status is persisted BEFORE each API POST. Subsequent targets continue
    after an isolated API error; an ambiguous target cannot be retried blindly.
    """
    for title in (article.get('original_title'),article['title']):
        if title and is_earthquake_occurrence(title,article.get('description',''),article.get('source','')):
            raise RuntimeError('PUBLICACIÓN BLOQUEADA: reporte de ocurrencia sísmica')
    if words(article.get('explanation',''))<min_caption_words(article):
        raise RuntimeError('PUBLICACIÓN BLOQUEADA: desarrollo insuficiente')

    ig_user,ig_token=ig_check()
    page,fb_token=fb_check()
    # Lee la versión remota más reciente; no usar el estado que se cargó
    # antes de generar la portada si otra ejecución pudo actualizarlo.
    remote_state,sha=load_state(gh)
    article_id=article['id']
    entry=remote_state['stories'].setdefault(article_id,{'article':article,'image':credit,'targets':{}})
    targets=targets_of(entry)
    receipt_path=output/'estado_publicacion.json'
    if is_confirmed(entry):
        receipt={'status':'already_published','article_id':article_id,'targets':targets,'checked_at':now().isoformat()}
        write_json(receipt_path,receipt)
        return receipt
    if has_uncertain(entry):
        raise RuntimeError('Publicación previa incierta: revisar Meta antes de reintentar')
    for dest in DESTINATIONS:
        if targets[dest].get('status') not in (None,'pending','published'):
            raise RuntimeError('Estado previo desconocido: '+dest)

    def persist():
        nonlocal sha
        sha=save_state(gh,remote_state,sha)

    def receipt(status=None):
        result={'status':status or ('published' if is_confirmed(entry) else 'partial_or_uncertain'),
          'article_id':article_id,'article_title':article['title'],'category':article['category'],
          'targets':targets,'checked_at':now().isoformat()}
        write_json(receipt_path,result)
        return result

    photo_pending=any(targets[name].get('status')!='published' for name in ('instagram','facebook'))
    image_url=entry.get('image_url') or ''
    if photo_pending:
        remote='sismonauta_noticias/images/'+article_id+'.jpg'
        previous,_=gh.read(remote)
        if previous is None:
            if image_file is None or not Path(image_file).is_file():
                raise RuntimeError('Falta la portada para publicar la foto')
            gh.write(remote,Path(image_file).read_bytes())
        elif image_file is not None and Path(image_file).is_file() and previous!=Path(image_file).read_bytes():
            log('IMAGEN REMOTA ORIGINAL CONSERVADA: no se sobrescribirá')
        image_url=gh.image_url(remote)
        accessible=False
        for _ in range(8):
            try:
                response=HTTP.get(image_url,timeout=25)
                if response.ok and response.headers.get('Content-Type','').lower().startswith('image/'):
                    accessible=True;break
            except requests.RequestException:pass
            time.sleep(3)
        if not accessible:raise RuntimeError('Imagen de GitHub no accesible para Meta')

    # No alterar el material editorial de un artículo parcialmente publicado.
    if not entry.get('article',{}).get('explanation'):
        entry['article']=article
    entry.setdefault('image',credit)
    entry.setdefault('caption',description)
    if image_url:entry['image_url']=image_url
    persist()
    fb_text=facebook_feed_text(entry['article'],entry['caption'])
    caption_photo=entry['caption']

    actions=(
        ('instagram',lambda:publish_ig(ig_user,ig_token,image_url,caption_photo)),
        ('facebook',lambda:publish_fb(page,fb_token,image_url,caption_photo)),
        ('facebook_feed',lambda:publish_fb_feed(page,fb_token,fb_text)),
    )
    failures=[]
    for platform,action in actions:
        row=targets[platform]
        if row.get('status')=='published':
            log('OMITIDO:',platform,'ya publicado');continue
        if row.get('status')!='pending':
            raise RuntimeError(platform+': estado desconocido; requiere revisión')
        # El POST aún no se hizo, pero no podemos asumir que no se hará
        # si se corta la ejecución al enviar o al guardar su resultado.
        row['status']='uncertain';row['started_at']=now().isoformat();row.pop('error',None)
        persist();receipt()
        try:
            identifier=action()
            row.update(status='published',id=str(identifier),published_at=now().isoformat())
            persist();receipt()
            log('META PUBLICADO:',platform,'ID:',identifier)
        except Exception as exc:
            row['error']=safe_error(exc)
            failures.append(platform+': '+safe_error(exc))
            try:persist()
            except Exception as persist_exc:
                log('ERROR GUARDANDO ESTADO:',safe_error(persist_exc))
            receipt();log('ERROR META:',platform,safe_error(exc))
    if is_confirmed(entry):
        entry['published_at']=now().isoformat();persist()
    result=receipt()
    if failures:raise RuntimeError('Publicación parcial/incierta; revisar Meta: '+' / '.join(failures))
    return result


def test_publication():
    """Pruebas sin red de migración de estados y endpoints Meta."""
    from unittest.mock import patch
    legacy={'targets':{'instagram':{'status':'published'},'facebook':{'status':'published'}}}
    t=targets_of(legacy)
    assert t['facebook_feed']['status']=='pending'
    assert not is_confirmed(legacy) and not has_uncertain(legacy)
    t['facebook_feed']={'status':'published','id':'123_456'}
    assert is_confirmed(legacy)
    t['facebook_feed']={'status':'uncertain'}
    assert has_uncertain(legacy)
    calls=[]
    def mock_graph(method,path,token,params=None,base=None):
        calls.append((method,path,params,base))
        return {'id':'123_456'} if path.endswith('/feed') else {'post_id':'123_456'}
    with patch(__name__+'.graph',side_effect=mock_graph):
        assert publish_fb('123','secret','https://example.org/image.jpg','noticia')=='123_456'
        assert publish_fb_feed('123','secret','noticia pública')=='123_456'
    assert calls[0][1]=='123/photos' and calls[0][2]['published']=='true'
    assert calls[1][1]=='123/feed' and calls[1][2]['published']=='true'
    assert calls[1][2]['message']=='noticia pública' and 'url' not in calls[1][2]
    log('PRUEBAS PUBLICACIÓN APROBADAS: IG foto, FB foto, FB post y antDuplicados')

def test_extraction():
    fixture='<html><body><article>'+''.join('<p>La investigación reunió datos de observatorios regionales y mediciones instrumentales. Los resultados muestran cambios documentados en la zona estudiada y permiten comparar registros históricos número %s.</p>'%i for i in range(6))+'</article></body></html>'
    assert source_text_sufficient(extract_article_html(fixture))
    assert not source_text_sufficient('Un titular y resumen corto')
    assert resolve_article_url('https://example.com/estudio')=='https://example.com/estudio'
    log('PRUEBAS EXTRACCIÓN APROBADAS')

def test_headlines():
    base={'title':'Investigadores estudian el comportamiento de los glaciares','description':'El estudio observa el retroceso de glaciares','category':'climatologia','source':'Instituto científico'}
    evidence='Un estudio documentó el retroceso de los glaciares en las últimas décadas.'
    assert headline_valid('El estudio documenta el retroceso de los glaciares',base,evidence)
    assert not headline_valid('Los glaciares retroceden 89 kilómetros en una semana',base,evidence)
    assert not headline_valid('No vas a creer lo que ocurre con los glaciares',base,evidence)
    assert not headline_valid('Terremoto magnitud 6.5 cerca de Santiago',base,evidence)
    log('PRUEBAS TITULARES APROBADAS')

def test_fallback():
    example=('El observatorio informó una nueva medición de la actividad de un volcán activo. '
     'Los instrumentos registraron cambios en las emisiones y continúan las observaciones. '
     'El equipo explicó que la evaluación se actualizará con nuevos datos sobre el fenómeno. '
     'Las autoridades publicaron el resumen para informar a las comunidades de la zona.')
    assert short_source_sufficient(example)
    result=brief_development({},example)
    assert result and words(result)>=FALLBACK_DEVELOPMENT_WORDS
    assert not short_source_sufficient('Un titular breve sobre un volcán.')
    log('PRUEBAS RECUPERACIÓN EDITORIAL APROBADAS')

def test_priority_direct():
    base={'title':'Investigación de monitoreo volcánico y riesgos naturales','description':'Investigadores estudian el monitoreo de volcanes y fallas geológicas','category':'volcanes','source':'Prueba','lang':'es','date':now().isoformat(),'url':'https://example.org/article','source_body':''}
    google=[dict(base,id='google-%04d'%i,search_category='volcanes') for i in range(1500)]
    direct=[dict(base,id='direct-%04d'%i,search_category='direct') for i in range(72)]
    ordered=diverse_order(google+direct,{'stories':{}})
    assert all(a['search_category']=='direct' for a in ordered[:72])
    log('PRUEBAS PRIORIDAD FUENTES APROBADAS')

def test_diversity():
    dummy={'stories':{str(i):{'article':{'category':'espacio','date':now().isoformat()},'targets':{d:{'status':'published'} for d in DESTINATIONS},'published_at':now().isoformat()} for i in range(5)}}
    sample=[{'category':category,'title':'Nuevo estudio de ciencia de la Tierra','description':'Nuevas mediciones geofísicas','date':now().isoformat(),'source_body':'','source':'Prueba','search_category':'rss'} for category in ('espacio','volcanes','oceanos')]
    assert diverse_order(sample,dummy)[0]['category']!='espacio'
    log('PRUEBAS DIVERSIDAD APROBADAS')

def main():
    parser=argparse.ArgumentParser(description='SISMONAUTA noticias con foto y post público Facebook')
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--out',default='output_noticias')
    for flag in ('filter','diversity','headlines','extraction','fallback','headline-digest','direct-sources','priority-direct','best-available','direct-editorial','publication'):
        parser.add_argument('--test-'+flag,action='store_true')
    args=parser.parse_args()
    test_earthquake_filter()
    if args.test_filter:return 0
    if args.test_publication:test_publication();return 0
    if args.test_priority_direct:test_priority_direct();return 0
    if args.test_headlines:test_headlines();return 0
    if args.test_extraction:test_extraction();return 0
    if args.test_fallback:test_fallback();return 0
    if args.test_diversity:test_diversity();return 0
    if args.test_direct_sources:
        assert any('sciencedaily.com/rss/earth_climate.xml' in u for _,u in DIRECT_FEEDS)
        assert any('phys.org/rss-feed/earth-news/' in u for _,u in DIRECT_FEEDS)
        log('PRUEBA FUENTES DIRECTAS APROBADA');return 0
    if args.test_direct_editorial:
        sample={'title':'New observations reveal changes in coastal waters','description':'Satellite observations inform ocean science research.','category':'oceanos','search_category':'direct','source_body':('Las observaciones satelitales documentaron cambios medibles en la superficie oceánica. Los investigadores compararon registros instrumentales obtenidos en diferentes campañas científicas. El estudio describe una metodología para medir variaciones mediante sensores remotos. Los resultados permiten comprender mejor la evolución de los procesos naturales. ')*4}
        assert editorial_eligible(sample);log('PRUEBA EDITORIAL DIRECTA APROBADA');return 0
    if args.test_headline_digest or args.test_best_available:
        test_publication();test_headlines();return 0

    output=Path(args.out);output.mkdir(parents=True,exist_ok=True)
    (output/'estado_publicacion.json').unlink(missing_ok=True)
    log('SISMONAUTA | NOTICIAS |',LOOKBACK_HOURS,'HORAS | PUBLICAR:',args.publish)
    gh=Github() if args.publish else None
    state,_=load_state(gh) if gh else ({'stories':{}},None)
    # No generar otro artículo si hay una noticia pendiente de completar
    # entre destinos y aún no se ha determinado si se puede reintentar.
    if args.publish and any(has_uncertain(x) for x in state['stories'].values() if isinstance(x,dict)):
        log('ADVERTENCIA: existen destinos inciertos. No se reintentarán automáticamente.')
    articles=diverse_order(feed_items(),state)
    log('NOTICIAS CANDIDATAS:',len(articles))
    article,photo,credit,description,reasons=select_news(articles,state)
    if article is None:
        log('SEGUNDO NIVEL: noticia documentada breve')
        article,photo,credit,description=select_documented_brief(articles,state)
    if article is None:
        log('TERCER NIVEL: resumen directo validado')
        article,photo,credit,description=select_verified_brief(articles,state)
    if article is None:
        log('CUARTO NIVEL: mejor resumen disponible')
        article,photo,credit,description=select_best_available(articles,state)
    if article is None and NEWS_ALLOW_HEADLINE_ONLY:
        article,photo,credit,description=select_headline_digest(articles,state)
    log('AUDITORÍA SELECCIÓN:',json.dumps(reasons,ensure_ascii=False))
    if article is None:
        write_json(output/'estado_publicacion.json',{'status':'no_eligible_news','published':False,'checked_at':now().isoformat()})
        log('SIN NOTICIAS CON RESPALDO SUFICIENTE');return 1 if args.publish else 0
    image_file=output/(article['id']+'.jpg')
    if photo is not None:make_card(photo,article,image_file)
    elif not image_file.is_file():
        # La portada remota original se conserva y es reutilizada por publish_article.
        image_file=None
    write_json(output/'noticia.json',{'article':article,'image':credit,'caption':description,'source_url':article.get('resolved_source_url') or article.get('url'),'source_name':article.get('source')})
    log('NOTICIA:',article['title'],'| DESARROLLO:',words(article['explanation']),'palabras')
    if not args.publish:
        write_json(output/'estado_publicacion.json',{'status':'preview_only','published':False,'article_id':article['id'],'image':str(image_file) if image_file else None,'checked_at':now().isoformat()})
        log('VISTA PREVIA COMPLETADA');return 0
    result=publish_article(gh,state,article,image_file,credit,description,output)
    if result['status']=='published':
        log('PUBLICACIÓN CONFIRMADA: INSTAGRAM FOTO + FACEBOOK FOTO + FACEBOOK POST PÚBLICO');return 0
    return 0 if result['status']=='already_published' else 1

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print('ERROR:',safe_error(exc),file=sys.stderr,flush=True)
        sys.exit(1)
