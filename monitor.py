
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA @sismonauta
MONITOR AUTOMATICO DE DIVULGACION SISMICA

Python 3.11+

- 25 temas cientificos documentados
- Temas ineditos con historial GitHub
- Fotos reales de Wikimedia Commons y Openverse
- Paisajes, montanas, cerros, bosques, playas y costas
- Licencias CC0 y dominio publico
- Una sola fotografia es suficiente
- Encuadres variables para cada escena
- Voz neural individual por escena
- Rotulos grandes, portada y musica original
- Animacion DALE LIKE
- Instagram Reel + Story
- Facebook Reel + Story + Feed
- Prevencion de duplicados y envios inciertos
- Omision segura si no hay fotografias validas

INSTALACION:
    pip install pillow requests edge-tts mutagen imageio-ffmpeg

Requiere FFmpeg instalado en PATH.

VISTA PREVIA:
    python monitor.py --out output_sismos

PUBLICACION:
    python monitor.py --publish --auto-publish --story

No utiliza Pexels ni requiere claves de imagenes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import ipaddress
import json
import math
import os
import random
import re
import shutil
import socket
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

# ============================================================
# 1. CONFIGURACION
# ============================================================

BRAND = "SISMONAUTA"
ACCOUNT = "sismonauta"

# Se mantiene para preservar los historiales anteriores.
STATE_PATH = "groundshake/state.json"
EDITORIAL_PATH = "dataviral/sismos_editorial.json"

GRAPH_VERSION = os.getenv(
    "IG_GRAPH_VERSION", "v25.0"
)

GRAPH_URL = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

W = 720
H = 1280
FPS = 30
TARGET_R7 = 0.70

VOICE = os.getenv(
    "DV_VOICE",
    "es-CL-CatalinaNeural"
)

MIN_VALID_PHOTOS = 1
MAX_VALID_PHOTOS = 3

COMMONS_TIMEOUT = (10, 35)
OPENVERSE_TIMEOUT = (8, 20)

COMMONS_PAGES = 3
COMMONS_PAGE_SIZE = 40

OPENVERSE_BACKOFF = 600
_OPENVERSE_UNAVAILABLE_UNTIL = 0.0

ENABLE_OPENVERSE = (
    os.getenv("ENABLE_OPENVERSE", "true")
    .lower() in ("true", "1", "yes")
)

USER_AGENT = (
    "SismonautaSismos/4.0 "
    "(educational media; Wikimedia Commons)"
)

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
})

# ============================================================
# 2. UTILIDADES
# ============================================================

def norm(value):
    return "".join(
        c for c in unicodedata.normalize(
            "NFKD",
            str(value).lower()
        )
        if not unicodedata.combining(c)
    )


def utcnow():
    return datetime.now(
        timezone.utc
    ).isoformat()


def log(*values):
    print(*values, flush=True)


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
            indent=2,
            default=str
        ),
        encoding="utf-8"
    )

    tmp.replace(path)


def safe_error(exc):
    message = str(exc)

    for name in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
    ):
        secret = os.getenv(name, "")
        if secret:
            message = message.replace(
                secret, "***"
            )

    return message[:350]


def has_term(text, term):
    text = norm(text)
    term = norm(term).strip()

    return bool(
        re.search(
            r"(?<![a-z])"
            + re.escape(term)
            + r"(?![a-z])",
            text
        )
    )


def ffmpeg(args):
    process = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            *map(str, args)
        ],
        capture_output=True,
        text=True
    )

    if process.returncode:
        raise RuntimeError(
            "FFmpeg: "
            + process.stderr[-1400:]
        )


# ============================================================
# 3. CATALOGO CIENTIFICO
# ============================================================

def topic(
    key,
    title,
    fact,
    lesson,
    source,
    queries
):
    return {
        "id": key,
        "category": "Divulgación sísmica",
        "title": title,
        "fact": fact,
        "lesson": lesson,
        "source": source,
        "queries": queries,
    }


USGS = (
    "https://www.usgs.gov/"
    "programs/earthquake-hazards"
)

NOAA = (
    "https://www.noaa.gov/jetstream/"
    "tsunamis/tsunami-inundation"
)

BGS = (
    "https://www.bgs.ac.uk/discovering-geology/"
    "earth-hazards/earthquakes/what-causes-earthquakes/"
)

