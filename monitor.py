#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA (@sismonauta) — edición SISMOS. Un único archivo Python 3.11+.

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
Variables: INSTAGRAM_TOKEN, INSTAGRAM_USER_ID, IG_EXPECTED_USERNAME=sismonauta,
           FACEBOOK_PAGE_ID, FACEBOOK_PAGE_TOKEN, FACEBOOK_EXPECTED_USERNAME=sismonauta,
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

# Visualización opcional en Colab/Jupyter. Nunca es necesaria en GitHub Actions.
try:
    from IPython.display import Video, display
except ImportError:
    Video = None
    def display(*args, **kwargs):
        return None

BRAND = 'SISMONAUTA'
ACCOUNT = 'sismonauta'
STATE_PATH = 'groundshake/state.json'  # Ruta histórica conservada para impedir duplicados tras renombrar la misma cuenta
GRAPH_VERSION = os.getenv('IG_GRAPH_VERSION','v25.0')
GRAPH_URL = os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
W,H,FPS = 720,1280,30
TARGET_R7 = 0.70  # OBJETIVO EXPERIMENTAL: no se garantiza
VOICE = os.getenv('DV_VOICE','es-CL-CatalinaNeural')
USER_AGENT = 'SismonautaSismos/2.1 (educational research; https://commons.wikimedia.org)'
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
  prompt=(f'Eres guionista científico de SISMONAUTA, cuenta @sismonauta. Tema: {topic["title"]}. '
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

# Remates editoriales: frases originales, breves, sin hechos añadidos.
# Voz y pantalla presentan el mismo remate; no se usan frases genéricas.
MEMORABLE_CLOSINGS = {
 'ondas': 'LAS ONDAS CUENTAN LO QUE NO VEMOS.',
 'magnitud': 'UN SOLO SISMO. MUCHAS FORMAS DE SENTIRLO.',
 'replicas': 'QUE PARE EL TEMBLOR NO SIGNIFICA QUE TERMINEN LAS RÉPLICAS.',
 'valdivia': 'VALDIVIA 1960: UNA FECHA QUE LA TIERRA NO BORRA.',
 'profundidad': 'LA PROFUNDIDAD TAMBIÉN CUENTA LA HISTORIA.',
 'placas': 'BAJO NUESTROS PIES, LA TIERRA SIGUE MOVIÉNDOSE.',
 'tsunamis': 'BAJO EL MAR, NO TODO TEMBLOR LEVANTA UNA OLA.',
 'alerta': 'LA ALERTA NO ADIVINA: DETECTA LO QUE YA COMENZÓ.',
 'falla': 'UNA FALLA NO ES SOLO UNA GRIETA: ES MOVIMIENTO.',
 'prediccion': 'PROBABILIDAD NO SIGNIFICA SABER CUÁNDO TEMBLARÁ.',
 'sismografo': 'UN SISMÓMETRO HACE VISIBLE LO INVISIBLE.',
 'terremoto': 'LAS ONDAS VIAJAN MÁS ALLÁ DE LO QUE VEMOS.',
}

# Etiquetas por fenómeno, sin inventar lugares o eventos ajenos al episodio.
TOPIC_HASHTAGS = {
 'ondas': ['OndasSismicas','OndasP','OndasS','PropagacionDeOndas','InteriorTerrestre','Sismologia','Geofisica','CienciaDeLaTierra'],
 'magnitud': ['MagnitudSismica','IntensidadSismica','EscalaDeMagnitud','MovimientoDelSuelo','Sismologia','RiesgoSismico','CienciaDeLaTierra','Geofisica'],
 'replicas': ['ReplicasSismicas','ActividadSismica','Terremoto','FallasGeologicas','CortezaTerrestre','Sismologia','Geologia','CienciaDeLaTierra'],
 'valdivia': ['Valdivia1960','TerremotoDeValdivia','ChileSismico','HistoriaSismica','Magnitud95','Sismologia','TerremotosHistoricos','GeologiaDeChile'],
 'profundidad': ['ProfundidadSismica','Hipocentro','Epicentro','Sismologia','TerremotosProfundos','TerremotosSuperficiales','Geofisica','CienciaDeLaTierra'],
 'placas': ['PlacasTectonicas','TectonicaDePlacas','Litosfera','Geologia','FallasGeologicas','CienciaDeLaTierra','Sismologia','MovimientoDePlacas'],
 'tsunamis': ['Tsunami','TerremotoSubmarino','FondoMarino','RiesgoDeTsunami','Sismologia','Oceano','CienciaDeLaTierra','SeguridadCostera'],
 'alerta': ['AlertaSismica','AlertaTemprana','DeteccionSismica','Sismometros','OndasSismicas','PrevencionSismica','Sismologia','TecnologiaSismica'],
 'falla': ['FallaGeologica','FallasTectonicas','Geologia','FracturaDeRocas','TectonicaDePlacas','Sismologia','CienciaDeLaTierra','MovimientoTectonico'],
 'prediccion': ['PrediccionSismica','ProbabilidadSismica','MitosSismicos','CienciaSismica','Sismologia','RiesgoSismico','Terremotos','Geofisica'],
 'sismografo': ['Sismometro','Sismografo','RegistroSismico','InstrumentosSismicos','Sismologia','Geofisica','OndasSismicas','TecnologiaSismica'],
 'terremoto': ['OndasSismicas','DistanciaEpicentral','PropagacionSismica','MovimientoDelSuelo','Sismologia','Geofisica','Terremotos','CienciaDeLaTierra'],
}

def episode_hashtags(ep):
 key=norm(ep.get('id',''))
 tags=['Sismonauta','Sismos','EducacionSismica','Terremotos']+TOPIC_HASHTAGS.get(key,['Sismologia','Geofisica','CienciaDeLaTierra','Geologia','PrevencionSismica','CulturaSismica','OndasSismicas','RiesgoSismico'])
 unique=[];seen=set()
 for tag in tags:
  if tag.lower() not in seen:
   unique.append('#'+tag);seen.add(tag.lower())
 assert len(unique)>=10
 return ' '.join(unique[:14])

def episode_caption_base(ep):
 key=norm(ep.get('id',''))
 title=str(ep.get('title','')).strip()
 fact=str(ep.get('fact','')).strip()
 lesson=str(ep.get('lesson','')).strip()
 close=MEMORABLE_CLOSINGS.get(key, str(ep.get('storyboard',[{}])[-1].get('overlay','')) if ep.get('storyboard') else lesson)
 if not all((title,fact,lesson,close)):
  raise RuntimeError('Falta información temática para la descripción de Instagram')
 # El pie usa exactamente el hecho, enseñanza y conclusión del episodio.
 return (f'{title}\n\n{fact}\n\n{lesson}\n\n'
         f'{close}\n\n¿Qué opinas? Te leemos en los comentarios.')

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

# Guiones por fenómeno: cada escena entrega información distinta y verificable.
# Cada par contiene (locución, rótulo de máximo 12 palabras).
RETENTION_STORIES = {
 'alerta': [
  ('¿Cómo puede una alerta avisarte antes de que sientas el terremoto?', '¿TE PUEDE AVISAR ANTES?'),
  ('No es que conozca el futuro. La explicación ocurre bajo nuestros pies.', 'NO ES ADIVINACIÓN'),
  ('El terremoto ya comenzó cuando los sensores detectan sus primeras ondas.', 'EL SISMO YA COMENZÓ'),
  ('La alerta puede llegar antes que las sacudidas fuertes, pero no siempre hay tiempo suficiente.', 'SEGUNDOS QUE PUEDEN IMPORTAR'),
  ('La alerta no predice el sismo. Detecta lo que ya empezó.', 'DETECTAR NO ES PREDECIR')],
 'ondas': [
  ('¿Cómo sabemos qué ocurre dentro de la Tierra sin poder mirar allí?', '¿CÓMO MIRAMOS BAJO TIERRA?'),
  ('La pista viene de algo que viaja después de un terremoto.', 'LAS ONDAS GUARDAN PISTAS'),
  ('Las ondas P y S se propagan de forma distinta.', 'NO TODAS VIAJAN IGUAL'),
  ('Las ondas S no atraviesan materiales líquidos. Esa diferencia revela propiedades del interior terrestre.', 'LAS ONDAS S NO CRUZAN LÍQUIDOS'),
  ('Lo que una onda no puede atravesar también nos enseña algo.', 'EL SILENCIO TAMBIÉN INFORMA')],
 'magnitud': [
  ('¿Por qué el mismo terremoto puede sentirse muy distinto en dos ciudades?', '¿UN SISMO, DOS EXPERIENCIAS?'),
  ('El número que escuchamos en las noticias no explica todo.', 'EL NÚMERO NO BASTA'),
  ('La magnitud describe el tamaño del terremoto.', 'LA MAGNITUD MIDE EL TAMAÑO'),
  ('La intensidad describe las sacudidas y los efectos en cada lugar.', 'LA INTENSIDAD CAMBIA SEGÚN EL LUGAR'),
  ('El terremoto es uno. Sus efectos no tienen por qué ser iguales.', 'UN SISMO. MUCHAS EXPERIENCIAS')],
 'replicas': [
  ('¿El peligro termina exactamente cuando deja de temblar?', '¿YA TERMINÓ TODO?'),
  ('Después de un terremoto, la región puede seguir reajustándose.', 'LA TIERRA SIGUE REAJUSTÁNDOSE'),
  ('Esos movimientos posteriores se conocen como réplicas.', 'PUEDEN LLEGAR RÉPLICAS'),
  ('Pueden ocurrir después del evento principal en la misma región.', 'EL MOVIMIENTO PUEDE CONTINUAR'),
  ('Que el suelo esté quieto ahora no significa que no haya más réplicas.', 'EL SILENCIO NO SIEMPRE ES EL FINAL')],
 'valdivia': [
  ('¿Sabías que el mayor terremoto registrado ocurrió en Chile?', '¿EL MAYOR TERREMOTO REGISTRADO?'),
  ('Sucedió en un lugar que quedó grabado en la historia sísmica.', 'CHILE QUEDÓ EN LA HISTORIA'),
  ('El 22 de mayo de 1960, Valdivia vivió un terremoto de magnitud nueve coma cinco.', 'VALDIVIA 1960: MAGNITUD 9,5'),
  ('Es el mayor registrado instrumentalmente. Sus datos siguen siendo relevantes para la ciencia.', 'UN REGISTRO EXTRAORDINARIO'),
  ('Valdivia nos recuerda cuánta energía puede liberar la Tierra.', 'VALDIVIA: UNA FECHA IMPOSIBLE DE OLVIDAR')],
 'profundidad': [
  ('¿Por qué dos terremotos de magnitud parecida pueden sentirse diferentes?', '¿MISMA MAGNITUD, DISTINTA SACUDIDA?'),
  ('Hay un dato que suele quedar fuera del titular.', 'MIRA BAJO LA SUPERFICIE'),
  ('No todos los sismos comienzan a la misma profundidad.', 'LA PROFUNDIDAD CAMBIA'),
  ('La profundidad del origen influye en cómo se percibe el movimiento del suelo.', 'NO TODO ES LA MAGNITUD'),
  ('Para entender un sismo, también hay que mirar dónde comienza.', 'IMPORTA LO QUE NO VEMOS')],
 'placas': [
  ('¿Cómo puede moverse el suelo que parece tan firme bajo tus pies?', '¿LA TIERRA ESTÁ QUIETA?'),
  ('El movimiento es lento. Pero sus efectos pueden ser enormes.', 'UN MOVIMIENTO CASI INVISIBLE'),
  ('La parte externa rígida de la Tierra está dividida en placas.', 'VIVIMOS SOBRE PLACAS'),
  ('Esas placas se desplazan lentamente y pueden acumular esfuerzos en sus bordes.', 'LOS ESFUERZOS SE ACUMULAN'),
  ('El suelo parece inmóvil. La Tierra sigue en movimiento.', 'LA QUIETUD ES SOLO APARIENCIA')],
 'tsunamis': [
  ('¿Cada terremoto bajo el mar termina creando un tsunami?', '¿SISMO SUBMARINO = TSUNAMI?'),
  ('La respuesta depende de más que la ubicación del epicentro.', 'NO BASTA CON ESTAR BAJO EL MAR'),
  ('No todos los terremotos submarinos producen tsunamis.', 'NO TODOS GENERAN TSUNAMIS'),
  ('El desplazamiento del fondo marino y otros factores influyen en su generación.', 'IMPORTA CÓMO SE MUEVE EL FONDO'),
  ('Bajo el océano, no todo temblor levanta una gran ola.', 'UN SISMO NO SIEMPRE ES UN TSUNAMI')],
 'falla': [
  ('¿Una falla geológica es simplemente una grieta en el terreno?', '¿SOLO UNA GRIETA?'),
  ('Hay una diferencia que cambia su significado.', 'LA CLAVE ES EL MOVIMIENTO'),
  ('Una falla es una fractura donde las rocas se han desplazado.', 'LAS ROCAS SE DESPLAZARON'),
  ('Estudiar esas fracturas ayuda a comprender la deformación de la corteza.', 'LA ROCA CONSERVA EVIDENCIAS'),
  ('Una falla no solo se ve. También cuenta cómo se movió la Tierra.', 'LA ROCA CUENTA SU HISTORIA')],
 'prediccion': [
  ('¿Alguien puede decirte el día exacto del próximo gran terremoto?', '¿SE PUEDE SABER EL DÍA EXACTO?'),
  ('Hay mucha diferencia entre una estimación y una predicción.', 'NO CONFUNDAS PROBABILIDAD Y CERTEZA'),
  ('Hoy no existe un método validado que anticipe con certeza fecha, lugar y magnitud.', 'NO EXISTE PREDICCIÓN EXACTA VALIDADA'),
  ('La ciencia puede estudiar probabilidades, pero eso no equivale a conocer el futuro.', 'RIESGO NO ES UNA FECHA'),
  ('Quien promete una fecha exacta necesita pruebas que la ciencia aún no tiene.', 'DESCONFÍA DE LA CERTEZA SIN PRUEBAS')],
 'sismografo': [
  ('¿Cómo puede una máquina registrar un movimiento que casi no vemos?', '¿CÓMO SE VE UN MOVIMIENTO INVISIBLE?'),
  ('La respuesta comienza con un instrumento sensible al suelo.', 'UN SENSOR ESCUCHA AL SUELO'),
  ('Los sismómetros detectan y registran sus movimientos.', 'EL SISMÓMETRO REGISTRA MOVIMIENTO'),
  ('Esos registros ayudan a estudiar terremotos y el paso de ondas sísmicas.', 'EL MOVIMIENTO SE CONVIERTE EN DATOS'),
  ('Lo que no podemos ver a simple vista puede dejar un registro.', 'LO INVISIBLE DEJA HUELLAS')],
 'terremoto': [
  ('¿Cómo puede sentirse un terremoto que ocurrió tan lejos?', '¿POR QUÉ SE SIENTE TAN LEJOS?'),
  ('La energía liberada no se queda junto al epicentro.', 'LA ENERGÍA VIAJA'),
  ('Las ondas sísmicas se propagan a través de la Tierra.', 'LAS ONDAS CRUZAN DISTANCIAS'),
  ('Algunas pueden recorrer grandes distancias y producir movimientos perceptibles.', 'EL MOVIMIENTO PUEDE LLEGAR LEJOS'),
  ('Un terremoto comienza en un lugar. Sus ondas pueden llegar mucho más allá.', 'EL EPICENTRO NO ES EL LÍMITE')],
}

def build_storyboard(ep):
 """Cinco beats documentados y CTA: texto y narración corresponden por escena."""
 key=str(ep.get('id',''))
 beats=RETENTION_STORIES.get(key)
 if not beats:
  raise RuntimeError('No existe un guion narrativo verificado para el tema '+key)
 storyboard=[]
 stages=('pregunta','intriga','revelacion','explicacion','cierre')
 queries=list(ep.get('queries') or ['earthquake seismometer'])
 for i,((narration,overlay),stage) in enumerate(zip(beats,stages)):
  overlay=overlay.strip()
  if not narration.strip() or not overlay or len(overlay.split())>12 or '...' in overlay or '…' in overlay:
   raise ValueError('Beat editorial incompleto o demasiado largo: '+key)
  storyboard.append({'narration':narration,'overlay':overlay,'query':queries[min(i,len(queries)-1)],'stage':stage})
 storyboard.append({'narration':'Dale me gusta y comparte, para seguir recibiendo más información sobre este fascinante tema. Muchas gracias!',
                    'overlay':'DALE LIKE','query':queries[-1],'stage':'cta'})
 return storyboard


# Títulos o palabras clave compatibles con fotografías del fenómeno;
# se rechazan paisajes genéricos aunque la búsqueda los haya devuelto.
# Temática visual estricta: señales concretas, no menciones genéricas a "seismic".
# El selector prioriza el título del archivo: una palabra suelta en etiquetas
# nunca basta para aceptar una imagen de un tema distinto.
# Paisajes naturales exclusivamente: fallas, deformación del terreno, costas y montañas.
# No usar instrumentos, edificios, rescates ni retratos como imágenes de apoyo.
PHOTO_TERMS={
 'ondas':('san andreas fault','geological fault scarp','earthquake surface rupture','tectonic fault landscape','rift valley'),
 'magnitud':('earthquake surface rupture','earthquake ground fissure','earthquake landslide','earthquake fault scarp'),
 'replicas':('earthquake surface rupture','earthquake ground fissure','earthquake landslide','fault scarp','landslide'),
 'valdivia':('1960 chile earthquake coast','1960 valdivia coast','1960 chile tsunami coast','1960 valdivia tsunami','valdivia 1960 shoreline'),
 'profundidad':('subduction trench','san andreas fault','geological fault scarp','earthquake surface rupture'),
 'placas':('san andreas fault','tectonic fault landscape','rift valley','geological fault scarp','plate boundary landscape','fault scarp'),
 'tsunamis':('tsunami coastline','tsunami inundation coast','tsunami coastal erosion','tsunami wave coast','tsunami coast','tsunami shoreline'),
 'alerta':('san andreas fault','earthquake surface rupture','tectonic fault landscape','earthquake ground fissure'),
 'falla':('san andreas fault','geological fault scarp','fault line landscape','fault rupture landscape','surface rupture'),
 'prediccion':('san andreas fault','earthquake fault scarp','earthquake ground fissure','tectonic fault landscape'),
 'sismografo':('san andreas fault','earthquake surface rupture','earthquake ground fissure','geological fault scarp'),
 'terremoto':('earthquake surface rupture','earthquake landslide','earthquake ground fissure','earthquake fault scarp','landslide'),
}

PHOTO_SEARCH_QUERIES={
 'ondas':['San Andreas Fault','fault scarp'],
 'magnitud':['earthquake landslide','earthquake surface rupture'],
 'replicas':['earthquake landslide','fault scarp'],
 'valdivia':['1960 Valdivia earthquake coast','1960 Chile tsunami'],
 'profundidad':['subduction coast','tectonic fault landscape'],
 'placas':['San Andreas Fault','rift valley'],
 'tsunamis':['tsunami coast','tsunami shoreline','tsunami coastal erosion','tsunami inundation','tsunami coastal landscape','tsunami wave shoreline','tsunami damage beach'],
 'alerta':['earthquake surface rupture','San Andreas Fault'],
 'falla':['San Andreas Fault','fault scarp','surface rupture'],
 'prediccion':['San Andreas Fault','fault scarp'],
 'sismografo':['San Andreas Fault','surface rupture'],
 'terremoto':['earthquake landslide','earthquake surface rupture'],
}

# Con el requisito editorial "solo paisajes", algunos temas instrumentales o
# abstractos no tienen una ilustración directa honesta. No seleccionarlos al azar.
LANDSCAPE_AUTO_TOPICS=frozenset(('falla','placas','tsunamis','terremoto','replicas'))


# Rechazo explícito: personas, escenas urbanas y aparatos. Se revisa el título,
# la descripción y las etiquetas; no se admiten fotos dudosas como relleno.
FORBIDDEN_VISUAL_TERMS=(
 'person','people','portrait','selfie','man ','woman ','child','children',
 'crowd','tourist','hiker','hiking','visitor','rescue','rescuer','firefighter',
 'soldier','police','protester','human','family','group photo','group of',
 'persona','personas','gente','retrato','turista','excursionista','rescate',
 'building','buildings','house','houses','city','cities','urban','street',
 'road','bridge','car ','vehicle','train','construction','infrastructure',
 'edificio','edificios','casa ','ciudad','calle','puente','vehiculo',
 'seismometer','seismograph','sensor','instrument','equipment','station',
 'sismometro','sismografo','instrumento','laboratory','laboratorio',
 'museum','exhibit','memorial','monument','sign','warning','poster'
)
LANDSCAPE_EVIDENCE=(
 'fault','rupture','fissure','rift','scarp','landslide','slip','subsidence',
 'coast','coastal','shoreline','shore','cliff','terrain','landscape',
 'valley','mountain','hillside','rock','outcrop','geological','tectonic',
 'tsunami','erosion','trench','falla','fractura','deslizamiento','costa',
 'acantilado','terreno','paisaje','cordillera','montana','ladera','roca'
)

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
 """Filtro conservador de metadatos: tema sísmico + paisaje sin personas.

 No prueba con certeza el contenido visual: si hay dudas se rechaza.
 """
 title=norm(re.sub(r'^file:', '',str(item.get('title','') or '').strip(),flags=re.I))
 desc=norm(str(item.get('description','') or ''))
 tags=norm(str(item.get('tags','') or ''))
 if not title:return False
 if re.search(r'\.(?:pdf|djvu|tiff?|svg|gif)(?:$|[?# ])',title):return False
 combined=' '+re.sub(r'[_-]+',' ',title+' '+desc+' '+tags)+' '
 if any(re.search(r"(?<![a-z])"+re.escape(term.strip())+r"(?![a-z])",title) for term in NON_PHOTO_TERMS):return False
 if any(term in title for term in FORBIDDEN_VISUAL_TERMS):return False
 # Evitar imágenes humanas/urbanas también cuando la descripción lo indique.
 if any(re.search(r'(?<![a-z])'+re.escape(term.strip())+r'(?![a-z])',desc) for term in ('people','person','portrait','crowd','tourist','rescue workers','buildings','houses','city street','evacuees','victims')):return False
 terms=tuple(norm(x) for x in PHOTO_TERMS.get(ep.get('id'),()))
 if not terms:return False
 # Aceptar solo títulos descriptivos del fenómeno, no etiquetas añadidas.
 if ep.get('id')=='tsunamis':
  # Los títulos de Commons suelen ser el nombre del evento, p. ej. «2011 tsunami».
  # Aceptar evidencia costera en título O descripción, nunca costa genérica
  # sin vínculo explícito al tsunami. Mantener el rechazo de material urbano.
  coastal=('coast','coastal','shore','shoreline','beach','sea','ocean','bay',
           'inundation','flood','erosion','wave','cliff','landscape','waterfront',
           'costa','playa','oleaje','inundacion','acantilado')
  event=('tsunami','maremoto','inundation by tsunami','tsunami inundation')
  if not any(x in title for x in event):return False
  if not any(x in (title+' '+desc) for x in coastal):return False
 else:
  if not any(term in title for term in terms):return False
  if not any(term in title for term in LANDSCAPE_EVIDENCE):return False
 # Para un hecho histórico, no inventar autenticidad con un paisaje moderno.
 if ep.get('id')=='valdivia' and not ('1960' in title and any(x in title for x in ('chile','valdivia'))):return False
 return True


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
   pixels=list(sample.resize((160,160)).get_flattened_data() if hasattr(Image.Image,'get_flattened_data') else sample.resize((160,160)).getdata())
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
 """Solo CC0 y dominio público verificable: pie sin créditos de imagen.

 CC BY y CC BY-SA requieren atribuciones; no se aceptan para esta
 modalidad sin referencias visibles en el texto de Instagram.
 """
 code=norm(str(code)).strip().lower().replace('_','-')
 compact=re.sub(r'[^a-z0-9]+','',code)
 return compact.startswith(('cc0','publicdomain','publicdomainmark','pdm','pdold','pdus','pdself'))


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


# Politica antirrepeticion: un tema cientifico solo puede publicarse una vez.
# Si una publicacion anterior quedo parcialmente completada, se reanuda el mismo
# episode_id en lugar de generar otro video con distintos estilos.
def legacy_topic_for_episode(episode_id):
 if not str(episode_id).startswith('sismos-'):
  return None
 angles=['¿Sabías esto?','La explicación científica','Lo que muestran los datos',
         'La diferencia importante','La pregunta que importa','Un detalle sorprendente',
         'Lo que no debemos confundir','Lo esencial en segundos']
 for topic in TOPICS:
  for style in STYLES:
   for angle in angles:
    old_id='sismos-'+hashlib.sha1((topic['id']+style+angle).encode()).hexdigest()[:12]
    if old_id==episode_id:return topic['id']
 return None

def remote_published_topics(state,history):
 used=set()
 published_keys=('reel','story','facebook_reel','facebook_story','facebook_feed')
 for eid,row in (state.get('episodes') or {}).items():
  if not isinstance(row,dict):continue
  if not any(row.get(k) in ('publicado','enviando','incierto') for k in published_keys):continue
  topic=row.get('topic') or legacy_topic_for_episode(eid)
  if not topic:
   topic=next((h.get('topic') for h in history.get('items',[]) if h.get('episode_id')==eid),None)
  if topic:used.add(topic)
  else:
   # Un episodio historico no identificable impide garantizar que no se repita.
   raise RuntimeError(f'Historial de publicación sin tema identificable: {eid}. Revisar antes de generar videos nuevos.')
 for entry in history.get('items',[]):
  if entry.get('status')=='publicado' and entry.get('topic'):used.add(entry['topic'])
 return used

def choose_topic(history, requested='', excluded_topics=None):
 excluded_topics=set(excluded_topics or ())
 if requested:
  if requested in excluded_topics:raise RuntimeError('Tema ya publicado en SISMONAUTA: '+requested+'. Se evita publicar una nueva variante del mismo video.')
  matches=[x for x in TOPICS if x['id']==requested]
  if not matches: raise ValueError('Tema desconocido. Disponibles: '+', '.join(x['id'] for x in TOPICS))
  return matches[0]
 candidates=[x for x in TOPICS if x['id'] in LANDSCAPE_AUTO_TOPICS and x['id'] not in excluded_topics]
 if not candidates:raise RuntimeError('Todos los temas de paisajes sísmicos disponibles ya fueron publicados. Se detiene para evitar videos repetidos; es necesario incorporar nuevos temas científicos verificados.')
 counts={x['id']:0 for x in candidates}
 for item in history.get('items',[]):
  if item.get('topic') in counts: counts[item['topic']]+=1
 minimum=min(counts.values())
 return random.SystemRandom().choice([x for x in candidates if counts[x['id']]==minimum])


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
   params={'q':query,'license':'cc0,pdm','category':'photograph','page_size':20},timeout=25)
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
 """Fotografías reales del fenómeno, sin búsquedas sísmicas genéricas.

 Se rechazan automáticamente metadatos ajenos y documentos/diagramas.
 Las escenas usan solo este conjunto seleccionado; no fotografías de relleno.
 """
 found=[];credits=[];seen=set();counts={'candidates':0,'metadata_rejected':0,'download_failed':0,'visual_rejected':0}
 if local_dir:
  for f in sorted(Path(local_dir).iterdir()):
   if f.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
    try:
     with Image.open(f) as im:
      if im.width>=500 and im.height>=350 and visual_photo_audit(f)[0]:
       found.append(f);credits.append({'source':str(f),'license':'Verificar autorización de uso'})
    except Exception:pass
  if not found:raise RuntimeError('No se encontraron fotos válidas en --photo-dir')
  return found,credits
 queries=PHOTO_SEARCH_QUERIES.get(ep.get('id'))
 if not queries:raise RuntimeError('Tema sin estrategia visual específica; no se usarán fotos genéricas')
 print('BUSQUEDA VISUAL TEMATICA:',ep['id'],'|',queries,flush=True)
 rejected_examples=[]
 for q in queries:
  for provider in (commons,openverse):
   try:results=list(provider(q))
   except Exception as exc:
    print(f'Fotos {provider.__name__} / {q}: {str(exc)[:110]}',flush=True);continue
   for item in results:
    if item['url'] in seen:continue
    seen.add(item['url']);counts['candidates']+=1
    if not photo_relevant(item,ep):
     counts['metadata_rejected']+=1
     if len(rejected_examples)<5:rejected_examples.append(str(item.get('title',''))[:85])
     continue
    path=folder/f'foto_{len(found)+1}.jpg'
    if not photo_download(item,path):counts['download_failed']+=1;continue
    okay,reason=visual_photo_audit(path)
    if not okay:
     print('FOTO DESCARTADA:',reason,'|',str(item.get('title',''))[:95],flush=True)
     path.unlink(missing_ok=True);counts['visual_rejected']+=1;continue
    found.append(path)
    credits.append({k:item.get(k,'') for k in ('source','license','title','creator','provider','description','tags')})
    print('Foto temática:',ep['id'],'|',item['title'][:85],'|',item['license'],flush=True)
    if len(found)>=3:break
   if len(found)>=3:break
  if len(found)>=3:break
 if not found and rejected_examples:print('EJEMPLOS DE TITULOS RECHAZADOS:',rejected_examples,flush=True)
 print('AUDITORIA FOTOGRAFIAS:',counts,'aceptadas=',len(found),flush=True)
 if not found:
  raise RuntimeError('No se encontraron fotografías auténticas del tema específico con licencia CC0/PD. No se publican imágenes de relleno.')
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

