
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA - Noticias científicas sísmicas

Busca noticias de las últimas 24 horas sobre:
- Investigaciones sísmicas
- Avances tecnológicos
- Descubrimientos
- Sistemas de alerta
- Tectónica y tsunamis

Excluye reportes sobre la simple ocurrencia de terremotos.

Genera una imagen de 1080x1350 con:
- Fotografía pertinente
- Banner azul
- Titular blanco con contorno negro

Publica en:
- Instagram @sismonauta
- Fanpage Facebook SISMONAUTA

Dependencias:
    pip install requests pillow

Variables de entorno:
    INSTAGRAM_TOKEN
    INSTAGRAM_USER_ID
    FACEBOOK_PAGE_TOKEN
    FACEBOOK_PAGE_ID
    GH_TOKEN
    GITHUB_REPOSITORY
    GITHUB_REF_NAME
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
from pathlib import Path
from zoneinfo import ZoneInfo

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

UA = (
    "SismonautaNews/1.0 "
    "(educational newsroom; Instagram @sismonauta)"
)

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": UA})

VERSION = os.getenv("IG_GRAPH_VERSION", "v25.0")

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

STATE_FILE = "sismonauta_noticias/state.json"

SOURCES = [
    (
        "es",
        "terremotos investigación científica tecnología "
        "-sismo hoy -temblor hoy -magnitud when:1d"
    ),
    (
        "es",
        "sismología estudio descubrimiento sensores "
        "alerta temprana when:1d"
    ),
    (
        "es",
        "tsunami investigación científica "
        "nueva tecnología when:1d"
    ),
    (
        "en",
        "earthquake research study technology "
        "discovery seismic sensors when:1d"
    ),
    (
        "en",
        "seismology new research early warning "
        "monitoring innovation when:1d"
    ),
    (
        "en",
        "tsunami research new study "
        "technology warning when:1d"
    ),
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
    r"breakthrough|technology|sensor|satellite|"
    r"warning|innovation|develop|model|detect|"
    r"predict|monitor|mapping|ai\b"
    r")"
)

EVENT = re.compile(
    r"(?i)\b("
    r"sismo de magnitud|temblor de magnitud|"
    r"terremoto de magnitud|earthquake of magnitude|"
    r"magnitude [0-9]|m[ .]?[4-9][.,][0-9]|"
    r"sismo hoy|earthquake strikes|earthquake hits|"
    r"earthquake struck|temblor hoy|"
    r"sismo se registr[oó]|earthquake reported|"
    r"earthquake shakes|terremoto sacude|"
    r"reportan sismo"
    r")"
)

SPAM = re.compile(
    r"(?i)\b("
    r"horoscope|hor[oó]scopo|astrolog|"
    r"prediction for today|conspiracy|"
    r"viral video|tiktok|sensational|apocalipsis"
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
# FUNCIONES AUXILIARES
# ============================================================

def clean(text):
    text = html.unescape(
        re.sub("<[^>]*>", " ", text or "")
    )
    return re.sub(r"\s+", " ", text).strip()


def get(url, **kwargs):
    timeout = kwargs.pop("timeout", 25)

    response = HTTP.get(
        url,
        timeout=timeout,
        **kwargs
    )

    response.raise_for_status()
    return response


# ============================================================
# BUSQUEDA DE NOTICIAS
# ============================================================

def feed_items():
    now = datetime.now(timezone.utc)
    results = {}

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
                get(url).content
            )
        except Exception as exc:
            print(
                "RSS no disponible:",
                lang,
                type(exc).__name__,
                str(exc)[:110],
                flush=True
            )
            continue

        for node in root.findall(".//item"):
            title = clean(
                node.findtext("title")
            )

            description = clean(
                node.findtext("description")
            )

            link = (
                node.findtext("link") or ""
            ).strip()

            published = node.findtext(
                "pubDate"
            ) or ""

            try:
                date = parsedate_to_datetime(
                    published
                ).astimezone(timezone.utc)
            except Exception:
                continue

            if not (
                now - timedelta(hours=24)
                <= date
                <= now + timedelta(minutes=5)
            ):
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
                    or suffix.lower()
                    == provider.lower()
                ):
                    title = prefix.strip()

            content = title + " " + description

            if not SCIENCE.search(content):
                continue

            if not ADVANCE.search(content):
                continue

            if EVENT.search(title):
                continue

            if SPAM.search(content):
                continue

            key = hashlib.sha256(
                re.sub(
                    r"\W+",
                    "",
                    title.lower()
                ).encode()
            ).hexdigest()[:20]

            results[key] = {
                "id": key,
                "title": title,
                "description": description,
                "url": link,
                "source": (
                    provider
                    or urllib.parse.urlparse(
                        link
                    ).netloc
                ),
                "date": date.isoformat(),
                "lang": lang,
            }

    return list(results.values())


