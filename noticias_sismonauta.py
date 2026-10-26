
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA - NOTICIAS CIENTIFICAS
================================

Archivo Python unico y autocontenido.

Busca noticias relacionadas con:
- Sismologia
- Estudios e investigaciones
- Fallas tectonicas
- Tsunamis
- Tecnologias de alerta
- Monitoreo sismico
- Descubrimientos cientificos

Publica en:
- Instagram @sismonauta
- Facebook Page SISMONAUTA

Genera:
- Imagen 1080 x 1350
- Banner azul
- Titular blanco con contorno negro
- Descripcion explicativa en espanol

Respaldo automatico:
- Si falla Gemini, prueba OpenRouter y Groq.
- Si no hay fotografia apta, genera arte propio.
- Si no hay articulo completo, utiliza un resumen
  preliminar solo cuando existen datos suficientes.
- Nunca inventa resultados cientificos.
- Nunca declara publicada una noticia sin confirmacion
  de las plataformas.

Dependencias:
pip install requests pillow
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
import urllib.parse
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import requests

from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageOps,
)


# ============================================================
# CONFIGURACION
# ============================================================

UA = "SismonautaNews/2.0"

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": UA
})

VERSION = os.getenv(
    "IG_GRAPH_VERSION",
    "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

STATE_FILE = "sismonauta_noticias/state.json"

ACCOUNT = "sismonauta"

MAX_ARTICLES = 35


# ============================================================
# CONSULTAS RSS
# ============================================================

SOURCES = [
    ("es", "sismología investigación when:1d"),
    ("es", "terremotos estudio científico when:1d"),
    ("es", "tsunami investigación when:1d"),
    ("es", "alerta sísmica tecnología when:1d"),
    ("es", "fallas tectónicas descubrimiento when:1d"),
    ("es", "ondas sísmicas científicos when:1d"),
    ("es", "geofísica terremotos investigación when:1d"),
    ("es", "sensor sísmico innovación when:1d"),
    ("es", "satélite terremotos estudio when:1d"),
    ("es", "nueva investigación terremotos when:1d"),
    ("es", "estudio científico fallas geológicas when:1d"),
    ("es", "investigadores placas tectónicas avance when:1d"),
    ("es", "nuevo estudio sismología when:1d"),

    ("en", "seismology research when:1d"),
    ("en", "earthquake technology when:1d"),
    ("en", "tsunami research when:1d"),
    ("en", "seismic early warning technology when:1d"),
    ("en", "geophysics fault scientific discovery when:1d"),
    ("en", "earthquake sensors scientific study when:1d"),
    ("en", "tectonic plates new research when:1d"),
    ("en", "earthquake satellite monitoring research when:1d"),
    ("en", "seismic imaging research when:1d"),
    ("en", "geological survey fault mapping technology when:1d"),
    ("en", "earthquake risk researchers new findings when:1d"),
    ("en", "earthquake early warning scientists when:1d"),
    ("en", "tsunami detection ocean research when:1d"),
]


SCIENCE = re.compile(
    r"(?i)\b("
    r"sism\w*|sísm\w*|seism\w*|"
    r"earthquak\w*|terremot\w*|"
    r"tsunami\w*|tect[oó]nic\w*|"
    r"tectoni\w*|fault\w*|fallas?\b|"
    r"geof[ií]sic\w*|geophysic\w*|"
    r"subduction|subducci[oó]n|"
    r"liquefaction|licuefacci[oó]n"
    r")"
)

ADVANCE = re.compile(
    r"(?i)\b("
    r"estudi\w*|investigaci[oó]n|"
    r"investigador\w*|cient[ií]fic\w*|"
    r"descubr\w*|hallazgo\w*|avance\w*|"
    r"tecnolog\w*|sensor\w*|sat[eé]lit\w*|"
    r"alerta\w*|sistema\w*|desarroll\w*|"
    r"m[eé]todo\w*|inteligencia artificial|"
    r"model\w*|research\w*|stud(?:y|ies)|"
    r"scientis\w*|discover\w*|"
    r"breakthrough\w*|technolog\w*|"
    r"satellite\w*|warning\w*|innovation\w*|"
    r"develop\w*|detect\w*|predict\w*|"
    r"monitor\w*|mapping|analy\w*|"
    r"analiz\w*|advanc\w*|publication|"
    r"published|paper\b"
    r")"
)

EVENT = re.compile(
    r"(?i)\b("
    r"sismo de magnitud|temblor de magnitud|"
    r"terremoto de magnitud|earthquake of magnitude|"
    r"magnitude [0-9]|m[ .]?[4-9][.,][0-9]|"
    r"sismo hoy|earthquake strikes|"
    r"earthquake hits|earthquake struck|"
    r"temblor hoy|sismo se registr[oó]|"
    r"earthquake reported|earthquake shakes|"
    r"terremoto sacude|reportan sismo"
    r")"
)

SPAM = re.compile(
    r"(?i)\b("
    r"horoscope|hor[oó]scopo|astrolog|"
    r"prediction for today|conspiracy|"
    r"viral video|tiktok|sensational|"
    r"apocalipsis"
    r")"
)

STOP = set(
    """
    a al algo ante con contra de del desde el en
    entre es esta este estas estos fue han hoy la
    las lo los más para por que se sin sobre su
    sus un una unos unas y the of in on to for
    with from as new how what why at by and
    scientists study research earthquake seismic
    tsunami can could may have has reveals reveal
    """.split()
)


# ============================================================
# UTILIDADES
# ============================================================

def clean(value):
    value = html.unescape(
        re.sub("<[^>]*>", " ", value or "")
    )

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def get(url, **kwargs):
    timeout = kwargs.pop("timeout", 25)

    response = HTTP.get(
        url,
        timeout=timeout,
        **kwargs
    )

    response.raise_for_status()

    return response


def write_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# BUSQUEDA DE NOTICIAS
# ============================================================

def feed_items():

    now = datetime.now(timezone.utc)

    output = {}

    audit = {
        "feeds_ok": 0,
        "feeds_failed": 0,
        "rss_items": 0,
        "outside_24h": 0,
        "missing_date": 0,
        "not_science": 0,
        "not_advance": 0,
        "event_only": 0,
        "spam": 0,
        "eligible_es": 0,
        "eligible_en": 0,
    }

    for lang, query in SOURCES:

        if lang == "es":
            region = ("es-CL", "CL", "CL:es-419")
        else:
            region = ("en-US", "US", "US:en")

        url = (
            "https://news.google.com/rss/search?"
            + urllib.parse.urlencode({
                "q": query,
                "hl": region[0],
                "gl": region[1],
                "ceid": region[2],
            })
        )

        try:
            root = ET.fromstring(
                get(
                    url,
                    timeout=30
                ).content
            )

            audit["feeds_ok"] += 1

        except Exception as exc:

            audit["feeds_failed"] += 1

            print(
                "RSS no disponible:",
                lang,
                query,
                type(exc).__name__,
                str(exc)[:100],
                flush=True
            )

            continue

        entries = root.findall(
            ".//channel/item"
        )

        print(
            "RSS:",
            lang,
            query,
            "resultados:",
            len(entries),
            flush=True
        )

        for node in entries:

            audit["rss_items"] += 1

            title = clean(
                node.findtext("title")
            )

            description = clean(
                node.findtext("description")
            )

            link = (
                node.findtext("link") or ""
            ).strip()

            try:
                date = parsedate_to_datetime(
                    node.findtext("pubDate") or ""
                ).astimezone(timezone.utc)

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
                node.findtext("source") or ""
            )

            if " - " in title:

                prefix, suffix = title.rsplit(
                    " - ",
                    1
                )

                if (
                    not provider
                    or suffix.lower() == provider.lower()
                ):
                    title = prefix.strip()

            content = (
                title + " " + description
            )

            if not SCIENCE.search(content):
                audit["not_science"] += 1
                continue

            if not ADVANCE.search(content):
                audit["not_advance"] += 1
                continue

            if EVENT.search(title):
                audit["event_only"] += 1
                continue

            if SPAM.search(content):
                audit["spam"] += 1
                continue

            identifier = hashlib.sha256(
                re.sub(
                    r"\W+",
                    "",
                    title.lower()
                ).encode("utf-8")
            ).hexdigest()[:20]

            if identifier not in output:

                output[identifier] = {
                    "id": identifier,
                    "title": title,
                    "description": description,
                    "url": link,
                    "source": provider,
                    "date": date.isoformat(),
                    "lang": lang,
                }

    audit["eligible_es"] = sum(
        item["lang"] == "es"
        for item in output.values()
    )

    audit["eligible_en"] = sum(
        item["lang"] == "en"
        for item in output.values()
    )

    print(
        "AUDITORIA RSS:",
        json.dumps(
            audit,
            ensure_ascii=False
        ),
        flush=True
    )

    if audit["feeds_ok"] == 0:
        raise RuntimeError(
            "No fue posible consultar los RSS"
        )

    return list(output.values())


