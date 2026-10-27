
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA (@sismonauta)
MONITOR DE DIVULGACIÓN SÍSMICA — VERSIÓN MEJORADA

Python 3.11+

CARACTERÍSTICAS
---------------
- 25 temas científicos documentados.
- Selección de temas inéditos.
- Historial antduplicados persistente en GitHub.
- Fotografías reales de Wikimedia Commons y Openverse.
- Acepta una fotografía auténtica cuando no existen más.
- Diferentes encuadres de una misma fotografía.
- Búsqueda temática y filtrado de imágenes.
- Licencias CC0 y dominio público.
- Openverse con reintentos y suspensión temporal.
- Voz individual sincronizada con cada escena.
- Rótulos centrales grandes.
- Música instrumental sintetizada.
- Portada automática.
- Escena final DALE LIKE con icono animado.
- Instagram Reel y Story.
- Facebook Reel, Story y Feed.
- Publicación automática o manual.
- Verificación de cuentas y estados de publicación.

INSTALACIÓN
-----------
pip install pillow requests edge-tts mutagen imageio-ffmpeg

También requiere FFmpeg disponible en PATH.

PRUEBA
------
python monitor.py --out output_sismos

PUBLICAR AUTOMÁTICAMENTE
------------------------
python monitor.py --publish --auto-publish --story

VARIABLES DE ENTORNO
--------------------
INSTAGRAM_TOKEN
INSTAGRAM_USER_ID
FACEBOOK_PAGE_ID
FACEBOOK_PAGE_TOKEN
GH_TOKEN
GITHUB_REPOSITORY
GITHUB_REF_NAME

OPCIONALES
----------
GEMINI_API_KEY
OPENROUTER_API_KEY
IG_GRAPH_VERSION
IG_GRAPH_BASE
IG_EXPECTED_USERNAME
FACEBOOK_EXPECTED_USERNAME
DV_VOICE

NOTA EDITORIAL
--------------
Los contenidos están basados en afirmaciones científicas
predefinidas. La IA no se considera fuente de verificación
independiente.

El sistema no inventa noticias, predicciones sísmicas ni
datos científicos nuevos para completar episodios.

Si no encuentra ninguna fotografía válida, no publica.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import wave

from datetime import datetime, timezone
from pathlib import Path

import requests

from PIL import (
    Image,
    ImageDraw,
    ImageEnhance,
    ImageFont,
    ImageOps,
    ImageFilter,
    ImageStat,
)

try:
    from IPython.display import Video, display
except ImportError:
    Video = None

    def display(*args, **kwargs):
        return None


# ============================================================
# 1. CONFIGURACIÓN GENERAL
# ============================================================

BRAND = "SISMONAUTA"
ACCOUNT = "sismonauta"

# Mantener ruta histórica para no perder antduplicados.
STATE_PATH = "groundshake/state.json"

GRAPH_VERSION = os.getenv(
    "IG_GRAPH_VERSION", "v25.0"
)

GRAPH_URL = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com",
).rstrip("/")

W = 720
H = 1280
FPS = 30

TARGET_R7 = 0.70

VOICE = os.getenv(
    "DV_VOICE",
    "es-CL-CatalinaNeural",
)

USER_AGENT = (
    "SismonautaSismos/3.0 "
    "(educational research; https://commons.wikimedia.org)"
)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": USER_AGENT
})

# Configuración de búsqueda.
MIN_VALID_PHOTOS = 1
MAX_VALID_PHOTOS = 3

COMMONS_TIMEOUT = (8, 30)
OPENVERSE_TIMEOUT = (8, 15)

OPENVERSE_MAX_ATTEMPTS = 2
OPENVERSE_BACKOFF = 600

_OPENVERSE_UNAVAILABLE_UNTIL = 0.0


# ============================================================
# 2. UTILIDADES
# ============================================================

def norm(s):
    return "".join(
        c for c in unicodedata.normalize(
            "NFKD", str(s).lower()
        )
        if not unicodedata.combining(c)
    )


def utcnow():
    return datetime.now(
        timezone.utc
    ).isoformat()


def read_json(path, default):
    try:
        return json.loads(
            Path(path).read_text(
                encoding="utf-8"
            )
        )
    except FileNotFoundError:
        return default


def write_json_atomic(path, obj):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tmp.replace(path)


# ============================================================
# 3. CATÁLOGO CIENTÍFICO
# ============================================================

TOPICS = [
    {
        "id": "ruptura",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Todos los terremotos rompen la superficie?",
        "fact": (
            "Un terremoto puede romper la superficie cuando "
            "el desplazamiento de una falla alcanza el terreno; "
            "no todos lo hacen."
        ),
        "lesson": "Una grieta visible no acompaña a todos los sismos.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards/science/faqs?page=1",
        "queries": ["earthquake surface rupture", "fault scarp"],
    },
    {
        "id": "deslizamientos",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Un terremoto puede derrumbar una montaña?",
        "fact": (
            "Las sacudidas fuertes pueden desestabilizar "
            "laderas y desencadenar deslizamientos de tierra."
        ),
        "lesson": "Las consecuencias de un terremoto no se limitan a las sacudidas.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards/science/faqs",
        "queries": ["earthquake landslide", "earthquake triggered landslide"],
    },
    {
        "id": "grietas",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿La Tierra se abre durante un terremoto?",
        "fact": (
            "Algunos terremotos provocan grietas poco profundas "
            "por deslizamientos y deformación del terreno; "
            "las fallas no se abren como grandes abismos."
        ),
        "lesson": "Las grietas superficiales no son pozos sin fondo.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards/science/faqs",
        "queries": ["earthquake ground fissure", "earthquake surface rupture"],
    },
    {
        "id": "transformante",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Qué ocurre cuando dos placas se deslizan de lado?",
        "fact": (
            "En los límites transformantes, dos placas se "
            "desplazan horizontalmente una respecto a la otra; "
            "estas zonas pueden generar grandes terremotos."
        ),
        "lesson": "La falla de San Andrés es un ejemplo de este movimiento.",
        "source": "https://www.noaa.gov/jetstream/tsunamis/tsunami-generation-earthquakes/jetstream-max-plate-tectonics-and-earthquakes",
        "queries": ["San Andreas Fault", "transform fault landscape"],
    },
    {
        "id": "rift",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Por qué se forman grandes valles de fractura?",
        "fact": (
            "En algunos límites divergentes, las placas se "
            "separan y pueden formar valles de rift "
            "acompañados de actividad sísmica."
        ),
        "lesson": "Un valle puede contar la historia de placas que se separan.",
        "source": "https://www.noaa.gov/jetstream/tsunamis/tsunami-generation-earthquakes/jetstream-max-plate-tectonics-and-earthquakes",
        "queries": ["East African Rift valley", "rift valley landscape"],
    },
    {
        "id": "subduccion",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Qué ocurre cuando una placa se hunde bajo otra?",
        "fact": (
            "En las zonas de subducción, una placa se introduce "
            "bajo otra y pueden producirse grandes terremotos "
            "y tsunamis."
        ),
        "lesson": "La costa puede revelar dónde convergen las placas.",
        "source": "https://www.noaa.gov/jetstream/tsunamis/tsunami-generation-earthquakes/jetstream-max-plate-tectonics-and-earthquakes",
        "queries": ["subduction zone coast", "tectonic coastal mountains"],
    },
    {
        "id": "colision",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Qué relación tienen las montañas con la tectónica?",
        "fact": (
            "La colisión de placas continentales puede elevar "
            "y deformar rocas, formando grandes cordilleras "
            "y fallas inversas."
        ),
        "lesson": "Las cordilleras guardan señales del movimiento terrestre.",
        "source": "https://www.bgs.ac.uk/discovering-geology/earth-hazards/earthquakes/what-causes-earthquakes/",
        "queries": ["Himalaya tectonic mountains", "folded geological mountain rocks"],
    },
    {
        "id": "falla_inversa",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Puede una falla elevar parte del paisaje?",
        "fact": (
            "En una falla inversa un bloque de roca se desplaza "
            "hacia arriba respecto al otro, debido a esfuerzos "
            "de compresión."
        ),
        "lesson": "La compresión también transforma el relieve.",
        "source": "https://www.bgs.ac.uk/discovering-geology/earth-hazards/earthquakes/what-causes-earthquakes/",
        "queries": ["thrust fault scarp", "reverse fault rock outcrop"],
    },
    {
        "id": "falla_normal",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Una falla puede hundir un bloque de terreno?",
        "fact": (
            "En las fallas normales un bloque desciende "
            "respecto al otro bajo condiciones de extensión "
            "de la corteza."
        ),
        "lesson": "La extensión deja escalones visibles en el terreno.",
        "source": "https://www.bgs.ac.uk/discovering-geology/earth-hazards/earthquakes/what-causes-earthquakes/",
        "queries": ["normal fault scarp", "rift valley fault scarp"],
    },
    {
        "id": "tsunami_oleadas",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿La primera ola de un tsunami es siempre la mayor?",
        "fact": (
            "La primera ola de un tsunami no tiene por qué ser "
            "la más alta ni la más destructiva; pueden llegar "
            "varias oleadas."
        ),
        "lesson": "La primera ola no marca necesariamente el final del peligro.",
        "source": "https://www.noaa.gov/jetstream/tsunamis/tsunami-inundation",
        "queries": ["tsunami inundation coast", "tsunami shoreline"],
    },
    {
        "id": "tsunami_retroceso",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Por qué el mar puede retroceder antes de un tsunami?",
        "fact": (
            "Algunos tsunamis llegan primero con un valle de onda "
            "y producen un retiro inusual del mar; no ocurre "
            "en todos los casos."
        ),
        "lesson": "Un retiro repentino del mar es una señal natural de peligro.",
        "source": "https://www.noaa.gov/jetstream/tsunamis/tsunami-inundation",
        "queries": ["tsunami receding shoreline", "tsunami exposed seafloor coast"],
    },
    {
        "id": "tsunami_rios",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Un tsunami puede avanzar por un río?",
        "fact": (
            "Los tsunamis pueden remontar ríos y estuarios "
            "conectados con el mar, extendiendo la inundación "
            "tierra adentro."
        ),
        "lesson": "La amenaza puede seguir el curso de un río.",
        "source": "https://www.noaa.gov/jetstream/tsunamis/tsunami-inundation",
        "queries": ["tsunami river estuary", "tsunami flooded coast"],
    },
    {
        "id": "tsunami_inundacion",
        "category": "Geología y fenómenos sísmicos",
        "title": "¿Un tsunami siempre parece una enorme ola que rompe?",
        "fact": (
            "Un tsunami puede llegar a la costa como una "
            "inundación que sube rápidamente, y no necesariamente "
            "como una ola gigante de película."
        ),
        "lesson": "El peligro no siempre tiene la forma que imaginamos.",
        "source": "https://www.weather.gov/safety/tsunami-about",
        "queries": ["tsunami coastal inundation", "tsunami flood shoreline"],
    },
    {
        "id": "ondas",
        "category": "Ciencia sísmica",
        "title": "¿Por qué hay distintos tipos de ondas sísmicas?",
        "fact": (
            "Las ondas P y S viajan de maneras diferentes; "
            "las ondas S no se propagan a través de líquidos."
        ),
        "lesson": "El comportamiento de las ondas revela propiedades del interior terrestre.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards/science/earthquake-science",
        "queries": ["seismograph recording instrument", "seismometer scientific equipment"],
    },
    {
        "id": "magnitud",
        "category": "Mitos y ciencia",
        "title": "¿Por qué magnitud e intensidad no son lo mismo?",
        "fact": (
            "La magnitud describe el tamaño de un terremoto; "
            "la intensidad describe los efectos y sacudidas "
            "en un lugar."
        ),
        "lesson": "Un mismo terremoto puede sentirse de forma diferente según la ubicación.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards",
        "queries": ["seismograph seismogram", "earthquake damaged building"],
    },
    {
        "id": "replicas",
        "category": "Ciencia sísmica",
        "title": "¿Por qué ocurren réplicas después de un terremoto?",
        "fact": (
            "Las réplicas son terremotos que siguen a uno mayor "
            "en su misma región durante el reajuste de la corteza."
        ),
        "lesson": "La actividad sísmica puede continuar tras un evento principal.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards",
        "queries": ["earthquake fault rocks", "earthquake geology fault"],
    },
    {
        "id": "valdivia",
        "category": "Historia sísmica",
        "title": "El mayor terremoto registrado instrumentalmente",
        "fact": (
            "El terremoto de Valdivia, Chile, del 22 de mayo "
            "de 1960 alcanzó una magnitud de 9,5."
        ),
        "lesson": "Los registros históricos permiten estudiar terremotos extraordinarios.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards/science/20-largest-earthquakes-world",
        "queries": ["1960 Valdivia earthquake Chile", "1960 Chile earthquake aftermath"],
    },
    {
        "id": "profundidad",
        "category": "Curiosidades sísmicas",
        "title": "¿Qué diferencia a un terremoto superficial de uno profundo?",
        "fact": (
            "Los terremotos pueden originarse a profundidades "
            "muy distintas; la profundidad influye en cómo "
            "se perciben sus ondas."
        ),
        "lesson": "La profundidad es un dato importante al interpretar un sismo.",
        "source": "https://earthquake.usgs.gov/earthquakes/map/",
        "queries": ["subduction zone geological landscape", "tectonic fault mountain"],
    },
    {
        "id": "placas",
        "category": "Tectónica",
        "title": "¿Por qué se mueve la superficie de la Tierra?",
        "fact": (
            "La litosfera está dividida en placas que se "
            "desplazan lentamente y pueden acumular "
            "esfuerzos en sus bordes."
        ),
        "lesson": "El movimiento de placas explica gran parte de la actividad sísmica.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards",
        "queries": ["tectonic fault landscape", "San Andreas Fault aerial"],
    },
    {
        "id": "tsunamis",
        "category": "Ciencia sísmica",
        "title": "¿Todos los terremotos submarinos provocan tsunamis?",
        "fact": (
            "No todos los terremotos submarinos generan tsunamis; "
            "importa el desplazamiento del fondo marino y "
            "otros factores."
        ),
        "lesson": "Un terremoto bajo el océano no implica automáticamente un tsunami.",
        "source": "https://www.tsunami.gov/",
        "queries": ["tsunami coast", "tsunami inundation shoreline"],
    },
    {
        "id": "alerta",
        "category": "Tecnología",
        "title": "¿Una alerta sísmica predice terremotos?",
        "fact": (
            "Los sistemas de alerta temprana detectan un "
            "terremoto ya iniciado e intentan avisar antes "
            "de la llegada de sacudidas fuertes."
        ),
        "lesson": "La alerta temprana no es una predicción del próximo terremoto.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards/earthquake-early-warning",
        "queries": ["earthquake surface rupture", "San Andreas Fault"],
    },
    {
        "id": "falla",
        "category": "Tectónica",
        "title": "¿Qué es realmente una falla geológica?",
        "fact": (
            "Una falla es una fractura de las rocas a lo "
            "largo de la cual ha ocurrido desplazamiento."
        ),
        "lesson": "Las fallas ayudan a comprender dónde y cómo se deforman las rocas.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards",
        "queries": ["San Andreas Fault California", "geological fault rock outcrop"],
    },
    {
        "id": "prediccion",
        "category": "Mitos y ciencia",
        "title": "¿La ciencia puede predecir el día exacto de un sismo?",
        "fact": (
            "Actualmente no existe un método científicamente "
            "validado que prediga con certeza fecha, lugar "
            "y magnitud de un gran terremoto."
        ),
        "lesson": "Las estimaciones de probabilidad no son predicciones exactas.",
        "source": "https://www.usgs.gov/faqs/can-you-predict-earthquakes",
        "queries": ["San Andreas Fault", "fault scarp"],
    },
    {
        "id": "sismografo",
        "category": "Tecnología",
        "title": "¿Cómo registra un instrumento el movimiento del suelo?",
        "fact": (
            "Los sismómetros detectan el movimiento del suelo "
            "y producen registros que ayudan a estudiar "
            "los terremotos."
        ),
        "lesson": "Las ondas sísmicas permiten investigar fenómenos que no podemos ver directamente.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards",
        "queries": ["seismograph recording paper", "seismometer field instrument"],
    },
    {
        "id": "terremoto",
        "category": "Curiosidades sísmicas",
        "title": "¿Por qué un terremoto lejano puede sentirse?",
        "fact": (
            "Las ondas sísmicas transportan energía a través "
            "de la Tierra y algunas pueden recorrer grandes distancias."
        ),
        "lesson": "La distancia al epicentro no explica por sí sola toda la percepción del temblor.",
        "source": "https://www.usgs.gov/programs/earthquake-hazards",
        "queries": ["earthquake surface rupture", "earthquake geological landscape"],
    },
]

