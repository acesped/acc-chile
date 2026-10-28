
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA | ALERTAS SISMICAS POR CIUDADES
Version EMSC-CSEM / 10 minutos / mapa mejorado

- Ultimos 10 minutos (600 segundos)
- Magnitudes M4.0 <= M < M7.0
- 100 km alrededor de 15 ciudades
- Mapa satelital con respaldo cartografico
- Imagen JPEG 1080 x 1350
- Instagram y Facebook
- Historial en GitHub Contents API
- Sin publicaciones duplicadas confirmadas
- Sin monitoreo de tsunami

Uso:
    python sismonauta_ciudades.py
    python sismonauta_ciudades.py --publish
    python sismonauta_ciudades.py --lookback-minutes 10
    python sismonauta_ciudades.py --lookback-minutes 10 --publish

El registro sismico puede ser revisado por EMSC.
No constituye una alerta oficial.
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
    ImageEnhance,
)

# ============================================================
# CONFIGURACION
# ============================================================

UTC = timezone.utc

EMSC_API = (
    "https://www.seismicportal.eu/"
    "fdsnws/event/1/query"
)

SATELLITE_API = (
    "https://server.arcgisonline.com/"
    "ArcGIS/rest/services/World_Imagery/"
    "MapServer/export"
)

MIN_MAG = 4.0
MAX_MAG = 7.0

RADIUS_KM = 100.0

LOOKBACK_HOURS = 0
LOOKBACK_MINUTES = 10

MAX_EVENTS_PER_RUN = 30

WIDTH = 1080
HEIGHT = 1350

MAP_HALF_WIDTH_M = 135000

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

FB_BASE = "https://graph.facebook.com"

PLATFORMS = (
    "instagram",
    "facebook",
)

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": "Sismonauta-EMSC/5.0",
    "Accept": "application/json",
})

# ============================================================
# CIUDADES MONITOREADAS
# ============================================================

# nombre, pais, bandera, latitud,
# longitud, zona horaria

CITIES = [
    (
        "Santiago", "Chile", "🇨🇱",
        -33.4489, -70.6693,
        "America/Santiago",
    ),
    (
        "Coquimbo", "Chile", "🇨🇱",
        -29.9533, -71.3395,
        "America/Santiago",
    ),
    (
        "Concepción", "Chile", "🇨🇱",
        -36.8201, -73.0444,
        "America/Santiago",
    ),
    (
        "Iquique", "Chile", "🇨🇱",
        -20.2307, -70.1357,
        "America/Santiago",
    ),
    (
        "Lima", "Perú", "🇵🇪",
        -12.0464, -77.0428,
        "America/Lima",
    ),
    (
        "Arequipa", "Perú", "🇵🇪",
        -16.4090, -71.5375,
        "America/Lima",
    ),
    (
        "Ciudad de México", "México", "🇲🇽",
        19.4326, -99.1332,
        "America/Mexico_City",
    ),
    (
        "Guadalajara", "México", "🇲🇽",
        20.6597, -103.3496,
        "America/Mexico_City",
    ),
    (
        "Cali", "Colombia", "🇨🇴",
        3.4516, -76.5320,
        "America/Bogota",
    ),
    (
        "Bogotá", "Colombia", "🇨🇴",
        4.7110, -74.0721,
        "America/Bogota",
    ),
    (
        "Caracas", "Venezuela", "🇻🇪",
        10.4806, -66.9036,
        "America/Caracas",
    ),
    (
        "Maracaibo", "Venezuela", "🇻🇪",
        10.6427, -71.6125,
        "America/Caracas",
    ),
    (
        "Quito", "Ecuador", "🇪🇨",
        -0.1807, -78.4678,
        "America/Guayaquil",
    ),
    (
        "La Habana", "Cuba", "🇨🇺",
        23.1136, -82.3666,
        "America/Havana",
    ),
    (
        "Los Ángeles", "Estados Unidos", "🇺🇸",
        34.0522, -118.2437,
        "America/Los_Angeles",
    ),
]