# ============================================================
# PRIORIZACION
# ============================================================

def priority(article):

    content = (
        article["title"]
        + " "
        + article["description"]
    ).lower()

    score = 0

    weights = [
        ("investigación", 4),
        ("research", 4),
        ("descubrimiento", 4),
        ("discovery", 4),
        ("tecnología", 4),
        ("technology", 4),
        ("estudio", 3),
        ("study", 3),
        ("scientists", 3),
        ("científic", 3),
        ("early warning", 4),
        ("alerta temprana", 4),
        ("sensor", 3),
        ("satellite", 3),
        ("satélite", 3),
        ("monitor", 2),
        ("seismic", 2),
        ("sísmic", 2),
    ]

    for term, weight in weights:
        if term in content:
            score += weight

    date = datetime.fromisoformat(
        article["date"]
    )

    age = (
        datetime.now(timezone.utc) - date
    ).total_seconds() / 3600

    score += max(
        0,
        24 - age
    ) * 0.12

    return score


# ============================================================
# TRADUCCION
# ============================================================

def translate_es(value):

    value = clean(value)

    if not value:
        return ""

    key = os.getenv(
        "GEMINI_API_KEY",
        ""
    ).strip()

    if key:

        try:
            model = os.getenv(
                "GEMINI_NEWS_MODEL",
                "gemini-2.5-flash"
            )

            endpoint = (
                "https://generativelanguage.googleapis.com/"
                "v1beta/models/"
                + model
                + ":generateContent"
            )

            prompt = (
                "Traduce fielmente al español de Chile. "
                "No inventes datos. Conserva nombres, "
                "cifras y matices. Devuelve solo la "
                "traducción:\n\n"
                + value
            )

            response = HTTP.post(
                endpoint,
                params={"key": key},
                json={
                    "contents": [{
                        "parts": [{
                            "text": prompt
                        }]
                    }],
                    "generationConfig": {
                        "temperature": 0.0
                    },
                },
                timeout=30
            )

            response.raise_for_status()

            parts = (
                response.json()["candidates"][0]
                ["content"]["parts"]
            )

            result = clean(
                "".join(
                    part.get("text", "")
                    for part in parts
                )
            )

            if len(result) >= 12:
                return result

        except Exception as exc:

            print(
                "Traduccion Gemini:",
                type(exc).__name__,
                flush=True
            )

    try:

        response = get(
            "https://translate.googleapis.com/"
            "translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "es",
                "dt": "t",
                "q": value,
            },
            timeout=18
        )

        result = clean(
            "".join(
                piece[0]
                for piece in response.json()[0]
                if piece and piece[0]
            )
        )

        if len(result) >= 12:
            return result

    except Exception as exc:

        print(
            "Traduccion alternativa:",
            type(exc).__name__,
            flush=True
        )

    return None


