
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA | ALERTAS SISMICAS POR CIUDAD
VERSION EMSC-CSEM

Fuente:
  European-Mediterranean Seismological Centre
  https://www.emsc-csem.org/
  https://www.seismicportal.eu/

Monitoreo:
  Magnitudes M4.0 a M6.9
  Radio de 100 km
  15 ciudades de America
  Ventana de consulta de 24 horas

Imagen:
  1080 x 1350
  Verde: M4.0 a M4.9
  Amarillo: M5.0 a M5.9
  Naranjo: M6.0 a M6.9
  Epicentro rojo
  Sin texto "Epicentro cercano a..."

Publicacion:
  Instagram @sismonauta
  Fanpage Facebook SISMONAUTA

Historial:
  GitHub Contents API
  Estado por evento y plataforma
  Sin git push

No consulta alertas de tsunami.
No constituye un servicio oficial
de alerta o prediccion sismica.

Dependencias:
  requests
  Pillow
  matplotlib
  cartopy

Ejecucion:
  python sismonauta_ciudades.py
  python sismonauta_ciudades.py --publish
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import re
import sys
import time
import urllib.parse

from datetime import datetime, timedelta, timezone
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
# CONFIGURACION GENERAL
# ============================================================

APP = "SISMONAUTA | ALERTAS CIUDADES EMSC"

UTC = timezone.utc

EMSC_API = (
    "https://www.seismicportal.eu/"
    "fdsnws/event/1/query"
)

EMSC_WEB = (
    "https://www.emsc-csem.org/"
    "Earthquake_information/"
)

MIN_MAG = 4.0
MAX_MAG = 7.0

RADIUS_KM = 100.0

LOOKBACK_HOURS = 24

FIRST_RUN_MINUTES = 90

MAX_EVENTS_PER_RUN = 30

OUT = Path("output_ciudades")

STATE_PATH = (
    "sismonauta_ciudades/state.json"
)

IMAGE_DIR = (
    "sismonauta_ciudades/images"
)

GRAPH_VERSION = os.getenv(
    "META_GRAPH_VERSION",
    "v25.0",
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com",
).rstrip("/")

FB_BASE = (
    "https://graph.facebook.com"
)

WIDTH = 1080
HEIGHT = 1350

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": (
        "Sismonauta-Ciudades-EMSC/1.0 "
        "(educational seismic monitoring)"
    ),
    "Accept": "application/json",
})

# ============================================================
# CIUDADES MONITOREADAS
# ============================================================

# Formato:
# nombre, pais, bandera, latitud, longitud, timezone

CITIES = [
    (
        "Santiago",
        "Chile",
        "🇨🇱",
        -33.4489,
        -70.6693,
        "America/Santiago",
    ),
    (
        "Coquimbo",
        "Chile",
        "🇨🇱",
        -29.9533,
        -71.3395,
        "America/Santiago",
    ),
    (
        "Concepción",
        "Chile",
        "🇨🇱",
        -36.8201,
        -73.0444,
        "America/Santiago",
    ),
    (
        "Iquique",
        "Chile",
        "🇨🇱",
        -20.2307,
        -70.1357,
        "America/Santiago",
    ),
    (
        "Lima",
        "Perú",
        "🇵🇪",
        -12.0464,
        -77.0428,
        "America/Lima",
    ),
    (
        "Arequipa",
        "Perú",
        "🇵🇪",
        -16.4090,
        -71.5375,
        "America/Lima",
    ),
    (
        "Ciudad de México",
        "México",
        "🇲🇽",
        19.4326,
        -99.1332,
        "America/Mexico_City",
    ),
    (
        "Guadalajara",
        "México",
        "🇲🇽",
        20.6597,
        -103.3496,
        "America/Mexico_City",
    ),
    (
        "Cali",
        "Colombia",
        "🇨🇴",
        3.4516,
        -76.5320,
        "America/Bogota",
    ),
    (
        "Bogotá",
        "Colombia",
        "🇨🇴",
        4.7110,
        -74.0721,
        "America/Bogota",
    ),
    (
        "Caracas",
        "Venezuela",
        "🇻🇪",
        10.4806,
        -66.9036,
        "America/Caracas",
    ),
    (
        "Maracaibo",
        "Venezuela",
        "🇻🇪",
        10.6427,
        -71.6125,
        "America/Caracas",
    ),
    (
        "Quito",
        "Ecuador",
        "🇪🇨",
        -0.1807,
        -78.4678,
        "America/Guayaquil",
    ),
    (
        "La Habana",
        "Cuba",
        "🇨🇺",
        23.1136,
        -82.3666,
        "America/Havana",
    ),
    (
        "Los Ángeles",
        "Estados Unidos",
        "🇺🇸",
        34.0522,
        -118.2437,
        "America/Los_Angeles",
    ),
]