# ============================================================
# UTILIDADES
# ============================================================

def log(*values):
    print(*values, flush=True)


def now_utc():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def safe_name(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        str(value),
    )[:100]


def save_json(path, data):
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


def short_error(exc):
    message = str(exc)

    for key in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
    ):
        secret = os.getenv(key, "")

        if secret:
            message = message.replace(
                secret,
                "***",
            )

    return message[:350]


def parse_time(value):
    if not value:
        raise ValueError(
            "Hora de origen no disponible"
        )

    result = datetime.fromisoformat(
        str(value).strip().replace(
            "Z",
            "+00:00",
        )
    )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=UTC,
        )

    return result.astimezone(UTC)


def depth_text(value):
    if value is None:
        return "No disponible"

    return f"{value:.1f} km"


# ============================================================
# DISTANCIA HAVERSINE
# ============================================================

def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        (lat1, lon1, lat2, lon2),
    )

    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    a = min(1.0, max(0.0, a))

    return (
        6371.0088
        * 2
        * math.asin(math.sqrt(a))
    )


def nearest_cities(lat, lon):
    result = []

    for city in CITIES:
        distance = haversine(
            lat,
            lon,
            city[3],
            city[4],
        )

        result.append(
            (distance, city)
        )

    return sorted(
        result,
        key=lambda item: item[0],
    )


def matching_cities(lat, lon):
    return [
        (distance, city)
        for distance, city in nearest_cities(
            lat, lon
        )
        if distance <= RADIUS_KM
    ]


# ============================================================
# CONSULTA EMSC - ULTIMOS 10 MINUTOS
# ============================================================

