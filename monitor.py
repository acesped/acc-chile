#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GROUNDSHAKE CL (@groundshake_cl) — edición SISMOS. Un único archivo Python 3.11+.

Genera Reels educativos 9:16 a partir de temas de sismología, sin catálogo fijo.
Usa IA opcional para nuevos enfoques, fuentes científicas de referencia y fotos
CC0/dominio público de Wikimedia Commons/Openverse; el video no se renderiza si
no consigue al menos una foto real. Crea escenas limpias, subtítulos centrales,
voz neural (edge-tts), música original sintetizada y portada. Publicación por
API de Instagram: publicación automática opcional con controles de fuente, identidad y antidualidad.

INSTALAR: pip install pillow requests edge-tts mutagen imageio-ffmpeg
         y un ejecutable ffmpeg en PATH (ver workflow GitHub Actions).
PRUEBA:  python monitor.py --out output
CON IA:  GEMINI_API_KEY=... python monitor.py --out output
PUBLICAR AUTOMATICAMENTE: python monitor.py --publish --auto-publish --story
Variables: INSTAGRAM_TOKEN, INSTAGRAM_USER_ID, IG_EXPECTED_USERNAME=groundshake_cl,
           GH_TOKEN, GITHUB_REPOSITORY=propietario/repo; repositorio público.
Opcional: --photo-dir /ruta/a/fotos para aportar fotografías autorizadas.