PLATFORMS = (
    "instagram",
    "facebook",
)

# ============================================================
# UTILIDADES
# ============================================================

def log(*values):
    print(*values, flush=True)


def now_utc():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def save_json(path, value):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def safe_name(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        str(value),
    )[:100]


def short_error(exc):
    message = str(exc)

    for variable in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
    ):
        secret = os.getenv(variable, "")

        if secret:
            message = message.replace(
                secret,
                "***",
            )

    return message[:350]


def parse_emsc_time(value):
    if not value:
        raise ValueError(
            "Fecha EMSC no disponible"
        )

    text = str(value).strip()

    parsed = datetime.fromisoformat(
        text.replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=UTC
        )

    return parsed.astimezone(UTC)


def depth_text(depth):
    if depth is None:
        return "No disponible"

    return f"{depth:.1f} km"


# ============================================================
# DISTANCIA HAVERSINE
# ============================================================

def haversine(
    lat1,
    lon1,
    lat2,
    lon2,
):
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (lat1, lon1, lat2, lon2),
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    a = min(
        1.0,
        max(0.0, a),
    )

    return (
        6371.0088
        * 2
        * math.asin(math.sqrt(a))
    )


def matching_cities(
    latitude,
    longitude,
):
    matches = []

    for city in CITIES:
        distance = haversine(
            latitude,
            longitude,
            city[3],
            city[4],
        )

        if distance <= RADIUS_KM:
            matches.append(
                (distance, city)
            )

    return sorted(
        matches,
        key=lambda item: item[0],
    )


# ============================================================
# CONSULTAR EMSC-CSEM
# ============================================================

