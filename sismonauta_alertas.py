
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA | ALERTAS M7+

Monitoreo mundial de terremotos M >= 7.0.
Fuentes: USGS y boletines oficiales NOAA/PTWC.

Imagen:
  1080 x 1350 px.
  Banner superior: ÚLTIMO MINUTO.
  Centro: mapa del epicentro.
  Banner inferior: magnitud y región.

Publicaciones:
  Instagram @sismonauta.
  Fanpage Facebook SISMONAUTA.

Persistencia:
  GitHub Contents API, sin git push.
  Estados separados por evento y plataforma.

Dependencias:
  pip install requests Pillow matplotlib cartopy

Ejecución:
  python sismonauta_alertas.py
  python sismonauta_alertas.py --publish
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
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# CONFIGURACIÓN
# ============================================================

APP = "SISMONAUTA ALERTAS M7+"

VERSION = os.getenv(
    "IG_GRAPH_VERSION", "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

USGS_URL = (
    "https://earthquake.usgs.gov/"
    "fdsnws/event/1/query"
)

PTWC_FEED = (
    "https://www.tsunami.gov/"
    "events/xml/PHEBAtom.xml"
)

NTWC_FEED = (
    "https://www.tsunami.gov/"
    "events/xml/PAAQAtom.xml"
)

STATE_PATH = (
    "sismonauta_alertas/state.json"
)

IMAGE_DIR = (
    "sismonauta_alertas/images"
)

OUT = Path("output_alertas")

MIN_MAG = 7.0

CHILE = ZoneInfo("America/Santiago")

UTC = timezone.utc

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": "Sismonauta-Alertas/1.0",
    "Accept": "*/*",
})

# Mantener como mínimo 24 horas de búsqueda
# para recuperar ejecuciones programadas retrasadas.
LOOKBACK_HOURS = 24

MAX_EVENTS = 10


# ============================================================
# UTILIDADES
# ============================================================

def log(*values):
    print(*values, flush=True)