def spanish_article(article):

    if article["lang"] == "es":
        return dict(article)

    title = translate_es(
        article["title"]
    )

    if not title:
        return None

    description = ""

    if article.get("description"):

        description = translate_es(
            article["description"]
        ) or ""

    converted = dict(article)

    converted["original_title"] = article["title"]
    converted["title"] = title
    converted["description"] = description

    return converted


# ============================================================
# LECTURA DEL ARTICULO
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
                self.block - 1
            )

        elif tag == "p" and self.in_paragraph:

            paragraph = clean(
                " ".join(self.parts)
            )

            if len(paragraph) > 65:
                self.paragraphs.append(paragraph)

            self.in_paragraph = False
            self.parts = []

    def handle_data(self, data):

        if self.in_paragraph and not self.block:
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
            }
        )

        if "html" not in response.headers.get(
            "Content-Type",
            ""
        ).lower():
            return ""

        if len(response.content) > 3_000_000:
            return ""

        parser = ArticleParser()

        parser.feed(response.text)

        paragraphs = []
        seen = set()

        for paragraph in parser.paragraphs:

            fingerprint = paragraph.lower()[:90]

            if fingerprint in seen:
                continue

            seen.add(fingerprint)

            if re.search(
                r"(?i)cookies|privacy policy|"
                r"subscribe|advertisement|copyright",
                paragraph
            ):
                continue

            paragraphs.append(paragraph)

        content = " ".join(paragraphs)

        if len(content.split()) < 160:
            return ""

        return content[:16000]

    except Exception as exc:

        print(
            "Articulo no accesible:",
            type(exc).__name__,
            str(exc)[:100],
            flush=True
        )

        return ""


# ============================================================
# REDACCION CON IA
# ============================================================

def ai_explanation(article, body):

    prompt = (
        "Eres periodista científico de SISMONAUTA. "
        "Redacta una explicación original en español "
        "de Chile, de aproximadamente 160 a 240 "
        "palabras y entre 3 y 4 párrafos.\n\n"

        "Debes explicar, cuando estén documentados:\n"
        "1. Qué se descubrió o investigó.\n"
        "2. Quién realizó la investigación.\n"
        "3. Qué metodología se utilizó.\n"
        "4. Cuáles fueron los resultados.\n"
        "5. Por qué importa el hallazgo.\n"
        "6. Qué limitaciones existen.\n\n"

        "REGLAS:\n"
        "- Utiliza solo información respaldada "
        "por el artículo.\n"
        "- No inventes resultados, nombres, cifras "
        "ni instituciones.\n"
        "- No copies textualmente el artículo.\n"
        "- No incluyas fuentes ni enlaces.\n"
        "- No incluyas créditos fotográficos.\n"
        "- No incluyas hashtags.\n"
        "- No incluyas titular.\n"
        "- Si el texto es insuficiente, responde "
        "solo INSUFICIENTE.\n\n"

        f"TITULAR: {article['title']}\n"
        f"MEDIO: {article.get('source', '')}\n\n"
        f"ARTICULO:\n{body[:14500]}"
    )

    # --------------------------------------------------------
    # GEMINI
    # --------------------------------------------------------

    key = os.getenv(
        "GEMINI_API_KEY",
        ""
    ).strip()

    if key:

        try:

            model = os.getenv(
                "GEMINI_NEWS_MODEL",
                "gemini-2.5-flash"
            )

            endpoint = (
                "https://generativelanguage.googleapis.com/"
                "v1beta/models/"
                + model
                + ":generateContent"
            )

            response = HTTP.post(
                endpoint,
                params={"key": key},
                json={
                    "contents": [{
                        "parts": [{
                            "text": prompt
                        }]
                    }],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 1200
                    },
                },
                timeout=55
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

            if (
                "INSUFICIENTE" not in result.upper()
                and len(result.split()) >= 115
            ):
                return result

        except Exception as exc:

            print(
                "Gemini no disponible:",
                type(exc).__name__,
                str(exc)[:120],
                flush=True
            )

    # --------------------------------------------------------
    # OPENROUTER
    # --------------------------------------------------------

    key = os.getenv(
        "OPENROUTER_API_KEY",
        ""
    ).strip()

    if key:

        try:

            model = os.getenv(
                "OPENROUTER_NEWS_MODEL",
                "openai/gpt-4o-mini"
            )

            response = HTTP.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + key
                },
                json={
                    "model": model,
                    "temperature": 0.1,
                    "max_tokens": 850,
                    "messages": [{
                        "role": "user",
                        "content": prompt
                    }],
                },
                timeout=55
            )

            response.raise_for_status()

            result = (
                response.json()["choices"][0]
                ["message"]["content"]
            ).strip()

            if (
                "INSUFICIENTE" not in result.upper()
                and len(result.split()) >= 115
            ):
                return result

        except Exception as exc:

            print(
                "OpenRouter no disponible:",
                type(exc).__name__,
                str(exc)[:120],
                flush=True
            )

    # --------------------------------------------------------
    # GROQ
    # --------------------------------------------------------

    key = os.getenv(
        "GROQ_API_KEY",
        ""
    ).strip()

    if key:

        try:

            model = os.getenv(
                "GROQ_NEWS_MODEL",
                "llama-3.3-70b-versatile"
            )

            response = HTTP.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + key
                },
                json={
                    "model": model,
                    "temperature": 0.1,
                    "max_tokens": 850,
                    "messages": [{
                        "role": "user",
                        "content": prompt
                    }],
                },
                timeout=55
            )

            response.raise_for_status()

            result = (
                response.json()["choices"][0]
                ["message"]["content"]
            ).strip()

            if (
                "INSUFICIENTE" not in result.upper()
                and len(result.split()) >= 115
            ):
                return result

        except Exception as exc:

            print(
                "Groq no disponible:",
                type(exc).__name__,
                str(exc)[:120],
                flush=True
            )

    return None