def fetch_events(hours=LOOKBACK_HOURS):
    end = now_utc()

    start = end - timedelta(
        hours=hours
    )

    parameters = {
        "format": "json",
        "starttime": start.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "endtime": end.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "minmagnitude": MIN_MAG,
        "maxmagnitude": MAX_MAG,
        "catalog": "EMSC-RTS",
        "orderby": "time",
        "limit": 20000,
        "nodata": 204,
    }

    log(
        "Consultando EMSC-CSEM:",
        iso(start),
        "hasta",
        iso(end),
    )

    response = HTTP.get(
        EMSC_API,
        params=parameters,
        timeout=70,
    )

    if response.status_code == 204:
        log("EMSC: sin eventos")
        return []

    response.raise_for_status()

    payload = response.json()

    if isinstance(payload, dict):
        if payload.get("type") == "Feature":
            features = [payload]
        else:
            features = payload.get(
                "features",
                [],
            )
    else:
        raise RuntimeError(
            "Formato EMSC inesperado"
        )

    if not isinstance(features, list):
        raise RuntimeError(
            "EMSC no devolvió lista de eventos"
        )

    # No aceptar silenciosamente una consulta
    # posiblemente truncada por el límite.
    if len(features) >= 20000:
        raise RuntimeError(
            "Consulta EMSC alcanzó el límite "
            "de 20000 eventos. Dividir la ventana."
        )

    events = []
    seen_ids = set()

    for feature in features:
        try:
            properties = (
                feature.get("properties")
                or {}
            )

            geometry = (
                feature.get("geometry")
                or {}
            )

            coordinates = (
                geometry.get("coordinates")
                or []
            )

            magnitude = float(
                properties["mag"]
            )

            latitude = properties.get(
                "lat"
            )

            longitude = properties.get(
                "lon"
            )

            if latitude is None:
                latitude = coordinates[1]

            if longitude is None:
                longitude = coordinates[0]

            latitude = float(latitude)
            longitude = float(longitude)

            if not all(
                math.isfinite(value)
                for value in (
                    magnitude,
                    latitude,
                    longitude,
                )
            ):
                continue

            if not (
                MIN_MAG <= magnitude < MAX_MAG
            ):
                continue

            if not (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                continue

            # El identificador unid es el
            # identificador de evento de EMSC.
            # source_id puede designar un origen,
            # por eso se prioriza unid.
            source_id = str(
                properties.get("unid")
                or feature.get("id")
                or properties.get("source_id")
                or ""
            ).strip()

            if not source_id:
                continue

            unique_id = (
                "emsc_" + source_id
            )

            if unique_id in seen_ids:
                continue

            event_time = parse_emsc_time(
                properties["time"]
            )

            # Descartar registros fuera
            # de la ventana temporal.
            if (
                event_time < start
                or event_time > end
            ):
                continue

            depth_value = properties.get(
                "depth"
            )

            depth = None

            if depth_value is not None:
                candidate = float(
                    depth_value
                )

                if math.isfinite(candidate):
                    depth = candidate

            place = str(
                properties.get(
                    "flynn_region"
                )
                or "Región no especificada"
            ).strip()

            matches = matching_cities(
                latitude,
                longitude,
            )

            if not matches:
                continue

            distance, city = matches[0]

            # Enlace de consulta por evento.
            event_url = (
                EMSC_API
                + "?"
                + urllib.parse.urlencode({
                    "format": "json",
                    "eventid": source_id,
                })
            )

            events.append({
                "id": unique_id,
                "emsc_id": source_id,
                "source": "EMSC-CSEM",
                "mag": magnitude,
                "magtype": str(
                    properties.get(
                        "magtype"
                    )
                    or ""
                ),
                "lat": latitude,
                "lon": longitude,
                "depth": depth,
                "place": place,
                "time": event_time,
                "url": event_url,
                "city": city,
                "distance_km": distance,
                "all_cities": [
                    {
                        "city": item[0],
                        "country": item[1],
                        "km": round(
                            dist,
                            1,
                        ),
                    }
                    for dist, item in matches
                ],
            })

            seen_ids.add(unique_id)

        except (
            KeyError,
            TypeError,
            ValueError,
            IndexError,
        ) as exc:
            log(
                "EMSC: registro omitido:",
                short_error(exc),
            )

    events.sort(
        key=lambda item: item["time"]
    )

    log(
        "EMSC: eventos recibidos:",
        len(features),
    )

    log(
        "EMSC: eventos dentro de "
        "los radios de 100 km:",
        len(events),
    )

    return events


# ============================================================
# COLORES SEGUN MAGNITUD
# ============================================================

def magnitude_colors(magnitude):
    if 4.0 <= magnitude < 5.0:
        return {
            "banner": "#168A45",
            "text": "#FFFFFF",
            "category": "VERDE",
        }

    if 5.0 <= magnitude < 6.0:
        return {
            "banner": "#FFE100",
            "text": "#101010",
            "category": "AMARILLO",
        }

    if 6.0 <= magnitude < 7.0:
        return {
            "banner": "#FA8615",
            "text": "#101010",
            "category": "NARANJO",
        }

    raise ValueError(
        "Magnitud fuera del intervalo"
    )


# ============================================================
# TIPOGRAFIA
# ============================================================

def get_font(
    size,
    bold=True,
):
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


def fitted_font(
    draw,
    text,
    max_width,
    max_height,
    initial=90,
    minimum=20,
):
    for size in range(
        initial,
        minimum - 1,
        -2,
    ):
        selected = get_font(size)

        bounds = draw.textbbox(
            (0, 0),
            text,
            font=selected,
        )

        width = (
            bounds[2] - bounds[0]
        )

        height = (
            bounds[3] - bounds[1]
        )

        if (
            width <= max_width
            and height <= max_height
        ):
            return selected

    return get_font(minimum)


def centered_text(
    draw,
    text,
    y,
    color,
    max_width=990,
    max_height=100,
    initial=85,
):
    selected = fitted_font(
        draw,
        text,
        max_width,
        max_height,
        initial,
    )

    bounds = draw.textbbox(
        (0, 0),
        text,
        font=selected,
    )

    text_width = (
        bounds[2] - bounds[0]
    )

    x = (
        WIDTH - text_width
    ) / 2

    # Ajuste de la caja de texto de Pillow.
    x -= bounds[0]

    draw.text(
        (x, y),
        text,
        font=selected,
        fill=color,
    )


# ============================================================
# MAPA CARTOGRAFICO
# ============================================================

def build_map(event):
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    latitude = event["lat"]
    longitude = event["lon"]

    city = event["city"]

    projection = ccrs.PlateCarree(
        central_longitude=longitude
    )

    fig = plt.figure(
        figsize=(9, 9),
        dpi=160,
        facecolor="#071D34",
    )

    ax = fig.add_axes(
        [0, 0, 1, 1],
        projection=projection,
    )

    latitude_span = 3.0

    longitude_span = (
        3.0
        / max(
            0.40,
            math.cos(
                math.radians(latitude)
            ),
        )
    )

    ax.set_extent(
        [
            longitude - longitude_span,
            longitude + longitude_span,
            latitude - latitude_span,
            latitude + latitude_span,
        ],
        crs=ccrs.PlateCarree(),
    )

    ax.set_facecolor("#073D67")

    ax.add_feature(
        cfeature.OCEAN.with_scale("110m"),
        facecolor="#06456D",
        zorder=0,
    )

    ax.add_feature(
        cfeature.LAND.with_scale("110m"),
        facecolor="#50764D",
        zorder=1,
    )

    ax.add_feature(
        cfeature.COASTLINE.with_scale("110m"),
        edgecolor="#E4E9DD",
        linewidth=1.4,
        zorder=2,
    )

    ax.add_feature(
        cfeature.BORDERS.with_scale("110m"),
        edgecolor="#CFD5C9",
        linewidth=0.9,
        zorder=2,
    )

    ax.gridlines(
        draw_labels=False,
        color="white",
        alpha=0.22,
        linewidth=0.5,
    )

    # Epicentro verdadero según EMSC.
    # Los anillos son decorativos.
    for size in (
        2600,
        1300,
        450,
    ):
        ax.scatter(
            [longitude],
            [latitude],
            s=size,
            facecolors="none",
            edgecolors="#FA343A",
            linewidths=2.4,
            transform=ccrs.PlateCarree(),
            zorder=7,
        )

    ax.scatter(
        [longitude],
        [latitude],
        s=115,
        c="#FF142C",
        edgecolors="white",
        linewidths=1.8,
        transform=ccrs.PlateCarree(),
        zorder=9,
    )

    # Centro de la ciudad monitoreada.
    ax.scatter(
        [city[4]],
        [city[3]],
        s=55,
        c="white",
        edgecolors="black",
        linewidths=1,
        transform=ccrs.PlateCarree(),
        zorder=9,
    )

    ax.text(
        city[4],
        city[3],
        "  " + city[0],
        transform=ccrs.PlateCarree(),
        color="white",
        fontsize=13,
        weight="bold",
        zorder=10,
        path_effects=[
            pe.withStroke(
                linewidth=3,
                foreground="#111111",
            )
        ],
    )

    buffer = io.BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=160,
        facecolor="#071D34",
    )

    plt.close(fig)

    buffer.seek(0)

    image = ImageOps.fit(
        Image.open(buffer).convert("RGB"),
        (WIDTH, HEIGHT),
        method=Image.Resampling.LANCZOS,
    )

    return image


