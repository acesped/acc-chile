#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA | noticias científicas con desarrollo real.

Python 3.11+; pip install requests pillow
Prueba: python noticias_sismonauta.py --test-filter
Previsualizar: python noticias_sismonauta.py --out output_noticias
Publicar: python noticias_sismonauta.py --publish --out output_noticias

No publica fichas de ocurrencias sísmicas. La imagen contiene solamente el titular; la descripción contiene el desarrollo completo.
No se muestra la fuente en el pie de la imagen ni en la descripción.
La URL y el medio se conservan en noticia.json para auditoría.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'SismonautaNews/5.2 (educational; https://www.nasa.gov/)' , 'Accept': '*/*'})
VERSION = os.getenv('IG_GRAPH_VERSION', 'v25.0')
IG_BASE = os.getenv('IG_GRAPH_BASE', 'https://graph.instagram.com').rstrip('/')
FB_BASE = 'https://graph.facebook.com'
STATE_FILE = 'sismonauta_noticias/state.json'
ACCOUNT = 'sismonauta'
LOOKBACK_HOURS = 60
MAX_ARTICLES = max(1, int(os.getenv('NEWS_MAX_ARTICLES', '350')))
W, H = 1080, 1350
MIN_SOURCE_WORDS = 85
MIN_DEVELOPMENT_WORDS = 80
HASHTAGS = '#SISMONAUTA #Ciencia #Actualidad #Naturaleza #Investigacion #PlanetaTierra #DivulgacionCientifica'

SEARCH_GROUPS = {
    'sismos': [('es','sismología investigación'),('es','fallas tectónicas'),('es','alerta sísmica tecnología'),('en','earthquake seismology research'),('en','seismic early warning research'),('en','tectonic fault science')],
    'volcanes': [('es','volcanes erupciones'),('es','actividad volcánica'),('es','vigilancia volcánica'),('en','volcano eruption'),('en','volcanic activity'),('en','volcano monitoring')],
    'meteorologia': [('es','meteorología tormentas'),('es','huracanes ciclones'),('es','fenómenos meteorológicos'),('en','meteorology weather research'),('en','hurricane cyclone'),('en','atmospheric science')],
    'climatologia': [('es','clima investigación'),('es','cambio climático'),('es','glaciares científicos'),('en','climate science'),('en','climate research'),('en','glacier research')],
    'oceanos': [('es','océanos investigación'),('es','tsunami océano'),('es','corrientes oceánicas'),('en','ocean science'),('en','oceanography discovery'),('en','sea level research')],
    'espacio': [('es','astronomía descubrimiento'),('es','NASA espacio'),('es','satélites observación terrestre'),('en','NASA space science'),('en','astronomy discovery'),('en','Earth observation satellite')],
    'desastres': [('es','desastres naturales'),('es','deslizamientos de tierra'),('es','inundaciones prevención'),('en','natural hazards'),('en','landslide flooding'),('en','disaster risk science')],
    'geologia': [('es','geología descubrimiento'),('es','geofísica investigación'),('es','formación de montañas'),('en','geology discovery'),('en','geophysics research'),('en','geological research')],
}
CATEGORY_PATTERNS = {
    'sismos': r'sism\w*|s[ií]smic\w*|seism\w*|earthquak\w*|terremot\w*|tect[oó]nic\w*|tectoni\w*|falla geol[oó]gica|fault zone|subduction|subducci[oó]n',
    'volcanes': r'volc[aá]n\w*|volcan\w*|volcanic\w*|erupci[oó]n\w*|eruption\w*|lava\b|magma\w*',
    'meteorologia': r'meteorolog\w*|weather\b|storm\w*|torment\w*|hurac[aá]n\w*|hurricane\w*|cicl[oó]n\w*|cyclone\w*|tornado\w*|atmosf[eé]ric\w*|atmospher\w*|rainfall|forecast\w*',
    'climatologia': r'climat\w*|climate\w*|calentamiento global|global warming|glaciar\w*|glacier\w*|criosfera|cryosphere|deshielo',
    'oceanos': r'oc[eé]an\w*|ocean\w*|oceanograf\w*|oceanograph\w*|tsunami\w*|maremot\w*|nivel del mar|sea level|seafloor|arrecife\w*',
    'espacio': r'astronom\w*|astronomy|espacio exterior|outer space|exploraci[oó]n espacial|space exploration|NASA\b|ESA\b|sat[eé]lit\w*|satellite\w*|planet\w*|galaxi\w*|galaxy|telescop\w*|asteroid\w*',
    'desastres': r'desastre\w* natural\w*|natural disaster\w*|deslizamiento\w*|landslide\w*|inundaci[oó]n\w*|flood\w*|alud\w*|avalanche\w*|incendio\w* forestal\w*|wildfire\w*|natural hazard\w*',
    'geologia': r'geolog\w*|ge[oó]log\w*|geophysic\w*|geof[ií]sic\w*|corteza terrestre|earth crust|rock formation\w*|geological\w*|geomorfolog\w*|continental drift',
}
CATEGORY_PATTERNS = {k: re.compile(v, re.I) for k,v in CATEGORY_PATTERNS.items()}
SPAM = re.compile(r'hor[oó]scop\w*|astrolog\w*|conspiracy|conspiraci[oó]n\w*|apocalipsis|lottery|apuesta\w*|oferta comercial', re.I)
MISLEADING = re.compile(r'predicen el pr[oó]ximo terremoto|exact earthquake prediction|fecha exacta del pr[oó]ximo sismo', re.I)

# BLOQUEO: reconocer especialmente "Terremoto moderado mag. 4.5 ...".
QUAKE_TERM = re.compile(r'\b(?:sismos?|tembl(?:or|ores)|terremotos?|earthquakes?|quakes?|seismic\s+(?:event|tremor|shock))\b', re.I)
QUAKE_MAGNITUDE = re.compile(r'(?<![a-z0-9])(?:mag(?:nitud|nitude)?\.?\s*(?:de\s+|of\s+)?[:=]?\s*[0-9](?:[.,][0-9]+)?|m(?:w|l|b|s)?\s*[:=]?\s*[3-9](?:[.,][0-9]+)?)\b', re.I)
QUAKE_REPORT = re.compile(r'\b(?:sacud\w*|remec\w*|ocurr\w*|registr\w*|report\w*|detect\w*|sentid\w*|sinti\w*|strik\w*|struck|hits?|shak\w*|shook|felt|occurred|recorded|detected|reported|hoy|today|ayer|yesterday)\b', re.I)
QUAKE_LOCATION = re.compile(r'\b(?:epicentro|hipocentro|profundidad|depth|km|kilometros?|near|offshore|cerca de|frente a|noreste|noroeste|sureste|suroeste|north|south|east|west|latitud|longitud|latitude|longitude)\b', re.I)
QUAKE_HEADLINE = re.compile(r'\b(?:nuevo|fuerte|moderado|leve|strong|moderate|minor|major)\s+(?:sismo|temblor|terremoto|earthquake|quake)\b|\b(?:sismo|temblor|terremoto|earthquake|quake)\s+(?:en|cerca|frente|near|off|in|at)\b', re.I)
QUAKE_RESEARCH = re.compile(r'\b(?:investig\w*|estudi\w*|cientific\w*|descubr\w*|hallazgo\w*|analis\w*|analiz\w*|alerta temprana|research\w*|stud(?:y|ies)|scientist\w*|discover\w*|finding\w*|early warning|new model|historic\w*|historico\w*)\b', re.I)
QUAKE_CATALOG_TITLE = re.compile(r'\b(?:moderado|moderate|fuerte|strong|leve|minor|light|weak)\b.{0,55}(?:\bmag\.?\s*[0-9]|\bmw\s*[0-9]|\bml\s*[0-9])', re.I)
QUAKE_DIRECT = re.compile(r'\b(?:sismo|temblor|terremoto|earthquake|quake)\b.{0,90}\b(?:sacude|remece|registrado|ocurre|ocurrio|reported|strikes|struck|hits|shakes)\b|\b(?:sacude|remece|registran|reportan|strikes|hits|shakes)\b.{0,90}\b(?:sismo|temblor|terremoto|earthquake|quake)\b', re.I)
QUAKE_CATALOG_SOURCES = ('volcanodiscovery','earthquake track','earthquake tracker','earthquake monitor','recent earthquakes','latest earthquakes')


