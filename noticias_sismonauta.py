
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA - Noticias científicas sísmicas.

Un único archivo Python.
Busca noticias recientes sobre avances, estudios y tecnología.
Excluye noticias que únicamente reportan terremotos.

Genera una imagen 1080 x 1350:
- Fotografía relacionada con la noticia
- Banner azul
- Titular blanco con contorno negro

Redacta una explicación extensa a partir del artículo.
Publica una imagen en Instagram y Facebook.

Dependencias:
    pip install requests pillow

Secretos:
    INSTAGRAM_TOKEN
    INSTAGRAM_USER_ID
    FACEBOOK_PAGE_TOKEN
    FACEBOOK_PAGE_ID
    GEMINI_API_KEY (o OPENROUTER_API_KEY o GROQ_API_KEY)
    GH_TOKEN
    GITHUB_REPOSITORY
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import os
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
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# CONFIGURACION
# ============================================================

UA = "SismonautaNews/2.0"

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": UA})

VERSION = os.getenv("IG_GRAPH_VERSION", "v25.0")

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

STATE_FILE = "sismonauta_noticias/state.json"

ACCOUNT = "sismonauta"

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
    ("en", "seismology research when:1d"),
    ("en", "earthquake technology when:1d"),
    ("en", "tsunami research when:1d"),
    ("en", "seismic early warning technology when:1d"),
    ("en", "geophysics fault scientific discovery when:1d"),
    ("en", "earthquake sensors scientific study when:1d"),
    ("en", "tectonic plates new research when:1d"),
    ("en", "earthquake satellite monitoring research when:1d"),
    ("es", "nueva investigación terremotos when:1d"),
    ("es", "estudio científico fallas geológicas when:1d"),
]

SCIENCE = re.compile(
    r"(?i)\b("
    r"sism|sismic|sísmic|seismic|seismolog|"
    r"earthquake|terremot|tsunami|tectoni|tectóni|"
    r"fault|falla|geof[ií]sic|geophysic"
    r")"
)

ADVANCE = re.compile(
    r"(?i)\b("
    r"estudio|investigaci[oó]n|investigador|"
    r"cient[ií]fic|descubrim|hallazgo|avance|"
    r"tecnolog|sensor|sat[eé]lit|alerta|sistema|"
    r"desarroll|m[eé]todo|inteligencia artificial|"
    r"modelo|research|study|scientist|discovery|"
    r"breakthrough|technology|satellite|warning|"
    r"innovation|develop|model|detect|predict|"
    r"monitor|mapping"
    r")"
)

EVENT = re.compile(
    r"(?i)\b("
    r"sismo de magnitud|temblor de magnitud|"
    r"terremoto de magnitud|earthquake of magnitude|"
    r"magnitude [0-9]|sismo hoy|earthquake strikes|"
    r"earthquake hits|earthquake struck|temblor hoy|"
    r"sismo se registr[oó]|earthquake reported|"
    r"earthquake shakes|terremoto sacude|reportan sismo"
    r")"
)

SPAM = re.compile(
    r"(?i)\b("
    r"horoscope|hor[oó]scopo|astrolog|"
    r"conspiracy|viral video|tiktok|apocalipsis"
    r")"
)

STOP = set(
    "a al algo ante con contra de del desde el en entre "
    "es esta este estas estos fue han hoy la las lo los "
    "más para por que se sin sobre su sus un una unos unas "
    "y the of in on to for with from as new how what why "
    "at by and scientists study research earthquake seismic "
    "tsunami can could may have has reveals reveal".split()
)


# ============================================================
# UTILIDADES
# ============================================================

