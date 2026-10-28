
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA - NOTICIAS CIENTIFICAS
=================================
Python 3.11+

NOTICIAS PERMITIDAS:
- Investigaciones sobre sismos y tectonica
- Volcanes, geologia y geofisica
- Meteorologia y fenomenos atmosfericos
- Climatologia y glaciares
- Oceanos, tsunamis y oceanografia
- Espacio, astronomia y satelites
- Desastres y riesgos naturales

PROHIBIDO:
- Reportar la ocurrencia de terremotos y temblores
- Publicar fichas de catalogos sismicos
- Publicar avisos de magnitud, epicentro y profundidad
- Inventar informacion cientifica

REDES:
- Instagram @sismonauta
- Facebook Page Sismonauta

CARACTERISTICAS:
- Noticias ultimas 24 horas
- RSS Google News espanol e ingles
- Traduccion al espanol
- Gemini, OpenRouter y Groq opcionales
- Explicaciones con informacion disponible
- Imagenes Wikimedia Commons CC0/PD
- Fondo grafico original como respaldo
- Formato 1080 x 1350
- Banner azul y letras blancas
- Historial persistente GitHub
- Deduplicacion por noticia y plataforma
- Proteccion de publicaciones inciertas
- Pruebas obligatorias del filtro sismico

INSTALACION:
    pip install requests pillow

VISTA PREVIA:
    python noticias_sismonauta.py --out output_noticias

PUBLICAR:
    python noticias_sismonauta.py --publish --out output_noticias

PRUEBA DEL FILTRO:
    python noticias_sismonauta.py --test-filter

VARIABLES PARA PUBLICAR:
    GH_TOKEN
    GITHUB_REPOSITORY
    GITHUB_REF_NAME
    INSTAGRAM_TOKEN
    INSTAGRAM_USER_ID
    FACEBOOK_PAGE_ID
    FACEBOOK_PAGE_TOKEN

OPCIONALES:
    GEMINI_API_KEY
    OPENROUTER_API_KEY
    GROQ_API_KEY
    IG_GRAPH_VERSION
    IG_GRAPH_BASE
    NEWS_MAX_ARTICLES
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


# ============================================================
# 1. CONFIGURACION
# ============================================================

UA = "SismonautaNews/4.2"

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": UA,
    "Accept": "*/*",
})

VERSION = os.getenv(
    "IG_GRAPH_VERSION", "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com",
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

ACCOUNT = "sismonauta"

# Mantener exactamente esta ruta.
STATE_FILE = "sismonauta_noticias/state.json"

LOOKBACK_HOURS = 24

MAX_ARTICLES = max(
    1,
    int(os.getenv("NEWS_MAX_ARTICLES", "100"))
)

MIN_DESCRIPTION_WORDS = 12

WIDTH = 1080
HEIGHT = 1350
MAX_FEED_ITEMS = 80

HASHTAGS_BASE = (
    "#SISMONAUTA #Ciencia #Actualidad "
    "#Naturaleza #Investigacion "
    "#PlanetaTierra #DivulgacionCientifica"
)


# ============================================================
# 2. UTILIDADES
# ============================================================

def log(*values):
    print(*values, flush=True)


def clean(value):
    value = html.unescape(
        re.sub(
            r"<[^>]*>",
            " ",
            str(value or ""),
        )
    )
    return re.sub(r"\s+", " ", value).strip()


def normalized(value):
    text = unicodedata.normalize(
        "NFKD", clean(value).lower()
    )
    return "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )


def word_count(value):
    return len(clean(value).split())


def utcnow():
    return datetime.now(timezone.utc)


def iso_now():
    return utcnow().isoformat()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    tmp.replace(path)


def safe_error(exc):
    message = str(exc)
    for name in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    ):
        secret = os.getenv(name, "")
        if secret:
            message = message.replace(secret, "***")
    return message[:300]


def get(url, **kwargs):
    timeout = kwargs.pop("timeout", 25)
    response = HTTP.get(
        url,
        timeout=timeout,
        **kwargs,
    )
    response.raise_for_status()
    return response


def article_fingerprint(title):
    value = re.sub(
        r"[^a-z0-9]+",
        "",
        normalized(title),
    )
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:20]


# ============================================================
# 3. BUSQUEDAS RSS
# ============================================================

SEARCH_GROUPS = {
    "sismos": [
        ("es", "sismología investigación"),
        ("es", "fallas tectónicas"),
        ("es", "alerta sísmica tecnología"),
        ("es", "geofísica terremotos"),
        ("en", "earthquake seismology research"),
        ("en", "tectonic fault science"),
        ("en", "seismic early warning"),
        ("en", "earthquake monitoring technology"),
    ],

    "volcanes": [
        ("es", "volcanes erupciones"),
        ("es", "actividad volcánica"),
        ("es", "vigilancia volcánica"),
        ("es", "investigación volcanes"),
        ("en", "volcano eruption"),
        ("en", "volcanic activity"),
        ("en", "volcano monitoring"),
        ("en", "volcanology discovery"),
    ],

    "meteorologia": [
        ("es", "meteorología tormentas"),
        ("es", "huracanes ciclones"),
        ("es", "fenómenos meteorológicos"),
        ("es", "pronóstico meteorológico"),
        ("en", "meteorology weather research"),
        ("en", "hurricane cyclone"),
        ("en", "extreme weather"),
        ("en", "atmospheric science"),
    ],

    "climatologia": [
        ("es", "clima investigación"),
        ("es", "cambio climático"),
        ("es", "glaciares científicos"),
        ("es", "temperatura global clima"),
        ("en", "climate science"),
        ("en", "climate research"),
        ("en", "glacier research"),
        ("en", "global temperature climate"),
    ],

    "oceanos": [
        ("es", "océanos investigación"),
        ("es", "tsunami océano"),
        ("es", "corrientes oceánicas"),
        ("es", "nivel del mar investigación"),
        ("en", "ocean science"),
        ("en", "oceanography discovery"),
        ("en", "tsunami research"),
        ("en", "sea level research"),
    ],

    "espacio": [
        ("es", "astronomía descubrimiento"),
        ("es", "NASA espacio"),
        ("es", "satélites observación terrestre"),
        ("es", "exploración espacial"),
        ("en", "NASA space science"),
        ("en", "astronomy discovery"),
        ("en", "Earth observation satellite"),
        ("en", "space exploration science"),
    ],

    "desastres": [
        ("es", "desastres naturales"),
        ("es", "deslizamientos de tierra"),
        ("es", "inundaciones prevención"),
        ("es", "riesgos naturales"),
        ("en", "natural hazards"),
        ("en", "landslide flooding"),
        ("en", "disaster risk science"),
        ("en", "natural disaster monitoring"),
    ],

    "geologia": [
        ("es", "geología descubrimiento"),
        ("es", "geofísica investigación"),
        ("es", "formación de montañas"),
        ("en", "geology discovery"),
        ("en", "geophysics research"),
        ("en", "Earth science discovery"),
        ("en", "geological research"),
    ],
}


# ============================================================
# 4. FILTROS TEMATICOS
# ============================================================