def draw_like_icon(draw,cx,cy,scale=1.0,fill=(255,255,255,255)):
 """Ícono tipo reacción «Me gusta» de Facebook: círculo azul y pulgar blanco.

 Dibujo vectorial original mediante Pillow; no necesita archivos externos.
 """
 s=float(scale)
 alpha=fill[3] if len(fill)==4 else 255
 blue=(24,119,242,alpha)
 white=(255,255,255,alpha)
 # Disco azul característico de la reacción Like.
 r=132*s
 draw.ellipse((cx-r,cy-r,cx+r,cy+r),fill=blue)
 # Muñeca y puño blancos, con la silueta lateral del pulgar elevado.
 # Coordenadas relativas para que el conjunto escale sin perder proporciones.
 def pts(items):return [(cx+x*s,cy+y*s) for x,y in items]
 # Puño y antebrazo separados por la línea azul de la muñeca.
 draw.rounded_rectangle((cx-88*s,cy-3*s,cx-54*s,cy+69*s),radius=8*s,fill=white)
 # Palma y nudillos, a la derecha del puño.
 palm=pts([(-46,1),(-24,-14),(13,-14),(26,-23),(47,-20),(58,-11),(58,1),
           (54,13),(52,21),(50,34),(47,42),(45,55),(31,68),(-45,68)])
 draw.polygon(palm,fill=white)
 # Pulgar hacia arriba: perfil reconocible, no cuatro dedos verticales.
 thumb=pts([(-39,4),(-20,-15),(-9,-38),(2,-74),(12,-93),(24,-96),
            (32,-88),(34,-75),(22,-38),(17,-21),(35,-21),(46,-16),
            (47,-7),(40,3)])
 draw.polygon(thumb,fill=white)
 # Separador de muñeca para conservar el estilo del pictograma.
 draw.line(pts([(-52,2),(-52,67)]),fill=blue,width=max(3,int(5*s)))


