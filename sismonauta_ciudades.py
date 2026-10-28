
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA - ALERTAS SISMICAS POR CIUDAD
Fuente exclusiva: EMSC-CSEM

- 15 ciudades
- Radio horizontal de 100 km desde cada centro urbano
- Magnitudes M4.0 hasta menos de M7.0
- Eventos ocurridos en los ultimos 60 minutos
- Imagen JPG 1080 x 1350
- Banners verdes, amarillos o naranjos
- Publicacion Instagram y Facebook
- Historial persistente mediante GitHub Contents API
- Sin consulta de tsunami

Uso:
    python sismonauta_ciudades.py
    python sismonauta_ciudades.py --publish

La informacion es preliminar y puede cambiar.
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
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# CONFIGURACION GENERAL
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

# Regla obligatoria:
# buscar solo terremotos de la ultima hora.
LOOKBACK_HOURS = 1

WIDTH = 1080
HEIGHT = 1350

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

PLATFORMS = (
    "instagram",
    "facebook",
)

HTTP = requests.Session()

HTTP.headers.update({
    "User-Agent": (
        "Sismonauta-Ciudades-EMSC/1.0"
    ),
    "Accept": "application/json",
})


# ============================================================
# CIUDADES
# ============================================================

# nombre, pais, bandera, latitud,
# longitud, zona horaria local

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
    message = str(exc)

    for name in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN",
    ):
        secret = os.getenv(name, "")

        if secret:
            message = message.replace(
                secret,
                "***",
            )

    return message[:300]


def parse_time(value):
    if not value:
        raise ValueError(
            "Fecha EMSC no disponible"
        )

    result = datetime.fromisoformat(
        str(value).replace(
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


def valid_number(value):
    return math.isfinite(
        float(value)
    )


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


def matching_cities(lat, lon):
    matches = []

    for city in CITIES:
        distance = haversine(
            lat, lon,
            city[3], city[4],
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
# CONSULTAR EMSC: ULTIMOS 60 MINUTOS
# ============================================================

def fetch_events():
    end = now_utc()

    start = end - timedelta(
        hours=LOOKBACK_HOURS
    )

    params = {
        "format": "json",
        "starttime": start.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "endtime": end.strftime(
            "%Y-%m-%dT%H:%M:%S"
        ),
        "minmagnitude": MIN_MAG,
        "maxmagnitude": 6.999,
        "catalog": "EMSC-RTS",
        "orderby": "time-asc",
        "limit": 20000,
        "nodata": 204,
    }

    log(
        "EMSC: desde",
        iso(start),
        "hasta",
        iso(end),
    )

    response = HTTP.get(
        EMSC_API,
        params=params,
        timeout=65,
    )

    if response.status_code == 204:
        return []

    response.raise_for_status()

    data = response.json()

    if data.get("type") == "Feature":
        features = [data]
    else:
        features = data.get(
            "features", []
        )

    if not isinstance(features, list):
        raise RuntimeError(
            "Respuesta EMSC inesperada"
        )

    if len(features) >= 20000:
        raise RuntimeError(
            "Consulta EMSC posiblemente truncada"
        )

    events = []
    seen = set()

    for feature in features:
        try:
            properties = (
                feature.get("properties") or {}
            )

            geometry = (
                feature.get("geometry") or {}
            )

            coordinates = (
                geometry.get("coordinates") or []
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

            if not all(
                valid_number(v)
                for v in (
                    magnitude, lat, lon
                )
            ):
                continue

            if not (
                MIN_MAG
                <= magnitude
                < MAX_MAG_EXCLUSIVE
            ):
                continue

            if not (
                -90 <= lat <= 90
                and -180 <= lon <= 180
            ):
                continue

            event_time = parse_time(
                properties["time"]
            )

            # Exclusion estricta de sismos
            # ocurridos hace mas de 60 minutos.
            if not (
                start <= event_time <= end
            ):
                continue

            event_id = str(
                properties.get("unid")
                or feature.get("id")
                or ""
            ).strip()

            if not event_id:
                continue

            unique_id = (
                "emsc_" + event_id
            )

            if unique_id in seen:
                continue

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

            matches = matching_cities(
                lat, lon
            )

            if not matches:
                continue

            distance, city = matches[0]

            place = str(
                properties.get(
                    "flynn_region"
                )
                or "Región no especificada"
            )

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
                    ) or ""
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
                        "name": item[0],
                        "country": item[1],
                        "distance_km": round(
                            dist, 1
                        ),
                    }
                    for dist, item in matches
                ],
            })

            seen.add(unique_id)

        except (
            KeyError,
            ValueError,
            TypeError,
            IndexError,
        ) as exc:
            log(
                "Registro EMSC omitido:",
                short_error(exc),
            )

    events.sort(
        key=lambda item: item["time"]
    )

    log(
        "Eventos recibidos:",
        len(features),
    )

    log(
        "Eventos en zonas monitoreadas:",
        len(events),
    )

    return events