def fetch_events():

    end = now_utc()

    start = end - timedelta(
        minutes=LOOKBACK_MINUTES
    )

    duration = (
        end - start
    ).total_seconds()

    if duration != 600:
        raise RuntimeError(
            "La consulta no comprende "
            "exactamente diez minutos"
        )

    log("=" * 52)
    log("CONSULTA SISMICA EMSC-CSEM")
    log("INICIO UTC:", iso(start))
    log("FIN UTC:", iso(end))
    log(
        "DURACION:",
        duration / 60,
        "minutos",
    )
    log("=" * 52)

    parameters = {
        "format": "json",
        "starttime": start.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "endtime": end.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "minmagnitude": MIN_MAG,
        "orderby": "time",
        "limit": 20000,
        "nodata": 204,
    }

    response = HTTP.get(
        EMSC_API,
        params=parameters,
        timeout=75,
    )

    log(
        "URL CONSULTADA:",
        response.url,
    )

    log(
        "HTTP:",
        response.status_code,
    )

    if response.status_code == 204:
        log(
            "EMSC: sin registros en "
            "la ventana consultada"
        )
        return []

    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Respuesta EMSC invalida"
        )

    if payload.get("type") == "Feature":
        features = [payload]
    else:
        features = payload.get(
            "features",
            [],
        )

    if not isinstance(features, list):
        raise RuntimeError(
            "Respuesta sin lista de eventos"
        )

    if len(features) >= 20000:
        raise RuntimeError(
            "Resultados EMSC truncados"
        )

    counters = {
        "received": len(features),
        "invalid": 0,
        "outside_time": 0,
        "outside_magnitude": 0,
        "outside_radius": 0,
        "duplicate": 0,
        "accepted": 0,
    }

    accepted = []
    seen = set()

    log(
        "EVENTOS RECIBIDOS:",
        len(features),
    )

    for number, feature in enumerate(
        features,
        start=1,
    ):
        try:
            props = feature.get(
                "properties"
            ) or {}

            geometry = feature.get(
                "geometry"
            ) or {}

            coords = geometry.get(
                "coordinates"
            ) or []

            magnitude = float(
                props["mag"]
            )

            lat_value = props.get("lat")
            lon_value = props.get("lon")

            if lat_value is None:
                lat_value = coords[1]

            if lon_value is None:
                lon_value = coords[0]

            latitude = float(lat_value)
            longitude = float(lon_value)

            event_time = parse_time(
                props["time"]
            )

            raw_id = str(
                props.get("unid")
                or feature.get("id")
                or props.get("source_id")
                or ""
            ).strip()

            if not raw_id:
                raise ValueError(
                    "Evento sin ID EMSC"
                )

            unique_id = (
                "emsc_" + raw_id
            )

            depth = None
            depth_value = props.get(
                "depth"
            )

            if depth_value is not None:
                value = float(depth_value)

                if math.isfinite(value):
                    depth = value

            place = str(
                props.get("flynn_region")
                or "Región no especificada"
            )

            if not all(
                math.isfinite(value)
                for value in (
                    magnitude,
                    latitude,
                    longitude,
                )
            ):
                raise ValueError(
                    "Valor numerico no finito"
                )

            if not (
                -90 <= latitude <= 90
                and -180 <= longitude <= 180
            ):
                raise ValueError(
                    "Coordenadas invalidas"
                )

        except (
            KeyError,
            ValueError,
            TypeError,
            IndexError,
            AttributeError,
        ) as exc:
            counters["invalid"] += 1

            log(
                "EVENTO INVALIDO:",
                number,
                short_error(exc),
            )
            continue

        nearest = nearest_cities(
            latitude,
            longitude,
        )

        distance, city = nearest[0]

        age_minutes = (
            end - event_time
        ).total_seconds() / 60

        within_time = (
            start <= event_time <= end
        )

        within_magnitude = (
            MIN_MAG <= magnitude < MAX_MAG
        )

        within_radius = (
            distance <= RADIUS_KM
        )

        log("-" * 45)
        log("ID:", raw_id)
        log("MAGNITUD:", magnitude)
        log("HORA UTC:", iso(event_time))
        log(
            "ANTIGUEDAD:",
            round(age_minutes, 1),
            "minutos",
        )
        log(
            "COORDENADAS:",
            latitude,
            longitude,
        )
        log("REGION:", place)
        log(
            "CIUDAD MAS CERCANA:",
            city[0],
            city[1],
        )
        log(
            "DISTANCIA:",
            round(distance, 1),
            "km",
        )
        log(
            "DENTRO DE 10 MIN:",
            within_time,
        )
        log(
            "MAGNITUD PERMITIDA:",
            within_magnitude,
        )
        log(
            "DENTRO DE 100 KM:",
            within_radius,
        )

        if not within_time:
            counters["outside_time"] += 1
            log("DESCARTADO: tiempo")
            continue

        if not within_magnitude:
            counters["outside_magnitude"] += 1
            log("DESCARTADO: magnitud")
            continue

        if not within_radius:
            counters["outside_radius"] += 1
            log("DESCARTADO: distancia")
            continue

        if unique_id in seen:
            counters["duplicate"] += 1
            log("DESCARTADO: duplicado")
            continue

        seen.add(unique_id)

        matches = matching_cities(
            latitude,
            longitude,
        )

        distance, city = matches[0]

        accepted.append({
            "id": unique_id,
            "emsc_id": raw_id,
            "source": "EMSC-CSEM",
            "mag": magnitude,
            "magtype": str(
                props.get("magtype")
                or ""
            ),
            "lat": latitude,
            "lon": longitude,
            "depth": depth,
            "place": place,
            "time": event_time,
            "city": city,
            "distance_km": distance,
            "url": (
                EMSC_API
                + "?"
                + urllib.parse.urlencode({
                    "format": "json",
                    "eventid": raw_id,
                })
            ),
            "all_cities": [
                {
                    "city": match_city[0],
                    "country": match_city[1],
                    "distance_km": round(
                        km, 1
                    ),
                }
                for km, match_city in matches
            ],
        })

        counters["accepted"] += 1

        log(
            "EVENTO ACEPTADO:",
            unique_id,
        )

    accepted.sort(
        key=lambda event: event["time"]
    )

    log("=" * 52)
    log("RESUMEN DE FILTROS")
    log(
        json.dumps(
            counters,
            ensure_ascii=False,
            indent=2,
        )
    )
    log("=" * 52)

    return accepted