def now_utc():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def clean(value):
    value = html.unescape(
        re.sub(r"<[^>]*>", " ", value or "")
    )
    return re.sub(r"\s+", " ", value).strip()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def request_json(url, params=None, timeout=35):
    response = HTTP.get(
        url,
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def request_text(url, timeout=35):
    response = HTTP.get(
        url,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


def safe_text(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        str(value),
    )[:90]


def short_error(exc):
    text = str(exc)
    for secret in (
        os.getenv("INSTAGRAM_TOKEN", ""),
        os.getenv("FACEBOOK_PAGE_TOKEN", ""),
        os.getenv("GH_TOKEN", ""),
    ):
        if secret:
            text = text.replace(secret, "***")
    return text[:300]


# ============================================================
# IDENTIFICACIÓN DE REGIÓN
# ============================================================

REGION_NAMES = {
    "japan": "JAPÓN",
    "japan region": "JAPÓN",
    "chile": "CHILE",
    "peru": "PERÚ",
    "mexico": "MÉXICO",
    "indonesia": "INDONESIA",
    "vanuatu": "VANUATU",
    "philippines": "FILIPINAS",
    "papua new guinea": "PAPÚA NUEVA GUINEA",
    "new zealand": "NUEVA ZELANDA",
    "russia": "RUSIA",
    "turkey": "TURQUÍA",
    "türkiye": "TURQUÍA",
    "ecuador": "ECUADOR",
    "panama": "PANAMÁ",
    "alaska": "ALASKA, EE. UU.",
    "california": "CALIFORNIA, EE. UU.",
    "united states": "ESTADOS UNIDOS",
    "solomon islands": "ISLAS SALOMÓN",
    "tonga": "TONGA",
    "fiji": "FIYI",
    "taiwan": "TAIWÁN",
    "greece": "GRECIA",
    "iceland": "ISLANDIA",
    "afghanistan": "AFGANISTÁN",
    "pakistan": "PAKISTÁN",
    "haiti": "HAITÍ",
    "colombia": "COLOMBIA",
    "argentina": "ARGENTINA",
    "bolivia": "BOLIVIA",
    "samoa": "SAMOA",
    "kamchatka": "KAMCHATKA, RUSIA",
    "kuril islands": "ISLAS KURILES",
    "south sandwich islands": "ISLAS SANDWICH DEL SUR",
    "bonin islands": "ISLAS BONIN, JAPÓN",
    "southern mid-atlantic ridge": "DORSAL MESOATLÁNTICA SUR",
}


def region_name(place):
    place = clean(place)

    if not place:
        return "REGIÓN NO DETERMINADA"

    tail = place.split(",")[-1].strip()
    lower = tail.lower()

    if lower in REGION_NAMES:
        return REGION_NAMES[lower]

    for key, value in REGION_NAMES.items():
        if key in lower:
            return value

    # No inventar un país para regiones oceánicas.
    return tail.upper()


# ============================================================
# CONSULTAR USGS
# ============================================================

def fetch_earthquakes(hours=LOOKBACK_HOURS):
    end = now_utc()
    start = end - timedelta(hours=hours)

    data = request_json(
        USGS_URL,
        params={
            "format": "geojson",
            "starttime": start.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "endtime": end.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "minmagnitude": 7.0,
            "eventtype": "earthquake",
            "orderby": "time-asc",
        },
        timeout=50,
    )

    events = []

    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []

        if len(coordinates) < 3:
            continue

        magnitude = properties.get("mag")

        if magnitude is None or float(magnitude) < MIN_MAG:
            continue

        milliseconds = properties.get("time")

        if milliseconds is None:
            continue

        event_id = feature.get("id")

        if not event_id:
            continue

        place = properties.get("place") or ""

        events.append({
            "id": str(event_id),
            "mag": float(magnitude),
            "place": place,
            "region": region_name(place),
            "latitude": float(coordinates[1]),
            "longitude": float(coordinates[0]),
            "depth": float(coordinates[2]),
            "time": datetime.fromtimestamp(
                milliseconds / 1000,
                UTC,
            ),
            "url": properties.get("url") or "",
            "detail_url": properties.get("detail") or "",
            "tsunami_flag": properties.get("tsunami"),
            "status": properties.get("status") or "",
        })

    return events


def get_detail(event):
    try:
        if event["detail_url"]:
            return request_json(
                event["detail_url"],
                timeout=35,
            )
    except Exception as exc:
        log("Detalle USGS:", short_error(exc))

    return {}


# ============================================================
# CIUDADES CERCANAS
# ============================================================

def nearby_cities(event, detail):
    products = (
        detail.get("properties") or {}
    ).get("products") or {}

    for product in products.get("nearby-cities", []):
        contents = product.get("contents") or {}

        for name, value in contents.items():
            if not name.endswith("nearby-cities.json"):
                continue

            url = value.get("url")

            if not url:
                continue

            try:
                data = request_json(
                    url,
                    timeout=25,
                )

                # USGS usa normalmente una lista
                # de ciudades en este producto.
                if isinstance(data, dict):
                    data = data.get("cities", [])

                result = []

                for city in data[:4]:
                    name = city.get("name")

                    if not name:
                        continue

                    distance = city.get("distance")

                    if distance is not None:
                        name += (
                            f" ({float(distance):.0f} km)"
                        )

                    result.append(name)

                if result:
                    return result

            except Exception as exc:
                log(
                    "Ciudades USGS:",
                    short_error(exc),
                )

    return []


# ============================================================
# BOLETINES OFICIALES DE TSUNAMI
# ============================================================

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom"
}


def parse_time(value):
    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).astimezone(UTC)
    except Exception:
        return None


def atom_entries(url):
    root = ET.fromstring(
        request_text(url, timeout=30)
    )

    entries = []

    for item in root.findall(
        ".//a:entry",
        ATOM_NS,
    ):
        def field(name):
            node = item.find(
                "a:" + name,
                ATOM_NS,
            )
            if node is None:
                return ""
            return clean(
                "".join(node.itertext())
            )

        link = ""

        for node in item.findall(
            "a:link",
            ATOM_NS,
        ):
            href = node.attrib.get("href", "")

            if href.startswith("https://"):
                link = href
                break

        entries.append({
            "title": field("title"),
            "summary": field("summary"),
            "content": field("content"),
            "updated": parse_time(field("updated")),
            "link": link,
        })

    return entries


