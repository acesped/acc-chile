
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA | ALERTAS SISMICAS POR CIUDAD

FUENTE
    EMSC-CSEM
    https://www.seismicportal.eu/

MONITOREO
    15 ciudades de America
    Radio: 100 km
    Magnitudes: 4.0 <= M < 7.0
    Ventana: ultimas 3 horas (180 minutos)

PUBLICACIONES
    Instagram @sismonauta
    Fanpage Facebook SISMONAUTA

IMAGENES
    1080 x 1350 JPG
    M4.0 - 4.9: verde
    M5.0 - 5.9: amarillo
    M6.0 - 6.9: naranjo
    Epicentro marcado en rojo

CONTROL
    Deduplicacion por ID de EMSC
    Historial persistente en GitHub
    Estado separado por plataforma
    Diagnosticos temporales y geograficos

No consulta boletines de tsunami.
No constituye alerta oficial.

Dependencias:
    pip install requests Pillow matplotlib cartopy

Ejecucion:
    python sismonauta_ciudades.py
    python sismonauta_ciudades.py --publish
    python sismonauta_ciudades.py --lookback-hours 3
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
# CONFIGURACION
# ============================================================

APP = "SISMONAUTA CIUDADES EMSC"

UTC = timezone.utc

EMSC_API = (
    "https://www.seismicportal.eu/"
    "fdsnws/event/1/query"
)

EMSC_SITE = (
    "https://www.emsc-csem.org/"
    "Earthquake_information/"
)

MIN_MAG = 4.0

MAX_MAG_EXCLUSIVE = 7.0

RADIUS_KM = 100.0

# NUEVA VENTANA DE CONSULTA
LOOKBACK_HOURS = 3
LOOKBACK_MINUTES = 180

MAX_EVENTS_PER_RUN = 30

WIDTH = 1080
HEIGHT = 1350

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

PLATFORMS = (
    "instagram",
    "facebook",
)

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": (
        "Sismonauta-Ciudades-EMSC/3.0"
    ),
    "Accept": "application/json",
})


# ============================================================
# CIUDADES MONITOREADAS
# ============================================================

# nombre, pais, bandera, lat, lon, timezone

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


def safe_name(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        str(value),
    )[:100]


def short_error(exc):
    text = str(exc)

    for name in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
    ):
        secret = os.getenv(name, "")

        if secret:
            text = text.replace(
                secret,
                "***",
            )

    return text[:350]


def parse_time(value):
    if not value:
        raise ValueError(
            "Hora del evento no disponible"
        )

    text = str(value).strip()

    result = datetime.fromisoformat(
        text.replace("Z", "+00:00")
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
# DISTANCIA GEOGRAFICA
# ============================================================

def haversine(lat1, lon1, lat2, lon2):
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
        for distance, city
        in nearest_cities(lat, lon)
        if distance <= RADIUS_KM
    ]


# ============================================================
# CONSULTAR EMSC: ULTIMAS TRES HORAS
# ============================================================

