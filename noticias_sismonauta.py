
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA - NOTICIAS CIENTIFICAS
=================================

Python 3.11+

TEMATICAS:
- Investigaciones sismologicas
- Fallas tectonicas y geofisica
- Volcanes y erupciones
- Meteorologia y fenomenos atmosfericos
- Climatologia y glaciares
- Oceanos, tsunamis y oceanografia
- Espacio, astronomia y satelites
- Desastres naturales y geologia

REGLA EDITORIAL OBLIGATORIA:
- NO publicar reportes de ocurrencia de sismos.
- NO publicar avisos de magnitud y epicentro.
- SI permitir estudios y descubrimientos
  relacionados con terremotos.
- Los sismos recientes son responsabilidad
  del sistema separado de alertas sismicas.

REDES:
- Instagram @sismonauta
- Fanpage Facebook SISMONAUTA

CARACTERISTICAS:
- Noticias publicadas en las ultimas 24 horas
- RSS en espanol e ingles
- Filtros cientificos mas flexibles
- Traduccion automatica al espanol
- Gemini, OpenRouter o Groq opcionales
- Explicaciones sin datos inventados
- Fotografias Wikimedia Commons CC0/PD
- Fondo grafico original de respaldo
- Imagen vertical 1080 x 1350
- Banner azul y letras blancas
- Historial GitHub antiduplicados
- Publicacion independiente por plataforma
- Proteccion ante envios inciertos

DEPENDENCIAS:
    pip install requests pillow

VISTA PREVIA:
    python noticias_sismonauta.py --out output_noticias

PUBLICAR:
    python noticias_sismonauta.py --publish --out output_noticias
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
# 1. CONFIGURACION GENERAL
# ============================================================

UA = "SismonautaNews/4.1"

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": UA,
    "Accept": "*/*",
})