CATEGORY_PATTERNS = {
    "sismos": re.compile(
        r"sism\w*|s[ií]smic\w*|seism\w*|"
        r"earthquak\w*|terremot\w*|"
        r"tect[oó]nic\w*|tectoni\w*|"
        r"falla geol[oó]gica|fault zone|"
        r"subduction|subducci[oó]n",
        re.I,
    ),

    "volcanes": re.compile(
        r"volc[aá]n\w*|volcan\w*|"
        r"volcanic\w*|erupci[oó]n\w*|"
        r"eruption\w*|lava\b|magma\w*|"
        r"ceniza volc[aá]nica|ash plume",
        re.I,
    ),

    "meteorologia": re.compile(
        r"meteorolog\w*|weather\b|"
        r"storm\w*|torment\w*|"
        r"hurac[aá]n\w*|hurricane\w*|"
        r"cicl[oó]n\w*|cyclone\w*|"
        r"tornado\w*|atmosf[eé]ric\w*|"
        r"atmospher\w*|precipitaci[oó]n\w*|"
        r"rainfall|forecast\w*|"
        r"pron[oó]stic\w*",
        re.I,
    ),

    "climatologia": re.compile(
        r"climat\w*|climate\w*|"
        r"calentamiento global|global warming|"
        r"glaciar\w*|glacier\w*|"
        r"criosfera|cryosphere|"
        r"temperatura global|global temperature|"
        r"deshielo|ice melt",
        re.I,
    ),

    "oceanos": re.compile(
        r"oc[eé]an\w*|ocean\w*|"
        r"oceanograf\w*|oceanograph\w*|"
        r"tsunami\w*|maremot\w*|"
        r"corrientes marinas|ocean currents|"
        r"nivel del mar|sea level|"
        r"fondo marino|seafloor|"
        r"arrecife\w*|coral reef",
        re.I,
    ),

    "espacio": re.compile(
        r"astronom\w*|astronomy|"
        r"espacio exterior|outer space|"
        r"exploraci[oó]n espacial|"
        r"space exploration|NASA\b|ESA\b|"
        r"sat[eé]lit\w*|satellite\w*|"
        r"planet\w*|planeta\w*|"
        r"galaxi\w*|galaxy|telescop\w*|"
        r"asteroid\w*|asteroide\w*|"
        r"solar system|sistema solar|"
        r"cosmic\w*|c[oó]smic\w*",
        re.I,
    ),

    "desastres": re.compile(
        r"desastre\w* natural\w*|"
        r"natural disaster\w*|"
        r"deslizamiento\w*|landslide\w*|"
        r"inundaci[oó]n\w*|flood\w*|"
        r"alud\w*|avalanche\w*|"
        r"incendio\w* forestal\w*|"
        r"wildfire\w*|riesgo natural|"
        r"natural hazard\w*|"
        r"emergencia clim[aá]tica|"
        r"extreme weather",
        re.I,
    ),

    "geologia": re.compile(
        r"geolog\w*|ge[oó]log\w*|"
        r"geophysic\w*|geof[ií]sic\w*|"
        r"corteza terrestre|earth crust|"
        r"rock formation\w*|"
        r"formaci[oó]n rocosa|"
        r"geological\w*|geomorfolog\w*|"
        r"tectonic\w*|continental drift",
        re.I,
    ),
}

ADVANCE = re.compile(
    r"estudi\w*|investig\w*|"
    r"cient[ií]fic\w*|scientis\w*|"
    r"research\w*|discover\w*|"
    r"descubr\w*|hallazgo\w*|"
    r"an[aá]lis\w*|analys\w*|"
    r"tecnolog\w*|technolog\w*|"
    r"monitor\w*|observ\w*|detect\w*|"
    r"sistema\w*|system\w*|"
    r"sat[eé]lit\w*|satellite\w*|"
    r"medici[oó]n\w*|measurement\w*|"
    r"alerta\w*|warning\w*|"
    r"model\w*|m[eé]todo\w*|"
    r"pron[oó]stic\w*|forecast\w*|"
    r"publicad\w*|published|nuev\w*|new\b",
    re.I,
)

SPAM = re.compile(
    r"hor[oó]scop\w*|astrolog\w*|"
    r"conspiracy|conspiraci[oó]n\w*|"
    r"apocalipsis|loter[ií]a|lottery|"
    r"apuesta\w*|betting|"
    r"oferta comercial|promoci[oó]n comercial",
    re.I,
)

MISLEADING = re.compile(
    r"predicen el pr[oó]ximo terremoto|"
    r"exact earthquake prediction|"
    r"fecha exacta del pr[oó]ximo sismo|"
    r"earthquake prediction date",
    re.I,
)


# ============================================================
# 5. BLOQUEO REFORZADO DE OCURRENCIAS
# ============================================================

# Se normaliza el texto a minusculas y
# sin tildes antes de aplicar las reglas.

QUAKE_TERM = re.compile(
    r"\b(?:"
    r"sismos?|tembl(?:or|ores)|terremotos?|"
    r"earthquakes?|quakes?|"
    r"seismic\s+(?:event|tremor|shock)"
    r")\b",
    re.I,
)

QUAKE_MAGNITUDE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"mag(?:nitud|nitude)?\.?\s*"
    r"(?:de\s+|of\s+)?[:=]?\s*"
    r"[0-9](?:[.,][0-9]+)?"
    r"|"
    r"m(?:w|l|b|s)?\s*[:=]?\s*"
    r"[3-9](?:[.,][0-9]+)?"
    r")\b",
    re.I,
)

QUAKE_REPORT = re.compile(
    r"\b(?:"
    r"sacud\w*|remec\w*|ocurr\w*|"
    r"registr\w*|report\w*|detect\w*|"
    r"sentid\w*|sinti\w*|"
    r"strik\w*|struck|hits?|shak\w*|"
    r"shook|felt|occurred|recorded|"
    r"detected|reported|"
    r"hoy|today|ayer|yesterday"
    r")\b",
    re.I,
)

QUAKE_LOCATION = re.compile(
    r"\b(?:"
    r"epicentro|hipocentro|profundidad|depth|"
    r"km|kilometros?|"
    r"near|offshore|cerca de|frente a|"
    r"noreste|noroeste|sureste|suroeste|"
    r"north|south|east|west|"
    r"latitud|longitud|latitude|longitude"
    r")\b",
    re.I,
)

QUAKE_HEADLINE = re.compile(
    r"\b(?:nuevo|fuerte|moderado|leve|"
    r"strong|moderate|minor|major)\s+"
    r"(?:sismo|temblor|terremoto|earthquake|quake)\b"
    r"|"
    r"\b(?:sismo|temblor|terremoto|"
    r"earthquake|quake)\s+"
    r"(?:en|cerca|frente|near|off|in|at)\b",
    re.I,
)

QUAKE_RESEARCH = re.compile(
    r"\b(?:"
    r"investig\w*|estudi\w*|cientific\w*|"
    r"descubr\w*|hallazgo\w*|"
    r"analis\w*|analiz\w*|"
    r"alerta temprana|"
    r"research\w*|stud(?:y|ies)|"
    r"scientist\w*|discover\w*|"
    r"finding\w*|early warning|"
    r"new model|historic\w*|historico\w*"
    r")\b",
    re.I,
)

QUAKE_CATALOG_SOURCES = (
    "volcanodiscovery",
    "earthquake track",
    "earthquake tracker",
    "earthquake monitor",
    "recent earthquakes",
    "latest earthquakes",
)

# Un titular de catalogo no necesariamente
# contiene "sismo"; puede decir:
# "Moderate mag. 4.5 - Pacific Ocean".
QUAKE_CATALOG_TITLE = re.compile(
    r"\b(?:"
    r"moderado|moderate|fuerte|strong|"
    r"leve|minor|light|weak"
    r")\b.{0,55}"
    r"(?:\bmag\.?\s*[0-9]|"
    r"\bmw\s*[0-9]|"
    r"\bml\s*[0-9])",
    re.I,
)

QUAKE_DIRECT = re.compile(
    r"\b(?:sismo|temblor|terremoto|"
    r"earthquake|quake)\b"
    r".{0,90}"
    r"\b(?:sacude|remece|registrado|"
    r"ocurre|ocurrio|reported|"
    r"strikes|struck|hits|shakes)\b"
    r"|"
    r"\b(?:sacude|remece|registran|"
    r"reportan|strikes|hits|shakes)\b"
    r".{0,90}"
    r"\b(?:sismo|temblor|terremoto|"
    r"earthquake|quake)\b",
    re.I,
)