# ============================================================
# PRIORIZACION DE NOTICIAS
# ============================================================

def priority(article):
    text = (
        article["title"]
        + " "
        + article["description"]
    ).lower()

    score = 0

    terms = [
        ("research", 4),
        ("investigación", 4),
        ("estudio", 3),
        ("study", 3),
        ("scientists", 3),
        ("científic", 3),
        ("discovery", 4),
        ("descubri", 4),
        ("technology", 4),
        ("tecnolog", 4),
        ("early warning", 4),
        ("alerta temprana", 4),
        ("sensor", 3),
        ("satellite", 3),
        ("satélite", 3),
        ("monitor", 2),
        ("seismic", 2),
        ("sísmic", 2),
    ]

    for term, weight in terms:
        if term in text:
            score += weight

    age = (
        datetime.fromisoformat(
            article["date"]
        )
        - (
            datetime.now(timezone.utc)
            - timedelta(hours=24)
        )
    ).total_seconds() / 3600

    return score + max(0, age) * 0.12


# ============================================================
# BUSQUEDA DE FOTOGRAFIAS
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
        and not word.endswith("news")
    ][:12]


def commons_photo(article):
    """
    Busca fotografías de Wikimedia Commons.

    Solo acepta CC0 o dominio público
    y exige coincidencia temática.
    """

    title = article["title"]
    words = tokens(title)

    core = [
        word
        for word in words
        if SCIENCE.search(word)
    ]

    queries = []

    proposals = [
        " ".join(words[:4]),
        " ".join((core + words)[:4]),
        "seismology research",
        "earthquake fault landscape",
    ]

    for query in proposals:
        if query and query not in queries:
            queries.append(query)

    candidates = []

    for query in queries[:3]:
        try:
            data = get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": (
                        query + " filetype:bitmap"
                    ),
                    "gsrnamespace": 6,
                    "gsrlimit": 12,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata|mime",
                    "iiurlwidth": 1500,
                    "format": "json",
                },
                timeout=22
            ).json()

        except Exception as exc:
            print(
                "Commons error:",
                type(exc).__name__,
                str(exc)[:80]
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

            if not (
                re.search(
                    r"\bcc0\b|public domain|"
                    r"publicdomain|pdm",
                    license_name
                )
                or "public domain" in usage
            ):
                continue

            name = page.get("title", "")

            description = clean(
                metadata.get(
                    "ImageDescription", {}
                ).get("value", "")
            )

            text = (
                name + " " + description
            ).lower()

            banned = (
                r"(?i)\b("
                r"moon|lunar|mars|planet|logo|"
                r"poster|screenshot|diagram|chart|"
                r"map|drawing|illustration|portrait|"
                r"people|person|crowd|tourist|"
                r"lroc|spacecraft|render|"
                r"ai.generated"
                r")\b"
            )

            if re.search(banned, text):
                continue

            if not SCIENCE.search(text):
                continue

            match = sum(
                1
                for word in set(words[:8])
                if word in text
            )

            if match < 1:
                continue

            image_url = (
                info.get("thumburl")
                or info.get("url")
            )

            if not image_url:
                continue

            if not info.get(
                "mime", ""
            ).startswith("image/"):
                continue

            score = match + (
                3
                if core
                and any(
                    word in text
                    for word in core
                )
                else 0
            )

            candidates.append(
                (
                    score,
                    name,
                    image_url,
                    description,
                    license_name,
                )
            )

        if candidates:
            break

    for (
        score,
        name,
        url,
        description,
        license_name
    ) in sorted(
        candidates,
        reverse=True
    ):
        try:
            response = get(
                url,
                timeout=35
            )

            if len(
                response.content
            ) > 15_000_000:
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
                "title": name,
                "url": url,
                "license": license_name,
                "description": (
                    description[:200]
                ),
                "illustrative": True,
            }

        except Exception:
            continue

    return None, None


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