def detailed_explanation(article):

    body = full_article_text(article)

    if not body:

        print(
            "Sin articulo completo accesible",
            flush=True
        )

        return None

    explanation = ai_explanation(
        article,
        body
    )

    if not explanation:

        print(
            "No se obtuvo explicacion extensa con IA",
            flush=True
        )

    return explanation


# ============================================================
# TEXTO ALTERNATIVO
# ============================================================

def evidence_brief(article):

    """
    Redaccion de respaldo basada exclusivamente
    en el resumen recibido.

    No inventa metodologia ni resultados.
    """

    title = clean(
        article.get("title", "")
    )

    summary = clean(
        article.get("description", "")
    )

    if len(summary.split()) < 18:
        return None

    text = (
        "Una información reciente del ámbito "
        "de la sismología aborda: "
        + title.rstrip(".")
        + ". "

        "De acuerdo con la descripción disponible, "
        + summary.rstrip(".")
        + ".\n\n"

        "Esta publicación ofrece una síntesis "
        "preliminar de la información disponible. "
        "El contenido accesible no permite confirmar "
        "todos los detalles de la metodología, los "
        "resultados ni sus limitaciones. "

        "Los avances científicos deben interpretarse "
        "en su contexto y no como una capacidad "
        "para predecir la fecha exacta de un terremoto. "
        "Para SISMONAUTA, compartir información "
        "responsable es tan importante como conocer "
        "las nuevas investigaciones."
    )

    return text


# ============================================================
# BUSQUEDA DE FOTOGRAFIAS
# ============================================================

def tokens(text):

    return [
        word
        for word in re.findall(
            r"[a-záéíóúñü]{4,}",
            text.lower()
        )
        if word not in STOP
    ][:12]


def commons_photo(article):

    title = article["title"]

    original = article.get(
        "original_title",
        title
    )

    content = (
        title
        + " "
        + original
        + " "
        + article.get("description", "")
    ).lower()

    searches = []

    if re.search(
        r"tsunami|coast|ocean|shore|cost|mar",
        content
    ):
        searches.extend([
            (
                "tsunami affected coast",
                ("tsunami", "coast", "shore")
            ),
            (
                "tsunami shoreline",
                ("tsunami", "coast", "shore")
            ),
        ])

    if re.search(
        r"sensor|detect|warning|alert|"
        r"sism[oó]graf|seismomet|monitor",
        content
    ):
        searches.extend([
            (
                "seismic monitoring station landscape",
                ("seismic", "seismometer", "station")
            ),
            (
                "USGS seismic station",
                ("seismic", "seismometer", "usgs")
            ),
        ])

    if re.search(
        r"fault|falla|tectonic|tect[oó]nic|"
        r"subduct|geolog",
        content
    ):
        searches.extend([
            (
                "San Andreas Fault landscape",
                ("fault", "tectonic", "geologic")
            ),
            (
                "earthquake ground rupture landscape",
                ("earthquake", "fault", "rupture")
            ),
        ])

    if not searches:

        searches = [
            (
                "earthquake fault landscape",
                ("earthquake", "fault", "rupture")
            )
        ]

    candidates = []

    for query, relevant_terms in searches[:6]:

        try:

            data = get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": 6,
                    "gsrlimit": 45,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|mime",
                    "iiurlwidth": 1500,
                    "format": "json",
                },
                timeout=25
            ).json()

        except Exception as exc:

            print(
                "Wikimedia no disponible:",
                type(exc).__name__,
                flush=True
            )

            continue

        pages = (
            data.get("query") or {}
        ).get("pages", {})

        for page in pages.values():

            info = (
                page.get("imageinfo") or [{}]
            )[0]

            metadata = (
                info.get("extmetadata") or {}
            )

            license_name = (
                clean(
                    metadata.get(
                        "LicenseShortName", {}
                    ).get("value", "")
                )
                + " "
                + clean(
                    metadata.get(
                        "License", {}
                    ).get("value", "")
                )
            ).lower()

            usage = clean(
                metadata.get(
                    "UsageTerms", {}
                ).get("value", "")
            ).lower()

            allowed = (
                re.search(
                    r"\bcc0\b|public domain|publicdomain|pdm",
                    license_name
                )
                or "public domain" in usage
            )

            if not allowed:
                continue

            name = page.get(
                "title",
                ""
            )

            description = clean(
                metadata.get(
                    "ImageDescription", {}
                ).get("value", "")
            )

            searchable = (
                name + " " + description
            ).lower()

            banned = re.search(
                r"(?i)\b("
                r"moon|lunar|mars|planetary|"
                r"spacecraft|diagram|map|chart|"
                r"poster|drawing|illustration|"
                r"screenshot|portrait|people|"
                r"person|crowd|tourist|"
                r"building|logo|render"
                r")\b",
                searchable
            )

            if banned:
                continue

            matches = sum(
                term in searchable
                for term in relevant_terms
            )

            if matches < 1:
                continue

            if not SCIENCE.search(searchable):
                continue

            image_url = (
                info.get("thumburl")
                or info.get("url")
            )

            if not image_url:
                continue

            mime = info.get(
                "mime",
                ""
            )

            if mime not in (
                "image/jpeg",
                "image/png",
                "image/webp",
            ):
                continue

            candidates.append({
                "score": matches,
                "title": name,
                "url": image_url,
                "description": description,
                "license": license_name,
            })

        if len(candidates) >= 3:
            break

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    for candidate in candidates:

        try:

            response = get(
                candidate["url"],
                timeout=30
            )

            if len(response.content) > 15_000_000:
                continue

            image = Image.open(
                io.BytesIO(response.content)
            ).convert("RGB")

            if min(image.size) < 450:
                continue

            return image, {
                "title": candidate["title"],
                "url": candidate["url"],
                "license": candidate["license"],
                "description": candidate["description"][:200],
                "illustrative": True,
            }

        except Exception:
            continue

    return None, None