# ============================================================
# COLORES DE LOS BANNERS
# ============================================================

def magnitude_colors(magnitude):

    if 4.0 <= magnitude < 5.0:
        return (
            "#168A45",
            "#FFFFFF",
            "VERDE",
        )

    if 5.0 <= magnitude < 6.0:
        return (
            "#FFE100",
            "#151515",
            "AMARILLO",
        )

    if 6.0 <= magnitude < 7.0:
        return (
            "#FA8615",
            "#151515",
            "NARANJO",
        )

    raise ValueError(
        "Magnitud fuera del rango"
    )


# ============================================================
# TIPOGRAFIA
# ============================================================

def get_font(size):
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

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(
                path,
                size,
            )

    return ImageFont.load_default()


def draw_centered(
    draw,
    text,
    center_y,
    max_width,
    max_height,
    max_size,
    color,
):
    selected = get_font(20)

    for size in range(
        max_size,
        19,
        -2,
    ):
        candidate = get_font(size)

        box = draw.textbbox(
            (0, 0),
            text,
            font=candidate,
        )

        width = box[2] - box[0]
        height = box[3] - box[1]

        if (
            width <= max_width
            and height <= max_height
        ):
            selected = candidate
            break

    box = draw.textbbox(
        (0, 0),
        text,
        font=selected,
    )

    width = box[2] - box[0]
    height = box[3] - box[1]

    x = (
        (WIDTH - width) / 2
        - box[0]
    )

    y = (
        center_y
        - height / 2
        - box[1]
    )

    draw.text(
        (int(x), int(y)),
        text,
        font=selected,
        fill=color,
    )


# ============================================================
# MAPA - PROYECCION WEB MERCATOR
# ============================================================

def mercator_xy(lat, lon):
    radius = 6378137.0

    lat = min(
        85.0,
        max(-85.0, lat),
    )

    x = radius * math.radians(lon)

    y = radius * math.log(
        math.tan(
            math.pi / 4
            + math.radians(lat) / 2
        )
    )

    return x, y


def map_bounds(event):
    center_x, center_y = mercator_xy(
        event["lat"],
        event["lon"],
    )

    half_width = MAP_HALF_WIDTH_M

    half_height = (
        half_width
        * HEIGHT
        / WIDTH
    )

    return (
        center_x - half_width,
        center_y - half_height,
        center_x + half_width,
        center_y + half_height,
    )


def pixel_location(lat, lon, bounds):
    xmin, ymin, xmax, ymax = bounds

    x, y = mercator_xy(lat, lon)

    px = (
        (x - xmin)
        / (xmax - xmin)
        * WIDTH
    )

    py = (
        (ymax - y)
        / (ymax - ymin)
        * HEIGHT
    )

    return int(px), int(py)


# ============================================================
# MAPA SATELITAL
# ============================================================

def load_satellite(event):
    bounds = map_bounds(event)

    parameters = {
        "bbox": ",".join(
            str(value)
            for value in bounds
        ),
        "bboxSR": 3857,
        "imageSR": 3857,
        "size": f"{WIDTH},{HEIGHT}",
        "format": "jpg",
        "f": "image",
        "dpi": 160,
    }

    response = requests.get(
        SATELLITE_API,
        params=parameters,
        timeout=45,
        headers={
            "User-Agent": "Sismonauta-EMSC/5.0",
        },
    )

    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        "",
    ).lower()

    if not content_type.startswith("image/"):
        raise RuntimeError(
            "No se recibio imagen satelital"
        )

    with Image.open(
        io.BytesIO(response.content)
    ) as source:
        image = source.convert("RGB")

    if image.size != (WIDTH, HEIGHT):
        raise RuntimeError(
            "Dimensiones satelitales inesperadas"
        )

    image = ImageEnhance.Contrast(
        image
    ).enhance(1.10)

    image = ImageEnhance.Color(
        image
    ).enhance(1.13)

    return image, bounds, "satellite"