STYLES = (
    "descubrimiento",
    "microdocumental",
    "mito_realidad",
    "mapa_explicado",
    "pregunta_respuesta",
)


# ============================================================
# 4. GUIONES CIENTÍFICOS POR ESCENA
# ============================================================

RETENTION_STORIES = {
    "ruptura": [
        ("¿Todos los terremotos dejan una gran fractura visible?", "¿SE ROMPE SIEMPRE EL SUELO?"),
        ("La respuesta depende de dónde llega el movimiento.", "NO SIEMPRE ES VISIBLE"),
        ("Una falla puede romper la superficie si el desplazamiento alcanza el terreno.", "RUPTURA EN LA SUPERFICIE"),
        ("Muchos terremotos no producen una ruptura superficial visible.", "NO TODOS LA PRODUCEN"),
        ("La ausencia de una grieta no significa que no haya ocurrido un sismo.", "LO INVISIBLE TAMBIÉN SUCEDE"),
    ],
    "deslizamientos": [
        ("¿Puede un terremoto derrumbar una ladera entera?", "¿TEMBLOR O DERRUMBE?"),
        ("Las montañas también responden al movimiento.", "LA LADERA PUEDE CEDER"),
        ("Una sacudida intensa puede desestabilizar el terreno.", "EL TERRENO PIERDE ESTABILIDAD"),
        ("Esto puede desencadenar deslizamientos de tierra.", "SE PRODUCEN DESLIZAMIENTOS"),
        ("El peligro no siempre termina cuando el suelo deja de temblar.", "MIRA TAMBIÉN LAS LADERAS"),
    ],
    "grietas": [
        ("¿Un terremoto puede abrir el suelo como en las películas?", "¿SE ABRE LA TIERRA?"),
        ("Las grietas reales no son abismos infinitos.", "LA REALIDAD ES DISTINTA"),
        ("Pueden aparecer fisuras superficiales por movimientos del terreno.", "APARECEN FISURAS REALES"),
        ("Deslizamientos y deformaciones pueden causar esas fracturas.", "EL TERRENO SE DEFORMA"),
        ("No confundas una grieta superficial con un enorme abismo.", "NO ES UN ABISMO"),
    ],
    "transformante": [
        ("¿Qué sucede cuando dos placas se rozan de lado?", "¿PLACAS QUE SE DESLIZAN?"),
        ("No todos los bordes de placas chocan de frente.", "NO SIEMPRE HAY CHOQUE"),
        ("En una falla transformante, las placas se mueven lateralmente.", "MOVIMIENTO HORIZONTAL"),
        ("Ese desplazamiento puede generar fuertes terremotos.", "PUEDEN OCURRIR TERREMOTOS"),
        ("La falla de San Andrés permite observar este tipo de límite.", "SAN ANDRÉS DA PISTAS"),
    ],
    "rift": [
        ("¿Por qué existen valles tan largos entre montañas?", "¿CÓMO NACE UN RIFT?"),
        ("Algunos revelan que la corteza se está separando.", "LA CORTEZA SE SEPARA"),
        ("En un límite divergente las placas se alejan entre sí.", "PLACAS EN SEPARACIÓN"),
        ("La extensión puede formar valles de rift y actividad sísmica.", "UN VALLE QUE SE ABRE"),
        ("El relieve también deja huellas del movimiento de placas.", "EL PAISAJE CUENTA HISTORIAS"),
    ],
    "subduccion": [
        ("¿Cómo puede una placa hundirse bajo otra?", "¿QUÉ ES SUBDUCCIÓN?"),
        ("En algunas costas ocurre un movimiento enorme y lento.", "OCURRE BAJO LAS COSTAS"),
        ("Una placa tectónica se introduce por debajo de otra.", "UNA PLACA DESCIENDE"),
        ("Estas zonas pueden producir grandes terremotos y tsunamis.", "GRANDES EVENTOS POSIBLES"),
        ("La forma de una costa puede estar ligada a su tectónica.", "UNA COSTA EN MOVIMIENTO"),
    ],
    "colision": [
        ("¿Las montañas pueden formarse por choque de placas?", "¿MONTAÑAS POR UN CHOQUE?"),
        ("Una cordillera puede acumular la historia de una colisión.", "LAS ROCAS SE COMPRIMEN"),
        ("Las placas continentales pueden empujar y deformar rocas.", "LA CORTEZA SE DEFORMA"),
        ("Ese proceso eleva montañas y origina fallas inversas.", "SE ELEVA EL RELIEVE"),
        ("Las rocas de una montaña conservan señales tectónicas.", "LA MONTAÑA GUARDA PISTAS"),
    ],
    "falla_inversa": [
        ("¿Puede un terremoto elevar una parte del terreno?", "¿EL SUELO PUEDE ELEVARSE?"),
        ("Las rocas se deforman cuando están comprimidas.", "LAS ROCAS SE COMPRIMEN"),
        ("En una falla inversa un bloque sube respecto al otro.", "UN BLOQUE SUBE"),
        ("El desplazamiento puede cambiar la forma del paisaje.", "EL RELIEVE CAMBIA"),
        ("Una falla puede dejar escalones en la superficie.", "LA TIERRA DEJA MARCAS"),
    ],
    "falla_normal": [
        ("¿Qué ocurre cuando la corteza se estira?", "¿LA CORTEZA SE ESTIRA?"),
        ("No todos los movimientos tectónicos comprimen.", "TAMBIÉN HAY EXTENSIÓN"),
        ("En una falla normal un bloque baja respecto al otro.", "UN BLOQUE DESCIENDE"),
        ("Ese movimiento puede formar escarpes y valles.", "APARECEN ESCARPES"),
        ("La forma del terreno conserva señales de la extensión.", "EL PAISAJE REVELA EXTENSIÓN"),
    ],
    "tsunami_oleadas": [
        ("¿La primera ola de un tsunami es siempre la mayor?", "¿LA PRIMERA ES LA PEOR?"),
        ("Una sola ola no cuenta toda la historia.", "PUEDE HABER MÁS OLAS"),
        ("La primera no tiene por qué ser la más alta.", "LA MAYOR PUEDE LLEGAR DESPUÉS"),
        ("Pueden llegar oleadas sucesivas durante horas.", "EL RIESGO PUEDE CONTINUAR"),
        ("No vuelvas a la costa hasta que las autoridades indiquen que es seguro.", "ESPERA INDICACIONES OFICIALES"),
    ],
    "tsunami_retroceso": [
        ("¿Por qué a veces el mar se aleja de repente?", "¿EL MAR DESAPARECE?"),
        ("Ese retiro no siempre es una marea normal.", "PUEDE SER UNA SEÑAL"),
        ("Algunos tsunamis llegan primero con un valle de onda.", "EL AGUA PUEDE RETROCEDER"),
        ("El fondo marino puede quedar temporalmente expuesto.", "EL FONDO QUEDA A LA VISTA"),
        ("Si notas un retiro repentino inusual, aléjate a zonas altas.", "ALÉJATE DE LA COSTA"),
    ],
    "tsunami_rios": [
        ("¿Un tsunami puede avanzar por un río?", "¿UN TSUNAMI EN UN RÍO?"),
        ("El océano puede llevar el riesgo tierra adentro.", "EL PELIGRO AVANZA"),
        ("Las olas pueden remontar ríos y estuarios costeros.", "PUEDE ENTRAR POR ESTUARIOS"),
        ("Eso puede extender la inundación lejos de la orilla.", "LLEGA MÁS ALLÁ DE LA PLAYA"),
        ("Los cauces conectados con el mar también requieren precaución.", "CUIDADO CON LOS CAUCES"),
    ],
    "tsunami_inundacion": [
        ("¿Un tsunami siempre parece una ola gigante de película?", "¿UNA OLA GIGANTE?"),
        ("Su aspecto puede ser mucho menos evidente.", "NO SIEMPRE ROMPE COMO OLA"),
        ("A veces llega como una subida rápida del agua.", "UNA INUNDACIÓN REPENTINA"),
        ("Las corrientes y la inundación pueden ser peligrosas.", "EL AGUA TIENE FUERZA"),
        ("No esperes una pared de agua para reconocer el peligro.", "NO ESPERES VER UNA GRAN OLA"),
    ],
    "alerta": [
        ("¿Cómo puede una alerta avisarte antes de que sientas el terremoto?", "¿TE PUEDE AVISAR ANTES?"),
        ("No es que conozca el futuro. La explicación ocurre bajo nuestros pies.", "NO ES ADIVINACIÓN"),
        ("El terremoto ya comenzó cuando los sensores detectan sus primeras ondas.", "EL SISMO YA COMENZÓ"),
        ("La alerta puede llegar antes que las sacudidas fuertes, pero no siempre hay tiempo suficiente.", "SEGUNDOS QUE PUEDEN IMPORTAR"),
        ("La alerta no predice el sismo. Detecta lo que ya empezó.", "DETECTAR NO ES PREDECIR"),
    ],
    "ondas": [
        ("¿Cómo sabemos qué ocurre dentro de la Tierra sin poder mirar allí?", "¿CÓMO MIRAMOS BAJO TIERRA?"),
        ("La pista viene de algo que viaja después de un terremoto.", "LAS ONDAS GUARDAN PISTAS"),
        ("Las ondas P y S se propagan de forma distinta.", "NO TODAS VIAJAN IGUAL"),
        ("Las ondas S no atraviesan materiales líquidos. Esa diferencia revela propiedades del interior terrestre.", "LAS ONDAS S NO CRUZAN LÍQUIDOS"),
        ("Lo que una onda no puede atravesar también nos enseña algo.", "EL SILENCIO TAMBIÉN INFORMA"),
    ],
    "magnitud": [
        ("¿Por qué el mismo terremoto puede sentirse muy distinto en dos ciudades?", "¿UN SISMO, DOS EXPERIENCIAS?"),
        ("El número que escuchamos en las noticias no explica todo.", "EL NÚMERO NO BASTA"),
        ("La magnitud describe el tamaño del terremoto.", "LA MAGNITUD MIDE EL TAMAÑO"),
        ("La intensidad describe las sacudidas y los efectos en cada lugar.", "LA INTENSIDAD CAMBIA SEGÚN EL LUGAR"),
        ("El terremoto es uno. Sus efectos no tienen por qué ser iguales.", "UN SISMO. MUCHAS EXPERIENCIAS"),
    ],
    "replicas": [
        ("¿El peligro termina exactamente cuando deja de temblar?", "¿YA TERMINÓ TODO?"),
        ("Después de un terremoto, la región puede seguir reajustándose.", "LA TIERRA SIGUE REAJUSTÁNDOSE"),
        ("Esos movimientos posteriores se conocen como réplicas.", "PUEDEN LLEGAR RÉPLICAS"),
        ("Pueden ocurrir después del evento principal en la misma región.", "EL MOVIMIENTO PUEDE CONTINUAR"),
        ("Que el suelo esté quieto ahora no significa que no haya más réplicas.", "EL SILENCIO NO SIEMPRE ES EL FINAL"),
    ],
    "valdivia": [
        ("¿Sabías que el mayor terremoto registrado ocurrió en Chile?", "¿EL MAYOR TERREMOTO REGISTRADO?"),
        ("Sucedió en un lugar que quedó grabado en la historia sísmica.", "CHILE QUEDÓ EN LA HISTORIA"),
        ("El 22 de mayo de 1960, Valdivia vivió un terremoto de magnitud nueve coma cinco.", "VALDIVIA 1960: MAGNITUD 9,5"),
        ("Es el mayor registrado instrumentalmente. Sus datos siguen siendo relevantes para la ciencia.", "UN REGISTRO EXTRAORDINARIO"),
        ("Valdivia nos recuerda cuánta energía puede liberar la Tierra.", "VALDIVIA: UNA FECHA IMPOSIBLE DE OLVIDAR"),
    ],
    "profundidad": [
        ("¿Por qué dos terremotos de magnitud parecida pueden sentirse diferentes?", "¿MISMA MAGNITUD, DISTINTA SACUDIDA?"),
        ("Hay un dato que suele quedar fuera del titular.", "MIRA BAJO LA SUPERFICIE"),
        ("No todos los sismos comienzan a la misma profundidad.", "LA PROFUNDIDAD CAMBIA"),
        ("La profundidad del origen influye en cómo se percibe el movimiento del suelo.", "NO TODO ES LA MAGNITUD"),
        ("Para entender un sismo, también hay que mirar dónde comienza.", "IMPORTA LO QUE NO VEMOS"),
    ],
    "placas": [
        ("¿Cómo puede moverse el suelo que parece tan firme bajo tus pies?", "¿LA TIERRA ESTÁ QUIETA?"),
        ("El movimiento es lento. Pero sus efectos pueden ser enormes.", "UN MOVIMIENTO CASI INVISIBLE"),
        ("La parte externa rígida de la Tierra está dividida en placas.", "VIVIMOS SOBRE PLACAS"),
        ("Esas placas se desplazan lentamente y pueden acumular esfuerzos en sus bordes.", "LOS ESFUERZOS SE ACUMULAN"),
        ("El suelo parece inmóvil. La Tierra sigue en movimiento.", "LA QUIETUD ES SOLO APARIENCIA"),
    ],
    "tsunamis": [
        ("¿Cada terremoto bajo el mar termina creando un tsunami?", "¿SISMO SUBMARINO = TSUNAMI?"),
        ("La respuesta depende de más que la ubicación del epicentro.", "NO BASTA CON ESTAR BAJO EL MAR"),
        ("No todos los terremotos submarinos producen tsunamis.", "NO TODOS GENERAN TSUNAMIS"),
        ("El desplazamiento del fondo marino y otros factores influyen en su generación.", "IMPORTA CÓMO SE MUEVE EL FONDO"),
        ("Bajo el océano, no todo temblor levanta una gran ola.", "UN SISMO NO SIEMPRE ES UN TSUNAMI"),
    ],
    "falla": [
        ("¿Una falla geológica es simplemente una grieta en el terreno?", "¿SOLO UNA GRIETA?"),
        ("Hay una diferencia que cambia su significado.", "LA CLAVE ES EL MOVIMIENTO"),
        ("Una falla es una fractura donde las rocas se han desplazado.", "LAS ROCAS SE DESPLAZARON"),
        ("Estudiar esas fracturas ayuda a comprender la deformación de la corteza.", "LA ROCA CONSERVA EVIDENCIAS"),
        ("Una falla no solo se ve. También cuenta cómo se movió la Tierra.", "LA ROCA CUENTA SU HISTORIA"),
    ],
    "prediccion": [
        ("¿Alguien puede decirte el día exacto del próximo gran terremoto?", "¿SE PUEDE SABER EL DÍA EXACTO?"),
        ("Hay mucha diferencia entre una estimación y una predicción.", "NO CONFUNDAS PROBABILIDAD Y CERTEZA"),
        ("Hoy no existe un método validado que anticipe con certeza fecha, lugar y magnitud.", "NO EXISTE PREDICCIÓN EXACTA VALIDADA"),
        ("La ciencia puede estudiar probabilidades, pero eso no equivale a conocer el futuro.", "RIESGO NO ES UNA FECHA"),
        ("Quien promete una fecha exacta necesita pruebas que la ciencia aún no tiene.", "DESCONFÍA DE LA CERTEZA SIN PRUEBAS"),
    ],
    "sismografo": [
        ("¿Cómo puede una máquina registrar un movimiento que casi no vemos?", "¿CÓMO SE VE UN MOVIMIENTO INVISIBLE?"),
        ("La respuesta comienza con un instrumento sensible al suelo.", "UN SENSOR ESCUCHA AL SUELO"),
        ("Los sismómetros detectan y registran sus movimientos.", "EL SISMÓMETRO REGISTRA MOVIMIENTO"),
        ("Esos registros ayudan a estudiar terremotos y el paso de ondas sísmicas.", "EL MOVIMIENTO SE CONVIERTE EN DATOS"),
        ("Lo que no podemos ver a simple vista puede dejar un registro.", "LO INVISIBLE DEJA HUELLAS"),
    ],
    "terremoto": [
        ("¿Cómo puede sentirse un terremoto que ocurrió tan lejos?", "¿POR QUÉ SE SIENTE TAN LEJOS?"),
        ("La energía liberada no se queda junto al epicentro.", "LA ENERGÍA VIAJA"),
        ("Las ondas sísmicas se propagan a través de la Tierra.", "LAS ONDAS CRUZAN DISTANCIAS"),
        ("Algunas pueden recorrer grandes distancias y producir movimientos perceptibles.", "EL MOVIMIENTO PUEDE LLEGAR LEJOS"),
        ("Un terremoto comienza en un lugar. Sus ondas pueden llegar mucho más allá.", "EL EPICENTRO NO ES EL LÍMITE"),
    ],
}