# ============================================================
# FONDO GRAFICO ORIGINAL DE RESPALDO
# ============================================================

def original_science_background(article):

    """
    Genera una composición geológica original.

    No necesita fotografías externas.

    No representa una fotografía documental
    del hallazgo científico.
    """

    seed = int(
        hashlib.sha256(
            article["id"].encode("utf-8")
        ).hexdigest()[:12],
        16
    )

    rng = random.Random(seed)

    W = 1080
    H = 1350

    image = Image.new(
        "RGB",
        (W, H),
        (5, 27, 53)
    )

    draw = ImageDraw.Draw(image)

    for y in range(H):

        t = y / H

        color = (
            int(8 + 17 * t),
            int(42 + 32 * t),
            int(83 + 30 * t),
        )

        draw.line(
            (0, y, W, y),
            fill=color
        )

    # Capas geologicas abstractas

    for layer in range(7):

        points = []

        base = 425 + layer * 116

        phase = rng.uniform(
            0,
            math.tau
        )

        for x in range(-20, W + 40, 16):

            y = (
                base
                + 65 * math.sin(x / 160 + phase)
                + 22 * math.sin(x / 37 + phase / 2)
            )

            points.append(
                (x, int(y))
            )

        points.extend([
            (W, H),
            (0, H)
        ])

        color = (
            10 + layer * 8,
            55 + layer * 8,
            87 + layer * 4,
        )

        draw.polygon(
            points,
            fill=color
        )

    # Lineas abstractas que recuerdan
    # estructuras geologicas

    for shift in (-25, 20, 75):

        points = [
            (160 + shift, 1130),
            (350 + shift, 860),
            (570 + shift, 780),
            (780 + shift, 490),
            (1030 + shift, 400),
        ]

        draw.line(
            points,
            fill=(226, 139, 62),
            width=7 if shift == 20 else 3
        )

    credit = {
        "title": "Arte editorial original SISMONAUTA",
        "url": "",
        "license": "Obra original",
        "description": (
            "Composicion geologica abstracta. "
            "No es una fotografia documental."
        ),
        "illustrative": True,
        "generated": True,
    }

    return image, credit


# ============================================================
# TIPOGRAFIA
# ============================================================

def font(size, bold=False):

    if bold:

        paths = [
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf",

            "/usr/share/fonts/truetype/liberation2/"
            "LiberationSans-Bold.ttf",
        ]

    else:

        paths = [
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf",
        ]

    for path in paths:

        if Path(path).exists():

            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