# ============================================================
# CARTOGRAFIA DE RESPALDO
# ============================================================

def fallback_map(event):
    import matplotlib
    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cf

    bounds = map_bounds(event)

    xmin, ymin, xmax, ymax = bounds

    projection = ccrs.epsg(3857)

    fig = plt.figure(
        figsize=(9, 11.25),
        dpi=120,
        facecolor="#10273C",
    )

    ax = fig.add_axes(
        [0, 0, 1, 1],
        projection=projection,
    )

    ax.set_extent(
        [xmin, xmax, ymin, ymax],
        crs=projection,
    )

    ax.set_facecolor("#16496D")

    ax.add_feature(
        cf.OCEAN.with_scale("10m"),
        facecolor="#16496D",
        zorder=0,
    )

    ax.add_feature(
        cf.LAND.with_scale("10m"),
        facecolor="#52714D",
        zorder=1,
    )

    ax.add_feature(
        cf.COASTLINE.with_scale("10m"),
        edgecolor="#E7EBE4",
        linewidth=1.2,
        zorder=2,
    )

    ax.add_feature(
        cf.BORDERS.with_scale("10m"),
        edgecolor="#E4E8E1",
        linewidth=0.8,
        zorder=2,
    )

    buffer = io.BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=120,
        facecolor="#10273C",
    )

    plt.close(fig)
    buffer.seek(0)

    with Image.open(buffer) as source:
        image = source.convert("RGB")

        if image.size != (WIDTH, HEIGHT):
            image = image.resize(
                (WIDTH, HEIGHT),
                Image.Resampling.LANCZOS,
            )

    return image, bounds, "fallback"


# ============================================================
# EPICENTRO
# ============================================================

def draw_epicenter(draw, x, y):
    # Anillos decorativos; no son ondas
    # sismicas ni areas de intensidad.

    for radius, color, width in (
        (95, "#FF4141", 5),
        (65, "#FF8A8A", 4),
        (36, "#FFFFFF", 3),
    ):
        draw.ellipse(
            (
                x - radius,
                y - radius,
                x + radius,
                y + radius,
            ),
            outline=color,
            width=width,
        )

    draw.ellipse(
        (
            x - 14,
            y - 14,
            x + 14,
            y + 14,
        ),
        fill="#FF152D",
        outline="#FFFFFF",
        width=4,
    )


# ============================================================
# COMPONER MAPA
# ============================================================

def build_map(event):
    try:
        image, bounds, map_kind = (
            load_satellite(event)
        )

        log("MAPA: satelital")

    except Exception as exc:
        log(
            "MAPA SATELITAL NO DISPONIBLE:",
            short_error(exc),
        )

        image, bounds, map_kind = (
            fallback_map(event)
        )

        log(
            "MAPA: cartografia de respaldo"
        )

    draw = ImageDraw.Draw(image)

    ex, ey = pixel_location(
        event["lat"],
        event["lon"],
        bounds,
    )

    draw_epicenter(
        draw,
        ex,
        ey,
    )

    city = event["city"]

    cx, cy = pixel_location(
        city[3],
        city[4],
        bounds,
    )

    if (
        0 <= cx < WIDTH
        and 0 <= cy < HEIGHT
    ):
        draw.ellipse(
            (
                cx - 8,
                cy - 8,
                cx + 8,
                cy + 8,
            ),
            fill="#FFFFFF",
            outline="#121212",
            width=3,
        )

        label = city[0].upper()
        selected = get_font(30)

        bounds_text = draw.textbbox(
            (0, 0),
            label,
            font=selected,
        )

        label_width = (
            bounds_text[2] - bounds_text[0]
        )

        tx = max(
            15,
            min(
                WIDTH - label_width - 15,
                cx + 20,
            ),
        )

        ty = max(
            300,
            min(
                940,
                cy - 12,
            ),
        )

        draw.text(
            (tx, ty),
            label,
            font=selected,
            fill="white",
            stroke_width=4,
            stroke_fill="#101820",
        )

    return image, map_kind