VERSION = os.getenv(
    "IG_GRAPH_VERSION",
    "v25.0",
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com",
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

ACCOUNT = "sismonauta"

# NO cambiar: contiene el historial anterior.
STATE_FILE = "sismonauta_noticias/state.json"

LOOKBACK_HOURS = 24

MAX_ARTICLES = int(
    os.getenv("NEWS_MAX_ARTICLES", "100")
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
# 2. BUSQUEDAS RSS POR CATEGORIA
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
# 3. FILTROS TEMATICOS
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
    r"publicad\w*|published|"
    r"nuev\w*|new\b",
    re.I,
)

SPAM = re.compile(
    r"hor[oó]scop\w*|horoscop\w*|"
    r"astrolog\w*|conspiracy|"
    r"conspiraci[oó]n\w*|"
    r"apocalipsis|loter[ií]a|lottery|"
    r"apuesta\w*|betting|"
    r"descuentos?|oferta comercial|"
    r"promoci[oó]n comercial",
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
# 4. FILTRO OBLIGATORIO DE OCURRENCIA SISMICA
# ============================================================

# Este filtro solo afecta a noticias de
# terremotos y temblores.
#
# Las noticias sobre volcanes, huracanes,
# tsunamis, oceanos y otros fenomenos
# permanecen elegibles.

EARTHQUAKE_WORDS = re.compile(
    r"\b("
    r"sismos?|temblores?|terremotos?|"
    r"earthquakes?|quakes?|"
    r"seismic event|seismic tremor"
    r")\b",
    re.I,
)

# Indicadores de que el titular informa
# un terremoto ocurrido y no un estudio.

EARTHQUAKE_EVENT = re.compile(
    r"\b("
    r"magnitud\s*(?:de\s*)?\d|"
    r"magnitude\s*(?:of\s*)?\d|"
    r"magnitude\s+[3-9](?:[.,]\d)?|"
    r"sacude|sacudi[oó]|sacuden|"
    r"remece|remeci[oó]|remecen|"
    r"se\s+registr[oó]|"
    r"se\s+report[oó]|"
    r"se\s+produjo|"
    r"se\s+detect[oó]|"
    r"se\s+sinti[oó]|"
    r"reportan|registran|"
    r"epicentro|"
    r"strikes?|struck|"
    r"hits?|shakes?|"
    r"felt|reported|recorded|"
    r"occurred|"
    r"tembl[oó]\s+la\s+tierra"
    r")\b",
    re.I,
)

# Titulares tipicos de reportes:
# "Sismo en Chile", "Nuevo terremoto",
# "Earthquake near Japan", etc.

EARTHQUAKE_HEADLINE = re.compile(
    r"\b("
    r"nuevo\s+sismo|fuerte\s+sismo|"
    r"nuevo\s+temblor|fuerte\s+temblor|"
    r"nuevo\s+terremoto|"
    r"strong\s+earthquake|"
    r"powerful\s+earthquake|"
    r"major\s+earthquake|"
    r"earthquake\s+(?:in|near|off)\b|"
    r"terremoto\s+(?:en|cerca|frente)\b|"
    r"sismo\s+(?:en|cerca|frente)\b|"
    r"temblor\s+(?:en|cerca|frente)\b"
    r")",
    re.I,
)

# Indicadores claros de una nota cientifica.
# Su presencia permite investigar noticias
# sobre terremotos historicos o actuales,
# pero no anula titulares de ocurrencia directa.

EARTHQUAKE_RESEARCH = re.compile(
    r"\b("
    r"investigaci[oó]n|investigadores?|"
    r"estudio|estudios|estudian|"
    r"cient[ií]ficos?|"
    r"descubrimiento|descubren|hallazgo|"
    r"an[aá]lisis|analizan|"
    r"nueva\s+tecnolog[ií]a|"
    r"alerta\s+temprana|"
    r"research|study|studies|"
    r"scientists?|researchers?|"
    r"discovery|findings?|"
    r"new\s+technology|"
    r"early\s+warning|new\s+model|"
    r"new\s+research"
    r")\b",
    re.I,
)

# Formas de ocurrencia que requieren
# que el titular realmente trate un sismo.

EARTHQUAKE_DIRECT_REPORT = re.compile(
    r"("
    r"(?:sismo|temblor|terremoto|earthquake|quake)"
    r"[^.!?]{0,85}"
    r"(?:sacude|remece|registrado|reportado|"
    r"ocurre|ocurri[oó]|se\s+registr[oó]|"
    r"se\s+report[oó]|strikes?|hits?|"
    r"struck|shakes?|reported|occurred)"
    r"|"
    r"(?:sacude|remece|se\s+registr[oó]|"
    r"reportan|strikes?|hits?|shakes?)"
    r"[^.!?]{0,85}"
    r"(?:sismo|temblor|terremoto|earthquake|quake)"
    r")",
    re.I,
)


def is_earthquake_occurrence(title, description=""):
    """
    True: noticia que informa una ocurrencia.

    False: noticia cientifica o de otra tematica.

    Se analiza especialmente el TITULAR para
    evitar confundir un estudio retrospectivo
    con un reporte sismico reciente.
    """

    title = clean(title)
    description = clean(description)

    if not EARTHQUAKE_WORDS.search(
        title + " " + description
    ):
        return False

    # 1. Reportes explicitos de ocurrencia.
    if EARTHQUAKE_DIRECT_REPORT.search(title):
        return True

    # 2. Titulares que anuncian un sismo
    # en una localidad determinada.
    if EARTHQUAKE_HEADLINE.search(title):
        if not EARTHQUAKE_RESEARCH.search(title):
            return True

    # 3. Titulares de magnitud y epicentro.
    if (
        EARTHQUAKE_WORDS.search(title)
        and EARTHQUAKE_EVENT.search(title)
        and not EARTHQUAKE_RESEARCH.search(title)
    ):
        return True

    # 4. Investigación claramente identificada.
    if EARTHQUAKE_RESEARCH.search(title):
        return False

    # 5. Si el titulo solo anuncia un sismo,
    # examinar el resumen.
    if (
        EARTHQUAKE_WORDS.search(title)
        and EARTHQUAKE_EVENT.search(description)
        and not EARTHQUAKE_RESEARCH.search(
            title + " " + description
        )
    ):
        return True

    # 6. Otros titulares de ocurrencia sin
    # magnitud, como "Fuerte temblor en...".
    if EARTHQUAKE_HEADLINE.search(title):
        return True

    return False


# ============================================================
# 5. UTILIDADES
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

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def normalized(value):
    text = unicodedata.normalize(
        "NFKD",
        clean(value).lower(),
    )

    return "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )


def word_count(value):
    return len(
        clean(value).split()
    )


def utcnow():
    return datetime.now(
        timezone.utc
    )


def iso_now():
    return utcnow().isoformat()


def write_json(path, data):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def safe_error(exc):
    message = str(exc)

    for key in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    ):
        secret = os.getenv(
            key, ""
        )

        if secret:
            message = message.replace(
                secret,
                "***",
            )

    return message[:300]


def get(url, **kwargs):
    timeout = kwargs.pop(
        "timeout",
        25,
    )

    response = HTTP.get(
        url,
        timeout=timeout,
        **kwargs,
    )

    response.raise_for_status()

    return response


def article_fingerprint(title):
    value = normalized(title)

    value = re.sub(
        r"[^a-z0-9]+",
        "",
        value,
    )

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:20]