def wrap_text(
    draw,
    text,
    selected_font,
    width,
    maxlines=5
):
    words = text.split()

    lines = []
    current = ""

    for word in words:
        proposal = (
            current + " " + word
        ).strip()

        if (
            draw.textbbox(
                (0, 0),
                proposal,
                font=selected_font
            )[2]
            <= width
        ):
            current = proposal
        else:
            if current:
                lines.append(current)

            current = word

    if current:
        lines.append(current)

    if len(lines) > maxlines:
        return None

    return lines


# ============================================================
# DISEÑO DE LA PUBLICACION
# ============================================================

def make_card(image, article, path):
    """
    Crea una imagen vertical 1080x1350.

    Diseño:
    - Fotografía de fondo
    - Cabecera SISMONAUTA
    - Banner azul adaptable
    - Titular blanco con contorno negro
    """

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

    # Cabecera institucional
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
            "La noticia no tiene titular"
        )

    inset = 48
    max_width = W - 2 * inset - 18

    selected_font = None
    lines = None
    line_height = None
    band_height = None

    for size in range(70, 23, -2):
        candidate_font = font(
            size,
            True
        )

        candidate_lines = wrap_text(
            draw,
            title,
            candidate_font,
            max_width,
            12
        )

        if not candidate_lines:
            continue

        leading = int(
            size * 1.27
        )

        height = max(
            210,
            len(candidate_lines) * leading
            + 100
        )

        if height <= 690:
            selected_font = candidate_font
            lines = candidate_lines
            line_height = leading
            band_height = height
            break

    if lines is None:
        raise ValueError(
            "Titular demasiado largo para "
            "mostrarse completo"
        )

    # Banner azul inferior
    bottom = H - 94
    top = bottom - band_height

    draw.rectangle(
        (0, top, W, bottom),
        fill=(0, 87, 202, 248)
    )

    draw.rectangle(
        (0, top, W, top + 9),
        fill=(36, 163, 255, 255)
    )

    total = len(lines) * line_height

    y = top + (
        band_height - total
    ) // 2

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

    # Pie institucional
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

    canvas = Image.alpha_composite(
        canvas,
        overlay
    ).convert("RGB")

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    canvas.save(
        path,
        "JPEG",
        quality=91,
        optimize=True
    )


# ============================================================
# DESCRIPCION PARA REDES SOCIALES
# ============================================================