def clean(value):
    value = html.unescape(
        re.sub("<[^>]*>", " ", value or "")
    )
    return re.sub(r"\s+", " ", value).strip()


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
# NOTICIAS DE LAS ULTIMAS 24 HORAS
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
                get(url, timeout=30).content
            )
            audit["feeds_ok"] += 1

        except Exception as exc:
            audit["feeds_failed"] += 1
            print(
                "RSS no disponible:",
                lang,
                query,
                str(exc)[:120],
                flush=True
            )
            continue

        entries = root.findall(".//channel/item")

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
                    " - ", 1
                )
                if (
                    not provider
                    or suffix.lower() == provider.lower()
                ):
                    title = prefix.strip()

            content = title + " " + description

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
                ).encode()
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
            "No se pudo consultar ninguna fuente RSS"
        )

    return list(output.values())


# ============================================================
# PRIORIDAD EDITORIAL
# ============================================================

def priority(article):

    content = (
        article["title"]
        + " "
        + article["description"]
    ).lower()

    score = 0

    weights = [
        ("investigación", 5),
        ("research", 5),
        ("descubrimiento", 5),
        ("discovery", 5),
        ("tecnología", 4),
        ("technology", 4),
        ("estudio", 4),
        ("study", 4),
        ("early warning", 4),
        ("alerta temprana", 4),
        ("sensor", 3),
        ("satélite", 3),
        ("satellite", 3),
        ("seismic", 2),
        ("sísmic", 2),
    ]

    for term, weight in weights:
        if term in content:
            score += weight

    date = datetime.fromisoformat(
        article["date"]
    )

    age_hours = (
        datetime.now(timezone.utc) - date
    ).total_seconds() / 3600

    score += max(
        0,
        24 - age_hours
    ) * 0.12

    return score


# ============================================================
# TRADUCCION AL ESPANOL
# ============================================================

def translate_es(text):

    text = clean(text)

    if not text:
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

            url = (
                "https://generativelanguage.googleapis.com/"
                "v1beta/models/"
                + model
                + ":generateContent"
            )

            prompt = (
                "Traduce fielmente al español de Chile. "
                "Conserva cifras, nombres y matices. "
                "No añadas información. "
                "Devuelve solo la traducción:\n\n"
                + text
            )

            response = HTTP.post(
                url,
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
                timeout=35
            )

            response.raise_for_status()

            result = clean(
                response.json()["candidates"][0]
                ["content"]["parts"][0]["text"]
            )

            if len(result) >= 12:
                return result

        except Exception as exc:
            print(
                "Traducción Gemini no disponible:",
                str(exc)[:100],
                flush=True
            )

    # Servicio alternativo; su disponibilidad
    # no está garantizada.

    try:
        response = get(
            "https://translate.googleapis.com/"
            "translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "es",
                "dt": "t",
                "q": text,
            },
            timeout=18
        )

        pieces = response.json()[0]

        result = clean(
            "".join(
                part[0]
                for part in pieces
                if part and part[0]
            )
        )

        if len(result) >= 12:
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

    result["original_title"] = article["title"]
    result["title"] = title

    if article.get("description"):
        description = translate_es(
            article["description"]
        )
        result["description"] = description or ""

    return result


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
                self.paragraphs.append(
                    paragraph
                )

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
            "No se pudo leer artículo:",
            str(exc)[:100],
            flush=True
        )
        return ""


# ============================================================
# REDACCION EXPLICATIVA EXTENSA
# ============================================================