def fetch_events():

    # Calculo directo del intervalo.
    end = now_utc()

    start = end - timedelta(
        hours=LOOKBACK_HOURS
    )

    duration_seconds = (
        end - start
    ).total_seconds()

    # Garantizar 180 minutos.
    if duration_seconds != 10800:
        raise RuntimeError(
            "La ventana temporal no "
            "corresponde a 3 horas"
        )

    log(
        "================================"
    )
    log(
        "DIAGNOSTICO TEMPORAL EMSC"
    )
    log(
        "INICIO UTC:",
        iso(start),
    )
    log(
        "FIN UTC:",
        iso(end),
    )
    log(
        "DURACION REAL:",
        duration_seconds / 60,
        "minutos",
    )
    log(
        "================================"
    )

    # Sin forzar catalog=EMSC-RTS:
    # consultar el catalogo disponible
    # en el servicio EMSC.
    #
    # Se solicita M >= 4 y luego
    # se aplica el limite M < 7
    # en el propio programa.
    params = {
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

    log(
        "PARAMETROS EMSC:",
        json.dumps(
            params,
            ensure_ascii=False,
        ),
    )

    response = HTTP.get(
        EMSC_API,
        params=params,
        timeout=75,
    )

    log(
        "URL CONSULTADA:",
        response.url,
    )

    log(
        "HTTP STATUS:",
        response.status_code,
    )

    if response.status_code == 204:
        log(
            "No se encontraron eventos "
            "en las ultimas tres horas"
        )

        return []

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise RuntimeError(
            "Respuesta EMSC inesperada"
        )

    if data.get("type") == "Feature":
        features = [data]
    else:
        features = data.get(
            "features",
            [],
        )

    if not isinstance(features, list):
        raise RuntimeError(
            "EMSC no entregó una lista"
        )

    if len(features) >= 20000:
        raise RuntimeError(
            "EMSC alcanzó el límite "
            "de resultados de la consulta"
        )

    log(
        "EVENTOS RECIBIDOS:",
        len(features),
    )

    events = []
    seen = set()

    diagnostics = {
        "received": len(features),
        "invalid": 0,
        "outside_magnitude": 0,
        "outside_time": 0,
        "outside_radius": 0,
        "duplicates": 0,
        "accepted": 0,
    }

    # ========================================================
    # ANALIZAR CADA EVENTO
    # ========================================================

    for number, feature in enumerate(
        features,
        start=1,
    ):

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

            lat_value = properties.get(
                "lat"
            )

            lon_value = properties.get(
                "lon"
            )

            if lat_value is None:
                lat_value = coordinates[1]

            if lon_value is None:
                lon_value = coordinates[0]

            lat = float(lat_value)
            lon = float(lon_value)

            event_time = parse_time(
                properties["time"]
            )

            event_id = str(
                properties.get("unid")
                or feature.get("id")
                or properties.get("source_id")
                or ""
            ).strip()

            if not event_id:
                raise ValueError(
                    "Evento sin ID"
                )

            unique_id = (
                "emsc_" + event_id
            )

            depth = None

            if (
                properties.get("depth")
                is not None
            ):
                candidate = float(
                    properties["depth"]
                )

                if math.isfinite(candidate):
                    depth = candidate

            place = str(
                properties.get(
                    "flynn_region"
                )
                or "Región no especificada"
            )

            if not all(
                math.isfinite(v)
                for v in (
                    magnitude,
                    lat,
                    lon,
                )
            ):
                raise ValueError(
                    "Valor numerico no finito"
                )

            if not (
                -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                raise ValueError(
                    "Coordenadas invalidas"
                )

        except (
            KeyError,
            TypeError,
            ValueError,
            IndexError,
            AttributeError,
        ) as exc:

            diagnostics["invalid"] += 1

            log(
                "EVENTO INVALIDO:",
                number,
                short_error(exc),
            )

            continue

        # ====================================================
        # DIAGNOSTICOS
        # ====================================================

        age_minutes = (
            end - event_time
        ).total_seconds() / 60

        within_time = (
            start <= event_time <= end
        )

        within_magnitude = (
            MIN_MAG <= magnitude
            < MAX_MAG_EXCLUSIVE
        )

        nearest = nearest_cities(
            lat,
            lon,
        )

        distance, city = nearest[0]

        within_radius = (
            distance <= RADIUS_KM
        )

        log(
            "--------------------------------"
        )
        log(
            "EVENTO EMSC:",
            event_id,
        )
        log(
            "MAGNITUD:",
            magnitude,
        )
        log(
            "HORA UTC:",
            iso(event_time),
        )
        log(
            "ANTIGUEDAD:",
            round(age_minutes, 2),
            "minutos",
        )
        log(
            "COORDENADAS:",
            lat,
            lon,
        )
        log(
            "REGION:",
            place,
        )
        log(
            "CIUDAD MAS CERCANA:",
            city[0],
            city[1],
        )
        log(
            "DISTANCIA:",
            round(distance, 2),
            "km",
        )
        log(
            "DENTRO DE 180 MINUTOS:",
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

        # ====================================================
        # FILTROS
        # ====================================================

        if not within_magnitude:

            diagnostics[
                "outside_magnitude"
            ] += 1

            log(
                "DESCARTADO: magnitud"
            )

            continue

        if not within_time:

            diagnostics[
                "outside_time"
            ] += 1

            log(
                "DESCARTADO: fuera de "
                "las ultimas 3 horas"
            )

            continue

        if not within_radius:

            diagnostics[
                "outside_radius"
            ] += 1

            log(
                "DESCARTADO: distancia "
                "superior a 100 km"
            )

            continue

        if unique_id in seen:

            diagnostics[
                "duplicates"
            ] += 1

            log(
                "DESCARTADO: ID duplicado"
            )

            continue

        seen.add(unique_id)

        matches = matching_cities(
            lat,
            lon,
        )

        distance, city = matches[0]

        event_url = (
            EMSC_API
            + "?"
            + urllib.parse.urlencode({
                "format": "json",
                "eventid": event_id,
            })
        )

        events.append({
            "id": unique_id,
            "emsc_id": event_id,
            "source": "EMSC-CSEM",
            "mag": magnitude,
            "magtype": str(
                properties.get(
                    "magtype"
                )
                or ""
            ),
            "lat": lat,
            "lon": lon,
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
                    "distance_km": round(
                        dist,
                        1,
                    ),
                }
                for dist, item in matches
            ],
        })

        diagnostics["accepted"] += 1

        log(
            "ACEPTADO:",
            unique_id,
        )

    events.sort(
        key=lambda item: item["time"]
    )

    log(
        "================================"
    )
    log(
        "RESUMEN EMSC - 3 HORAS"
    )
    log(
        json.dumps(
            diagnostics,
            ensure_ascii=False,
            indent=2,
        )
    )
    log(
        "================================"
    )

    return events


# ============================================================
# COLORES POR MAGNITUD
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
            "#101010",
            "AMARILLO",
        )

    if 6.0 <= magnitude < 7.0:
        return (
            "#FA8615",
            "#101010",
            "NARANJO",
        )

    raise ValueError(
        "Magnitud fuera de rango"
    )