def is_earthquake_occurrence(
    title,
    description="",
    source="",
):
    """
    True si el contenido es un reporte
    de ocurrencia de un sismo.

    No bloquea automaticamente noticias de
    volcanes, huracanes o desastres distintos.

    Distingue investigaciones explicitas
    de partes de catalogos sismicos.
    """

    title = normalized(title)
    description = normalized(description)
    source = normalized(source)
    combined = title + " " + description

    has_quake = bool(
        QUAKE_TERM.search(combined)
    )

    catalog = any(
        name in source
        for name in QUAKE_CATALOG_SOURCES
    )

    scientific_title = bool(
        QUAKE_RESEARCH.search(title)
    )

    # Catalogos de eventos, incluido
    # "Terremoto moderado mag. 4.5".
    if catalog and (
        has_quake
        or QUAKE_CATALOG_TITLE.search(title)
    ):
        if not scientific_title:
            return True

    # Titulares de magnitud.
    if (
        QUAKE_TERM.search(title)
        and QUAKE_MAGNITUDE.search(title)
        and not scientific_title
    ):
        return True

    # Catalogos sin la palabra terremoto.
    if (
        QUAKE_CATALOG_TITLE.search(title)
        and QUAKE_LOCATION.search(title)
        and not scientific_title
    ):
        return True

    # Reportes de sismos ocurridos.
    if (
        QUAKE_TERM.search(title)
        and QUAKE_REPORT.search(title)
        and not scientific_title
    ):
        return True

    if (
        QUAKE_DIRECT.search(title)
        and not scientific_title
    ):
        return True

    # Sismo en Chile / Earthquake near Japan.
    if (
        QUAKE_HEADLINE.search(title)
        and not scientific_title
    ):
        return True

    # Investigaciones cientificas reales.
    if scientific_title:
        return False

    # Reporte de un evento en el resumen.
    if (
        QUAKE_TERM.search(title)
        and (
            QUAKE_MAGNITUDE.search(description)
            or QUAKE_REPORT.search(description)
        )
        and (
            QUAKE_LOCATION.search(combined)
            or QUAKE_MAGNITUDE.search(combined)
        )
    ):
        return True

    return False


# ============================================================
# 6. PRUEBAS OBLIGATORIAS DEL FILTRO
# ============================================================

def test_earthquake_filter():
    rejected = [
        (
            "Terremoto moderado mag. 4.5 - "
            "Océano Pacífico Norte, 230 km "
            "al noreste de Kamchatka"
        ),
        "Sismo magnitud 5.2 cerca de Santiago",
        "Terremoto Mw 6.1 frente a Japón",
        "Sismo M4.3 en el norte de Chile",
        "Temblor de magnitud 3,8 sacude Perú",
        "Earthquake M5.6 near Tokyo",
        "Moderate earthquake strikes California",
        "Sismo de magnitud 4.9 registrado en México",
        "Fuerte temblor en el norte de Chile",
        "Earthquake near Japan",
        "Terremoto de magnitud 5,0 con epicentro en Perú",
    ]

    allowed = [
        "Científicos estudian el terremoto de Valdivia de 1960",
        "Investigadores analizan un terremoto de magnitud 7",
        "Nuevo estudio explica las fallas tectónicas",
        "NASA descubre nuevas características de Marte",
        "Volcán presenta actividad eruptiva",
        "Investigadores analizan corrientes oceánicas",
        "Huracán se intensifica sobre el océano",
        "Nuevo sistema de alerta temprana para terremotos",
    ]

    failures = []

    for title in rejected:
        if not is_earthquake_occurrence(title):
            failures.append(
                "NO RECHAZADA: " + title
            )

    for title in allowed:
        if is_earthquake_occurrence(title):
            failures.append(
                "BLOQUEADA INDEBIDAMENTE: " + title
            )

    if failures:
        raise RuntimeError(
            "FILTRO SISMICO NO SUPERA PRUEBAS: "
            + " | ".join(failures)
        )

    log(
        "FILTRO SISMICO VALIDADO:",
        len(rejected),
        "reportes bloqueados;",
        len(allowed),
        "noticias permitidas"
    )


# ============================================================
# 7. CLASIFICACION EDITORIAL
# ============================================================

def classify_article(
    title,
    description,
    source="",
):
    if is_earthquake_occurrence(
        title,
        description,
        source,
    ):
        return None

    content = (
        clean(title) + " " + clean(description)
    )

    if SPAM.search(content):
        return None

    if MISLEADING.search(content):
        return None

    matches = []

    for category, pattern in CATEGORY_PATTERNS.items():
        if pattern.search(content):
            matches.append(category)

    if not matches:
        return None

    priorities = (
        "sismos",
        "volcanes",
        "oceanos",
        "desastres",
        "meteorologia",
        "climatologia",
        "geologia",
        "espacio",
    )

    for category in priorities:
        if category in matches:
            return category

    return matches[0]


def is_low_information(title, description):
    title = clean(title)
    description = clean(description)

    if word_count(title) < 4:
        return True

    if not description:
        return False

    return (
        normalized(description)
        == normalized(title)
    )


# ============================================================
# 8. NOTICIAS RSS
# ============================================================

def feed_items():
    now = utcnow()
    output = {}

    audit = {
        "feeds_ok": 0,
        "feeds_failed": 0,
        "rss_items": 0,
        "outside_24h": 0,
        "missing_date": 0,
        "earthquake_occurrence": 0,
        "not_relevant": 0,
        "invalid": 0,
        "accepted": 0,
    }

    for search_category, searches in SEARCH_GROUPS.items():
        for lang, query in searches:

            region = (
                ("es-CL", "CL", "CL:es-419")
                if lang == "es"
                else ("en-US", "US", "US:en")
            )

            url = (
                "https://news.google.com/rss/search?"
                + urllib.parse.urlencode({
                    "q": query + " when:1d",
                    "hl": region[0],
                    "gl": region[1],
                    "ceid": region[2],
                })
            )

            try:
                root = ET.fromstring(
                    get(url, timeout=20).content
                )
                audit["feeds_ok"] += 1

            except Exception as exc:
                audit["feeds_failed"] += 1
                log(
                    "RSS ERROR:",
                    search_category,
                    query,
                    safe_error(exc)[:100],
                )
                continue

            entries = root.findall(
                ".//channel/item"
            )[:MAX_FEED_ITEMS]

            log(
                "RSS:",
                search_category,
                lang,
                query,
                "resultados:",
                len(entries),
            )

            for node in entries:
                audit["rss_items"] += 1

                title = clean(
                    node.findtext("title")
                )
                description = clean(
                    node.findtext("description")
                )
                link = clean(
                    node.findtext("link")
                )

                try:
                    date = parsedate_to_datetime(
                        node.findtext("pubDate") or ""
                    )

                    if date.tzinfo is None:
                        date = date.replace(
                            tzinfo=timezone.utc
                        )

                    date = date.astimezone(
                        timezone.utc
                    )

                except Exception:
                    audit["missing_date"] += 1
                    continue

                if not (
                    now - timedelta(hours=24)
                    <= date
                    <= now + timedelta(minutes=5)
                ):
                    audit["outside_24h"] += 1
                    continue

                provider = clean(
                    node.findtext("source")
                )

                if " - " in title:
                    prefix, suffix = title.rsplit(
                        " - ", 1
                    )

                    if (
                        not provider
                        or normalized(suffix)
                        == normalized(provider)
                    ):
                        title = prefix.strip()

                # BLOQUEO RSS.
                if is_earthquake_occurrence(
                    title,
                    description,
                    provider,
                ):
                    audit[
                        "earthquake_occurrence"
                    ] += 1

                    log(
                        "DESCARTADO - OCURRENCIA SISMICA:",
                        title[:130],
                    )
                    continue

                if is_low_information(
                    title,
                    description,
                ):
                    audit["invalid"] += 1
                    continue

                category = classify_article(
                    title,
                    description,
                    provider,
                )

                if not category:
                    audit["not_relevant"] += 1
                    continue

                identifier = article_fingerprint(
                    title
                )

                if identifier in output:
                    continue

                output[identifier] = {
                    "id": identifier,
                    "title": title,
                    "description": description,
                    "url": link,
                    "source": provider,
                    "date": date.isoformat(),
                    "lang": lang,
                    "category": category,
                    "search_category": search_category,
                    "scientific_advance": bool(
                        ADVANCE.search(
                            title + " " + description
                        )
                    ),
                }
                audit["accepted"] += 1

    log(
        "AUDITORIA RSS:",
        json.dumps(
            audit,
            ensure_ascii=False,
        ),
    )

    if audit["feeds_ok"] == 0:
        raise RuntimeError(
            "No se pudo consultar ninguna fuente RSS"
        )

    return list(output.values())


# ============================================================
# 9. PRIORIDAD EDITORIAL
# ============================================================