def ai_explanation(article, body):

    prompt = (
        "Eres periodista científico de SISMONAUTA. "
        "Redacta una explicación periodística original "
        "en español de Chile, de 160 a 240 palabras "
        "y organizada en 3 o 4 párrafos.\n\n"

        "La explicación debe describir:\n"
        "1. Qué se descubrió, investigó o anunció.\n"
        "2. Quién realizó el trabajo, si está documentado.\n"
        "3. Cómo se realizó la investigación.\n"
        "4. Cuáles fueron los resultados concretos.\n"
        "5. Por qué es importante para comprender "
        "los sismos, la tectónica o los tsunamis.\n"
        "6. Qué limitaciones presenta, cuando correspondan.\n\n"

        "REGLAS OBLIGATORIAS:\n"
        "- Utiliza solamente información respaldada "
        "por el artículo proporcionado.\n"
        "- No inventes cifras, nombres ni resultados.\n"
        "- No conviertas hipótesis en hechos demostrados.\n"
        "- No copies párrafos textuales.\n"
        "- No incluyas enlaces ni fuentes en la redacción.\n"
        "- No menciones créditos fotográficos.\n"
        "- No añadas hashtags ni titulares.\n"
        "- Si el artículo es insuficiente, "
        "responde solamente INSUFICIENTE.\n\n"

        f"TITULAR: {article['title']}\n"
        f"MEDIO: {article.get('source', '')}\n\n"
        f"ARTÍCULO:\n{body[:14500]}"
    )

    # ---------------- GEMINI ----------------

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
                item.get("text", "")
                for item in parts
            ).strip()

            if (
                "INSUFICIENTE" not in result.upper()
                and len(result.split()) >= 115
            ):
                return result

        except Exception as exc:
            print(
                "Gemini no disponible:",
                str(exc)[:120],
                flush=True
            )

    # ---------------- OPENROUTER ----------------

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
                str(exc)[:120],
                flush=True
            )

    # ---------------- GROQ ----------------

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
                str(exc)[:120],
                flush=True
            )

    return None


def detailed_explanation(article):

    body = full_article_text(article)

    if not body:
        print(
            "Artículo sin texto completo accesible.",
            flush=True
        )
        return None

    text = ai_explanation(
        article,
        body
    )

    if not text:
        print(
            "No fue posible crear una explicación "
            "extensa respaldada por el artículo.",
            flush=True
        )
        return None

    return text


# ============================================================
# FOTOGRAFIA DE WIKIMEDIA COMMONS
# ============================================================

def tokens(text):

    words = re.findall(
        r"[a-záéíóúñü]{4,}",
        text.lower()
    )

    return [
        word
        for word in words
        if word not in STOP
    ][:12]