def wrap_text(draw, text, selected_font, width):

    words = text.split()

    lines = []
    current = ""

    for word in words:

        proposed = (
            current + " " + word
        ).strip()

        measured = draw.textbbox(
            (0, 0),
            proposed,
            font=selected_font
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
# DISEÑO DE LA PUBLICACION
# ============================================================

def make_card(image, article, path):

    W = 1080
    H = 1350

    canvas = ImageOps.fit(
        image,
        (W, H),
        Image.Resampling.LANCZOS,
        centering=(0.5, 0.44)
    ).convert("RGBA")

    overlay = Image.new(
        "RGBA",
        (W, H),
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(overlay)

    # Cabecera SISMONAUTA

    draw.rectangle(
        (0, 0, W, 100),
        fill=(4, 21, 46, 235)
    )

    draw.text(
        (54, 27),
        "SISMONAUTA  |  ACTUALIDAD",
        font=font(37, True),
        fill="white"
    )

    title = clean(
        article["title"]
    )

    if not title:
        raise ValueError(
            "Noticia sin titular"
        )

    selected_font = None
    lines = None
    line_height = 0
    band_height = 0

    for size in range(70, 23, -2):

        test_font = font(
            size,
            True
        )

        candidate_lines = wrap_text(
            draw,
            title,
            test_font,
            W - 120
        )

        leading = int(
            size * 1.27
        )

        height = max(
            210,
            len(candidate_lines) * leading + 100
        )

        if height <= 690:

            selected_font = test_font
            lines = candidate_lines
            line_height = leading
            band_height = height

            break

    if not lines:
        raise ValueError(
            "Titular demasiado largo"
        )

    bottom = H - 94
    top = bottom - band_height

    # Banner azul

    draw.rectangle(
        (0, top, W, bottom),
        fill=(0, 87, 202, 248)
    )

    draw.rectangle(
        (0, top, W, top + 9),
        fill=(36, 163, 255, 255)
    )

    total_height = (
        len(lines) * line_height
    )

    y = top + (
        band_height - total_height
    ) // 2

    # Titular blanco y contorno negro

    for line in lines:

        bbox = draw.textbbox(
            (0, 0),
            line,
            font=selected_font,
            stroke_width=3
        )

        text_width = (
            bbox[2] - bbox[0]
        )

        x = (
            W - text_width
        ) // 2

        draw.text(
            (x, y),
            line,
            font=selected_font,
            fill=(255, 255, 255, 255),
            stroke_width=3,
            stroke_fill=(0, 0, 0, 255)
        )

        y += line_height

    # Pie de identidad grafica

    draw.rectangle(
        (0, H - 94, W, H),
        fill=(3, 24, 49, 245)
    )

    draw.text(
        (54, H - 67),
        "CIENCIA  •  SISMOS  •  TECNOLOGÍA",
        font=font(28, True),
        fill="white"
    )

    result = Image.alpha_composite(
        canvas,
        overlay
    ).convert("RGB")

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    result.save(
        path,
        "JPEG",
        quality=91,
        optimize=True
    )


# ============================================================
# DESCRIPCION PARA REDES SOCIALES
# ============================================================

def caption(article, credit):

    explanation = (
        article.get("explanation") or ""
    ).strip()

    if len(explanation.split()) < 55:

        raise ValueError(
            "La explicacion disponible "
            "es demasiado breve"
        )

    parts = [
        "🌎 SISMONAUTA | ACTUALIDAD SÍSMICA",
        "",
        clean(article["title"]),
        "",
        explanation,
        "",
        (
            "¿Qué opinas de este descubrimiento? "
            "Te leemos en los comentarios."
        ),
        "",
        (
            "#SISMONAUTA #Sismologia #Ciencia "
            "#Terremotos #Tecnologia #Investigacion "
            "#Geologia #Tectonica"
        )
    ]

    text = "\n".join(parts)

    if len(text) > 2200:

        raise ValueError(
            "Descripcion demasiado larga "
            "para Instagram"
        )

    return text


# ============================================================
# GITHUB API
# ============================================================

class Github:

    def __init__(self):

        repository = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = (
            os.getenv("GITHUB_REF_NAME")
            or "main"
        )

        self.base = (
            "https://api.github.com/repos/"
            + repository
        )

        token = os.environ[
            "GH_TOKEN"
        ]

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

        response = self.session.get(
            self.base,
            timeout=20
        )

        response.raise_for_status()

        if response.json().get("private"):

            raise RuntimeError(
                "El repositorio debe ser publico "
                "para alojar las imagenes"
            )

    def read(self, path):

        endpoint = (
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path,
                safe="/"
            )
        )

        response = self.session.get(
            endpoint,
            params={
                "ref": self.branch
            },
            timeout=25
        )

        if response.status_code == 404:
            return None, None

        response.raise_for_status()

        data = response.json()

        return (
            base64.b64decode(data["content"]),
            data["sha"]
        )

    def write(self, path, raw):

        endpoint = (
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path,
                safe="/"
            )
        )

        for attempt in range(3):

            previous, sha = self.read(path)

            payload = {
                "message": (
                    "SISMONAUTA noticias: "
                    + path
                ),
                "branch": self.branch,
                "content": base64.b64encode(
                    raw
                ).decode(),
            }

            if sha:
                payload["sha"] = sha

            response = self.session.put(
                endpoint,
                json=payload,
                timeout=50
            )

            if (
                response.status_code in (409, 422)
                and attempt < 2
            ):
                time.sleep(2)
                continue

            response.raise_for_status()
            return

    def image_url(self, path):

        repository = os.environ[
            "GITHUB_REPOSITORY"
        ]

        return (
            "https://raw.githubusercontent.com/"
            + repository
            + "/"
            + urllib.parse.quote(
                self.branch,
                safe=""
            )
            + "/"
            + urllib.parse.quote(
                path,
                safe="/"
            )
        )


# ============================================================
# META GRAPH API
# ============================================================

def graph(
    method,
    path,
    token,
    params=None,
    base=IG_BASE
):

    endpoint = (
        f"{base}/{VERSION}/{path}"
    )

    payload = dict(
        params or {}
    )

    payload["access_token"] = token

    if method == "GET":

        response = requests.get(
            endpoint,
            params=payload,
            timeout=55
        )

    else:

        response = requests.post(
            endpoint,
            data=payload,
            timeout=70
        )

    try:

        data = response.json()

    except Exception:

        raise RuntimeError(
            "Meta devolvio una respuesta no JSON"
        )

    if not response.ok or data.get("error"):

        error = data.get(
            "error",
            {}
        )

        raise RuntimeError(
            "Meta HTTP "
            + str(response.status_code)
            + ": "
            + str(
                error.get(
                    "message",
                    data
                )
            )[:230]
        )

    return data