# ============================================================
# GENERAR IMAGEN FINAL
# ============================================================

def make_image(event, path):
    image, map_kind = build_map(event)

    draw = ImageDraw.Draw(image)

    city = event["city"]

    banner, foreground, category = (
        magnitude_colors(event["mag"])
    )

    # Banner superior
    draw.rounded_rectangle(
        (22, 24, 1058, 268),
        radius=25,
        fill=banner,
        outline="#FFFFFF",
        width=4,
    )

    draw_centered(
        draw,
        "ALERTA",
        center_y=90,
        max_width=940,
        max_height=94,
        max_size=99,
        color=foreground,
    )

    draw.line(
        (83, 148, 997, 148),
        fill=foreground,
        width=3,
    )

    draw_centered(
        draw,
        "ÚLTIMO MINUTO",
        center_y=207,
        max_width=965,
        max_height=85,
        max_size=74,
        color=foreground,
    )

    # Banner inferior
    draw.rounded_rectangle(
        (18, 1000, 1062, 1328),
        radius=25,
        fill=banner,
        outline="#FFFFFF",
        width=4,
    )

    draw_centered(
        draw,
        (
            "SISMO DE MAGNITUD "
            f"{event['mag']:.1f}"
        ),
        center_y=1057,
        max_width=985,
        max_height=76,
        max_size=64,
        color=foreground,
    )

    # La percepcion no se infiere
    # exclusivamente de la distancia.
    if event.get("felt_confirmed", False):
        line_two = (
            "SE HA PERCIBIDO EN "
            + city[0].upper()
        )
    else:
        line_two = (
            "CERCA DE "
            + city[0].upper()
        )

    draw_centered(
        draw,
        line_two,
        center_y=1160,
        max_width=985,
        max_height=72,
        max_size=54,
        color=foreground,
    )

    draw_centered(
        draw,
        "¿LO SENTISTE?",
        center_y=1262,
        max_width=970,
        max_height=75,
        max_size=67,
        color=foreground,
    )

    attribution = (
        "Imagen: Esri / proveedores"
        if map_kind == "satellite"
        else "Mapa: Natural Earth"
    )

    selected = get_font(17)

    draw.rounded_rectangle(
        (15, 965, 410, 995),
        radius=5,
        fill="#102030",
    )

    draw.text(
        (24, 968),
        attribution,
        font=selected,
        fill="white",
    )

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    image.save(
        path,
        "JPEG",
        quality=95,
        optimize=True,
    )

    log(
        "IMAGEN CREADA:",
        path,
        "| BANNER:",
        category,
        "| MAPA:",
        map_kind,
    )


# ============================================================
# PIE DE PUBLICACION
# ============================================================