# ============================================================
# 5. TEXTOS EDITORIALES Y HASHTAGS
# ============================================================

MEMORABLE_CLOSINGS = {
    "ondas": "LAS ONDAS CUENTAN LO QUE NO VEMOS.",
    "magnitud": "UN SOLO SISMO. MUCHAS FORMAS DE SENTIRLO.",
    "replicas": "QUE PARE EL TEMBLOR NO SIGNIFICA QUE TERMINEN LAS RÉPLICAS.",
    "valdivia": "VALDIVIA 1960: UNA FECHA QUE LA TIERRA NO BORRA.",
    "profundidad": "LA PROFUNDIDAD TAMBIÉN CUENTA LA HISTORIA.",
    "placas": "BAJO NUESTROS PIES, LA TIERRA SIGUE MOVIÉNDOSE.",
    "tsunamis": "BAJO EL MAR, NO TODO TEMBLOR LEVANTA UNA OLA.",
    "alerta": "LA ALERTA NO ADIVINA: DETECTA LO QUE YA COMENZÓ.",
    "falla": "UNA FALLA NO ES SOLO UNA GRIETA: ES MOVIMIENTO.",
    "prediccion": "PROBABILIDAD NO SIGNIFICA SABER CUÁNDO TEMBLARÁ.",
    "sismografo": "UN SISMÓMETRO HACE VISIBLE LO INVISIBLE.",
    "terremoto": "LAS ONDAS VIAJAN MÁS ALLÁ DE LO QUE VEMOS.",
}

TOPIC_HASHTAGS = {
    "ondas": ["OndasSismicas", "OndasP", "OndasS", "PropagacionDeOndas", "InteriorTerrestre", "Sismologia", "Geofisica", "CienciaDeLaTierra"],
    "magnitud": ["MagnitudSismica", "IntensidadSismica", "EscalaDeMagnitud", "MovimientoDelSuelo", "Sismologia", "RiesgoSismico", "CienciaDeLaTierra", "Geofisica"],
    "replicas": ["ReplicasSismicas", "ActividadSismica", "Terremoto", "FallasGeologicas", "CortezaTerrestre", "Sismologia", "Geologia", "CienciaDeLaTierra"],
    "valdivia": ["Valdivia1960", "TerremotoDeValdivia", "ChileSismico", "HistoriaSismica", "Magnitud95", "Sismologia", "TerremotosHistoricos", "GeologiaDeChile"],
    "profundidad": ["ProfundidadSismica", "Hipocentro", "Epicentro", "Sismologia", "TerremotosProfundos", "TerremotosSuperficiales", "Geofisica", "CienciaDeLaTierra"],
    "placas": ["PlacasTectonicas", "TectonicaDePlacas", "Litosfera", "Geologia", "FallasGeologicas", "CienciaDeLaTierra", "Sismologia", "MovimientoDePlacas"],
    "tsunamis": ["Tsunami", "TerremotoSubmarino", "FondoMarino", "RiesgoDeTsunami", "Sismologia", "Oceano", "CienciaDeLaTierra", "SeguridadCostera"],
    "alerta": ["AlertaSismica", "AlertaTemprana", "DeteccionSismica", "Sismometros", "OndasSismicas", "PrevencionSismica", "Sismologia", "TecnologiaSismica"],
    "falla": ["FallaGeologica", "FallasTectonicas", "Geologia", "FracturaDeRocas", "TectonicaDePlacas", "Sismologia", "CienciaDeLaTierra", "MovimientoTectonico"],
    "prediccion": ["PrediccionSismica", "ProbabilidadSismica", "MitosSismicos", "CienciaSismica", "Sismologia", "RiesgoSismico", "Terremotos", "Geofisica"],
    "sismografo": ["Sismometro", "Sismografo", "RegistroSismico", "InstrumentosSismicos", "Sismologia", "Geofisica", "OndasSismicas", "TecnologiaSismica"],
    "terremoto": ["OndasSismicas", "DistanciaEpicentral", "PropagacionSismica", "MovimientoDelSuelo", "Sismologia", "Geofisica", "Terremotos", "CienciaDeLaTierra"],
}


def episode_hashtags(ep):
    key = norm(ep.get("id", ""))

    tags = [
        "Sismonauta",
        "Sismos",
        "EducacionSismica",
        "Terremotos",
    ]

    tags += TOPIC_HASHTAGS.get(
        key,
        [
            "Sismologia",
            "Geofisica",
            "CienciaDeLaTierra",
            "Geologia",
            "PrevencionSismica",
            "CulturaSismica",
            "OndasSismicas",
            "RiesgoSismico",
        ]
    )

    unique = []
    seen = set()

    for tag in tags:
        if tag.lower() not in seen:
            unique.append("#" + tag)
            seen.add(tag.lower())

    return " ".join(unique[:14])


def episode_caption_base(ep):
    key = norm(ep.get("id", ""))

    title = str(ep.get("title", "")).strip()
    fact = str(ep.get("fact", "")).strip()
    lesson = str(ep.get("lesson", "")).strip()

    close = MEMORABLE_CLOSINGS.get(
        key,
        lesson
    )

    if not all((title, fact, lesson, close)):
        raise RuntimeError(
            "Falta información editorial para la descripción"
        )

    return (
        f"{title}\n\n"
        f"{fact}\n\n"
        f"{lesson}\n\n"
        f"{close}\n\n"
        "¿Qué opinas? Te leemos en los comentarios."
    )


# ============================================================
# 6. IA OPCIONAL
# ============================================================

def ai_json(prompt):
    problems = []

    key = os.getenv(
        "GEMINI_API_KEY", ""
    ).strip()

    raw = None

    if key:
        configured = os.getenv(
            "GEMINI_MODEL", ""
        ).strip().removeprefix("models/")

        candidates = [configured] if configured else []

        try:
            listing = HTTP.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={
                    "x-goog-api-key": key
                },
                params={
                    "pageSize": 100
                },
                timeout=25
            )

            listing.raise_for_status()

            available = []

            for model in listing.json().get("models", []):
                name = str(
                    model.get("name", "")
                ).removeprefix("models/")

                methods = (
                    model.get("supportedGenerationMethods")
                    or []
                )

                if (
                    name.startswith("gemini-")
                    and "generateContent" in methods
                    and not any(
                        x in name
                        for x in (
                            "image",
                            "audio",
                            "tts",
                            "embedding",
                            "live",
                        )
                    )
                ):
                    available.append(name)

            available.sort(
                key=lambda name: (
                    0 if "flash-lite" in name
                    else 1 if "flash" in name
                    else 2,
                    "preview" in name,
                    name,
                )
            )

            candidates.extend(available[:6])

        except Exception as exc:
            problems.append(
                "Listado Gemini: " + str(exc)[:110]
            )

        for model in dict.fromkeys(candidates):
            try:
                url = (
                    "https://generativelanguage.googleapis.com/"
                    "v1beta/models/"
                    + urllib.parse.quote(model, safe="")
                    + ":generateContent"
                )

                response = HTTP.post(
                    url,
                    headers={
                        "x-goog-api-key": key
                    },
                    json={
                        "contents": [
                            {
                                "parts": [
                                    {"text": prompt}
                                ]
                            }
                        ],
                        "generationConfig": {
                            "temperature": 0.35,
                            "maxOutputTokens": 1600,
                            "responseMimeType": "application/json",
                        },
                    },
                    timeout=90
                )

                if response.status_code in (
                    400, 404, 429, 503
                ):
                    problems.append(
                        f"Gemini {model}: HTTP {response.status_code}"
                    )
                    continue

                response.raise_for_status()

                raw = "".join(
                    str(p.get("text", ""))
                    for p in (
                        response.json()
                        .get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts") or []
                    )
                )

                if not raw.strip():
                    raise ValueError(
                        "Respuesta sin texto"
                    )

                break

            except Exception as exc:
                problems.append(
                    f"Gemini {model}: {str(exc)[:100]}"
                )

    if raw is None:
        key = os.getenv(
            "OPENROUTER_API_KEY", ""
        ).strip()

        if not key:
            raise RuntimeError(
                "Sin modelo accesible. "
                + "; ".join(problems[-5:])
            )

        model = os.getenv(
            "OPENROUTER_MODEL",
            "openrouter/free"
        )

        if (
            model != "openrouter/free"
            and not model.endswith(":free")
        ):
            raise RuntimeError(
                "Solo se permite OpenRouter gratuito"
            )

        response = HTTP.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + key
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.35,
                "max_tokens": 1400,
            },
            timeout=110
        )

        response.raise_for_status()

        raw = response.json()[
            "choices"
        ][0]["message"]["content"]

    if not isinstance(raw, str):
        raise ValueError(
            "Respuesta IA no textual"
        )

    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        raw.strip(),
        flags=re.I
    )

    match = re.search(
        r"\{[\s\S]*\}",
        raw
    )

    if not match:
        raise ValueError(
            "La IA no devolvió JSON"
        )

    return json.loads(
        match.group()
    )


def generate_angle(topic, history, use_ai=True):
    styles = list(STYLES)

    angles = [
        "¿Sabías esto?",
        "La explicación científica",
        "Lo que muestran los datos",
        "La diferencia importante",
        "La pregunta que importa",
        "Un detalle sorprendente",
        "Lo que no debemos confundir",
        "Lo esencial en segundos",
    ]

    used = {
        (
            item.get("topic"),
            item.get("style"),
            item.get("angle"),
        )
        for item in history.get("items", [])
    }

    combos = [
        (style, angle)
        for style in styles
        for angle in angles
        if (
            topic["id"],
            style,
            angle,
        ) not in used
    ]

    if not combos:
        if not (
            use_ai
            and (
                os.getenv("GEMINI_API_KEY")
                or os.getenv("OPENROUTER_API_KEY")
            )
        ):
            raise RuntimeError(
                "Se agotaron los enfoques locales "
                "para el tema"
            )

        existing = [
            x.get("angle", "")
            for x in history.get("items", [])
            if x.get("topic") == topic["id"]
        ][-80:]

        proposal = ai_json(
            'Devuelve JSON {"angle":"...","style":"..."}. '
            "Tema: " + topic["title"]
            + ". Hecho documentado: "
            + topic["fact"]
            + ". Evita repetir: "
            + json.dumps(existing, ensure_ascii=False)
            + ". Introduce solo un enfoque editorial nuevo, "
            "sin hechos adicionales. Estilos: "
            + ", ".join(STYLES)
        )

        style = str(
            proposal.get("style", "descubrimiento")
        )

        if style not in STYLES:
            style = "descubrimiento"

        angle = str(
            proposal.get("angle", "")
        ).strip()[:100]

        if (
            not angle
            or norm(angle) in {
                norm(x) for x in existing
            }
        ):
            raise RuntimeError(
                "La IA no encontró un enfoque nuevo"
            )

    else:
        style, angle = random.SystemRandom().choice(
            combos
        )

    script = " ".join([
        topic["title"],
        topic["fact"],
        topic["lesson"],
    ])

    visuals = [
        topic["title"],
        topic["fact"],
        topic["lesson"],
    ]

    if (
        use_ai
        and (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
        )
    ):
        prompt = (
            "Eres guionista científico de SISMONAUTA, "
            "cuenta @sismonauta. "
            f"Tema: {topic['title']}. "
            f"HECHO PERMITIDO: {topic['fact']} "
            f"CONCLUSIÓN: {topic['lesson']}. "
            f"Enfoque: {angle}. "
            f"Estructura: {style}. "
            'Devuelve JSON con "script" (40-58 palabras) '
            'y "visuals" (seis mensajes de 3-10 palabras). '
            "No añadas fechas, cifras, causalidades, "
            "ejemplos concretos, alertas, predicciones "
            "ni hechos nuevos."
        )

        try:
            result = ai_json(prompt)

            candidate = str(
                result.get("script", "")
            ).strip()

            v = result.get("visuals")

            if (
                40 <= len(candidate.split()) <= 58
                and isinstance(v, list)
                and len(v) == 6
                and all(
                    isinstance(z, str)
                    and 3 <= len(z.split()) <= 10
                    and len(z) <= 100
                    for z in v
                )
            ):
                script = candidate
                visuals = v

        except Exception as exc:
            print(
                "IA no disponible; usando hechos documentados:",
                str(exc)[:140],
                flush=True
            )

    episode_id = (
        "sismos-"
        + hashlib.sha1(
            (
                topic["id"]
                + style
                + angle
            ).encode()
        ).hexdigest()[:12]
    )

    return dict(
        topic,
        style=style,
        angle=angle,
        script=script,
        visuals=visuals,
        episode_id=episode_id,
        created_at=utcnow(),
        verified=False,
        review_note=(
            "Revisar guion frente a la fuente "
            "antes de publicar"
        ),
    )