# ============================================================
# VALIDAR INSTAGRAM
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
        }
    )

    if (
        str(info.get("id")) != user_id
        or info.get("username", "").lower()
        != ACCOUNT
    ):

        raise RuntimeError(
            "Cuenta Instagram incorrecta"
        )

    print(
        "INSTAGRAM @sismonauta verificado",
        flush=True
    )

    return user_id, token


# ============================================================
# VALIDAR FACEBOOK
# ============================================================

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
        FB_BASE
    )

    if str(info.get("id")) != page_id:

        raise RuntimeError(
            "Facebook Page ID incorrecto"
        )

    if (
        info.get("username")
        and info["username"].lower() != ACCOUNT
    ):

        raise RuntimeError(
            "La fanpage no corresponde "
            "a SISMONAUTA"
        )

    print(
        "FACEBOOK validado:",
        info.get("name"),
        flush=True
    )

    return page_id, token


# ============================================================
# PUBLICAR INSTAGRAM
# ============================================================

def publish_ig(
    user_id,
    token,
    image_url,
    text
):

    container = graph(
        "POST",
        user_id + "/media",
        token,
        {
            "image_url": image_url,
            "caption": text,
        }
    )["id"]

    for attempt in range(18):

        time.sleep(8)

        result = graph(
            "GET",
            container,
            token,
            {
                "fields": "status_code"
            }
        )

        status = result.get(
            "status_code"
        )

        if status == "FINISHED":
            break

        if status in ("ERROR", "EXPIRED"):

            raise RuntimeError(
                "Instagram rechazo la imagen: "
                + status
            )

    else:

        raise RuntimeError(
            "Contenedor Instagram "
            "sin confirmacion"
        )

    result = graph(
        "POST",
        user_id + "/media_publish",
        token,
        {
            "creation_id": container
        }
    )

    return result["id"]


# ============================================================
# PUBLICAR FACEBOOK
# ============================================================

def publish_fb(
    page_id,
    token,
    image_url,
    text
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
        FB_BASE
    )

    return (
        result.get("post_id")
        or result.get("id")
    )


# ============================================================
# HISTORIAL ANTIDUPLICADOS
# ============================================================