# ============================================================
# 6. CLASIFICACION EDITORIAL
# ============================================================

def classify_article(title, description):

    # REGLA INVIOLABLE DE SISMONAUTA NOTICIAS.
    if is_earthquake_occurrence(
        title, description
    ):
        return None

    content = (
        clean(title)
        + " "
        + clean(description)
    )

    if SPAM.search(content):
        return None

    if MISLEADING.search(content):
        return None

    matches = []

    for category, pattern in (
        CATEGORY_PATTERNS.items()
    ):
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
# 7. NOTICIAS RSS - ULTIMAS 24 HORAS
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

    for search_category, searches in (
        SEARCH_GROUPS.items()
    ):

        for lang, query in searches:

            region = (
                ("es-CL", "CL", "CL:es-419")
                if lang == "es"
                else ("en-US", "US", "US:en")
            )

            full_query = (
                query + " when:1d"
            )

            url = (
                "https://news.google.com/rss/search?"
                + urllib.parse.urlencode({
                    "q": full_query,
                    "hl": region[0],
                    "gl": region[1],
                    "ceid": region[2],
                })
            )

            try:
                root = ET.fromstring(
                    get(
                        url,
                        timeout=20,
                    ).content
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
                "|",
                query,
                "| resultados:",
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
                        " - ",
                        1,
                    )

                    if (
                        not provider
                        or normalized(suffix)
                        == normalized(provider)
                    ):
                        title = prefix.strip()

                # PRIMER BLOQUEO.
                # Los reportes sismicos no son
                # noticias cientificas publicables.
                if is_earthquake_occurrence(
                    title,
                    description,
                ):
                    audit[
                        "earthquake_occurrence"
                    ] += 1

                    log(
                        "DESCARTADO - OCURRENCIA:",
                        title[:105],
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
            "No fue posible consultar "
            "ninguna fuente RSS"
        )

    return list(output.values())


# ============================================================
# 8. PRIORIDAD EDITORIAL
# ============================================================