def caption(article, credit):
    title = article["title"]
    description = article["description"]

    description = re.sub(
        r"\s*-\s*"
        + re.escape(
            article["source"]
        )
        + r"\s*$",
        "",
        description,
        flags=re.I
    )

    if (
        len(description) > 40
        and description.lower()
        != title.lower()
    ):
        summary = description
    else:
        summary = ""

    if len(summary) > 400:
        summary = summary[
            :400
        ].rsplit(" ", 1)[0]

    parts = [
        "🌎 ACTUALIDAD SÍSMICA | SISMONAUTA",
        "",
        title,
    ]

    if (
        summary
        and not summary.lower().startswith(
            title.lower()[:30]
        )
    ):
        parts.extend([
            "",
            summary,
        ])

    chile_date = datetime.fromisoformat(
        article["date"]
    ).astimezone(
        ZoneInfo("America/Santiago")
    ).strftime(
        "%d-%m-%Y %H:%M"
    )

    parts.extend([
        "",
        f'🗞️ Fuente: {article["source"]}',
        f'🔗 Noticia: {article["url"]}',
        f'🕒 Publicada: {chile_date} (Chile)',
        "",
        (
            "📷 Imagen ilustrativa con licencia "
            + credit["license"]
            + " — Wikimedia Commons: "
            + credit["title"]
        ),
        "",
        (
            "#SISMONAUTA #Sismologia "
            "#Ciencia #Terremotos "
            "#Tecnologia #Investigacion "
            "#Geologia #Tectonica"
        ),
    ])

    return "\n".join(
        parts
    )[:2100]


# ============================================================
# API DE GITHUB
# ============================================================