# ============================================================
# 7. STORYBOARD — VOZ Y RÓTULO POR ESCENA
# ============================================================

def build_storyboard(ep):
    key = str(
        ep.get("id", "")
    )

    beats = RETENTION_STORIES.get(
        key
    )

    if not beats:
        raise RuntimeError(
            "No existe guion narrativo para " + key
        )

    stages = (
        "pregunta",
        "intriga",
        "revelacion",
        "explicacion",
        "cierre",
    )

    queries = list(
        ep.get("queries")
        or ["earthquake fault"]
    )

    storyboard = []

    for i, ((narration, overlay), stage) in enumerate(
        zip(beats, stages)
    ):
        overlay = overlay.strip()

        if (
            not narration.strip()
            or not overlay
            or len(overlay.split()) > 12
            or "..." in overlay
            or "…" in overlay
        ):
            raise ValueError(
                "Escena editorial incompleta: " + key
            )

        storyboard.append({
            "narration": narration,
            "overlay": overlay,
            "query": queries[
                min(i, len(queries) - 1)
            ],
            "stage": stage,
        })

    storyboard.append({
        "narration": (
            "Dale me gusta y comparte, para seguir "
            "recibiendo más información sobre este "
            "fascinante tema. Muchas gracias!"
        ),
        "overlay": "DALE LIKE",
        "query": queries[-1],
        "stage": "cta",
    })

    return storyboard


# ============================================================
# 8. BÚSQUEDA DE FOTOGRAFÍAS
# ============================================================

PHOTO_TERMS = {
    "ruptura": ("surface rupture", "fault scarp", "ground rupture", "earthquake fissure"),
    "deslizamientos": ("earthquake landslide", "earthquake triggered landslide", "landslide"),
    "grietas": ("ground fissure", "surface rupture", "earthquake fissure", "fault scarp"),
    "transformante": ("san andreas fault", "transform fault", "fault scarp"),
    "rift": ("rift valley", "east african rift", "rift escarpment"),
    "subduccion": ("subduction", "tectonic coast", "coastal mountain"),
    "colision": ("himalaya", "folded rock", "mountain rock", "folded geological", "folded strata"),
    "falla_inversa": ("thrust fault", "reverse fault", "fault scarp", "overthrust"),
    "falla_normal": ("normal fault", "rift valley", "fault scarp"),
    "tsunami_oleadas": ("tsunami", "maremoto"),
    "tsunami_retroceso": ("tsunami", "maremoto"),
    "tsunami_rios": ("tsunami", "maremoto"),
    "tsunami_inundacion": ("tsunami", "maremoto"),
    "ondas": ("san andreas fault", "geological fault", "rift valley"),
    "magnitud": ("earthquake surface rupture", "earthquake landslide"),
    "replicas": ("earthquake", "fault scarp", "landslide"),
    "valdivia": ("1960 chile earthquake", "1960 valdivia", "1960 chile tsunami"),
    "profundidad": ("subduction", "tectonic fault", "fault scarp"),
    "placas": ("san andreas fault", "tectonic fault", "rift valley", "fault scarp"),
    "tsunamis": ("tsunami", "maremoto"),
    "alerta": ("earthquake surface rupture", "san andreas fault"),
    "falla": ("san andreas fault", "geological fault", "fault scarp", "surface rupture"),
    "prediccion": ("san andreas fault", "fault scarp"),
    "sismografo": ("san andreas fault", "surface rupture"),
    "terremoto": ("earthquake", "fault scarp", "landslide"),
}

PHOTO_SEARCH_QUERIES = {
    "ruptura": ["earthquake surface rupture", "fault scarp"],
    "deslizamientos": ["earthquake landslide", "earthquake triggered landslide"],
    "grietas": ["earthquake ground fissure", "earthquake surface rupture"],
    "transformante": ["San Andreas Fault", "transform fault landscape"],
    "rift": ["East African Rift valley", "rift valley landscape"],
    "subduccion": ["subduction zone coast", "tectonic coastal mountains", "Andes subduction coast"],
    "colision": ["Himalaya tectonic mountains", "folded geological mountain rocks", "folded rock outcrop"],
    "falla_inversa": ["thrust fault scarp", "reverse fault rock outcrop", "thrust fault geology landscape", "Lewis overthrust fault"],
    "falla_normal": ["normal fault scarp", "rift valley fault scarp"],
    "tsunami_oleadas": ["tsunami inundation coast", "tsunami shoreline"],
    "tsunami_retroceso": ["tsunami receding shoreline", "tsunami exposed seafloor coast"],
    "tsunami_rios": ["tsunami river estuary", "tsunami flooded coast"],
    "tsunami_inundacion": ["tsunami coastal inundation", "tsunami flood shoreline"],
    "ondas": ["San Andreas Fault", "fault scarp"],
    "magnitud": ["earthquake landslide", "earthquake surface rupture"],
    "replicas": ["earthquake landslide", "fault scarp"],
    "valdivia": ["1960 Valdivia earthquake coast", "1960 Chile tsunami"],
    "profundidad": ["subduction coast", "tectonic fault landscape"],
    "placas": ["San Andreas Fault", "rift valley"],
    "tsunamis": ["tsunami coast", "tsunami shoreline", "tsunami coastal erosion", "tsunami inundation"],
    "alerta": ["earthquake surface rupture", "San Andreas Fault"],
    "falla": ["San Andreas Fault", "fault scarp", "surface rupture"],
    "prediccion": ["San Andreas Fault", "fault scarp"],
    "sismografo": ["San Andreas Fault", "surface rupture"],
    "terremoto": ["earthquake landslide", "earthquake surface rupture"],
}

LANDSCAPE_AUTO_TOPICS = frozenset((
    "falla",
    "placas",
    "tsunamis",
    "terremoto",
    "replicas",
    "ruptura",
    "deslizamientos",
    "grietas",
    "transformante",
    "rift",
    "subduccion",
    "colision",
    "falla_inversa",
    "falla_normal",
    "tsunami_oleadas",
    "tsunami_retroceso",
    "tsunami_rios",
    "tsunami_inundacion",
))

FORBIDDEN_VISUAL_TERMS = (
    "person", "people", "portrait", "selfie",
    "man ", "woman ", "child", "children",
    "crowd", "tourist", "hiker", "hiking",
    "visitor", "rescue", "rescuer", "firefighter",
    "soldier", "police", "protester", "human",
    "family", "group photo", "group of",
    "persona", "personas", "gente", "retrato",
    "turista", "excursionista", "rescate",
    "building", "buildings", "house", "houses",
    "city", "cities", "urban", "street",
    "road", "bridge", "car ", "vehicle", "train",
    "construction", "infrastructure",
    "edificio", "edificios", "casa ",
    "ciudad", "calle", "puente", "vehiculo",
    "seismometer", "seismograph", "sensor",
    "instrument", "equipment", "station",
    "sismometro", "sismografo",
    "laboratory", "laboratorio",
    "museum", "exhibit", "memorial", "monument",
    "sign", "warning", "poster",
    "lroc", "lunar", "moon", "moon surface",
    "mars", "martian", "apollo mission",
    "planetary", "orbiter", "spacecraft",
    "satellite image of moon",
)

LANDSCAPE_EVIDENCE = (
    "fault", "rupture", "fissure", "rift",
    "scarp", "landslide", "slip",
    "subsidence", "coast", "coastal",
    "shoreline", "shore", "cliff",
    "terrain", "landscape", "valley",
    "mountain", "hillside", "rock",
    "outcrop", "geological", "tectonic",
    "tsunami", "erosion", "trench",
    "falla", "fractura", "deslizamiento",
    "costa", "acantilado", "terreno",
    "paisaje", "cordillera", "montana",
    "ladera", "roca", "estratos",
)

NON_PHOTO_TERMS = (
    "diagram", "diagrama", "infographic",
    "infografia", "flowchart", "flow chart",
    "schematic", "schema", "esquema",
    "cross section", "cross-section", "cutaway",
    "seismogram", "waveform", "spectrogram",
    "graph", "graphic", "chart", "plot",
    "hazard map", "seismic map", "mapa",
    "cartography", "map ",
    "illustration", "drawing", "dibujo",
    "cartoon", "vector", "icon", "logo",
    "poster", "banner", "slide",
    "presentation", "powerpoint",
    "screenshot", "screen capture",
    "screengrab", "table ", "figure ",
    "fig.", "pdf", "paper", "journal",
    "proceedings", "research report",
    "technical report", "book cover",
    "newspaper", "document", "scanned",
    "scan of", "textbook", "certificate",
    "brochure", "leaflet", "manual",
    "report of", "guide for",
    "advertisement", "stock photo",
    "watermark", "labeled", "labelled",
    "sonar", "buried objects",
    "target classification",
)


def contains_term(text, term):
    term = norm(
        str(term).strip()
    )

    return bool(
        re.search(
            r"(?<![a-z])"
            + re.escape(term)
            + r"(?![a-z])",
            text,
        )
    )


def photo_relevant(item, ep):
    """
    Filtrado de metadatos.

    Acepta evidencia en título o descripción
    sustantiva. No acepta fotografías genéricas
    solo por etiquetas de búsqueda.

    Sigue siendo un filtro automático, no
    una comprobación visual infalible.
    """

    title = norm(
        re.sub(
            r"^file:",
            "",
            str(
                item.get("title", "") or ""
            ).strip(),
            flags=re.I,
        )
    )

    desc = norm(
        str(
            item.get("description", "") or ""
        )
    )

    tags = norm(
        str(
            item.get("tags", "") or ""
        )
    )

    if not title:
        return False

    if re.search(
        r"\.(?:pdf|djvu|tiff?|svg|gif)(?:$|[?# ])",
        title,
    ):
        return False

    title_clean = re.sub(
        r"[_-]+",
        " ",
        title
    )

    combined = (
        title_clean + " " + desc + " " + tags
    )

    # Excluir documentos y esquemas.
    if any(
        contains_term(title_clean, term)
        for term in NON_PHOTO_TERMS
    ):
        return False

    # Excluir personas, ciudades, equipos
    # y contenido expresamente no permitido.
    if any(
        contains_term(title_clean, term)
        for term in FORBIDDEN_VISUAL_TERMS
    ):
        return False

    forbidden_desc = (
        "people", "person", "portrait",
        "crowd", "tourist",
        "rescue workers", "buildings",
        "houses", "city street",
        "evacuees", "victims",
    )

    if any(
        contains_term(desc, term)
        for term in forbidden_desc
    ):
        return False

    topic = ep.get("id", "")

    terms = tuple(
        norm(t)
        for t in PHOTO_TERMS.get(
            topic, ()
        )
    )

    if not terms:
        return False

    title_match = any(
        contains_term(title_clean, term)
        for term in terms
    )

    desc_match = (
        len(desc) >= 30
        and any(
            contains_term(desc, term)
            for term in terms
        )
    )

    if not (
        title_match or desc_match
    ):
        return False

    landscape = any(
        contains_term(title_clean, term)
        or contains_term(desc, term)
        for term in LANDSCAPE_EVIDENCE
    )

    if not landscape:
        return False

    tsunami_topics = {
        "tsunamis",
        "tsunami_oleadas",
        "tsunami_retroceso",
        "tsunami_rios",
        "tsunami_inundacion",
    }

    if topic in tsunami_topics:
        # No aceptar paisajes costeros
        # sin evidencia específica de tsunami.
        if not any(
            contains_term(
                title_clean + " " + desc,
                term
            )
            for term in ("tsunami", "maremoto")
        ):
            return False

        coastal_terms = (
            "coast", "coastal", "shore",
            "shoreline", "beach", "sea",
            "ocean", "bay", "inundation",
            "flood", "erosion", "wave",
            "river", "estuary", "water",
            "costa", "playa", "inundacion",
            "rio", "estuario",
        )

        if not any(
            contains_term(combined, term)
            for term in coastal_terms
        ):
            return False

    if topic == "valdivia":
        # No representar el evento de 1960
        # mediante una foto moderna.
        if not (
            "1960" in title_clean
            and any(
                x in title_clean
                for x in ("chile", "valdivia")
            )
        ):
            return False

    return True


# ============================================================
# 9. AUDITORÍA VISUAL
# ============================================================

def visual_photo_audit(path):
    """
    Verificación automática conservadora.

    Evalúa resolución, predominio de blanco,
    densidad de bordes y texto con OCR opcional.

    No pretende sustituir una inspección humana.
    """

    try:
        with Image.open(path) as src:
            rgb = ImageOps.exif_transpose(
                src
            ).convert("RGB")

            if (
                rgb.width < 500
                or rgb.height < 350
            ):
                return False, "resolución insuficiente"

            sample = ImageOps.contain(
                rgb,
                (480, 480)
            )

            pixels = list(
                sample.resize(
                    (160, 160)
                ).getdata()
            )

            white = sum(
                1
                for r, g, b in pixels
                if min(r, g, b) > 224
                and max(r, g, b) - min(r, g, b) < 20
            ) / len(pixels)

            # Menos restrictivo que antes,
            # pero mantiene el rechazo de páginas.
            if white > 0.72:
                return (
                    False,
                    "fondo documental predominantemente blanco",
                )

            gray = sample.convert("L")

            edges = gray.filter(
                ImageFilter.FIND_EDGES
            )

            avg_edge = ImageStat.Stat(
                edges
            ).mean[0]

            if avg_edge > 68:
                return (
                    False,
                    "densidad extrema de bordes"
                )

            if shutil.which("tesseract"):
                try:
                    import csv

                    result = subprocess.run(
                        [
                            "tesseract",
                            str(path),
                            "stdout",
                            "--psm",
                            "11",
                            "tsv",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=18,
                    )

                    if result.returncode == 0:
                        rows = list(
                            csv.DictReader(
                                io.StringIO(result.stdout),
                                delimiter="\t"
                            )
                        )

                        confident = []

                        for row in rows:
                            word = (
                                row.get("text") or ""
                            ).strip()

                            try:
                                confidence = float(
                                    row.get("conf") or "-1"
                                )
                            except ValueError:
                                confidence = -1

                            if (
                                confidence >= 75
                                and re.fullmatch(
                                    r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{3,}",
                                    word,
                                )
                            ):
                                confident.append(word)

                        if len(confident) >= 3:
                            return (
                                False,
                                "texto visible detectado por OCR",
                            )

                except (
                    OSError,
                    subprocess.TimeoutExpired
                ):
                    pass

            else:
                if white > 0.35 and avg_edge > 30:
                    return (
                        False,
                        "posible documento sin OCR",
                    )

            return (
                True,
                "imagen apta por inspección automática",
            )

    except Exception as exc:
        return (
            False,
            "imagen ilegible: " + str(exc)[:80],
        )


# ============================================================
# 10. LICENCIAS
# ============================================================

def license_accepted(code):
    """
    Solo CC0 y dominio público.

    Se mantienen las condiciones originales
    del sistema editorial SISMONAUTA.
    """

    code = norm(
        str(code)
    ).strip().lower().replace("_", "-")

    compact = re.sub(
        r"[^a-z0-9]+",
        "",
        code
    )

    return compact.startswith((
        "cc0",
        "publicdomain",
        "publicdomainmark",
        "pdm",
        "pdold",
        "pdus",
        "pdself",
    ))


# ============================================================
# 11. WIKIMEDIA COMMONS
# ============================================================

def commons(query):
    """
    Fuente principal.

    Comprueba licencia y obtiene imágenes
    originales o miniaturas de Commons.
    """

    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 40,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size",
        "iiurlwidth": 1400,
    }

    response = HTTP.get(
        "https://commons.wikimedia.org/w/api.php",
        params=params,
        timeout=COMMONS_TIMEOUT,
    )

    response.raise_for_status()

    pages = (
        response.json()
        .get("query", {})
        .get("pages", {})
    )

    for page in pages.values():
        info = (
            page.get("imageinfo")
            or [{}]
        )[0]

        meta = (
            info.get("extmetadata")
            or {}
        )

        def val(key):
            raw = str(
                (
                    meta.get(key)
                    or {}
                ).get("value", "")
            )

            return re.sub(
                r"<[^>]+>",
                " ",
                raw
            )

        license_code = val(
            "LicenseShortName"
        ).strip()

        if not license_accepted(license_code):
            continue

        url = (
            info.get("thumburl")
            or info.get("url")
        )

        title = page.get(
            "title", ""
        )

        if not re.search(
            r"\.(jpg|jpeg|png|webp)$",
            title,
            re.I
        ):
            continue

        if not url:
            continue

        yield {
            "url": url,
            "source": info.get(
                "descriptionurl", ""
            ),
            "license": license_code,
            "title": title,
            "description": val("ImageDescription"),
            "creator": (
                val("Artist")
                or "Autor no identificado"
            ),
            "tags": "",
            "provider": "Wikimedia",
        }