TOPICS = [
    topic(
        "ruptura",
        "¿Todos los terremotos rompen la superficie?",
        "Un terremoto puede romper la superficie cuando "
        "el desplazamiento de una falla alcanza el terreno; "
        "no todos lo hacen.",
        "Una grieta visible no acompaña a todos los sismos.",
        USGS + "/science/faqs?page=1",
        ["earthquake surface rupture", "fault scarp"],
    ),
    topic(
        "deslizamientos",
        "¿Un terremoto puede derrumbar una montaña?",
        "Las sacudidas fuertes pueden desestabilizar "
        "laderas y desencadenar deslizamientos de tierra.",
        "Las consecuencias de un terremoto no se limitan "
        "a las sacudidas.",
        USGS + "/science/faqs",
        ["earthquake landslide", "mountain hillside"],
    ),
    topic(
        "grietas",
        "¿La Tierra se abre durante un terremoto?",
        "Algunos terremotos provocan grietas poco profundas "
        "por deslizamientos y deformación del terreno; "
        "las fallas no se abren como grandes abismos.",
        "Las grietas superficiales no son pozos sin fondo.",
        USGS + "/science/faqs",
        ["earthquake ground fissure", "rock landscape"],
    ),
    topic(
        "transformante",
        "¿Qué ocurre cuando dos placas se deslizan de lado?",
        "En los límites transformantes, dos placas se "
        "desplazan horizontalmente una respecto a la otra; "
        "estas zonas pueden generar grandes terremotos.",
        "La falla de San Andrés es un ejemplo.",
        "https://www.noaa.gov/jetstream/tsunamis/"
        "tsunami-generation-earthquakes/"
        "jetstream-max-plate-tectonics-and-earthquakes",
        ["San Andreas Fault", "California mountains"],
    ),
    topic(
        "rift",
        "¿Por qué se forman grandes valles de fractura?",
        "En algunos límites divergentes, las placas se "
        "separan y pueden formar valles de rift "
        "acompañados de actividad sísmica.",
        "Un valle puede contar la historia de placas "
        "que se separan.",
        USGS,
        ["East African Rift valley", "rift mountains"],
    ),
    topic(
        "subduccion",
        "¿Qué ocurre cuando una placa se hunde bajo otra?",
        "En las zonas de subducción, una placa se introduce "
        "bajo otra y pueden producirse grandes terremotos "
        "y tsunamis.",
        "La costa puede revelar dónde convergen las placas.",
        USGS,
        ["Andes mountains coastline", "Pacific coast mountains"],
    ),
    topic(
        "colision",
        "¿Qué relación tienen las montañas con la tectónica?",
        "La colisión de placas continentales puede elevar "
        "y deformar rocas, formando grandes cordilleras "
        "y fallas inversas.",
        "Las cordilleras guardan señales del movimiento terrestre.",
        BGS,
        ["Himalaya mountains", "mountain geological landscape"],
    ),
    topic(
        "falla_inversa",
        "¿Puede una falla elevar parte del paisaje?",
        "En una falla inversa un bloque de roca se desplaza "
        "hacia arriba respecto al otro, debido a esfuerzos "
        "de compresión.",
        "La compresión también transforma el relieve.",
        BGS,
        ["thrust fault", "rock mountains"],
    ),
    topic(
        "falla_normal",
        "¿Una falla puede hundir un bloque de terreno?",
        "En las fallas normales un bloque desciende respecto "
        "al otro bajo condiciones de extensión de la corteza.",
        "La extensión deja escalones visibles en el terreno.",
        BGS,
        ["normal fault scarp", "rift valley landscape"],
    ),
    topic(
        "tsunami_oleadas",
        "¿La primera ola de un tsunami es siempre la mayor?",
        "La primera ola de un tsunami no tiene por qué ser "
        "la más alta ni la más destructiva; pueden llegar "
        "varias oleadas.",
        "La primera ola no marca necesariamente el final del peligro.",
        NOAA,
        ["tsunami coast", "ocean waves coastline"],
    ),
    topic(
        "tsunami_retroceso",
        "¿Por qué el mar puede retroceder antes de un tsunami?",
        "Algunos tsunamis llegan primero con un valle de onda "
        "y producen un retiro inusual del mar; no ocurre "
        "en todos los casos.",
        "Un retiro repentino del mar es una señal natural de peligro.",
        NOAA,
        ["tsunami sea withdrawal", "beach shoreline landscape"],
    ),
    topic(
        "tsunami_rios",
        "¿Un tsunami puede avanzar por un río?",
        "Los tsunamis pueden remontar ríos y estuarios "
        "conectados con el mar, extendiendo la inundación "
        "tierra adentro.",
        "La amenaza puede seguir el curso de un río.",
        NOAA,
        ["tsunami estuary", "river estuary coastline"],
    ),
    topic(
        "tsunami_inundacion",
        "¿Un tsunami siempre parece una enorme ola que rompe?",
        "Un tsunami puede llegar a la costa como una "
        "inundación que sube rápidamente, y no necesariamente "
        "como una ola gigante de película.",
        "El peligro no siempre tiene la forma que imaginamos.",
        "https://www.weather.gov/safety/tsunami-about",
        ["tsunami coastal inundation", "beach coast ocean"],
    ),
    topic(
        "ondas",
        "¿Por qué hay distintos tipos de ondas sísmicas?",
        "Las ondas P y S viajan de maneras diferentes; "
        "las ondas S no se propagan a través de líquidos.",
        "El comportamiento de las ondas revela propiedades "
        "del interior terrestre.",
        USGS + "/science/earthquake-science",
        ["seismometer", "geological mountain landscape"],
    ),
    topic(
        "magnitud",
        "¿Por qué magnitud e intensidad no son lo mismo?",
        "La magnitud describe el tamaño de un terremoto; "
        "la intensidad describe los efectos y sacudidas "
        "en un lugar.",
        "Un mismo terremoto puede sentirse de forma diferente.",
        USGS,
        ["earthquake landscape", "mountain terrain"],
    ),
    topic(
        "replicas",
        "¿Por qué ocurren réplicas después de un terremoto?",
        "Las réplicas son terremotos que siguen a uno mayor "
        "en su misma región durante el reajuste de la corteza.",
        "La actividad sísmica puede continuar tras un evento principal.",
        USGS,
        ["earthquake geology", "rock mountains landscape"],
    ),
    topic(
        "valdivia",
        "El mayor terremoto registrado instrumentalmente",
        "El terremoto de Valdivia, Chile, del 22 de mayo "
        "de 1960 alcanzó una magnitud de 9,5.",
        "Los registros históricos permiten estudiar "
        "terremotos extraordinarios.",
        USGS + "/science/20-largest-earthquakes-world",
        ["1960 Valdivia earthquake", "Valdivia Chile landscape"],
    ),
    topic(
        "profundidad",
        "¿Qué diferencia a un terremoto superficial de uno profundo?",
        "Los terremotos pueden originarse a profundidades "
        "muy distintas; la profundidad influye en cómo "
        "se perciben sus ondas.",
        "La profundidad es importante al interpretar un sismo.",
        "https://earthquake.usgs.gov/earthquakes/map/",
        ["mountain rock layers", "mountain landscape"],
    ),
    topic(
        "placas",
        "¿Por qué se mueve la superficie de la Tierra?",
        "La litosfera está dividida en placas que se "
        "desplazan lentamente y pueden acumular "
        "esfuerzos en sus bordes.",
        "El movimiento de placas explica gran parte "
        "de la actividad sísmica.",
        USGS,
        ["San Andreas Fault", "mountain valleys"],
    ),
    topic(
        "tsunamis",
        "¿Todos los terremotos submarinos provocan tsunamis?",
        "No todos los terremotos submarinos generan tsunamis; "
        "importa el desplazamiento del fondo marino "
        "y otros factores.",
        "Un terremoto bajo el océano no implica automáticamente "
        "un tsunami.",
        "https://www.tsunami.gov/",
        ["tsunami coast", "ocean shoreline"],
    ),
    topic(
        "alerta",
        "¿Una alerta sísmica predice terremotos?",
        "Los sistemas de alerta temprana detectan un "
        "terremoto ya iniciado e intentan avisar antes "
        "de la llegada de sacudidas fuertes.",
        "La alerta temprana no es una predicción.",
        USGS + "/earthquake-early-warning",
        ["earthquake monitoring station", "mountain landscape"],
    ),
    topic(
        "falla",
        "¿Qué es realmente una falla geológica?",
        "Una falla es una fractura de las rocas a lo "
        "largo de la cual ha ocurrido desplazamiento.",
        "Las fallas ayudan a comprender cómo se deforman las rocas.",
        USGS,
        ["San Andreas Fault", "rocky mountain landscape"],
    ),
    topic(
        "prediccion",
        "¿La ciencia puede predecir el día exacto de un sismo?",
        "Actualmente no existe un método científicamente "
        "validado que prediga con certeza fecha, lugar "
        "y magnitud de un gran terremoto.",
        "Las probabilidades no son predicciones exactas.",
        "https://www.usgs.gov/faqs/can-you-predict-earthquakes",
        ["San Andreas Fault", "mountain forest landscape"],
    ),
    topic(
        "sismografo",
        "¿Cómo registra un instrumento el movimiento del suelo?",
        "Los sismómetros detectan el movimiento del suelo "
        "y producen registros que ayudan a estudiar terremotos.",
        "Las ondas sísmicas permiten investigar fenómenos "
        "que no podemos ver directamente.",
        USGS,
        ["seismograph", "geological rock landscape"],
    ),
    topic(
        "terremoto",
        "¿Por qué un terremoto lejano puede sentirse?",
        "Las ondas sísmicas transportan energía a través "
        "de la Tierra y algunas pueden recorrer "
        "grandes distancias.",
        "La distancia al epicentro no explica por sí sola "
        "la percepción del temblor.",
        USGS,
        ["earthquake fault", "mountain forest landscape"],
    ),
]

STYLES = (
    "descubrimiento",
    "microdocumental",
    "mito_realidad",
    "mapa_explicado",
    "pregunta_respuesta",
)

# ============================================================
# 4. GUIONES POR ESCENA
# ============================================================