def priority(article):
    text = normalized(
        article["title"]
        + " "
        + article["description"]
    )

    weights = {
        "sismos": 9,
        "volcanes": 8,
        "oceanos": 7,
        "desastres": 7,
        "meteorologia": 6,
        "climatologia": 6,
        "geologia": 6,
        "espacio": 5,
    }

    score = weights.get(
        article.get("category"), 0
    )

    terms = [
        ("investigacion", 5),
        ("research", 5),
        ("descubrimiento", 5),
        ("discovery", 5),
        ("nasa", 4),
        ("noaa", 4),
        ("tecnologia", 4),
        ("technology", 4),
        ("estudio", 4),
        ("study", 4),
        ("volcan", 3),
        ("eruption", 3),
        ("huracan", 3),
        ("hurricane", 3),
        ("tsunami", 3),
        ("oceano", 2),
        ("ocean", 2),
        ("satellite", 2),
        ("satelite", 2),
    ]

    for term, value in terms:
        if term in text:
            score += value

    if article.get("scientific_advance"):
        score += 3

    date = datetime.fromisoformat(
        article["date"]
    )

    age = max(
        0,
        (utcnow() - date).total_seconds() / 3600
    )

    score += max(0, 24 - age) * 0.18
    score += min(
        5,
        word_count(
            article.get("description", "")
        ) / 12,
    )

    return score


# ============================================================
# 10. IA - GEMINI, OPENROUTER Y GROQ
# ============================================================

def ai_text(prompt, max_tokens=1100):

    key = os.getenv(
        "GEMINI_API_KEY", ""
    ).strip()

    if key:
        model = os.getenv(
            "GEMINI_NEWS_MODEL",
            "gemini-2.5-flash",
        )

        try:
            response = HTTP.post(
                (
                    "https://generativelanguage."
                    "googleapis.com/v1beta/models/"
                    + model
                    + ":generateContent"
                ),
                headers={
                    "x-goog-api-key": key
                },
                json={
                    "contents": [{
                        "parts": [{
                            "text": prompt
                        }]
                    }],
                    "generationConfig": {
                        "temperature": 0.15,
                        "maxOutputTokens": max_tokens,
                    },
                },
                timeout=65,
            )
            response.raise_for_status()

            parts = (
                response.json()["candidates"][0]
                ["content"]["parts"]
            )

            result = "\n\n".join(
                part.get("text", "")
                for part in parts
            ).strip()

            if result:
                return result

        except Exception as exc:
            log(
                "Gemini no disponible:",
                safe_error(exc)[:120],
            )

    key = os.getenv(
        "OPENROUTER_API_KEY", ""
    ).strip()

    if key:
        try:
            model = os.getenv(
                "OPENROUTER_NEWS_MODEL",
                "openrouter/free",
            )

            response = HTTP.post(
                (
                    "https://openrouter.ai/"
                    "api/v1/chat/completions"
                ),
                headers={
                    "Authorization": "Bearer " + key
                },
                json={
                    "model": model,
                    "temperature": 0.15,
                    "max_tokens": max_tokens,
                    "messages": [{
                        "role": "user",
                        "content": prompt,
                    }],
                },
                timeout=65,
            )

            response.raise_for_status()

            result = (
                response.json()["choices"][0]
                ["message"]["content"]
            )

            if isinstance(result, str):
                result = result.strip()
                if result:
                    return result

        except Exception as exc:
            log(
                "OpenRouter no disponible:",
                safe_error(exc)[:120],
            )

    key = os.getenv(
        "GROQ_API_KEY", ""
    ).strip()

    if key:
        try:
            model = os.getenv(
                "GROQ_NEWS_MODEL",
                "llama-3.3-70b-versatile",
            )

            response = HTTP.post(
                (
                    "https://api.groq.com/"
                    "openai/v1/chat/completions"
                ),
                headers={
                    "Authorization": "Bearer " + key
                },
                json={
                    "model": model,
                    "temperature": 0.15,
                    "max_tokens": max_tokens,
                    "messages": [{
                        "role": "user",
                        "content": prompt,
                    }],
                },
                timeout=65,
            )

            response.raise_for_status()

            result = (
                response.json()["choices"][0]
                ["message"]["content"]
            ).strip()

            if result:
                return result

        except Exception as exc:
            log(
                "Groq no disponible:",
                safe_error(exc)[:120],
            )

    return None


# ============================================================
# 11. TRADUCCION AL ESPANOL
# ============================================================

def translate_es(value):
    value = clean(value)

    if not value:
        return ""

    prompt = (
        "Traduce fielmente al español de Chile. "
        "No agregues datos ni cambies nombres, "
        "fechas ni cifras. Devuelve solo "
        "la traducción:\n\n"
        + value
    )

    result = ai_text(
        prompt,
        max_tokens=650,
    )

    if result and len(result) >= 10:
        return clean(result)

    try:
        response = get(
            (
                "https://translate.googleapis.com/"
                "translate_a/single"
            ),
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "es",
                "dt": "t",
                "q": value,
            },
            timeout=18,
        )

        result = clean(
            "".join(
                piece[0]
                for piece in response.json()[0]
                if piece and piece[0]
            )
        )

        if len(result) >= 10:
            return result

    except Exception:
        pass

    return None


def spanish_article(article):
    if article["lang"] == "es":
        return dict(article)

    title = translate_es(
        article["title"]
    )

    if not title:
        return None

    converted = dict(article)
    converted["original_title"] = article["title"]
    converted["title"] = title

    if article.get("description"):
        converted["description"] = (
            translate_es(
                article["description"]
            ) or ""
        )

    converted["lang"] = "es"
    return converted


# ============================================================
# 12. EXTRACCION DEL ARTICULO
# ============================================================

class ArticleParser(HTMLParser):

    def __init__(self):
        super().__init__()
        self.block = 0
        self.in_paragraph = False
        self.parts = []
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        if tag in (
            "script", "style", "nav",
            "footer", "header", "aside",
            "noscript",
        ):
            self.block += 1

        elif tag == "p" and not self.block:
            self.in_paragraph = True
            self.parts = []

    def handle_endtag(self, tag):
        if tag in (
            "script", "style", "nav",
            "footer", "header", "aside",
            "noscript",
        ):
            self.block = max(0, self.block - 1)

        elif tag == "p" and self.in_paragraph:
            paragraph = clean(
                " ".join(self.parts)
            )

            if len(paragraph) > 55:
                self.paragraphs.append(paragraph)

            self.in_paragraph = False
            self.parts = []

    def handle_data(self, data):
        if (
            self.in_paragraph
            and not self.block
        ):
            self.parts.append(data)


def full_article_text(article):
    url = article.get("url", "")

    if not url.startswith("https://"):
        return ""

    try:
        response = get(
            url,
            timeout=20,
            headers={
                "Accept": "text/html"
            },
        )

        if "html" not in response.headers.get(
            "Content-Type", ""
        ).lower():
            return ""

        if len(response.content) > 3_000_000:
            return ""

        parser = ArticleParser()
        parser.feed(response.text)

        paragraphs = []
        seen = set()

        for paragraph in parser.paragraphs:
            key = normalized(paragraph)[:100]

            if key in seen:
                continue

            seen.add(key)

            if re.search(
                r"cookies|privacy policy|"
                r"subscribe|advertisement",
                paragraph,
                re.I,
            ):
                continue

            paragraphs.append(paragraph)

        content = " ".join(paragraphs)

        if word_count(content) < 60:
            return ""

        return content[:16000]

    except Exception as exc:
        log(
            "Articulo no accesible:",
            type(exc).__name__,
        )
        return ""


# ============================================================
# 13. EXPLICACION DETALLADA
# ============================================================