# ============================================================
# COLORES DE BANNERS
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
# FUENTES Y TEXTO
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
                path, size
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
        initial, 19, -2
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
        draw, text,
        width, height,
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

    # Anillos graficos ilustrativos.
    for size in (
        2600, 1300, 450
    ):
        ax.scatter(
            [lon], [lat],
            s=size,
            facecolors="none",
            edgecolors="#FA343A",
            linewidths=2.4,
            transform=ccrs.PlateCarree(),
            zorder=7,
        )

    # Epicentro EMSC.
    ax.scatter(
        [lon], [lat],
        s=115,
        c="#FF142C",
        edgecolors="white",
        linewidths=1.8,
        transform=ccrs.PlateCarree(),
        zorder=9,
    )

    # Ciudad seleccionada.
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
        initial=110,
        height=115,
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

    # NO agregar "Epicentro cercano a...".

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
        "Imagen creada:",
        path,
        "| banner:",
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
                "público para servir imágenes."
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

        raw = base64.b64decode(
            data["content"]
        )

        return json.loads(
            raw.decode("utf-8")
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
                response.status_code
                in (409, 422)
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
            instagram.get("username", "")
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
            "ERROR", "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram: " + status
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
                    "Content-Type", ""
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
        "Imagen aún no accesible "
        "públicamente"
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
        "targets", {}
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
        "targets", {}
    )

    return all(
        targets.get(
            platform, {}
        ).get("status") == "published"
        for platform in PLATFORMS
    )


def has_uncertain(entry):
    targets = entry.get(
        "targets", {}
    )

    return any(
        targets.get(
            platform, {}
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
        state, event
    )

    if is_finished(entry):
        log(
            "YA PUBLICADO:",
            event_id,
        )
        return "already_published"

    # No volver a publicar si una llamada
    # anterior pudo haberse completado.
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
        IMAGE_DIR + "/" + filename
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

    wait_public_image(image_url)

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

        # Guardar antes de llamar a Meta.
        target["status"] = "uncertain"

        target.pop(
            "error", None
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
                "ERROR:",
                event_id,
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

    # Se mantiene por compatibilidad
    # con GitHub Actions, pero es fijo.
    parser.add_argument(
        "--lookback-hours",
        type=int,
        choices=[1],
        default=1,
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
        "source": "EMSC-CSEM",
        "checked_at": iso(now_utc()),
        "publish_requested": args.publish,
        "lookback_hours": 1,
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
            "Ciudades:", len(CITIES)
        )
        log(
            "Radio:", RADIUS_KM, "km"
        )
        log(
            "Magnitudes: 4.0 a < 7.0"
        )
        log(
            "Ventana: ultimos 60 minutos"
        )
        log(
            "Publicar:", args.publish
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
            "events", {}
        )

        # Siempre consulta SOLO la ultima hora.
        events = fetch_events()

        pending = [
            event
            for event in events
            if not is_finished(
                state["events"].get(
                    event["id"], {}
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
                    event["distance_km"], 1
                ),
                "status": result,
                "targets": entry["targets"],
            })

            save_json(
                receipt_path,
                receipt,
            )

        if all(
            s == "preview_only"
            for s in statuses
        ):
            receipt["status"] = (
                "preview_only"
            )

        elif any(
            s == "partial_or_uncertain"
            for s in statuses
        ):
            receipt["status"] = (
                "partial_or_uncertain"
            )

        elif all(
            s in (
                "published",
                "already_published",
            )
            for s in statuses
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