class Github:

    def __init__(self):
        repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = (
            os.getenv(
                "GITHUB_REF_NAME"
            )
            or "main"
        )

        self.base = (
            "https://api.github.com/repos/"
            + repo
        )

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": (
                "Bearer "
                + os.environ["GH_TOKEN"]
            ),
            "Accept": (
                "application/vnd.github+json"
            ),
            "X-GitHub-Api-Version": (
                "2022-11-28"
            ),
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
            + urllib.parse.quote(
                path,
                safe="/"
            )
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
            data["sha"],
        )

    def write(self, path, raw):
        url = (
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path,
                safe="/"
            )
        )

        for attempt in range(3):
            old, sha = self.read(path)

            body = {
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
                body["sha"] = sha

            response = self.session.put(
                url,
                json=body,
                timeout=50
            )

            if (
                response.status_code in (
                    409,
                    422
                )
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
# API DE META
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

    payload[
        "access_token"
    ] = token

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
            f"Meta devolvió HTTP "
            f"{response.status_code} no JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get(
            "error",
            {}
        )

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
        or info.get(
            "username",
            ""
        ).lower() != "sismonauta"
    ):
        raise RuntimeError(
            "Token Instagram no corresponde "
            "a @sismonauta"
        )

    print(
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
        FB_BASE
    )

    if str(
        info.get("id")
    ) != page_id:
        raise RuntimeError(
            "Facebook Page ID incorrecto"
        )

    if (
        info.get("username")
        and info["username"].lower()
        != "sismonauta"
    ):
        raise RuntimeError(
            "Fanpage incorrecta"
        )

    print(
        "Fanpage validada:",
        info.get("name")
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

        status = graph(
            "GET",
            container,
            token,
            {
                "fields": "status_code"
            }
        )["status_code"]

        if status == "FINISHED":
            break

        if status in (
            "ERROR",
            "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram no procesó la imagen: "
                + status
            )

    else:
        raise RuntimeError(
            "Estado incierto: contenedor "
            "Instagram sin confirmar"
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


def save_state(
    github,
    state
):
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
        else {
            "stories": {}
        }
    )

    articles = feed_items()

    articles = [
        article
        for article in articles
        if article["lang"] == "es"
    ]

    articles.sort(
        key=priority,
        reverse=True
    )

    print(
        "Noticias en español candidatas "
        "últimas 24 horas:",
        len(articles),
        flush=True
    )

    if not articles:
        print(
            "SIN NOTICIAS verificables del período. "
            "No se publica.",
            flush=True
        )

        (
            output / "estado_publicacion.json"
        ).write_text(
            json.dumps({
                "status": "no_eligible_news",
                "published": False,
            },
                ensure_ascii=False
            ),
            encoding="utf8"
        )

        return (
            2
            if args.publish
            else 0
        )

    chosen = None
    image = None
    credit = None

    for article in articles[:25]:
        if article["id"] in state["stories"]:
            continue

        print(
            "Evaluando:",
            article["title"][:110],
            flush=True
        )

        photo, metadata = commons_photo(
            article
        )

        if photo is not None:
            chosen = article
            image = photo
            credit = metadata
            break

    if not chosen:
        print(
            "No existe noticia inédita con "
            "fotografía reutilizable y pertinente.",
            flush=True
        )

        (
            output / "estado_publicacion.json"
        ).write_text(
            json.dumps({
                "status": "no_licensed_relevant_photo",
                "published": False,
                "articles_reviewed": min(
                    len(articles),
                    25
                ),
            },
                ensure_ascii=False
            ),
            encoding="utf8"
        )

        return (
            3
            if args.publish
            else 0
        )

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

    (
        output / "noticia.json"
    ).write_text(
        json.dumps({
            "article": chosen,
            "image": credit,
            "caption": description,
        },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf8"
    )

    print(
        "SELECCIONADA:",
        chosen["title"],
        "|",
        chosen["source"],
        "|",
        chosen["date"],
        flush=True
    )

    print(
        "FOTO:",
        credit["title"],
        "|",
        credit["license"],
        flush=True
    )

    if not args.publish:
        print(
            "MODO PRUEBA: imagen creada, "
            "sin publicación",
            image_file,
            flush=True
        )

        (
            output / "estado_publicacion.json"
        ).write_text(
            json.dumps({
                "status": "preview_only",
                "published": False,
                "image": str(
                    image_file
                ),
            },
                ensure_ascii=False
            ),
            encoding="utf8"
        )

        return 0

    # ========================================================
    # VALIDAR CUENTAS DE META
    # ========================================================

    instagram_id, instagram_token = ig_check()

    facebook_id, facebook_token = fb_check()

    # ========================================================
    # PUBLICAR IMAGEN EN GITHUB PARA META
    # ========================================================

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
            "La imagen no es pública "
            "y legible: "
            + str(
                response.status_code
            )
        )

    # ========================================================
    # REGISTRAR ENVIO PENDIENTE
    # ========================================================

    state["stories"][
        chosen["id"]
    ] = {
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

    # ========================================================
    # PUBLICAR EN AMBAS PLATAFORMAS
    # ========================================================

    for target in (
        "instagram",
        "facebook"
    ):
        entry = state[
            "stories"
        ][chosen["id"]][
            "targets"
        ][target]

        # Proteger frente a respuestas inciertas.
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
                    "Meta no devolvió ID "
                    "de publicación"
                )

            entry.update(
                status="published",
                id=str(
                    identifier
                )
            )

            save_state(
                github,
                state
            )

            print(
                "PUBLICADO",
                target,
                "ID:",
                identifier,
                flush=True
            )

        except Exception as exc:
            failures.append(
                target
                + ": "
                + str(exc)
            )

            print(
                "ERROR",
                target,
                str(exc)[:250],
                flush=True
            )

    # ========================================================
    # RECIBO FINAL
    # ========================================================

    receipt = {
        "status": (
            "published"
            if not failures
            else "partial_or_uncertain"
        ),
        "article_id": chosen["id"],
        "article_title": chosen["title"],
        "targets": state[
            "stories"
        ][chosen["id"]][
            "targets"
        ],
    }

    (
        output / "estado_publicacion.json"
    ).write_text(
        json.dumps(
            receipt,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf8"
    )

    if failures:
        raise RuntimeError(
            "Publicación parcial o incierta. "
            "Revisar antes de reintentar: "
            + " / ".join(failures)
        )

    print(
        "PUBLICACIÓN VERIFICADA: "
        "Instagram y Facebook",
        flush=True
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(
            main()
        )

    except Exception as exc:
        print(
            "ERROR:",
            exc,
            file=sys.stderr,
            flush=True
        )

        sys.exit(1)