def clean(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))).strip()


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',clean(value).lower()) if not unicodedata.combining(c))


def words(value):
    return len(clean(value).split())


def log(*args):
    print(*args, flush=True)


def now():
    return datetime.now(timezone.utc)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(path)


def safe_error(exc):
    message = str(exc)
    for name in ('GH_TOKEN','INSTAGRAM_TOKEN','FACEBOOK_PAGE_TOKEN','GEMINI_API_KEY','OPENROUTER_API_KEY','GROQ_API_KEY'):
        secret = os.getenv(name, '')
        if secret:
            message = message.replace(secret, '***')
    return message[:300]


def get(url, **kwargs):
    response = HTTP.get(url, timeout=kwargs.pop('timeout',25), **kwargs)
    response.raise_for_status()
    return response


def is_earthquake_occurrence(title, description='', source=''):
    title, description, source = normalized(title), normalized(description), normalized(source)
    combined = title + ' ' + description
    has_quake = bool(QUAKE_TERM.search(combined))
    scientific_title = bool(QUAKE_RESEARCH.search(title))
    catalog = any(x in source for x in QUAKE_CATALOG_SOURCES)
    if catalog and (has_quake or QUAKE_CATALOG_TITLE.search(title)) and not scientific_title:
        return True
    if QUAKE_TERM.search(title) and QUAKE_MAGNITUDE.search(title) and not scientific_title:
        return True
    if QUAKE_CATALOG_TITLE.search(title) and QUAKE_LOCATION.search(title) and not scientific_title:
        return True
    if QUAKE_TERM.search(title) and QUAKE_REPORT.search(title) and not scientific_title:
        return True
    if QUAKE_DIRECT.search(title) and not scientific_title:
        return True
    if QUAKE_HEADLINE.search(title) and not scientific_title:
        return True
    if scientific_title:
        return False
    if QUAKE_TERM.search(title) and (QUAKE_MAGNITUDE.search(description) or QUAKE_REPORT.search(description)) and (QUAKE_LOCATION.search(combined) or QUAKE_MAGNITUDE.search(combined)):
        return True
    return False


def test_earthquake_filter():
    blocked = [
        'Terremoto moderado mag. 4.5 - Océano Pacífico Norte, 230 km al noreste de Kamchatka',
        'Sismo magnitud 5.2 cerca de Santiago',
        'Terremoto Mw 6.1 frente a Japón',
        'Sismo M4.3 en el norte de Chile',
        'Temblor de magnitud 3,8 sacude Perú',
        'Earthquake M5.6 near Tokyo',
        'Moderate earthquake strikes California',
        'Sismo de magnitud 4.9 registrado en México',
        'Fuerte temblor en el norte de Chile',
        'Earthquake near Japan',
        'Terremoto de magnitud 5,0 con epicentro en Perú',
    ]
    allowed = [
        'Científicos estudian el terremoto de Valdivia de 1960',
        'Investigadores analizan un terremoto de magnitud 7',
        'Nuevo estudio explica las fallas tectónicas',
        'NASA descubre nuevas características de Marte',
        'Volcán presenta actividad eruptiva',
        'Investigadores analizan corrientes oceánicas',
        'Huracán se intensifica sobre el océano',
        'Nuevo sistema de alerta temprana para terremotos',
    ]
    for title in blocked:
        assert is_earthquake_occurrence(title), 'No bloqueó: ' + title
    for title in allowed:
        assert not is_earthquake_occurrence(title), 'Bloqueo incorrecto: ' + title
    log('FILTRO SISMICO VALIDADO:', len(blocked),'bloqueados;',len(allowed),'permitidos')


def classify(title, description, source=''):
    if is_earthquake_occurrence(title,description,source):
        return None
    content = clean(title) + ' ' + clean(description)
    if SPAM.search(content) or MISLEADING.search(content):
        return None
    matches = [category for category, pattern in CATEGORY_PATTERNS.items() if pattern.search(content)]
    for category in ('sismos','volcanes','oceanos','desastres','meteorologia','climatologia','geologia','espacio'):
        if category in matches:
            return category
    return None


# Fuentes directas que permiten recuperar el artículo sin el enlace
# intermedio de Google News. Los errores son independientes por feed.
DIRECT_FEEDS = (
    ('NASA publicaciones', 'https://www.nasa.gov/feed/'),
    ('NASA comunicados', 'https://www.nasa.gov/news-release/feed/'),
    ('NASA Tierra', 'https://www.nasa.gov/earth/feed/'),
    ('NASA ciencia', 'https://science.nasa.gov/feed/'),
    ('NASA tecnología', 'https://www.nasa.gov/technology/feed/'),
    ('NOAA noticias', 'https://www.noaa.gov/rss.xml'),
    ('ESA noticias', 'https://www.esa.int/rssfeed/Our_Activities/Space_News'),
    ('ESA ciencia espacial', 'https://www.esa.int/rssfeed/ESC'),
)


def direct_feed_items(cutoff, current):
    """Documentos directos con fecha y contenido del RSS, sin inventar."""
    results = []
    seen = set()
    for publisher, url in DIRECT_FEEDS:
        try:
            xml = ET.fromstring(get(url, timeout=20).content)
        except Exception as exc:
            log('RSS DIRECTO ERROR:', publisher, safe_error(exc)[:90])
            continue
        nodes = xml.findall('.//channel/item')
        if not nodes:
            nodes = xml.findall('.//{http://www.w3.org/2005/Atom}entry')
        for node in nodes[:70]:
            def node_text(*tags):
                for tag in tags:
                    value = node.findtext(tag)
                    if value:
                        return value.strip()
                return ''
            title = clean(node_text('title', '{http://www.w3.org/2005/Atom}title'))
            link = node_text('link')
            if not link:
                atom_link = node.find('{http://www.w3.org/2005/Atom}link')
                link = atom_link.get('href', '') if atom_link is not None else ''
            rawdate = node_text('pubDate', '{http://www.w3.org/2005/Atom}updated', '{http://www.w3.org/2005/Atom}published')
            try:
                date = parsedate_to_datetime(rawdate)
            except Exception:
                try:
                    date = datetime.fromisoformat(rawdate.replace('Z','+00:00'))
                except Exception:
                    continue
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            date = date.astimezone(timezone.utc)
            if not cutoff <= date <= current + timedelta(minutes=5):
                continue
            summary = clean(node_text('description', '{http://www.w3.org/2005/Atom}summary'))
            rawbody = node_text('{http://purl.org/rss/1.0/modules/content/}encoded', '{http://www.w3.org/2005/Atom}content')
            body = clean(rawbody)
            if not title or not link.startswith('https://'):
                continue
            category = classify(title, summary + ' ' + body[:650], publisher)
            if not category or is_earthquake_occurrence(title, summary, publisher):
                continue
            identifier = hashlib.sha256(re.sub(r'[^a-z0-9]','',normalized(title)).encode()).hexdigest()[:20]
            if identifier in seen:
                continue
            seen.add(identifier)
            results.append(dict(id=identifier,title=title,description=summary,url=link,source=publisher,
                date=date.isoformat(),lang='en',category=category,search_category='direct',source_body=body))
    log('FUENTES DIRECTAS: noticias elegibles:', len(results))
    return results