def detailed_explanation(article):
    body = full_article_text(article)

    if not body:
        return None

    prompt = (
        "Eres periodista científico de SISMONAUTA. "
        "Redacta una explicación original y clara "
        "en español de Chile.\n\n"
        "Extensión orientativa: 80 a 190 palabras, "
        "según los datos disponibles.\n\n"
        "Explica qué se descubrió, observó o "
        "investigó, y su importancia.\n"
        "Utiliza solamente los datos proporcionados.\n"
        "No inventes cifras, fechas, instituciones, "
        "resultados ni causas.\n"
        "No añadas predicciones sísmicas.\n"
        "No conviertas investigaciones sobre "
        "terremotos en avisos de sismos recientes.\n"
        "No copies párrafos extensos.\n"
        "No incluyas hashtags ni enlaces.\n"
        "No agregues frases de relleno.\n"
        "Si faltan hechos comprobables, "
        "responde INSUFICIENTE.\n\n"
        f"TITULAR: {article['title']}\n"
        f"MEDIO: {article.get('source', '')}\n"
        f"CATEGORÍA: {article.get('category', '')}\n\n"
        f"ARTÍCULO:\n{body[:14500]}"
    )

    result = ai_text(
        prompt,
        max_tokens=1050,
    )

    if not result:
        return None

    if "INSUFICIENTE" in result.upper():
        return None

    if word_count(result) < 25:
        return None

    return result.strip()


# ============================================================
# 14. EXPLICACION DESDE RSS
# ============================================================

def evidence_brief(article):
    title = clean(
        article.get("title", "")
    )
    summary = clean(
        article.get("description", "")
    )

    if not summary:
        return None

    if normalized(summary) == normalized(title):
        return None

    if word_count(summary) < MIN_DESCRIPTION_WORDS:
        return None

    prompt = (
        "Redacta una nota informativa para "
        "SISMONAUTA, en español de Chile.\n"
        "Utiliza exclusivamente el titular "
        "y el resumen de la noticia.\n"
        "Puede tratar de geología, sismología "
        "científica, volcanes, meteorología, "
        "clima, océanos, espacio o desastres.\n"
        "Redacta solamente hechos concretos.\n"
        "No inventes información ni cifras.\n"
        "No conviertas estudios de terremotos "
        "en alertas sísmicas.\n"
        "Si existen pocos datos, escribe menos.\n"
        "No repitas el titular como explicación.\n"
        "No añadas hashtags ni enlaces.\n"
        "Si los datos no permiten explicar "
        "un hecho, responde INSUFICIENTE.\n\n"
        f"TITULAR: {title}\n\n"
        f"RESUMEN: {summary}"
    )

    result = ai_text(
        prompt,
        max_tokens=650,
    )

    if (
        result
        and "INSUFICIENTE" not in result.upper()
        and word_count(result) >= 20
    ):
        return result.strip()

    # Respaldo sin generacion de hechos nuevos.
    if word_count(summary) >= 18:
        return summary.rstrip(".") + "."

    return None


def obtain_explanation(article):
    result = detailed_explanation(article)

    if result:
        return result

    result = evidence_brief(article)

    if result:
        return result

    summary = clean(
        article.get("description", "")
    )

    if (
        word_count(summary) >= 12
        and normalized(summary)
        != normalized(article["title"])
    ):
        return summary.rstrip(".") + "."

    return None


# ============================================================
# 15. FOTOGRAFIAS WIKIMEDIA COMMONS
# ============================================================

PHOTO_QUERIES = {
    "sismos": [
        "earthquake fault landscape",
        "San Andreas Fault",
        "mountain rock landscape",
    ],
    "volcanes": [
        "volcano landscape",
        "volcanic eruption",
        "volcanic mountain",
    ],
    "meteorologia": [
        "storm clouds landscape",
        "hurricane satellite",
        "thunderstorm clouds",
    ],
    "climatologia": [
        "glacier landscape",
        "polar ice landscape",
        "mountain glacier",
    ],
    "oceanos": [
        "ocean coastline",
        "sea waves shore",
        "coastal landscape",
    ],
    "espacio": [
        "NASA Earth from space",
        "Hubble galaxy",
        "planet astronomy",
    ],
    "desastres": [
        "landslide mountain",
        "flooded river landscape",
        "forest wildfire",
    ],
    "geologia": [
        "rock formations landscape",
        "mountain geology",
        "geological fault",
    ],
}


def commons_license_accepted(metadata):
    def value(key):
        return clean(
            (metadata.get(key) or {}).get(
                "value", ""
            )
        ).lower()

    short = value("LicenseShortName")
    code = value("License")
    usage = value("UsageTerms")

    combined = " ".join(
        (short, code, usage)
    )

    allowed = (
        "cc0",
        "public domain",
        "publicdomain",
        "pdm",
        "pd-old",
        "pd-us",
        "pd-self",
    )

    return any(
        marker in combined
        for marker in allowed
    )


def commons_photo(article):
    category = article.get(
        "category", "geologia"
    )

    searches = PHOTO_QUERIES.get(
        category,
        PHOTO_QUERIES["geologia"],
    )

    candidates = []
    seen = set()

    for query in searches:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": 40,
            "prop": "imageinfo",
            "iiprop": (
                "url|extmetadata|mime|size"
            ),
            "iiurlwidth": 1500,
        }

        continuation = {}

        for _ in range(2):
            try:
                data = get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        **params,
                        **continuation,
                    },
                    timeout=25,
                ).json()

            except Exception as exc:
                log(
                    "COMMONS ERROR:",
                    query,
                    safe_error(exc)[:100],
                )
                break

            pages = (
                data.get("query") or {}
            ).get("pages", [])

            if isinstance(pages, dict):
                pages = list(pages.values())

            for page in pages:
                infos = (
                    page.get("imageinfo") or []
                )

                if not infos:
                    continue

                info = infos[0]
                metadata = (
                    info.get("extmetadata") or {}
                )

                if not commons_license_accepted(
                    metadata
                ):
                    continue

                title = clean(
                    page.get("title", "")
                )

                if not re.search(
                    r"\.(jpg|jpeg|png|webp)$",
                    title,
                    re.I,
                ):
                    continue

                if info.get("mime") not in (
                    "image/jpeg",
                    "image/png",
                    "image/webp",
                ):
                    continue

                if (
                    int(info.get("width") or 0) < 500
                    or int(info.get("height") or 0) < 450
                ):
                    continue

                if re.search(
                    r"diagram|infographic|schematic|"
                    r"drawing|illustration|logo|"
                    r"poster|chart|presentation|"
                    r"screenshot",
                    title,
                    re.I,
                ):
                    continue

                url = (
                    info.get("thumburl")
                    or info.get("url")
                )

                if not url or url in seen:
                    continue

                seen.add(url)

                candidates.append({
                    "title": title,
                    "url": url,
                    "source": info.get(
                        "descriptionurl", ""
                    ),
                    "license": clean(
                        (
                            metadata.get(
                                "LicenseShortName"
                            ) or {}
                        ).get("value", "")
                    ),
                    "generated": False,
                    "query": query,
                })

            continuation = data.get("continue")

            if not continuation:
                break

        if candidates:
            break

    for candidate in candidates[:18]:
        try:
            response = get(
                candidate["url"],
                timeout=30,
            )

            if len(response.content) > 15_000_000:
                continue

            with Image.open(
                io.BytesIO(response.content)
            ) as src:
                image = src.convert("RGB")

            if min(image.size) < 450:
                continue

            log(
                "FOTO COMMONS:",
                candidate["title"][:90],
            )

            return image, candidate

        except Exception:
            continue

    return None, None


# ============================================================
# 16. FONDO GRAFICO ORIGINAL
# ============================================================