def make_caption(event):
    city = event["city"]

    local_time = event["time"].astimezone(
        ZoneInfo(city[5])
    )

    age_minutes = (
        now_utc() - event["time"]
    ).total_seconds() / 60

    # Diferenciar ocurrencia y percepcion.
    if event.get("felt_confirmed", False):
        description = (
            f"Un sismo de magnitud "
            f"{event['mag']:.1f} se ha percibido "
            f"en las cercanías de "
            f"{city[0]}, {city[1]}."
        )
    else:
        description = (
            f"Un sismo de magnitud "
            f"{event['mag']:.1f} se ha registrado "
            f"en las cercanías de "
            f"{city[0]}, {city[1]}."
        )

    if 0 <= age_minutes <= 30:
        description += " Hace instantes."

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

    caption = (
        f"🚨 ¡ÚLTIMO MINUTO! | "
        f"SISMO DE MAGNITUD "
        f"{event['mag']:.1f} EN "
        f"{city[0].upper()}, "
        f"{city[1].upper()} "
        f"{city[2]}\n\n"

        f"{description}\n\n"

        "💬 ¿Sentiste el movimiento? "
        "¡Coméntanos!\n\n"

        "📲 Sigue a @sismonauta para "
        "conocer los últimos registros "
        "sísmicos.\n\n"

        "#SISMONAUTA #UltimoMinuto "
        "#Sismo #Terremoto "
        f"#{country_tag} "
        f"#{city_tag} "
        "#EMSC #Sismologia"
    )

    return caption[:2200]


# ============================================================
# GITHUB CONTENTS API
# ============================================================

class Github:
    def __init__(self):
        self.repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = os.getenv(
            "GITHUB_REF_NAME",
            "main",
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

        if response.json().get("private"):
            raise RuntimeError(
                "Se necesita un repositorio "
                "publico para servir imagenes "
                "a Meta"
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

        raw = base64.b64decode(
            data["content"]
        )

        return json.loads(
            raw.decode("utf-8")
        )

    def write_bytes(self, path, content):
        endpoint = self.endpoint(path)

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
                sha = previous.json()["sha"]

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
                response.status_code in (409, 422)
                and attempt < 4
            ):
                time.sleep(2 + attempt)
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

    def public_url(self, path):
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
            "Meta no devolvio JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = data.get("error") or {}

        raise RuntimeError(
            "Meta API: "
            + str(
                error.get("message", data)
            )[:250]
        )

    return data


# ============================================================
# VALIDAR CUENTAS META
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
                "username", ""
            )
        ).lower() != "sismonauta"
    ):
        raise RuntimeError(
            "La cuenta Instagram no es "
            "@sismonauta"
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
            facebook.get("name", "")
        ).lower()
    ):
        raise RuntimeError(
            "La fanpage no es SISMONAUTA"
        )

    log(
        "INSTAGRAM:",
        instagram.get("username"),
    )

    log(
        "FACEBOOK:",
        facebook.get("name"),
    )

    return (
        ig_id,
        ig_token,
        fb_id,
        fb_token,
    )


# ============================================================
# PUBLICACION INSTAGRAM
# ============================================================

def publish_instagram(
    user_id,
    token,
    image_url,
    caption,
):
    container = graph(
        "POST",
        f"{user_id}/media",
        token,
        {
            "image_url": image_url,
            "caption": caption,
        },
    )

    container_id = container.get("id")

    if not container_id:
        raise RuntimeError(
            "Instagram no creo contenedor"
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
                "Contenedor Instagram: "
                + str(status)
            )

    if not finished:
        raise RuntimeError(
            "Instagram no termino "
            "de procesar la imagen"
        )

    published = graph(
        "POST",
        f"{user_id}/media_publish",
        token,
        {
            "creation_id": container_id
        },
    )

    identifier = published.get("id")

    if not identifier:
        raise RuntimeError(
            "Instagram no confirmo el post"
        )

    return str(identifier)


# ============================================================
# PUBLICACION FACEBOOK
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
            "Facebook no confirmo la foto"
        )

    return str(identifier)


# ============================================================
# VERIFICACION IMAGEN PUBLICA
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
        "Imagen publica no disponible"
    )


# ============================================================
# HISTORIAL Y DEDUPLICACION
# ============================================================

def new_state():
    return {
        "events": {}
    }