# ============================================================
# 12. OPENVERSE — REINTENTOS Y FALLBACK
# ============================================================

def openverse(query):
    """
    Openverse es fuente secundaria.

    Mejoras:
    - Timeout de lectura reducido.
    - Dos intentos como máximo.
    - Suspensión temporal ante fallos.
    - No bloquea los demás proveedores.
    """

    global _OPENVERSE_UNAVAILABLE_UNTIL

    if time.monotonic() < _OPENVERSE_UNAVAILABLE_UNTIL:
        print(
            "OPENVERSE: omitido temporalmente "
            "por fallos anteriores.",
            flush=True
        )
        return

    endpoint = (
        "https://api.openverse.org/v1/images/"
    )

    params = {
        "q": query,
        "license": "cc0,pdm",
        "category": "photograph",
        "page_size": 30,
    }

    payload = None

    for attempt in range(
        OPENVERSE_MAX_ATTEMPTS
    ):
        try:
            response = HTTP.get(
                endpoint,
                params=params,
                timeout=OPENVERSE_TIMEOUT,
            )

            if response.status_code in (
                429, 500, 502, 503, 504
            ):
                raise requests.HTTPError(
                    "Openverse HTTP "
                    + str(response.status_code)
                )

            response.raise_for_status()

            payload = response.json()
            break

        except (
            requests.Timeout,
            requests.ConnectionError,
            requests.HTTPError,
            ValueError,
        ) as exc:

            print(
                f"OPENVERSE intento "
                f"{attempt + 1}/{OPENVERSE_MAX_ATTEMPTS}: "
                f"{str(exc)[:130]}",
                flush=True
            )

            if attempt + 1 < OPENVERSE_MAX_ATTEMPTS:
                time.sleep(
                    2 * (attempt + 1)
                )

    if payload is None:
        _OPENVERSE_UNAVAILABLE_UNTIL = (
            time.monotonic()
            + OPENVERSE_BACKOFF
        )

        print(
            "OPENVERSE suspendido durante "
            "10 minutos. Se mantiene Wikimedia Commons.",
            flush=True
        )

        return

    for item in payload.get(
        "results", []
    ):
        license_code = str(
            item.get("license", "")
        ).lower()

        if not license_accepted(
            license_code
        ):
            continue

        if item.get("mature") is True:
            continue

        url = (
            item.get("url")
            or item.get("thumbnail")
        )

        if not url:
            continue

        tags = " ".join(
            str(tag.get("name", ""))
            for tag in (
                item.get("tags") or []
            )
            if isinstance(tag, dict)
        )

        yield {
            "url": url,
            "source": item.get(
                "foreign_landing_url", ""
            ),
            "license": license_code,
            "title": item.get("title", ""),
            "description": (
                item.get("description")
                or ""
            ),
            "tags": tags,
            "creator": (
                item.get("creator")
                or "Autor no identificado"
            ),
            "provider": "Openverse",
        }


# ============================================================
# 13. DESCARGA SEGURA DE FOTOGRAFÍAS
# ============================================================

def photo_download(item, dest):
    import ipaddress
    import socket

    from urllib.parse import urlparse

    url = item["url"]

    def valid_url(candidate):
        parsed = urlparse(candidate)

        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return False

        try:
            addresses = socket.getaddrinfo(
                parsed.hostname,
                443,
                type=socket.SOCK_STREAM,
            )

            if not addresses:
                return False

            return all(
                ipaddress.ip_address(
                    address[4][0]
                ).is_global
                for address in addresses
            )

        except Exception:
            return False

    if not valid_url(url):
        return False

    try:
        for _ in range(5):
            with HTTP.get(
                url,
                timeout=(8, 22),
                stream=True,
                allow_redirects=False,
            ) as response:

                if response.status_code in (
                    301, 302, 303, 307, 308
                ):
                    location = (
                        response.headers.get("Location", "")
                    )

                    url = urllib.parse.urljoin(
                        url,
                        location
                    )

                    if not valid_url(url):
                        return False

                    continue

                response.raise_for_status()

                content_type = (
                    response.headers
                    .get("Content-Type", "")
                    .lower()
                )

                if not any(
                    x in content_type
                    for x in (
                        "image/jpeg",
                        "image/png",
                        "image/webp",
                    )
                ):
                    return False

                data = bytearray()

                for chunk in response.iter_content(
                    65536
                ):
                    data.extend(chunk)

                    if len(data) > 12_000_000:
                        return False

                with Image.open(
                    io.BytesIO(data)
                ) as im:

                    im = ImageOps.exif_transpose(
                        im
                    )

                    if (
                        im.width < 500
                        or im.height < 350
                    ):
                        return False

                    im.convert("RGB").save(
                        dest,
                        quality=91
                    )

                return True

    except Exception as exc:
        print(
            "Imagen no aceptada:",
            str(exc)[:85],
            flush=True
        )

    return False


# ============================================================
# 14. SELECCIÓN DE FOTOGRAFÍAS
# ============================================================

def get_photos(ep, folder, local_dir=None):
    """
    Requiere al menos UNA foto real válida.

    Si encuentra una, el video puede continuar.
    Si encuentra varias, las distribuye entre escenas.

    No usa imágenes de relleno.
    """

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    found = []
    credits = []
    seen = set()

    counts = {
        "candidates": 0,
        "metadata_rejected": 0,
        "download_failed": 0,
        "visual_rejected": 0,
    }

    # ----------------------------------------
    # FOTOGRAFÍAS APORTADAS MANUALMENTE
    # ----------------------------------------

    if local_dir:
        directory = Path(local_dir)

        if not directory.is_dir():
            raise RuntimeError(
                "No existe --photo-dir: "
                + str(directory)
            )

        for file in sorted(
            directory.iterdir()
        ):
            if file.suffix.lower() not in (
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            ):
                continue

            try:
                with Image.open(file) as im:
                    if (
                        im.width >= 500
                        and im.height >= 350
                        and visual_photo_audit(file)[0]
                    ):
                        found.append(file)

                        credits.append({
                            "source": str(file),
                            "license": (
                                "Verificar autorización de uso"
                            ),
                        })

            except Exception:
                continue

        if not found:
            raise RuntimeError(
                "No se encontraron fotografías "
                "válidas en --photo-dir"
            )

        return found[:MAX_VALID_PHOTOS], credits[:MAX_VALID_PHOTOS]

    # ----------------------------------------
    # BÚSQUEDAS TEMÁTICAS
    # ----------------------------------------

    queries = PHOTO_SEARCH_QUERIES.get(
        ep.get("id")
    )

    if not queries:
        raise RuntimeError(
            "Tema sin estrategia visual específica"
        )

    print(
        "BUSQUEDA VISUAL TEMATICA:",
        ep["id"],
        "|",
        queries,
        flush=True
    )

    rejected_examples = []

    for query in queries:
        # Wikimedia primero, Openverse segundo.
        for provider in (
            commons,
            openverse,
        ):
            try:
                results = list(
                    provider(query)
                )

            except Exception as exc:
                print(
                    f"Fotos {provider.__name__} / "
                    f"{query}: {str(exc)[:110]}",
                    flush=True
                )
                continue

            for item in results:
                url = item.get("url", "")

                if url in seen:
                    continue

                seen.add(url)

                counts["candidates"] += 1

                if not photo_relevant(
                    item, ep
                ):
                    counts["metadata_rejected"] += 1

                    if len(rejected_examples) < 5:
                        rejected_examples.append(
                            str(
                                item.get("title", "")
                            )[:85]
                        )

                    continue

                path = (
                    folder
                    / f"foto_{len(found) + 1}.jpg"
                )

                if not photo_download(
                    item, path
                ):
                    counts["download_failed"] += 1
                    continue

                okay, reason = visual_photo_audit(
                    path
                )

                if not okay:
                    print(
                        "FOTO DESCARTADA:",
                        reason,
                        "|",
                        str(
                            item.get("title", "")
                        )[:95],
                        flush=True
                    )

                    path.unlink(
                        missing_ok=True
                    )

                    counts["visual_rejected"] += 1
                    continue

                found.append(path)

                credits.append({
                    key: item.get(key, "")
                    for key in (
                        "source",
                        "license",
                        "title",
                        "creator",
                        "provider",
                        "description",
                        "tags",
                    )
                })

                print(
                    "FOTO TEMATICA:",
                    ep["id"],
                    "|",
                    str(
                        item.get("title", "")
                    )[:85],
                    "|",
                    item.get("license"),
                    flush=True
                )

                if len(found) >= MAX_VALID_PHOTOS:
                    break

            if len(found) >= MAX_VALID_PHOTOS:
                break

        if len(found) >= MAX_VALID_PHOTOS:
            break

    if not found and rejected_examples:
        print(
            "EJEMPLOS DE TITULOS RECHAZADOS:",
            rejected_examples,
            flush=True
        )

    print(
        "AUDITORIA FOTOGRAFIAS:",
        counts,
        "aceptadas=",
        len(found),
        flush=True
    )

    if not found:
        raise RuntimeError(
            "No se encontraron fotografías auténticas "
            "del tema específico con licencia CC0/PD. "
            "No se publican imágenes de relleno."
        )

    if len(found) == 1:
        print(
            "MODO FOTO UNICA: una fotografía "
            "verificada disponible; se utilizarán "
            "distintos encuadres.",
            flush=True
        )

    else:
        print(
            f"FOTOGRAFIAS VERIFICADAS: {len(found)}",
            flush=True
        )

    return found, credits


# ============================================================
# 15. FUENTES Y COMPOSICIÓN VISUAL
# ============================================================

def font(size, bold=True):
    options = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )

    for path in options:
        if Path(path).exists():
            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


def wrap(draw, text, ft, width):
    lines = []
    line = ""

    for word in text.split():
        attempt = (
            line + " " + word
        ).strip()

        if draw.textbbox(
            (0, 0),
            attempt,
            font=ft
        )[2] <= width:
            line = attempt

        else:
            if line:
                lines.append(line)

            line = word

    if line:
        lines.append(line)

    return lines


# ============================================================
# 16. MEJORA: ENCUADRES DIFERENTES
# ============================================================

def photo_crop_variant(src, index):
    """
    Genera variaciones del encuadre original.

    No crea fotografías artificiales.
    No añade objetos ni altera el fenómeno.

    Se limita a ampliar y desplazar suavemente
    la fotografía real.
    """

    image = ImageOps.exif_transpose(
        src
    ).convert("RGB")

    # Variaciones de recorte para escenas
    # sucesivas que reutilizan la misma imagen.

    centerings = [
        (0.50, 0.50),
        (0.35, 0.45),
        (0.65, 0.55),
        (0.45, 0.35),
        (0.55, 0.65),
    ]

    zooms = [
        1.00,
        1.08,
        1.13,
        1.05,
        1.10,
    ]

    centering = centerings[
        index % len(centerings)
    ]

    zoom = zooms[
        index % len(zooms)
    ]

    fitted = ImageOps.fit(
        image,
        (
            int(W * zoom),
            int(H * zoom),
        ),
        method=Image.Resampling.LANCZOS,
        centering=centering,
    )

    if zoom == 1:
        return fitted

    excess_x = fitted.width - W
    excess_y = fitted.height - H

    cx, cy = centering

    left = int(
        excess_x * cx
    )

    top = int(
        excess_y * cy
    )

    return fitted.crop((
        left,
        top,
        left + W,
        top + H,
    ))


# ============================================================
# 17. ESCENA FOTOGRÁFICA
# ============================================================

def clean_scene(photo, message, dest, variant=0):
    """
    Fondo fotográfico y rótulo centrado.

    variant permite reutilizar una única foto
    con encuadres diferentes.
    """

    with Image.open(photo) as src:
        im = photo_crop_variant(
            src,
            variant
        )

    im = im.filter(
        ImageFilter.GaussianBlur(
            radius=6
        )
    )

    im = ImageEnhance.Color(
        im
    ).enhance(0.78)

    im = ImageEnhance.Contrast(
        im
    ).enhance(0.93)

    im = Image.blend(
        im,
        Image.new(
            "RGB",
            (W, H),
            (0, 0, 0)
        ),
        0.49,
    )

    veil = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0)
    )

    veil_draw = ImageDraw.Draw(
        veil
    )

    for y in range(H):
        strength = max(
            0.0,
            1.0 - abs(
                y - H * 0.5
            ) / (H * 0.43)
        )

        alpha = int(
            56 * strength * strength
        )

        veil_draw.line(
            (0, y, W, y),
            fill=(0, 0, 0, alpha),
        )

    im = Image.alpha_composite(
        im.convert("RGBA"),
        veil
    ).convert("RGB")

    draw = ImageDraw.Draw(
        im
    )

    message = re.sub(
        r"\s+",
        " ",
        str(message or "")
    )

    message = (
        message
        .replace("…", "")
        .replace("...", "")
        .strip()
    )

    if not message:
        raise ValueError(
            "Escena sin texto"
        )

    max_width = W - 104

    chosen = None

    for size in range(
        65,
        17,
        -1
    ):
        ft = font(size)

        lines = wrap(
            draw,
            message.upper(),
            ft,
            max_width
        )

        line_height = int(
            size * 1.27
        )

        if (
            not lines
            or len(lines) > 8
            or len(lines) * line_height > H * 0.63
        ):
            continue

        if any(
            draw.textbbox(
                (0, 0),
                line,
                font=ft,
                stroke_width=3
            )[2] > max_width
            for line in lines
        ):
            continue

        chosen = (
            ft,
            lines,
            line_height,
        )

        break

    if chosen is None:
        raise ValueError(
            "Escena imposible de componer: "
            + message[:90]
        )

    ft, lines, line_height = chosen

    y = (
        H - len(lines) * line_height
    ) // 2

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=ft,
            stroke_width=3,
        )

        x = (
            W - (bbox[2] - bbox[0])
        ) / 2

        draw.text(
            (x, y),
            line,
            font=ft,
            fill="white",
            stroke_width=3,
            stroke_fill="#09131C",
        )

        y += line_height

    im.save(
        dest,
        quality=92
    )


