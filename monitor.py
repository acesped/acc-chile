#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA @sismonauta — monitor editorial, render y publicación.

Instalar: pip install pillow requests edge-tts mutagen imageio-ffmpeg
Requiere ffmpeg y, para publicar, secretos de Meta/GitHub.
  python monitor.py --test-discovery
  python monitor.py --out output_sismos
  python monitor.py --publish --auto-publish --story

YouTube es un destino opcional y no bloquea Instagram/Facebook.
Los estados remotos se registran en groundshake/state.json.
"""
from __future__ import annotations
import argparse, asyncio, base64, hashlib, html, io, ipaddress, json, math, os, random, re, shutil, socket, subprocess, sys, tempfile, time, unicodedata, urllib.parse, wave
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageFilter, ImageStat

BRAND='SISMONAUTA'; ACCOUNT='sismonauta'; STATE_PATH='groundshake/state.json'; EDITORIAL_PATH='dataviral/sismos_editorial.json'
GRAPH_VERSION=os.getenv('IG_GRAPH_VERSION','v25.0'); GRAPH_URL=os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
W,H,FPS=720,1280,30; TARGET_R7=.70; VOICE=os.getenv('DV_VOICE','es-CL-CatalinaNeural')
HTTP=requests.Session(); HTTP.headers.update({'User-Agent':'SismonautaScience/6.1 educational Wikimedia Commons','Accept':'application/json'})
RNG=random.SystemRandom()
def norm(s): return ''.join(c for c in unicodedata.normalize('NFKD',str(s or '').lower()) if not unicodedata.combining(c))
def clean(s): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]*>',' ',str(s or '')))).strip()
def slug(s): return re.sub('[^a-z0-9]+','-',norm(s)).strip('-')
def log(*s): print(*s,flush=True)
def utcnow(): return datetime.now(timezone.utc).isoformat()
def read_json(path,default):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError,json.JSONDecodeError): return default

def write_json(path,obj):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    q=p.with_suffix(p.suffix+'.tmp'); q.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); q.replace(p)
def safe_error(e):
    s=str(e)
    for key in ('GH_TOKEN','INSTAGRAM_TOKEN','FACEBOOK_PAGE_TOKEN','GEMINI_API_KEY','OPENROUTER_API_KEY','GROQ_API_KEY','YT_CLIENT_ID','YT_CLIENT_SECRET','YT_REFRESH_TOKEN'):
        secret=os.getenv(key,'')
        if secret: s=s.replace(secret,'***')
    return s[:350]
def ffmpeg(args):
    r=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,args)],capture_output=True,text=True)
    if r.returncode: raise RuntimeError('FFmpeg: '+r.stderr[-1300:])

USGS='https://www.usgs.gov/programs/earthquake-hazards'
BGS='https://www.bgs.ac.uk/discovering-geology/earth-hazards/earthquakes/what-causes-earthquakes/'
NOAA='https://www.weather.gov/safety/tsunami-about'
# Todos los hechos se limitan a un banco editorial documentado: no se generan nuevas afirmaciones con IA.
FACT_BANK=[
 ('tectonica','placas_movimiento','Las placas tectónicas se mueven lentamente a lo largo del tiempo.','Sus límites concentran gran parte de la actividad sísmica.',USGS,['tectonic plates mountains','rift valley landscape']),
 ('tectonica','subduccion','En una zona de subducción, una placa se introduce por debajo de otra.','Algunas de estas zonas pueden generar grandes terremotos y tsunamis.',USGS,['Andes mountains coast','Pacific coast mountains']),
 ('tectonica','transformantes','En los límites transformantes, las placas se desplazan lateralmente.','La falla de San Andrés es un ejemplo de este movimiento.',USGS,['San Andreas Fault','California mountains']),
 ('tectonica','divergencia','En los límites divergentes, las placas se separan.','La extensión puede formar valles de rift y fallas normales.',USGS,['East African Rift valley','mountain valley']),
 ('tectonica','colision_continental','La colisión entre placas continentales puede elevar y deformar las rocas.','Estos procesos contribuyen a formar grandes cordilleras.',BGS,['Himalaya mountains','mountain landscape']),
 ('fallas','falla_normal','En una falla normal, un bloque desciende respecto del otro bajo extensión.','El relieve puede mostrar escarpes relacionados con ese movimiento.',BGS,['normal fault scarp','rocky mountains']),
 ('fallas','falla_inversa','En una falla inversa, un bloque asciende respecto del otro por compresión.','Las fallas inversas son importantes en regiones de convergencia.',BGS,['thrust fault landscape','mountain rock formations']),
 ('fallas','ruptura_superficie','No todos los terremotos producen una ruptura visible en la superficie.','El desplazamiento de una falla puede permanecer bajo tierra.',USGS,['earthquake surface rupture','rocky landscape']),
 ('fallas','escarpes','Un escarpe de falla puede quedar marcado en el paisaje.','La forma del terreno puede revelar desplazamientos acumulados.',USGS,['fault scarp landscape','rock formations']),
 ('fallas','grietas','Las grietas superficiales pueden resultar de deformación o deslizamientos.','Una grieta por sí sola no describe toda la geometría de una falla.',USGS,['ground fissure landscape','rocky terrain']),
 ('ondas','ondas_p','Las ondas P pueden propagarse por sólidos y líquidos.','Suelen ser las primeras señales detectadas por sensores sísmicos.',USGS,['seismometer landscape','mountain geology']),
 ('ondas','ondas_s','Las ondas S no se propagan a través de líquidos.','Su comportamiento ayuda a investigar la estructura interna terrestre.',USGS,['mountain rock strata','rock formations']),
 ('ondas','ondas_superficiales','Las ondas superficiales viajan a lo largo de la superficie terrestre.','Pueden contribuir de forma importante al movimiento del suelo.',USGS,['earthquake landscape','mountain valley']),
 ('ondas','atenuacion','El movimiento sísmico suele cambiar con la distancia al origen.','El tipo de suelo y la trayectoria también influyen en las sacudidas.',USGS,['valley landscape','geological mountains']),
 ('ondas','resonancia_suelo','Los suelos y sedimentos pueden modificar las sacudidas sísmicas.','Los efectos del terreno varían de un lugar a otro.',USGS,['sedimentary basin valley','mountain landscape']),
 ('medicion','magnitud_intensidad','La magnitud describe el tamaño de un terremoto, mientras que la intensidad describe sus efectos en un lugar.','Un mismo sismo puede sentirse de forma distinta según la ubicación.',USGS,['mountain valley landscape','rocky hills']),
 ('medicion','profundidad','Los terremotos pueden originarse a diferentes profundidades.','La profundidad es un dato importante para interpretar sus efectos.',USGS,['geological rock layers','mountain rocks']),
 ('medicion','sismometros','Los sismómetros registran el movimiento del suelo.','Sus mediciones permiten estudiar ondas y localizar terremotos.',USGS,['seismometer','geological landscape']),
 ('medicion','epicentro','El epicentro es el punto de la superficie situado sobre el foco del terremoto.','No debe confundirse con el origen a profundidad.',USGS,['mountain terrain','rock formations']),
 ('medicion','replicas','Después de un terremoto pueden ocurrir réplicas en la región afectada.','La actividad sísmica no necesariamente termina con el evento principal.',USGS,['mountain forest landscape','rocky mountain landscape']),
 ('riesgo','peligro_riesgo','La amenaza sísmica y el riesgo no significan lo mismo.','El riesgo depende también de la exposición y la vulnerabilidad.',USGS,['city mountains panorama','natural landscape']),
 ('riesgo','alerta_temprana','La alerta temprana detecta un terremoto cuando ya comenzó.','No predice la fecha exacta de un sismo futuro.',USGS,['seismic monitoring station','mountain landscape']),
 ('riesgo','prediccion','No existe un método científicamente validado para predecir con exactitud fecha, lugar y magnitud de un gran terremoto.','Las evaluaciones probabilísticas no son predicciones exactas.','https://www.usgs.gov/faqs/can-you-predict-earthquakes',['San Andreas Fault','mountains landscape']),
 ('riesgo','deslizamientos','Las sacudidas intensas pueden desencadenar deslizamientos en laderas.','Los efectos de un terremoto pueden extenderse más allá de las estructuras.',USGS,['landslide mountain','mountain hillside']),
 ('tsunamis','origen_tsunami','Algunos terremotos submarinos pueden desplazar el fondo marino y generar tsunamis.','No todos los terremotos bajo el océano producen un tsunami.',NOAA,['ocean coast','Pacific coastline']),
 ('tsunamis','primera_ola','La primera ola de un tsunami no tiene por qué ser la mayor.','Pueden llegar varias oleadas y el peligro continuar durante horas.',NOAA,['ocean waves coastline','beach shoreline']),
 ('tsunamis','retroceso_mar','Algunos tsunamis producen un retiro inusual del mar antes de la llegada del agua.','Ese retiro no ocurre en todos los tsunamis y es una señal natural de peligro.',NOAA,['sea beach','coastal cliffs']),
 ('tsunamis','inundacion','Un tsunami puede llegar como una inundación rápida y no como una enorme ola que rompe.','Las corrientes y la inundación pueden ser muy peligrosas.',NOAA,['ocean shoreline','coastal landscape']),
 ('tsunamis','estuarios','Las ondas de tsunami pueden avanzar por ríos y estuarios conectados al mar.','La inundación puede extenderse tierra adentro.',NOAA,['river estuary coast','river valley']),
 ('geologia','rocas_deformadas','Las rocas pueden conservar evidencias de esfuerzos y deformaciones tectónicas.','El paisaje geológico permite reconstruir procesos pasados.',BGS,['folded rock layers','geological landscape']),
 ('geologia','levantamiento','El movimiento tectónico puede elevar sectores de la corteza terrestre.','Las montañas registran largos procesos de deformación.',BGS,['mountain range landscape','Andes mountains']),
 ('geologia','valle_rift','Un valle de rift puede originarse donde la corteza se estira y fractura.','La separación tectónica deja huellas visibles en el relieve.',USGS,['East African Rift','rift valley landscape']),
]
ANGLES=[
 ('mecanismo','¿Cómo funciona {subject}?','Así ocurre este proceso geológico'),('significado','¿Qué nos enseña {subject}?','Una pista sobre el planeta'),
 ('evidencia','¿Qué señales deja {subject}?','La evidencia ayuda a entenderlo'),('mito','¿Qué solemos confundir sobre {subject}?','No todo es como parece'),
 ('consecuencia','¿Por qué importa {subject}?','Sus efectos ayudan a comprender el riesgo'),('explicacion','La ciencia detrás de {subject}','Un fenómeno explicado en segundos'),
 ('clave','¿Cuál es la clave para comprender {subject}?','Una idea científica fundamental'),('observacion','¿Qué observan los científicos al estudiar {subject}?','Observar ayuda a entender el proceso'),
 ('interpretacion','¿Cómo interpretar correctamente {subject}?','La interpretación requiere contexto'),('origen','¿Qué procesos explican {subject}?','Detrás del fenómeno hay procesos físicos'),
 ('contexto','¿En qué contexto aparece {subject}?','El entorno geológico importa'),('relieve','¿Qué relación guarda el paisaje con {subject}?','El relieve puede guardar pistas'),
 ('escala','¿Por qué la escala importa al estudiar {subject}?','La escala cambia lo que observamos'),('tiempo','¿Qué cambia con el tiempo al analizar {subject}?','Los procesos terrestres tienen historia'),
 ('detalles','¿Qué detalle suele pasar inadvertido sobre {subject}?','Los detalles ayudan a comprender'),('pregunta','¿Qué deberíamos preguntarnos sobre {subject}?','Preguntar es el primer paso'),
 ('diferencia','¿Cómo distinguir {subject} de otros fenómenos?','Es importante reconocer diferencias'),('limites','¿Qué no podemos concluir solamente a partir de {subject}?','No saquemos conclusiones apresuradas'),
 ('investigacion','¿Por qué se investiga {subject}?','La investigación aporta conocimiento'),('datos','¿Qué información necesitamos para estudiar {subject}?','Los datos son fundamentales'),
 ('errores','¿Qué errores conviene evitar al explicar {subject}?','Una explicación correcta importa'),('relacion','¿Cómo se relaciona {subject} con otros procesos de la Tierra?','La Tierra funciona como un sistema'),
 ('fundamentos','¿Qué principios científicos ayudan a explicar {subject}?','Vamos a los fundamentos'),('lectura','¿Cómo leer las pistas que deja {subject}?','La ciencia interpreta evidencias'),
 ('impacto','¿Qué importancia tiene comprender {subject}?','Comprender permite tomar mejores decisiones'),('estudio','¿Cómo se estudia {subject} paso a paso?','Primero comprendemos el fenómeno'),
 ('preguntas_frecuentes','¿Qué pregunta frecuente surge al hablar de {subject}?','Una duda que merece respuesta'),('comparacion','¿Qué hace especial a {subject} dentro de la geología?','Hay diferencias que importan'),
 ('riesgo','¿Por qué conviene conocer mejor {subject}?','El conocimiento mejora la comprensión del riesgo'),('aprendizaje','¿Qué podemos aprender hoy acerca de {subject}?','Una explicación breve y científica'),
 ('precision','¿Cómo hablar con precisión científica de {subject}?','Evitemos las simplificaciones incorrectas'),('sintesis','¿Qué es lo esencial que debes saber de {subject}?','Lo fundamental sin exageraciones')]
SUBJECTS=dict(zip(
 ('placas_movimiento subduccion transformantes divergencia colision_continental falla_normal falla_inversa ruptura_superficie escarpes grietas ondas_p ondas_s ondas_superficiales atenuacion resonancia_suelo magnitud_intensidad profundidad sismometros epicentro replicas peligro_riesgo alerta_temprana prediccion deslizamientos origen_tsunami primera_ola retroceso_mar inundacion estuarios rocas_deformadas levantamiento valle_rift').split(),
 ('el movimiento de las placas','la subducción','las fallas transformantes','la separación de placas','la colisión continental','una falla normal','una falla inversa','la ruptura superficial','un escarpe de falla','las grietas del terreno','las ondas P','las ondas S','las ondas superficiales','la propagación de las ondas','el efecto del suelo','la magnitud y la intensidad','la profundidad de los sismos','los sismómetros','el epicentro','las réplicas','la amenaza y el riesgo sísmico','la alerta temprana','las predicciones sísmicas','los deslizamientos por sismos','el origen de los tsunamis','la primera ola de un tsunami','el retroceso del mar','la inundación por tsunami','los tsunamis en ríos','la deformación de las rocas','el levantamiento de montañas','los valles de rift')))
LEGACY_IDS=set('alerta colision deslizamientos falla falla_inversa falla_normal grietas magnitud ondas placas prediccion profundidad replicas rift ruptura sismografo subduccion terremoto transformante tsunami_inundacion tsunami_oleadas tsunami_retroceso tsunami_rios tsunamis valdivia'.split())
STYLES=('descubrimiento','microdocumental','mito_realidad','mapa_explicado','pregunta_respuesta')

def make_candidate(fact,angle,style):
    category,key,statement,lesson,source,queries=fact; angle_id,template,bridge=angle
    concept_id=f'{category}:{key}:{angle_id}'
    return dict(id=concept_id,topic_key=key,category='Divulgación sísmica',domain=category,title=template.format(subject=SUBJECTS[key]),fact=statement,lesson=lesson,source=source,queries=queries,angle=angle_id,style=style,bridge=bridge,episode_id='sismos-'+hashlib.sha1(concept_id.encode()).hexdigest()[:12],created_at=utcnow(),base_verified=True,verified=False,review_note='Hechos de banco editorial; no constituye verificación web en tiempo real.')
def legacy_topic_for_episode(episode_id):
    angles=('¿Sabías esto?','La explicación científica','Lo que muestran los datos','La diferencia importante','La pregunta que importa','Un detalle sorprendente','Lo que no debemos confundir','Lo esencial en segundos')
    for key in LEGACY_IDS:
        for style in STYLES:
            for angle in angles:
                if 'sismos-'+hashlib.sha1((key+style+angle).encode()).hexdigest()[:12]==episode_id: return key
    return None

def history_signatures(state,history):
    ids=set(); keys=set(); titles=[]
    for item in history.get('items',[]):
        if not isinstance(item,dict): continue
        if item.get('status') in ('publicado','enviando','incierto'):
            if item.get('episode_id'): ids.add(item['episode_id'])
            if item.get('topic'): keys.add(str(item['topic']))
            if item.get('title'): titles.append(item['title'])
    for eid,row in (state.get('episodes') or {}).items():
        if not isinstance(row,dict): continue
        if any(row.get(k) in ('publicado','enviando','incierto','aplazado') for k in ('reel','story','facebook_reel','facebook_story','facebook_feed','youtube_short','youtube_video')):
            ids.add(eid)
            if row.get('topic'): keys.add(str(row['topic']))
            old=legacy_topic_for_episode(eid)
            if old: keys.add(old)
            if row.get('title'): titles.append(row['title'])
    return ids,keys,titles

def similarity(a,b):
    aa=set(re.findall(r'[a-z0-9]{4,}',norm(a))); bb=set(re.findall(r'[a-z0-9]{4,}',norm(b)))
    return len(aa&bb)/max(1,len(aa|bb))

def ai_new_angle(prompt):
    gemini=os.getenv('GEMINI_API_KEY','').strip()
    if gemini:
        try:
            model=os.getenv('GEMINI_MONITOR_MODEL','gemini-2.5-flash')
            r=HTTP.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',headers={'x-goog-api-key':gemini},json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'temperature':.7,'maxOutputTokens':240}},timeout=35)
            r.raise_for_status(); return '\n'.join(p.get('text','') for p in r.json()['candidates'][0]['content']['parts'])
        except Exception as exc: log('IA Gemini:',safe_error(exc)[:80])
    for provider,env,url,model in (('OpenRouter','OPENROUTER_API_KEY','https://openrouter.ai/api/v1/chat/completions',os.getenv('OPENROUTER_MONITOR_MODEL','openrouter/free')),('Groq','GROQ_API_KEY','https://api.groq.com/openai/v1/chat/completions',os.getenv('GROQ_MONITOR_MODEL','llama-3.3-70b-versatile'))):
        key=os.getenv(env,'').strip()
        if not key: continue
        try:
            r=HTTP.post(url,headers={'Authorization':'Bearer '+key},json={'model':model,'temperature':.7,'max_tokens':240,'messages':[{'role':'user','content':prompt}]},timeout=35)
            r.raise_for_status(); return str(r.json()['choices'][0]['message']['content'])
        except Exception as exc: log('IA',provider,safe_error(exc)[:80])
    return None

def discover_dynamic_topic(history,state,excluded,requested=''):
    published_ids,published_topics,old_titles=history_signatures(state,history)
    facts=[f for f in FACT_BANK if not requested or requested in (f[0],f[1])]; RNG.shuffle(facts)
    for fact in facts[:12]:
        domain,key,statement,lesson,source,queries=fact
        prompt=('Propón UNA pregunta NUEVA e interesante para un video educativo en español de Chile. '
                'Pregunta específica, no paráfrasis literal, ni afirmaciones nuevas. '
                'Usa solo estos dos hechos:\n'+statement+'\n'+lesson+'\n'
                'Solo la pregunta, de 7 a 16 palabras, con signos ¿?, sin explicaciones ni terremotos recientes.')
        for _ in range(3):
            proposal=clean(ai_new_angle(prompt) or '')
            proposal=re.sub(r'^(?:titulo|pregunta|tema)\s*:\s*','',proposal,flags=re.I).strip('"* ')
            if not (7<=len(proposal.split())<=16 and proposal.startswith('¿') and proposal.endswith('?')): continue
            if any(similarity(proposal,old)>.72 for old in old_titles): continue
            angle_id='nuevo-'+hashlib.sha1(norm(proposal).encode()).hexdigest()[:10]
            ep=make_candidate(fact,(angle_id,proposal,'La ciencia permite comprender mejor este fenómeno'),STYLES[0])
            ep['discovery']='ai_grounded_question'
            if ep['episode_id'] not in published_ids and ep['id'] not in published_topics and ep['id'] not in excluded: return ep
    return None

def choose_topic(history,requested='',excluded_topics=None,state=None,limit=None):
    published_ids,published_topics,old_titles=history_signatures(state or {},history); excluded=set(excluded_topics or ()); candidates=[]
    for fact in FACT_BANK:
        for angle in ANGLES:
            style=STYLES[int(hashlib.sha1((fact[1]+angle[0]).encode()).hexdigest()[:4],16)%len(STYLES)]
            ep=make_candidate(fact,angle,style)
            if ep['id'] in excluded or ep['episode_id'] in published_ids or ep['id'] in published_topics: continue
            if requested and requested not in (ep['id'],ep['topic_key'],ep['domain']): continue
            if any(similarity(ep['title'],t)>.86 for t in old_titles): continue
            candidates.append(ep)
    if not candidates:
        dynamic=discover_dynamic_topic(history,state or {},excluded,requested) if os.getenv('DISCOVERY_USE_AI','true').lower() in ('1','yes','true') else None
        if dynamic: return dynamic
        raise RuntimeError('SIN_TEMAS_NUEVOS: no se repetirá contenido')
    counts={}
    for row in history.get('items',[]):
        k=row.get('topic_key') or row.get('topic'); counts[k]=counts.get(k,0)+1
    least=min(counts.get(e['topic_key'],0) for e in candidates)
    return RNG.choice([e for e in candidates if counts.get(e['topic_key'],0)<=least+1])

OVERLAY_STOPWORDS=set(('el la los las un una unos unas de del al en por para con sin sobre que como cual cuales donde cuando porque y o a se su sus es son fue fueron puede pueden podria podrían este esta estos estas lo le les hay ser estar entre durante desde hasta tambien mas muy bajo ante respecto otro otra otros otras ello eso ese esa sus').split())
OVERLAY_GENERIC={'¿SABÍAS ESTO?','LA CIENCIA LO EXPLICA','EL DATO CIENTÍFICO','LO QUE DEBES SABER','SIGUE APRENDIENDO','UNA PISTA SOBRE EL PLANETA'}
def phrase_tokens(value): return [w for w in re.findall('[a-záéíóúñü0-9]+',norm(value)) if len(w)>=3 and w not in OVERLAY_STOPWORDS]
def overlay_for_speech(narration,subject='',taken=None,max_words=12):
    taken=taken if taken is not None else set(); speech=clean(narration).strip(' .!¿?')
    speech=re.sub(r'^(?:f[ií]jate en este detalle|la clave est[aá] en entender|en resumen|observa esto)\s*[:：,]\s*','',speech,flags=re.I)
    speech=re.sub(r'^(?:¿?qu[eé] |¿?por qu[eé] |¿?c[oó]mo |¿?cu[aá]l es |¿?qu[eé] ocurre cuando )','',speech,flags=re.I)
    tokens=speech.split()
    if not tokens: raise ValueError('Narración vacía')
    windows=[]
    for n in range(min(max_words,len(tokens)),max(2,min(4,len(tokens))-1),-1):
        for i in range(len(tokens)-n+1):
            candidate=' '.join(tokens[i:i+n]).strip(' .,;:¿?¡!'); semantic=phrase_tokens(candidate)
            if not semantic: continue
            score=len(set(semantic))*3+sum(len(x)>6 for x in semantic)-i*.06
            if norm(candidate) in taken or candidate.upper() in OVERLAY_GENERIC: score-=1000
            windows.append((score,candidate))
    windows.sort(key=lambda x:x[0],reverse=True)
    for _,candidate in windows:
        if norm(candidate) not in taken: return candidate.upper()
    alt=' '.join(tokens[:max_words]).strip(' .,;:¿?¡!').upper()
    if norm(alt) in taken: raise ValueError('Rótulos idénticos')
    return alt

def build_storyboard(ep):
    title=clean(ep['title']); fact=clean(ep['fact']).rstrip('.')+'.'; lesson=clean(ep['lesson']).rstrip('.')+'.'
    subject=clean(SUBJECTS.get(ep.get('topic_key',''),ep.get('title','este fenómeno')))
    spoken=[title,f'Para entender {subject}, hay un detalle fundamental.',fact,lesson,f'En resumen: {lesson}','Dale me gusta y comparte para seguir aprendiendo ciencia con SISMONAUTA. ¡Muchas gracias!']
    stages=('pregunta','intriga','revelacion','explicacion','cierre','cta'); queries=ep.get('queries') or ['geological landscape']; taken=set(); result=[]
    for i,(narration,stage) in enumerate(zip(spoken,stages)):
        if stage=='cta': overlay='DALE LIKE'
        elif stage=='intriga': overlay=overlay_for_speech(f'El detalle fundamental de {subject}',subject,taken)
        elif stage=='cierre': overlay=overlay_for_speech(f'{subject}: {lesson}',subject,taken)
        else: overlay=overlay_for_speech(narration,subject,taken)
        if len(overlay.split())>12: raise ValueError('Rótulo demasiado largo')
        taken.add(norm(overlay)); result.append(dict(narration=narration,overlay=overlay,query=queries[min(i,len(queries)-1)],stage=stage))
    return result

def generate_angle(topic_data,history,use_ai=True):
    ep=dict(topic_data); ep['script']=' '.join(row['narration'] for row in build_storyboard(ep)); ep['visuals']=[row['overlay'] for row in build_storyboard(ep)]; return ep

def episode_hashtags(ep):
    tags=['Sismonauta','Sismos','Sismologia','Geofisica','CienciaDeLaTierra','PrevencionSismica','DivulgacionCientifica','AprendeConSismonauta']
    if ep['domain']=='tsunamis': tags+=['Tsunamis','Oceano','SeguridadCostera']
    elif ep['domain']=='tectonica': tags+=['TectonicaDePlacas','Geologia']
    return ' '.join('#'+x for x in dict.fromkeys(tags))
def episode_caption_base(ep): return f"{ep['title']}\n\n{ep['fact']}\n\n{ep['lesson']}\n\n¿Qué opinas? Te leemos en los comentarios."

NATURE_QUERIES=['mountain landscape','rocky hills','forest landscape','ocean coast','coastal cliffs','river valley','geological landscape']
NATURE_TERMS=('mountain','hill','forest','beach','ocean','sea','coast','shore','cliff','valley','river','landscape','terrain','rock','geolog','fault','rift','earthquake','tsunami','volcan','montana','cerro','bosque','playa','costa','roca','naturaleza','seismometer')
NON_PHOTO_TERMS=('diagram','infographic','flowchart','schematic','cross section','seismogram','waveform','graph','chart','drawing','illustration','cartoon','vector','icon','logo','poster','banner','screenshot','map','satellite image','pdf')
def license_accepted(value):
    v=re.sub(r'[^a-z0-9]','',norm(value)); return v.startswith(('cc0','publicdomain','publicdomainmark','pdm','pdold','pdus','pdself'))
def photo_relevant(item,ep):
    title=norm(str(item.get('title') or '').replace('_',' '))
    if not title or any(t in title for t in NON_PHOTO_TERMS): return False
    combined=' '.join((title,norm(item.get('description')),norm(item.get('tags'))))
    return any(t in combined for t in NATURE_TERMS) or len(title)>12
def visual_photo_audit(path):
    try:
        with Image.open(path) as raw:
            im=ImageOps.exif_transpose(raw).convert('RGB')
            if im.width<500 or im.height<350: return False,'resolución insuficiente'
            sample=ImageOps.contain(im,(320,320)).convert('L')
            if ImageStat.Stat(sample).stddev[0]<2: return False,'imagen casi uniforme'
        return True,'fotografía técnicamente apta'
    except Exception as e: return False,'imagen ilegible: '+safe_error(e)
def commons(query):
    endpoint='https://commons.wikimedia.org/w/api.php'; continuation={}; seen=set()
    for page_number in range(3):
        params={'action':'query','format':'json','formatversion':2,'generator':'search','gsrsearch':query,'gsrnamespace':6,'gsrlimit':40,'prop':'imageinfo','iiprop':'url|extmetadata|size|mime','iiurlwidth':1400,**continuation}
        response=HTTP.get(endpoint,params=params,timeout=(10,35)); response.raise_for_status(); data=response.json()
        pages=(data.get('query') or {}).get('pages',[])
        if isinstance(pages,dict): pages=list(pages.values())
        log('WIKIMEDIA:',query,'página:',page_number+1,'candidatos:',len(pages))
        for page in pages:
            info=(page.get('imageinfo') or [None])[0]
            if not info: continue
            meta=info.get('extmetadata') or {}
            def val(k): return clean((meta.get(k) or {}).get('value',''))
            license_code=val('LicenseShortName')
            if not license_accepted(license_code): continue
            title=str(page.get('title') or '')
            if not title.lower().endswith(('.jpg','.jpeg','.png','.webp')): continue
            if min(int(info.get('width') or 0),int(info.get('height') or 0))<450: continue
            if info.get('mime') not in ('image/jpeg','image/png','image/webp'): continue
            url=info.get('thumburl') or info.get('url')
            if not url or url in seen: continue
            seen.add(url)
            yield dict(url=url,source=info.get('descriptionurl',''),license=license_code,title=title,description=val('ImageDescription'),creator=val('Artist'),tags='',provider='Wikimedia',search_query=query)
        continuation=data.get('continue')
        if not continuation: break
_OPENVERSE_UNAVAILABLE_UNTIL=0
def openverse(query):
    global _OPENVERSE_UNAVAILABLE_UNTIL
    if os.getenv('ENABLE_OPENVERSE','true').lower() not in ('true','1','yes') or time.monotonic()<_OPENVERSE_UNAVAILABLE_UNTIL: return
    try:
        res=HTTP.get('https://api.openverse.org/v1/images/',params={'q':query,'license':'cc0,pdm','page_size':30},timeout=(8,20))
        if res.status_code in (401,403,429): _OPENVERSE_UNAVAILABLE_UNTIL=time.monotonic()+600; return
        res.raise_for_status(); data=res.json()
    except (requests.RequestException,ValueError) as e:
        _OPENVERSE_UNAVAILABLE_UNTIL=time.monotonic()+600; log('Openverse no disponible:',safe_error(e)); return
    for item in data.get('results',[]):
        if not license_accepted(item.get('license','')) or item.get('mature'): continue
        url=item.get('url') or item.get('thumbnail')
        if url: yield dict(url=url,source=item.get('foreign_landing_url',''),license=item.get('license',''),title=item.get('title',''),description=item.get('description',''),tags=' '.join(t.get('name','') for t in item.get('tags',[]) if isinstance(t,dict)),creator=item.get('creator',''),provider='Openverse',search_query=query)
def valid_image_url(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443): return False
    try:
        addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(row[4][0]).is_global for row in addresses)
    except Exception: return False
def photo_download(item,dest):
    url=str(item['url'])
    if not valid_image_url(url): return False
    try:
        for _ in range(5):
            with HTTP.get(url,timeout=(8,25),stream=True,allow_redirects=False) as response:
                if response.status_code in (301,302,303,307,308):
                    url=urllib.parse.urljoin(url,response.headers.get('Location',''))
                    if not valid_image_url(url): return False
                    continue
                response.raise_for_status()
                if not any(t in response.headers.get('Content-Type','').lower() for t in ('image/jpeg','image/png','image/webp')): return False
                data=bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>12_000_000: return False
                with Image.open(io.BytesIO(data)) as im:
                    im=ImageOps.exif_transpose(im)
                    if min(im.width,im.height)<350 or im.width<500: return False
                    im.convert('RGB').save(dest,'JPEG',quality=91)
                return True
    except Exception as e: log('DESCARGA DESCARTADA:',safe_error(e)[:100])
    return False
def get_photos(ep,folder,local_dir=None):
    folder.mkdir(parents=True,exist_ok=True); found=[]; credits=[]; seen=set()
    if local_dir:
        local=Path(local_dir)
        if not local.is_dir(): raise RuntimeError('No existe --photo-dir')
        for path in sorted(local.iterdir()):
            if path.suffix.lower() not in ('.jpg','.jpeg','.png','.webp'): continue
            if visual_photo_audit(path)[0]:
                found.append(path); credits.append(dict(source=str(path),license='Autorización manual requerida',title=path.name,provider='Local'))
            if len(found)>=3: break
        if not found: raise RuntimeError('No hay fotografías válidas en --photo-dir')
        return found,credits
    generic=(['ocean coast','sandy beach','coastal cliffs','river estuary'] if ep['domain']=='tsunamis' else NATURE_QUERIES)
    for query in dict.fromkeys([*ep['queries'],*generic]):
        for provider in (commons,openverse):
            try:
                for item in provider(query):
                    if item['url'] in seen: continue
                    seen.add(item['url'])
                    if not photo_relevant(item,ep): continue
                    dest=folder/f'foto_{len(found)+1}.jpg'
                    if not photo_download(item,dest): continue
                    if not visual_photo_audit(dest)[0]: dest.unlink(missing_ok=True); continue
                    found.append(dest); credits.append({k:item.get(k,'') for k in ('source','license','title','creator','provider','description','tags','search_query')})
                    log('FOTO ACEPTADA:',item.get('title','')[:90])
                    if len(found)>=3: return found,credits
            except Exception as e: log('PROVEEDOR:',provider.__name__,safe_error(e)[:120])
    if not found: raise RuntimeError('No se encontraron fotografías auténticas con licencia CC0/PD.')
    return found,credits

def font(size,bold=True):
    choices=(['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf'] if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
    for path in choices:
        if Path(path).exists(): return ImageFont.truetype(path,size)
    return ImageFont.load_default()
def wrap(draw,text,ft,width):
    lines=[]; current=''
    for word in str(text).split():
        proposal=(current+' '+word).strip()
        if draw.textbbox((0,0),proposal,font=ft)[2]<=width: current=proposal
        else:
            if current: lines.append(current)
            current=word
    if current: lines.append(current)
    return lines
def photo_crop_variant(src,index):
    centering=[(.50,.50),(.35,.45),(.65,.55),(.45,.35),(.55,.65)][index%5]; zoom=[1,1.08,1.13,1.05,1.10][index%5]
    fitted=ImageOps.fit(ImageOps.exif_transpose(src).convert('RGB'),(int(W*zoom),int(H*zoom)),method=Image.Resampling.LANCZOS,centering=centering)
    if zoom==1: return fitted
    return fitted.crop((int((fitted.width-W)*centering[0]),int((fitted.height-H)*centering[1]),int((fitted.width-W)*centering[0])+W,int((fitted.height-H)*centering[1])+H))
def clean_scene(photo,message,dest,variant=0):
    with Image.open(photo) as src: im=photo_crop_variant(src,variant)
    im=im.filter(ImageFilter.GaussianBlur(6)); im=ImageEnhance.Color(im).enhance(.78); im=ImageEnhance.Contrast(im).enhance(.93)
    im=Image.blend(im,Image.new('RGB',(W,H),(0,0,0)),.49)
    draw=ImageDraw.Draw(im); message=clean(message).replace('…','').upper()
    if not message: raise RuntimeError('Escena sin rótulo')
    chosen=None
    for size in range(65,17,-1):
        ft=font(size); lines=wrap(draw,message,ft,W-104); step=int(size*1.27)
        if lines and len(lines)<=8 and len(lines)*step<H*.63: chosen=(ft,lines,step); break
    if chosen is None: raise RuntimeError('Texto demasiado largo para escena')
    ft,lines,step=chosen; y=(H-len(lines)*step)//2
    for line in lines:
        bb=draw.textbbox((0,0),line,font=ft,stroke_width=3); x=(W-(bb[2]-bb[0]))/2
        draw.text((x,y),line,font=ft,fill='white',stroke_width=3,stroke_fill='#09131C'); y+=step
    im.save(dest,'JPEG',quality=92)
def draw_like_icon(draw,cx,cy,scale=1.0,fill=(255,255,255,255)):
    s=scale; alpha=fill[3] if len(fill)==4 else 255; r=132*s
    draw.ellipse((cx-r,cy-r,cx+r,cy+r),fill=(24,119,242,alpha))
    draw.rounded_rectangle((cx-88*s,cy-3*s,cx-54*s,cy+69*s),radius=8*s,fill=(255,255,255,alpha))
    pts=[(-46,1),(-24,-14),(13,-14),(26,-23),(47,-20),(58,-11),(58,1),(54,13),(52,21),(50,34),(47,42),(45,55),(31,68),(-45,68),(-39,4),(-20,-15),(-9,-38),(2,-74),(12,-93),(24,-96),(32,-88),(34,-75),(22,-38),(17,-21),(35,-21),(46,-16),(47,-7),(40,3)]
    draw.polygon([(cx+x*s,cy+y*s) for x,y in pts],fill=(255,255,255,alpha))
def cta_scene_video(dest,duration):
    tmp=Path(tempfile.mkdtemp(prefix='cta_sismonauta_'))
    try:
        for n in range(max(1,int(duration*FPS))):
            im=Image.new('RGBA',(W,H),(0,0,0,255)); draw=ImageDraw.Draw(im)
            visible=(n//max(6,int(FPS*.45))%2)==0
            draw_like_icon(draw,W/2,H/2-40,scale=1.20*(1 if visible else .88),fill=(255,255,255,255 if visible else 82))
            title='DALE LIKE'; ft=font(54); bb=draw.textbbox((0,0),title,font=ft,stroke_width=3)
            draw.text(((W-(bb[2]-bb[0]))/2,H-160),title,font=ft,fill='white',stroke_width=3,stroke_fill='#09131C')
            im.convert('RGB').save(tmp/f'frame_{n:04d}.png')
        ffmpeg(['-framerate',FPS,'-i',tmp/'frame_%04d.png','-t',duration,'-r',FPS,'-c:v','libx264','-preset','veryfast','-crf',23,'-pix_fmt','yuv420p','-movflags','+faststart',dest])
    finally: shutil.rmtree(tmp,ignore_errors=True)
def soundtrack(path,secs=45,sr=22050):
    import array
    buf=array.array('h'); notes=(130.81,164.81,196.00,261.63)
    for i in range(int(math.ceil(secs*sr))):
        t=i/sr; fade=max(0,min(1,t/1.3,(secs-t)/2)); note=notes[int(t*2)%4]
        pad=.36*math.sin(2*math.pi*130.81*t)+.22*math.sin(2*math.pi*196*t)+.13*math.sin(2*math.pi*261.63*t)
        pulse=.15*math.sin(2*math.pi*note*t)*(.5+.5*math.cos(2*math.pi*2*t))
        value=max(-1,min(1,(pad+pulse)*.55*fade)); buf.append(int(32767*value))
    with wave.open(str(path),'wb') as f: f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr); f.writeframes(buf.tobytes())
async def speak(text,out):
    import edge_tts
    await edge_tts.Communicate(text,voice=VOICE,rate='+8%').save(str(out))
    if not out.exists() or out.stat().st_size<1000: raise RuntimeError('No se generó la voz')
def render(ep,out,photo_dir=None,voice=True):
    if not shutil.which('ffmpeg'): raise RuntimeError('FFmpeg no instalado')
    folder=out/ep['episode_id']; folder.mkdir(parents=True,exist_ok=True)
    scenes=build_storyboard(ep); ep['storyboard']=scenes; ep['script']=' '.join(s['narration'] for s in scenes); ep['visuals']=[s['overlay'] for s in scenes]
    write_json(folder/'storyboard.json',scenes)
    photos,credits=get_photos(ep,folder,photo_dir)
    if not photo_dir and not all(license_accepted(c.get('license','')) for c in credits): raise RuntimeError('Fotografías sin licencia CC0/PD validada')
    ep['image_credits']=credits; durations=[]; voices=[]
    from mutagen.mp3 import MP3
    for i,scene in enumerate(scenes):
        seconds=0
        if voice:
            clip=folder/f'voz_escena_{i:02d}.mp3'; asyncio.run(speak(scene['narration'],clip)); seconds=float(MP3(str(clip)).info.length); voices.append(clip)
        stage=scene['stage']; minimum,rate=((3.6,2.7) if stage=='pregunta' else (3.0,2.6) if stage=='intriga' else (5.4,2.3) if stage=='cta' else (4.7,2.15))
        durations.append(round(max(seconds+.55,minimum,len(scene['overlay'].split())/rate+.7),2))
    total=round(sum(durations),2); log('VOZ POR ESCENA:',durations,'TOTAL:',total)
    media=[]; cover=None; j=0
    for i,scene in enumerate(scenes):
        if scene['stage']=='cta':
            clip=folder/f'escena_{i:02d}.mp4'; cta_scene_video(clip,durations[i]); media.append(('video',clip,durations[i])); continue
        frame=folder/f'escena_{i:02d}.jpg'; photo=photos[j%len(photos)]; clean_scene(photo,scene['overlay'],frame,variant=i); media.append(('image',frame,durations[i]))
        if cover is None: cover=out/(ep['episode_id']+'_portada.jpg'); shutil.copy2(frame,cover)
        j+=1
    music=folder/'musica.wav'; soundtrack(music,total+1); inputs=[]
    for kind,path,duration in media:
        if kind=='image': inputs+=['-loop','1','-framerate',FPS,'-t',duration,'-i',path]
        else: inputs+=['-i',path]
    n=len(media); inputs+=['-i',music]
    for clip in voices: inputs+=['-i',clip]
    filters=[]
    for i,(_,_,duration) in enumerate(media): filters.append(f'[{i}:v]scale={W}:{H},setsar=1,trim=duration={duration},setpts=PTS-STARTPTS,format=yuv420p[v{i}]')
    filters.append(''.join(f'[v{i}]' for i in range(n))+f'concat=n={n}:v=1:a=0[v]')
    if voice:
        for i,duration in enumerate(durations): filters.append(f'[{n+1+i}:a]aresample=44100,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a{i}]')
        filters.append(''.join(f'[a{i}]' for i in range(len(voices)))+f'concat=n={len(voices)}:v=0:a=1[narr]')
        filters.append(f'[{n}:a]volume=0.30[mus];[narr]volume=1.0[nar];[mus][nar]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.94[a]')
    else: filters.append(f'[{n}:a]volume=0.65,alimiter=limit=0.94[a]')
    movie=out/(ep['episode_id']+'.mp4')
    ffmpeg([*inputs,'-filter_complex',';'.join(filters),'-map','[v]','-map','[a]','-t',total,'-r',FPS,'-c:v','libx264','-preset','veryfast','-crf',23,'-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',movie])
    write_json(folder/'creditos.json',credits); write_json(folder/'duraciones_escenas.json',{'segundos':durations,'total':total,'sincronizacion':'voz separada por escena','musica':'instrumental_original','fotografias_distintas':len(photos)})
    log('VIDEO:',movie,'PORTADA:',cover,'VOZ:',voice); return movie,cover,credits

# === SISMONAUTA / YOUTUBE DATA API v3 ===
# Los problemas de YouTube no deben abortar publicaciones ya realizadas en Meta.
# OAuth: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
YOUTUBE_API='https://www.googleapis.com/youtube/v3'
YOUTUBE_UPLOAD='https://www.googleapis.com/upload/youtube/v3/videos'

class YouTubeUploadLimit(RuntimeError):
    """Límite de subida confirmado; seguro aplazar SIN marcar como incierto."""


def youtube_is_upload_limit(status_code, response_text):
    if status_code not in (400, 403, 429):
        return False
    try:
        payload=json.loads(response_text)
        err=payload.get('error') or {}
        reasons=[str(x.get('reason','')).lower() for x in err.get('errors',[]) if isinstance(x,dict)]
        msg=str(err.get('message','')).lower()
        return ('uploadlimitexceeded' in reasons
                or 'the user has exceeded the number of videos they may upload' in msg)
    except (ValueError, TypeError, AttributeError):
        return 'exceeded the number of videos they may upload' in str(response_text).lower()


def youtube_check_error(response, context):
    if youtube_is_upload_limit(response.status_code,response.text):
        raise YouTubeUploadLimit('YouTube: límite de carga de videos alcanzado')
    raise RuntimeError(f'YouTube {context} HTTP {response.status_code}: '+safe_error(response.text)[:180])


def youtube_cooldown_active(state):
    until=str(state.get('youtube_upload_retry_after') or '')
    if not until: return False
    try:
        return datetime.fromisoformat(until.replace('Z','+00:00'))>datetime.now(timezone.utc)
    except (ValueError,TypeError):
        return False


def yt_token():
    required=('YT_CLIENT_ID','YT_CLIENT_SECRET','YT_REFRESH_TOKEN')
    missing=[k for k in required if not os.getenv(k,'').strip()]
    if missing: raise RuntimeError('YouTube: faltan secretos '+', '.join(missing))
    r=requests.post('https://oauth2.googleapis.com/token',data={
        'client_id':os.environ['YT_CLIENT_ID'],
        'client_secret':os.environ['YT_CLIENT_SECRET'],
        'refresh_token':os.environ['YT_REFRESH_TOKEN'],
        'grant_type':'refresh_token'},timeout=30)
    if not r.ok: raise RuntimeError(f'YouTube OAuth HTTP {r.status_code}: '+safe_error(r.text)[:200])
    token=r.json().get('access_token')
    if not token: raise RuntimeError('YouTube OAuth sin access_token')
    return token


def yt_api_get(path,token,params):
    r=requests.get(YOUTUBE_API+'/'+path,headers={'Authorization':'Bearer '+token},params=params,timeout=40)
    if not r.ok: raise RuntimeError(f'YouTube API HTTP {r.status_code}: '+safe_error(r.text)[:200])
    return r.json()


def yt_verify_channel(token):
    info=yt_api_get('channels',token,{'part':'snippet','mine':'true','maxResults':10})
    channels=info.get('items',[])
    channel_id=os.getenv('YT_CHANNEL_ID','').strip()
    if not channel_id: raise RuntimeError('Configure YT_CHANNEL_ID para evitar publicaciones accidentales')
    matches=[x for x in channels if x.get('id')==channel_id]
    if len(matches)!=1: raise RuntimeError('YouTube: OAuth no corresponde al YT_CHANNEL_ID esperado')
    log('YOUTUBE CANAL VERIFICADO:',matches[0]['id'],matches[0].get('snippet',{}).get('title',''))
    return channel_id


def yt_privacy():
    value=os.getenv('YT_PRIVACY','private').strip().lower()
    if value not in ('public','unlisted','private'): raise RuntimeError('YT_PRIVACY debe ser public, unlisted o private')
    return value


def yt_wide_video(vertical,dest):
    vf=('[0:v]split=2[back][front];'
        '[back]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,'
        'boxblur=30:10,eq=brightness=-0.16[bg];'
        '[front]scale=-2:720[fg];'
        '[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[out]')
    ffmpeg(['-i',vertical,'-filter_complex',vf,'-map','[out]','-map','0:a:0',
            '-c:v','libx264','-preset','veryfast','-crf','23','-r',FPS,
            '-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',dest])
    if not dest.exists() or dest.stat().st_size<10000: raise RuntimeError('No se creó video horizontal YouTube')
    return dest


def yt_upload(movie,title,description,token,kind):
    """Subida reanudable. Un resultado de red ambiguo NUNCA se reintenta a ciegas."""
    privacy=yt_privacy()
    marker='#Shorts' if kind=='short' else '#Sismonauta'
    title=clean(title)[:100]
    description=(description+'\n\n'+marker)[:5000]
    metadata={'snippet':{'title':title,'description':description,'categoryId':'27',
                         'defaultLanguage':'es','tags':['Sismonauta','Sismos','Sismología','Geología','Ciencia']},
              'status':{'privacyStatus':privacy,'selfDeclaredMadeForKids':False}}
    size=movie.stat().st_size
    if size<=0: raise RuntimeError('Archivo YouTube vacío')
    r=requests.post(YOUTUBE_UPLOAD,params={'uploadType':'resumable','part':'snippet,status'},
        headers={'Authorization':'Bearer '+token,'X-Upload-Content-Type':'video/mp4',
                 'X-Upload-Content-Length':str(size),'Content-Type':'application/json; charset=UTF-8'},
        json=metadata,timeout=45,allow_redirects=False)
    if not r.ok: youtube_check_error(r,'inicio de carga')
    session=r.headers.get('Location','')
    parsed=urllib.parse.urlparse(session)
    if parsed.scheme!='https' or parsed.hostname not in ('www.googleapis.com','youtube.googleapis.com'):
        raise RuntimeError('YouTube: URL de sesión inesperada')
    chunk=8*1024*1024; offset=0; retries=0
    with movie.open('rb') as fh:
        while offset<size:
            fh.seek(offset)
            data=fh.read(min(chunk,size-offset))
            end=offset+len(data)-1
            try:
                r=requests.put(session,headers={'Authorization':'Bearer '+token,
                    'Content-Type':'video/mp4','Content-Length':str(len(data)),
                    'Content-Range':f'bytes {offset}-{end}/{size}'},data=data,timeout=180,allow_redirects=False)
                if r.status_code in (200,201):
                    response=r.json(); video_id=str(response.get('id') or '')
                    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id): raise RuntimeError('YouTube: respuesta sin ID válido')
                    return video_id
                if r.status_code==308:
                    rng=r.headers.get('Range','')
                    match=re.search(r'bytes=0-(\d+)',rng)
                    offset=int(match.group(1))+1 if match else 0
                    retries=0
                    continue
                if r.status_code not in (429,500,502,503,504):
                    youtube_check_error(r,'carga')
            except (requests.Timeout,requests.ConnectionError):
                pass
            retries+=1
            if retries>7: raise RuntimeError('YouTube: subida inconclusa; NO repetir sin comprobar el canal')
            time.sleep(min(2**retries,30))
            try:
                probe=requests.put(session,headers={'Authorization':'Bearer '+token,
                    'Content-Length':'0','Content-Range':f'bytes */{size}'},timeout=40,allow_redirects=False)
                if probe.status_code in (200,201):
                    video_id=str(probe.json().get('id') or '')
                    if re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id): return video_id
                if probe.status_code==308:
                    match=re.search(r'bytes=0-(\d+)',probe.headers.get('Range',''))
                    offset=int(match.group(1))+1 if match else 0
                else:
                    if youtube_is_upload_limit(probe.status_code,probe.text):
                        raise YouTubeUploadLimit('YouTube: límite de carga de videos alcanzado')
                    raise RuntimeError('YouTube: estado de sesión incierto; comprobación manual necesaria')
            except requests.RequestException as exc:
                raise RuntimeError('YouTube: no pudo consultarse sesión; comprobación manual necesaria') from exc
    raise RuntimeError('YouTube: carga terminó sin confirmación')


def yt_publish_episode(ep,movie,state,sha,gh,out_dir):
    """Short + horizontal; error aislado, guardando estados en GitHub.

    'aplazado': rechazo definitivo por cuota (seguro reintentar más tarde).
    'incierto': se inició un envío pero no hay confirmación (NO reintentar).
    """
    episode_id=ep['episode_id']
    row=state.setdefault('episodes',{}).setdefault(episode_id,{})
    receipt=Path(out_dir)/'publicacion_confirmada.json'
    report=read_json(receipt,{})

    def save(which,status,video_id=None):
        nonlocal sha
        if video_id: row[which+'_id']=video_id
        row[which]=status; row['updated_at']=utcnow()
        result=gh.write(STATE_PATH,json.dumps(state,ensure_ascii=False,indent=2).encode(),sha)
        sha=result['content']['sha']
        if video_id:
            report[which+'_id']=video_id
            report[which+'_url']='https://www.youtube.com/watch?v='+video_id
            report[which+'_uploaded']=True
            report[which+'_requested_privacy']=yt_privacy()
            write_json(receipt,report)

    for name in ('youtube_short','youtube_video'):
        status=row.get(name)
        if status in ('enviando','incierto'):
            log('YOUTUBE:',name,'en estado incierto; revisar YouTube Studio. Se omite YouTube.')
            report['youtube_warning']=name+': envío anterior incierto'
            write_json(receipt,report)
            return sha
        if status=='publicado' and not re.fullmatch(r'[A-Za-z0-9_-]{11}',str(row.get(name+'_id') or '')):
            raise RuntimeError(name+': publicado sin ID válido')
        if status not in (None,'publicado','aplazado'):
            raise RuntimeError(name+': estado desconocido')

    if youtube_cooldown_active(state):
        log('YOUTUBE APLAZADO: límite de carga; reintento permitido desde',state['youtube_upload_retry_after'])
        report['youtube_warning']='Límite de carga; cooldown activo'
        report['youtube_retry_after']=state['youtube_upload_retry_after']
        write_json(receipt,report)
        return sha

    if all(row.get(name)=='publicado' for name in ('youtube_short','youtube_video')):
        return sha

    # Verificar YouTube SOLO DESPUÉS de publicar en Meta.
    token=yt_token(); yt_verify_channel(token); yt_privacy()
    base=episode_caption_base(ep)+'\n\n'+episode_hashtags(ep)
    for name in ('youtube_short','youtube_video'):
        if row.get(name)=='publicado': continue
        target=movie
        if name=='youtube_video':
            target=Path(out_dir)/(episode_id+'_youtube_16x9.mp4')
            if not target.exists(): yt_wide_video(movie,target)
        save(name,'enviando')
        try:
            video_id=yt_upload(target,ep['title'],base,token,'short' if name=='youtube_short' else 'video')
        except YouTubeUploadLimit:
            # Respuesta explícita de YouTube: no se creó una nueva subida.
            # Dejar enfriamiento persistente para evitar fallos en cada ejecución.
            from datetime import timedelta
            state['youtube_upload_retry_after']=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
            save(name,'aplazado')
            report['youtube_warning']='Límite de subida de YouTube; Meta publicado normalmente'
            report['youtube_retry_after']=state['youtube_upload_retry_after']
            write_json(receipt,report)
            log('YOUTUBE APLAZADO POR LÍMITE:',name,'hasta',state['youtube_upload_retry_after'])
            return sha
        except Exception:
            # Si la solicitud pudo haber llegado a Google, el resultado es incierto.
            save(name,'incierto')
            raise
        save(name,'publicado',video_id)
        log('YOUTUBE',name,'CONFIRMADO:',video_id)
    return sha
# === FIN SISMONAUTA / YOUTUBE ===


class Github:
    def __init__(self):
        self.repo=os.environ['GITHUB_REPOSITORY']; self.token=os.environ['GH_TOKEN']; self.branch=os.getenv('GITHUB_REF_NAME','main')
        self.base='https://api.github.com/repos/'+self.repo
        self.s=requests.Session(); self.s.headers.update({'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
    def api(self,method,path,**kwargs):
        r=self.s.request(method,self.base+path,timeout=60,**kwargs); r.raise_for_status(); return r.json()
    def read(self,path):
        r=self.s.get(self.base+'/contents/'+urllib.parse.quote(path,safe='/'),params={'ref':self.branch},timeout=40)
        if r.status_code==404: return None,None
        r.raise_for_status(); j=r.json(); return base64.b64decode(j['content']),j['sha']
    def write(self,path,data,sha=None):
        payload={'message':'SISMONAUTA SISMOS: '+path,'content':base64.b64encode(data).decode(),'branch':self.branch}
        if sha: payload['sha']=sha
        return self.api('PUT','/contents/'+urllib.parse.quote(path,safe='/'),json=payload)
    def assert_public(self):
        if self.api('GET','').get('private'): raise RuntimeError('El repositorio debe ser público para las URLs multimedia')

def ig(method,endpoint,token,fields):
    url=f'{GRAPH_URL}/{GRAPH_VERSION}/{endpoint}'; fields={**fields,'access_token':token}
    response=requests.get(url,params=fields,timeout=45) if method=='GET' else requests.post(url,data=fields,timeout=65)
    try: data=response.json()
    except ValueError: raise RuntimeError('Instagram no respondió JSON')
    if not response.ok or data.get('error'):
        err=data.get('error') or {}; raise RuntimeError(f'Instagram {response.status_code}: '+str(err.get('message',data))[:250])
    return data
def verify_instagram_account():
    expected=os.getenv('IG_EXPECTED_USERNAME',ACCOUNT).strip().lstrip('@').lower()
    if expected!=ACCOUNT: raise RuntimeError('Cuenta Instagram esperada incorrecta')
    token=os.getenv('INSTAGRAM_TOKEN',''); user=os.getenv('INSTAGRAM_USER_ID','')
    if not token or not user: raise RuntimeError('Faltan credenciales Instagram')
    me=ig('GET','me',token,{'fields':'id,username'})
    if str(me.get('id'))!=str(user) or str(me.get('username','')).lower()!=ACCOUNT: raise RuntimeError('El token no pertenece a @sismonauta')
    log('TOKEN Instagram válido; cuenta verificada: @sismonauta')
def ig_publish(user,token,url,caption,media_type='REELS'):
    data={'media_type':media_type,'video_url':url}
    if media_type=='REELS': data.update({'caption':caption,'share_to_feed':'true'})
    container=ig('POST',user+'/media',token,data)['id']
    for _ in range(30):
        time.sleep(10); status=ig('GET',container,token,{'fields':'status_code,status'}); state=status.get('status_code')
        if state=='FINISHED': break
        if state in ('ERROR','EXPIRED'): raise RuntimeError('Instagram procesamiento fallido: '+str(status)[:200])
    else: raise RuntimeError('Instagram: procesamiento incierto')
    return str(ig('POST',user+'/media_publish',token,{'creation_id':container})['id'])
def fb_api(method,endpoint,token,params=None):
    url=f'https://graph.facebook.com/{GRAPH_VERSION}/{endpoint}'
    r=requests.request(method,url,params={'access_token':token,**(params or {})},timeout=90)
    try: data=r.json()
    except ValueError: raise RuntimeError('Facebook no respondió JSON')
    if not r.ok or data.get('error'):
        error=data.get('error') or {}; raise RuntimeError(f'Facebook HTTP {r.status_code}: '+str(error.get('message',data))[:250])
    return data
def verify_facebook_page():
    page=os.getenv('FACEBOOK_PAGE_ID','').strip(); token=os.getenv('FACEBOOK_PAGE_TOKEN','').strip()
    if not page or not token or not page.isdigit(): raise RuntimeError('Credenciales de Facebook inválidas')
    info=fb_api('GET',page,token,{'fields':'id,name,username'})
    if str(info.get('id'))!=page: raise RuntimeError('Token de fanpage incorrecto')
    expected=os.getenv('FACEBOOK_EXPECTED_USERNAME',ACCOUNT).strip().lstrip('@').lower()
    username=str(info.get('username') or '').lower()
    if username and expected and username!=expected: raise RuntimeError('Fanpage incorrecta: '+username)
    log('PAGINA Facebook validada:',info.get('name',''))
    return page,token
def fb_upload_reels_session(page,token,movie,endpoint):
    session=fb_api('POST',f'{page}/{endpoint}',token,{'upload_phase':'start'})
    video_id=str(session.get('video_id') or ''); upload_url=str(session.get('upload_url') or '')
    if not video_id.isdigit() or not upload_url.startswith('https://rupload.facebook.com/'): raise RuntimeError('Facebook: sesión de subida incompleta')
    headers={'Authorization':'OAuth '+token,'offset':'0','file_size':str(movie.stat().st_size),'Content-Type':'application/octet-stream'}
    with movie.open('rb') as fh: r=requests.post(upload_url,headers=headers,data=fh,timeout=300)
    try: result=r.json()
    except ValueError: raise RuntimeError('Facebook: subida incierta')
    if not r.ok or result.get('success') is not True: raise RuntimeError('Facebook: carga no confirmada')
    return video_id
def fb_publish_reel(page,token,movie,caption,episode_id):
    video_id=fb_upload_reels_session(page,token,movie,'video_reels')
    result=fb_api('POST',f'{page}/video_reels',token,{'upload_phase':'finish','video_id':video_id,'video_state':'PUBLISHED','description':caption[:5000],'title':str(episode_id)[:200]})
    if result.get('success') is not True: raise RuntimeError('Facebook Reel: finalización incierta')
    return video_id
def fb_publish_story(page,token,movie):
    video_id=fb_upload_reels_session(page,token,movie,'video_stories')
    result=fb_api('POST',f'{page}/video_stories',token,{'upload_phase':'finish','video_id':video_id,'video_state':'PUBLISHED'})
    if result.get('success') is not True: raise RuntimeError('Facebook Story: finalización incierta')
    return video_id
def remote_published_topics(state,history): return history_signatures(state,history)[1]

def publish(ep,movie,approval,story=False,auto=False,out_dir=None,facebook=True,youtube=True):
    if not auto and approval!=ep['episode_id']: raise RuntimeError('Se requiere --approved-id')
    if auto and (not ep.get('base_verified') or not ep.get('source','').startswith('https://')): raise RuntimeError('Publicación automática sin base editorial')
    verify_instagram_account(); token=os.environ['INSTAGRAM_TOKEN']; user=os.environ['INSTAGRAM_USER_ID']
    gh=Github(); gh.assert_public(); raw,sha=gh.read(STATE_PATH)
    state=json.loads(raw) if raw else {'schema':1,'episodes':{}}
    items=state.setdefault('episodes',{}); episode_id=ep['episode_id']; row=items.setdefault(episode_id,{})
    old_ids,old_topics,_=history_signatures(state,{'items':[]})
    if episode_id in old_ids and row.get('topic') not in (None,ep['id']): raise RuntimeError('Colisión de ID de episodio')
    if ep['id'] in (old_topics-{row.get('topic')}): raise RuntimeError('Enfoque editorial ya publicado')
    for old_id,old_row in items.items():
        if old_id!=episode_id and old_row.get('topic')==ep['id'] and any(old_row.get(k) in ('publicado','enviando','incierto','aplazado') for k in ('reel','story','facebook_reel','facebook_story','facebook_feed','youtube_short','youtube_video')): raise RuntimeError('Enfoque editorial duplicado')
    row.setdefault('topic',ep['id']); row.setdefault('topic_key',ep['topic_key']); row.setdefault('title',ep['title'])
    destinations=['reel']+(['story'] if story else [])+(['facebook_reel']+(['facebook_story'] if story else []) if facebook else [])
    for dest in destinations:
        status=row.get(dest)
        if status in ('enviando','incierto'): raise RuntimeError(f'{dest}: envío anterior incierto; revisar Meta antes de reintentar')
        if status=='publicado' and not str(row.get(dest+'_id') or '').isdigit(): raise RuntimeError(f'{dest}: publicado sin ID')
        if status not in (None,'publicado'): raise RuntimeError('Estado desconocido del historial')
    def save(which,status):
        nonlocal sha
        row[which]=status; row['updated_at']=utcnow()
        result=gh.write(STATE_PATH,json.dumps(state,ensure_ascii=False,indent=2).encode(),sha)
        sha=result['content']['sha']
    receipt_path=Path(out_dir or movie.parent)/'publicacion_confirmada.json'
    receipt={'account':ACCOUNT,'episode_id':episode_id,'confirmed_at':utcnow()}; write_json(receipt_path,receipt)
    def confirmed(which,mid):
        row[which+'_id']=str(mid); save(which,'publicado')
        receipt[which+'_id']=str(mid); receipt[which+'_published']=True; receipt['confirmed_at']=utcnow(); write_json(receipt_path,receipt)
        log(which.upper(),'ACEPTADO POR META:',mid)
    credits=ep.get('image_credits',[])
    if not credits: raise RuntimeError('Sin fotografías verificadas')
    if any(not license_accepted(c.get('license','')) for c in credits): raise RuntimeError('Sin licencia CC0/PD')
    caption=episode_caption_base(ep)+'\n\n'+episode_hashtags(ep)
    if len(caption)>2200: raise RuntimeError('Descripción demasiado larga')
    pending_ig=[d for d in ('reel','story') if d in destinations and row.get(d)!='publicado']
    url=None
    if pending_ig:
        data=movie.read_bytes(); digest=hashlib.sha256(data).hexdigest()[:16]; remote=f'groundshake_sismos_media/{episode_id}-{digest}.mp4'
        old,_=gh.read(remote)
        if old is None:
            result=gh.write(remote,data); commit=result['commit']['sha']
        else:
            if old!=data: raise RuntimeError('Colisión de archivo MP4')
            commit=gh.api('GET',f'/commits/{gh.branch}')['sha']
        url=f'https://raw.githubusercontent.com/{gh.repo}/{commit}/{remote}'
        r=requests.get(url,timeout=90); r.raise_for_status()
        if len(r.content)!=movie.stat().st_size: raise RuntimeError('URL MP4 incompleta')
    for which in ('reel','story'):
        if which not in destinations or row.get(which)=='publicado': continue
        save(which,'enviando')
        try: media_id=ig_publish(user,token,url,caption if which=='reel' else '',media_type='REELS' if which=='reel' else 'STORIES')
        except BaseException: save(which,'incierto'); raise
        confirmed(which,media_id)
    if facebook:
        page,fb_token=verify_facebook_page()
        for which in ('facebook_reel','facebook_story'):
            if which not in destinations or row.get(which)=='publicado': continue
            save(which,'enviando')
            try:
                if which=='facebook_reel': mid=fb_publish_reel(page,fb_token,movie,caption,episode_id)
                else: mid=fb_publish_story(page,fb_token,movie)
            except BaseException: save(which,'incierto'); raise
            confirmed(which,mid)
    if youtube:
        # YouTube se intenta solo después de Meta. Su fallo no revierte publicaciones ya aceptadas.
        try:
            sha=yt_publish_episode(ep,movie,state,sha,gh,Path(out_dir or movie.parent))
        except Exception as exc:
            warning='YouTube omitido: '+safe_error(exc)
            log('ADVERTENCIA:',warning)
            # Evitar que un recibo parcial sobreescriba confirmaciones registradas por YouTube.
            latest=read_json(receipt_path,receipt); latest['youtube_warning']=warning; write_json(receipt_path,latest)
    log('PUBLICACIONES SOLICITADAS; recibo:',receipt_path)

def metrics(csv_path):
    import csv
    with open(csv_path,encoding='utf-8-sig',newline='') as fh:
        for row in csv.DictReader(fh):
            plays=int(row['plays']); views=int(row['views_7s'])
            if plays<0 or views<0 or views>plays: raise ValueError('Métricas inconsistentes')
            rate=views/plays if plays else 0
            log(f"{row['episode_id']}: R7={rate:.1%} | objetivo=70% | {'ALCANZADO' if rate>=TARGET_R7 else 'NO ALCANZADO'}")

def test_discovery():
    history={'items':[]}; state={'episodes':{}}; seen=set()
    for fact in FACT_BANK:
        for angle in ANGLES:
            ep=make_candidate(fact,angle,'microdocumental')
            assert ep['episode_id'] not in seen,ep['episode_id']
            seen.add(ep['episode_id'])
            assert len(build_storyboard(ep))==6 and ep['source'].startswith('https://')
    assert len(seen)>=1000,f'Banco insuficiente: {len(seen)}'
    old={'¿SABÍAS ESTO?','LA CIENCIA LO EXPLICA','EL DATO CIENTÍFICO','LO QUE DEBES SABER','SIGUE APRENDIENDO'}
    for fact in FACT_BANK:
        ep=make_candidate(fact,ANGLES[0],'microdocumental'); scenes=build_storyboard(ep)
        labels=[row['overlay'] for row in scenes[:5]]
        assert len(set(labels))==5,(ep['topic_key'],labels)
        for row in scenes[:5]:
            assert row['overlay'] not in old,row
            assert len(row['overlay'].split())<=12,row
            assert phrase_tokens(row['overlay']),row
    log('RÓTULOS SEMÁNTICOS: cinco por episodio, sin plantillas genéricas')
    sample=make_candidate(FACT_BANK[0],ANGLES[0],'microdocumental')
    state['episodes'][sample['episode_id']]={'topic':sample['id'],'reel':'publicado'}
    ids,topics,_=history_signatures(state,history)
    assert sample['episode_id'] in ids and sample['id'] in topics
    for key in LEGACY_IDS:
        original='sismos-'+hashlib.sha1((key+STYLES[0]+'¿Sabías esto?').encode()).hexdigest()[:12]
        assert legacy_topic_for_episode(original)==key
    # Rechazo de YouTube claramente recuperable vs fallo incierto.
    sample_error=json.dumps({'error':{'code':400,'message':'The user has exceeded the number of videos they may upload.', 'errors':[{'reason':'uploadLimitExceeded'}]}})
    assert youtube_is_upload_limit(400,sample_error)
    assert not youtube_is_upload_limit(401,'invalid credentials')
    state['youtube_upload_retry_after']=(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
    assert youtube_cooldown_active(state)
    log('PRUEBAS APROBADAS:',len(seen),'combinaciones científicas; historial, legacy y límites YouTube compatibles')

def main():
    parser=argparse.ArgumentParser(description='SISMONAUTA: descubrimiento científico continuo')
    parser.add_argument('--out',default='output_sismos'); parser.add_argument('--topic',default=''); parser.add_argument('--photo-dir',default='')
    parser.add_argument('--no-ai',action='store_true'); parser.add_argument('--no-voice',action='store_true'); parser.add_argument('--publish',action='store_true')
    parser.add_argument('--auto-publish',action='store_true'); parser.add_argument('--approved-id',default=''); parser.add_argument('--story',action='store_true')
    parser.add_argument('--no-facebook',action='store_true'); parser.add_argument('--metrics-csv',default=''); parser.add_argument('--list',action='store_true')
    parser.add_argument('--episode-json',default=''); parser.add_argument('--test-discovery',action='store_true')
    parser.add_argument('--no-youtube',action='store_true',help='No subir Shorts ni videos a YouTube')
    args=parser.parse_args()
    if args.test_discovery: test_discovery(); return 0
    if args.list:
        for fact in FACT_BANK: log(fact[1],SUBJECTS[fact[1]])
        return 0
    if args.metrics_csv: metrics(args.metrics_csv); return 0
    if args.auto_publish and not args.publish: raise RuntimeError('--auto-publish requiere --publish')
    if args.auto_publish and args.episode_json: raise RuntimeError('--auto-publish no admite --episode-json')
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    (out/'publicacion_confirmada.json').unlink(missing_ok=True)
    history_path=out/'historial_sismos.json'; history=read_json(history_path,{'items':[]}); gh=None
    if os.getenv('GH_TOKEN') and os.getenv('GITHUB_REPOSITORY'):
        try:
            gh=Github(); raw,_=gh.read(EDITORIAL_PATH)
            if raw: history=json.loads(raw)
        except Exception as exc:
            if args.publish: raise
            log('Historial remoto no disponible:',safe_error(exc))
    history.setdefault('items',[]); live_state={'episodes':{}}
    if args.publish:
        gh=gh or Github(); raw_state,_=gh.read(STATE_PATH)
        if raw_state: live_state=json.loads(raw_state)
        log('ANTIDUPLICADOS:',sorted(remote_published_topics(live_state,history)))
        verify_instagram_account()
        if not args.no_facebook: verify_facebook_page()
        # La validación YouTube NO es un prerrequisito para producir o publicar en Meta.
    excluded=set(); failures=[]
    for attempt in range(max(1,int(os.getenv('DISCOVERY_MAX_ATTEMPTS','30')))):
        if args.episode_json:
            ep=read_json(args.episode_json,None)
            if not isinstance(ep,dict) or not all(ep.get(k) for k in ('episode_id','title','script','visuals','source','queries')) or not str(ep['episode_id']).startswith('sismos-'): raise RuntimeError('JSON de episodio inválido')
            ep.setdefault('domain','geologia'); ep.setdefault('topic_key',ep.get('id','revisado'))
            if args.auto_publish: raise RuntimeError('No se publica automáticamente un JSON manual')
        else:
            try: ep=choose_topic(history,args.topic,excluded,state=live_state)
            except RuntimeError as exc:
                log('SIN PUBLICACIÓN:',safe_error(exc))
                write_json(out/'estado_ejecucion.json',{'status':'no_new_topics','published':False,'attempted':len(excluded),'checked_at':utcnow()}); return 0
        episode=ep['episode_id']; write_json(out/(episode+'_guion.json'),ep)
        log('EPISODIO:',episode,'TEMA:',ep['title'],'FUENTE:',ep['source'])
        if args.publish and not args.auto_publish and args.approved_id!=episode: raise RuntimeError('Se requiere --approved-id '+episode)
        if args.auto_publish and (not ep.get('base_verified') or not str(ep.get('source','')).startswith('https://')): raise RuntimeError('Falta fuente editorial')
        try:
            movie,cover,credits=render(ep,out,args.photo_dir or None,voice=not args.no_voice)
            break
        except RuntimeError as exc:
            message=str(exc)
            photo_issue=any(t in message for t in ('No se encontraron fotografías auténticas','No hay fotografías válidas','Fotografías sin licencia'))
            if not(photo_issue and not args.topic and not args.episode_json and not args.photo_dir): raise
            excluded.add(ep['id']); failures.append((ep['id'],safe_error(exc)))
            log('REINTENTO VISUAL:',ep['id'],safe_error(exc))
    else:
        write_json(out/'estado_ejecucion.json',{'status':'skipped_no_valid_images','published':False,'attempts':len(failures),'checked_at':utcnow()})
        log('SIN FOTOGRAFÍAS TRAS LOS REINTENTOS'); return 0
    ep['image_credits']=credits; write_json(out/(episode+'_guion.json'),ep)
    if not any(item.get('episode_id')==episode for item in history['items']):
        history['items'].append({'episode_id':episode,'topic':ep['id'],'topic_key':ep['topic_key'],'title':ep['title'],'style':ep.get('style',''), 'angle':ep.get('angle',''),'time':utcnow(),'status':'preparado' if args.publish else 'vista_previa'})
    write_json(history_path,history)
    if args.publish:
        publish(ep,movie,args.approved_id,story=args.story,auto=args.auto_publish,out_dir=out,facebook=not args.no_facebook,youtube=not args.no_youtube)
        for item in history['items']:
            if item.get('episode_id')==episode: item['status']='publicado'
        write_json(history_path,history)
        gh=gh or Github(); raw,sha=gh.read(EDITORIAL_PATH)
        remote=json.loads(raw) if raw else {'items':[]}; remote.setdefault('items',[])
        existing={item.get('episode_id') for item in remote['items']}
        for item in history['items']:
            if item.get('episode_id') not in existing: remote['items'].append(item)
        gh.write(EDITORIAL_PATH,json.dumps(remote,ensure_ascii=False,indent=2).encode(),sha)
    write_json(out/'estado_ejecucion.json',{'status':'published' if args.publish else 'preview_only','episode_id':episode,'topic':ep['id'],'photos':len(credits),'published':bool(args.publish),'checked_at':utcnow()})
    log('OBJETIVO R7:',TARGET_R7,'(experimental)')
    return 0

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc: log('ERROR:',safe_error(exc)); sys.exit(1)