# Cinco escenas educativas + una escena final DALE LIKE.
# Cada par contiene (voz, rotulo).

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
        ("En una falla transformante las placas se mueven lateralmente.", "MOVIMIENTO HORIZONTAL"),
        ("Ese desplazamiento puede generar fuertes terremotos.", "PUEDEN OCURRIR TERREMOTOS"),
        ("La falla de San Andrés permite observar este tipo de límite.", "SAN ANDRÉS DA PISTAS"),
    ],
    "rift": [
        ("¿Por qué existen valles tan largos entre montañas?", "¿CÓMO NACE UN RIFT?"),
        ("Algunos revelan que la corteza se está separando.", "LA CORTEZA SE SEPARA"),
        ("En un límite divergente las placas se alejan entre sí.", "PLACAS EN SEPARACIÓN"),
        ("La extensión puede formar valles de rift y actividad sísmica.", "UN VALLE QUE SE ABRE"),
        ("El relieve deja huellas del movimiento de placas.", "EL PAISAJE CUENTA HISTORIAS"),
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
        ("No es que conozca el futuro.", "NO ES ADIVINACIÓN"),
        ("El terremoto ya comenzó cuando los sensores detectan sus primeras ondas.", "EL SISMO YA COMENZÓ"),
        ("La alerta puede llegar antes que las sacudidas fuertes, pero no siempre hay tiempo suficiente.", "SEGUNDOS QUE PUEDEN IMPORTAR"),
        ("La alerta no predice el sismo. Detecta lo que ya empezó.", "DETECTAR NO ES PREDECIR"),
    ],
    "ondas": [
        ("¿Cómo sabemos qué ocurre dentro de la Tierra?", "¿CÓMO MIRAMOS BAJO TIERRA?"),
        ("La pista viene de las ondas que viajan tras un terremoto.", "LAS ONDAS GUARDAN PISTAS"),
        ("Las ondas P y S se propagan de forma distinta.", "NO TODAS VIAJAN IGUAL"),
        ("Las ondas S no atraviesan líquidos.", "LAS ONDAS S NO CRUZAN LÍQUIDOS"),
        ("Las ondas sísmicas revelan propiedades del interior terrestre.", "LAS ONDAS NOS ENSEÑAN"),
    ],
    "magnitud": [
        ("¿Por qué un terremoto se siente diferente según el lugar?", "¿UN SISMO, DOS EXPERIENCIAS?"),
        ("El número de magnitud no lo explica todo.", "EL NÚMERO NO BASTA"),
        ("La magnitud describe el tamaño del terremoto.", "MAGNITUD: TAMAÑO"),
        ("La intensidad describe las sacudidas y efectos en cada lugar.", "INTENSIDAD: EFECTOS"),
        ("El terremoto es uno. Sus efectos pueden ser diferentes.", "UN SISMO. MUCHAS EXPERIENCIAS"),
    ],
    "replicas": [
        ("¿El peligro termina cuando deja de temblar?", "¿YA TERMINÓ TODO?"),
        ("Después de un terremoto la región puede reajustarse.", "LA TIERRA SIGUE REAJUSTÁNDOSE"),
        ("Esos movimientos posteriores se conocen como réplicas.", "PUEDEN LLEGAR RÉPLICAS"),
        ("Pueden ocurrir después del evento principal.", "EL MOVIMIENTO PUEDE CONTINUAR"),
        ("El suelo quieto no garantiza que terminen las réplicas.", "EL SILENCIO NO ES EL FINAL"),
    ],
    "valdivia": [
        ("¿Sabías que el mayor terremoto registrado ocurrió en Chile?", "¿EL MAYOR TERREMOTO?"),
        ("Sucedió en un lugar que marcó la historia sísmica.", "CHILE QUEDÓ EN LA HISTORIA"),
        ("El 22 de mayo de 1960, Valdivia vivió un terremoto de magnitud nueve coma cinco.", "VALDIVIA 1960: MAGNITUD 9,5"),
        ("Es el mayor registrado instrumentalmente.", "UN REGISTRO EXTRAORDINARIO"),
        ("Valdivia recuerda cuánta energía puede liberar la Tierra.", "UNA FECHA HISTÓRICA"),
    ],
    "profundidad": [
        ("¿Por qué dos sismos parecidos pueden sentirse diferentes?", "¿MISMA MAGNITUD?"),
        ("Hay un dato que suele quedar fuera del titular.", "MIRA BAJO LA SUPERFICIE"),
        ("No todos los sismos comienzan a la misma profundidad.", "LA PROFUNDIDAD CAMBIA"),
        ("Esa profundidad influye en cómo se percibe el movimiento.", "NO TODO ES MAGNITUD"),
        ("Para comprender un sismo también importa dónde comienza.", "IMPORTA LO QUE NO VEMOS"),
    ],
    "placas": [
        ("¿Cómo puede moverse el suelo que parece tan firme?", "¿LA TIERRA ESTÁ QUIETA?"),
        ("El movimiento es lento, pero sus efectos pueden ser enormes.", "UN MOVIMIENTO CASI INVISIBLE"),
        ("La parte externa rígida de la Tierra está dividida en placas.", "VIVIMOS SOBRE PLACAS"),
        ("Estas placas se desplazan y acumulan esfuerzos en sus bordes.", "LOS ESFUERZOS SE ACUMULAN"),
        ("El suelo parece inmóvil, pero la Tierra sigue moviéndose.", "LA QUIETUD ES APARIENCIA"),
    ],
    "tsunamis": [
        ("¿Cada terremoto submarino genera un tsunami?", "¿SISMO SUBMARINO = TSUNAMI?"),
        ("La respuesta depende de más que la ubicación.", "NO BASTA CON ESTAR BAJO EL MAR"),
        ("No todos los terremotos submarinos producen tsunamis.", "NO TODOS GENERAN TSUNAMIS"),
        ("Importan el desplazamiento del fondo marino y otros factores.", "IMPORTA CÓMO SE MUEVE EL FONDO"),
        ("No todo temblor bajo el océano levanta una gran ola.", "NO SIEMPRE HAY TSUNAMI"),
    ],
    "falla": [
        ("¿Una falla geológica es simplemente una grieta?", "¿SOLO UNA GRIETA?"),
        ("Hay una diferencia que cambia su significado.", "LA CLAVE ES EL MOVIMIENTO"),
        ("Una falla es una fractura donde las rocas se desplazaron.", "LAS ROCAS SE DESPLAZARON"),
        ("Estudiarla ayuda a entender la deformación de la corteza.", "LA ROCA CONSERVA EVIDENCIAS"),
        ("Una falla también cuenta cómo se movió la Tierra.", "LA ROCA CUENTA SU HISTORIA"),
    ],
    "prediccion": [
        ("¿Podemos conocer el día exacto del próximo terremoto?", "¿SE PUEDE SABER EL DÍA?"),
        ("Una estimación no es una predicción exacta.", "NO CONFUNDAS PROBABILIDAD"),
        ("No existe un método validado que anticipe con certeza fecha, lugar y magnitud.", "NO EXISTE PREDICCIÓN EXACTA"),
        ("La ciencia puede estudiar probabilidades.", "RIESGO NO ES UNA FECHA"),
        ("Desconfía de quien promete una fecha exacta sin pruebas.", "EXIGE EVIDENCIAS"),
    ],
    "sismografo": [
        ("¿Cómo puede una máquina registrar el movimiento del suelo?", "¿CÓMO SE REGISTRA?"),
        ("La respuesta comienza con un instrumento sensible.", "UN SENSOR ESCUCHA AL SUELO"),
        ("Los sismómetros detectan y registran movimientos.", "EL SISMÓMETRO REGISTRA"),
        ("Esos datos ayudan a estudiar terremotos y ondas.", "MOVIMIENTO CONVERTIDO EN DATOS"),
        ("Lo invisible puede dejar un registro.", "LO INVISIBLE DEJA HUELLAS"),
    ],
    "terremoto": [
        ("¿Cómo puede sentirse un terremoto muy lejano?", "¿POR QUÉ SE SIENTE LEJOS?"),
        ("La energía liberada no se queda junto al epicentro.", "LA ENERGÍA VIAJA"),
        ("Las ondas sísmicas se propagan por la Tierra.", "LAS ONDAS CRUZAN DISTANCIAS"),
        ("Algunas recorren enormes distancias y pueden sentirse.", "EL MOVIMIENTO LLEGA LEJOS"),
        ("El epicentro no marca el límite de las ondas.", "EL EPICENTRO NO ES EL LÍMITE"),
    ],
}

# ============================================================
# 5. TEXTOS Y HASHTAGS
# ============================================================

MEMORABLE_CLOSINGS = {
    "ondas": "LAS ONDAS CUENTAN LO QUE NO VEMOS.",
    "magnitud": "UN SOLO SISMO. MUCHAS FORMAS DE SENTIRLO.",
    "replicas": "QUE PARE EL TEMBLOR NO SIGNIFICA QUE TERMINEN LAS RÉPLICAS.",
    "valdivia": "VALDIVIA 1960: UNA FECHA HISTÓRICA.",
    "profundidad": "LA PROFUNDIDAD TAMBIÉN CUENTA.",
    "placas": "LA TIERRA SIGUE MOVIÉNDOSE.",
    "tsunamis": "NO TODO TEMBLOR BAJO EL MAR CREA UNA OLA.",
    "alerta": "DETECTAR NO ES PREDECIR.",
    "falla": "UNA FALLA ES MOVIMIENTO.",
    "prediccion": "PROBABILIDAD NO ES CERTEZA.",
    "sismografo": "LO INVISIBLE DEJA REGISTROS.",
    "terremoto": "LAS ONDAS VIAJAN MÁS ALLÁ.",
}


def episode_hashtags(ep):
    key = norm(ep.get("id", ""))

    tags = [
        "Sismonauta",
        "Sismos",
        "EducacionSismica",
        "Terremotos",
    ]

    topic_tags = {
        "tsunami": [
            "Tsunami",
            "Tsunamis",
            "Maremoto",
            "Oceano",
            "SeguridadCostera",
            "Prevencion",
            "CienciaDelMar",
            "FenomenosNaturales",
        ],
        "general": [
            "Sismologia",
            "Geofisica",
            "CienciaDeLaTierra",
            "Geologia",
            "PrevencionSismica",
            "CulturaSismica",
            "OndasSismicas",
            "RiesgoSismico",
        ]
    }

    if "tsunami" in key:
        tags.extend(topic_tags["tsunami"])
    else:
        tags.extend(topic_tags["general"])

    tags.extend([
        "DivulgacionCientifica",
        "AprendeConSismonauta"
    ])

    unique = list(
        dict.fromkeys(
            tag for tag in tags
            if tag
        )
    )

    return " ".join(
        "#" + tag
        for tag in unique[:14]
    )


def episode_caption_base(ep):
    key = norm(ep.get("id", ""))

    title = str(ep.get("title", "")).strip()
    fact = str(ep.get("fact", "")).strip()
    lesson = str(ep.get("lesson", "")).strip()

    close = MEMORABLE_CLOSINGS.get(
        key, lesson
    )

    if not all((title, fact, lesson, close)):
        raise RuntimeError(
            "Faltan datos editoriales"
        )

    caption = (
        f"{title}\n\n"
        f"{fact}\n\n"
        f"{lesson}\n\n"
        f"{close}\n\n"
        "¿Qué opinas? Te leemos en los comentarios."
    )

    if ep.get("image_credits"):
        caption += (
            "\n\nImágenes de archivo utilizadas "
            "con fines ilustrativos."
        )

    return caption


# ============================================================
# 6. SELECCION DE EPISODIOS
# ============================================================

def generate_angle(topic_data, history, use_ai=True):
    # Los hechos proceden del catalogo documentado.
    # No se generan afirmaciones nuevas con IA.

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

    choices = [
        (style, angle)
        for style in STYLES
        for angle in angles
        if (
            topic_data["id"],
            style,
            angle
        ) not in used
    ]

    if not choices:
        raise RuntimeError(
            "No quedan enfoques locales inéditos"
        )

    style, angle = random.SystemRandom().choice(
        choices
    )

    episode_id = (
        "sismos-"
        + hashlib.sha1(
            (
                topic_data["id"]
                + style
                + angle
            ).encode()
        ).hexdigest()[:12]
    )

    return {
        **topic_data,
        "style": style,
        "angle": angle,
        "episode_id": episode_id,
        "created_at": utcnow(),
        "script": (
            topic_data["title"] + " "
            + topic_data["fact"] + " "
            + topic_data["lesson"]
        ),
        "visuals": [],
        "verified": False,
        "review_note": (
            "Base editorial predefinida. "
            "No equivale a verificación de la "
            "fuente en tiempo real."
        ),
    }