# ============================================================
# 18. ICONO DALE LIKE
# ============================================================

def draw_like_icon(
    draw,
    cx,
    cy,
    scale=1.0,
    fill=(255, 255, 255, 255),
):
    s = float(scale)

    alpha = (
        fill[3]
        if len(fill) == 4
        else 255
    )

    blue = (
        24, 119, 242, alpha
    )

    white = (
        255, 255, 255, alpha
    )

    radius = 132 * s

    draw.ellipse(
        (
            cx-radius,
            cy-radius,
            cx+radius,
            cy+radius,
        ),
        fill=blue,
    )

    def pts(items):
        return [
            (
                cx + x*s,
                cy + y*s,
            )
            for x, y in items
        ]

    draw.rounded_rectangle(
        (
            cx-88*s,
            cy-3*s,
            cx-54*s,
            cy+69*s,
        ),
        radius=8*s,
        fill=white,
    )

    palm = pts([
        (-46, 1), (-24, -14),
        (13, -14), (26, -23),
        (47, -20), (58, -11),
        (58, 1), (54, 13),
        (52, 21), (50, 34),
        (47, 42), (45, 55),
        (31, 68), (-45, 68),
    ])

    draw.polygon(
        palm,
        fill=white
    )

    thumb = pts([
        (-39, 4), (-20, -15),
        (-9, -38), (2, -74),
        (12, -93), (24, -96),
        (32, -88), (34, -75),
        (22, -38), (17, -21),
        (35, -21), (46, -16),
        (47, -7), (40, 3),
    ])

    draw.polygon(
        thumb,
        fill=white
    )

    draw.line(
        pts([
            (-52, 2),
            (-52, 67),
        ]),
        fill=blue,
        width=max(
            3,
            int(5*s)
        ),
    )


# ============================================================
# 19. ANIMACIÓN FINAL DALE LIKE
# ============================================================

def cta_scene_video(dest_video, duration):
    tmpdir = Path(
        tempfile.mkdtemp(
            prefix="cta_like_"
        )
    )

    try:
        total_frames = max(
            int(duration * FPS),
            1
        )

        blink_period = max(
            int(FPS * 0.45),
            6
        )

        bottom_text = "DALE LIKE"

        for n in range(total_frames):
            img = Image.new(
                "RGBA",
                (W, H),
                (0, 0, 0, 255)
            )

            visible = (
                (n // blink_period) % 2
            ) == 0

            pulse = (
                1.0 if visible else 0.88
            )

            alpha = (
                255 if visible else 82
            )

            halo = Image.new(
                "RGBA",
                (W, H),
                (0, 0, 0, 0)
            )

            halo_draw = ImageDraw.Draw(
                halo
            )

            for extra, opacity in (
                (54, 40),
                (26, 68),
            ):
                halo_draw.ellipse(
                    (
                        W/2-140-extra,
                        H/2-130-extra,
                        W/2+140+extra,
                        H/2+150+extra,
                    ),
                    fill=(
                        255,
                        255,
                        255,
                        opacity if visible else 0,
                    ),
                )

            img = Image.alpha_composite(
                img,
                halo
            )

            draw = ImageDraw.Draw(
                img
            )

            draw_like_icon(
                draw,
                W/2,
                H//2-40,
                scale=1.20*pulse,
                fill=(
                    255,
                    255,
                    255,
                    alpha
                )
            )

            ft = font(54)

            bbox = draw.textbbox(
                (0, 0),
                bottom_text,
                font=ft,
                stroke_width=3,
            )

            text_width = (
                bbox[2] - bbox[0]
            )

            x = (
                W - text_width
            ) / 2

            y = H - 160

            draw.text(
                (x, y),
                bottom_text,
                font=ft,
                fill=(
                    255, 255, 255, 255
                ),
                stroke_width=3,
                stroke_fill=(
                    9, 19, 28, 255
                ),
            )

            img.convert("RGB").save(
                tmpdir / f"frame_{n:04d}.png"
            )

        ffmpeg([
            "-framerate",
            str(FPS),
            "-i",
            tmpdir / "frame_%04d.png",
            "-t",
            str(duration),
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            dest_video,
        ])

    finally:
        shutil.rmtree(
            tmpdir,
            ignore_errors=True
        )


# ============================================================
# 20. MÚSICA ORIGINAL
# ============================================================

def soundtrack(path, secs=45, sr=22050):
    import array

    n = int(
        math.ceil(secs * sr)
    )

    buf = array.array("h")

    for i in range(n):
        t = i / sr

        fade = max(
            0.0,
            min(
                1.0,
                t / 1.3,
                (secs-t) / 2.0
            )
        )

        notes = (
            130.81,
            164.81,
            196.00,
            261.63,
        )

        note = notes[
            int(t*2) % 4
        ]

        pad = (
            0.36 * math.sin(
                2*math.pi*130.81*t
            )
            + 0.22 * math.sin(
                2*math.pi*196*t
            )
            + 0.13 * math.sin(
                2*math.pi*261.63*t
            )
        )

        pulse = (
            0.15
            * math.sin(
                2*math.pi*note*t
            )
            * (
                0.5
                + 0.5*math.cos(
                    2*math.pi*2*t
                )
            )
        )

        shimmer = (
            0.07
            * math.sin(
                2*math.pi*392*t
            )
            * (
                0.5
                + 0.5*math.sin(
                    2*math.pi*t/8
                )
            )
        )

        sample = max(
            -1.0,
            min(
                1.0,
                (
                    pad
                    + pulse
                    + shimmer
                ) * 0.55 * fade
            )
        )

        buf.append(
            int(
                32767 * sample
            )
        )

    with wave.open(
        str(path),
        "wb"
    ) as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(
            buf.tobytes()
        )


# ============================================================
# 21. VOZ NEURAL Y FFMPEG
# ============================================================

async def speak(text, out):
    import edge_tts

    await edge_tts.Communicate(
        text,
        voice=VOICE,
        rate="+8%",
    ).save(str(out))

    if (
        not out.exists()
        or out.stat().st_size < 1000
    ):
        raise RuntimeError(
            "Voz no generada"
        )


def ffmpeg(cmd):
    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *map(str, cmd),
        ],
        capture_output=True,
        text=True,
    )

    if process.returncode:
        raise RuntimeError(
            "FFmpeg: "
            + process.stderr[-1400:]
        )


# ============================================================
# 22. RENDERIZAR VIDEO
# ============================================================

def render(
    ep,
    out,
    photo_dir=None,
    voice=True
):
    """
    Produce el Reel completo.

    Con una fotografía válida continúa.
    Cada escena puede utilizar un encuadre
    distinto de la misma imagen.
    """

    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "FFmpeg no instalado"
        )

    folder = (
        out / ep["episode_id"]
    )

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    storyboard = build_storyboard(
        ep
    )

    if len(storyboard) < 4:
        raise RuntimeError(
            "Historia insuficiente"
        )

    ep["storyboard"] = storyboard

    ep["script"] = " ".join(
        beat["narration"]
        for beat in storyboard
    )

    ep["visuals"] = [
        beat["overlay"]
        for beat in storyboard
    ]

    write_json_atomic(
        folder / "storyboard.json",
        storyboard
    )

    # ----------------------------------------
    # FOTOGRAFÍAS
    # ----------------------------------------

    photos, credits = get_photos(
        ep,
        folder,
        photo_dir
    )

    if not local_photo_audit(
        photos,
        credits,
        ep,
        photo_dir
    ):
        raise RuntimeError(
            "Fotografías sin pertinencia "
            "sísmica suficiente"
        )

    # MEJORA PRINCIPAL:
    # No se requieren dos imágenes.

    if len(photos) < MIN_VALID_PHOTOS:
        raise RuntimeError(
            "No hay fotografías auténticas "
            "para generar el video"
        )

    if len(photos) == 1:
        print(
            "RENDER CON FOTO UNICA: "
            "se aplicarán encuadres diferentes.",
            flush=True
        )

    # ----------------------------------------
    # VOZ POR ESCENA
    # ----------------------------------------

    durations = []
    voices = []

    for i, beat in enumerate(storyboard):
        if voice:
            clip = (
                folder
                / f"voz_escena_{i:02d}.mp3"
            )

            try:
                asyncio.run(
                    speak(
                        beat["narration"],
                        clip
                    )
                )

                from mutagen.mp3 import MP3

                seconds = float(
                    MP3(str(clip)).info.length
                )

                voices.append(clip)

            except Exception as exc:
                raise RuntimeError(
                    f"Fallo voz escena {i+1}: {exc}"
                ) from exc

        else:
            seconds = 0.0

        words = len(
            beat["overlay"].split()
        )

        stage = beat.get(
            "stage", ""
        )

        if stage == "pregunta":
            min_duration = 3.6
            read_rate = 2.7

        elif stage == "intriga":
            min_duration = 3.0
            read_rate = 2.6

        elif stage == "cta":
            min_duration = 5.4
            read_rate = 2.3

        else:
            min_duration = 4.7
            read_rate = 2.15

        duration = max(
            seconds + 0.55,
            min_duration,
            words / read_rate + 0.7,
        )

        durations.append(
            round(duration, 2)
        )

    total = round(
        sum(durations),
        2
    )

    print(
        "SINCRONIA escenas:",
        durations,
        "duracion total:",
        total,
        flush=True
    )

    # ----------------------------------------
    # CREAR ESCENAS
    # ----------------------------------------

    media_items = []
    cover = None
    photo_index = 0

    for i, beat in enumerate(storyboard):
        stage = beat.get(
            "stage", ""
        )

        if stage == "cta":
            clip_path = (
                folder
                / f"escena_{i:02d}.mp4"
            )

            cta_scene_video(
                clip_path,
                durations[i]
            )

            media_items.append({
                "kind": "video",
                "path": clip_path,
                "duration": durations[i],
            })

        else:
            frame = (
                folder
                / f"escena_{i:02d}.jpg"
            )

            photo = photos[
                min(
                    photo_index,
                    len(photos)-1
                )
            ]

            clean_scene(
                photo,
                beat["overlay"],
                frame,
                variant=i,
            )

            media_items.append({
                "kind": "image",
                "path": frame,
                "duration": durations[i],
            })

            if cover is None:
                cover = (
                    out
                    / (
                        ep["episode_id"]
                        + "_portada.jpg"
                    )
                )

                shutil.copy2(
                    frame,
                    cover
                )

            print(
                "ESCENA-FOTO:",
                i+1,
                "|",
                stage,
                "|",
                photo.name,
                "|",
                f"encuadre {i+1}",
                flush=True
            )

            photo_index += 1

    if cover is None:
        raise RuntimeError(
            "No se pudo crear portada"
        )

    # ----------------------------------------
    # MÚSICA ORIGINAL
    # ----------------------------------------

    music = (
        folder / "musica.wav"
    )

    soundtrack(
        music,
        secs=total+1
    )

    # ----------------------------------------
    # PREPARAR INPUTS FFMPEG
    # ----------------------------------------

    inputs = []

    for item in media_items:
        if item["kind"] == "image":
            inputs += [
                "-loop",
                "1",
                "-framerate",
                str(FPS),
                "-t",
                str(item["duration"]),
                "-i",
                item["path"],
            ]

        else:
            inputs += [
                "-i",
                item["path"],
            ]

    media_count = len(
        media_items
    )

    inputs += [
        "-i",
        music,
    ]

    voice_start = (
        media_count + 1
    )

    for v in voices:
        inputs += [
            "-i",
            v,
        ]

    filters = []

    for i, item in enumerate(media_items):
        filters.append(
            f"[{i}:v]"
            f"scale={W}:{H},"
            "setsar=1,"
            f"trim=duration={item['duration']},"
            "setpts=PTS-STARTPTS,"
            f"format=yuv420p[v{i}]"
        )

    filters.append(
        "".join(
            f"[v{i}]"
            for i in range(
                media_count
            )
        )
        + f"concat=n={media_count}:v=1:a=0[v]"
    )

    if voice:
        for i, duration in enumerate(durations):
            filters.append(
                f"[{voice_start+i}:a]"
                "aresample=44100,"
                "apad,"
                f"atrim=duration={duration},"
                "asetpts=PTS-STARTPTS"
                f"[a{i}]"
            )

        filters.append(
            "".join(
                f"[a{i}]"
                for i in range(
                    len(voices)
                )
            )
            + f"concat=n={len(voices)}:v=0:a=1[narr]"
        )

        filters.append(
            f"[{media_count}:a]"
            "volume=0.30[mus];"
            "[narr]volume=1.0[nar];"
            "[mus][nar]"
            "amix=inputs=2:"
            "duration=first:"
            "dropout_transition=0:"
            "normalize=0,"
            "alimiter=limit=0.94[a]"
        )

    else:
        filters.append(
            f"[{media_count}:a]"
            "volume=0.65,"
            "alimiter=limit=0.94[a]"
        )

    # ----------------------------------------
    # RENDER FINAL
    # ----------------------------------------

    movie = (
        out
        / (
            ep["episode_id"]
            + ".mp4"
        )
    )

    ffmpeg([
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        str(total),
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-movflags",
        "+faststart",
        movie,
    ])

    write_json_atomic(
        folder / "creditos.json",
        credits
    )

    write_json_atomic(
        folder / "duraciones_escenas.json",
        {
            "segundos": durations,
            "total": total,
            "sincronizacion": (
                "voz separada por escena"
            ),
            "musica": (
                "instrumental_original"
            ),
            "fotografias_distintas": len(photos),
            "encuadres": len(storyboard)-1,
        }
    )

    print(
        "VIDEO:",
        movie,
        "PORTADA:",
        cover,
        "VOZ sincronizada:",
        voice,
        flush=True
    )

    return movie, cover, credits


def local_photo_audit(
    photos,
    credits,
    ep,
    photo_dir
):
    if photo_dir:
        print(
            "AVISO: fotografías aportadas manualmente; "
            "revisar pertinencia y licencias.",
            flush=True
        )

        return True

    return all(
        photo_relevant(c, ep)
        and license_accepted(
            c.get("license", "")
        )
        for c in credits
    )


# ============================================================
# 23. GITHUB — ESTADO PERSISTENTE
# ============================================================