def feed_items():
    cutoff, current = now()-timedelta(hours=LOOKBACK_HOURS), now()
    output = {}
    audit = {'feeds_ok':0,'feeds_failed':0,'rss_items':0,'outside_window':0,'missing_date':0,'earthquake_occurrence':0,'not_relevant':0,'invalid':0,'accepted':0}
    for search_category, queries in SEARCH_GROUPS.items():
        for lang, query in queries:
            hl, gl, ceid = ('es-CL','CL','CL:es-419') if lang == 'es' else ('en-US','US','US:en')
            feed = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q':query+' when:3d','hl':hl,'gl':gl,'ceid':ceid})
            try:
                root = ET.fromstring(get(feed, timeout=20).content)
                audit['feeds_ok'] += 1
            except Exception as exc:
                audit['feeds_failed'] += 1
                log('RSS ERROR:',query,safe_error(exc)[:100]); continue
            entries = root.findall('.//channel/item')[:80]
            log('RSS:',search_category,lang,query,'resultados:',len(entries))
            for node in entries:
                audit['rss_items'] += 1
                title = clean(node.findtext('title'))
                description = clean(node.findtext('description'))
                url = clean(node.findtext('link'))
                provider = clean(node.findtext('source'))
                if ' - ' in title:
                    prefix, suffix = title.rsplit(' - ',1)
                    if not provider or normalized(suffix)==normalized(provider):
                        title = prefix.strip()
                try:
                    date = parsedate_to_datetime(node.findtext('pubDate') or '')
                    if not date.tzinfo:
                        date = date.replace(tzinfo=timezone.utc)
                    date = date.astimezone(timezone.utc)
                except Exception:
                    audit['missing_date'] += 1; continue
                if not (cutoff <= date <= current + timedelta(minutes=5)):
                    audit['outside_window'] += 1; continue
                if is_earthquake_occurrence(title,description,provider):
                    audit['earthquake_occurrence'] += 1
                    log('DESCARTADO - OCURRENCIA SISMICA:',title[:120]); continue
                if words(title)<4 or not url.startswith('https://'):
                    audit['invalid'] += 1; continue
                category = classify(title,description,provider)
                if not category:
                    audit['not_relevant'] += 1; continue
                identifier = hashlib.sha256(re.sub(r'[^a-z0-9]','',normalized(title)).encode()).hexdigest()[:20]
                if identifier in output:
                    continue
                output[identifier] = dict(id=identifier,title=title,description=description,url=url,source=provider,date=date.isoformat(),lang=lang,category=category,search_category=search_category)
                audit['accepted'] += 1
    for entry in direct_feed_items(cutoff, current):
        old = output.get(entry['id'])
        if not old or len(entry.get('source_body','')) > len(old.get('source_body','')):
            output[entry['id']] = entry
    log('AUDITORIA RSS:',json.dumps(audit,ensure_ascii=False))
    if audit['feeds_ok']==0 and not output:
        raise RuntimeError('No fue posible consultar ninguna fuente RSS')
    return list(output.values())


def priority(article):
    weight = {'sismos':9,'volcanes':8,'oceanos':7,'desastres':7,'meteorologia':6,'climatologia':6,'geologia':6,'espacio':5}
    text = normalized(article['title']+' '+article['description'])
    score = weight.get(article['category'],0)
    for term,value in [('investigacion',5),('research',5),('descubrimiento',5),('discovery',5),('nasa',4),('noaa',4),('tecnologia',4),('technology',4),('estudio',4),('study',4),('volcan',3),('eruption',3),('huracan',3),('tsunami',3)]:
        if term in text:
            score += value
    age = max(0,(now()-datetime.fromisoformat(article['date'])).total_seconds()/3600)
    return score + max(0,LOOKBACK_HOURS-age)*0.18 + min(5,words(article['description'])/12) + (7 if source_text_sufficient(article.get('source_body','')) else 0)


def ai_text(prompt,max_tokens=1300):
    key=os.getenv('GEMINI_API_KEY','').strip()
    if key:
        try:
            model=os.getenv('GEMINI_NEWS_MODEL','gemini-2.5-flash')
            response=HTTP.post('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',headers={'x-goog-api-key':key},json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'temperature':0.15,'maxOutputTokens':max_tokens}},timeout=65)
            response.raise_for_status()
            result='\n\n'.join(p.get('text','') for p in response.json()['candidates'][0]['content']['parts']).strip()
            if result: return result
        except Exception as exc:
            log('Gemini no disponible:',safe_error(exc)[:115])
    key=os.getenv('OPENROUTER_API_KEY','').strip()
    if key:
        try:
            response=HTTP.post('https://openrouter.ai/api/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':os.getenv('OPENROUTER_NEWS_MODEL','openrouter/free'),'temperature':0.15,'max_tokens':max_tokens,'messages':[{'role':'user','content':prompt}]},timeout=65)
            response.raise_for_status()
            result=response.json()['choices'][0]['message']['content']
            if isinstance(result,str) and result.strip(): return result.strip()
        except Exception as exc:
            log('OpenRouter no disponible:',safe_error(exc)[:115])
    key=os.getenv('GROQ_API_KEY','').strip()
    if key:
        try:
            response=HTTP.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':os.getenv('GROQ_NEWS_MODEL','llama-3.3-70b-versatile'),'temperature':0.15,'max_tokens':max_tokens,'messages':[{'role':'user','content':prompt}]},timeout=65)
            response.raise_for_status()
            result=response.json()['choices'][0]['message']['content']
            if isinstance(result,str) and result.strip(): return result.strip()
        except Exception as exc:
            log('Groq no disponible:',safe_error(exc)[:115])
    return None


def translate_es(value):
    value=clean(value)
    if not value: return ''
    prompt='Traduce fielmente al español de Chile sin agregar datos ni cambiar nombres o cifras. Devuelve solo la traducción:\n\n'+value
    result=ai_text(prompt,650)
    if result and len(result)>10: return clean(result)
    try:
        response=get('https://translate.googleapis.com/translate_a/single',params={'client':'gtx','sl':'auto','tl':'es','dt':'t','q':value},timeout=18)
        result=clean(''.join(part[0] for part in response.json()[0] if part and part[0]))
        if len(result)>10: return result
    except Exception: pass
    return None