def build_storyboard(ep):
    beats = RETENTION_STORIES.get(
        ep.get("id")
    )

    if not beats or len(beats) != 5:
        raise RuntimeError(
            "Falta storyboard para el tema"
        )

    stages = (
        "pregunta",
        "intriga",
        "revelacion",
        "explicacion",
        "cierre",
    )

    queries = ep.get("queries") or [
        "natural landscape"
    ]

    result = []

    for i, ((narration, overlay), stage) in enumerate(
        zip(beats, stages)
    ):
        if (
            not narration.strip()
            or not overlay.strip()
            or len(overlay.split()) > 12
        ):
            raise RuntimeError(
                "Escena editorial no valida"
            )

        result.append({
            "narration": narration,
            "overlay": overlay,
            "query": queries[
                min(i, len(queries) - 1)
            ],
            "stage": stage,
        })

    result.append({
        "narration": (
            "Dale me gusta y comparte, para seguir "
            "recibiendo más información sobre este "
            "fascinante tema. ¡Muchas gracias!"
        ),
        "overlay": "DALE LIKE",
        "query": queries[-1],
        "stage": "cta",
    })

    return result


# ============================================================
# 7. FOTOGRAFIAS FLEXIBLES
# ============================================================

# Ya no se exige una fotografia que muestre
# directamente el evento geologico.
#
# Se aceptan fotografias naturales como
# contexto visual de divulgacion cientifica.

NATURE_QUERIES = [
    "mountain landscape",
    "rocky hills",
    "forest landscape",
    "ocean coast",
    "sea beach",
    "coastal cliffs",
    "river valley",
    "mountain forest",
    "geological landscape",
    "rock formations",
    "mountain coastline",
    "natural landscape",
]

PHOTO_SEARCH_QUERIES = {
    "ruptura": [
        "earthquake surface rupture",
        "fault scarp",
        "rocky mountains",
    ],
    "deslizamientos": [
        "earthquake landslide",
        "landslide mountain",
        "green mountain hills",
    ],
    "grietas": [
        "ground fissure",
        "rock formations",
        "dry rocky landscape",
    ],
    "transformante": [
        "San Andreas Fault",
        "California mountains",
        "rocky hills",
    ],
    "rift": [
        "East African Rift valley",
        "rift valley",
        "mountain valley",
    ],
    "subduccion": [
        "Andes mountains",
        "Pacific coast mountains",
        "coastal cliffs",
    ],
    "colision": [
        "Himalaya mountains",
        "folded geological rocks",
        "mountain landscape",
    ],
    "falla_inversa": [
        "thrust fault",
        "rock outcrop",
        "mountain hills",
    ],
    "falla_normal": [
        "normal fault",
        "rift valley",
        "mountain landscape",
    ],
    "tsunami_oleadas": [
        "2011 Japan tsunami",
        "ocean waves coast",
        "sea beach",
        "Pacific ocean coast",
    ],
    "tsunami_retroceso": [
        "tsunami receding sea",
        "beach shoreline",
        "exposed seafloor",
        "coastal beach",
        "ocean coast",
    ],
    "tsunami_rios": [
        "tsunami river",
        "river estuary",
        "coastal river",
        "river valley",
    ],
    "tsunami_inundacion": [
        "2011 Japan tsunami flooding",
        "tsunami coastal inundation",
        "ocean shoreline",
        "sandy beach",
        "coastal cliffs",
    ],
    "ondas": [
        "seismograph",
        "mountain geology",
        "rock formations",
    ],
    "magnitud": [
        "earthquake",
        "mountain landscape",
        "rocky hills",
    ],
    "replicas": [
        "earthquake fault",
        "mountain hills",
        "forest landscape",
    ],
    "valdivia": [
        "1960 Valdivia earthquake",
        "Valdivia Chile landscape",
        "Chile forest landscape",
    ],
    "profundidad": [
        "geological mountains",
        "rock formations",
        "mountain landscape",
    ],
    "placas": [
        "San Andreas Fault",
        "mountain landscape",
        "rift valley",
    ],
    "tsunamis": [
        "tsunami coast",
        "ocean shoreline",
        "Pacific coast",
        "sea beach",
    ],
    "alerta": [
        "earthquake landscape",
        "mountain landscape",
        "forest mountains",
    ],
    "falla": [
        "San Andreas Fault",
        "rock outcrop",
        "mountain valley",
    ],
    "prediccion": [
        "geological landscape",
        "rocky hills",
        "forest mountains",
    ],
    "sismografo": [
        "seismograph",
        "mountain landscape",
        "rock formations",
    ],
    "terremoto": [
        "earthquake geology",
        "mountain hills",
        "coastal cliffs",
    ],
}

NATURE_TERMS = (
    "mountain", "mountains", "hill", "hills",
    "forest", "trees", "woodland",
    "beach", "ocean", "sea", "coast",
    "coastal", "shore", "shoreline",
    "cliff", "cliffs", "valley", "river",
    "lake", "landscape", "nature",
    "natural", "terrain", "rock",
    "rocks", "geology", "geological",
    "fault", "rift", "tsunami",
    "earthquake", "desert", "volcano",
    "volcanic", "canyon", "waterfall",
    "island", "bay", "water", "wave",
    "inundation", "flood",
    "montana", "montanas", "cerro",
    "cerros", "bosque", "arboles",
    "playa", "mar", "costa",
    "acantilado", "valle", "rio",
    "lago", "paisaje", "naturaleza",
    "roca", "rocas", "falla",
    "terremoto", "sismo",
    "desierto", "volcan",
    "inundacion",
)

NON_PHOTO_TERMS = (
    "diagram", "infographic", "flowchart",
    "schematic", "schema", "cross section",
    "seismogram", "waveform",
    "graph", "chart", "plot",
    "illustration", "drawing",
    "cartoon", "vector", "icon", "logo",
    "poster", "banner", "slide",
    "screenshot", "screen capture",
    "pdf", "book cover", "document",
    "scanned", "certificate",
    "diagrama", "infografia",
    "esquema", "ilustracion",
    "dibujo", "mapa", "map ",
    "satellite image", "lunar", "mars",
    "moon surface",
)

ALLOWED_PHOTO_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
)


def license_accepted(code):
    code = norm(code).strip().lower()

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


def photo_relevant(item, ep):
    """
    Filtro fotografico flexible.

    Se aceptan paisajes naturales de contexto.
    No exige imagen de un sismo o tsunami real.

    Se excluyen diagramas, mapas, documentos,
    logos e imagenes claramente no fotograficas.
    """

    title = str(
        item.get("title") or ""
    )

    description = str(
        item.get("description") or ""
    )

    tags = str(
        item.get("tags") or ""
    )

    clean_title = norm(
        re.sub(
            r"^File:",
            "",
            title,
            flags=re.I
        ).replace("_", " ")
    )

    description = norm(description)
    tags = norm(tags)

    if not clean_title:
        return False

    if any(
        has_term(clean_title, forbidden)
        for forbidden in NON_PHOTO_TERMS
    ):
        return False

    # Fotografia de un paisaje o fenomeno.
    text = (
        clean_title + " "
        + description + " "
        + tags
    )

    if any(
        has_term(text, term)
        for term in NATURE_TERMS
    ):
        return True

    # Tambien se admite una fotografia
    # natural con metadatos escasos.
    # La búsqueda inicial debe corresponder
    # a una consulta tematica o paisajistica.
    if (
        len(clean_title) >= 8
        and item.get("provider") in (
            "Wikimedia",
            "Openverse",
        )
        and not any(
            has_term(clean_title, word)
            for word in (
                "portrait", "selfie",
                "advertisement", "logo",
                "drawing", "diagram",
                "painting", "illustration",
            )
        )
    ):
        return True

    return False


# ============================================================
# 8. AUDITORIA VISUAL
# ============================================================

def visual_photo_audit(path):
    """
    Verificacion automatica basica.

    No usa OCR ni rechaza playas claras,
    nieve, arena, cielos o bosques.

    Comprueba formato, resolucion y que
    el archivo sea una imagen legible.
    """

    try:
        with Image.open(path) as src:
            image = ImageOps.exif_transpose(
                src
            ).convert("RGB")

            if (
                image.width < 500
                or image.height < 350
            ):
                return (
                    False,
                    "resolucion insuficiente"
                )

            sample = ImageOps.contain(
                image,
                (320, 320)
            )

            gray = sample.convert("L")

            stat = ImageStat.Stat(gray)

            if stat.stddev[0] < 2.0:
                return (
                    False,
                    "imagen casi uniforme"
                )

            return (
                True,
                "fotografia tecnicamente apta"
            )

    except Exception as exc:
        return (
            False,
            "imagen ilegible: "
            + safe_error(exc)[:90]
        )