# ============================================================
# COMPONER IMAGEN
# ============================================================

def make_image(
    event,
    output_path,
):
    image = build_map(event)

    draw = ImageDraw.Draw(image)

    city = event["city"]

    colors = magnitude_colors(
        event["mag"]
    )

    banner = colors["banner"]
    foreground = colors["text"]

    # Banner superior.
    draw.rounded_rectangle(
        (26, 28, 1054, 211),
        radius=26,
        fill=banner,
        outline="#FFFFFF",
        width=3,
    )

    centered_text(
        draw,
        "ÚLTIMO MINUTO",
        55,
        foreground,
        max_width=990,
        max_height=115,
        initial=110,
    )

    # Banner inferior.
    draw.rounded_rectangle(
        (20, 1060, 1060, 1313),
        radius=24,
        fill=banner,
        outline="#FFFFFF",
        width=3,
    )

    centered_text(
        draw,
        (
            "SISMO DE MAGNITUD "
            f"{event['mag']:.1f}"
        ),
        1082,
        foreground,
        max_width=990,
        max_height=95,
        initial=74,
    )

    centered_text(
        draw,
        (
            "CERCA DE "
            + city[0].upper()
            + ", "
            + city[1].upper()
        ),
        1187,
        foreground,
        max_width=990,
        max_height=85,
        initial=54,
    )

    # Ningún banner secundario con
    # "Epicentro cercano a...".

    output_path = Path(output_path)

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

    log(
        "IMAGEN:",
        output_path,
        "| categoría:",
        colors["category"],
    )