class Github:

    def __init__(self):
        self.repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.token = os.environ[
            "GH_TOKEN"
        ]

        self.branch = os.getenv(
            "GITHUB_REF_NAME",
            "main"
        )

        self.base = (
            "https://api.github.com/repos/"
            + self.repo
        )

        self.s = requests.Session()

        self.s.headers.update({
            "Authorization": (
                "Bearer " + self.token
            ),
            "Accept": (
                "application/vnd.github+json"
            ),
            "X-GitHub-Api-Version": (
                "2022-11-28"
            ),
        })

    def api(self, method, path, **kwargs):
        response = self.s.request(
            method,
            self.base + path,
            timeout=50,
            **kwargs
        )

        response.raise_for_status()

        return response.json()

    def read(self, path):
        response = self.s.get(
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path,
                safe="/"
            ),
            params={
                "ref": self.branch
            },
            timeout=40,
        )

        if response.status_code == 404:
            return None, None

        response.raise_for_status()

        data = response.json()

        return (
            base64.b64decode(
                data["content"]
            ),
            data["sha"],
        )

    def write(self, path, data, sha=None):
        payload = {
            "message": (
                "SISMONAUTA SISMOS: "
                + path
            ),
            "content": base64.b64encode(
                data
            ).decode(),
            "branch": self.branch,
        }

        if sha:
            payload["sha"] = sha

        return self.api(
            "PUT",
            "/contents/"
            + urllib.parse.quote(
                path,
                safe="/"
            ),
            json=payload,
        )

    def assert_public(self):
        if self.api(
            "GET", ""
        ).get("private"):
            raise RuntimeError(
                "El repositorio debe ser público "
                "para alojar la URL multimedia"
            )


# ============================================================
# 24. INSTAGRAM GRAPH API
# ============================================================

def ig(method, endpoint, token, fields):
    fields = dict(
        fields,
        access_token=token
    )

    url = (
        f"{GRAPH_URL}/"
        f"{GRAPH_VERSION}/"
        f"{endpoint}"
    )

    if method == "GET":
        response = requests.get(
            url,
            params=fields,
            timeout=45
        )

    else:
        response = requests.post(
            url,
            data=fields,
            timeout=65
        )

    data = response.json()

    if (
        not response.ok
        or data.get("error")
    ):
        raise RuntimeError(
            f"Instagram {response.status_code}: "
            + str(data)[:400]
        )

    return data


def ig_publish(
    user,
    token,
    url,
    caption,
    media_type="REELS"
):
    data = {
        "media_type": media_type,
        "video_url": url,
    }

    if media_type == "REELS":
        data.update({
            "caption": caption,
            "share_to_feed": "true",
        })

    container_id = ig(
        "POST",
        f"{user}/media",
        token,
        data
    )["id"]

    for _ in range(30):
        time.sleep(10)

        status = ig(
            "GET",
            container_id,
            token,
            {
                "fields": (
                    "status_code,status"
                )
            }
        )

        state = status.get(
            "status_code"
        )

        if state == "FINISHED":
            break

        if state in (
            "ERROR",
            "EXPIRED"
        ):
            raise RuntimeError(
                "Procesamiento Instagram falló: "
                + str(status)
            )

    else:
        raise RuntimeError(
            "Tiempo de espera agotado; "
            "publicación incierta."
        )

    return ig(
        "POST",
        f"{user}/media_publish",
        token,
        {
            "creation_id": container_id
        }
    )["id"]


def verify_instagram_account():
    expected = os.getenv(
        "IG_EXPECTED_USERNAME",
        ACCOUNT
    ).strip().lstrip("@").lower()

    if expected != ACCOUNT:
        raise RuntimeError(
            "Cuenta esperada incorrecta: "
            + expected
        )

    token = os.getenv(
        "INSTAGRAM_TOKEN", ""
    )

    user = os.getenv(
        "INSTAGRAM_USER_ID", ""
    )

    if not token or not user:
        raise RuntimeError(
            "Faltan INSTAGRAM_TOKEN "
            "o INSTAGRAM_USER_ID"
        )

    me = ig(
        "GET",
        "me",
        token,
        {
            "fields": "id,username"
        }
    )

    if (
        str(me.get("id")) != user
        or str(
            me.get("username", "")
        ).lower() != ACCOUNT
    ):
        raise RuntimeError(
            "El token Instagram no corresponde "
            "a @sismonauta"
        )

    print(
        "TOKEN Instagram válido; "
        "cuenta verificada: @sismonauta",
        flush=True
    )


# ============================================================
# 25. FACEBOOK GRAPH API
# ============================================================

def fb_api(
    method,
    endpoint,
    token,
    params=None
):
    url = (
        "https://graph.facebook.com/"
        f"{GRAPH_VERSION}/"
        f"{endpoint}"
    )

    response = requests.request(
        method,
        url,
        params={
            "access_token": token,
            **(params or {}),
        },
        timeout=90,
    )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Facebook respondió sin JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get(
            "error", {}
        )

        raise RuntimeError(
            f"Facebook HTTP {response.status_code}: "
            + error.get(
                "message",
                str(data)[:180]
            )
        )

    return data


def verify_facebook_page():
    page = os.getenv(
        "FACEBOOK_PAGE_ID", ""
    ).strip()

    token = os.getenv(
        "FACEBOOK_PAGE_TOKEN", ""
    ).strip()

    if not page or not token:
        raise RuntimeError(
            "Faltan credenciales de Facebook"
        )

    if not page.isdigit():
        raise RuntimeError(
            "FACEBOOK_PAGE_ID debe ser numérico"
        )

    info = fb_api(
        "GET",
        page,
        token,
        {
            "fields": "id,name,username"
        }
    )

    if str(info.get("id")) != page:
        raise RuntimeError(
            "El token no corresponde a la Fanpage"
        )

    expected = os.getenv(
        "FACEBOOK_EXPECTED_USERNAME",
        "sismonauta"
    ).strip().lstrip("@").lower()

    username = str(
        info.get("username") or ""
    ).lower()

    if (
        username
        and expected
        and username != expected
    ):
        raise RuntimeError(
            "Fanpage incorrecta: "
            + username
        )

    print(
        "PAGINA Facebook validada:",
        info.get("name", ""),
        flush=True
    )

    return page, token


# ============================================================
# 26. FACEBOOK REEL
# ============================================================

def fb_publish_reel(
    page,
    token,
    movie,
    caption,
    episode_id
):
    session = fb_api(
        "POST",
        f"{page}/video_reels",
        token,
        {
            "upload_phase": "start"
        }
    )

    video_id = str(
        session.get("video_id") or ""
    )

    upload_url = str(
        session.get("upload_url") or ""
    )

    if (
        not video_id.isdigit()
        or not upload_url.startswith(
            "https://rupload.facebook.com/"
        )
    ):
        raise RuntimeError(
            "Facebook Reel: sesión de carga incompleta"
        )

    headers = {
        "Authorization": "OAuth " + token,
        "offset": "0",
        "file_size": str(
            movie.stat().st_size
        ),
        "Content-Type": (
            "application/octet-stream"
        ),
    }

    with movie.open("rb") as fh:
        response = requests.post(
            upload_url,
            headers=headers,
            data=fh,
            timeout=300,
        )

    try:
        uploaded = response.json()
    except ValueError:
        raise RuntimeError(
            "Facebook Reel: carga incierta"
        )

    if (
        not response.ok
        or uploaded.get("success") is not True
    ):
        raise RuntimeError(
            "Facebook Reel: carga no confirmada"
        )

    result = fb_api(
        "POST",
        f"{page}/video_reels",
        token,
        {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption[:5000],
            "title": str(episode_id)[:200],
        },
    )

    if result.get("success") is not True:
        raise RuntimeError(
            "Facebook Reel: finalización no confirmada"
        )

    return video_id


# ============================================================
# 27. FACEBOOK STORY
# ============================================================

def fb_publish_story(page, token, movie):
    session = fb_api(
        "POST",
        f"{page}/video_stories",
        token,
        {
            "upload_phase": "start"
        }
    )

    video_id = str(
        session.get("video_id") or ""
    )

    upload_url = str(
        session.get("upload_url") or ""
    )

    if (
        not video_id.isdigit()
        or not upload_url.startswith(
            "https://rupload.facebook.com/"
        )
    ):
        raise RuntimeError(
            "Facebook Story: sesión incompleta"
        )

    headers = {
        "Authorization": "OAuth " + token,
        "offset": "0",
        "file_size": str(
            movie.stat().st_size
        ),
        "Content-Type": (
            "application/octet-stream"
        ),
    }

    with movie.open("rb") as fh:
        response = requests.post(
            upload_url,
            headers=headers,
            data=fh,
            timeout=300,
        )

    try:
        uploaded = response.json()
    except ValueError:
        raise RuntimeError(
            "Facebook Story: carga incierta"
        )

    if (
        not response.ok
        or uploaded.get("success") is not True
    ):
        raise RuntimeError(
            "Facebook Story: carga no confirmada"
        )

    result = fb_api(
        "POST",
        f"{page}/video_stories",
        token,
        {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
        }
    )

    if result.get("success") is not True:
        raise RuntimeError(
            "Facebook Story: finalización no confirmada"
        )

    return video_id


# ============================================================
# 28. FACEBOOK FEED
# ============================================================

def fb_publish_feed_video(
    page,
    token,
    movie,
    caption,
    episode_id
):
    endpoint = (
        "https://graph.facebook.com/"
        f"{GRAPH_VERSION}/"
        f"{page}/videos"
    )

    with movie.open("rb") as fh:
        response = requests.post(
            endpoint,
            data={
                "access_token": token,
                "description": caption[:5000],
                "title": str(episode_id)[:200],
                "published": "true",
            },
            files={
                "source": (
                    movie.name,
                    fh,
                    "video/mp4",
                )
            },
            timeout=360,
        )

    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(
            "Facebook Feed: respuesta no JSON"
        )

    if (
        not response.ok
        or result.get("error")
    ):
        error = result.get(
            "error", {}
        )

        raise RuntimeError(
            "Facebook Feed: "
            + error.get(
                "message",
                str(result)[:180]
            )
        )

    video_id = str(
        result.get("id") or ""
    )

    if not video_id.isdigit():
        raise RuntimeError(
            "Facebook Feed: no se devolvió ID"
        )

    return video_id


# ============================================================
# 29. HISTORIAL ANTIDUPLICADOS
# ============================================================

def legacy_topic_for_episode(episode_id):
    if not str(episode_id).startswith(
        "sismos-"
    ):
        return None

    angles = [
        "¿Sabías esto?",
        "La explicación científica",
        "Lo que muestran los datos",
        "La diferencia importante",
        "La pregunta que importa",
        "Un detalle sorprendente",
        "Lo que no debemos confundir",
        "Lo esencial en segundos",
    ]

    for topic in TOPICS:
        for style in STYLES:
            for angle in angles:
                old_id = (
                    "sismos-"
                    + hashlib.sha1(
                        (
                            topic["id"]
                            + style
                            + angle
                        ).encode()
                    ).hexdigest()[:12]
                )

                if old_id == episode_id:
                    return topic["id"]

    return None


def remote_published_topics(
    state,
    history
):
    used = set()

    published_keys = (
        "reel",
        "story",
        "facebook_reel",
        "facebook_story",
        "facebook_feed",
    )

    for episode_id, row in (
        state.get("episodes") or {}
    ).items():

        if not isinstance(row, dict):
            continue

        if not any(
            row.get(key) in (
                "publicado",
                "enviando",
                "incierto",
            )
            for key in published_keys
        ):
            continue

        topic = (
            row.get("topic")
            or legacy_topic_for_episode(
                episode_id
            )
        )

        if not topic:
            topic = next(
                (
                    h.get("topic")
                    for h in history.get(
                        "items", []
                    )
                    if h.get("episode_id") == episode_id
                ),
                None
            )

        if topic:
            used.add(topic)

        else:
            raise RuntimeError(
                "Historial sin tema identificable: "
                + episode_id
            )

    for entry in history.get(
        "items", []
    ):
        if (
            entry.get("status") == "publicado"
            and entry.get("topic")
        ):
            used.add(
                entry["topic"]
            )

    return used


def choose_topic(
    history,
    requested="",
    excluded_topics=None
):
    excluded = set(
        excluded_topics or ()
    )

    if requested:
        if requested in excluded:
            raise RuntimeError(
                "Tema ya publicado: " + requested
            )

        matches = [
            topic
            for topic in TOPICS
            if topic["id"] == requested
        ]

        if not matches:
            raise ValueError(
                "Tema desconocido: " + requested
            )

        return matches[0]

    candidates = [
        topic
        for topic in TOPICS
        if (
            topic["id"] in LANDSCAPE_AUTO_TOPICS
            and topic["id"] not in excluded
        )
    ]

    if not candidates:
        raise RuntimeError(
            "CATALOGO_AGOTADO: no quedan "
            "temas científicos inéditos"
        )

    counts = {
        x["id"]: 0
        for x in candidates
    }

    for entry in history.get(
        "items", []
    ):
        topic_id = entry.get(
            "topic"
        )

        if topic_id in counts:
            counts[topic_id] += 1

    minimum = min(
        counts.values()
    )

    return random.SystemRandom().choice([
        topic
        for topic in candidates
        if counts[topic["id"]] == minimum
    ])


# ============================================================
# 30. PUBLICAR EN LAS CINCO PLATAFORMAS
# ============================================================