# ============================================================
# 9. WIKIMEDIA COMMONS PAGINADO
# ============================================================

def commons(query):
    endpoint = (
        "https://commons.wikimedia.org/w/api.php"
    )

    params = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": COMMONS_PAGE_SIZE,
        "prop": "imageinfo",
        "iiprop": "url|extmetadata|size",
        "iiurlwidth": 1400,
    }

    continuation = {}
    seen = set()

    for page_number in range(COMMONS_PAGES):
        request_params = {
            **params,
            **continuation,
        }

        response = HTTP.get(
            endpoint,
            params=request_params,
            timeout=COMMONS_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        pages = (
            payload.get("query", {})
            .get("pages", [])
        )

        if isinstance(pages, dict):
            pages = list(pages.values())

        log(
            "WIKIMEDIA:",
            query,
            "pagina:",
            page_number + 1,
            "candidatos:",
            len(pages),
        )

        for page in pages:
            infos = page.get("imageinfo") or []

            if not infos:
                continue

            info = infos[0]
            meta = info.get("extmetadata") or {}

            def val(key):
                raw = str(
                    (meta.get(key) or {})
                    .get("value") or ""
                )

                raw = re.sub(
                    r"<[^>]+>",
                    " ",
                    raw
                )

                return re.sub(
                    r"\s+",
                    " ",
                    raw
                ).strip()

            license_code = val(
                "LicenseShortName"
            )

            if not license_accepted(
                license_code
            ):
                continue

            title = str(
                page.get("title") or ""
            )

            if not title.lower().endswith(
                ALLOWED_PHOTO_EXTENSIONS
            ):
                continue

            if (
                int(info.get("width") or 0) < 500
                or int(info.get("height") or 0) < 350
            ):
                continue

            url = (
                info.get("thumburl")
                or info.get("url")
            )

            if not url or url in seen:
                continue

            seen.add(url)

            yield {
                "url": url,
                "source": info.get(
                    "descriptionurl", ""
                ),
                "license": license_code,
                "title": title,
                "description": val(
                    "ImageDescription"
                ),
                "creator": (
                    val("Artist")
                    or "Autor no identificado"
                ),
                "tags": "",
                "provider": "Wikimedia",
                "search_query": query,
            }

        continuation = payload.get(
            "continue"
        )

        if not continuation:
            break


# ============================================================
# 10. OPENVERSE
# ============================================================

def openverse(query):
    global _OPENVERSE_UNAVAILABLE_UNTIL

    if not ENABLE_OPENVERSE:
        return

    if time.monotonic() < _OPENVERSE_UNAVAILABLE_UNTIL:
        log(
            "OPENVERSE: temporalmente omitido"
        )
        return

    endpoint = (
        "https://api.openverse.org/v1/images/"
    )

    params = {
        "q": query,
        "license": "cc0,pdm",
        "page_size": 30,
    }

    try:
        response = HTTP.get(
            endpoint,
            params=params,
            timeout=OPENVERSE_TIMEOUT,
        )

        if response.status_code in (
            401, 403, 429
        ):
            _OPENVERSE_UNAVAILABLE_UNTIL = (
                time.monotonic()
                + OPENVERSE_BACKOFF
            )

            log(
                "OPENVERSE: HTTP",
                response.status_code,
                "suspendido temporalmente."
            )
            return

        response.raise_for_status()
        payload = response.json()

    except (
        requests.RequestException,
        ValueError,
    ) as exc:
        _OPENVERSE_UNAVAILABLE_UNTIL = (
            time.monotonic()
            + OPENVERSE_BACKOFF
        )

        log(
            "OPENVERSE no disponible:",
            safe_error(exc),
        )
        return

    for item in payload.get(
        "results", []
    ):
        license_code = str(
            item.get("license") or ""
        )

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
            str(tag.get("name") or "")
            for tag in item.get("tags", [])
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
                item.get("description") or ""
            ),
            "tags": tags,
            "creator": (
                item.get("creator")
                or "Autor no identificado"
            ),
            "provider": "Openverse",
            "search_query": query,
        }


# ============================================================
# 11. DESCARGA CONTROLADA
# ============================================================

def valid_image_url(url):
    parsed = urllib.parse.urlparse(
        url
    )

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

        return bool(addresses) and all(
            ipaddress.ip_address(
                entry[4][0]
            ).is_global
            for entry in addresses
        )

    except Exception:
        return False


def photo_download(item, dest):
    url = str(item["url"])

    if not valid_image_url(url):
        return False

    try:
        for _ in range(5):
            with HTTP.get(
                url,
                timeout=(8, 25),
                stream=True,
                allow_redirects=False,
            ) as response:

                if response.status_code in (
                    301, 302, 303, 307, 308
                ):
                    location = response.headers.get(
                        "Location", ""
                    )

                    url = urllib.parse.urljoin(
                        url, location
                    )

                    if not valid_image_url(url):
                        return False

                    continue

                response.raise_for_status()

                content_type = (
                    response.headers
                    .get("Content-Type", "")
                    .lower()
                )

                if not any(
                    kind in content_type
                    for kind in (
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
                        "JPEG",
                        quality=91,
                    )

                return True

    except Exception as exc:
        log(
            "DESCARGA DESCARTADA:",
            safe_error(exc)[:100],
        )

    return False


# ============================================================
# 12. SELECCION FLEXIBLE DE FOTOGRAFIAS
# ============================================================

def get_photos(ep, folder, local_dir=None):
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
        "accepted": 0,
    }

    if local_dir:
        directory = Path(local_dir)

        if not directory.is_dir():
            raise RuntimeError(
                "No existe --photo-dir"
            )

        for file in sorted(
            directory.iterdir()
        ):
            if file.suffix.lower() not in (
                ALLOWED_PHOTO_EXTENSIONS
            ):
                continue

            okay, _ = visual_photo_audit(
                file
            )

            if okay:
                found.append(file)

                credits.append({
                    "source": str(file),
                    "license": (
                        "Autorización manual requerida"
                    ),
                    "title": file.name,
                    "provider": "Local",
                })

            if len(found) >= MAX_VALID_PHOTOS:
                break

        if not found:
            raise RuntimeError(
                "No hay fotografías válidas "
                "en --photo-dir"
            )

        return found, credits

    queries = list(
        PHOTO_SEARCH_QUERIES.get(
            ep.get("id"), []
        )
    )

    if not queries:
        queries = list(
            ep.get("queries") or []
        )

    # Segunda etapa: paisajes naturales
    # aunque no representen el hecho especifico.
    if "tsunami" in ep.get("id", ""):
        generic = [
            "ocean coast",
            "sandy beach",
            "coastal cliffs",
            "river estuary",
            "mountain coastline",
        ]
    else:
        generic = [
            "mountain landscape",
            "rocky hills",
            "forest landscape",
            "geological landscape",
            "river valley",
        ]

    queries.extend(generic)

    # Evitar consultas repetidas.
    queries = list(
        dict.fromkeys(queries)
    )

    log(
        "BUSQUEDA VISUAL FLEXIBLE:",
        ep["id"],
        queries,
    )

    rejected = []

    for query in queries:
        for provider in (
            commons,
            openverse,
        ):
            if (
                provider is openverse
                and not ENABLE_OPENVERSE
            ):
                continue

            try:
                results = provider(query)

                for item in results:
                    url = item.get("url", "")

                    if not url or url in seen:
                        continue

                    seen.add(url)
                    counts["candidates"] += 1

                    if not photo_relevant(
                        item, ep
                    ):
                        counts[
                            "metadata_rejected"
                        ] += 1

                        if len(rejected) < 5:
                            rejected.append(
                                str(item.get(
                                    "title", ""
                                ))[:100]
                            )
                        continue

                    path = (
                        folder
                        / (
                            "foto_"
                            + str(len(found) + 1)
                            + ".jpg"
                        )
                    )

                    if not photo_download(
                        item, path
                    ):
                        counts[
                            "download_failed"
                        ] += 1
                        continue

                    okay, reason = (
                        visual_photo_audit(path)
                    )

                    if not okay:
                        counts[
                            "visual_rejected"
                        ] += 1

                        path.unlink(
                            missing_ok=True
                        )

                        log(
                            "FOTO DESCARTADA:",
                            reason,
                        )
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
                            "search_query",
                        )
                    })

                    counts["accepted"] += 1

                    log(
                        "FOTO ACEPTADA:",
                        item.get("title"),
                        "| licencia:",
                        item.get("license"),
                    )

                    if (
                        len(found)
                        >= MAX_VALID_PHOTOS
                    ):
                        break

            except Exception as exc:
                log(
                    "PROVEEDOR:",
                    provider.__name__,
                    safe_error(exc)[:140],
                )

            if len(found) >= MAX_VALID_PHOTOS:
                break

        if len(found) >= MAX_VALID_PHOTOS:
            break

    log(
        "AUDITORIA FOTOGRAFIAS:",
        counts,
    )

    if not found and rejected:
        log(
            "TITULOS DESCARTADOS:",
            rejected,
        )

    if not found:
        raise RuntimeError(
            "No se encontraron fotografías auténticas "
            "con licencia CC0/PD."
        )

    if len(found) == 1:
        log(
            "MODO FOTO UNICA:",
            "se usaran encuadres diferentes"
        )
    else:
        log(
            "FOTOS VALIDAS:",
            len(found)
        )

    return found, credits