def original_science_background(article):
    seed = int(
        hashlib.sha256(
            article["id"].encode("utf-8")
        ).hexdigest()[:12],
        16,
    )

    rng = random.Random(seed)

    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (5, 27, 53),
    )

    draw = ImageDraw.Draw(image)

    for y in range(HEIGHT):
        t = y / HEIGHT
        color = (
            int(8 + 17*t),
            int(42 + 32*t),
            int(83 + 30*t),
        )
        draw.line(
            (0, y, WIDTH, y),
            fill=color,
        )

    for layer in range(7):
        points = []
        base = 425 + layer * 116
        phase = rng.uniform(0, math.tau)

        for x in range(
            -20, WIDTH + 40, 16
        ):
            y = (
                base
                + 65 * math.sin(
                    x/160 + phase
                )
                + 22 * math.sin(
                    x/37 + phase/2
                )
            )
            points.append((x, int(y)))

        points.extend([
            (WIDTH, HEIGHT),
            (0, HEIGHT),
        ])

        draw.polygon(
            points,
            fill=(
                10 + layer*8,
                55 + layer*8,
                87 + layer*4,
            ),
        )

    if article.get("category") in (
        "espacio",
        "meteorologia",
        "climatologia",
    ):
        for _ in range(75):
            x = rng.randint(0, WIDTH-1)
            y = rng.randint(100, HEIGHT-150)
            radius = rng.randint(1, 4)

            draw.ellipse(
                (
                    x-radius, y-radius,
                    x+radius, y+radius,
                ),
                fill=(170, 210, 240),
            )

    else:
        for shift in (-25, 20, 75):
            draw.line(
                [
                    (160+shift, 1130),
                    (350+shift, 860),
                    (570+shift, 780),
                    (780+shift, 490),
                    (1030+shift, 400),
                ],
                fill=(226, 139, 62),
                width=(
                    7 if shift == 20 else 3
                ),
            )

    return image, {
        "title": "Arte original SISMONAUTA",
        "url": "",
        "license": "Obra original",
        "generated": True,
    }


# ============================================================
# 17. FUENTES Y TEXTO
# ============================================================

def font(size, bold=False):
    if bold:
        paths = [
            "/usr/share/fonts/truetype/"
            "dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/"
            "liberation2/LiberationSans-Bold.ttf",
        ]
    else:
        paths = [
            "/usr/share/fonts/truetype/"
            "dejavu/DejaVuSans.ttf",
        ]

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(
                path, size
            )

    return ImageFont.load_default()


def wrap_text(draw, text, selected_font, width):
    lines = []
    current = ""

    for word in text.split():
        proposed = (
            current + " " + word
        ).strip()

        measured = draw.textbbox(
            (0, 0),
            proposed,
            font=selected_font,
        )[2]

        if measured <= width:
            current = proposed
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


# ============================================================
# 18. IMAGEN PARA INSTAGRAM Y FACEBOOK
# ============================================================

def make_card(image, article, path):
    canvas = ImageOps.fit(
        image,
        (WIDTH, HEIGHT),
        Image.Resampling.LANCZOS,
        centering=(0.5, 0.44),
    ).convert("RGBA")

    overlay = Image.new(
        "RGBA",
        (WIDTH, HEIGHT),
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(overlay)

    draw.rectangle(
        (0, 0, WIDTH, 100),
        fill=(4, 21, 46, 235),
    )

    draw.text(
        (54, 27),
        "SISMONAUTA  |  ACTUALIDAD",
        font=font(37, True),
        fill="white",
    )

    title = clean(article["title"])

    selected_font = None
    lines = None
    line_height = 0
    band_height = 0

    for size in range(70, 21, -2):
        candidate = font(size, True)

        candidate_lines = wrap_text(
            draw,
            title,
            candidate,
            WIDTH - 120,
        )

        leading = int(size * 1.27)

        band = max(
            210,
            len(candidate_lines) * leading + 100,
        )

        if (
            band <= 690
            and len(candidate_lines) <= 10
        ):
            selected_font = candidate
            lines = candidate_lines
            line_height = leading
            band_height = band
            break

    if not lines:
        raise ValueError(
            "El titular no cabe en la imagen"
        )

    bottom = HEIGHT - 94
    top = bottom - band_height

    draw.rectangle(
        (0, top, WIDTH, bottom),
        fill=(0, 87, 202, 248),
    )

    draw.rectangle(
        (0, top, WIDTH, top+9),
        fill=(36, 163, 255, 255),
    )

    total_height = len(lines) * line_height

    y = top + (
        band_height - total_height
    ) // 2

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=selected_font,
            stroke_width=3,
        )

        text_width = bbox[2] - bbox[0]
        x = (WIDTH - text_width) // 2

        draw.text(
            (x, y),
            line,
            font=selected_font,
            fill=(255, 255, 255, 255),
            stroke_width=3,
            stroke_fill=(0, 0, 0, 255),
        )

        y += line_height

    draw.rectangle(
        (0, HEIGHT-94, WIDTH, HEIGHT),
        fill=(3, 24, 49, 245),
    )

    labels = {
        "sismos": "CIENCIA SÍSMICA",
        "volcanes": "VOLCANES",
        "meteorologia": "METEOROLOGÍA",
        "climatologia": "CLIMA",
        "oceanos": "OCÉANOS",
        "espacio": "ESPACIO",
        "desastres": "NATURALEZA",
        "geologia": "GEOLOGÍA",
    }

    category = labels.get(
        article.get("category"),
        "CIENCIA",
    )

    draw.text(
        (54, HEIGHT-67),
        "CIENCIA  •  " + category,
        font=font(28, True),
        fill="white",
    )

    result = Image.alpha_composite(
        canvas, overlay
    ).convert("RGB")

    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result.save(
        path,
        "JPEG",
        quality=91,
        optimize=True,
    )


# ============================================================
# 19. DESCRIPCIONES Y HASHTAGS
# ============================================================

CATEGORY_HASHTAGS = {
    "sismos": (
        "#Sismos #Terremotos #Sismologia "
        "#Geofisica #Tectonica"
    ),
    "volcanes": (
        "#Volcanes #Erupciones "
        "#Volcanologia #Geologia"
    ),
    "meteorologia": (
        "#Meteorologia #Tiempo "
        "#Tormentas #Atmosfera"
    ),
    "climatologia": (
        "#Climatologia #Clima "
        "#CambioClimatico #Glaciares"
    ),
    "oceanos": (
        "#Oceanos #Oceanografia "
        "#Tsunamis #CienciaDelMar"
    ),
    "espacio": (
        "#Espacio #Astronomia "
        "#NASA #ExploracionEspacial"
    ),
    "desastres": (
        "#DesastresNaturales "
        "#Prevencion #RiesgosNaturales"
    ),
    "geologia": (
        "#Geologia #PlanetaTierra "
        "#Geociencias #Investigacion"
    ),
}


def caption(article, credit):
    explanation = clean(
        article.get("explanation", "")
    )

    if word_count(explanation) < 12:
        raise ValueError(
            "Informacion insuficiente"
        )

    labels = {
        "sismos": "CIENCIA SÍSMICA",
        "volcanes": "ACTUALIDAD VOLCÁNICA",
        "meteorologia": "METEOROLOGÍA",
        "climatologia": "CLIMATOLOGÍA",
        "oceanos": "CIENCIAS DEL OCÉANO",
        "espacio": "CIENCIA ESPACIAL",
        "desastres": "RIESGOS NATURALES",
        "geologia": "CIENCIAS DE LA TIERRA",
    }

    category = article.get(
        "category", "sismos"
    )

    label = labels.get(
        category,
        "ACTUALIDAD CIENTÍFICA",
    )

    source = clean(
        article.get("source", "")
    )

    def assemble(body):
        parts = [
            "🌎 SISMONAUTA | " + label,
            "",
            clean(article["title"]),
            "",
            body,
            "",
            "💬 ¿Qué opinas? Te leemos en los comentarios.",
        ]

        if source:
            parts.extend([
                "",
                "📰 Medio: " + source,
            ])

        parts.extend([
            "",
            HASHTAGS_BASE,
            CATEGORY_HASHTAGS.get(category, ""),
        ])

        return "\n".join(parts)

    text = assemble(explanation)

    if len(text) > 2200:
        excess = len(text) - 2200
        limit = max(
            80,
            len(explanation) - excess - 20,
        )

        shortened = explanation[:limit]
        shortened = shortened.rsplit(
            " ", 1
        )[0].rstrip(",;:") + "."

        text = assemble(shortened)

    if len(text) > 2200:
        raise ValueError(
            "Descripcion demasiado larga"
        )

    return text


# ============================================================
# 20. GITHUB: ESTADO E IMAGENES
# ============================================================