# ============================================================
# PIE DE PUBLICACION
# ============================================================

def make_caption(event):
    city = event["city"]

    local_time = event["time"].astimezone(
        ZoneInfo(city[5])
    )

    age_seconds = (
        now_utc() - event["time"]
    ).total_seconds()

    if 0 <= age_seconds <= 1800:
        headline_prefix = (
            "🚨 ÚLTIMO MINUTO"
        )

        opening = (
            "Hace unos instantes, "
            "se ha registrado"
        )
    else:
        headline_prefix = (
            "📡 SISMO REGISTRADO"
        )

        opening = (
            "Se registró"
        )

    city_tag = re.sub(
        r"[^A-Za-z]",
        "",
        city[0],
    )

    country_tag = re.sub(
        r"[^A-Za-z]",
        "",
        city[1],
    )

    text = (
        f"{headline_prefix} | "
        f"SISMO DE MAGNITUD "
        f"{event['mag']:.1f} CERCA DE "
        f"{city[0].upper()}, "
        f"{city[1].upper()} "
        f"{city[2]}\n\n"

        f"{opening} un sismo de "
        f"magnitud {event['mag']:.1f} "
        f"en las cercanías de "
        f"{city[0]}, {city[1]}.\n\n"

        f"📍 Epicentro: "
        f"{event['place']}\n"

        f"📏 Distancia a "
        f"{city[0]}: "
        f"{event['distance_km']:.1f} km\n"

        f"📊 Magnitud: "
        f"{event['mag']:.1f}\n"

        f"⬇️ Profundidad: "
        f"{depth_text(event['depth'])}\n"

        f"🕒 Fecha y hora local: "
        f"{local_time:%d/%m/%Y – %H:%M}\n"

        f"🌐 Coordenadas: "
        f"{event['lat']:.3f}, "
        f"{event['lon']:.3f}\n\n"

        "💬 ¿Sentiste el movimiento? "
        "Comenta desde qué ciudad y "
        "qué intensidad percibiste.\n\n"

        "📲 Sigue a @sismonauta para "
        "conocer los últimos registros "
        "sísmicos.\n\n"

        "Datos sísmicos: EMSC-CSEM\n"
        f"{EMSC_WEB}\n\n"

        "#SISMONAUTA #UltimoMinuto "
        "#Sismo #Terremoto "
        f"#{country_tag} "
        f"#{city_tag} "
        "#EMSC #Sismologia"
    )

    return text[:2200]