def local_photo_audit(
    photos,
    credits,
    ep,
    photo_dir
):
    if photo_dir:
        log(
            "FOTOS LOCALES:",
            "verificar derechos de uso."
        )
        return bool(photos)

    return bool(photos) and all(
        license_accepted(
            credit.get("license", "")
        )
        for credit in credits
    )


# ============================================================
# 13. FUENTES Y COMPOSICION VISUAL
# ============================================================

def font(size, bold=True):
    if bold:
        options = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        options = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    for path in options:
        if Path(path).exists():
            return ImageFont.truetype(
                path, size
            )

    return ImageFont.load_default()


def wrap(draw, text, ft, width):
    lines = []
    current = ""

    for word in text.split():
        proposed = (
            current + " " + word
        ).strip()

        if draw.textbbox(
            (0, 0),
            proposed,
            font=ft
        )[2] <= width:
            current = proposed

        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def photo_crop_variant(src, index):
    image = ImageOps.exif_transpose(
        src
    ).convert("RGB")

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
            int(H * zoom)
        ),
        method=Image.Resampling.LANCZOS,
        centering=centering,
    )

    if zoom == 1:
        return fitted

    extra_x = fitted.width - W
    extra_y = fitted.height - H

    left = int(
        extra_x * centering[0]
    )

    top = int(
        extra_y * centering[1]
    )

    return fitted.crop((
        left,
        top,
        left + W,
        top + H,
    ))


def clean_scene(
    photo,
    message,
    dest,
    variant=0
):
    with Image.open(photo) as source:
        im = photo_crop_variant(
            source, variant
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
            fill=(0, 0, 0, alpha)
        )

    im = Image.alpha_composite(
        im.convert("RGBA"),
        veil
    ).convert("RGB")

    draw = ImageDraw.Draw(im)

    message = re.sub(
        r"\s+",
        " ",
        str(message or "")
    )

    message = (
        message.replace("…", "")
        .replace("...", "")
        .strip()
    )

    if not message:
        raise ValueError(
            "Escena sin texto"
        )

    max_width = W - 104
    chosen = None

    for size in range(65, 17, -1):
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
            or (
                len(lines) * line_height
                > H * 0.63
            )
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
            line_height
        )

        break

    if chosen is None:
        raise ValueError(
            "Texto demasiado largo para escena"
        )

    ft, lines, line_height = chosen

    y = (
        H - len(lines) * line_height
    ) // 2

    for line in lines:
        box = draw.textbbox(
            (0, 0),
            line,
            font=ft,
            stroke_width=3
        )

        x = (
            W - (box[2] - box[0])
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
        "JPEG",
        quality=92
    )


# ============================================================
# 14. ICONO ANIMADO DALE LIKE
# ============================================================

def draw_like_icon(
    draw,
    cx,
    cy,
    scale=1.0,
    fill=(255, 255, 255, 255)
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
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius
        ),
        fill=blue
    )

    def pts(items):
        return [
            (
                cx + x * s,
                cy + y * s
            )
            for x, y in items
        ]

    draw.rounded_rectangle(
        (
            cx - 88 * s,
            cy - 3 * s,
            cx - 54 * s,
            cy + 69 * s
        ),
        radius=8 * s,
        fill=white
    )

    draw.polygon(
        pts([
            (-46, 1), (-24, -14),
            (13, -14), (26, -23),
            (47, -20), (58, -11),
            (58, 1), (54, 13),
            (52, 21), (50, 34),
            (47, 42), (45, 55),
            (31, 68), (-45, 68),
        ]),
        fill=white
    )

    draw.polygon(
        pts([
            (-39, 4), (-20, -15),
            (-9, -38), (2, -74),
            (12, -93), (24, -96),
            (32, -88), (34, -75),
            (22, -38), (17, -21),
            (35, -21), (46, -16),
            (47, -7), (40, 3),
        ]),
        fill=white
    )


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
                        W / 2 - 140 - extra,
                        H / 2 - 130 - extra,
                        W / 2 + 140 + extra,
                        H / 2 + 150 + extra,
                    ),
                    fill=(
                        255,
                        255,
                        255,
                        opacity if visible else 0
                    ),
                )

            img = Image.alpha_composite(
                img,
                halo
            )

            draw = ImageDraw.Draw(img)

            draw_like_icon(
                draw,
                W / 2,
                H // 2 - 40,
                scale=1.20 * pulse,
                fill=(
                    255, 255, 255,
                    255 if visible else 82
                ),
            )

            text = "DALE LIKE"
            ft = font(54)

            box = draw.textbbox(
                (0, 0),
                text,
                font=ft,
                stroke_width=3
            )

            x = (
                W - (box[2] - box[0])
            ) / 2

            draw.text(
                (x, H - 160),
                text,
                font=ft,
                fill="white",
                stroke_width=3,
                stroke_fill="#09131C"
            )

            img.convert("RGB").save(
                tmpdir / f"frame_{n:04d}.png"
            )

        ffmpeg([
            "-framerate", str(FPS),
            "-i", tmpdir / "frame_%04d.png",
            "-t", str(duration),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            dest_video,
        ])

    finally:
        shutil.rmtree(
            tmpdir,
            ignore_errors=True
        )


# ============================================================
# 15. MUSICA ORIGINAL
# ============================================================

def soundtrack(path, secs=45, sr=22050):
    import array

    n = int(
        math.ceil(secs * sr)
    )

    buf = array.array("h")

    notes = (
        130.81,
        164.81,
        196.00,
        261.63,
    )

    for i in range(n):
        t = i / sr

        fade = max(
            0.0,
            min(
                1.0,
                t / 1.3,
                (secs - t) / 2.0
            )
        )

        note = notes[
            int(t * 2) % 4
        ]

        pad = (
            0.36 * math.sin(
                2 * math.pi * 130.81 * t
            )
            + 0.22 * math.sin(
                2 * math.pi * 196 * t
            )
            + 0.13 * math.sin(
                2 * math.pi * 261.63 * t
            )
        )

        pulse = (
            0.15
            * math.sin(
                2 * math.pi * note * t
            )
            * (
                0.5
                + 0.5 * math.cos(
                    2 * math.pi * 2 * t
                )
            )
        )

        shimmer = (
            0.07
            * math.sin(
                2 * math.pi * 392 * t
            )
            * (
                0.5
                + 0.5 * math.sin(
                    2 * math.pi * t / 8
                )
            )
        )

        value = max(
            -1.0,
            min(
                1.0,
                (
                    pad + pulse + shimmer
                ) * 0.55 * fade
            )
        )

        buf.append(
            int(32767 * value)
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
# 16. VOZ NEURAL
# ============================================================

async def speak(text, out):
    import edge_tts

    await edge_tts.Communicate(
        text,
        voice=VOICE,
        rate="+8%"
    ).save(str(out))

    if (
        not out.exists()
        or out.stat().st_size < 1000
    ):
        raise RuntimeError(
            "No se genero la voz"
        )


# ============================================================
# 17. GENERACION DEL VIDEO
# ============================================================

def render(
    ep,
    out,
    photo_dir=None,
    voice=True
):
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

    # Obtener fotografias antes de gastar
    # tiempo en voces y renderizado.
    photos, credits = get_photos(
        ep,
        folder,
        photo_dir
    )

    if not local_photo_audit(
        photos, credits, ep, photo_dir
    ):
        raise RuntimeError(
            "Fotografías sin licencia "
            "o validez suficiente"
        )

    if len(photos) < MIN_VALID_PHOTOS:
        raise RuntimeError(
            "No hay fotografías auténticas"
        )

    ep["image_credits"] = credits

    durations = []
    voices = []

    for i, beat in enumerate(storyboard):
        if voice:
            clip = (
                folder
                / f"voz_escena_{i:02d}.mp3"
            )

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

        else:
            seconds = 0.0

        words = len(
            beat["overlay"].split()
        )

        stage = beat.get(
            "stage", ""
        )

        if stage == "pregunta":
            minimum, rate = 3.6, 2.7

        elif stage == "intriga":
            minimum, rate = 3.0, 2.6

        elif stage == "cta":
            minimum, rate = 5.4, 2.3

        else:
            minimum, rate = 4.7, 2.15

        duration = max(
            seconds + 0.55,
            minimum,
            words / rate + 0.7
        )

        durations.append(
            round(duration, 2)
        )

    total = round(
        sum(durations),
        2
    )

    log(
        "VOZ POR ESCENA:",
        durations,
        "TOTAL:",
        total
    )

    media = []
    cover = None
    photo_index = 0

    for i, beat in enumerate(storyboard):
        if beat["stage"] == "cta":
            clip = (
                folder
                / f"escena_{i:02d}.mp4"
            )

            cta_scene_video(
                clip,
                durations[i]
            )

            media.append({
                "kind": "video",
                "path": clip,
                "duration": durations[i],
            })

            continue

        frame = (
            folder
            / f"escena_{i:02d}.jpg"
        )

        photo = photos[
            photo_index % len(photos)
        ]

        clean_scene(
            photo,
            beat["overlay"],
            frame,
            variant=i,
        )

        media.append({
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

        log(
            "ESCENA-FOTO:",
            i + 1,
            "|",
            photo.name,
            "| encuadre",
            i + 1
        )

        photo_index += 1

    if cover is None:
        raise RuntimeError(
            "Portada no generada"
        )

    music = (
        folder / "musica.wav"
    )

    soundtrack(
        music,
        secs=total + 1
    )

    inputs = []

    for item in media:
        if item["kind"] == "image":
            inputs.extend([
                "-loop", "1",
                "-framerate", str(FPS),
                "-t", str(item["duration"]),
                "-i", item["path"],
            ])
        else:
            inputs.extend([
                "-i", item["path"],
            ])

    media_count = len(media)

    inputs.extend([
        "-i", music
    ])

    voice_start = media_count + 1

    for clip in voices:
        inputs.extend([
            "-i", clip
        ])

    filters = []

    for i, item in enumerate(media):
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
            for i in range(media_count)
        )
        + f"concat=n={media_count}:v=1:a=0[v]"
    )

    if voice:
        for i, duration in enumerate(
            durations
        ):
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
                for i in range(len(voices))
            )
            + f"concat=n={len(voices)}:"
            "v=0:a=1[narr]"
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
        "-map", "[v]",
        "-map", "[a]",
        "-t", str(total),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
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
            "musica": "instrumental_original",
            "fotografias_distintas": len(photos),
            "encuadres": len(storyboard) - 1,
        }
    )

    log(
        "VIDEO:",
        movie,
        "PORTADA:",
        cover,
        "VOZ:",
        voice,
    )

    return movie, cover, credits