def commons_photo(article):

    title = article["title"]
    words = tokens(title)

    core = [
        word
        for word in words
        if SCIENCE.search(word)
    ]

    queries = []

    for query in [
        " ".join(words[:4]),
        " ".join((core + words)[:4]),
        " ".join(core[:2]) + " geology landscape",
    ]:
        if query and query not in queries:
            queries.append(query)

    candidates = []

    for query in queries:

        try:
            data = get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query + " filetype:bitmap",
                    "gsrnamespace": 6,
                    "gsrlimit": 15,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|mime",
                    "iiurlwidth": 1500,
                    "format": "json",
                },
                timeout=25
            ).json()

        except Exception as exc:
            print(
                "Wikimedia error:",
                str(exc)[:100],
                flush=True
            )
            continue

        pages = (
            data.get("query") or {}
        ).get("pages", {})

        for page in pages.values():

            info = (
                page.get("imageinfo")
                or [{}]
            )[0]

            metadata = (
                info.get("extmetadata")
                or {}
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

            banned = (
                r"(?i)\b("
                r"moon|lunar|mars|astronaut|"
                r"poster|logo|diagram|chart|"
                r"map|drawing|illustration|"
                r"portrait|people|person|"
                r"crowd|tourist|lroc|"
                r"spacecraft|screenshot"
                r")\b"
            )

            if re.search(banned, searchable):
                continue

            if not SCIENCE.search(searchable):
                continue

            matches = sum(
                1
                for word in set(words[:8])
                if word in searchable
            )

            if matches < 1:
                continue

            image_url = (
                info.get("thumburl")
                or info.get("url")
            )

            if not image_url:
                continue

            if not info.get(
                "mime",
                ""
            ).startswith("image/"):
                continue

            candidates.append({
                "score": matches,
                "name": name,
                "url": image_url,
                "description": description,
                "license": license_name,
            })

        if candidates:
            break

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    for candidate in candidates:

        try:
            response = get(
                candidate["url"],
                timeout=35
            )

            if len(response.content) > 15_000_000:
                continue

            image = Image.open(
                io.BytesIO(
                    response.content
                )
            ).convert("RGB")

            width, height = image.size

            if width < 600 or height < 450:
                continue

            return image, {
                "title": candidate["name"],
                "url": candidate["url"],
                "license": candidate["license"],
                "description": candidate["description"][:250],
                "illustrative": True,
            }

        except Exception:
            continue

    return None, None


# ============================================================
# DISEÑO: BANNER AZUL Y TITULAR
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

        proposal = (
            current + " " + word
        ).strip()

        measured = draw.textbbox(
            (0, 0),
            proposal,
            font=selected_font
        )[2]

        if measured <= width:
            current = proposal
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def make_card(image, article, path):

    W, H = 1080, 1350

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

    # Encabezado institucional

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

    title = clean(article["title"])

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

        test_lines = wrap_text(
            draw,
            title,
            test_font,
            W - 120
        )

        if len(test_lines) > 12:
            continue

        leading = int(
            size * 1.27
        )

        height = max(
            210,
            len(test_lines) * leading + 100
        )

        if height <= 690:
            selected_font = test_font
            lines = test_lines
            line_height = leading
            band_height = height
            break

    if not lines:
        raise ValueError(
            "Titular demasiado largo para el diseño"
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

    for line in lines:

        bbox = draw.textbbox(
            (0, 0),
            line,
            font=selected_font,
            stroke_width=3
        )

        text_width = bbox[2] - bbox[0]

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

    # Pie de marca, sin fuentes ni créditos

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
# TEXTO EXTENSO PARA INSTAGRAM Y FACEBOOK
# ============================================================

def caption(article, credit):

    explanation = article.get(
        "explanation",
        ""
    ).strip()

    if len(explanation.split()) < 115:
        raise ValueError(
            "La explicación es demasiado breve"
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
            "Descripción demasiado larga para Instagram"
        )

    return text


# ============================================================
# GITHUB: ALMACENAMIENTO E HISTORIAL
# ============================================================

class Github:

    def __init__(self):

        repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = (
            os.getenv("GITHUB_REF_NAME")
            or "main"
        )

        self.base = (
            "https://api.github.com/repos/"
            + repo
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
            timeout=20
        )

        response.raise_for_status()

        if response.json().get("private"):
            raise RuntimeError(
                "El repositorio debe ser público "
                "para servir imágenes a Instagram"
            )

    def read(self, path):

        url = (
            self.base
            + "/contents/"
            + urllib.parse.quote(path, safe="/")
        )

        response = self.session.get(
            url,
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
            base64.b64decode(
                data["content"]
            ),
            data["sha"]
        )

    def write(self, path, raw):

        url = (
            self.base
            + "/contents/"
            + urllib.parse.quote(path, safe="/")
        )

        for attempt in range(3):

            old, sha = self.read(path)

            body = {
                "message": (
                    "SISMONAUTA noticias: " + path
                ),
                "branch": self.branch,
                "content": base64.b64encode(
                    raw
                ).decode(),
            }

            if sha:
                body["sha"] = sha

            response = self.session.put(
                url,
                json=body,
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

        repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        return (
            "https://raw.githubusercontent.com/"
            + repo
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

    url = (
        f"{base}/{VERSION}/{path}"
    )

    payload = dict(
        params or {}
    )

    payload["access_token"] = token

    if method == "GET":
        response = requests.get(
            url,
            params=payload,
            timeout=55
        )
    else:
        response = requests.post(
            url,
            data=payload,
            timeout=70
        )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "Meta devolvió una respuesta no JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get("error", {})

        raise RuntimeError(
            f"Meta HTTP {response.status_code}: "
            + str(
                error.get(
                    "message",
                    data
                )
            )[:230]
        )

    return data


# ============================================================
# COMPROBAR CUENTAS
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
            "Token Instagram incorrecto"
        )

    print(
        "Instagram @sismonauta verificado",
        flush=True
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
        FB_BASE
    )

    if str(info.get("id")) != page_id:
        raise RuntimeError(
            "Facebook Page ID incorrecto"
        )

    if (
        info.get("username")
        and info["username"].lower()
        != ACCOUNT
    ):
        raise RuntimeError(
            "La fanpage no corresponde a SISMONAUTA"
        )

    print(
        "Fanpage validada:",
        info.get("name"),
        flush=True
    )

    return page_id, token


# ============================================================
# PUBLICACION EN INSTAGRAM
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

        response = graph(
            "GET",
            container,
            token,
            {
                "fields": "status_code"
            }
        )

        status = response.get(
            "status_code"
        )

        if status == "FINISHED":
            break

        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                "Instagram no procesó la imagen: "
                + status
            )

    else:
        raise RuntimeError(
            "Contenedor Instagram sin confirmar"
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
# PUBLICACION EN FACEBOOK
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
        ).encode()
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

    output = Path(args.out)

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

    articles = feed_items()

    articles.sort(
        key=priority,
        reverse=True
    )

    print(
        "Noticias ES + EN de últimas 24 horas:",
        len(articles),
        flush=True
    )

    if not articles:

        print(
            "SIN NOTICIAS ELEGIBLES",
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

    for original in articles[:35]:

        if original["id"] in state["stories"]:
            continue

        print(
            "Evaluando:",
            original["title"][:110],
            flush=True
        )

        # Buscar foto usando el titular original.
        photo, metadata = commons_photo(
            original
        )

        if photo is None:
            continue

        translated = spanish_article(
            original
        )

        if translated is None:
            continue

        # Exigir una explicación basada en el artículo.
        explanation = detailed_explanation(
            translated
        )

        if not explanation:
            continue

        translated["explanation"] = explanation

        # Verificar longitud antes de elegir.
        try:
            description = caption(
                translated,
                metadata
            )
        except ValueError as exc:
            print(
                "Noticia descartada:",
                str(exc),
                flush=True
            )
            continue

        chosen = translated
        image = photo
        credit = metadata
        break

    if chosen is None:

        print(
            "No existe una noticia publicable "
            "con fotografía y explicación suficientes.",
            flush=True
        )

        write_json(
            output / "estado_publicacion.json",
            {
                "status": "no_licensed_relevant_photo",
                "published": False,
                "articles_reviewed": min(
                    len(articles),
                    35
                ),
            }
        )

        return 0

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
        "FOTOGRAFÍA:",
        credit["title"],
        flush=True
    )

    if not args.publish:

        print(
            "MODO PRUEBA: sin publicación",
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
    # VALIDAR INSTAGRAM Y FACEBOOK
    # --------------------------------------------------------

    instagram_id, instagram_token = ig_check()
    facebook_id, facebook_token = fb_check()

    # --------------------------------------------------------
    # SUBIR IMAGEN PÚBLICA A GITHUB
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
            "La imagen no es pública para Meta"
        )

    # --------------------------------------------------------
    # REGISTRO PENDIENTE
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
    # PUBLICAR EN LAS DOS REDES
    # --------------------------------------------------------

    for target in ("instagram", "facebook"):

        entry = (
            state["stories"][chosen["id"]]
            ["targets"][target]
        )

        # Marcar como incierto antes de enviar.
        # Evita reintentos ciegos y duplicados.
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
                    "Meta no devolvió identificador"
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
    # RECIBO DE PUBLICACIÓN
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
            "Publicación parcial o incierta. "
            "Revisar antes de reintentar: "
            + " / ".join(failures)
        )

    print(
        "PUBLICACIÓN CONFIRMADA EN "
        "INSTAGRAM Y FACEBOOK",
        flush=True
    )

    return 0


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