def normalize_words(value):
    return {
        word.lower()
        for word in re.findall(
            r"[A-Za-z]{5,}",
            value or "",
        )
        if word.lower() not in {
            "earthquake",
            "region",
            "magnitude",
            "tsunami",
            "islands",
            "offshore",
        }
    }


def bulletin_matches(entry, event):
    """
    Exige correlación temporal y geográfica
    y, cuando esté disponible, magnitud próxima.

    Una coincidencia débil nunca autoriza
    afirmar que no existe amenaza.
    """
    updated = entry.get("updated")

    if updated is None:
        return False

    gap = (
        updated - event["time"]
    ).total_seconds()

    if gap < -600 or gap > 4 * 3600:
        return False

    text = (
        entry["title"]
        + " "
        + entry["summary"]
        + " "
        + entry["content"]
    ).lower()

    place_words = normalize_words(event["place"])

    if not place_words:
        return False

    if not any(word in text for word in place_words):
        return False

    magnitudes = re.findall(
        r"(?:magnitude|mag)\s*[:=]?\s*"
        r"([0-9]+(?:\.[0-9]+)?)",
        text,
        flags=re.I,
    )

    if magnitudes:
        if not any(
            abs(float(value) - event["mag"]) <= 0.6
            for value in magnitudes
        ):
            return False

    return True


def classify_bulletin(text):
    upper = text.upper()

    negatives = (
        "NO TSUNAMI WARNING, ADVISORY, WATCH, OR THREAT",
        "NO TSUNAMI THREAT",
        "NO TSUNAMI WARNING",
        "NO DESTRUCTIVE TSUNAMI THREAT",
    )

    if any(value in upper for value in negatives):
        # La inexistencia de amenaza debe estar
        # afirmada explícitamente por el boletín.
        return "no_threat"

    positives = (
        "TSUNAMI THREAT MESSAGE",
        "POTENTIAL THREAT",
        "TSUNAMI WARNING IS IN EFFECT",
        "TSUNAMI WARNING IN EFFECT",
        "TSUNAMI WATCH IS IN EFFECT",
        "TSUNAMI ADVISORY IS IN EFFECT",
    )

    if any(value in upper for value in positives):
        return "threat"

    return "information"


def parse_risk_areas(text):
    """
    Extrae únicamente zonas explícitas
    de las secciones del boletín.

    Si no es posible extraerlas con seguridad,
    no inventa áreas de riesgo.
    """
    upper = html.unescape(text).upper()

    patterns = [
        (
            r"TSUNAMI WAVES ARE FORECAST TO BE "
            r"HAZARDOUS ALONG THE COASTS OF(.*?)(?:"
            r"RECOMMENDED ACTIONS|NEXT UPDATE|"
            r"PRELIMINARY EARTHQUAKE|$)"
        ),
        (
            r"TSUNAMI THREAT FORECAST(.*?)(?:"
            r"RECOMMENDED ACTIONS|"
            r"PRELIMINARY EARTHQUAKE|$)"
        ),
    ]

    for pattern in patterns:
        found = re.search(
            pattern,
            upper,
            re.S,
        )

        if not found:
            continue

        chunk = re.sub(
            r"\s+",
            " ",
            found.group(1),
        ).strip()

        if 15 <= len(chunk) <= 650:
            return chunk.capitalize()

    return ""