def cta_scene_video(dest_video,duration):
 """Escena final: fondo negro, ícono Like estilo Facebook parpadeante y texto al pie."""
 tmpdir=Path(tempfile.mkdtemp(prefix='cta_like_'))
 try:
  total_frames=max(int(duration*FPS),1)
  blink_period=max(int(FPS*0.45),6)
  bottom_text='DALE LIKE'
  for n in range(total_frames):
   img=Image.new('RGBA',(W,H),(0,0,0,255))
   draw=ImageDraw.Draw(img)
   visible=((n//blink_period)%2)==0
   pulse=1.0 if visible else 0.88
   alpha=255 if visible else 82
   # Halo leve para que el parpadeo se perciba mejor.
   halo=Image.new('RGBA',(W,H),(0,0,0,0))
   hdraw=ImageDraw.Draw(halo)
   for extra,a in ((54,40),(26,68)):
    hdraw.ellipse((W/2-140-extra,H/2-130-extra,W/2+140+extra,H/2+150+extra),fill=(255,255,255,a if visible else 0))
   img=Image.alpha_composite(img,halo)
   draw=ImageDraw.Draw(img)
   draw_like_icon(draw,W/2,H//2-40,scale=1.20*pulse,fill=(255,255,255,alpha))
   ft=font(54)
   bbox=draw.textbbox((0,0),bottom_text,font=ft,stroke_width=3)
   tw=bbox[2]-bbox[0]
   tx=(W-tw)/2
   ty=H-160
   draw.text((tx,ty),bottom_text,font=ft,fill=(255,255,255,255),stroke_width=3,stroke_fill=(9,19,28,255))
   img.convert('RGB').save(tmpdir/f'frame_{n:04d}.png',quality=95)
  ffmpeg(['-framerate',str(FPS),'-i',tmpdir/'frame_%04d.png','-t',str(duration),'-r',str(FPS),'-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',dest_video])
 finally:
  shutil.rmtree(tmpdir,ignore_errors=True)

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
 if len(storyboard)<4:raise RuntimeError('Historia sin suficientes ideas comprobables')
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
  words=len(beat['overlay'].split())
  stage=beat.get('stage','')
  if stage=='pregunta':
   min_dur=3.6
   read_rate=2.7
  elif stage=='intriga':
   min_dur=3.0
   read_rate=2.6
  elif stage=='cta':
   min_dur=5.4
   read_rate=2.3
  else:
   min_dur=4.7
   read_rate=2.15
  durations.append(round(max(sec+0.55,min_dur,words/read_rate+0.7),2))
 total=round(sum(durations),2)
 print('SINCRONÍA escenas:',durations,'duración total:',total,flush=True)
 print('ESTRUCTURA: PREGUNTA → INTRIGA → REVELACIÓN → CIERRE → CTA',flush=True)
 media_items=[]
 cover=None
 photo_idx=0
 for i,beat in enumerate(storyboard):
  stage=beat.get('stage','')
  if stage=='cta':
   clip_path=folder/f'escena_{i:02d}.mp4'
   cta_scene_video(clip_path,durations[i])
   media_items.append({'kind':'video','path':clip_path,'duration':durations[i]})
  else:
   frame=folder/f'escena_{i:02d}.jpg'
   clean_scene(photos[min(photo_idx,len(photos)-1)],beat['overlay'],frame)
   media_items.append({'kind':'image','path':frame,'duration':durations[i]})
   if cover is None:
    cover=out/(ep['episode_id']+'_portada.jpg')
    shutil.copy2(frame,cover)
   print('ESCENA-FOTO:',i+1,'|',beat['stage'],'|',photos[min(photo_idx,len(photos)-1)].name,flush=True)
   photo_idx+=1
 if cover is None:
  raise RuntimeError('No se pudo generar portada')
 music=folder/'musica.wav';soundtrack(music,secs=total+1)
 inputs=[]
 for item in media_items:
  if item['kind']=='image':
   inputs+=['-loop','1','-framerate',str(FPS),'-t',str(item['duration']),'-i',item['path']]
  else:
   inputs+=['-i',item['path']]
 media_count=len(media_items)
 inputs+=['-i',music]
 voice_start=media_count+1
 for v in voices:inputs+=['-i',v]
 filt=[]
 for i,item in enumerate(media_items):
  filt.append(f'[{i}:v]scale={W}:{H},setsar=1,trim=duration={item["duration"]},setpts=PTS-STARTPTS,format=yuv420p[v{i}]')
 filt.append(''.join(f'[v{i}]' for i in range(len(media_items)))+f'concat=n={len(media_items)}:v=1:a=0[v]')
 if has_voice:
  for i,d in enumerate(durations):
   filt.append(f'[{voice_start+i}:a]aresample=44100,apad,atrim=duration={d},asetpts=PTS-STARTPTS[a{i}]')
  filt.append(''.join(f'[a{i}]' for i in range(len(voices)))+f'concat=n={len(voices)}:v=0:a=1[narr]')
  filt.append(f'[{media_count}:a]volume=0.30[mus];[narr]volume=1.0[nar];[mus][nar]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.94[a]')
 else:
  filt.append(f'[{media_count}:a]volume=0.65,alimiter=limit=0.94[a]')
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
  payload={'message':'SISMONAUTA SISMOS: '+path,'content':base64.b64encode(data).decode(),'branch':self.branch}
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

def fb_api(method, endpoint, token, params=None):
 """Graph API de páginas; nunca imprimir el token."""
 url=f'https://graph.facebook.com/{GRAPH_VERSION}/{endpoint}'
 kw={'params':{'access_token':token,**(params or {})},'timeout':90}
 r=requests.request(method,url,**kw)
 try: data=r.json()
 except ValueError: raise RuntimeError(f'Facebook HTTP {r.status_code}: respuesta no JSON')
 if not r.ok or data.get('error'):
  err=data.get('error',{})
  raise RuntimeError(f'Facebook HTTP {r.status_code}: {err.get("message",str(data)[:180])}; código {err.get("code","?")}')
 return data

def verify_facebook_page():
 """Confirma que el token pertenece a la página declarada antes de generar/publicar."""
 page=os.getenv('FACEBOOK_PAGE_ID','').strip()
 token=os.getenv('FACEBOOK_PAGE_TOKEN','').strip()
 if not page or not token:raise RuntimeError('Faltan FACEBOOK_PAGE_ID y/o FACEBOOK_PAGE_TOKEN')
 if not page.isdigit():raise RuntimeError('FACEBOOK_PAGE_ID debe ser numérico')
 info=fb_api('GET',page,token,{'fields':'id,name,username'})
 if str(info.get('id'))!=page:raise RuntimeError('Token de Facebook no corresponde a FACEBOOK_PAGE_ID')
 expected=os.getenv('FACEBOOK_EXPECTED_USERNAME','sismonauta').strip().lstrip('@').lower()
 username=str(info.get('username') or '').lower()
 # Algunas páginas no exponen username por la API: no confundir con id o nombre.
 if username and expected and username!=expected:
  raise RuntimeError(f'Página Facebook incorrecta: @{username}; se esperaba @{expected}')
 print(f'PÁGINA Facebook validada: {info.get("name","")} (ID {page})',flush=True)
 return page,token

def fb_publish_reel(page,token,movie,caption,episode_id):
 """Publica un Reel nativo de página; nunca lo confunde con una Story."""
 session=fb_api('POST',f'{page}/video_reels',token,{'upload_phase':'start'})
 video_id=str(session.get('video_id') or '')
 upload_url=str(session.get('upload_url') or '')
 if not video_id.isdigit() or not upload_url.startswith('https://rupload.facebook.com/'):
  raise RuntimeError('Facebook: sesión de carga incompleta, no se publica')
 print(f'FACEBOOK CARGA INICIADA: video_id={video_id}',flush=True)
 headers={'Authorization':'OAuth '+token,'offset':'0','file_size':str(movie.stat().st_size),'Content-Type':'application/octet-stream'}
 with movie.open('rb') as fh:
  resp=requests.post(upload_url,headers=headers,data=fh,timeout=300)
 try: uploaded=resp.json()
 except ValueError:raise RuntimeError(f'Facebook: carga respondió HTTP {resp.status_code} sin JSON; estado incierto, no reintentar a ciegas')
 if not resp.ok or uploaded.get('success') is not True:
  raise RuntimeError(f'Facebook: error subiendo video (HTTP {resp.status_code}, sin confirmar publicación)')
 print(f'FACEBOOK VIDEO SUBIDO: {video_id}',flush=True)
 result=fb_api('POST',f'{page}/video_reels',token,{
  'upload_phase':'finish','video_id':video_id,'video_state':'PUBLISHED',
  'description':caption[:5000], 'title':str(episode_id)[:200]})
 if result.get('success') is not True:
  raise RuntimeError(f'Facebook: resultado de finalización no confirmado para {video_id}: {str(result)[:180]}')
 # Meta ha aceptado publicación; procesamiento/disponibilidad puede ser posterior.
 return video_id

def fb_publish_story(page,token,movie):
 """Publica una Story de video nativa y separada del Reel de Facebook."""
 session=fb_api('POST',f'{page}/video_stories',token,{'upload_phase':'start'})
 video_id=str(session.get('video_id') or '')
 upload_url=str(session.get('upload_url') or '')
 if not video_id.isdigit() or not upload_url.startswith('https://rupload.facebook.com/'):
  raise RuntimeError('Facebook Story: sesión de carga incompleta')
 headers={'Authorization':'OAuth '+token,'offset':'0',
          'file_size':str(movie.stat().st_size),'Content-Type':'application/octet-stream'}
 with movie.open('rb') as fh:
  response=requests.post(upload_url,headers=headers,data=fh,timeout=300)
 try: uploaded=response.json()
 except ValueError:raise RuntimeError(f'Facebook Story: respuesta no JSON HTTP {response.status_code}; resultado incierto')
 if not response.ok or uploaded.get('success') is not True:
  raise RuntimeError(f'Facebook Story: carga no confirmada HTTP {response.status_code}')
 done=fb_api('POST',f'{page}/video_stories',token,
             {'upload_phase':'finish','video_id':video_id,'video_state':'PUBLISHED'})
 if done.get('success') is not True:
  raise RuntimeError(f'Facebook Story: finalización no confirmada para {video_id}: {str(done)[:180]}')
 return video_id


def fb_publish_feed_video(page,token,movie,caption,episode_id):
 """Carga el MP4 como vídeo nativo del feed de la fanpage, sin post de enlace."""
 endpoint=f'https://graph.facebook.com/{GRAPH_VERSION}/{page}/videos'
 with movie.open('rb') as fh:
  response=requests.post(endpoint,data={'access_token':token,
                         'description':caption[:5000],
                         'title':str(episode_id)[:200],
                         'published':'true'},
                         files={'source':(movie.name,fh,'video/mp4')},timeout=360)
 try: result=response.json()
 except ValueError:raise RuntimeError(f'Facebook Feed: respuesta no JSON HTTP {response.status_code}; envío incierto')
 if not response.ok or result.get('error'):
  err=result.get('error',{})
  raise RuntimeError(f'Facebook Feed HTTP {response.status_code}: {err.get("message",str(result)[:180])}; código {err.get("code","?")}')
 video_id=str(result.get('id') or '')
 if not video_id.isdigit():
  raise RuntimeError(f'Facebook Feed: Meta no devolvió ID de video: {str(result)[:180]}')
 # El procesamiento de Meta es asíncrono: ID significa aceptación, no visibilidad final.
 return video_id


def publish(ep,movie,approval,story=False,auto=False,out_dir=None,facebook=True):
 """Reanuda exclusivamente destinos pendientes; jamás reenvía publicados o inciertos."""
 if not auto and approval!=ep['episode_id']:
  raise RuntimeError('Necesita --approved-id que coincida con el episodio')
 if auto and not ep.get('base_verified'):
  raise RuntimeError('Publicación autónoma bloqueada: falta verificación editorial')
 expected=os.getenv('IG_EXPECTED_USERNAME',ACCOUNT).strip().lstrip('@').lower()
 if expected!=ACCOUNT:raise RuntimeError('Cuenta de destino debe ser @'+ACCOUNT)
 token=os.environ['INSTAGRAM_TOKEN'];user=os.environ['INSTAGRAM_USER_ID']
 me=ig('GET','me',token,{'fields':'id,username'})
 if str(me.get('id'))!=user or str(me.get('username','')).lower()!=ACCOUNT:
  raise RuntimeError('Token Instagram apunta a otra cuenta')
 gh=Github();gh.assert_public()
 raw,sha=gh.read(STATE_PATH)
 state=json.loads(raw) if raw else {'schema':1,'episodes':{}}
 items=state.setdefault('episodes',{});row=items.setdefault(ep['episode_id'],{})
 for old_eid,old_row in items.items():
  if old_eid==ep['episode_id'] or not isinstance(old_row,dict):continue
  old_topic=old_row.get('topic') or legacy_topic_for_episode(old_eid)
  if old_topic==ep.get('id') and any(old_row.get(k) in ('publicado','enviando','incierto') for k in ('reel','story','facebook_reel','facebook_story','facebook_feed')):
   raise RuntimeError(f'Tema {old_topic} ya tiene un video enviado ({old_eid}); no se publicará una variante duplicada.')
 if not row.get('topic'):
  row['topic']=ep.get('id') or legacy_topic_for_episode(ep['episode_id'])

 destinations=['reel']+(['story'] if story else [])+(['facebook_reel']+(['facebook_story'] if story else [])+['facebook_feed'] if facebook else [])
 for dest in destinations:
  status=row.get(dest)
  if status in ('enviando','incierto'):
   raise RuntimeError(f'{dest}: envío anterior incierto. Revisar en Meta y conciliar el historial antes de reintentar; no duplicar')
  if status=='publicado' and not str(row.get(dest+'_id') or '').isdigit():
   raise RuntimeError(f'{dest}: figura publicado sin ID verificable en historial; revisar manualmente')
  if status not in (None,'publicado'):
   raise RuntimeError(f'{dest}: estado de historial no reconocido: {status!r}')
 print('REANUDACIÓN SEGURA:',{k:row.get(k,'pendiente') for k in destinations},flush=True)
 def save(status,which):
  nonlocal sha
  row[which]=status;row['updated_at']=utcnow()
  result=gh.write(STATE_PATH,json.dumps(state,ensure_ascii=False,indent=2).encode(),sha)
  sha=result['content']['sha']
 receipt_path=Path(out_dir or movie.parent)/'publicacion_confirmada.json'
 confirmation={'account':ACCOUNT,'episode_id':ep['episode_id'],'reel_id':str(row.get('reel_id') or '') or None,
  'reel_published':row.get('reel')=='publicado','story_requested':bool(story),'story_published':row.get('story')=='publicado',
  'story_id':str(row.get('story_id') or '') or None,
  'facebook_reel_requested':bool(facebook),'facebook_reel_published':row.get('facebook_reel')=='publicado',
  'facebook_reel_id':str(row.get('facebook_reel_id') or '') or None,
  'facebook_story_requested':bool(facebook and story),'facebook_story_published':row.get('facebook_story')=='publicado',
  'facebook_story_id':str(row.get('facebook_story_id') or '') or None,
  'facebook_feed_requested':bool(facebook),'facebook_feed_published':row.get('facebook_feed')=='publicado',
  'facebook_feed_id':str(row.get('facebook_feed_id') or '') or None,'confirmed_at':utcnow()}
 def confirm(which,media_id):
  row[which+'_id']=str(media_id)
  save('publicado',which)
  key='reel' if which=='reel' else which
  confirmation[key+'_id']=str(media_id)
  confirmation[key+'_published']=True
  confirmation['confirmed_at']=utcnow()
  write_json_atomic(receipt_path,confirmation)
  print(f'{which.upper()} ACEPTADO POR META: {media_id}',flush=True)
 write_json_atomic(receipt_path,confirmation)
 credits=ep.get('image_credits',[])
 if not credits or any(not license_accepted(c.get('license','')) for c in credits):
  raise RuntimeError('Sin imágenes CC0/dominio público verificadas')
 caption=episode_caption_base(ep)+'\n\n'+episode_hashtags(ep)
 if len(caption)>2200 or len(re.findall(r'(?<!\w)#[A-Za-z0-9_]+',caption))<10:
  raise RuntimeError('Descripción o hashtags no cumplen requisitos editoriales')
 # Una URL pública es necesaria solo si falta publicar Instagram, no al reanudar Facebook.
 pending_ig=[x for x in ('reel','story') if x in destinations and row.get(x)!='publicado']
 url=None
 if pending_ig:
  contents=movie.read_bytes();digest=hashlib.sha256(contents).hexdigest()[:16]
  remote=f'groundshake_sismos_media/{ep["episode_id"]}-{digest}.mp4'
  old_file,_=gh.read(remote)
  if old_file is None:result=gh.write(remote,contents);commit=result['commit']['sha']
  else:
   if old_file!=contents:raise RuntimeError('Colisión de medios')
   commit=gh.api('GET',f'/commits/{gh.branch}')['sha']
  url=f'https://raw.githubusercontent.com/{gh.repo}/{commit}/{remote}'
  with requests.get(url,timeout=90) as r:
   r.raise_for_status()
   if len(r.content)!=movie.stat().st_size:raise RuntimeError('URL MP4 incompleta')
 for which in ('reel','story'):
  if which not in destinations:continue
  if row.get(which)=='publicado':
   print(f'{which.upper()} OMITIDO: ya publicado con ID {row[which+"_id"]}',flush=True);continue
  save('enviando',which)
  try:pid=ig_publish(user,token,url,caption if which=='reel' else '',media_type='REELS' if which=='reel' else 'STORIES')
  except BaseException:save('incierto',which);raise
  confirm(which,pid)
 if facebook:
  page,fb_token=verify_facebook_page()
  confirmation['facebook_page_id']=page
  write_json_atomic(receipt_path,confirmation)
  for which in ('facebook_reel','facebook_story','facebook_feed'):
   if which not in destinations:continue
   if row.get(which)=='publicado':
    print(f'{which.upper()} OMITIDO: ya publicado con ID {row[which+"_id"]}',flush=True);continue
   save('enviando',which)
   try:
    if which=='facebook_reel':pid=fb_publish_reel(page,fb_token,movie,caption,ep['episode_id'])
    elif which=='facebook_story':pid=fb_publish_story(page,fb_token,movie)
    else:pid=fb_publish_feed_video(page,fb_token,movie,caption,ep['episode_id'])
   except BaseException:
    save('incierto',which)
    confirmation[which+'_uncertain']=True
    write_json_atomic(receipt_path,confirmation)
    raise
   confirm(which,pid)
 print('PUBLICACIONES SOLICITADAS A META; recibo:',receipt_path,flush=True)

def metrics(csv_path):
 import csv
 with open(csv_path,encoding='utf-8-sig',newline='') as fh:
  for row in csv.DictReader(fh):
   plays=int(row['plays']);views=int(row['views_7s'])
   if plays<0 or views<0 or views>plays:raise ValueError('Métricas inconsistentes')
   rate=views/plays if plays else 0
   print(f"{row['episode_id']}: R7={rate:.1%} | objetivo=70% | {'ALCANZADO' if rate>=TARGET_R7 else 'NO ALCANZADO'}")


def main():
 ap=argparse.ArgumentParser(description='SISMONAUTA @sismonauta: edición sismos, fotos reales, escenas limpias')
 ap.add_argument('--out',default='output_sismos');ap.add_argument('--topic',default='',help='ID de tema o vacío para elegir')
 ap.add_argument('--photo-dir',default='',help='Carpeta con fotografías propias/autorizadas, sin descarga')
 ap.add_argument('--no-ai',action='store_true');ap.add_argument('--no-voice',action='store_true')
 ap.add_argument('--publish',action='store_true');ap.add_argument('--auto-publish',action='store_true',help='Publicar automáticamente solo temas documentados de la base editorial');ap.add_argument('--approved-id',default='');ap.add_argument('--story',action='store_true')
 ap.add_argument('--no-facebook',action='store_true',help='Publicar solo en Instagram')
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
 # Consultar historial de ENVÍOS, no solo historial editorial: un episodio puede
 # haberse publicado parcialmente y no figurar aun en sismos_editorial.json.
 published_topics=set()
 if args.publish:
  gh_state=Github()
  raw_state,_=gh_state.read(STATE_PATH)
  live_state=json.loads(raw_state) if raw_state else {'episodes':{}}
  published_topics=remote_published_topics(live_state,history)
  print('ANTIDUPLICADOS: temas ya publicados/en proceso:',sorted(published_topics),flush=True)
 if args.auto_publish and not args.publish:
  raise RuntimeError('--auto-publish requiere --publish')
 if args.publish:
  verify_instagram_account()
  if not args.no_facebook:verify_facebook_page()
 if args.auto_publish and args.episode_json:
  raise RuntimeError('No usar --episode-json con --auto-publish: solo se admiten temas base')
 if args.episode_json:
  ep=read_json(args.episode_json,None)
  if not isinstance(ep,dict) or not all(ep.get(x) for x in ('episode_id','title','script','visuals','source','queries')):
   raise RuntimeError('El JSON del episodio no es válido o está incompleto')
  if not str(ep['episode_id']).startswith('sismos-'):
   raise RuntimeError('Este archivo solo admite episodios sísmicos de SISMONAUTA')
  t={'id':ep.get('id','revisado')}
 else:
  t=choose_topic(history,args.topic,published_topics);ep=generate_angle(t,history,use_ai=(not args.no_ai and not args.auto_publish))
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
  publish(ep,movie,args.approved_id,story=args.story,auto=args.auto_publish,out_dir=out,facebook=not args.no_facebook)
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