class ArticleParser(HTMLParser):
    """Extrae párrafos visibles, omitiendo zonas de navegación."""
    SKIP={'script','style','nav','footer','header','aside','noscript'}
    def __init__(self):
        super().__init__(); self.block=0; self.in_p=False; self.parts=[]; self.paragraphs=[]
    def handle_starttag(self,tag,attrs):
        if tag in self.SKIP: self.block+=1
        elif tag=='p' and self.block==0: self.in_p=True; self.parts=[]
    def handle_endtag(self,tag):
        if tag in self.SKIP: self.block=max(0,self.block-1)
        elif tag=='p' and self.in_p:
            paragraph=clean(' '.join(self.parts))
            if len(paragraph)>55: self.paragraphs.append(paragraph)
            self.in_p=False; self.parts=[]
    def handle_data(self,data):
        if self.in_p and self.block==0: self.parts.append(data)


def full_article_text(article):
    direct = article.get('source_body', '')
    if source_text_sufficient(direct):
        return direct[:18000]
    url=article.get('url','')
    if not url.startswith('https://'): return ''
    try:
        response=get(url,timeout=20,headers={'Accept':'text/html'})
        if 'html' not in response.headers.get('Content-Type','').lower() or len(response.content)>3_000_000: return ''
        parser=ArticleParser(); parser.feed(response.text)
        paras=[]; seen=set()
        for paragraph in parser.paragraphs:
            key=normalized(paragraph)[:100]
            if key in seen or re.search(r'cookies|privacy policy|subscribe|advertisement',paragraph,re.I): continue
            seen.add(key); paras.append(paragraph)
        return ' '.join(paras)[:18000]
    except Exception as exc:
        log('ARTICULO NO ACCESIBLE:',type(exc).__name__)
        return ''


def source_text_sufficient(text):
    """El respaldo editorial no puede reducirse al titular o al RSS."""
    if words(text)<MIN_SOURCE_WORDS: return False
    sentences=[s for s in re.split(r'(?<=[.!?])\s+',clean(text)) if words(s)>=8]
    return len({normalized(s) for s in sentences})>=4


def deterministic_development(text):
    """Respaldo extractivo: no agrega hechos, no inventa conectores causales."""
    paras=[]; seen=set(); total=0
    for raw in re.split(r'(?<=[.!?])\s+|\n+',clean(text)):
        phrase=clean(raw)
        if words(phrase)<8 or words(phrase)>85: continue
        fp=normalized(phrase)
        if fp in seen or re.search(r'cookies|subscribe|advertisement|privacy policy',phrase,re.I): continue
        seen.add(fp); paras.append(phrase); total+=words(phrase)
        if total>=165: break
    if total<MIN_DEVELOPMENT_WORDS: return None
    return '\n\n'.join(' '.join(paras[i:i+2]) for i in range(0,len(paras),2))


def validate_development(article,source_text):
    title=normalized(article.get('title',''))
    explanation=clean(article.get('explanation',''))
    if not source_text_sufficient(source_text): return False,'Fuente sin contenido suficiente'
    if words(explanation)<MIN_DEVELOPMENT_WORDS: return False,'Desarrollo inferior a 80 palabras'
    if normalized(explanation)==title: return False,'El desarrollo repite el titular'
    sentences=[s for s in re.split(r'(?<=[.!?])\s+',explanation) if words(s)>=5]
    if len({normalized(s) for s in sentences})<3: return False,'Faltan hechos desarrollados'
    if 'INSUFICIENTE' in explanation.upper(): return False,'Contenido insuficiente'
    return True,'Desarrollo informativo válido'


def obtain_development(article,source_text):
    """Prioriza la redacción original; solo admite extracto fiel como respaldo."""
    if not source_text_sufficient(source_text): return None
    prompt=(
        'Eres periodista científico de SISMONAUTA. Redacta una noticia en español de Chile, '
        'con desarrollo de aproximadamente 120 a 190 palabras y varios párrafos. '
        'Explica el hecho concreto, contexto, resultados y consecuencias SOLO cuando estén documentados. '
        'Conserva cifras, nombres, fechas y lugares exactamente. No inventes, no rellenes, no copies '
        'párrafos extensos ni añadas fuentes, enlaces, hashtags o notas editoriales. '
        'Si no hay datos suficientes, responde INSUFICIENTE. '
        'No conviertas una investigación sísmica en reporte de ocurrencia.\n\n'
        f'TITULAR: {article["title"]}\nCATEGORIA: {article["category"]}\n'
        f'TEXTO DOCUMENTAL:\n{source_text[:15500]}'
    )
    result=ai_text(prompt,1350)
    if result and 'INSUFICIENTE' not in result.upper():
        candidate=dict(article,explanation=result.strip())
        if validate_development(candidate,source_text)[0]: return result.strip()
    fallback=deterministic_development(source_text)
    if fallback and validate_development(dict(article,explanation=fallback),source_text)[0]:
        log('REDACCION EXTRACTIVA: fuente suficiente; IA no disponible o texto descartado')
        return fallback
    return None


PHOTO_QUERIES={
    'sismos':['earthquake fault landscape','San Andreas Fault','mountain rock landscape'],
    'volcanes':['volcano landscape','volcanic eruption','volcanic mountain'],
    'meteorologia':['storm clouds landscape','hurricane satellite','thunderstorm clouds'],
    'climatologia':['glacier landscape','polar ice landscape','mountain glacier'],
    'oceanos':['ocean coastline','sea waves shore','coastal landscape'],
    'espacio':['NASA Earth from space','Hubble galaxy','planet astronomy'],
    'desastres':['landslide mountain','flooded river landscape','forest wildfire'],
    'geologia':['rock formations landscape','mountain geology','geological fault'],
}


def commons_license_accepted(meta):
    values=[clean((meta.get(k) or {}).get('value','')).lower() for k in ('LicenseShortName','License','UsageTerms')]
    combined=' '.join(values)
    return any(x in combined for x in ('cc0','public domain','publicdomain','pdm','pd-old','pd-us','pd-self'))