def tsunami_status(event):
    """
    Consulta PTWC y NTWC.

    'unknown' significa que no hay
    confirmación suficiente; NO significa
    que el tsunami sea imposible.
    """
    all_bulletins = []

    successful = 0

    for center, url in (
        ("PTWC", PTWC_FEED),
        ("NTWC", NTWC_FEED),
    ):
        try:
            entries = atom_entries(url)
            successful += 1

            for entry in entries:
                entry["center"] = center
                all_bulletins.append(entry)

        except Exception as exc:
            log(
                "Boletines",
                center,
                short_error(exc),
            )

    matches = [
        entry
        for entry in all_bulletins
        if bulletin_matches(entry, event)
    ]

    matches.sort(
        key=lambda item: item["updated"],
        reverse=True,
    )

    # Prioridad a amenazas vigentes;
    # la clasificación inicial se conserva
    # y no se deduce desde el indicador USGS.
    threats = []
    no_threats = []

    for entry in matches:
        content = (
            entry["title"]
            + "\n"
            + entry["summary"]
            + "\n"
            + entry["content"]
        )

        status = classify_bulletin(content)

        if status == "threat":
            threats.append((entry, content))

        if status == "no_threat":
            no_threats.append((entry, content))

    # El boletín más reciente asociado al
    # evento determina el estado reportado.
    if matches:
        newest = matches[0]

        content = (
            newest["title"]
            + "\n"
            + newest["summary"]
            + "\n"
            + newest["content"]
        )

        status = classify_bulletin(content)

        if status == "threat":
            areas = parse_risk_areas(content)

            message = (
                "El centro "
                + newest["center"]
                + " publicó un boletín de "
                "amenaza o alerta de tsunami "
                "asociado al evento."
            )

            if areas:
                message += (
                    " Zonas mencionadas: "
                    + areas[:400]
                )
            else:
                message += (
                    " Las zonas específicas "
                    "deben consultarse en el "
                    "boletín oficial."
                )

            return {
                "status": "threat",
                "message": message,
                "areas": areas,
                "center": newest["center"],
                "url": newest["link"],
            }

        if status == "no_threat":
            return {
                "status": "no_threat",
                "message": (
                    "El boletín más reciente de "
                    + newest["center"]
                    + " asociado al evento no "
                    "indica una amenaza o alerta "
                    "de tsunami en su ámbito "
                    "de cobertura."
                ),
                "areas": "",
                "center": newest["center"],
                "url": newest["link"],
            }

    return {
        "status": "unknown",
        "message": (
            "Estado de tsunami pendiente "
            "de confirmación oficial. "
            "Consulta los centros de alerta "
            "y las autoridades costeras."
        ),
        "areas": "",
        "center": (
            "PTWC/NTWC"
            if successful
            else "no disponible"
        ),
        "url": "https://www.tsunami.gov/",
    }


# ============================================================
# DISEÑO DEL MAPA
# ============================================================

def build_map(event, path):
    """
    Cartopy descarga datos cartográficos
    Natural Earth cuando no están en caché.

    El mapa usa coordenadas reales de USGS.
    """
    import matplotlib
    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    latitude = event["latitude"]
    longitude = event["longitude"]

    projection = ccrs.PlateCarree(
        central_longitude=longitude
    )

    fig = plt.figure(
        figsize=(9, 9),
        dpi=160,
        facecolor="#061c33",
    )

    ax = fig.add_axes(
        [0, 0, 1, 1],
        projection=projection,
    )

    ax.set_extent(
        [-13, 13, latitude - 12, latitude + 12],
        crs=projection,
    )

    ax.set_facecolor("#063962")

    ax.add_feature(
        cfeature.OCEAN.with_scale("110m"),
        facecolor="#063962",
        zorder=0,
    )

    ax.add_feature(
        cfeature.LAND.with_scale("110m"),
        facecolor="#386944",
        zorder=1,
    )

    ax.add_feature(
        cfeature.COASTLINE.with_scale("110m"),
        edgecolor="#e0e9db",
        linewidth=1.25,
        zorder=2,
    )

    ax.add_feature(
        cfeature.BORDERS.with_scale("110m"),
        edgecolor="#c8d4cf",
        linewidth=0.65,
        zorder=2,
    )

    ax.gridlines(
        draw_labels=False,
        linewidth=0.35,
        color="white",
        alpha=0.18,
    )

    # El marcador se coloca en las coordenadas
    # exactas entregadas por USGS.
    ax.scatter(
        [longitude],
        [latitude],
        transform=ccrs.PlateCarree(),
        s=1800,
        facecolors="none",
        edgecolors="#ff2638",
        linewidths=2,
        zorder=8,
    )

    ax.scatter(
        [longitude],
        [latitude],
        transform=ccrs.PlateCarree(),
        s=650,
        facecolors="none",
        edgecolors="#ff3440",
        linewidths=3,
        zorder=9,
    )

    ax.scatter(
        [longitude],
        [latitude],
        transform=ccrs.PlateCarree(),
        s=120,
        color="#ff142c",
        edgecolors="white",
        linewidths=1.8,
        zorder=10,
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fig.savefig(
        path,
        dpi=160,
        facecolor="#061c33",
    )

    plt.close(fig)


# ============================================================
# COMPOSICIÓN DE IMAGEN
# ============================================================

def font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/"
        "DejaVuSans-Bold.ttf",

        "/usr/share/fonts/truetype/liberation2/"
        "LiberationSans-Bold.ttf",
    ]

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(
                path,
                size,
            )

    return ImageFont.load_default()