# ============================================================
# GITHUB CONTENTS API
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

        token = os.environ[
            "GH_TOKEN"
        ]

        self.base = (
            "https://api.github.com/repos/"
            + self.repository
        )

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": (
                "Bearer " + token
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
            timeout=30,
        )

        response.raise_for_status()

        information = response.json()

        if information.get("private"):
            raise RuntimeError(
                "El repositorio debe ser público "
                "para alojar imágenes accesibles "
                "por Meta."
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
            timeout=30,
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()

        data = response.json()

        decoded = base64.b64decode(
            data["content"]
        ).decode("utf-8")

        return json.loads(decoded)

    def write_bytes(
        self,
        path,
        content,
    ):
        endpoint = self.endpoint(
            path
        )

        for attempt in range(5):
            previous = self.session.get(
                endpoint,
                params={
                    "ref": self.branch
                },
                timeout=30,
            )

            if previous.status_code == 404:
                sha = None
            else:
                previous.raise_for_status()

                sha = previous.json()[
                    "sha"
                ]

            payload = {
                "message": (
                    "SISMONAUTA EMSC: "
                    + path
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
                timeout=60,
            )

            if response.ok:
                return

            if (
                response.status_code
                in (409, 422)
                and attempt < 4
            ):
                time.sleep(
                    2 + attempt
                )
                continue

            response.raise_for_status()

    def write_json(
        self,
        path,
        data,
    ):
        raw = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")

        self.write_bytes(
            path,
            raw,
        )

    def public_url(self, path):
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
# META GRAPH API
# ============================================================

def graph(
    method,
    path,
    token,
    parameters=None,
    base=None,
):
    base = base or IG_BASE

    endpoint = (
        f"{base}/{GRAPH_VERSION}/{path}"
    )

    values = dict(
        parameters or {}
    )

    values["access_token"] = token

    if method == "GET":
        response = requests.get(
            endpoint,
            params=values,
            timeout=60,
        )
    else:
        response = requests.post(
            endpoint,
            data=values,
            timeout=80,
        )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Meta respondió sin JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = (
            data.get("error")
            or {}
        )

        raise RuntimeError(
            "Meta API: "
            + str(
                error.get(
                    "message",
                    data,
                )
            )[:250]
        )

    return data


# ============================================================
# VALIDAR CUENTAS
# ============================================================

def validate_accounts():
    ig_token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    ig_id = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    fb_token = os.environ[
        "FACEBOOK_PAGE_TOKEN"
    ]

    fb_id = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    instagram = graph(
        "GET",
        "me",
        ig_token,
        {
            "fields": "id,username"
        },
    )

    if (
        str(instagram.get("id"))
        != str(ig_id)
        or str(
            instagram.get(
                "username",
                "",
            )
        ).lower() != "sismonauta"
    ):
        raise RuntimeError(
            "Instagram no corresponde "
            "a @sismonauta"
        )

    facebook = graph(
        "GET",
        fb_id,
        fb_token,
        {
            "fields": "id,name"
        },
        FB_BASE,
    )

    if (
        str(facebook.get("id"))
        != str(fb_id)
        or "sismonauta" not in str(
            facebook.get(
                "name",
                "",
            )
        ).lower()
    ):
        raise RuntimeError(
            "La fanpage no corresponde "
            "a SISMONAUTA"
        )

    log(
        "Instagram:",
        instagram.get("username"),
    )

    log(
        "Facebook:",
        facebook.get("name"),
    )

    return (
        ig_id,
        ig_token,
        fb_id,
        fb_token,
    )


# ============================================================
# PUBLICAR INSTAGRAM
# ============================================================

def publish_instagram(
    user_id,
    token,
    image_url,
    caption,
):
    response = graph(
        "POST",
        f"{user_id}/media",
        token,
        {
            "image_url": image_url,
            "caption": caption,
        },
    )

    container_id = response.get(
        "id"
    )

    if not container_id:
        raise RuntimeError(
            "Instagram no devolvió "
            "identificador de contenedor"
        )

    finished = False

    for attempt in range(25):
        time.sleep(5)

        result = graph(
            "GET",
            container_id,
            token,
            {
                "fields": "status_code"
            },
        )

        status = result.get(
            "status_code"
        )

        if status == "FINISHED":
            finished = True
            break

        if status in (
            "ERROR",
            "EXPIRED",
        ):
            raise RuntimeError(
                "Instagram contenedor: "
                + str(status)
            )

    if not finished:
        raise RuntimeError(
            "Instagram no terminó "
            "de procesar la imagen"
        )

    result = graph(
        "POST",
        f"{user_id}/media_publish",
        token,
        {
            "creation_id": container_id
        },
    )

    identifier = result.get(
        "id"
    )

    if not identifier:
        raise RuntimeError(
            "Instagram no confirmó "
            "la publicación"
        )

    return str(identifier)


# ============================================================
# PUBLICAR FACEBOOK
# ============================================================

def publish_facebook(
    page_id,
    token,
    image_url,
    caption,
):
    result = graph(
        "POST",
        f"{page_id}/photos",
        token,
        {
            "url": image_url,
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
            "Facebook no confirmó "
            "la publicación"
        )

    return str(identifier)


# ============================================================
# VERIFICAR IMAGEN PUBLICA
# ============================================================

def wait_public_image(url):
    for attempt in range(12):
        try:
            response = HTTP.get(
                url,
                timeout=25,
            )

            content_type = (
                response.headers.get(
                    "Content-Type",
                    "",
                )
            )

            if (
                response.ok
                and content_type.startswith(
                    "image/"
                )
            ):
                return

        except requests.RequestException:
            pass

        time.sleep(5)

    raise RuntimeError(
        "La imagen todavía no está "
        "accesible públicamente"
    )


# ============================================================
# HISTORIAL DE EVENTOS
# ============================================================

def new_state():
    return {
        "events": {}
    }


def ensure_entry(
    state,
    event,
):
    entry = state[
        "events"
    ].setdefault(
        event["id"],
        {
            "magnitude": event["mag"],
            "place": event["place"],
            "time": iso(
                event["time"]
            ),
            "source": "EMSC-CSEM",
            "targets": {},
        },
    )

    entry.setdefault(
        "targets",
        {},
    )

    for platform in PLATFORMS:
        entry["targets"].setdefault(
            platform,
            {
                "status": "pending"
            },
        )

    return entry


def event_is_finished(entry):
    targets = entry.get(
        "targets",
        {},
    )

    return all(
        targets.get(
            platform,
            {},
        ).get("status") == "published"
        for platform in PLATFORMS
    )


def event_is_uncertain(entry):
    targets = entry.get(
        "targets",
        {},
    )

    return any(
        targets.get(
            platform,
            {},
        ).get("status") == "uncertain"
        for platform in PLATFORMS
    )


# ============================================================
# PROCESAR EVENTO
# ============================================================

def process_event(
    event,
    github,
    state,
    publish,
    credentials,
):
    event_id = event["id"]

    entry = ensure_entry(
        state,
        event,
    )

    if event_is_finished(entry):
        log(
            "YA PUBLICADO:",
            event_id,
        )

        return "already_published"

    # Ante respuesta incierta de Meta,
    # no repetir automáticamente.
    if event_is_uncertain(entry):
        log(
            "REVISIÓN MANUAL:",
            event_id,
        )

        return "partial_or_uncertain"

    filename = (
        safe_name(event_id)
        + ".jpg"
    )

    image_path = (
        OUT / filename
    )

    make_image(
        event,
        image_path,
    )

    caption = make_caption(
        event
    )

    save_json(
        OUT
        / (
            safe_name(event_id)
            + ".json"
        ),
        {
            "event": event,
            "caption": caption,
        },
    )

    if not publish:
        log(
            "VISTA PREVIA:",
            image_path,
        )

        return "preview_only"

    ig_id, ig_token, fb_id, fb_token = (
        credentials
    )

    remote_path = (
        IMAGE_DIR
        + "/"
        + filename
    )

    # El historial se guarda antes
    # de las operaciones externas.
    github.write_json(
        STATE_PATH,
        state,
    )

    # Guardar la imagen para que Meta
    # pueda descargarla mediante URL pública.
    github.write_bytes(
        remote_path,
        image_path.read_bytes(),
    )

    image_url = github.public_url(
        remote_path
    )

    wait_public_image(
        image_url
    )

    entry["image_url"] = (
        image_url
    )

    github.write_json(
        STATE_PATH,
        state,
    )

    for platform in PLATFORMS:
        target = entry[
            "targets"
        ][platform]

        if (
            target.get("status")
            == "published"
        ):
            continue

        # Estado de seguridad:
        # nunca reenviar ciegamente
        # si Meta pudiera haber publicado.
        target["status"] = "uncertain"

        target.pop(
            "error",
            None,
        )

        github.write_json(
            STATE_PATH,
            state,
        )

        try:
            if platform == "instagram":
                identifier = (
                    publish_instagram(
                        ig_id,
                        ig_token,
                        image_url,
                        caption,
                    )
                )
            else:
                identifier = (
                    publish_facebook(
                        fb_id,
                        fb_token,
                        image_url,
                        caption,
                    )
                )

            target["status"] = (
                "published"
            )

            target["id"] = (
                identifier
            )

            github.write_json(
                STATE_PATH,
                state,
            )

            log(
                "PUBLICADO:",
                event_id,
                platform,
                identifier,
            )

        except Exception as exc:
            target["error"] = (
                short_error(exc)
            )

            github.write_json(
                STATE_PATH,
                state,
            )

            log(
                "ERROR DE PUBLICACIÓN:",
                platform,
                short_error(exc),
            )

    if event_is_finished(entry):
        return "published"

    return "partial_or_uncertain"


# ============================================================
# EJECUCION PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=APP
    )

    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Publicar en Instagram "
            "y Facebook"
        ),
    )

    parser.add_argument(
        "--out",
        default="output_ciudades",
    )

    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=LOOKBACK_HOURS,
    )

    args = parser.parse_args()

    global OUT

    OUT = Path(
        args.out
    )

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    receipt_path = (
        OUT
        / "estado_publicacion.json"
    )

    receipt = {
        "status": "starting",
        "source": "EMSC-CSEM",
        "publish_requested": args.publish,
        "checked_at": iso(
            now_utc()
        ),
        "radius_km": RADIUS_KM,
        "min_magnitude": MIN_MAG,
        "max_magnitude_exclusive": MAX_MAG,
        "events": [],
    }

    try:
        if not (
            1 <= args.lookback_hours <= 168
        ):
            raise ValueError(
                "La ventana temporal debe "
                "estar entre 1 y 168 horas"
            )

        log(
            "================================="
        )
        log(
            "SISMONAUTA | EMSC-CSEM"
        )
        log(
            "Ciudades:",
            len(CITIES),
        )
        log(
            "Radio:",
            RADIUS_KM,
            "km",
        )
        log(
            "Magnitudes:",
            MIN_MAG,
            "a menos de",
            MAX_MAG,
        )
        log(
            "Publicar:",
            args.publish,
        )
        log(
            "================================="
        )

        github = (
            Github()
            if args.publish
            else None
        )

        original_state = (
            github.read(STATE_PATH)
            if github
            else None
        )

        state = (
            original_state
            or new_state()
        )

        state.setdefault(
            "events",
            {},
        )

        events = fetch_events(
            hours=args.lookback_hours
        )

        # Primera instalación:
        # evitar publicar terremotos
        # antiguos de toda la ventana.
        if (
            args.publish
            and original_state is None
        ):
            threshold = (
                now_utc()
                - timedelta(
                    minutes=FIRST_RUN_MINUTES
                )
            )

            events = [
                event
                for event in events
                if event["time"] >= threshold
            ]

            # Persistir la inicialización
            # incluso si no hay eventos.
            github.write_json(
                STATE_PATH,
                state,
            )

        # Con historial existente, excluir
        # los ya confirmados en ambas redes.
        pending = [
            event
            for event in events
            if not event_is_finished(
                state["events"].get(
                    event["id"],
                    {},
                )
            )
        ]

        log(
            "Eventos coincidentes:",
            len(events),
        )

        log(
            "Eventos pendientes:",
            len(pending),
        )

        if not pending:
            receipt["status"] = (
                "no_new_event"
            )

            save_json(
                receipt_path,
                receipt,
            )

            log(
                "SIN NUEVOS EVENTOS"
            )

            return 0

        # Limitar la cantidad de publicaciones
        # por ejecución sin perder las demás:
        # seguirán pendientes en la siguiente.
        pending = pending[
            :MAX_EVENTS_PER_RUN
        ]

        credentials = None

        if args.publish:
            credentials = (
                validate_accounts()
            )

        statuses = []

        for event in pending:
            log(
                "EVENTO EMSC:",
                event["id"],
                f"M{event['mag']:.1f}",
                event["city"][0],
                f"{event['distance_km']:.1f} km",
            )

            try:
                result = process_event(
                    event,
                    github,
                    state,
                    args.publish,
                    credentials,
                )
            except Exception as exc:
                result = "partial_or_uncertain"

                log(
                    "ERROR DE EVENTO:",
                    event["id"],
                    short_error(exc),
                )

            statuses.append(
                result
            )

            entry = ensure_entry(
                state,
                event,
            )

            receipt["events"].append({
                "id": event["id"],
                "magnitude": event["mag"],
                "city": event["city"][0],
                "country": event["city"][1],
                "distance_km": round(
                    event["distance_km"],
                    1,
                ),
                "status": result,
                "targets": entry["targets"],
            })

            save_json(
                receipt_path,
                receipt,
            )

        if all(
            status == "preview_only"
            for status in statuses
        ):
            receipt["status"] = (
                "preview_only"
            )

        elif any(
            status == "partial_or_uncertain"
            for status in statuses
        ):
            receipt["status"] = (
                "partial_or_uncertain"
            )

        elif all(
            status in (
                "published",
                "already_published",
            )
            for status in statuses
        ):
            receipt["status"] = (
                "published"
            )

        else:
            receipt["status"] = (
                "error"
            )

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

        receipt["error"] = (
            short_error(exc)
        )

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