# ============================================================
# 18. GITHUB - ESTADO PERSISTENTE
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
            data["sha"]
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
                "El repositorio debe ser publico "
                "para las URLs multimedia"
            )


# ============================================================
# 19. INSTAGRAM GRAPH API
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

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Instagram no respondio JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        raise RuntimeError(
            f"Instagram {response.status_code}: "
            + str(data)[:350]
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
                "fields": "status_code,status"
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
                "Instagram procesamiento fallido: "
                + str(status)
            )

    else:
        raise RuntimeError(
            "Instagram: procesamiento incierto"
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
            "Cuenta esperada incorrecta"
        )

    token = os.getenv(
        "INSTAGRAM_TOKEN", ""
    )

    user = os.getenv(
        "INSTAGRAM_USER_ID", ""
    )

    if not token or not user:
        raise RuntimeError(
            "Faltan credenciales Instagram"
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
            "El token no pertenece a @sismonauta"
        )

    log(
        "TOKEN Instagram válido;",
        "cuenta verificada: @sismonauta"
    )


# ============================================================
# 20. FACEBOOK GRAPH API
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
            "Facebook no respondio JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get("error") or {}

        raise RuntimeError(
            f"Facebook HTTP {response.status_code}: "
            + str(
                error.get("message", data)
            )[:250]
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
            "Faltan credenciales Facebook"
        )

    if not page.isdigit():
        raise RuntimeError(
            "FACEBOOK_PAGE_ID debe ser numerico"
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
            "Token de fanpage incorrecto"
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

    log(
        "PAGINA Facebook validada:",
        info.get("name", "")
    )

    return page, token


# ============================================================
# 21. FACEBOOK REEL
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
            "Facebook Reel: sesion incompleta"
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
        }
    )

    if result.get("success") is not True:
        raise RuntimeError(
            "Facebook Reel: finalizacion incierta"
        )

    return video_id


# ============================================================
# 22. FACEBOOK STORY
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
            "Facebook Story: sesion incompleta"
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
            "Facebook Story: finalizacion incierta"
        )

    return video_id


# ============================================================
# 23. FACEBOOK FEED
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
        error = result.get("error") or {}

        raise RuntimeError(
            "Facebook Feed: "
            + str(
                error.get("message", result)
            )[:250]
        )

    video_id = str(
        result.get("id") or ""
    )

    if not video_id.isdigit():
        raise RuntimeError(
            "Facebook Feed: sin ID"
        )

    return video_id


# ============================================================
# 24. HISTORIAL ANTIDUPLICADOS
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

    for item in TOPICS:
        for style in STYLES:
            for angle in angles:
                old_id = (
                    "sismos-"
                    + hashlib.sha1(
                        (
                            item["id"]
                            + style
                            + angle
                        ).encode()
                    ).hexdigest()[:12]
                )

                if old_id == episode_id:
                    return item["id"]

    return None


def remote_published_topics(state, history):
    used = set()

    keys = (
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
            for key in keys
        ):
            continue

        topic_id = (
            row.get("topic")
            or legacy_topic_for_episode(
                episode_id
            )
        )

        if not topic_id:
            topic_id = next(
                (
                    item.get("topic")
                    for item in history.get(
                        "items", []
                    )
                    if item.get("episode_id")
                    == episode_id
                ),
                None
            )

        if not topic_id:
            raise RuntimeError(
                "Historial sin tema: "
                + episode_id
            )

        used.add(topic_id)

    for item in history.get(
        "items", []
    ):
        if (
            item.get("status") == "publicado"
            and item.get("topic")
        ):
            used.add(
                item["topic"]
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
            item
            for item in TOPICS
            if item["id"] == requested
        ]

        if not matches:
            raise ValueError(
                "Tema desconocido: " + requested
            )

        return matches[0]

    # Correccion:
    # Los 25 temas estan disponibles
    # para seleccion automatica.
    candidates = [
        item
        for item in TOPICS
        if item["id"] not in excluded
    ]

    if not candidates:
        raise RuntimeError(
            "CATALOGO_AGOTADO: no quedan "
            "temas cientificos ineditos"
        )

    counts = {
        item["id"]: 0
        for item in candidates
    }

    for item in history.get(
        "items", []
    ):
        topic_id = item.get(
            "topic"
        )

        if topic_id in counts:
            counts[topic_id] += 1

    minimum = min(counts.values())

    return random.SystemRandom().choice([
        item
        for item in candidates
        if counts[item["id"]] == minimum
    ])