def split_lines(draw, text, selected_font, width):
    words = text.split()
    lines = []
    current = ""

    for word in words:
        candidate = (
            current + " " + word
        ).strip()

        bbox = draw.textbbox(
            (0, 0),
            candidate,
            font=selected_font,
            stroke_width=3,
        )

        if bbox[2] - bbox[0] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def banner_text(draw, text, bounds, max_size):
    x1, y1, x2, y2 = bounds
    available_w = x2 - x1 - 48
    available_h = y2 - y1 - 24

    for size in range(max_size, 23, -2):
        selected = font(size)

        lines = split_lines(
            draw,
            text,
            selected,
            available_w,
        )

        line_height = int(size * 1.25)

        if len(lines) * line_height <= available_h:
            break
    else:
        raise RuntimeError(
            "El titular no cabe en el banner"
        )

    top = (
        y1
        + (y2 - y1 - len(lines) * line_height) // 2
    )

    for line in lines:
        bbox = draw.textbbox(
            (0, 0),
            line,
            font=selected,
            stroke_width=3,
        )

        width = bbox[2] - bbox[0]

        x = (x1 + x2 - width) // 2

        draw.text(
            (x, top),
            line,
            font=selected,
            fill="white",
            stroke_width=3,
            stroke_fill="black",
        )

        top += line_height