class Github:

    def __init__(self):
        self.repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = (
            os.getenv("GITHUB_REF_NAME")
            or "main"
        )

        self.base = (
            "https://api.github.com/repos/"
            + self.repo
        )

        token = os.environ["GH_TOKEN"]

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

        response = self.session.get(
            self.base,
            timeout=20,
        )

        response.raise_for_status()

        if response.json().get("private"):
            raise RuntimeError(
                "Repositorio publico requerido "
                "para alojar las imagenes"
            )

    def endpoint(self, path):
        return (
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path, safe="/"
            )
        )

    def read(self, path):
        response = self.session.get(
            self.endpoint(path),
            params={"ref": self.branch},
            timeout=25,
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

    def write(self, path, raw):
        endpoint = self.endpoint(path)

        for attempt in range(4):
            _, sha = self.read(path)

            body = {
                "message": (
                    "SISMONAUTA noticias: "
                    + path
                ),
                "branch": self.branch,
                "content": base64.b64encode(
                    raw
                ).decode("ascii"),
            }

            if sha:
                body["sha"] = sha

            response = self.session.put(
                endpoint,
                json=body,
                timeout=60,
            )

            if (
                response.status_code in (409, 422)
                and attempt < 3
            ):
                time.sleep(2 + attempt)
                continue

            response.raise_for_status()
            return response.json()

    def image_url(self, path):
        return (
            "https://raw.githubusercontent.com/"
            + self.repo + "/"
            + urllib.parse.quote(
                self.branch, safe=""
            )
            + "/"
            + urllib.parse.quote(
                path, safe="/"
            )
        )


# ============================================================
# 21. META GRAPH API
# ============================================================

def graph(
    method,
    path,
    token,
    params=None,
    base=None,
):
    base = base or IG_BASE
    endpoint = f"{base}/{VERSION}/{path}"

    payload = dict(params or {})
    payload["access_token"] = token

    if method == "GET":
        response = requests.get(
            endpoint,
            params=payload,
            timeout=55,
        )
    else:
        response = requests.post(
            endpoint,
            data=payload,
            timeout=75,
        )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Meta no devolvio JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get("error") or {}

        raise RuntimeError(
            "Meta HTTP "
            + str(response.status_code)
            + ": "
            + str(
                error.get("message", data)
            )[:240]
        )

    return data


# ============================================================
# 22. VALIDAR CUENTAS
# ============================================================

def ig_check():
    token = os.environ["INSTAGRAM_TOKEN"]
    user_id = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    info = graph(
        "GET",
        "me",
        token,
        {"fields": "id,username"},
    )

    if (
        str(info.get("id")) != str(user_id)
        or str(
            info.get("username", "")
        ).lower() != ACCOUNT
    ):
        raise RuntimeError(
            "Cuenta Instagram incorrecta"
        )

    log(
        "Instagram @sismonauta verificado"
    )

    return user_id, token


def fb_check():
    page_id = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    token = os.environ[
        "FACEBOOK_PAGE_TOKEN"
    ]

    info = graph(
        "GET",
        page_id,
        token,
        {"fields": "id,name,username"},
        FB_BASE,
    )

    if str(info.get("id")) != str(page_id):
        raise RuntimeError(
            "Facebook Page ID incorrecto"
        )

    if (
        info.get("username")
        and str(
            info["username"]
        ).lower() != ACCOUNT
    ):
        raise RuntimeError(
            "La fanpage no corresponde a SISMONAUTA"
        )

    log(
        "Facebook validado:",
        info.get("name"),
    )

    return page_id, token


# ============================================================
# 23. PUBLICACION EN INSTAGRAM
# ============================================================

def publish_ig(
    user_id,
    token,
    image_url,
    text,
):
    container = graph(
        "POST",
        user_id + "/media",
        token,
        {
            "image_url": image_url,
            "caption": text,
        },
    )["id"]

    for _ in range(18):
        time.sleep(8)

        result = graph(
            "GET",
            container,
            token,
            {"fields": "status_code"},
        )

        status = result.get(
            "status_code"
        )

        if status == "FINISHED":
            break

        if status in (
            "ERROR", "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram rechazo la imagen: "
                + status
            )

    else:
        raise RuntimeError(
            "Instagram no confirmo contenedor"
        )

    result = graph(
        "POST",
        user_id + "/media_publish",
        token,
        {"creation_id": container},
    )

    identifier = result.get("id")

    if not identifier:
        raise RuntimeError(
            "Instagram no devolvio ID"
        )

    return str(identifier)


# ============================================================
# 24. PUBLICACION EN FACEBOOK
# ============================================================

def publish_fb(
    page_id,
    token,
    image_url,
    text,
):
    result = graph(
        "POST",
        page_id + "/photos",
        token,
        {
            "url": image_url,
            "caption": text,
            "published": "true",
        },
        FB_BASE,
    )

    identifier = (
        result.get("post_id")
        or result.get("id")
    )

    if not identifier:
        raise RuntimeError(
            "Facebook no devolvio ID"
        )

    return str(identifier)


# ============================================================
# 25. HISTORIAL ANTIDUPLICADOS
# ============================================================

def load_state(github):
    raw, _ = github.read(STATE_FILE)

    if raw:
        state = json.loads(raw)
        state.setdefault("stories", {})
        return state

    return {"stories": {}}


def save_state(github, state):
    github.write(
        STATE_FILE,
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
    )


def publication_targets(entry):
    targets = entry.setdefault(
        "targets", {}
    )

    for platform in (
        "instagram", "facebook"
    ):
        targets.setdefault(
            platform,
            {"status": "pending"},
        )

    return targets


def is_confirmed(entry):
    targets = entry.get(
        "targets", {}
    )

    return all(
        targets.get(
            platform, {}
        ).get("status") == "published"
        for platform in (
            "instagram", "facebook"
        )
    )


def has_uncertain(entry):
    targets = entry.get(
        "targets", {}
    )

    return any(
        targets.get(
            platform, {}
        ).get("status") == "uncertain"
        for platform in (
            "instagram", "facebook"
        )
    )


# ============================================================
# 26. SELECCIONAR UNA NOTICIA
# ============================================================

def select_news(articles, state):
    reasons = {
        "earthquake_occurrence": 0,
        "already_published": 0,
        "uncertain_previous": 0,
        "translation_failed": 0,
        "no_information": 0,
        "invalid_caption": 0,
        "generated_backgrounds": 0,
    }

    stories = state.get("stories", {})

    for original in articles[:MAX_ARTICLES]:

        # SEGUNDA VERIFICACION:
        # comprobar titular ORIGINAL.
        if is_earthquake_occurrence(
            original["title"],
            original.get("description", ""),
            original.get("source", ""),
        ):
            reasons[
                "earthquake_occurrence"
            ] += 1

            log(
                "DESCARTADA OCURRENCIA:",
                original["title"][:120],
            )
            continue

        existing = stories.get(original["id"])

        # No arriesgar una nueva publicacion
        # de una noticia con historial previo.
        if existing:
            targets = existing.get("targets", {})

            if is_confirmed(existing):
                reasons["already_published"] += 1
                continue

            if has_uncertain(existing):
                reasons["uncertain_previous"] += 1
                continue

            # Reanudar solo cuando los destinos
            # tengan estados conocidos.
            valid_states = {
                None, "pending", "published"
            }

            if any(
                targets.get(k, {}).get("status")
                not in valid_states
                for k in ("instagram", "facebook")
            ):
                reasons["uncertain_previous"] += 1
                continue

        log(
            "EVALUANDO:",
            original["title"][:110],
            "| categoria:",
            original["category"],
        )

        article = spanish_article(original)

        if article is None:
            reasons["translation_failed"] += 1
            continue

        # TERCERA VERIFICACION:
        # revisar la version traducida.
        if is_earthquake_occurrence(
            article["title"],
            article.get("description", ""),
            article.get("source", ""),
        ):
            reasons[
                "earthquake_occurrence"
            ] += 1

            log(
                "DESCARTADA TRADUCCION:",
                article["title"][:120],
            )
            continue

        explanation = obtain_explanation(article)

        if not explanation:
            reasons["no_information"] += 1
            continue

        article["explanation"] = explanation

        photo, metadata = commons_photo(original)

        if photo is None:
            photo, metadata = (
                original_science_background(article)
            )

            reasons[
                "generated_backgrounds"
            ] += 1

            log(
                "FONDO ORIGINAL GENERADO"
            )

        try:
            description = caption(
                article, metadata
            )
        except ValueError:
            reasons["invalid_caption"] += 1
            continue

        return (
            article,
            photo,
            metadata,
            description,
            reasons,
        )

    return (
        None, None, None, None, reasons
    )


# ============================================================
# 27. PUBLICACION SEGURA EN META
# ============================================================

def publish_article(
    github,
    state,
    article,
    image_file,
    credit,
    description,
    output,
):

    # CUARTA VERIFICACION:
    # antes de enviar cualquier publicacion.
    original_title = (
        article.get("original_title")
        or article["title"]
    )

    source = article.get("source", "")

    if is_earthquake_occurrence(
        original_title,
        article.get("description", ""),
        source,
    ):
        raise RuntimeError(
            "PUBLICACION BLOQUEADA: "
            "ocurrencia sismica en texto original"
        )

    if is_earthquake_occurrence(
        article["title"],
        article.get("description", ""),
        source,
    ):
        raise RuntimeError(
            "PUBLICACION BLOQUEADA: "
            "ocurrencia sismica en texto traducido"
        )

    instagram_id, instagram_token = ig_check()
    facebook_id, facebook_token = fb_check()

    article_id = article["id"]

    entry = state["stories"].setdefault(
        article_id,
        {
            "article": article,
            "image": credit,
            "targets": {},
        },
    )

    targets = publication_targets(entry)

    if is_confirmed(entry):
        return {
            "status": "already_published",
            "article_id": article_id,
            "targets": targets,
        }

    if has_uncertain(entry):
        raise RuntimeError(
            "Publicacion anterior incierta: "
            "revisar Meta antes de reintentar"
        )

    remote_path = (
        "sismonauta_noticias/images/"
        + article_id
        + ".jpg"
    )

    github.write(
        remote_path,
        image_file.read_bytes(),
    )

    image_url = github.image_url(remote_path)

    accessible = False

    for attempt in range(8):
        try:
            response = HTTP.get(
                image_url,
                timeout=25,
            )

            if (
                response.ok
                and response.headers.get(
                    "Content-Type", ""
                ).lower().startswith("image/")
            ):
                accessible = True
                break

        except requests.RequestException:
            pass

        time.sleep(3)

    if not accessible:
        raise RuntimeError(
            "Imagen no accesible para Meta"
        )

    entry["article"] = article
    entry["image"] = credit
    entry["image_url"] = image_url

    save_state(github, state)

    failures = []

    for platform in (
        "instagram", "facebook"
    ):
        target = targets[platform]

        if target.get("status") == "published":
            log(
                "OMITIDO:",
                platform,
                "ya publicado",
            )
            continue

        # Evitar duplicados ante errores de red
        # o resultados de publicacion inciertos.
        target["status"] = "uncertain"
        target.pop("error", None)
        save_state(github, state)

        try:
            if platform == "instagram":
                identifier = publish_ig(
                    instagram_id,
                    instagram_token,
                    image_url,
                    description,
                )
            else:
                identifier = publish_fb(
                    facebook_id,
                    facebook_token,
                    image_url,
                    description,
                )

            target.update({
                "status": "published",
                "id": str(identifier),
            })

            save_state(github, state)

            log(
                "PUBLICADO:",
                platform,
                "ID:",
                identifier,
            )

        except Exception as exc:
            target["error"] = safe_error(exc)

            save_state(github, state)

            failures.append(
                platform + ": " + safe_error(exc)
            )

            log(
                "ERROR META:",
                platform,
                safe_error(exc),
            )

    status = (
        "published"
        if is_confirmed(entry)
        else "partial_or_uncertain"
    )

    receipt = {
        "status": status,
        "article_id": article_id,
        "article_title": article["title"],
        "category": article["category"],
        "targets": targets,
        "checked_at": iso_now(),
    }

    write_json(
        output / "estado_publicacion.json",
        receipt,
    )

    if failures:
        raise RuntimeError(
            "Publicacion parcial o incierta. "
            "Revisar Meta antes de reintentar: "
            + " / ".join(failures)
        )

    return receipt


# ============================================================
# 28. PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA: noticias cientificas "
            "sin reportes de ocurrencia sismica"
        )
    )

    parser.add_argument(
        "--publish",
        action="store_true",
    )

    parser.add_argument(
        "--out",
        default="output_noticias",
    )

    parser.add_argument(
        "--test-filter",
        action="store_true",
    )

    args = parser.parse_args()

    # Pruebas obligatorias antes de consultar
    # RSS o publicar.
    test_earthquake_filter()

    if args.test_filter:
        log(
            "PRUEBAS COMPLETADAS SIN PUBLICAR"
        )
        return 0

    output = Path(args.out)
    output.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        output / "estado_publicacion.json"
    ).unlink(missing_ok=True)

    log("=" * 55)
    log("SISMONAUTA - NOTICIAS CIENTIFICAS")
    log("VENTANA: 24 HORAS")
    log("CATEGORIAS:", len(SEARCH_GROUPS))
    log(
        "BUSQUEDAS:",
        sum(
            len(items)
            for items in SEARCH_GROUPS.values()
        ),
    )
    log(
        "OCURRENCIAS SISMICAS: PROHIBIDAS"
    )
    log("PUBLICAR:", args.publish)
    log("=" * 55)

    github = (
        Github()
        if args.publish
        else None
    )

    state = (
        load_state(github)
        if github
        else {"stories": {}}
    )

    articles = feed_items()

    articles.sort(
        key=priority,
        reverse=True,
    )

    log(
        "NOTICIAS CANDIDATAS:",
        len(articles),
    )

    if not articles:
        write_json(
            output / "estado_publicacion.json",
            {
                "status": "no_eligible_news",
                "published": False,
                "lookback_hours": 24,
                "checked_at": iso_now(),
            },
        )

        log("SIN NOTICIAS ELEGIBLES")
        return 0

    (
        chosen,
        image,
        credit,
        description,
        reasons,
    ) = select_news(
        articles,
        state,
    )

    log(
        "AUDITORIA SELECCION:",
        json.dumps(
            reasons,
            ensure_ascii=False,
        ),
    )

    if chosen is None:
        write_json(
            output / "estado_publicacion.json",
            {
                "status": "no_eligible_news",
                "published": False,
                "lookback_hours": 24,
                "articles_reviewed": min(
                    len(articles),
                    MAX_ARTICLES,
                ),
                "reasons": reasons,
                "checked_at": iso_now(),
            },
        )

        log(
            "SIN NOTICIAS NUEVAS PUBLICABLES"
        )
        return 0

    # Ultima comprobacion antes de
    # preparar imagen y descripcion.
    for title in (
        chosen.get("original_title"),
        chosen["title"],
    ):
        if title and is_earthquake_occurrence(
            title,
            chosen.get("description", ""),
            chosen.get("source", ""),
        ):
            raise RuntimeError(
                "NOTICIA BLOQUEADA: "
                "ocurrencia de terremoto"
            )

    image_file = (
        output / (chosen["id"] + ".jpg")
    )

    make_card(
        image,
        chosen,
        image_file,
    )

    write_json(
        output / "noticia.json",
        {
            "article": chosen,
            "image": credit,
            "caption": description,
        },
    )

    log(
        "NOTICIA SELECCIONADA:",
        chosen["title"],
    )
    log(
        "CATEGORIA:",
        chosen["category"],
    )
    log(
        "EXTENSION:",
        word_count(
            chosen["explanation"]
        ),
        "palabras",
    )

    if not args.publish:
        write_json(
            output / "estado_publicacion.json",
            {
                "status": "preview_only",
                "published": False,
                "article_id": chosen["id"],
                "category": chosen["category"],
                "image": str(image_file),
                "lookback_hours": 24,
                "checked_at": iso_now(),
            },
        )

        log(
            "VISTA PREVIA COMPLETADA"
        )
        return 0

    receipt = publish_article(
        github,
        state,
        chosen,
        image_file,
        credit,
        description,
        output,
    )

    if receipt["status"] == "published":
        log(
            "PUBLICACION CONFIRMADA EN "
            "INSTAGRAM Y FACEBOOK"
        )
        return 0

    if receipt["status"] == "already_published":
        return 0

    return 1


# ============================================================
# 29. EJECUCION
# ============================================================

if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as exc:
        print(
            "ERROR:",
            safe_error(exc),
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)