def commons_photo(article):
    candidates=[]; seen=set()
    for query in PHOTO_QUERIES.get(article['category'],PHOTO_QUERIES['geologia']):
        params={'action':'query','format':'json','formatversion':2,'generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':40,'prop':'imageinfo','iiprop':'url|extmetadata|mime|size','iiurlwidth':1500}
        continuation={}
        for _ in range(2):
            try:
                data=get('https://commons.wikimedia.org/w/api.php',params={**params,**continuation},timeout=25).json()
            except Exception as exc:
                log('COMMONS ERROR:',query,safe_error(exc)[:90]); break
            pages=(data.get('query') or {}).get('pages',[])
            if isinstance(pages,dict): pages=list(pages.values())
            for page in pages:
                infos=page.get('imageinfo') or []
                if not infos: continue
                info=infos[0]; meta=info.get('extmetadata') or {}
                if not commons_license_accepted(meta): continue
                title=clean(page.get('title',''))
                if not re.search(r'\.(?:jpg|jpeg|png|webp)$',title,re.I): continue
                if info.get('mime') not in ('image/jpeg','image/png','image/webp'): continue
                if min(int(info.get('width') or 0),int(info.get('height') or 0))<450: continue
                if re.search(r'diagram|infographic|schematic|drawing|illustration|logo|poster|chart|presentation|screenshot',title,re.I): continue
                url=info.get('thumburl') or info.get('url')
                if not url or url in seen: continue
                seen.add(url)
                candidates.append({'title':title,'url':url,'source':info.get('descriptionurl',''),'license':clean((meta.get('LicenseShortName') or {}).get('value','')),'generated':False,'query':query})
            continuation=data.get('continue')
            if not continuation: break
        if candidates: break
    for item in candidates[:18]:
        try:
            response=get(item['url'],timeout=30)
            if len(response.content)>15_000_000: continue
            with Image.open(io.BytesIO(response.content)) as im:
                image=im.convert('RGB')
            if min(image.size)<450: continue
            log('FOTO COMMONS:',item['title'][:90]); return image,item
        except Exception: continue
    return None,None


def original_background(article):
    seed=int(hashlib.sha256(article['id'].encode()).hexdigest()[:12],16)
    rng=random.Random(seed)
    image=Image.new('RGB',(W,H),(5,27,53)); d=ImageDraw.Draw(image)
    for y in range(H):
        p=y/H; d.line((0,y,W,y),fill=(int(8+17*p),int(42+32*p),int(83+30*p)))
    for layer in range(7):
        phase=rng.uniform(0,math.tau); base=425+layer*116; points=[]
        for x in range(-20,W+40,16): points.append((x,int(base+65*math.sin(x/160+phase)+22*math.sin(x/37+phase/2))))
        d.polygon(points+[(W,H),(0,H)],fill=(10+layer*8,55+layer*8,87+layer*4))
    if article['category'] in ('espacio','meteorologia','climatologia'):
        for _ in range(75):
            x=rng.randrange(W); y=rng.randrange(100,H-150); r=rng.randint(1,4)
            d.ellipse((x-r,y-r,x+r,y+r),fill=(170,210,240))
    return image,{'title':'Arte original SISMONAUTA','url':'','license':'Obra original','generated':True}