def ensure_entry(state, event):
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

    targets = entry.setdefault(
        "targets",
        {},
    )

    for platform in PLATFORMS:
        targets.setdefault(
            platform,
            {
                "status": "pending"
            },
        )

    return entry


def is_finished(entry):
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


def has_uncertain(entry):
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

    if is_finished(entry):
        log(
            "YA PUBLICADO:",
            event_id,
        )
        return "already_published"

    if has_uncertain(entry):
        log(
            "REVISION MANUAL:",
            event_id,
        )
        return "partial_or_uncertain"

    filename = (
        safe_name(event_id)
        + ".jpg"
    )

    image_path = OUT / filename

    make_image(
        event,
        image_path,
    )

    caption = make_caption(event)

    save_json(
        OUT / (
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

    remote_path = (
        IMAGE_DIR
        + "/"
        + filename
    )

    github.write_json(
        STATE_PATH,
        state,
    )

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

    entry["image_url"] = image_url

    github.write_json(
        STATE_PATH,
        state,
    )

    ig_id, ig_token, fb_id, fb_token = (
        credentials
    )

    for platform in PLATFORMS:
        target = entry[
            "targets"
        ][platform]

        if target.get("status") == "published":
            continue

        # Bloquear duplicaciones ante un
        # resultado ambiguo de Meta.
        target["status"] = "uncertain"
        target.pop("error", None)

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
                "ERROR META:",
                platform,
                short_error(exc),
            )

    if is_finished(entry):
        return "published"

    return "partial_or_uncertain"


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA EMSC - 10 MINUTOS"
        )
    )

    parser.add_argument(
        "--publish",
        action="store_true",
    )

    parser.add_argument(
        "--out",
        default="output_ciudades",
    )

    parser.add_argument(
        "--lookback-minutes",
        type=int,
        choices=[10],
        default=10,
    )

    args = parser.parse_args()

    global OUT
    OUT = Path(args.out)

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
        "checked_at": iso(now_utc()),
        "publish_requested": args.publish,
        "lookback_hours": 0,
        "lookback_minutes": 10,
        "radius_km": RADIUS_KM,
        "min_magnitude": MIN_MAG,
        "max_magnitude_exclusive": MAX_MAG,
        "events": [],
    }

    try:
        log("=" * 52)
        log("SISMONAUTA - EMSC-CSEM")
        log("CIUDADES:", len(CITIES))
        log("VENTANA: 10 MINUTOS")
        log("RADIO:", RADIUS_KM, "KM")
        log("MAGNITUDES: M4.0 A M6.9")
        log("PUBLICAR:", args.publish)
        log("=" * 52)

        github = (
            Github()
            if args.publish
            else None
        )

        state = (
            github.read(STATE_PATH)
            if github
            else None
        ) or new_state()

        state.setdefault(
            "events",
            {},
        )

        events = fetch_events()

        pending = [
            event
            for event in events
            if not is_finished(
                state["events"].get(
                    event["id"],
                    {},
                )
            )
        ]

        log(
            "EVENTOS COINCIDENTES:",
            len(events),
        )

        log(
            "EVENTOS PENDIENTES:",
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

        pending = pending[
            :MAX_EVENTS_PER_RUN
        ]

        credentials = None

        if args.publish:
            credentials = validate_accounts()

        statuses = []

        for event in pending:
            log(
                "PROCESANDO:",
                event["id"],
                f"M{event['mag']:.1f}",
                event["city"][0],
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

            entry = ensure_entry(
                state,
                event,
            )

            statuses.append(result)

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
            value == "preview_only"
            for value in statuses
        ):
            receipt["status"] = (
                "preview_only"
            )

        elif any(
            value == "partial_or_uncertain"
            for value in statuses
        ):
            receipt["status"] = (
                "partial_or_uncertain"
            )

        elif all(
            value in (
                "published",
                "already_published",
            )
            for value in statuses
        ):
            receipt["status"] = (
                "published"
            )

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