# ============================================================
# 25. PUBLICACION EN LAS CINCO PLATAFORMAS
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
            "Publicacion automatica no autorizada"
        )

    token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    user = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    verify_instagram_account()

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
        ep["episode_id"],
        {}
    )

    keys = (
        "reel",
        "story",
        "facebook_reel",
        "facebook_story",
        "facebook_feed",
    )

    for old_id, old_row in items.items():
        if (
            old_id == ep["episode_id"]
            or not isinstance(old_row, dict)
        ):
            continue

        old_topic = (
            old_row.get("topic")
            or legacy_topic_for_episode(old_id)
        )

        if (
            old_topic == ep.get("id")
            and any(
                old_row.get(k) in (
                    "publicado",
                    "enviando",
                    "incierto",
                )
                for k in keys
            )
        ):
            raise RuntimeError(
                "Tema ya publicado o en proceso: "
                + str(old_topic)
            )

    row.setdefault(
        "topic",
        ep.get("id")
    )

    destinations = ["reel"]

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
        status = row.get(dest)

        if status in (
            "enviando",
            "incierto"
        ):
            raise RuntimeError(
                f"{dest}: envio anterior incierto"
            )

        if (
            status == "publicado"
            and not str(
                row.get(dest + "_id") or ""
            ).isdigit()
        ):
            raise RuntimeError(
                f"{dest}: publicado sin ID valido"
            )

        if status not in (
            None,
            "publicado"
        ):
            raise RuntimeError(
                "Estado del historial desconocido"
            )

    log(
        "REANUDACION SEGURA:",
        {
            k: row.get(k, "pendiente")
            for k in destinations
        }
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
        "reel_published": (
            row.get("reel") == "publicado"
        ),
        "story_requested": bool(story),
        "story_published": (
            row.get("story") == "publicado"
        ),
        "story_id": row.get("story_id"),
        "facebook_reel_requested": bool(
            facebook
        ),
        "facebook_reel_published": (
            row.get("facebook_reel") == "publicado"
        ),
        "facebook_reel_id": row.get(
            "facebook_reel_id"
        ),
        "facebook_story_requested": bool(
            facebook and story
        ),
        "facebook_story_published": (
            row.get("facebook_story") == "publicado"
        ),
        "facebook_story_id": row.get(
            "facebook_story_id"
        ),
        "facebook_feed_requested": bool(
            facebook
        ),
        "facebook_feed_published": (
            row.get("facebook_feed") == "publicado"
        ),
        "facebook_feed_id": row.get(
            "facebook_feed_id"
        ),
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

        confirmation["confirmed_at"] = (
            utcnow()
        )

        write_json_atomic(
            receipt_path,
            confirmation
        )

        log(
            which.upper(),
            "ACEPTADO POR META:",
            media_id
        )

    write_json_atomic(
        receipt_path,
        confirmation
    )

    credits = ep.get(
        "image_credits", []
    )

    if not credits:
        raise RuntimeError(
            "Sin fotografias verificadas"
        )

    if any(
        not license_accepted(
            c.get("license", "")
        )
        for c in credits
    ):
        raise RuntimeError(
            "Sin licencia CC0/PD validada"
        )

    caption = (
        episode_caption_base(ep)
        + "\n\n"
        + episode_hashtags(ep)
    )

    if len(caption) > 2200:
        raise RuntimeError(
            "Descripcion demasiado larga"
        )

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
                    "Colision de archivo multimedia"
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

            if (
                len(response.content)
                != movie.stat().st_size
            ):
                raise RuntimeError(
                    "URL MP4 incompleta"
                )

    # Instagram Reel + Story.
    for which in (
        "reel",
        "story",
    ):
        if which not in destinations:
            continue

        if row.get(which) == "publicado":
            log(
                which.upper(),
                "YA PUBLICADO"
            )
            continue

        save("enviando", which)

        try:
            media_id = ig_publish(
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
            save("incierto", which)
            raise

        confirm(which, media_id)

    # Facebook Reel + Story + Feed.
    if facebook:
        page, fb_token = (
            verify_facebook_page()
        )

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
                log(
                    which.upper(),
                    "YA PUBLICADO"
                )
                continue

            save("enviando", which)

            try:
                if which == "facebook_reel":
                    media_id = fb_publish_reel(
                        page,
                        fb_token,
                        movie,
                        caption,
                        ep["episode_id"],
                    )

                elif which == "facebook_story":
                    media_id = fb_publish_story(
                        page,
                        fb_token,
                        movie,
                    )

                else:
                    media_id = fb_publish_feed_video(
                        page,
                        fb_token,
                        movie,
                        caption,
                        ep["episode_id"],
                    )

            except BaseException:
                save("incierto", which)

                confirmation[
                    which + "_uncertain"
                ] = True

                write_json_atomic(
                    receipt_path,
                    confirmation
                )
                raise

            confirm(which, media_id)

    log(
        "PUBLICACIONES SOLICITADAS A META;",
        "recibo:",
        receipt_path
    )


# ============================================================
# 26. METRICAS
# ============================================================

def metrics(csv_path):
    import csv

    with open(
        csv_path,
        encoding="utf-8-sig",
        newline=""
    ) as fh:
        for row in csv.DictReader(fh):
            plays = int(row["plays"])
            views = int(row["views_7s"])

            if (
                plays < 0
                or views < 0
                or views > plays
            ):
                raise ValueError(
                    "Metricas inconsistentes"
                )

            rate = (
                views / plays
                if plays else 0
            )

            result = (
                "ALCANZADO"
                if rate >= TARGET_R7
                else "NO ALCANZADO"
            )

            log(
                f"{row['episode_id']}: "
                f"R7={rate:.1%} | "
                f"objetivo=70% | {result}"
            )


# ============================================================
# 27. PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA @sismonauta: "
            "videos educativos con paisajes reales"
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
        for item in TOPICS:
            log(
                item["id"],
                item["title"]
            )
        return 0

    if args.metrics_csv:
        metrics(args.metrics_csv)
        return 0

    if (
        args.auto_publish
        and not args.publish
    ):
        raise RuntimeError(
            "--auto-publish requiere --publish"
        )

    if (
        args.auto_publish
        and args.episode_json
    ):
        raise RuntimeError(
            "--auto-publish no admite "
            "--episode-json"
        )

    out = Path(args.out)

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
        {"items": []}
    )

    # Leer historial editorial remoto.
    if (
        os.getenv("GH_TOKEN")
        and os.getenv("GITHUB_REPOSITORY")
    ):
        try:
            gh = Github()

            raw, _ = gh.read(
                EDITORIAL_PATH
            )

            if raw:
                history = json.loads(raw)

        except Exception as exc:
            log(
                "Historial editorial no disponible:",
                safe_error(exc)
            )

    history.setdefault(
        "items", []
    )

    published_topics = set()

    if args.publish:
        gh_state = Github()

        raw_state, _ = gh_state.read(
            STATE_PATH
        )

        live_state = (
            json.loads(raw_state)
            if raw_state
            else {"episodes": {}}
        )

        published_topics = (
            remote_published_topics(
                live_state,
                history
            )
        )

        log(
            "ANTIDUPLICADOS:",
            sorted(published_topics)
        )

        verify_instagram_account()

        if not args.no_facebook:
            verify_facebook_page()

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
                    ep.get(field)
                    for field in (
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
                    "JSON de episodio invalido"
                )

            if not str(
                ep["episode_id"]
            ).startswith("sismos-"):
                raise RuntimeError(
                    "Episodio ajeno a SISMONAUTA"
                )

            topic_data = {
                "id": ep.get("id", "revisado")
            }

        else:
            try:
                topic_data = choose_topic(
                    history,
                    args.topic,
                    published_topics | tried_visual
                )

            except RuntimeError as exc:
                if (
                    tried_visual
                    and not args.topic
                    and "CATALOGO_AGOTADO" in str(exc)
                ):
                    report = {
                        "status": (
                            "skipped_no_valid_images"
                        ),
                        "reason": (
                            "Sin fotografias verificadas "
                            "para los temas ineditos"
                        ),
                        "discarded_topics": sorted(
                            tried_visual
                        ),
                        "published": False,
                        "checked_at": utcnow(),
                    }

                    write_json_atomic(
                        out / "estado_ejecucion.json",
                        report
                    )

                    log(
                        "SIN PUBLICACION:",
                        report["reason"]
                    )

                    log(
                        "TEMAS DESCARTADOS:",
                        report["discarded_topics"]
                    )

                    return 0

                raise

            ep = generate_angle(
                topic_data,
                history,
                use_ai=not args.no_ai
            )

            ep["base_verified"] = bool(
                args.auto_publish
                and topic_data.get("source")
                and topic_data.get("fact")
                and topic_data.get("lesson")
            )

            ep["editorial_check"] = (
                "Contenido basado en la base editorial "
                "documentada. Fuentes no verificadas "
                "en tiempo real."
            )

        episode = ep["episode_id"]

        write_json_atomic(
            out / (
                episode + "_guion.json"
            ),
            ep
        )

        log(
            "EPISODIO:",
            episode,
            "TEMA:",
            ep["title"],
            "FUENTE:",
            ep["source"]
        )

        if (
            args.publish
            and not args.auto_publish
            and args.approved_id != episode
        ):
            raise RuntimeError(
                "No se publica sin --approved-id "
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
                "Falta fuente editorial"
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
            message = str(exc)

            photo_issue = any(
                text in message
                for text in (
                    "No se encontraron fotografías auténticas",
                    "No hay fotografías auténticas",
                    "Fotografías sin licencia",
                    "Fotografías sin pertinencia",
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
                topic_data["id"]
            )

            log(
                "REINTENTO VISUAL:",
                topic_data["id"],
                safe_error(exc)
            )

    ep["image_credits"] = credits

    write_json_atomic(
        out / (
            episode + "_guion.json"
        ),
        ep
    )

    if not any(
        item.get("episode_id") == episode
        for item in history["items"]
    ):
        history["items"].append({
            "episode_id": episode,
            "topic": topic_data["id"],
            "style": ep.get("style", ""),
            "angle": ep.get("angle", ""),
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

        for item in history["items"]:
            if item.get("episode_id") == episode:
                item["status"] = "publicado"

        write_json_atomic(
            history_path,
            history
        )

        gh = Github()

        raw, sha = gh.read(
            EDITORIAL_PATH
        )

        gh.write(
            EDITORIAL_PATH,
            json.dumps(
                history,
                ensure_ascii=False,
                indent=2
            ).encode(),
            sha
        )

    else:
        log(
            "VISTA PREVIA:",
            "no se publico nada"
        )

    write_json_atomic(
        out / "estado_ejecucion.json",
        {
            "status": (
                "published"
                if args.publish
                else "preview_only"
            ),
            "episode_id": episode,
            "topic": topic_data["id"],
            "photos": len(credits),
            "published": bool(args.publish),
            "checked_at": utcnow(),
        }
    )

    log(
        "OBJETIVO R7:",
        TARGET_R7,
        "(experimental)"
    )

    return 0


# ============================================================
# 28. INICIO
# ============================================================

if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as exc:
        log(
            "ERROR:",
            safe_error(exc)
        )

        sys.exit(1)