def font(size,bold=False):
    choices=(['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'] if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    for path in choices:
        if Path(path).exists(): return ImageFont.truetype(path,size)
    return ImageFont.load_default()


def wrap(draw,text,ft,width):
    lines=[]; line=''
    for word in clean(text).split():
        trial=(line+' '+word).strip()
        if draw.textbbox((0,0),trial,font=ft)[2]<=width: line=trial
        else:
            if line: lines.append(line)
            line=word
    if line: lines.append(line)
    return lines


def image_summary(text,min_words=45,max_words=75):
    """Resumen extractivo: toma 45-75 palabras del desarrollo validado."""
    phrases=re.split(r'(?<=[.!?])\s+',clean(text))
    picked=[]; total=0
    for sentence in phrases:
        count=words(sentence)
        if not count: continue
        if total+count>max_words:
            remaining=max_words-total
            if total<min_words and remaining>=5:
                picked.append(' '.join(sentence.split()[:remaining]).rstrip(',;:')+'…')
            break
        picked.append(sentence); total+=count
        if total>=min_words: break
    summary=' '.join(picked).strip()
    if words(summary)<min_words:
        summary=' '.join(clean(text).split()[:max_words]).rstrip(',;:')+'…'
    return summary

def sanitize_explanation(text,title=''):
    text=clean(text)
    if not text:
        return ''
    text=re.sub(r'^\*\*\s*TITULAR\s*:\s*', '', text, flags=re.I)
    text=re.sub(r'^TITULAR\s*:\s*', '', text, flags=re.I)
    text=re.sub(r'^\*\*\s*', '', text)
    text=re.sub(r'\s*\*\*\s*', ' ', text)
    text=clean(text)
    if title:
        title_clean=clean(title)
        escaped=re.escape(title_clean)
        text=re.sub(r'^(?:\*\*\s*)?(?:TITULAR\s*:\s*)?'+escaped+r'(?:\s*\*\*)?[\s:–—.-]*', '', text, flags=re.I)
    return clean(text)


# ============================================================
# TITULARES DE IMPACTO, SIN CLICKBAIT NI DATOS INVENTADOS
# ============================================================

HEADLINE_MIN_WORDS = 5
HEADLINE_MAX_WORDS = 19
HEADLINE_MAX_CHARS = 128
HEADLINE_FORBIDDEN = re.compile(
    r'(?i)\b(?:no\s+vas\s+a\s+creer|incre[ií]ble|impactante|'
    r'shock|aterrador|apocalipsis|la\s+verdad\s+oculta|'
    r'nadie\s+te\s+cuenta|te\s+dejar[aá]\s+sin\s+palabras)\b'
)


def headline_numbers(text):
    # Compara las cifras de modo conservador; no autoriza números nuevos.
    return set(re.findall(r'(?<!\w)\d+(?:[.,]\d+)?(?!\w)', clean(text)))


def headline_valid(candidate, article, source_text):
    candidate = clean(candidate).strip('\"“”«»*# ')
    if not (HEADLINE_MIN_WORDS <= words(candidate) <= HEADLINE_MAX_WORDS):
        return False
    if len(candidate) > HEADLINE_MAX_CHARS or '\n' in candidate:
        return False
    if HEADLINE_FORBIDDEN.search(candidate) or 'TITULAR:' in candidate.upper():
        return False
    if re.search(r'https?://|#[A-Za-z]|\b(?:urgente|última hora)\b', candidate, re.I):
        return False
    original = article.get('original_title') or article.get('title') or ''
    evidence = ' '.join((original, article.get('description', ''), source_text))
    if not headline_numbers(candidate).issubset(headline_numbers(evidence)):
        return False
    if is_earthquake_occurrence(candidate, article.get('description',''), article.get('source','')):
        return False
    return True


def factual_headline(article, source_text):
    """Prioriza una noticia concreta y comprobada, no un titular genérico.

    Si la IA falla, conserva el título periodístico existente: evita fabricar
    afirmaciones por medio de plantillas o preguntas especulativas.
    """
    original = clean(article.get('title', ''))
    prompt = (
        'Eres editor de titulares científicos de SISMONAUTA para Instagram. '
        'Reescribe el titular como una frase de impacto informativo, precisa y '
        'muy clara, de 7 a 16 palabras (máximo 128 caracteres). '
        'La primera lectura debe contar QUÉ sucedió y POR QUÉ importa. '
        'Prioriza el resultado concreto, el descubrimiento, una cifra documentada '
        'o la consecuencia demostrada. Usa verbos activos y lenguaje sencillo. '
        'Mantén la cautela científica (podría, sugiere, según el estudio). '
        'NO exageres, NO inventes cifras, peligros, conclusiones ni predicciones, '
        'NO uses preguntas vacías, sensacionalismo, hashtags, comillas ni emojis. '
        'No conviertas investigaciones sísmicas en avisos de terremotos ocurridos. '
        'Responde ÚNICAMENTE con el titular final, en una sola línea.\n\n'
        'TITULAR PERIODÍSTICO: ' + original + '\n'
        'DESCRIPCIÓN DOCUMENTADA: ' + clean(article.get('description',''))[:850] + '\n'
        'DESARROLLO VERIFICADO: ' + clean(article.get('explanation',''))[:2600] + '\n'
        'FUENTE DOCUMENTAL: ' + clean(source_text)[:4300]
    )
    result = ai_text(prompt, 170)
    if result:
        candidate = clean(result.splitlines()[0]).strip('\"“”«»*# ')
        candidate = re.sub(r'^(?:titular|título|headline)\s*:\s*', '', candidate, flags=re.I)
        if headline_valid(candidate, article, source_text):
            log('TITULAR DE IMPACTO VALIDADO:', candidate)
            return candidate
        log('TITULAR IA DESCARTADO: no cumple longitud, seguridad o cifras')
    log('TITULAR ORIGINAL CONSERVADO: sin reescritura verificable')
    return original


def test_headlines():
    base = dict(title='Investigadores estudian el comportamiento de los glaciares',
                description='El estudio observa el retroceso de glaciares',
                category='climatologia', source='Instituto científico')
    evidence = 'Un estudio documentó el retroceso de los glaciares en las últimas décadas.'
    assert headline_valid('El estudio documenta el retroceso de los glaciares', base, evidence)
    assert not headline_valid('Los glaciares retroceden 89 kilómetros en una semana', base, evidence)
    assert not headline_valid('No vas a creer lo que ocurre con los glaciares', base, evidence)
    assert not headline_valid('Terremoto magnitud 6.5 cerca de Santiago', base, evidence)
    assert not headline_valid('¡Impactante!', base, evidence)
    log('PRUEBAS TITULARES: seguridad, hechos y legibilidad correctos')


def make_card(image,article,path):
    """Tarjeta solo con titular. El desarrollo va exclusivamente en el pie."""
    canvas=ImageOps.fit(image,(W,H),Image.Resampling.LANCZOS,centering=(0.5,0.44)).convert('RGBA')
    veil=Image.new('RGBA',(W,H),(0,0,0,0)); draw=ImageDraw.Draw(veil)
    draw.rectangle((0,0,W,112),fill=(4,21,46,245))
    draw.text((54,32),'SISMONAUTA  |  ACTUALIDAD',font=font(36,True),fill='white')
    title=clean(article.get('image_headline') or article['title'])
    top=640; bottom=H-90
    draw.rectangle((0,top,W,bottom),fill=(3,29,58,244))
    draw.rectangle((0,top,W,top+10),fill=(36,163,255,255))
    title_font=None; title_lines=[]; title_step=0
    for size in range(72,28,-2):
        ft=font(size,True); lines=wrap(draw,title,ft,W-120); step=int(size*1.20)
        if len(lines)<=6 and len(lines)*step<=((bottom-top)-90):
            title_font,title_lines,title_step=ft,lines,step; break
    if title_font is None: raise ValueError('Titular demasiado largo para tarjeta')
    block_height=len(title_lines)*title_step
    y=top+((bottom-top)-block_height)//2
    for line in title_lines:
        bbox=draw.textbbox((0,0),line,font=title_font,stroke_width=3)
        x=(W-(bbox[2]-bbox[0]))/2
        draw.text((x,y),line,font=title_font,fill='white',stroke_width=3,stroke_fill='black')
        y+=title_step
    draw.rectangle((0,H-90,W,H),fill=(3,24,49,245))
    category={'sismos':'CIENCIA SÍSMICA','volcanes':'VOLCANES','meteorologia':'METEOROLOGÍA','climatologia':'CLIMA','oceanos':'OCÉANOS','espacio':'ESPACIO','desastres':'NATURALEZA','geologia':'GEOLOGÍA'}.get(article['category'],'CIENCIA')
    draw.text((54,H-63),'CIENCIA  •  '+category,font=font(27,True),fill='white')
    result=Image.alpha_composite(canvas,veil).convert('RGB')
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    result.save(path,'JPEG',quality=91,optimize=True)



CATEGORY_HASHTAGS={
    'sismos':'#Sismos #Terremotos #Sismologia #Geofisica #Tectonica',
    'volcanes':'#Volcanes #Erupciones #Volcanologia #Geologia',
    'meteorologia':'#Meteorologia #Tiempo #Tormentas #Atmosfera',
    'climatologia':'#Climatologia #Clima #CambioClimatico #Glaciares',
    'oceanos':'#Oceanos #Oceanografia #Tsunamis #CienciaDelMar',
    'espacio':'#Espacio #Astronomia #NASA #ExploracionEspacial',
    'desastres':'#DesastresNaturales #Prevencion #RiesgosNaturales',
    'geologia':'#Geologia #PlanetaTierra #Geociencias #Investigacion',
}


def caption(article,credit):
    """La fuente no se muestra y el titular aparece una sola vez."""
    title=clean(article.get('image_headline') or article['title'])
    explanation=sanitize_explanation(article.get('explanation',''),article.get('title',''))
    explanation=sanitize_explanation(explanation,title)
    if words(explanation)<MIN_DEVELOPMENT_WORDS: raise ValueError('Falta desarrollo de 80 palabras')
    category=article['category']
    label={'sismos':'CIENCIA SÍSMICA','volcanes':'ACTUALIDAD VOLCÁNICA','meteorologia':'METEOROLOGÍA','climatologia':'CLIMATOLOGÍA','oceanos':'CIENCIAS DEL OCÉANO','espacio':'CIENCIA ESPACIAL','desastres':'RIESGOS NATURALES','geologia':'CIENCIAS DE LA TIERRA'}.get(category,'ACTUALIDAD CIENTÍFICA')
    footer='\n\n💬 ¿Qué opinas? Te leemos en los comentarios.\n\n'+HASHTAGS+' '+CATEGORY_HASHTAGS.get(category,'')
    header='🌎 SISMONAUTA | '+label+'\n\n'+title+'\n\n'
    available=2200-len(header)-len(footer)
    if available<300: raise ValueError('Titular demasiado largo')
    if len(explanation)>available:
        explanation=explanation[:available].rsplit(' ',1)[0].rstrip(',;:')+'.'
    if words(explanation)<MIN_DEVELOPMENT_WORDS: raise ValueError('Descripción no permite desarrollo válido')
    return header+explanation+footer



class Github:
    def __init__(self):
        self.repo=os.environ['GITHUB_REPOSITORY']
        self.branch=os.getenv('GITHUB_REF_NAME') or 'main'
        self.base='https://api.github.com/repos/'+self.repo
        self.session=requests.Session()
        self.session.headers.update({'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
        response=self.session.get(self.base,timeout=20); response.raise_for_status()
        if response.json().get('private'): raise RuntimeError('Se requiere repositorio público para alojar imágenes')
    def endpoint(self,path):
        return self.base+'/contents/'+urllib.parse.quote(path,safe='/')
    def read(self,path):
        response=self.session.get(self.endpoint(path),params={'ref':self.branch},timeout=25)
        if response.status_code==404: return None,None
        response.raise_for_status(); data=response.json()
        return base64.b64decode(data['content']),data['sha']
    def write(self,path,raw):
        for attempt in range(4):
            _,sha=self.read(path)
            body={'message':'SISMONAUTA noticias: '+path,'branch':self.branch,'content':base64.b64encode(raw).decode('ascii')}
            if sha: body['sha']=sha
            response=self.session.put(self.endpoint(path),json=body,timeout=60)
            if response.status_code in (409,422) and attempt<3:
                time.sleep(attempt+2); continue
            response.raise_for_status(); return response.json()
    def image_url(self,path):
        return 'https://raw.githubusercontent.com/'+self.repo+'/'+urllib.parse.quote(self.branch,safe='')+'/'+urllib.parse.quote(path,safe='/')


def graph(method,path,token,params=None,base=None):
    url=f'{base or IG_BASE}/{VERSION}/{path}'
    fields={**(params or {}),'access_token':token}
    response=requests.get(url,params=fields,timeout=55) if method=='GET' else requests.post(url,data=fields,timeout=75)
    try: data=response.json()
    except ValueError: raise RuntimeError('Meta no devolvió JSON')
    if not response.ok or data.get('error'):
        error=data.get('error') or {}
        raise RuntimeError('Meta HTTP '+str(response.status_code)+': '+str(error.get('message',data))[:240])
    return data


def ig_check():
    token=os.environ['INSTAGRAM_TOKEN']; user=os.environ['INSTAGRAM_USER_ID']
    info=graph('GET','me',token,{'fields':'id,username'})
    if str(info.get('id'))!=str(user) or str(info.get('username','')).lower()!=ACCOUNT: raise RuntimeError('Cuenta Instagram incorrecta')
    log('Instagram @sismonauta verificado'); return user,token


def fb_check():
    page=os.environ['FACEBOOK_PAGE_ID']; token=os.environ['FACEBOOK_PAGE_TOKEN']
    info=graph('GET',page,token,{'fields':'id,name,username'},FB_BASE)
    if str(info.get('id'))!=str(page) or (info.get('username') and str(info['username']).lower()!=ACCOUNT): raise RuntimeError('Fanpage Facebook incorrecta')
    log('Facebook validado:',info.get('name')); return page,token


def publish_ig(user,token,image_url,text):
    container=graph('POST',user+'/media',token,{'image_url':image_url,'caption':text})['id']
    for _ in range(18):
        time.sleep(8)
        status=graph('GET',container,token,{'fields':'status_code'}).get('status_code')
        if status=='FINISHED': break
        if status in ('ERROR','EXPIRED'): raise RuntimeError('Instagram rechazó imagen: '+str(status))
    else: raise RuntimeError('Instagram no confirmó contenedor')
    result=graph('POST',user+'/media_publish',token,{'creation_id':container})
    if not result.get('id'): raise RuntimeError('Instagram no devolvió ID')
    return str(result['id'])


def publish_fb(page,token,image_url,text):
    result=graph('POST',page+'/photos',token,{'url':image_url,'caption':text,'published':'true'},FB_BASE)
    identifier=result.get('post_id') or result.get('id')
    if not identifier: raise RuntimeError('Facebook no devolvió ID')
    return str(identifier)


def load_state(gh):
    raw,_=gh.read(STATE_FILE)
    state=json.loads(raw) if raw else {'stories':{}}
    state.setdefault('stories',{})
    return state


def save_state(gh,state):
    gh.write(STATE_FILE,json.dumps(state,ensure_ascii=False,indent=2,sort_keys=True).encode())


def targets_of(entry):
    targets=entry.setdefault('targets',{})
    for name in ('instagram','facebook'):
        targets.setdefault(name,{'status':'pending'})
    return targets


def is_confirmed(entry):
    return all(entry.get('targets',{}).get(p,{}).get('status')=='published' for p in ('instagram','facebook'))


def has_uncertain(entry):
    return any(entry.get('targets',{}).get(p,{}).get('status')=='uncertain' for p in ('instagram','facebook'))


def recent_published_categories(state, limit=12):
    """Categorías recientes efectivamente publicadas en las dos plataformas."""
    rows = []
    for entry in (state.get('stories') or {}).values():
        if not isinstance(entry, dict) or not is_confirmed(entry):
            continue
        article = entry.get('article') or {}
        category = article.get('category')
        if category in SEARCH_GROUPS:
            stamp = (entry.get('published_at') or entry.get('checked_at')
                     or article.get('date') or '')
            rows.append((str(stamp), category))
    rows.sort(reverse=True)
    return [category for _, category in rows[:limit]]


def diverse_order(articles, state):
    """Conserva prioridad científica, pero favorece temas no publicados recientemente.

    No excluye NASA, ni impone cuotas si faltan noticias verificables.
    """
    recent = recent_published_categories(state)
    recent_counts = {name: recent.count(name) for name in SEARCH_GROUPS}
    last = recent[0] if recent else None
    def rank(article):
        cat = article.get('category')
        score = priority(article)
        score -= 9 * recent_counts.get(cat, 0)
        if article.get('search_category') == 'direct' and not article.get('source', '').startswith('NASA'):
            score += 7
        if cat == last:
            score -= 18
        if cat not in recent:
            score += 16
        # Una ficha con cuerpo accesible es preferible, sin monopolizar el ranking.
        return score
    ordered = sorted(articles, key=rank, reverse=True)
    log('DIVERSIDAD EDITORIAL:', json.dumps({
        'ultimas_categorias': recent[:8],
        'candidatos_por_categoria': {name: sum(a.get('category') == name for a in articles) for name in SEARCH_GROUPS},
        'primeras_categorias': [a.get('category') for a in ordered[:12]],
    }, ensure_ascii=False))
    return ordered


def select_news(articles,state):
    reasons={'earthquake_occurrence':0,'already_published':0,'uncertain_previous':0,'translation_failed':0,'source_too_short':0,'no_development':0,'invalid_caption':0,'generated_backgrounds':0}
    for original in articles[:MAX_ARTICLES]:
        if is_earthquake_occurrence(original['title'],original.get('description',''),original.get('source','')):
            reasons['earthquake_occurrence']+=1; continue
        existing=state.get('stories',{}).get(original['id'])
        if existing and is_confirmed(existing): reasons['already_published']+=1; continue
        if existing and has_uncertain(existing): reasons['uncertain_previous']+=1; continue
        article=dict(original)
        if article['lang']=='en':
            translated=translate_es(article['title'])
            if not translated: reasons['translation_failed']+=1; continue
            article['original_title']=article['title']; article['title']=translated
            article['description']=translate_es(article['description']) or article['description']
        if is_earthquake_occurrence(article['title'],article['description'],article.get('source','')):
            reasons['earthquake_occurrence']+=1; continue
        log('EVALUANDO:',article['title'][:110],'| categoria:',article['category'])
        # Nunca construir una noticia completa únicamente con el RSS.
        source_text=full_article_text(original)
        if not source_text_sufficient(source_text):
            reasons['source_too_short']+=1
            log('DESCARTADA: artículo fuente sin al menos 85 palabras y cuatro oraciones'); continue
        # Traducir el cuerpo completo solo si se redactara sin IA.
        development=obtain_development(article,source_text)
        # Un respaldo extractivo en ingles requiere traduccion confirmada.
        if development and original['lang']=='en' and not (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or os.getenv('GROQ_API_KEY')):
            development=translate_es(development)
        if not development:
            reasons['no_development']+=1; log('DESCARTADA: desarrollo informativo insuficiente'); continue
        article['explanation']=development
        valid,reason=validate_development(article,source_text)
        if not valid:
            reasons['no_development']+=1; log('DESCARTADA:',reason); continue
        article['image_headline']=factual_headline(article,source_text)
        photo,credit=commons_photo(original)
        if photo is None:
            photo,credit=original_background(article)
            reasons['generated_backgrounds']+=1
            log('FONDO GRÁFICO ORIGINAL')
        try: description=caption(article,credit)
        except ValueError as exc:
            reasons['invalid_caption']+=1; log('DESCARTADA:',str(exc)); continue
        return article,photo,credit,description,reasons
    return None,None,None,None,reasons


def publish_article(gh,state,article,image_file,credit,description,output):
    for title in (article.get('original_title'),article['title']):
        if title and is_earthquake_occurrence(title,article.get('description',''),article.get('source','')):
            raise RuntimeError('PUBLICACION BLOQUEADA: reporte de ocurrencia sísmica')
    if words(article.get('explanation',''))<MIN_DEVELOPMENT_WORDS:
        raise RuntimeError('PUBLICACION BLOQUEADA: desarrollo insuficiente')
    instagram_id,instagram_token=ig_check()
    facebook_id,facebook_token=fb_check()
    article_id=article['id']
    entry=state['stories'].setdefault(article_id,{'article':article,'image':credit,'targets':{}})
    targets=targets_of(entry)
    if is_confirmed(entry): return {'status':'already_published','article_id':article_id,'targets':targets}
    if has_uncertain(entry): raise RuntimeError('Publicación previa incierta: revisar Meta antes de reintentar')
    remote='sismonauta_noticias/images/'+article_id+'.jpg'
    gh.write(remote,image_file.read_bytes())
    image_url=gh.image_url(remote)
    accessible=False
    for _ in range(8):
        try:
            response=HTTP.get(image_url,timeout=25)
            if response.ok and response.headers.get('Content-Type','').lower().startswith('image/'):
                accessible=True; break
        except requests.RequestException: pass
        time.sleep(3)
    if not accessible: raise RuntimeError('Imagen no accesible para Meta')
    entry.update(article=article,image=credit,image_url=image_url)
    save_state(gh,state)
    failures=[]
    for platform in ('instagram','facebook'):
        row=targets[platform]
        if row.get('status')=='published':
            log('OMITIDO:',platform,'ya publicado'); continue
        if row.get('status') not in (None,'pending'):
            raise RuntimeError('Estado previo desconocido para '+platform)
        row['status']='uncertain'; row.pop('error',None)
        save_state(gh,state)
        try:
            identifier=(publish_ig(instagram_id,instagram_token,image_url,description) if platform=='instagram' else publish_fb(facebook_id,facebook_token,image_url,description))
            row.update(status='published',id=str(identifier)); save_state(gh,state)
            log('PUBLICADO:',platform,'ID:',identifier)
        except Exception as exc:
            row['error']=safe_error(exc); save_state(gh,state)
            failures.append(platform+': '+safe_error(exc))
            log('ERROR META:',platform,safe_error(exc))
    receipt={'status':'published' if is_confirmed(entry) else 'partial_or_uncertain','article_id':article_id,'article_title':article['title'],'category':article['category'],'targets':targets,'checked_at':now().isoformat()}
    write_json(output/'estado_publicacion.json',receipt)
    if failures: raise RuntimeError('Publicación parcial o incierta, revisar antes de reintentar: '+' / '.join(failures))
    return receipt


def main():
    parser=argparse.ArgumentParser(description='SISMONAUTA noticias con desarrollo informativo')
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--out',default='output_noticias')
    parser.add_argument('--test-filter',action='store_true')
    parser.add_argument('--test-diversity',action='store_true')
    parser.add_argument('--test-headlines',action='store_true')
    args=parser.parse_args()
    test_earthquake_filter()
    if args.test_filter: return 0
    if args.test_headlines:
        test_headlines(); return 0
    if args.test_diversity:
        dummy={'stories': {str(i): {'article': {'category': 'espacio', 'date': '2026-10-%02dT10:00:00+00:00' % (10+i)}, 'targets': {'instagram': {'status': 'published'}, 'facebook': {'status': 'published'}}} for i in range(5)}}
        sample=[{'category': category, 'title':'Nuevo estudio de ciencia de la Tierra', 'description':'Nuevas mediciones geofísicas y descubrimientos científicos', 'date':now().isoformat(), 'source_body':'', 'source':'Prueba', 'search_category':'rss'} for category in ('espacio','volcanes','oceanos')]
        assert diverse_order(sample, dummy)[0]['category'] != 'espacio'
        assert diverse_order(sample, {'stories': {}})[0]['category'] in SEARCH_GROUPS
        log('PRUEBAS DE DIVERSIDAD APROBADAS'); return 0
    output=Path(args.out); output.mkdir(parents=True,exist_ok=True)
    (output/'estado_publicacion.json').unlink(missing_ok=True)
    log('SISMONAUTA | NOTICIAS CON DESARROLLO | 60 HORAS | PUBLICAR:',args.publish)
    log('MAX_ARTICLES:',MAX_ARTICLES,'| FUENTES DIRECTAS:',len(DIRECT_FEEDS))
    gh=Github() if args.publish else None
    state=load_state(gh) if gh else {'stories':{}}
    articles=diverse_order(feed_items(), state)
    log('NOTICIAS CANDIDATAS:',len(articles))
    article,photo,credit,description,reasons=select_news(articles,state)
    log('AUDITORIA SELECCION:',json.dumps(reasons,ensure_ascii=False))
    if article is None:
        write_json(output/'estado_publicacion.json',{'status':'no_eligible_news','published':False,'lookback_hours':LOOKBACK_HOURS,'articles_reviewed':min(len(articles),MAX_ARTICLES),'reasons':reasons,'checked_at':now().isoformat()})
        log('SIN NOTICIAS CON DESARROLLO SUFICIENTE: no es posible garantizar una noticia verificable; no se publicará contenido inventado'); return 0
    image_file=output/(article['id']+'.jpg')
    make_card(photo,article,image_file)
    # La fuente se guarda solamente en los metadatos, nunca en pie ni caption.
    write_json(output/'noticia.json',{'article':article,'image':credit,'caption':description,'source_url':article.get('url'),'source_name':article.get('source')})
    log('TITULAR PARA IMAGEN:',article.get('image_headline',article['title']))
    log('NOTICIA:',article['title'],'| DESARROLLO:',words(article['explanation']),'palabras')
    if not args.publish:
        write_json(output/'estado_publicacion.json',{'status':'preview_only','published':False,'article_id':article['id'],'image':str(image_file),'checked_at':now().isoformat()})
        log('VISTA PREVIA COMPLETADA'); return 0
    receipt=publish_article(gh,state,article,image_file,credit,description,output)
    if receipt['status']=='published':
        log('PUBLICACION CONFIRMADA EN INSTAGRAM Y FACEBOOK'); return 0
    return 0 if receipt['status']=='already_published' else 1


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print('ERROR:',safe_error(exc),file=sys.stderr,flush=True)
        sys.exit(1)