def priority(article):
    text = normalized(
        article["title"]
        + " "
        + article["description"]
    )

    score = 0.0

    category_weights = {
        "sismos": 9,
        "volcanes": 8,
        "oceanos": 7,
        "desastres": 7,
        "meteorologia": 6,
        "climatologia": 6,
        "geologia": 6,
        "espacio": 5,
    }

    score += category_weights.get(
        article.get("category"),
        0,
    )

    weights = [
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

    for term, weight in weights:
        if term in text:
            score += weight

    if article.get(
        "scientific_advance"
    ):
        score += 3

    date = datetime.fromisoformat(
        article["date"]
    )

    age = max(
        0,
        (
            utcnow() - date
        ).total_seconds() / 3600,
    )

    score += max(
        0,
        24 - age,
    ) * 0.18

    score += min(
        5,
        word_count(
            article.get("description")
        ) / 12,
    )

    return score


# ============================================================
# 9. IA - GEMINI, OPENROUTER, GROQ
# ============================================================

def ai_text(prompt, max_tokens=1100):

    key = os.getenv(
        "GEMINI_API_KEY", ""
    ).strip()

    if key:
        try:
            model = os.getenv(
                "GEMINI_NEWS_MODEL",
                "gemini-2.5-flash",
            )

            response = HTTP.post(
                (
                    "https://generativelanguage."
                    "googleapis.com/v1beta/models/"
                    + model
                    + ":generateContent"
                ),
                headers={
                    "x-goog-api-key": key,
                },
                json={
                    "contents": [{
                        "parts": [{
                            "text": prompt,
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
                    "Authorization": "Bearer " + key,
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
                    "Authorization": "Bearer " + key,
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
# 10. TRADUCCION AL ESPANOL
# ============================================================

def translate_es(value):
    value = clean(value)

    if not value:
        return ""

    prompt = (
        "Traduce fielmente al español de Chile. "
        "No agregues datos ni cambies nombres, "
        "fechas ni cifras. Devuelve solamente "
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

    result = dict(article)

    result["original_title"] = (
        article["title"]
    )

    result["title"] = title

    description = article.get(
        "description", ""
    )

    if description:
        result["description"] = (
            translate_es(description) or ""
        )

    result["lang"] = "es"

    return result


# ============================================================
# 11. EXTRAER ARTICULO COMPLETO
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
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "aside",
            "noscript",
        ):
            self.block += 1

        elif tag == "p" and not self.block:
            self.in_paragraph = True
            self.parts = []

    def handle_endtag(self, tag):
        if tag in (
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "aside",
            "noscript",
        ):
            self.block = max(
                0,
                self.block - 1,
            )

        elif tag == "p" and self.in_paragraph:
            paragraph = clean(
                " ".join(self.parts)
            )

            if len(paragraph) > 55:
                self.paragraphs.append(
                    paragraph
                )

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
                "Accept": "text/html",
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
            key = normalized(
                paragraph
            )[:100]

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

            paragraphs.append(
                paragraph
            )

        content = " ".join(
            paragraphs
        )

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
# 12. EXPLICACION DETALLADA
# ============================================================

def detailed_explanation(article):
    body = full_article_text(
        article
    )

    if not body:
        return None

    prompt = (
        "Eres periodista científico de SISMONAUTA. "
        "Redacta una explicación original y clara "
        "en español de Chile.\n\n"

        "Extensión orientativa: 80 a 190 palabras, "
        "según los datos disponibles.\n\n"

        "Explica qué se descubrió, observó o "
        "investigó y su importancia.\n"

        "Incluye solamente datos que aparezcan "
        "en el material proporcionado.\n"

        "No inventes cifras, instituciones, "
        "fechas ni conclusiones.\n"

        "No agregues predicciones sísmicas.\n"

        "No conviertas investigaciones sobre "
        "terremotos en avisos de sismos recientes.\n"

        "No copies extensos párrafos del artículo.\n"

        "No añadas hashtags ni enlaces.\n"

        "Evita frases genéricas de relleno.\n\n"

        "Si falta información verificable, "
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
# 13. EXPLICACION DESDE EL RESUMEN RSS
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
        "SISMONAUTA, en español de Chile.\n\n"

        "Puede tratar de sismología científica, "
        "volcanes, océanos, meteorología, "
        "climatología, geología, desastres "
        "naturales o espacio.\n\n"

        "Utiliza exclusivamente los datos del "
        "titular y el resumen.\n"

        "No inventes resultados, causas, "
        "instituciones ni cifras.\n"

        "No transformes un estudio sobre "
        "terremotos en una alerta sísmica.\n"

        "Si hay pocos datos, escribe menos "
        "en lugar de inventar.\n"

        "No repitas el titular como explicación.\n"
        "No agregues hashtags ni enlaces.\n"

        "Si no puedes explicar un hecho concreto, "
        "responde INSUFICIENTE.\n\n"

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

    if word_count(summary) >= 18:
        return summary.rstrip(".") + "."

    return None


def obtain_explanation(article):
    result = detailed_explanation(
        article
    )

    if result:
        return result

    result = evidence_brief(
        article
    )

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
# 14. FOTOGRAFIAS DE WIKIMEDIA COMMONS
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
    short = clean(
        (
            metadata.get("LicenseShortName")
            or {}
        ).get("value", "")
    ).lower()

    code = clean(
        (
            metadata.get("License")
            or {}
        ).get("value", "")
    ).lower()

    usage = clean(
        (
            metadata.get("UsageTerms")
            or {}
        ).get("value", "")
    ).lower()

    combined = " ".join((
        short,
        code,
        usage,
    ))

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
            "iiprop": "url|extmetadata|mime|size",
            "iiurlwidth": 1500,
        }

        continuation = {}

        for page_number in range(2):
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
                pages = list(
                    pages.values()
                )

            for page in pages:
                infos = page.get(
                    "imageinfo"
                ) or []

                if not infos:
                    continue

                info = infos[0]
                metadata = (
                    info.get("extmetadata")
                    or {}
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
                    r"diagram|infographic|"
                    r"schematic|drawing|"
                    r"illustration|logo|"
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

            continuation = data.get(
                "continue"
            )

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
            ) as original:
                image = original.convert(
                    "RGB"
                )

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
# 15. FONDO GRAFICO ORIGINAL
# ============================================================

def original_science_background(article):
    seed = int(
        hashlib.sha256(
            article["id"].encode("utf-8")
        ).hexdigest()[:12],
        16,
    )

    rng = random.Random(
        seed
    )

    image = Image.new(
        "RGB",
        (WIDTH, HEIGHT),
        (5, 27, 53),
    )

    draw = ImageDraw.Draw(
        image
    )

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
        phase = rng.uniform(
            0, math.tau
        )

        for x in range(
            -20,
            WIDTH + 40,
            16,
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

            points.append(
                (x, int(y))
            )

        points.extend([
            (WIDTH, HEIGHT),
            (0, HEIGHT),
        ])

        color = (
            10 + layer*8,
            55 + layer*8,
            87 + layer*4,
        )

        draw.polygon(
            points,
            fill=color,
        )

    if article.get("category") in (
        "espacio",
        "meteorologia",
        "climatologia",
    ):
        for _ in range(75):
            x = rng.randint(
                0, WIDTH-1
            )

            y = rng.randint(
                100, HEIGHT-150
            )

            radius = rng.randint(
                1, 4
            )

            draw.ellipse(
                (
                    x-radius,
                    y-radius,
                    x+radius,
                    y+radius,
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

    metadata = {
        "title": "Arte original SISMONAUTA",
        "url": "",
        "license": "Obra original",
        "generated": True,
    }

    return image, metadata


# ============================================================
# 16. TIPOGRAFIA Y DISENO
# ============================================================

def font(size, bold=False):
    if bold:
        paths = [
            (
                "/usr/share/fonts/truetype/"
                "dejavu/DejaVuSans-Bold.ttf"
            ),
            (
                "/usr/share/fonts/truetype/"
                "liberation2/"
                "LiberationSans-Bold.ttf"
            ),
        ]

    else:
        paths = [
            (
                "/usr/share/fonts/truetype/"
                "dejavu/DejaVuSans.ttf"
            ),
        ]

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(
                path,
                size,
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
# 17. CREACION DE IMAGEN
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

    draw = ImageDraw.Draw(
        overlay
    )

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

    title = clean(
        article["title"]
    )

    selected_font = None
    lines = None
    line_height = 0
    band_height = 0

    for size in range(
        70, 21, -2
    ):
        candidate = font(
            size, True
        )

        candidate_lines = wrap_text(
            draw,
            title,
            candidate,
            WIDTH - 120,
        )

        leading = int(
            size * 1.27
        )

        band = max(
            210,
            len(candidate_lines)
            * leading + 100,
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

    total_height = (
        len(lines) * line_height
    )

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

        text_width = (
            bbox[2] - bbox[0]
        )

        x = (
            WIDTH - text_width
        ) // 2

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
        (
            0,
            HEIGHT-94,
            WIDTH,
            HEIGHT,
        ),
        fill=(3, 24, 49, 245),
    )

    labels = {
        "sismos": "SISMOS",
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
        (54, HEIGHT - 67),
        (
            "CIENCIA  •  "
            + category
            + "  •  ACTUALIDAD"
        ),
        font=font(28, True),
        fill="white",
    )

    result = Image.alpha_composite(
        canvas,
        overlay,
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
# 18. DESCRIPCION Y HASHTAGS
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
            "Información insuficiente "
            "para el texto de publicación"
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
        "category",
        "sismos",
    )

    category_label = labels.get(
        category,
        "ACTUALIDAD CIENTÍFICA",
    )

    source = clean(
        article.get("source", "")
    )

    parts = [
        "🌎 SISMONAUTA | " + category_label,
        "",
        clean(article["title"]),
        "",
        explanation,
        "",
        (
            "💬 ¿Qué opinas? "
            "Te leemos en los comentarios."
        ),
    ]

    if source:
        parts.extend([
            "",
            "📰 Medio: " + source,
        ])

    parts.extend([
        "",
        HASHTAGS_BASE,
        CATEGORY_HASHTAGS.get(
            category, ""
        ),
    ])

    result = "\n".join(
        parts
    )

    if len(result) > 2200:
        overflow = len(result) - 2200

        explanation = explanation[
            :max(
                100,
                len(explanation) - overflow - 20
            )
        ].rsplit(" ", 1)[0].rstrip(",;:") + "."

        parts[4] = explanation
        result = "\n".join(parts)

    if len(result) > 2200:
        raise ValueError(
            "Descripción demasiado larga"
        )

    return result


# ============================================================
# 19. GITHUB - IMAGENES E HISTORIAL
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

        token = os.environ[
            "GH_TOKEN"
        ]

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": (
                "2022-11-28"
            ),
        })

        response = self.session.get(
            self.base,
            timeout=20,
        )

        response.raise_for_status()

        if response.json().get("private"):
            raise RuntimeError(
                "El repositorio debe ser público "
                "para alojar la imagen"
            )

    def endpoint(self, path):
        return (
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path,
                safe="/",
            )
        )

    def read(self, path):
        response = self.session.get(
            self.endpoint(path),
            params={
                "ref": self.branch
            },
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
        endpoint = self.endpoint(
            path
        )

        for attempt in range(4):
            old, sha = self.read(path)

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
            + self.repo
            + "/"
            + urllib.parse.quote(
                self.branch,
                safe="",
            )
            + "/"
            + urllib.parse.quote(
                path,
                safe="/",
            )
        )


# ============================================================
# 20. META GRAPH API
# ============================================================

def graph(
    method,
    path,
    token,
    params=None,
    base=None,
):
    base = base or IG_BASE

    endpoint = (
        f"{base}/{VERSION}/{path}"
    )

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
            "Meta devolvió respuesta no JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get(
            "error"
        ) or {}

        raise RuntimeError(
            "Meta HTTP "
            + str(response.status_code)
            + ": "
            + str(
                error.get(
                    "message",
                    data
                )
            )[:240]
        )

    return data


# ============================================================
# 21. VERIFICAR INSTAGRAM Y FACEBOOK
# ============================================================

def ig_check():
    token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    user_id = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    info = graph(
        "GET",
        "me",
        token,
        {
            "fields": "id,username"
        },
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
        {
            "fields": "id,name,username"
        },
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
            "La fanpage no corresponde "
            "a SISMONAUTA"
        )

    log(
        "Facebook validado:",
        info.get("name"),
    )

    return page_id, token


# ============================================================
# 22. PUBLICAR INSTAGRAM
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

    for attempt in range(18):
        time.sleep(8)

        result = graph(
            "GET",
            container,
            token,
            {
                "fields": "status_code"
            },
        )

        status = result.get(
            "status_code"
        )

        if status == "FINISHED":
            break

        if status in (
            "ERROR",
            "EXPIRED",
        ):
            raise RuntimeError(
                "Instagram rechazó la imagen: "
                + status
            )

    else:
        raise RuntimeError(
            "Instagram no confirmó el contenedor"
        )

    result = graph(
        "POST",
        user_id + "/media_publish",
        token,
        {
            "creation_id": container
        },
    )

    identifier = result.get("id")

    if not identifier:
        raise RuntimeError(
            "Instagram no devolvió ID"
        )

    return str(identifier)


# ============================================================
# 23. PUBLICAR FACEBOOK
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
            "Facebook no devolvió ID"
        )

    return str(identifier)


# ============================================================
# 24. HISTORIAL ANTIDUPLICADOS
# ============================================================

def load_state(github):
    raw, _ = github.read(
        STATE_FILE
    )

    if raw:
        state = json.loads(raw)
        state.setdefault(
            "stories", {}
        )
        return state

    return {
        "stories": {}
    }


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


def is_confirmed(entry):
    targets = entry.get(
        "targets", {}
    )

    return all(
        targets.get(
            platform, {}
        ).get("status") == "published"
        for platform in (
            "instagram",
            "facebook",
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
            "instagram",
            "facebook",
        )
    )


def publication_targets(entry):
    targets = entry.setdefault(
        "targets", {}
    )

    for platform in (
        "instagram",
        "facebook",
    ):
        targets.setdefault(
            platform,
            {
                "status": "pending"
            },
        )

    return targets


# ============================================================
# 25. SELECCIONAR NOTICIA ELEGIBLE
# ============================================================

def select_news(
    articles,
    state,
    output,
):
    reasons = {
        "earthquake_occurrence": 0,
        "already_published": 0,
        "uncertain_previous": 0,
        "translation_failed": 0,
        "no_information": 0,
        "invalid_caption": 0,
        "generated_backgrounds": 0,
    }

    for original in articles[:MAX_ARTICLES]:

        # SEGUNDO BLOQUEO.
        # Nunca seleccionar ocurrencias.
        if is_earthquake_occurrence(
            original["title"],
            original.get(
                "description", ""
            ),
        ):
            reasons[
                "earthquake_occurrence"
            ] += 1

            log(
                "OMITIDA OCURRENCIA SISMICA:",
                original["title"][:110],
            )

            continue

        existing = state.get(
            "stories", {}
        ).get(original["id"])

        if (
            existing
            and is_confirmed(existing)
        ):
            reasons[
                "already_published"
            ] += 1
            continue

        if (
            existing
            and has_uncertain(existing)
        ):
            reasons[
                "uncertain_previous"
            ] += 1
            continue

        log(
            "EVALUANDO:",
            original["title"][:110],
            "| categoria:",
            original["category"],
        )

        article = spanish_article(
            original
        )

        if article is None:
            reasons[
                "translation_failed"
            ] += 1
            continue

        # Verificacion de la traduccion.
        if is_earthquake_occurrence(
            article["title"],
            article.get(
                "description", ""
            ),
        ):
            reasons[
                "earthquake_occurrence"
            ] += 1

            log(
                "OMITIDA TRADUCCION DE OCURRENCIA:",
                article["title"][:100],
            )
            continue

        explanation = obtain_explanation(
            article
        )

        if not explanation:
            reasons["no_information"] += 1
            continue

        article["explanation"] = explanation

        photo, metadata = commons_photo(
            original
        )

        if photo is None:
            photo, metadata = (
                original_science_background(
                    article
                )
            )

            reasons[
                "generated_backgrounds"
            ] += 1

            log(
                "FONDO GRAFICO ORIGINAL GENERADO"
            )

        try:
            description = caption(
                article, metadata
            )

        except ValueError as exc:
            reasons["invalid_caption"] += 1

            log(
                "OMITIDA:",
                safe_error(exc),
            )

            continue

        return (
            article,
            photo,
            metadata,
            description,
            reasons,
        )

    return (
        None,
        None,
        None,
        None,
        reasons,
    )


# ============================================================
# 26. PUBLICACION SEGURA EN META
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

    # TERCER BLOQUEO.
    # Se comprueba de nuevo inmediatamente
    # antes de cualquier accion de publicacion.
    if is_earthquake_occurrence(
        article.get("original_title")
        or article["title"],
        article.get("description", ""),
    ):
        raise RuntimeError(
            "PUBLICACION BLOQUEADA: "
            "noticia sobre ocurrencia de sismo"
        )

    if is_earthquake_occurrence(
        article["title"],
        article.get("description", ""),
    ):
        raise RuntimeError(
            "PUBLICACION BLOQUEADA: "
            "ocurrencia sismica"
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

    targets = publication_targets(
        entry
    )

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

    image_url = github.image_url(
        remote_path
    )

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

    save_state(
        github,
        state,
    )

    failures = []

    for platform in (
        "instagram",
        "facebook",
    ):
        target = targets[platform]

        if target.get(
            "status"
        ) == "published":
            log(
                "OMITIDO:",
                platform,
                "ya publicado",
            )
            continue

        # Se guarda el bloqueo antes de
        # enviar la solicitud a Meta.
        target["status"] = "uncertain"
        target.pop("error", None)

        save_state(
            github,
            state,
        )

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

            save_state(
                github,
                state,
            )

            log(
                "PUBLICADO:",
                platform,
                "ID:",
                identifier,
            )

        except Exception as exc:
            target["error"] = (
                safe_error(exc)
            )

            save_state(
                github,
                state,
            )

            failures.append(
                platform
                + ": "
                + safe_error(exc)
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
# 27. PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA - Noticias cientificas "
            "sin reportes de ocurrencia de sismos"
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

    args = parser.parse_args()

    output = Path(
        args.out
    )

    output.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        output / "estado_publicacion.json"
    ).unlink(
        missing_ok=True
    )

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

        log(
            "SIN NOTICIAS ELEGIBLES"
        )

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
        output,
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

    # BLOQUEO ADICIONAL ANTES DE
    # GENERAR LOS ARCHIVOS.
    if is_earthquake_occurrence(
        chosen["title"],
        chosen.get("description", ""),
    ):
        raise RuntimeError(
            "Se detecto una ocurrencia sismica "
            "en la noticia seleccionada"
        )

    image_file = (
        output
        / (
            chosen["id"] + ".jpg"
        )
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
# 28. EJECUCION
# ============================================================

if __name__ == "__main__":
    try:
        sys.exit(
            main()
        )

    except Exception as exc:
        print(
            "ERROR:",
            safe_error(exc),
            file=sys.stderr,
            flush=True,
        )

        sys.exit(1)