# ============================================================
# TIPOGRAFIA
# ============================================================

def font(size):
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


def fitted_font(
    draw,
    text,
    max_width,
    max_height,
    initial=90,
):
    for size in range(
        initial,
        19,
        -2,
    ):
        selected = font(size)

        box = draw.textbbox(
            (0, 0),
            text,
            font=selected,
        )

        width = box[2] - box[0]
        height = box[3] - box[1]

        if (
            width <= max_width
            and height <= max_height
        ):
            return selected

    return font(20)


def centered_text(
    draw,
    text,
    y,
    color,
    initial,
    width=980,
    height=100,
):
    selected = fitted_font(
        draw,
        text,
        width,
        height,
        initial,
    )

    box = draw.textbbox(
        (0, 0),
        text,
        font=selected,
    )

    text_width = box[2] - box[0]

    x = (
        (WIDTH - text_width) / 2
        - box[0]
    )

    draw.text(
        (x, y),
        text,
        font=selected,
        fill=color,
    )


# ============================================================
# MAPA DEL EPICENTRO
# ============================================================

def build_map(event):

    import matplotlib
    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    import cartopy.crs as ccrs
    import cartopy.feature as cf

    lat = event["lat"]
    lon = event["lon"]
    city = event["city"]

    projection = ccrs.PlateCarree(
        central_longitude=lon
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

    lat_span = 3.0

    lon_span = (
        3.0
        / max(
            0.40,
            math.cos(
                math.radians(lat)
            )
        )
    )

    ax.set_extent(
        [
            lon - lon_span,
            lon + lon_span,
            lat - lat_span,
            lat + lat_span,
        ],
        crs=ccrs.PlateCarree(),
    )

    ax.set_facecolor(
        "#073D67"
    )

    ax.add_feature(
        cf.OCEAN.with_scale("110m"),
        facecolor="#06456D",
        zorder=0,
    )

    ax.add_feature(
        cf.LAND.with_scale("110m"),
        facecolor="#50764D",
        zorder=1,
    )

    ax.add_feature(
        cf.COASTLINE.with_scale("110m"),
        edgecolor="#E4E9DD",
        linewidth=1.4,
        zorder=2,
    )

    ax.add_feature(
        cf.BORDERS.with_scale("110m"),
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

    # Anillos ilustrativos
    for size in (
        2600,
        1300,
        450,
    ):
        ax.scatter(
            [lon],
            [lat],
            s=size,
            facecolors="none",
            edgecolors="#FA343A",
            linewidths=2.4,
            transform=ccrs.PlateCarree(),
            zorder=7,
        )

    # Epicentro real
    ax.scatter(
        [lon],
        [lat],
        s=115,
        c="#FF142C",
        edgecolors="white",
        linewidths=1.8,
        transform=ccrs.PlateCarree(),
        zorder=9,
    )

    # Ciudad monitoreada
    ax.scatter(
        [city[4]],
        [city[3]],
        s=55,
        c="white",
        edgecolors="black",
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

    with Image.open(buffer) as source:
        image = ImageOps.fit(
            source.convert("RGB"),
            (WIDTH, HEIGHT),
            method=Image.Resampling.LANCZOS,
        )

    return image


# ============================================================
# CREAR IMAGEN
# ============================================================

def make_image(event, path):

    image = build_map(event)
    draw = ImageDraw.Draw(image)

    city = event["city"]

    banner, foreground, category = (
        magnitude_colors(
            event["mag"]
        )
    )

    # Banner superior
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
        initial=110,
        height=115,
    )

    # Banner inferior
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
        initial=74,
        height=90,
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
        initial=54,
        height=90,
    )

    # No agregar "Epicentro cercano a..."

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    image.save(
        path,
        "JPEG",
        quality=93,
        optimize=True,
    )

    log(
        "IMAGEN CREADA:",
        path,
        "| BANNER:",
        category,
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

    # Evitar presentar un sismo antiguo
    # como si acabara de ocurrir.
    if 0 <= age_seconds <= 1800:
        opening = (
            "Hace unos instantes, "
            "se ha registrado"
        )
        prefix = "🚨 ÚLTIMO MINUTO"
    else:
        opening = "Se registró"
        prefix = "📡 SISMO REGISTRADO"

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
        f"{prefix} | SISMO DE MAGNITUD "
        f"{event['mag']:.1f} CERCA DE "
        f"{city[0].upper()}, "
        f"{city[1].upper()} "
        f"{city[2]}\n\n"

        f"{opening} un sismo de magnitud "
        f"{event['mag']:.1f} "
        f"en las cercanías de "
        f"{city[0]}, {city[1]}.\n\n"

        f"📍 Epicentro: {event['place']}\n"

        f"📏 Distancia a {city[0]}: "
        f"{event['distance_km']:.1f} km\n"

        f"📊 Magnitud: {event['mag']:.1f}\n"

        f"⬇️ Profundidad: "
        f"{depth_text(event['depth'])}\n"

        f"🕒 Fecha y hora local: "
        f"{local_time:%d/%m/%Y – %H:%M}\n"

        f"🌐 Coordenadas: "
        f"{event['lat']:.3f}, "
        f"{event['lon']:.3f}\n\n"

        "💬 ¿Sentiste el movimiento? "
        "Comenta desde qué ciudad y qué "
        "intensidad percibiste.\n\n"

        "📲 Sigue a @sismonauta para "
        "conocer los últimos registros "
        "sísmicos.\n\n"

        "Datos sísmicos: EMSC-CSEM\n"
        f"{EMSC_SITE}\n\n"

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

        self.s = requests.Session()

        self.s.headers.update({
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

        response = self.s.get(
            self.base,
            timeout=30,
        )

        response.raise_for_status()

        if response.json().get("private"):
            raise RuntimeError(
                "El repositorio debe ser "
                "público para servir imágenes"
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
        response = self.s.get(
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

        return json.loads(
            base64.b64decode(
                data["content"]
            ).decode("utf-8")
        )

    def write_bytes(self, path, content):
        endpoint = self.endpoint(path)

        for attempt in range(5):
            previous = self.s.get(
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

            response = self.s.put(
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

    values = dict(parameters or {})

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
            data.get("error") or {}
        )

        raise RuntimeError(
            "Meta API: "
            + str(
                error.get("message", data)
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
                "username", ""
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
            facebook.get("name", "")
        ).lower()
    ):
        raise RuntimeError(
            "Fanpage Facebook incorrecta"
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

    result = graph(
        "POST",
        f"{user_id}/media",
        token,
        {
            "image_url": image_url,
            "caption": caption,
        },
    )

    container_id = result.get("id")

    if not container_id:
        raise RuntimeError(
            "Instagram no creó contenedor"
        )

    finished = False

    for attempt in range(25):
        time.sleep(5)

        status_data = graph(
            "GET",
            container_id,
            token,
            {
                "fields": "status_code"
            },
        )

        status = status_data.get(
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
                "Instagram: "
                + str(status)
            )

    if not finished:
        raise RuntimeError(
            "Instagram no terminó "
            "de procesar el contenedor"
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
            "Instagram no confirmó publicación"
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
            "Facebook no confirmó publicación"
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
        "Imagen no accesible públicamente"
    )


# ============================================================
# HISTORIAL PERSISTENTE
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
            "REQUIERE REVISION MANUAL:",
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

    caption = make_caption(
        event
    )

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

        if (
            target.get("status")
            == "published"
        ):
            continue

        # Persistir un estado de seguridad
        # antes de enviar la solicitud a Meta.
        target["status"] = (
            "uncertain"
        )

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

            target["status"] = (
                "published"
            )

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
                "ERROR:",
                event_id,
                platform,
                short_error(exc),
            )

    if is_finished(entry):
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
    )

    parser.add_argument(
        "--out",
        default="output_ciudades",
    )

    # La ventana se fija en 3 horas.
    parser.add_argument(
        "--lookback-hours",
        type=int,
        choices=[3],
        default=3,
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
        "checked_at": iso(
            now_utc()
        ),
        "publish_requested": (
            args.publish
        ),
        "lookback_hours": 3,
        "lookback_minutes": 180,
        "radius_km": RADIUS_KM,
        "min_magnitude": MIN_MAG,
        "max_magnitude_exclusive": (
            MAX_MAG_EXCLUSIVE
        ),
        "events": [],
    }

    try:

        log(
            "================================"
        )
        log(
            "SISMONAUTA - EMSC"
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
            MAX_MAG_EXCLUSIVE,
        )
        log(
            "Ventana:",
            LOOKBACK_MINUTES,
            "minutos",
        )
        log(
            "Publicar:",
            args.publish,
        )
        log(
            "================================"
        )

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

        # Siempre consulta ultimas 3 horas.
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
            "Eventos coincidentes:",
            len(events),
        )

        log(
            "Pendientes:",
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
                "Sin nuevos eventos"
            )

            return 0

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
                "SISMO:",
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

                result = (
                    "partial_or_uncertain"
                )

                log(
                    "ERROR DE EVENTO:",
                    event["id"],
                    short_error(exc),
                )

            entry = ensure_entry(
                state,
                event,
            )

            statuses.append(
                result
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

        receipt["status"] = (
            "error"
        )

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