def make_image(event, output_path):
    width, height = 1080, 1350

    temp_map = OUT / (
        safe_text(event["id"]) + "_map.png"
    )

    build_map(event, temp_map)

    image = Image.new(
        "RGB",
        (width, height),
        "#071d34",
    )

    map_image = Image.open(
        temp_map
    ).convert("RGB")

    map_image = ImageOps.fit(
        map_image,
        (width, height),
        Image.Resampling.LANCZOS,
    )

    image.paste(map_image, (0, 0))

    draw = ImageDraw.Draw(image)

    # Banner rojo superior
    draw.rounded_rectangle(
        (23, 30, 1057, 205),
        radius=18,
        fill="#d40a16",
        outline="#ff3444",
        width=5,
    )

    banner_text(
        draw,
        "ÚLTIMO MINUTO",
        (35, 37, 1045, 195),
        94,
    )

    # Banner rojo inferior
    draw.rounded_rectangle(
        (18, 1105, 1062, 1315),
        radius=16,
        fill="#a20812",
        outline="#ff2335",
        width=5,
    )

    headline = (
        f"SISMO DE MAGNITUD "
        f"{event['mag']:.1f} "
        f"EN {event['region']}"
    )

    banner_text(
        draw,
        headline,
        (35, 1120, 1045, 1298),
        62,
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    image.save(
        output_path,
        "JPEG",
        quality=93,
        optimize=True,
    )


# ============================================================
# TEXTO DE PUBLICACIÓN
# ============================================================

def make_caption(event, cities, tsunami):
    local_time = event["time"].astimezone(CHILE)

    formatted_date = local_time.strftime(
        "%d/%m/%Y a las %H:%M"
    )

    if cities:
        cities_text = ", ".join(cities[:4])
    else:
        cities_text = (
            "USGS no identifica ciudades "
            "cercanas en los datos consultados."
        )

    text = (
        "🚨 ÚLTIMO MINUTO | SISMONAUTA\n\n"

        f"Un terremoto de magnitud "
        f"{event['mag']:.1f} fue registrado "
        f"por el Servicio Geológico de "
        f"Estados Unidos (USGS) en "
        f"{event['region']}.\n\n"

        f"📍 Ubicación: {event['place']}\n"

        f"📊 Magnitud: {event['mag']:.1f}\n"

        f"⬇️ Profundidad: "
        f"{event['depth']:.1f} km\n"

        f"🕒 Hora: {formatted_date} "
        f"(hora de Chile)\n"

        f"🌐 Coordenadas: "
        f"{event['latitude']:.3f}, "
        f"{event['longitude']:.3f}\n\n"

        f"🏙️ Ciudades cercanas: "
        f"{cities_text}\n\n"

        f"🌊 SITUACIÓN DE TSUNAMI\n"
        f"{tsunami['message']}\n\n"

        f"Datos sísmicos: USGS\n"
        f"{event['url']}\n\n"

        f"Boletines de tsunami:\n"
        f"{tsunami['url'] or 'https://www.tsunami.gov/'}\n\n"

        "#SISMONAUTA #UltimoMinuto "
        "#Terremoto #Sismo #USGS "
        "#Tsunami #Sismologia"
    )

    if len(text) > 2200:
        text = text[:2197] + "..."

    return text


# ============================================================
# GITHUB: ALMACENAMIENTO PERSISTENTE
# ============================================================

class Github:
    def __init__(self):
        self.repository = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = os.getenv(
            "GITHUB_REF_NAME",
            "main",
        )

        token = os.environ["GH_TOKEN"]

        self.base = (
            "https://api.github.com/repos/"
            + self.repository
        )

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

        info = self.session.get(
            self.base,
            timeout=25,
        )

        info.raise_for_status()

        if info.json().get("private"):
            raise RuntimeError(
                "El repositorio debe ser público "
                "para alojar imágenes accesibles "
                "desde Meta."
            )

    def endpoint(self, path):
        return (
            self.base
            + "/contents/"
            + urllib.parse.quote(path, safe="/")
        )

    def read(self, path):
        response = self.session.get(
            self.endpoint(path),
            params={"ref": self.branch},
            timeout=30,
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()
        data = response.json()

        return json.loads(
            base64.b64decode(
                data["content"]
            ).decode("utf-8")
        )

    def write_bytes(self, path, content):
        endpoint = self.endpoint(path)

        for attempt in range(4):
            previous = self.session.get(
                endpoint,
                params={"ref": self.branch},
                timeout=30,
            )

            if previous.status_code == 404:
                sha = None
            else:
                previous.raise_for_status()
                sha = previous.json()["sha"]

            payload = {
                "message": (
                    "SISMONAUTA alerta: " + path
                ),
                "branch": self.branch,
                "content": base64.b64encode(
                    content
                ).decode("ascii"),
            }

            if sha:
                payload["sha"] = sha

            response = self.session.put(
                endpoint,
                json=payload,
                timeout=50,
            )

            if response.ok:
                return

            if (
                response.status_code in (409, 422)
                and attempt < 3
            ):
                time.sleep(3)
                continue

            response.raise_for_status()

    def write_json(self, path, data):
        raw = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")

        self.write_bytes(path, raw)

    def raw_url(self, path):
        return (
            "https://raw.githubusercontent.com/"
            + self.repository
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
# META GRAPH
# ============================================================

def graph(method, path, token, parameters=None, base=None):
    base = base or IG_BASE

    endpoint = (
        f"{base}/{VERSION}/{path}"
    )

    values = dict(parameters or {})
    values["access_token"] = token

    if method == "GET":
        response = requests.get(
            endpoint,
            params=values,
            timeout=55,
        )
    else:
        response = requests.post(
            endpoint,
            data=values,
            timeout=75,
        )

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "Meta respondió sin JSON"
        )

    if not response.ok or data.get("error"):
        error = data.get("error") or {}

        raise RuntimeError(
            "Meta API: "
            + str(error.get("message", data))[:200]
        )

    return data


def validate_accounts():
    ig_token = os.environ["INSTAGRAM_TOKEN"]
    ig_id = os.environ["INSTAGRAM_USER_ID"]

    fb_token = os.environ["FACEBOOK_PAGE_TOKEN"]
    fb_id = os.environ["FACEBOOK_PAGE_ID"]

    ig = graph(
        "GET",
        "me",
        ig_token,
        {"fields": "id,username"},
    )

    if (
        str(ig.get("id")) != str(ig_id)
        or ig.get("username", "").lower()
        != "sismonauta"
    ):
        raise RuntimeError(
            "Instagram no corresponde "
            "a @sismonauta"
        )

    fb = graph(
        "GET",
        fb_id,
        fb_token,
        {"fields": "id,name"},
        FB_BASE,
    )

    if str(fb.get("id")) != str(fb_id):
        raise RuntimeError(
            "Fanpage de Facebook incorrecta"
        )

    log(
        "Instagram:",
        ig.get("username"),
    )

    log(
        "Facebook:",
        fb.get("name"),
    )

    return ig_id, ig_token, fb_id, fb_token


def publish_instagram(user_id, token, url, caption):
    container = graph(
        "POST",
        f"{user_id}/media",
        token,
        {
            "image_url": url,
            "caption": caption,
        },
    )["id"]

    finished = False

    for attempt in range(20):
        time.sleep(6)

        result = graph(
            "GET",
            container,
            token,
            {"fields": "status_code"},
        )

        status = result.get("status_code")

        if status == "FINISHED":
            finished = True
            break

        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                "Instagram: contenedor "
                + str(status)
            )

    if not finished:
        raise RuntimeError(
            "Instagram no terminó de "
            "procesar el contenedor"
        )

    result = graph(
        "POST",
        f"{user_id}/media_publish",
        token,
        {"creation_id": container},
    )

    if not result.get("id"):
        raise RuntimeError(
            "Instagram no confirmó publicación"
        )

    return str(result["id"])


def publish_facebook(page_id, token, url, caption):
    result = graph(
        "POST",
        f"{page_id}/photos",
        token,
        {
            "url": url,
            "caption": caption,
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
            "Facebook no confirmó publicación"
        )

    return str(identifier)


# ============================================================
# HISTORIAL Y PUBLICACIONES POR PLATAFORMA
# ============================================================

def new_state():
    return {"events": {}}


def event_is_finished(entry):
    targets = entry.get("targets") or {}

    return all(
        targets.get(platform, {}).get("status")
        == "published"
        for platform in ("instagram", "facebook")
    )


def ensure_entry(state, event):
    return state["events"].setdefault(
        event["id"],
        {
            "magnitude": event["mag"],
            "place": event["place"],
            "time": iso(event["time"]),
            "targets": {
                "instagram": {"status": "pending"},
                "facebook": {"status": "pending"},
            },
        },
    )


def wait_public_image(url):
    # Permitir propagación en raw.githubusercontent.
    for attempt in range(12):
        try:
            response = HTTP.get(
                url,
                timeout=20,
            )

            if (
                response.ok
                and response.headers.get(
                    "Content-Type", ""
                ).startswith("image/")
            ):
                return

        except requests.RequestException:
            pass

        time.sleep(5)

    raise RuntimeError(
        "Imagen todavía no accesible públicamente"
    )


def process_event(event, github, state, publish, credentials):
    event_id = event["id"]

    entry = ensure_entry(state, event)

    # Una publicación ya confirmada en ambas redes
    # nunca se vuelve a enviar.
    if event_is_finished(entry):
        log("YA PUBLICADO:", event_id)
        return "already_published"

    # Las publicaciones de resultado incierto
    # requieren revisión antes de repetir la llamada.
    for platform in ("instagram", "facebook"):
        status = (
            entry["targets"][platform].get("status")
        )

        if status == "uncertain":
            log(
                "REVISIÓN MANUAL:",
                event_id,
                platform,
            )
            return "partial_or_uncertain"

    detail = get_detail(event)
    cities = nearby_cities(event, detail)
    tsunami = tsunami_status(event)

    filename = safe_text(event_id) + ".jpg"
    image_path = OUT / filename

    make_image(event, image_path)

    caption = make_caption(
        event,
        cities,
        tsunami,
    )

    save_json(
        OUT / (safe_text(event_id) + ".json"),
        {
            "event": event,
            "nearby_cities": cities,
            "tsunami": tsunami,
            "caption": caption,
        },
    )

    if not publish:
        log("VISTA PREVIA:", image_path)
        return "preview_only"

    ig_id, ig_token, fb_id, fb_token = credentials

    remote_path = (
        IMAGE_DIR + "/" + filename
    )

    github.write_bytes(
        remote_path,
        image_path.read_bytes(),
    )

    image_url = github.raw_url(
        remote_path
    )

    wait_public_image(image_url)

    entry["image_url"] = image_url

    github.write_json(
        STATE_PATH,
        state,
    )

    for platform in ("instagram", "facebook"):
        target = entry["targets"][platform]

        if target.get("status") == "published":
            continue

        # Persistimos el estado ANTES de llamar
        # a Meta para evitar reenvíos ciegos.
        target["status"] = "uncertain"

        github.write_json(
            STATE_PATH,
            state,
        )

        try:
            if platform == "instagram":
                identifier = publish_instagram(
                    ig_id,
                    ig_token,
                    image_url,
                    caption,
                )
            else:
                identifier = publish_facebook(
                    fb_id,
                    fb_token,
                    image_url,
                    caption,
                )

            target["status"] = "published"
            target["id"] = identifier

            github.write_json(
                STATE_PATH,
                state,
            )

            log(
                "PUBLICADO",
                platform,
                identifier,
            )

        except Exception as exc:
            target["error"] = short_error(exc)

            github.write_json(
                STATE_PATH,
                state,
            )

            log(
                "ERROR",
                platform,
                short_error(exc),
            )

    if event_is_finished(entry):
        return "published"

    return "partial_or_uncertain"


# ============================================================
# EJECUCIÓN
# ============================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--publish",
        action="store_true",
    )

    parser.add_argument(
        "--out",
        default="output_alertas",
    )

    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
    )

    args = parser.parse_args()

    global OUT
    OUT = Path(args.out)

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    receipt_path = (
        OUT / "estado_publicacion.json"
    )

    receipt = {
        "status": "starting",
        "publish_requested": args.publish,
        "checked_at": iso(now_utc()),
        "events": [],
    }

    try:
        github = Github() if args.publish else None

        state = (
            github.read(STATE_PATH)
            if github
            else None
        ) or new_state()

        state.setdefault("events", {})

        credentials = None

        events = fetch_earthquakes(
            hours=args.lookback_hours
        )

        # Primera instalación: no repasar
        # todo el historial de las últimas 24 h.
        # Solo los últimos 90 minutos.
        if args.publish and github.read(STATE_PATH) is None:
            threshold = now_utc() - timedelta(
                minutes=90
            )

            events = [
                event
                for event in events
                if event["time"] >= threshold
            ]

        events = sorted(
            events,
            key=lambda item: item["time"],
        )[:MAX_EVENTS]

        pending = [
            event for event in events
            if not event_is_finished(
                state["events"].get(
                    event["id"], {}
                )
            )
        ]

        log(
            "USGS M7+:",
            len(events),
            "| Pendientes:",
            len(pending),
        )

        if not pending:
            receipt["status"] = "no_new_event"
            save_json(
                receipt_path,
                receipt,
            )
            return 0

        if args.publish:
            credentials = validate_accounts()

        statuses = []

        for event in pending:
            log(
                "SISMO:",
                event["id"],
                f"M{event['mag']:.1f}",
                event["region"],
            )

            result = process_event(
                event,
                github,
                state,
                args.publish,
                credentials,
            )

            statuses.append(result)

            receipt["events"].append({
                "id": event["id"],
                "magnitude": event["mag"],
                "region": event["region"],
                "status": result,
                "targets": (
                    state["events"][event["id"]]
                    ["targets"]
                ),
            })

        if all(
            result == "preview_only"
            for result in statuses
        ):
            receipt["status"] = "preview_only"

        elif any(
            result == "partial_or_uncertain"
            for result in statuses
        ):
            receipt["status"] = "partial_or_uncertain"

        elif all(
            result in (
                "published",
                "already_published",
            )
            for result in statuses
        ):
            receipt["status"] = "published"

        else:
            receipt["status"] = "error"

        save_json(
            receipt_path,
            receipt,
        )

        log(
            "ESTADO FINAL:",
            receipt["status"],
        )

        if receipt["status"] in (
            "published",
            "preview_only",
            "no_new_event",
        ):
            return 0

        return 1

    except Exception as exc:
        receipt["status"] = "error"
        receipt["error"] = short_error(exc)

        save_json(
            receipt_path,
            receipt,
        )

        log(
            "ERROR GENERAL:",
            short_error(exc),
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