def load_state(github):

    raw, sha = github.read(
        STATE_FILE
    )

    if raw:
        return json.loads(raw)

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
            sort_keys=True
        ).encode("utf-8")
    )


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--publish",
        action="store_true"
    )

    parser.add_argument(
        "--out",
        default="output_noticias"
    )

    args = parser.parse_args()

    output = Path(
        args.out
    )

    output.mkdir(
        parents=True,
        exist_ok=True
    )

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

    # --------------------------------------------------------
    # BUSQUEDA DE NOTICIAS
    # --------------------------------------------------------

    articles = feed_items()

    articles.sort(
        key=priority,
        reverse=True
    )

    print(
        "NOTICIAS CANDIDATAS:",
        len(articles),
        flush=True
    )

    if not articles:

        print(
            "SIN NOTICIAS VERIFICABLES",
            flush=True
        )

        write_json(
            output / "estado_publicacion.json",
            {
                "status": "no_eligible_news",
                "published": False,
            }
        )

        return 0

    chosen = None
    image = None
    credit = None

    reasons = {
        "already_seen": 0,
        "no_photo": 0,
        "no_translation": 0,
        "no_article_or_ai": 0,
        "invalid_caption": 0,
    }

    # --------------------------------------------------------
    # EVALUAR CANDIDATOS
    # --------------------------------------------------------

    for original in articles[:MAX_ARTICLES]:

        if original["id"] in state["stories"]:

            reasons["already_seen"] += 1

            continue

        print(
            "EVALUANDO:",
            original["title"][:110],
            flush=True
        )

        # ----------------------------------------------------
        # TRADUCIR
        # ----------------------------------------------------

        article = spanish_article(
            original
        )

        if article is None:

            reasons["no_translation"] += 1

            print(
                "OMITIDA: traduccion no disponible",
                flush=True
            )

            continue

        # ----------------------------------------------------
        # DESCRIPCION COMPLETA
        # ----------------------------------------------------

        explanation = detailed_explanation(
            article
        )

        # ----------------------------------------------------
        # ALTERNATIVA SIN ARTICULO COMPLETO
        # ----------------------------------------------------

        if not explanation:

            explanation = evidence_brief(
                article
            )

            if explanation:

                print(
                    "REDACCION DE RESPALDO:",
                    "sintesis preliminar",
                    flush=True
                )

        if not explanation:

            reasons["no_article_or_ai"] += 1

            print(
                "OMITIDA: informacion insuficiente",
                flush=True
            )

            continue

        article["explanation"] = explanation

        # ----------------------------------------------------
        # BUSCAR FOTOGRAFIA
        # ----------------------------------------------------

        photo, metadata = commons_photo(
            original
        )

        # ----------------------------------------------------
        # FONDO DE RESPALDO
        # ----------------------------------------------------

        if photo is None:

            reasons["no_photo"] += 1

            photo, metadata = original_science_background(
                article
            )

            print(
                "FONDO PROPIO GENERADO",
                flush=True
            )

        # ----------------------------------------------------
        # COMPROBAR TEXTO
        # ----------------------------------------------------

        try:

            caption(
                article,
                metadata
            )

        except ValueError as exc:

            reasons["invalid_caption"] += 1

            print(
                "OMITIDA:",
                str(exc),
                flush=True
            )

            continue

        chosen = article
        image = photo
        credit = metadata

        break

    # --------------------------------------------------------
    # SIN CANDIDATO
    # --------------------------------------------------------

    if chosen is None:

        print(
            "AUDITORIA SELECCION:",
            json.dumps(
                reasons,
                ensure_ascii=False
            ),
            flush=True
        )

        print(
            "No se obtuvo una noticia "
            "con informacion suficiente.",
            flush=True
        )

        write_json(
            output / "estado_publicacion.json",
            {
                "status": "no_eligible_news",
                "published": False,
                "articles_reviewed": min(
                    len(articles),
                    MAX_ARTICLES
                ),
                "reasons": reasons,
            }
        )

        return 0

    # --------------------------------------------------------
    # GENERAR IMAGEN
    # --------------------------------------------------------

    image_file = (
        output
        / (chosen["id"] + ".jpg")
    )

    make_card(
        image,
        chosen,
        image_file
    )

    description = caption(
        chosen,
        credit
    )

    write_json(
        output / "noticia.json",
        {
            "article": chosen,
            "image": credit,
            "caption": description,
        }
    )

    print(
        "NOTICIA SELECCIONADA:",
        chosen["title"],
        flush=True
    )

    print(
        "RECURSO VISUAL:",
        credit["title"],
        flush=True
    )

    # --------------------------------------------------------
    # MODO PRUEBA
    # --------------------------------------------------------

    if not args.publish:

        print(
            "MODO VISTA PREVIA",
            flush=True
        )

        write_json(
            output / "estado_publicacion.json",
            {
                "status": "preview_only",
                "published": False,
                "image": str(image_file),
            }
        )

        return 0

    # --------------------------------------------------------
    # COMPROBAR CUENTAS
    # --------------------------------------------------------

    instagram_id, instagram_token = ig_check()

    facebook_id, facebook_token = fb_check()

    # --------------------------------------------------------
    # SUBIR IMAGEN A GITHUB
    # --------------------------------------------------------

    remote_path = (
        "sismonauta_noticias/images/"
        + chosen["id"]
        + ".jpg"
    )

    github.write(
        remote_path,
        image_file.read_bytes()
    )

    image_url = github.image_url(
        remote_path
    )

    response = HTTP.get(
        image_url,
        timeout=25
    )

    if (
        not response.ok
        or not response.headers.get(
            "Content-Type",
            ""
        ).startswith("image/")
    ):

        raise RuntimeError(
            "La imagen no esta disponible "
            "publicamente para Meta"
        )

    # --------------------------------------------------------
    # HISTORIAL PENDIENTE
    # --------------------------------------------------------

    state["stories"][chosen["id"]] = {
        "article": chosen,
        "image": credit,
        "targets": {
            "instagram": {
                "status": "pending"
            },
            "facebook": {
                "status": "pending"
            },
        },
    }

    save_state(
        github,
        state
    )

    failures = []

    # --------------------------------------------------------
    # PUBLICAR EN AMBAS REDES
    # --------------------------------------------------------

    for target in (
        "instagram",
        "facebook"
    ):

        entry = (
            state["stories"][chosen["id"]]
            ["targets"][target]
        )

        # Marcar antes de enviar a Meta.
        # Evita duplicados ante respuestas inciertas.

        entry["status"] = "uncertain"

        save_state(
            github,
            state
        )

        try:

            if target == "instagram":

                identifier = publish_ig(
                    instagram_id,
                    instagram_token,
                    image_url,
                    description
                )

            else:

                identifier = publish_fb(
                    facebook_id,
                    facebook_token,
                    image_url,
                    description
                )

            if not identifier:

                raise RuntimeError(
                    "Meta no devolvio identificador"
                )

            entry.update(
                status="published",
                id=str(identifier)
            )

            save_state(
                github,
                state
            )

            print(
                "PUBLICADO:",
                target,
                "ID:",
                identifier,
                flush=True
            )

        except Exception as exc:

            failures.append(
                target + ": " + str(exc)
            )

            print(
                "ERROR:",
                target,
                str(exc)[:250],
                flush=True
            )

    # --------------------------------------------------------
    # RECIBO DE PUBLICACION
    # --------------------------------------------------------

    receipt = {
        "status": (
            "published"
            if not failures
            else "partial_or_uncertain"
        ),
        "article_id": chosen["id"],
        "article_title": chosen["title"],
        "targets": (
            state["stories"][chosen["id"]]
            ["targets"]
        ),
    }

    write_json(
        output / "estado_publicacion.json",
        receipt
    )

    if failures:

        raise RuntimeError(
            "Publicacion parcial o incierta. "
            "Revisar antes de reintentar: "
            + " / ".join(failures)
        )

    print(
        "PUBLICACION CONFIRMADA EN "
        "INSTAGRAM Y FACEBOOK",
        flush=True
    )

    return 0


# ============================================================
# EJECUCION
# ============================================================

if __name__ == "__main__":

    try:
        sys.exit(main())

    except Exception as exc:

        print(
            "ERROR:",
            exc,
            file=sys.stderr,
            flush=True
        )

        sys.exit(1)