Los hechos generados por IA NO se consideran verificados por sí solos. Solo se
admiten temas evergreen de una base de afirmaciones y referencias editables;
cuando IA introduce un hecho nuevo es necesario revisarlo antes de publicar.
"""
from __future__ import annotations
import argparse, asyncio, base64, hashlib, io, json, math, os, random, re, shutil, subprocess, sys, tempfile, time, unicodedata, urllib.parse, wave
from datetime import datetime, timezone
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageFilter, ImageStat

BRAND = 'GROUNDSHAKE CL'
ACCOUNT = 'groundshake_cl'
STATE_PATH = 'groundshake/state.json'  # Historial exclusivo de @groundshake_cl
GRAPH_VERSION = os.getenv('IG_GRAPH_VERSION','v25.0')
GRAPH_URL = os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
W,H,FPS = 720,1280,30
TARGET_R7 = 0.70  # OBJETIVO EXPERIMENTAL: no se garantiza
VOICE = os.getenv('DV_VOICE','es-CL-CatalinaNeural')
USER_AGENT = 'GroundshakeSismos/2.0 (educational research; https://commons.wikimedia.org)'
HTTP = requests.Session(); HTTP.headers.update({'User-Agent':USER_AGENT})

# Temas documentados con referencia informativa verificable. No son noticias.
# Cada ejecución puede abordar otros ángulos, pero nunca inventar cifras.
TOPICS = [
 {'id':'ondas','category':'Ciencia sísmica','title':'¿Por qué hay distintos tipos de ondas sísmicas?',
  'fact':'Las ondas P y S viajan de maneras diferentes; las ondas S no se propagan a través de líquidos.',
  'lesson':'El comportamiento de las ondas revela propiedades del interior terrestre.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards/science/earthquake-science',
  'queries':['seismograph recording instrument','seismometer scientific equipment','geology earth cutaway model']},
 {'id':'magnitud','category':'Mitos y ciencia','title':'¿Por qué magnitud e intensidad no son lo mismo?',
  'fact':'La magnitud describe el tamaño de un terremoto; la intensidad describe los efectos y sacudidas en un lugar.',
  'lesson':'Un mismo terremoto puede sentirse de forma diferente según la ubicación.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards',
  'queries':['seismograph seismogram','earthquake damaged building','seismometer instrument']},
 {'id':'replicas','category':'Ciencia sísmica','title':'¿Por qué ocurren réplicas después de un terremoto?',
  'fact':'Las réplicas son terremotos que siguen a uno mayor en su misma región durante el reajuste de la corteza.',
  'lesson':'La actividad sísmica puede continuar tras un evento principal.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards',
  'queries':['earthquake fault rocks','seismometer monitoring','earthquake geology fault']},
 {'id':'valdivia','category':'Historia sísmica','title':'El mayor terremoto registrado instrumentalmente',
  'fact':'El terremoto de Valdivia, Chile, del 22 de mayo de 1960 alcanzó una magnitud de 9,5.',
  'lesson':'Los registros históricos permiten estudiar terremotos extraordinarios.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards/science/20-largest-earthquakes-world',
  'queries':['1960 Valdivia earthquake Chile','Valdivia Chile 1960 tsunami','1960 Chile earthquake aftermath']},
 {'id':'profundidad','category':'Curiosidades sísmicas','title':'¿Qué diferencia a un terremoto superficial de uno profundo?',
  'fact':'Los terremotos pueden originarse a profundidades muy distintas; la profundidad influye en cómo se perciben sus ondas.',
  'lesson':'La profundidad es un dato importante al interpretar un sismo.',
  'source':'https://earthquake.usgs.gov/earthquakes/map/',
  'queries':['seismic monitoring station','subduction zone geological landscape','tectonic fault mountain']},
 {'id':'placas','category':'Tectónica','title':'¿Por qué se mueve la superficie de la Tierra?',
  'fact':'La litosfera está dividida en placas que se desplazan lentamente y pueden acumular esfuerzos en sus bordes.',
  'lesson':'El movimiento de placas explica gran parte de la actividad sísmica.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards',
  'queries':['tectonic fault landscape','San Andreas Fault aerial','geological rock layers fault']},
 {'id':'tsunamis','category':'Ciencia sísmica','title':'¿Todos los terremotos submarinos provocan tsunamis?',
  'fact':'No todos los terremotos submarinos generan tsunamis; importa el desplazamiento del fondo marino y otros factores.',
  'lesson':'Un terremoto bajo el océano no implica automáticamente un tsunami.',
  'source':'https://www.tsunami.gov/',
  'queries':['ocean coast aerial photograph','tsunami warning sign coast','ocean waves shoreline']},
 {'id':'alerta','category':'Tecnología','title':'¿Una alerta sísmica predice terremotos?',
  'fact':'Los sistemas de alerta temprana detectan un terremoto ya iniciado e intentan avisar antes de la llegada de sacudidas fuertes.',
  'lesson':'La alerta temprana no es una predicción del próximo terremoto.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards/earthquake-early-warning',
  'queries':['seismometer instrument electronics','earthquake monitoring control room','seismic detection sensor']},
 {'id':'falla','category':'Tectónica','title':'¿Qué es realmente una falla geológica?',
  'fact':'Una falla es una fractura de las rocas a lo largo de la cual ha ocurrido desplazamiento.',
  'lesson':'Las fallas ayudan a comprender dónde y cómo se deforman las rocas.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards',
  'queries':['San Andreas Fault California','geological fault rock outcrop','fault scarp terrain']},
 {'id':'prediccion','category':'Mitos y ciencia','title':'¿La ciencia puede predecir el día exacto de un sismo?',
  'fact':'Actualmente no existe un método científicamente validado que prediga con certeza fecha, lugar y magnitud de un gran terremoto.',
  'lesson':'Las estimaciones de probabilidad no son predicciones exactas.',
  'source':'https://www.usgs.gov/faqs/can-you-predict-earthquakes',
  'queries':['seismograph seismogram close up','earthquake research laboratory','seismometer instrument']},
 {'id':'sismografo','category':'Tecnología','title':'¿Cómo registra un instrumento el movimiento del suelo?',
  'fact':'Los sismómetros detectan el movimiento del suelo y producen registros que ayudan a estudiar los terremotos.',
  'lesson':'Las ondas sísmicas permiten investigar fenómenos que no podemos ver directamente.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards',
  'queries':['seismograph recording paper','seismometer field instrument','earthquake science seismograph']},
 {'id':'terremoto','category':'Curiosidades sísmicas','title':'¿Por qué un terremoto lejano puede sentirse?',
  'fact':'Las ondas sísmicas transportan energía a través de la Tierra y algunas pueden recorrer grandes distancias.',
  'lesson':'La distancia al epicentro no explica por sí sola toda la percepción del temblor.',
  'source':'https://www.usgs.gov/programs/earthquake-hazards',
  'queries':['earthquake seismograph','earthquake geological landscape','seismic station']},
]
STYLES = ('descubrimiento','microdocumental','mito_realidad','mapa_explicado','pregunta_respuesta')


def norm(s):
 return ''.join(c for c in unicodedata.normalize('NFKD',str(s).lower()) if not unicodedata.combining(c))
def utcnow(): return datetime.now(timezone.utc).isoformat()
def read_json(path,default):
 try: return json.loads(Path(path).read_text(encoding='utf-8'))
 except FileNotFoundError: return default

def write_json_atomic(path,obj):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)

def generate_angle(topic, history, use_ai=True):
 """Diversificar enfoque sin inventar un hecho: la afirmación base es inmutable."""
 styles=[
  ('descubrimiento','Empieza con una pregunta sorprendente y revela la respuesta antes de los 7 segundos.'),
  ('microdocumental','Relata el dato como un documental conciso con contexto.'),
  ('mito_realidad','Confronta una creencia común únicamente si el hecho base la refuta.'),
  ('mapa_explicado','Destaca el lugar o proceso espacial sin inventar coordenadas.'),
  ('pregunta_respuesta','Presenta una pregunta clara y respóndela con ejemplos sencillos.'),
 ]
 used={(x.get('topic'),x.get('style'),x.get('angle')) for x in history.get('items',[])}
 angles=['¿Sabías esto?','La explicación científica','Lo que muestran los datos','La diferencia importante','La pregunta que importa','Un detalle sorprendente','Lo que no debemos confundir','Lo esencial en segundos']
 combos=[(s,a) for s,_ in styles for a in angles if (topic['id'],s,a) not in used]
 if not combos:
  if not (use_ai and (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY'))):
   raise RuntimeError('Se agotaron los enfoques locales; active Gemini/OpenRouter para proponer enfoques nuevos.')
  # Los enfoques nuevos mantienen SIEMPRE la misma afirmación documentada.
  existing=[x.get('angle','') for x in history.get('items',[]) if x.get('topic')==topic['id']][-80:]
  proposal=ai_json('Crea SOLO JSON con {"angle":"...","style":"..."}. Tema: '+topic['title']+
      '. Hecho documentado: '+topic['fact']+'. Evita repetir estos enfoques: '+json.dumps(existing,ensure_ascii=False)+
      '. El enfoque nuevo es una PREGUNTA editorial breve, distinta de anteriores, y no introduce hechos nuevos. '+
      'style debe ser uno de: '+', '.join(STYLES))
  style=str(proposal.get('style','descubrimiento'))
  if style not in STYLES:style='descubrimiento'
  angle=str(proposal.get('angle','')).strip()[:100]
  if not angle or norm(angle) in {norm(x) for x in existing}:
   raise RuntimeError('La IA no encontró un enfoque editorial suficientemente nuevo; no se repite.')
 else:
  style,angle=random.SystemRandom().choice(combos)
 script=' '.join([topic['title'],topic['fact'],topic['lesson']])
 visuals=[topic['title'],topic['fact'],topic['lesson']]
 if use_ai and (os.getenv('GEMINI_API_KEY') or os.getenv('OPENROUTER_API_KEY')):
  prompt=(f'Eres guionista científico de GROUNDSHAKE CL, cuenta @groundshake_cl. Tema: {topic["title"]}. '
          f'HECHO PERMITIDO: {topic["fact"]} CONCLUSIÓN: {topic["lesson"]}. '
          f'Enfoque: {angle}. Estructura: {style}. '
          'Escribe solo JSON con claves "script" (40-58 palabras) y "visuals" '
          '(lista de seis mensajes, cada uno de 3-10 palabras). '
          'Primer mensaje: gancho visual inmediato, segundo: curiosidad, tercero: primera revelación antes del segundo 7. '
          'No añadas fechas, cifras, causalidades, ejemplos concretos, alertas, predicciones ni hechos nuevos. '
          'No exageres los datos. No uses llamadas alarmistas a la acción. Español natural.')
  try:
   result=ai_json(prompt)
   cand=str(result.get('script','')).strip()
   v=result.get('visuals')
   if 40<=len(cand.split())<=58 and isinstance(v,list) and len(v)==6 and all(isinstance(z,str) and 3<=len(z.split())<=10 and len(z)<=100 for z in v):
    script,visuals=cand,v
   else: print('IA: salida incompatible; se conserva texto basado en hechos.',flush=True)
  except Exception as exc: print('IA no disponible; usando afirmaciones documentadas:',str(exc)[:140],flush=True)
 if len(visuals)!=6:
  visuals=[topic['title'],'Lo que parece evidente no siempre lo es',topic['fact'],
           'La ciencia ayuda a explicarlo',topic['lesson'],'Aprende ciencia con Dato Wow']
 return dict(topic,style=style,angle=angle,script=script,visuals=visuals,
             episode_id='sismos-'+hashlib.sha1((topic['id']+style+angle).encode()).hexdigest()[:12],
             created_at=utcnow(),verified=False,review_note='Revisar guion frente a la fuente antes de publicar')


# Estructuras originales por tema: pregunta, intriga y remate.
# Nunca se añade un dato científico que no esté en TOPICS.
# Cada rótulo es una oración breve, completa y ligada a la locución.
EDITORIAL_BEATS = {
 'ondas': ('¿TODAS LAS ONDAS SÍSMICAS VIAJAN IGUAL?', 'UNA DIFERENCIA LO CAMBIA TODO', 'LAS ONDAS S NO CRUZAN LÍQUIDOS', 'EL INTERIOR DE LA TIERRA DEJA PISTAS'),
 'magnitud': ('¿DOS LUGARES SIENTEN IGUAL UN SISMO?', 'EL NÚMERO NO LO CUENTA TODO', 'MAGNITUD NO ES INTENSIDAD', 'UN SISMO, DIFERENTES EFECTOS'),
 'replicas': ('¿UN TERREMOTO TERMINA CUANDO DEJA DE TEMBLAR?', 'LA CORTEZA SIGUE REAJUSTÁNDOSE', 'DESPUÉS PUEDEN LLEGAR RÉPLICAS', 'EL SILENCIO NO ES EL FINAL'),
 'valdivia': ('¿SABES DÓNDE OCURRIÓ EL MAYOR SISMO REGISTRADO?', 'FUE EN CHILE', 'VALDIVIA 1960: MAGNITUD 9,5', 'LA TIERRA TIENE MEMORIA'),
 'profundidad': ('¿IMPORTA A QUÉ PROFUNDIDAD OCURRE UN SISMO?', 'NO TODO ESTÁ EN LA MAGNITUD', 'LA PROFUNDIDAD CAMBIA CÓMO SE SIENTE', 'MIRA MÁS ALLÁ DEL EPICENTRO'),
 'placas': ('¿CÓMO PUEDE MOVERSE EL SUELO BAJO TUS PIES?', 'EL MOVIMIENTO NO SIEMPRE SE NOTA', 'LAS PLACAS ACUMULAN ESFUERZOS', 'LA TIERRA NUNCA ESTÁ QUIETA'),
 'tsunamis': ('¿TODO SISMO BAJO EL MAR PROVOCA UN TSUNAMI?', 'EL OCÉANO NO RESPONDE SIEMPRE IGUAL', 'NO TODOS GENERAN TSUNAMIS', 'EL DESPLAZAMIENTO DEL FONDO IMPORTA'),
 'alerta': ('¿UNA ALERTA SÍSMICA VE EL FUTURO?', 'LA RESPUESTA TE SORPRENDERÁ', 'EL TERREMOTO YA COMENZÓ', 'UNA ALERTA NO ES UNA PREDICCIÓN'),
 'falla': ('¿QUÉ OCURRE REALMENTE EN UNA FALLA?', 'NO ES SOLO UNA GRIETA', 'LAS ROCAS SE DESPLAZAN', 'EL MOVIMIENTO DEJA HUELLAS'),
 'prediccion': ('¿ALGUIEN PUEDE DECIR CUÁNDO TEMBLARÁ?', 'EXISTE UNA DIFERENCIA CLAVE', 'NO HAY PREDICCIÓN EXACTA VALIDADA', 'PROBABILIDAD NO ES CERTEZA'),
 'sismografo': ('¿CÓMO ESCUCHAMOS UN SISMO INVISIBLE?', 'LA CLAVE ESTÁ EN EL MOVIMIENTO', 'EL SISMÓMETRO REGISTRA EL SUELO', 'LAS ONDAS DEJAN SU FIRMA'),
 'terremoto': ('¿POR QUÉ SE SIENTE UN SISMO TAN LEJOS?', 'LA ENERGÍA NO SE QUEDA QUIETA', 'LAS ONDAS RECORREN GRANDES DISTANCIAS', 'LA DISTANCIA NO CUENTA TODO'),
}

def short_reveal(sentence):
 """Selecciona una unidad semántica completa; jamás añade '...' o '…'."""
 t=re.sub(r'\s+',' ',str(sentence)).strip().rstrip('.,;:')
 if len(t.split())<=9:
  return t
 # Se busca primero una proposición completa, no un prefijo cortado.
 for part in re.split(r'[;:]\s*|,\s*',t):
  part=part.strip().rstrip('.,;:')
  if 3<=len(part.split())<=9:
   return part
 # El texto íntegro es preferible a un rótulo incompleto: clean_scene
 # ajustará automáticamente tipografía y salto de línea.
 return t

def build_storyboard(ep):
 """Pregunta humana → curiosidad → respuesta factual → remate memorable.

 La voz de cada escena se obtiene del mismo mensaje que aparece en pantalla,
 o de una frase factual más desarrollada en la revelación. Sin mensajes ajenos.
 """
 key=str(ep.get('id',''))
 fact=str(ep['fact']).strip()
 lesson=str(ep['lesson']).strip()
 labels=EDITORIAL_BEATS.get(key)
 if not labels:
  title=str(ep['title']).strip().rstrip('.')
  hook=title if title.startswith('¿') else '¿Qué revela este fenómeno sísmico?'
  labels=(hook,'HAY UNA EXPLICACIÓN CIENTÍFICA',short_reveal(fact),short_reveal(lesson))
 hook,tease,reveal,close=labels
 # Primera escena: pregunta audible y visible. La revelación lleva la evidencia.
 # La intriga no presenta una afirmación científica nueva.
 parts=[(hook,hook,'pregunta'),(tease,tease,'intriga'),
        (fact,reveal,'revelacion'),(lesson,close,'cierre')]
 storyboard=[]
 queries=list(ep.get('queries') or ['earthquake seismograph'])
 for i,(narration,overlay,stage) in enumerate(parts):
  narration=str(narration).strip()
  overlay=re.sub(r'(?:\.{3}|…)+','',str(overlay)).strip()
  if not overlay or not narration:raise ValueError('Escena sin contenido')
  if len(overlay.split())>12:
   raise ValueError('Rótulo editorial demasiado largo: '+overlay)
  storyboard.append({'narration':narration,'overlay':overlay,
                     'query':queries[min(i,len(queries)-1)],'stage':stage})
 return storyboard


# Títulos o palabras clave compatibles con fotografías del fenómeno;
# se rechazan paisajes genéricos aunque la búsqueda los haya devuelto.
PHOTO_TERMS={
 'ondas':('seism','earthquake','seismo','sismo','terremot'),
 'magnitud':('earthquake','seism','terremot','sismo','damage'),
 'replicas':('earthquake','seism','terremot','sismo','fault'),
 'valdivia':('valdivia','1960 chile earthquake','1960 chile tsunami'),
 'profundidad':('seism','earthquake','subduction','sismo','terremot'),
 'placas':('tectonic','fault','subduction','seism','earthquake'),
 'tsunamis':('tsunami','earthquake','terremot','maremoto'),
 'alerta':('seism','earthquake','early warning','sism','terremot'),
 'falla':('fault','tectonic','earthquake','falla geol'),
 'prediccion':('seism','earthquake','sism','terremot'),
 'sismografo':('seismograph','seismometer','seismogram','sismograf','sismometr'),
 'terremoto':('earthquake','seism','terremot','sismo'),
}

# Etiquetas que señalan materiales informativos, no fotografía documental.
# No se descarta "File:" indiscriminadamente porque Commons nombra así TODAS las fotos.
NON_PHOTO_TERMS = (
 'diagram','diagrama','infographic','infografia','flowchart','flow chart',
 'schematic','schema','esquema','cross section','cross-section','cutaway',
 'seismogram','waveform','spectrogram','graph','graphic','chart','plot',
 'hazard map','seismic map','mapa','cartography','map ','illustration',
 'drawing','dibujo','cartoon','vector','icon','logo','poster','banner',
 'slide','presentation','powerpoint','screenshot','screen capture',
 'screengrab','table ','figure ','fig.','pdf','paper','journal',
 'proceedings','research report','technical report','book cover',
 'newspaper','document','scanned','scan of','textbook',
 'certificate','brochure','leaflet','manual','report of','guide for',
 'advertisement','stock photo','watermark','labeled','labelled',
 'sonar','buried objects','target classification'
)

def photo_relevant(item,ep):
 """Priorizar fotos documentales, excluir infografías y estudios científicos."""
 title=norm(str(item.get('title','') or ''))
 desc=norm(str(item.get('description','') or ''))
 tags=norm(str(item.get('tags','') or ''))
 metadata=' '.join((title,desc,tags))
 if not metadata.strip():return False
 if re.search(r'\.(?:pdf|djvu|tiff?|svg|gif)(?:$|[?# ])',title):return False
 if any(term in title for term in NON_PHOTO_TERMS):return False
 # Las palabras prohibidas en etiquetas/descripción suelen indicar gráficos también.
 if any(term in tags for term in NON_PHOTO_TERMS if len(term)>5):return False
 terms=PHOTO_TERMS.get(ep.get('id'),('earthquake','seism','sismo','terremot','tectonic'))
 return any(norm(x) in metadata for x in terms)


def visual_photo_audit(path):
 """Rechazar páginas, gráficos y fotos con texto; no afirmar certeza absoluta.

 Tesseract es opcional. Sin OCR hacemos inspección de aspecto, saturación,
 luminosidad y bordes: si no resulta convincente se rechaza (fail closed).
 """
 try:
  with Image.open(path) as src:
   rgb=ImageOps.exif_transpose(src).convert('RGB')
   if rgb.width<500 or rgb.height<350:return False,'resolución insuficiente'
   sample=ImageOps.contain(rgb,(480,480))
   # Fondos predominantemente blancos son habituales en papers y diagramas.
   pixels=list(sample.resize((160,160)).getdata())
   white=sum(1 for r,g,b in pixels if min(r,g,b)>224 and max(r,g,b)-min(r,g,b)<20)/len(pixels)
   if white>0.58:return False,'fondo documental predominantemente blanco'
   gray=sample.convert('L')
   edges=gray.filter(ImageFilter.FIND_EDGES)
   avg_edge=ImageStat.Stat(edges).mean[0]
   # Un umbral extremo evita descartar fotografías de escombros con textura.
   if avg_edge>63:return False,'densidad extrema de bordes'
   if shutil.which('tesseract'):
    # TSV incluye confianza: evita confundir rocas/escombros con letras.
    try:
     result=subprocess.run(['tesseract',str(path),'stdout','--psm','11','tsv'],
       capture_output=True,text=True,timeout=18)
     if result.returncode==0:
      import csv
      rows=list(csv.DictReader(io.StringIO(result.stdout),delimiter='\t'))
      confident=[]
      for row in rows:
       word=(row.get('text') or '').strip()
       try:confidence=float(row.get('conf') or '-1')
       except ValueError:confidence=-1
       if confidence>=75 and re.fullmatch(r'[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}',word):
        confident.append(word)
      if len(confident)>=3:return False,'texto visible detectado por OCR (alta confianza)'
    except (OSError,subprocess.TimeoutExpired):
     pass
   else:
    # Si no hay OCR, limitar gráficos habituales: página pálida con aristas.
    if white>0.27 and avg_edge>24:return False,'posible diagrama sin OCR disponible'
   return True,'imagen apta por inspección automática'
 except Exception as exc:return False,'imagen ilegible: '+str(exc)[:80]


def license_accepted(code):
 """Solo dominio público o Creative Commons que permite uso comercial y adaptación."""
 code=norm(str(code)).strip().lower().replace('_','-')
 compact=re.sub(r'[^a-z0-9]+','',code)
 if compact.startswith(('cc0','publicdomain','publicdomainmark','pdm','pdold','pdus','pdself')):
  return True
 if compact.startswith(('ccby','ccbysa')) and not any(x in compact for x in ('nc','nd')):
  return True
 if compact.startswith(('bysa','by')) and not any(x in compact for x in ('nc','nd')):
  return True
 return False


def ai_json(prompt):
 """Gemini con descubrimiento de modelos y respaldo gratuito OpenRouter.
 Un error 404 del modelo no bloquea la generación del episodio.
 """
 problems=[]
 key=os.getenv('GEMINI_API_KEY','').strip()
 if key:
  configured=os.getenv('GEMINI_MODEL','').strip().removeprefix('models/')
  candidates=[configured] if configured else []
  try:
   listing=HTTP.get('https://generativelanguage.googleapis.com/v1beta/models',
      headers={'x-goog-api-key':key},params={'pageSize':100},timeout=25)
   listing.raise_for_status()
   available=[]
   for m in listing.json().get('models',[]):
    name=str(m.get('name','')).removeprefix('models/')
    if name.startswith('gemini-') and 'generateContent' in (m.get('supportedGenerationMethods') or []) and not any(z in name for z in ('image','audio','tts','embedding','live')):
     available.append(name)
   available.sort(key=lambda name:(0 if 'flash-lite' in name else 1 if 'flash' in name else 2, 'preview' in name, name))
   candidates.extend(available[:6])
  except Exception as exc:problems.append('Listado Gemini: '+str(exc)[:110])
  for model in dict.fromkeys(candidates):
   try:
    r=HTTP.post('https://generativelanguage.googleapis.com/v1beta/models/'+urllib.parse.quote(model,safe='')+':generateContent',
      headers={'x-goog-api-key':key},json={'contents':[{'parts':[{'text':prompt}]}],
      'generationConfig':{'temperature':0.35,'maxOutputTokens':1600,'responseMimeType':'application/json'}},timeout=90)
    if r.status_code in (400,404,429,503):
     problems.append(f'Gemini {model}: HTTP {r.status_code}');continue
    r.raise_for_status()
    raw=''.join(str(part.get('text','')) for part in (r.json().get('candidates',[{}])[0].get('content',{}).get('parts') or []))
    if not raw.strip():raise ValueError('Respuesta sin texto')
    break
   except Exception as exc:
    problems.append(f'Gemini {model}: {str(exc)[:100]}')
  else:raw=None
 else:raw=None
 if raw is None:
  key=os.getenv('OPENROUTER_API_KEY','').strip()
  if not key:raise RuntimeError('Sin modelo accesible. '+'; '.join(problems[-5:]))
  model=os.getenv('OPENROUTER_MODEL','openrouter/free')
  if model!='openrouter/free' and not model.endswith(':free'):
   raise RuntimeError('Solo se permite OpenRouter gratuito')
  r=HTTP.post('https://openrouter.ai/api/v1/chat/completions',
    headers={'Authorization':'Bearer '+key},json={'model':model,'messages':[{'role':'user','content':prompt}],
    'temperature':0.35,'max_tokens':1400},timeout=110)
  r.raise_for_status();raw=r.json()['choices'][0]['message']['content']
 if not isinstance(raw,str):raise ValueError('Respuesta IA no textual')
 raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw.strip(),flags=re.I)
 match=re.search(r'\{[\s\S]*\}',raw)
 if not match:raise ValueError('IA no devolvió JSON')
 return json.loads(match.group())


def choose_topic(history, requested=''):
 if requested:
  matches=[x for x in TOPICS if x['id']==requested]
  if not matches: raise ValueError('Tema desconocido. Disponibles: '+', '.join(x['id'] for x in TOPICS))
  return matches[0]
 counts={x['id']:0 for x in TOPICS}
 for item in history.get('items',[]):
  if item.get('topic') in counts: counts[item['topic']]+=1
 minimum=min(counts.values())
 return random.SystemRandom().choice([x for x in TOPICS if counts[x['id']]==minimum])


def commons(query):
 p=dict(action='query',format='json',generator='search',gsrsearch=query,
        gsrnamespace=6,gsrlimit=40,prop='imageinfo',
        iiprop='url|extmetadata|size',iiurlwidth=1400)
 r=HTTP.get('https://commons.wikimedia.org/w/api.php',params=p,timeout=24);r.raise_for_status()
 pages=(r.json().get('query') or {}).get('pages',{})
 for page in pages.values():
  a=(page.get('imageinfo') or [{}])[0];meta=a.get('extmetadata') or {}
  def val(k):return re.sub('<[^>]+>',' ',str((meta.get(k) or {}).get('value','')))
  license_=val('LicenseShortName').strip()
  if not license_accepted(license_):continue
  url=a.get('thumburl') or a.get('url')
  title=page.get('title','')
  if not re.search(r'\.(?:jpg|jpeg|png|webp)$',title,re.I):continue
  if not url or not re.search(r'\.(jpg|jpeg|png|webp)(?:\?|$)',url,re.I):continue
  if url: yield {'url':url,'source':a.get('descriptionurl',''),
    'license':license_,'title':title,'description':val('ImageDescription'),
    'creator':val('Artist') or 'Autor no identificado',
    'tags':'','provider':'Wikimedia'}

def openverse(query):
 r=HTTP.get('https://api.openverse.org/v1/images/',
   params={'q':query,'license':'cc0,pdm,by,by-sa','category':'photograph','page_size':20},timeout=25)
 r.raise_for_status()
 for a in r.json().get('results',[]):
  license_=str(a.get('license','')).lower()
  if not license_accepted(license_):continue
  url=a.get('url') or a.get('thumbnail')
  # Openverse también puede indexar documentos rasterizados; exigir soporte fotográfico.
  if str(a.get('mature','')).lower()=='true':continue
  if url: yield {'url':url,'source':a.get('foreign_landing_url',''),
      'license':license_,'title':a.get('title',''),
      'description':'','tags':' '.join(str(t.get('name','')) for t in (a.get('tags') or []) if isinstance(t,dict)),
      'creator':a.get('creator') or 'Autor no identificado','provider':'Openverse'}


def photo_download(item,dest):
 """Aceptar solo JPG/PNG/WebP de HTTPS y tamaño limitado; no seguir hosts arbitrarios internos."""
 import ipaddress, socket
 from urllib.parse import urlparse
 url=item['url'];u=urlparse(url)
 if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443): return False
 try:
  addrs=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
  if not addrs or not all(ipaddress.ip_address(a[4][0]).is_global for a in addrs):return False
  # requests valida HTTPS; desactivar redirecciones hacia destinos no validados.
  for _ in range(5):
   with HTTP.get(url,timeout=(8,22),stream=True,allow_redirects=False) as r:
    if r.status_code in (301,302,303,307,308):
     url=urllib.parse.urljoin(url,r.headers.get('Location',''));u=urlparse(url)
     if u.scheme!='https' or not u.hostname: return False
     addrs=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
     if not addrs or not all(ipaddress.ip_address(a[4][0]).is_global for a in addrs):return False
     continue
    r.raise_for_status()
    if not any(x in r.headers.get('Content-Type','').lower() for x in ('image/jpeg','image/png','image/webp')):return False
    data=bytearray()
    for chunk in r.iter_content(65536):
     data.extend(chunk)
     if len(data)>12_000_000:return False
    with Image.open(io.BytesIO(data)) as im:
     im=ImageOps.exif_transpose(im)
     if im.width<500 or im.height<350:return False
     im.convert('RGB').save(dest,quality=91)
    return True
 except Exception as exc: print('Imagen no aceptada:',str(exc)[:85],flush=True)
 return False


def get_photos(ep,folder,local_dir=None):
 """La ausencia de fotos reales bloquea el render: nunca genera fondos planos."""
 found=[];credits=[]; seen=set()
 if local_dir:
  for f in sorted(Path(local_dir).iterdir()):
   if f.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
    try:
     with Image.open(f) as im:
      if im.width>=500 and im.height>=350 and visual_photo_audit(f)[0]:
       found.append(f);credits.append({'source':str(f),'license':'Verificar autorización de uso'})
    except Exception: pass
  if not found:raise RuntimeError('No se encontraron fotos válidas en --photo-dir')
  return found,credits
 queries=list(dict.fromkeys([*ep['queries'],
    'earthquake seismometer','seismograph earthquake','seismic monitoring station',
    'earthquake early warning seismometer', 'earthquake monitoring equipment']))
 for q in queries:
  for provider in (commons,openverse):
   try:
    results=list(provider(q))
   except Exception as exc:
    print(f'Fotos {provider.__name__} / {q}: {str(exc)[:110]}',flush=True);continue
   accepted=0
   for item in results:
    if item['url'] in seen:continue
    seen.add(item['url'])
    if not photo_relevant(item,ep):continue
    path=folder/f'foto_{len(found)+1}.jpg'
    if photo_download(item,path):
     okay,reason=visual_photo_audit(path)
     if not okay:
      print('FOTO DESCARTADA:',reason,'|',str(item.get('title',''))[:95],flush=True)
      path.unlink(missing_ok=True)
      continue
     found.append(path)
     credits.append({k:item.get(k,'') for k in ('source','license','title','creator','provider','description')})
     accepted+=1
     print('Foto válida:',item['title'][:85],'licencia:',item['license'],flush=True)
     if accepted>=2 or len(found)>=3:break
   if len(found)>=3:break
  if len(found)>=3:break
 if not found: raise RuntimeError('No se encontraron FOTOGRAFÍAS limpias y pertinentes (sin documentos, gráficos o texto) con licencia reutilizable con licencia reutilizable (CC0, PD, CC BY o CC BY-SA); se cancela el Reel.')
 return found,credits


def font(size,bold=True):
 options=(['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf']
          if bold else ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'])
 for p in options:
  if Path(p).exists():return ImageFont.truetype(p,size)
 return ImageFont.load_default()

def wrap(draw,text,ft,width):
 out=[];line=''
 for word in text.split():
  attempt=(line+' '+word).strip()
  if draw.textbbox((0,0),attempt,font=ft)[2]<=width:line=attempt
  else:
   if line:out.append(line)
   line=word
 if line:out.append(line)
 return out

def clean_scene(photo,message,dest):
 """Fondo fotográfico limpio; adapta textos largos sin truncarlos."""
 with Image.open(photo) as src:
  im=ImageOps.fit(ImageOps.exif_transpose(src).convert('RGB'),(W,H),method=Image.Resampling.LANCZOS)
 im=im.filter(ImageFilter.GaussianBlur(radius=6))
 im=ImageEnhance.Color(im).enhance(0.78)
 im=ImageEnhance.Contrast(im).enhance(0.93)
 # Fondo oscuro, pero conservando la fotografía como protagonista.
 im=Image.blend(im,Image.new('RGB',(W,H),(0,0,0)),0.49)
 # Centro más oscuro sin recuadros, líneas ni cintillos.
 veil=Image.new('RGBA',(W,H),(0,0,0,0));vp=veil.load()
 for yy in range(H):
  strength=max(0.0,1.0-abs(yy-H*0.5)/(H*0.43))
  alpha=int(56*strength*strength)
  ImageDraw.Draw(veil).line((0,yy,W,yy),fill=(0,0,0,alpha))
 im=Image.alpha_composite(im.convert('RGBA'),veil).convert('RGB')
 draw=ImageDraw.Draw(im)
 message=re.sub(r'\s+',' ',str(message or '')).replace('…','').replace('...','').strip()
 if not message:raise ValueError('Escena sin texto')
 # Más espacio vertical, tipografía realmente adaptable. No cortar mensajes.
 max_width=W-104
 choices=[]
 for size in range(65,17,-1):
  ft=font(size)
  lines=wrap(draw,message.upper(),ft,max_width)
  line_height=int(size*1.27)
  if not lines or len(lines)>8 or len(lines)*line_height>H*0.63:continue
  if any(draw.textbbox((0,0),line,font=ft,stroke_width=3)[2]>max_width for line in lines):continue
  choices.append((size,ft,lines,line_height))
  break
 if not choices:
  raise ValueError('Escena imposible de componer sin recortar: '+message[:90])
 size,ft,lines,line_height=choices[0]
 y=(H-len(lines)*line_height)//2
 for line in lines:
  bbox=draw.textbbox((0,0),line,font=ft,stroke_width=3)
  x=(W-(bbox[2]-bbox[0]))/2
  draw.text((x,y),line,font=ft,fill='white',stroke_width=3,stroke_fill='#09131C')
  y+=line_height
 im.save(dest,quality=92)


def scene_durations(visuals, voice_duration=0.0):
 """Dar tiempo a leer: velocidad conservadora de 2.1 palabras/segundo y pausa.

 La voz se respeta, sin acelerarla. Se reparte tiempo adicional proporcionalmente
 a las palabras cuando la locución necesita más tiempo que la lectura visual.
 """
 if not visuals: raise ValueError('No hay escenas')
 durations=[]
 for msg in visuals:
  words=len(re.findall(r"\b[\wÁÉÍÓÚÜÑáéíóúüñ]+\b", str(msg)))
  duration=max(5.0, 1.5 + words / 2.1)
  durations.append(round(duration, 2))
 # Garantizar que toda la narración pueda terminar antes del último fotograma.
 needed=max(0.0, float(voice_duration)+1.5-sum(durations))
 if needed:
  weight=[max(1.0,d) for d in durations]
  total=sum(weight)
  durations=[round(d+needed*w/total,2) for d,w in zip(durations,weight)]
 return durations


def soundtrack(path,secs=45,sr=22050):
 """Cama musical instrumental original, audible y sin reclamaciones de terceros."""
 import array
 n=int(math.ceil(secs*sr));buf=array.array('h')
 for i in range(n):
  t=i/sr
  fade=max(0.,min(1.,t/1.3,(secs-t)/2.0))
  # Arpegio lento, colchón armónico y pulso leve; amplitud normalizada a PCM.
  note=(130.81,164.81,196.00,261.63)[int(t*2)%4]
  pad=(0.36*math.sin(2*math.pi*130.81*t)
       +0.22*math.sin(2*math.pi*196*t)
       +0.13*math.sin(2*math.pi*261.63*t))
  pulse=0.15*math.sin(2*math.pi*note*t)*(0.5+0.5*math.cos(2*math.pi*2*t))
  shimmer=0.07*math.sin(2*math.pi*392*t)*(0.5+0.5*math.sin(2*math.pi*t/8))
  sample=max(-1.,min(1.,(pad+pulse+shimmer)*0.55*fade))
  buf.append(int(32767*sample))
 with wave.open(str(path),'wb') as wf:
  wf.setnchannels(1);wf.setsampwidth(2);wf.setframerate(sr);wf.writeframes(buf.tobytes())

async def speak(text,out):
 import edge_tts
 await edge_tts.Communicate(text,voice=VOICE,rate='+8%').save(str(out))
 if not out.exists() or out.stat().st_size<1000:raise RuntimeError('Voz no generada')

def ffmpeg(cmd):
 p=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,cmd)],capture_output=True,text=True)
 if p.returncode:raise RuntimeError('FFmpeg: '+p.stderr[-1400:])

def render(ep,out,photo_dir=None,voice=True):
 """Audio individual por escena: imagen, voz y rótulo cambian juntos."""
 if not shutil.which('ffmpeg'):raise RuntimeError('ffmpeg no instalado')
 folder=out/ep['episode_id'];folder.mkdir(parents=True,exist_ok=True)
 storyboard=build_storyboard(ep)
 if len(storyboard)<3:raise RuntimeError('Historia sin suficientes ideas comprobables')
 ep['storyboard']=storyboard
 # El relato publicado coincide exactamente con las frases asignadas a escenas.
 ep['script']=' '.join(z['narration'] for z in storyboard)
 ep['visuals']=[z['overlay'] for z in storyboard]
 write_json_atomic(folder/'storyboard.json',storyboard)
 photos,credits=get_photos(ep,folder,photo_dir)
 if not local_photo_audit(photos,credits,ep,photo_dir):
  raise RuntimeError('Fotografías sin pertinencia sísmica suficiente')
 if ep.get('base_verified') and len(photos)<2:
  raise RuntimeError('Publicación autónoma detenida: se necesitan al menos dos fotografías sísmicas pertinentes')
 durations=[];voices=[];has_voice=False
 for i,beat in enumerate(storyboard):
  if voice:
   clip=folder/f'voz_escena_{i:02d}.mp3'
   try:
    asyncio.run(speak(beat['narration'],clip))
    from mutagen.mp3 import MP3
    sec=float(MP3(str(clip)).info.length)
    voices.append(clip)
    has_voice=True
   except Exception as exc:
    raise RuntimeError(f'La voz de la escena {i+1} falló; no publicar un video desincronizado: {exc}')
  else:sec=0
  # El texto permanece mientras se pronuncia su oración + pausa para lectura.
  words=len(beat['overlay'].split())
  durations.append(round(max(sec+0.5, 3.3 if i==0 else (2.6 if i==1 else 4.5), words/(2.8 if i<2 else 2.2)+0.6),2))
 total=round(sum(durations),2)
 print('SINCRONÍA escenas:',durations,'duración total:',total,flush=True)
 print('ESTRUCTURA: PREGUNTA → INTRIGA → REVELACIÓN → CIERRE',flush=True)
 frame_paths=[]
 for i,beat in enumerate(storyboard):
  frame=folder/f'escena_{i:02d}.jpg'
  clean_scene(photos[i%len(photos)],beat['overlay'],frame)
  frame_paths.append(frame)
 cover=out/(ep['episode_id']+'_portada.jpg');shutil.copy2(frame_paths[0],cover)
 music=folder/'musica.wav';soundtrack(music,secs=total+1)
 inputs=[]
 for frame,d in zip(frame_paths,durations):
  inputs+=['-loop','1','-framerate',str(FPS),'-t',str(d),'-i',frame]
 mi=len(frame_paths);inputs+=['-i',music]
 voice_start=mi+1
 for v in voices:inputs+=['-i',v]
 filt=[]
 for i in range(len(frame_paths)):
  filt.append(f'[{i}:v]scale={W}:{H},setsar=1,format=yuv420p[v{i}]')
 filt.append(''.join(f'[v{i}]' for i in range(len(frame_paths)))+f'concat=n={len(frame_paths)}:v=1:a=0[v]')
 if has_voice:
  for i,d in enumerate(durations):
   filt.append(f'[{voice_start+i}:a]aresample=44100,apad,atrim=duration={d},asetpts=PTS-STARTPTS[a{i}]')
  filt.append(''.join(f'[a{i}]' for i in range(len(voices)))+f'concat=n={len(voices)}:v=0:a=1[narr]')
  filt.append(f'[{mi}:a]volume=0.30[mus];[narr]volume=1.0[nar];[mus][nar]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.94[a]')
 else:
  filt.append(f'[{mi}:a]volume=0.65,alimiter=limit=0.94[a]')
 movie=out/(ep['episode_id']+'.mp4')
 ffmpeg([*inputs,'-filter_complex',';'.join(filt),'-map','[v]','-map','[a]','-t',str(total),
         '-r',str(FPS),'-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p',
         '-c:a','aac','-b:a','160k','-movflags','+faststart',movie])
 write_json_atomic(folder/'creditos.json',credits)
 write_json_atomic(folder/'duraciones_escenas.json',{'segundos':durations,'total':total,
  'sincronizacion':'voz separada por escena; rótulo basado en la misma oración','musica':'instrumental_original'})
 print('VIDEO:',movie,'PORTADA:',cover,'VOZ sincronizada:',has_voice,flush=True)
 return movie,cover,credits


def local_photo_audit(photos,credits,ep,photo_dir):
 if photo_dir:
  print('AVISO: fotografías aportadas manualmente; revisar pertinencia y licencias antes de publicar',flush=True)
  return True
 return all(photo_relevant(c,ep) and license_accepted(c.get('license','')) for c in credits)

class Github:
 def __init__(self):
  self.repo=os.environ['GITHUB_REPOSITORY'];self.token=os.environ['GH_TOKEN']
  self.branch=os.getenv('GITHUB_REF_NAME','main');self.base=f'https://api.github.com/repos/{self.repo}'
  self.s=requests.Session();self.s.headers.update({'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
 def api(self,method,path,**kw):
  r=self.s.request(method,self.base+path,timeout=50,**kw);r.raise_for_status();return r.json()
 def read(self,path):
  r=self.s.get(self.base+'/contents/'+urllib.parse.quote(path,safe='/'),params={'ref':self.branch},timeout=40)
  if r.status_code==404:return None,None
  r.raise_for_status();j=r.json();return base64.b64decode(j['content']),j['sha']
 def write(self,path,data,sha=None):
  payload={'message':'GROUNDSHAKE CL SISMOS: '+path,'content':base64.b64encode(data).decode(),'branch':self.branch}
  if sha:payload['sha']=sha
  return self.api('PUT','/contents/'+urllib.parse.quote(path,safe='/'),json=payload)
 def assert_public(self):
  if self.api('GET','').get('private'):raise RuntimeError('Repositorio GitHub debe ser público para la URL multimedia')

def ig(method,endpoint,token,fields):
 fields=dict(fields,access_token=token)
 url=f'{GRAPH_URL}/{GRAPH_VERSION}/{endpoint}'
 r=requests.get(url,params=fields,timeout=45) if method=='GET' else requests.post(url,data=fields,timeout=65)
 j=r.json();
 if not r.ok or j.get('error'):raise RuntimeError(f'Instagram {r.status_code}: {str(j)[:400]}')
 return j

def ig_publish(user,token,url,caption,media_type='REELS'):
 data={'media_type':media_type,'video_url':url}
 if media_type=='REELS':data.update(caption=caption,share_to_feed='true')
 cid=ig('POST',f'{user}/media',token,data)['id']
 for _ in range(30):
  time.sleep(10);status=ig('GET',cid,token,{'fields':'status_code,status'})
  if status.get('status_code')=='FINISHED':break
  if status.get('status_code') in ('ERROR','EXPIRED'):raise RuntimeError('Procesamiento Instagram falló: '+str(status))
 else:raise RuntimeError('Timeout; publicación incierta, no reintentar automáticamente')
 return ig('POST',f'{user}/media_publish',token,{'creation_id':cid})['id']

def verify_instagram_account():
 """Fail early on revoked token / wrong destination account."""
 expected=os.getenv('IG_EXPECTED_USERNAME',ACCOUNT).strip().lstrip('@').lower()
 if expected!=ACCOUNT:raise RuntimeError('Cuenta esperada incorrecta: @'+expected)
 token=os.getenv('INSTAGRAM_TOKEN','');user=os.getenv('INSTAGRAM_USER_ID','')
 if not token or not user:raise RuntimeError('Faltan INSTAGRAM_TOKEN / INSTAGRAM_USER_ID')
 me=ig('GET','me',token,{'fields':'id,username'})
 if str(me.get('id'))!=user or str(me.get('username','')).lower()!=ACCOUNT:
  raise RuntimeError('Token de Meta asociado a otra cuenta de Instagram; se requiere @'+ACCOUNT)
 print('TOKEN Instagram válido; cuenta verificada: @'+ACCOUNT,flush=True)

def publish(ep,movie,approval,story=False,auto=False,out_dir=None):
 if not auto and approval!=ep['episode_id']:raise RuntimeError('Necesita --approved-id que coincida con el episodio')
 if auto and not ep.get('base_verified'):
  raise RuntimeError('Publicación autónoma bloqueada: episodio no basado en hechos editoriales verificados')
 expected=os.getenv('IG_EXPECTED_USERNAME',ACCOUNT).strip().lstrip('@').lower()
 if expected!=ACCOUNT:raise RuntimeError('Cuenta de destino debe ser @'+ACCOUNT)
 token=os.environ['INSTAGRAM_TOKEN'];user=os.environ['INSTAGRAM_USER_ID']
 me=ig('GET','me',token,{'fields':'id,username'})
 if str(me.get('id'))!=user or me.get('username','').lower()!=ACCOUNT:raise RuntimeError('Token Instagram apunta a otra cuenta')
 gh=Github();gh.assert_public()
 raw,sha=gh.read(STATE_PATH);state=json.loads(raw) if raw else {'schema':1,'episodes':{}}
 items=state.setdefault('episodes',{});old=items.get(ep['episode_id'],{})
 if any(old.get(x) in ('enviando','publicado','incierto') for x in ('reel','story')):raise RuntimeError('Ya publicado o envío incierto')
 contents=movie.read_bytes();digest=hashlib.sha256(contents).hexdigest()[:16]
 remote=f'groundshake_sismos_media/{ep["episode_id"]}-{digest}.mp4'
 old_file,_=gh.read(remote)
 if old_file is None:receipt=gh.write(remote,contents);commit=receipt['commit']['sha']
 else:
  if old_file!=contents:raise RuntimeError('Colisión de medios')
  commit=gh.api('GET',f'/commits/{gh.branch}')['sha']
 url=f'https://raw.githubusercontent.com/{gh.repo}/{commit}/{remote}'
 with requests.get(url,timeout=90) as r:
  r.raise_for_status()
  if len(r.content)!=movie.stat().st_size:raise RuntimeError('URL del MP4 incompleta')
 def save(status,which='reel'):
  nonlocal sha
  row=items.setdefault(ep['episode_id'],{});row[which]=status;row['updated_at']=utcnow()
  receipt=gh.write(STATE_PATH,json.dumps(state,ensure_ascii=False,indent=2).encode(),sha)
  sha=receipt['content']['sha']
 # Instagram admite como máximo 2.200 caracteres en la descripción.
 # Mantener atribuciones completas y concisas; nunca recortar licencias a ciegas.
 def clean_credit(v,limit=100):
  value=re.sub(r'\s+',' ',re.sub(r'<[^>]*>',' ',str(v or ''))).strip()
  return value if len(value)<=limit else value[:limit].rsplit(' ',1)[0].rstrip(' ,;')
 credits=ep.get('image_credits',[])
 # El vídeo usa como máximo tres fotos; se listan TODAS las utilizadas.
 credit_lines=[]
 for ix,c in enumerate(credits,1):
  title=clean_credit(c.get('title') or 'Imagen',65)
  author=clean_credit(c.get('creator') or 'Autor no identificado',60)
  lic=clean_credit(c.get('license') or 'Licencia no identificada',36)
  source=str(c.get('source') or '').strip()
  if not source.startswith('https://'):
   raise RuntimeError('Falta URL verificable de atribución de imagen; no se publica')
  credit_lines.append(f'{ix}. {title} — {author} — {lic} — {source}')
 credit_text=('\n\nImágenes / créditos:\n'+'\n'.join(credit_lines)) if credit_lines else ''
 hashtags='\n\n#GroundshakeCL #Sismos #Ciencia #Terremotos'
 base=f'{ep["title"]}\n\n{ep["lesson"]}\n\nFuente científica: {ep["source"]}'
 caption=base+credit_text+hashtags
 if len(caption)>2200:
  # Simplificar identificadores descriptivos, sin suprimir autor, licencia o enlaces.
  credit_lines=[]
  for ix,c in enumerate(credits,1):
   author=clean_credit(c.get('creator') or 'Autor no identificado',35)
   lic=clean_credit(c.get('license') or 'Licencia no identificada',24)
   source=str(c.get('source') or '').strip()
   credit_lines.append(f'{ix}. {author} — {lic} — {source}')
  credit_text=('\n\nCréditos:\n'+'\n'.join(credit_lines)) if credit_lines else ''
  caption=base+credit_text+hashtags
 if len(caption)>2200:
  raise RuntimeError('Atribuciones completas superan 2200 caracteres; reducir número de fotos o usar imágenes de dominio público verificadas')
 print(f'DESCRIPCIÓN Instagram: {len(caption)}/2200 caracteres; fotos acreditadas: {len(credits)}',flush=True)
 save('enviando')
 try:pid=ig_publish(user,token,url,caption)
 except BaseException:save('incierto');raise
 items[ep['episode_id']]['reel_id']=pid
 save('publicado')
 # Recibo verificable: se emite solo después de la respuesta media_publish de Meta.
 receipt_path=Path(out_dir or movie.parent)/'publicacion_confirmada.json'
 confirmation={'account':ACCOUNT,'episode_id':ep['episode_id'],'reel_id':str(pid),
               'reel_published':True,'story_requested':bool(story),'story_published':False,
               'story_id':None,'confirmed_at':utcnow()}
 write_json_atomic(receipt_path,confirmation)
 print('REEL PUBLICADO EN META:',pid,flush=True)
 if story:
  save('enviando','story')
  try:sid=ig_publish(user,token,url,'',media_type='STORIES')
  except BaseException:save('incierto','story');raise
  items[ep['episode_id']]['story_id']=sid
  save('publicado','story')
  confirmation['story_id']=str(sid)
  confirmation['story_published']=True
  confirmation['confirmed_at']=utcnow()
  write_json_atomic(receipt_path,confirmation)
  print('STORY PUBLICADA EN META:',sid,flush=True)
 # Registrar IDs completos a remoto.
 receipt=gh.write(STATE_PATH,json.dumps(state,ensure_ascii=False,indent=2).encode(),sha)
 print('PUBLICADO Instagram:',pid,flush=True)


def metrics(csv_path):
 import csv
 with open(csv_path,encoding='utf-8-sig',newline='') as fh:
  for row in csv.DictReader(fh):
   plays=int(row['plays']);views=int(row['views_7s'])
   if plays<0 or views<0 or views>plays:raise ValueError('Métricas inconsistentes')
   rate=views/plays if plays else 0
   print(f"{row['episode_id']}: R7={rate:.1%} | objetivo=70% | {'ALCANZADO' if rate>=TARGET_R7 else 'NO ALCANZADO'}")


def main():
 ap=argparse.ArgumentParser(description='GROUNDSHAKE CL @groundshake_cl: edición sismos, fotos reales, escenas limpias')
 ap.add_argument('--out',default='output_sismos');ap.add_argument('--topic',default='',help='ID de tema o vacío para elegir')
 ap.add_argument('--photo-dir',default='',help='Carpeta con fotografías propias/autorizadas, sin descarga')
 ap.add_argument('--no-ai',action='store_true');ap.add_argument('--no-voice',action='store_true')
 ap.add_argument('--publish',action='store_true');ap.add_argument('--auto-publish',action='store_true',help='Publicar automáticamente solo temas documentados de la base editorial');ap.add_argument('--approved-id',default='');ap.add_argument('--story',action='store_true')
 ap.add_argument('--metrics-csv',default='');ap.add_argument('--list',action='store_true')
 ap.add_argument('--episode-json',default='',help='Reutilizar exactamente un guion previamente revisado')
 args=ap.parse_args()
 if args.list:
  for t in TOPICS:print(t['id']+'  '+t['title'])
  return
 if args.metrics_csv:metrics(args.metrics_csv);return
 out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
 (out/'publicacion_confirmada.json').unlink(missing_ok=True)
 history_path=out/'historial_sismos.json';history=read_json(history_path,{'items':[]})
 # En GitHub Actions, consultar historial remoto también para no repetir enfoques.
 if os.getenv('GH_TOKEN') and os.getenv('GITHUB_REPOSITORY'):
  try:
   gh=Github();raw,_=gh.read('dataviral/sismos_editorial.json')
   if raw:history=json.loads(raw)
  except Exception as exc:print('Historial remoto no disponible:',str(exc)[:140],flush=True)
 if args.auto_publish and not args.publish:
  raise RuntimeError('--auto-publish requiere --publish')
 if args.publish:
  verify_instagram_account()
 if args.auto_publish and args.episode_json:
  raise RuntimeError('No usar --episode-json con --auto-publish: solo se admiten temas base')
 if args.episode_json:
  ep=read_json(args.episode_json,None)
  if not isinstance(ep,dict) or not all(ep.get(x) for x in ('episode_id','title','script','visuals','source','queries')):
   raise RuntimeError('El JSON del episodio no es válido o está incompleto')
  if not str(ep['episode_id']).startswith('sismos-'):
   raise RuntimeError('Este archivo solo admite episodios sísmicos de GROUNDSHAKE CL')
  t={'id':ep.get('id','revisado')}
 else:
  t=choose_topic(history,args.topic);ep=generate_angle(t,history,use_ai=(not args.no_ai and not args.auto_publish))
  ep['base_verified']=bool(args.auto_publish and t.get('source') and t.get('fact') and t.get('lesson'))
  ep['editorial_check']='Afirmaciones restringidas a la base editorial; no es verificación automática de la página de origen' if args.auto_publish else 'Requiere revisión de datos'
 episode=ep['episode_id'];write_json_atomic(out/(episode+'_guion.json'),ep)
 print('EPISODIO:',episode,'TEMA:',ep['title'],'FUENTE:',ep['source'],flush=True)
 if args.publish and not args.auto_publish and args.approved_id!=episode:
  raise RuntimeError('No se publica sin --approved-id '+episode+'; revise primero guion, fuente e imágenes')
 if args.auto_publish and (not ep.get('base_verified') or not str(ep.get('source','')).startswith('https://')):
  raise RuntimeError('No se puede autopublicar: falta fuente o afirmación editorial validada')
 movie,cover,credits=render(ep,out,args.photo_dir or None,voice=not args.no_voice)
 ep['image_credits']=credits
 write_json_atomic(out/(episode+'_guion.json'),ep)  # incluye storyboard realmente narrado
 if not any(x.get('episode_id')==episode for x in history['items']):
  history['items'].append({'episode_id':episode,'topic':t['id'],'style':ep['style'],'angle':ep['angle'],
                           'time':utcnow(),'status':'preparado' if args.publish else 'vista_previa'})
 write_json_atomic(history_path,history)
 if args.publish:
  publish(ep,movie,args.approved_id,story=args.story,auto=args.auto_publish,out_dir=out)
  for entry in history['items']:
   if entry.get('episode_id')==episode:entry['status']='publicado'
  write_json_atomic(history_path,history)
  # Solo persistir el historial remoto tras publicación confirmada.
  gh=Github();raw,sha=gh.read('dataviral/sismos_editorial.json')
  gh.write('dataviral/sismos_editorial.json',json.dumps(history,ensure_ascii=False,indent=2).encode(),sha)
 else:print('VISTA PREVIA: no se publicó nada.',flush=True)
 print('OBJETIVO R7:',TARGET_R7,'(experimental; nunca garantizado)',flush=True)

if __name__=='__main__':
 try: main()
 except Exception as exc:
  print('ERROR:',str(exc),file=sys.stderr);sys.exit(1)