def publish(
    ep,
    movie,
    approval,
    story=False,
    auto=False,
    out_dir=None,
    facebook=True
):
    """
    Respeta estados anteriores.

    No reenvía publicaciones previamente
    publicadas o con resultado incierto.
    """

    if (
        not auto
        and approval != ep["episode_id"]
    ):
        raise RuntimeError(
            "Se requiere --approved-id"
        )

    if (
        auto
        and not ep.get("base_verified")
    ):
        raise RuntimeError(
            "Publicación autónoma no autorizada"
        )

    expected = os.getenv(
        "IG_EXPECTED_USERNAME",
        ACCOUNT
    ).strip().lstrip("@").lower()

    if expected != ACCOUNT:
        raise RuntimeError(
            "Cuenta esperada incorrecta"
        )

    token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    user = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    me = ig(
        "GET",
        "me",
        token,
        {
            "fields": "id,username"
        }
    )

    if (
        str(me.get("id")) != user
        or str(
            me.get("username", "")
        ).lower() != ACCOUNT
    ):
        raise RuntimeError(
            "Token Instagram de otra cuenta"
        )

    gh = Github()
    gh.assert_public()

    raw, sha = gh.read(
        STATE_PATH
    )

    state = (
        json.loads(raw)
        if raw
        else {
            "schema": 1,
            "episodes": {},
        }
    )

    items = state.setdefault(
        "episodes", {}
    )

    row = items.setdefault(
        ep["episode_id"], {}
    )

    for old_id, old_row in items.items():
        if (
            old_id == ep["episode_id"]
            or not isinstance(old_row, dict)
        ):
            continue

        old_topic = (
            old_row.get("topic")
            or legacy_topic_for_episode(
                old_id
            )
        )

        if (
            old_topic == ep.get("id")
            and any(
                old_row.get(k) in (
                    "publicado",
                    "enviando",
                    "incierto",
                )
                for k in (
                    "reel",
                    "story",
                    "facebook_reel",
                    "facebook_story",
                    "facebook_feed",
                )
            )
        ):
            raise RuntimeError(
                "Tema ya publicado o en proceso: "
                + old_topic
            )

    if not row.get("topic"):
        row["topic"] = ep.get(
            "id"
        )

    destinations = [
        "reel"
    ]

    if story:
        destinations.append(
            "story"
        )

    if facebook:
        destinations.append(
            "facebook_reel"
        )

        if story:
            destinations.append(
                "facebook_story"
            )

        destinations.append(
            "facebook_feed"
        )

    for dest in destinations:
        status = row.get(
            dest
        )

        if status in (
            "enviando",
            "incierto"
        ):
            raise RuntimeError(
                f"{dest}: envío anterior incierto"
            )

        if (
            status == "publicado"
            and not str(
                row.get(dest + "_id") or ""
            ).isdigit()
        ):
            raise RuntimeError(
                f"{dest}: publicado sin ID válido"
            )

        if status not in (
            None,
            "publicado"
        ):
            raise RuntimeError(
                "Estado de historial desconocido"
            )

    print(
        "REANUDACION SEGURA:",
        {
            k: row.get(k, "pendiente")
            for k in destinations
        },
        flush=True
    )

    def save(status, which):
        nonlocal sha

        row[which] = status
        row["updated_at"] = utcnow()

        result = gh.write(
            STATE_PATH,
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2
            ).encode(),
            sha
        )

        sha = result[
            "content"
        ]["sha"]

    receipt_path = (
        Path(out_dir or movie.parent)
        / "publicacion_confirmada.json"
    )

    confirmation = {
        "account": ACCOUNT,
        "episode_id": ep["episode_id"],
        "reel_id": row.get("reel_id"),
        "reel_published": row.get("reel") == "publicado",
        "story_requested": bool(story),
        "story_published": row.get("story") == "publicado",
        "story_id": row.get("story_id"),
        "facebook_reel_requested": bool(facebook),
        "facebook_reel_published": row.get("facebook_reel") == "publicado",
        "facebook_reel_id": row.get("facebook_reel_id"),
        "facebook_story_requested": bool(facebook and story),
        "facebook_story_published": row.get("facebook_story") == "publicado",
        "facebook_story_id": row.get("facebook_story_id"),
        "facebook_feed_requested": bool(facebook),
        "facebook_feed_published": row.get("facebook_feed") == "publicado",
        "facebook_feed_id": row.get("facebook_feed_id"),
        "confirmed_at": utcnow(),
    }

    def confirm(which, media_id):
        row[which + "_id"] = str(
            media_id
        )

        save(
            "publicado",
            which
        )

        confirmation[
            which + "_id"
        ] = str(media_id)

        confirmation[
            which + "_published"
        ] = True

        confirmation[
            "confirmed_at"
        ] = utcnow()

        write_json_atomic(
            receipt_path,
            confirmation
        )

        print(
            f"{which.upper()} ACEPTADO POR META:",
            media_id,
            flush=True
        )

    write_json_atomic(
        receipt_path,
        confirmation
    )

    credits = ep.get(
        "image_credits", []
    )

    if (
        not credits
        or any(
            not license_accepted(
                c.get("license", "")
            )
            for c in credits
        )
    ):
        raise RuntimeError(
            "No hay imágenes CC0/PD verificadas"
        )

    caption = (
        episode_caption_base(ep)
        + "\n\n"
        + episode_hashtags(ep)
    )

    if (
        len(caption) > 2200
        or len(
            re.findall(
                r"(?<!\w)#[A-Za-z0-9_]+",
                caption
            )
        ) < 10
    ):
        raise RuntimeError(
            "Descripción editorial inválida"
        )

    # ----------------------------------------
    # URL MULTIMEDIA PARA INSTAGRAM
    # ----------------------------------------

    pending_ig = [
        dest
        for dest in (
            "reel",
            "story",
        )
        if (
            dest in destinations
            and row.get(dest) != "publicado"
        )
    ]

    url = None

    if pending_ig:
        contents = movie.read_bytes()

        digest = hashlib.sha256(
            contents
        ).hexdigest()[:16]

        remote = (
            "groundshake_sismos_media/"
            + ep["episode_id"]
            + "-"
            + digest
            + ".mp4"
        )

        old_file, _ = gh.read(
            remote
        )

        if old_file is None:
            result = gh.write(
                remote,
                contents
            )

            commit = result[
                "commit"
            ]["sha"]

        else:
            if old_file != contents:
                raise RuntimeError(
                    "Colisión de archivos multimedia"
                )

            commit = gh.api(
                "GET",
                f"/commits/{gh.branch}"
            )["sha"]

        url = (
            "https://raw.githubusercontent.com/"
            f"{gh.repo}/{commit}/{remote}"
        )

        with requests.get(
            url,
            timeout=90
        ) as response:
            response.raise_for_status()

            if len(
                response.content
            ) != movie.stat().st_size:
                raise RuntimeError(
                    "URL MP4 incompleta"
                )

    # ----------------------------------------
    # INSTAGRAM REEL + STORY
    # ----------------------------------------

    for which in (
        "reel",
        "story"
    ):
        if which not in destinations:
            continue

        if row.get(which) == "publicado":
            print(
                which.upper(),
                "OMITIDO: ya publicado",
                flush=True
            )
            continue

        save(
            "enviando",
            which
        )

        try:
            published_id = ig_publish(
                user,
                token,
                url,
                caption if which == "reel" else "",
                media_type=(
                    "REELS"
                    if which == "reel"
                    else "STORIES"
                ),
            )

        except BaseException:
            save(
                "incierto",
                which
            )
            raise

        confirm(
            which,
            published_id
        )

    # ----------------------------------------
    # FACEBOOK REEL + STORY + FEED
    # ----------------------------------------

    if facebook:
        page, fb_token = verify_facebook_page()

        confirmation[
            "facebook_page_id"
        ] = page

        write_json_atomic(
            receipt_path,
            confirmation
        )

        for which in (
            "facebook_reel",
            "facebook_story",
            "facebook_feed",
        ):
            if which not in destinations:
                continue

            if row.get(which) == "publicado":
                print(
                    which.upper(),
                    "OMITIDO: ya publicado",
                    flush=True
                )
                continue

            save(
                "enviando",
                which
            )

            try:
                if which == "facebook_reel":
                    published_id = fb_publish_reel(
                        page,
                        fb_token,
                        movie,
                        caption,
                        ep["episode_id"],
                    )

                elif which == "facebook_story":
                    published_id = fb_publish_story(
                        page,
                        fb_token,
                        movie,
                    )

                else:
                    published_id = fb_publish_feed_video(
                        page,
                        fb_token,
                        movie,
                        caption,
                        ep["episode_id"],
                    )

            except BaseException:
                save(
                    "incierto",
                    which
                )

                confirmation[
                    which + "_uncertain"
                ] = True

                write_json_atomic(
                    receipt_path,
                    confirmation
                )

                raise

            confirm(
                which,
                published_id
            )

    print(
        "PUBLICACIONES SOLICITADAS A META;",
        "recibo:",
        receipt_path,
        flush=True
    )


# ============================================================
# 31. MÉTRICAS
# ============================================================

def metrics(csv_path):
    import csv

    with open(
        csv_path,
        encoding="utf-8-sig",
        newline=""
    ) as fh:

        for row in csv.DictReader(fh):
            plays = int(
                row["plays"]
            )

            views = int(
                row["views_7s"]
            )

            if (
                plays < 0
                or views < 0
                or views > plays
            ):
                raise ValueError(
                    "Métricas inconsistentes"
                )

            rate = (
                views / plays
                if plays
                else 0
            )

            result = (
                "ALCANZADO"
                if rate >= TARGET_R7
                else "NO ALCANZADO"
            )

            print(
                f"{row['episode_id']}: "
                f"R7={rate:.1%} | "
                f"objetivo=70% | "
                f"{result}"
            )


# ============================================================
# 32. PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA @sismonauta: "
            "videos sísmicos con fotos reales"
        )
    )

    parser.add_argument(
        "--out",
        default="output_sismos"
    )

    parser.add_argument(
        "--topic",
        default=""
    )

    parser.add_argument(
        "--photo-dir",
        default=""
    )

    parser.add_argument(
        "--no-ai",
        action="store_true"
    )

    parser.add_argument(
        "--no-voice",
        action="store_true"
    )

    parser.add_argument(
        "--publish",
        action="store_true"
    )

    parser.add_argument(
        "--auto-publish",
        action="store_true"
    )

    parser.add_argument(
        "--approved-id",
        default=""
    )

    parser.add_argument(
        "--story",
        action="store_true"
    )

    parser.add_argument(
        "--no-facebook",
        action="store_true"
    )

    parser.add_argument(
        "--metrics-csv",
        default=""
    )

    parser.add_argument(
        "--list",
        action="store_true"
    )

    parser.add_argument(
        "--episode-json",
        default=""
    )

    args = parser.parse_args()

    if args.list:
        for topic in TOPICS:
            print(
                topic["id"],
                topic["title"]
            )
        return

    if args.metrics_csv:
        metrics(
            args.metrics_csv
        )
        return

    out = Path(
        args.out
    )

    out.mkdir(
        parents=True,
        exist_ok=True
    )

    (
        out / "publicacion_confirmada.json"
    ).unlink(
        missing_ok=True
    )

    history_path = (
        out / "historial_sismos.json"
    )

    history = read_json(
        history_path,
        {
            "items": []
        }
    )

    # ----------------------------------------
    # HISTORIAL EDITORIAL REMOTO
    # ----------------------------------------

    if (
        os.getenv("GH_TOKEN")
        and os.getenv("GITHUB_REPOSITORY")
    ):
        try:
            gh = Github()

            raw, _ = gh.read(
                "dataviral/sismos_editorial.json"
            )

            if raw:
                history = json.loads(
                    raw
                )

        except Exception as exc:
            print(
                "Historial remoto no disponible:",
                str(exc)[:140],
                flush=True
            )

    # ----------------------------------------
    # TEMAS PUBLICADOS / EN PROCESO
    # ----------------------------------------

    published_topics = set()

    if args.publish:
        gh_state = Github()

        raw_state, _ = gh_state.read(
            STATE_PATH
        )

        live_state = (
            json.loads(raw_state)
            if raw_state
            else {
                "episodes": {}
            }
        )

        published_topics = remote_published_topics(
            live_state,
            history
        )

        print(
            "ANTIDUPLICADOS:",
            sorted(published_topics),
            flush=True
        )

    if (
        args.auto_publish
        and not args.publish
    ):
        raise RuntimeError(
            "--auto-publish requiere --publish"
        )

    if args.publish:
        verify_instagram_account()

        if not args.no_facebook:
            verify_facebook_page()

    if (
        args.auto_publish
        and args.episode_json
    ):
        raise RuntimeError(
            "No usar --episode-json "
            "con --auto-publish"
        )

    # ----------------------------------------
    # SELECCIÓN DE TEMAS Y FOTOGRAFÍAS
    # ----------------------------------------

    tried_visual = set()

    while True:
        if args.episode_json:
            ep = read_json(
                args.episode_json,
                None
            )

            if (
                not isinstance(ep, dict)
                or not all(
                    ep.get(x)
                    for x in (
                        "episode_id",
                        "title",
                        "script",
                        "visuals",
                        "source",
                        "queries",
                    )
                )
            ):
                raise RuntimeError(
                    "JSON del episodio inválido"
                )

            if not str(
                ep["episode_id"]
            ).startswith("sismos-"):
                raise RuntimeError(
                    "Solo episodios sísmicos SISMONAUTA"
                )

            topic = {
                "id": ep.get(
                    "id",
                    "revisado"
                )
            }

        else:
            try:
                topic = choose_topic(
                    history,
                    args.topic,
                    published_topics | tried_visual,
                )

            except RuntimeError as exc:
                if (
                    tried_visual
                    and not args.topic
                ):
                    raise RuntimeError(
                        "No fue posible encontrar "
                        "ninguna fotografía válida "
                        "para los temas inéditos probados. "
                        "Temas descartados: "
                        + ", ".join(
                            sorted(tried_visual)
                        )
                    ) from exc

                raise

            ep = generate_angle(
                topic,
                history,
                use_ai=(
                    not args.no_ai
                    and not args.auto_publish
                )
            )

            ep["base_verified"] = bool(
                args.auto_publish
                and topic.get("source")
                and topic.get("fact")
                and topic.get("lesson")
            )

            ep["editorial_check"] = (
                "Afirmaciones restringidas "
                "a la base editorial; "
                "no equivale a verificar "
                "la página de origen en tiempo real."
                if args.auto_publish
                else "Requiere revisión de datos"
            )

        episode = ep[
            "episode_id"
        ]

        write_json_atomic(
            out / (
                episode + "_guion.json"
            ),
            ep
        )

        print(
            "EPISODIO:",
            episode,
            "TEMA:",
            ep["title"],
            "FUENTE:",
            ep["source"],
            flush=True
        )

        if (
            args.publish
            and not args.auto_publish
            and args.approved_id != episode
        ):
            raise RuntimeError(
                "No se publica sin "
                "--approved-id "
                + episode
            )

        if (
            args.auto_publish
            and (
                not ep.get("base_verified")
                or not str(
                    ep.get("source", "")
                ).startswith("https://")
            )
        ):
            raise RuntimeError(
                "Falta fuente o afirmación "
                "editorial predefinida"
            )

        try:
            movie, cover, credits = render(
                ep,
                out,
                args.photo_dir or None,
                voice=not args.no_voice
            )

            break

        except RuntimeError as exc:
            message = str(
                exc
            )

            photo_issue = any(
                text in message
                for text in (
                    "No se encontraron fotografías auténticas",
                    "No hay fotografías auténticas",
                    "Fotografías sin pertinencia sísmica suficiente",
                )
            )

            if not (
                photo_issue
                and not args.topic
                and not args.episode_json
                and not args.photo_dir
            ):
                raise

            tried_visual.add(
                topic["id"]
            )

            print(
                "REINTENTO VISUAL: descartado",
                topic["id"],
                "por falta de fotografías pertinentes;",
                "probando otro tema inédito.",
                "Detalle:",
                message,
                flush=True
            )

    # ----------------------------------------
    # GUARDAR GUION Y CRÉDITOS
    # ----------------------------------------

    ep["image_credits"] = credits

    write_json_atomic(
        out / (
            episode + "_guion.json"
        ),
        ep
    )

    if not any(
        entry.get("episode_id") == episode
        for entry in history["items"]
    ):
        history["items"].append({
            "episode_id": episode,
            "topic": topic["id"],
            "style": ep["style"],
            "angle": ep["angle"],
            "time": utcnow(),
            "status": (
                "preparado"
                if args.publish
                else "vista_previa"
            ),
        })

    write_json_atomic(
        history_path,
        history
    )

    # ----------------------------------------
    # PUBLICACIÓN EN META
    # ----------------------------------------

    if args.publish:
        publish(
            ep,
            movie,
            args.approved_id,
            story=args.story,
            auto=args.auto_publish,
            out_dir=out,
            facebook=not args.no_facebook,
        )

        for entry in history["items"]:
            if entry.get(
                "episode_id"
            ) == episode:
                entry["status"] = "publicado"

        write_json_atomic(
            history_path,
            history
        )

        gh = Github()

        raw, sha = gh.read(
            "dataviral/sismos_editorial.json"
        )

        gh.write(
            "dataviral/sismos_editorial.json",
            json.dumps(
                history,
                ensure_ascii=False,
                indent=2
            ).encode(),
            sha
        )

    else:
        print(
            "VISTA PREVIA: no se publicó nada.",
            flush=True
        )

    print(
        "OBJETIVO R7:",
        TARGET_R7,
        "(experimental, nunca garantizado)",
        flush=True
    )


# ============================================================
# 33. INICIO
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "ERROR:",
            str(exc),
            file=sys.stderr
        )

        sys.exit(1)
