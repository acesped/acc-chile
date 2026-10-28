
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA | ALERTAS SISMICAS POR CIUDAD

Sismos M4.0 a M6.9 dentro de 100 km de 15 ciudades.

Instagram: @sismonauta
Facebook: Fanpage SISMONAUTA

No consulta alertas de tsunami.
No constituye un servicio oficial de alertas sísmicas.

Uso:
  python sismonauta_ciudades.py
  python sismonauta_ciudades.py --publish
"""

from __future__ import annotations

import argparse
import base64
import html
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
# CONFIGURACION
# ============================================================

UTC = timezone.utc

API = (
    "https://earthquake.usgs.gov/"
    "fdsnws/event/1/query"
)

GRAPH_VER = os.getenv(
    "META_GRAPH_VERSION", "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = "https://graph.facebook.com"

STATE = "sismonauta_ciudades/state.json"

IMAGE_DIR = "sismonauta_ciudades/images"

RADIUS = 100.0

MIN_MAG = 4.0
MAX_MAG = 7.0

CITIES = [
    (
        "Santiago", "Chile", "🇨🇱",
        -33.4489, -70.6693,
        "America/Santiago"
    ),
    (
        "Coquimbo", "Chile", "🇨🇱",
        -29.9533, -71.3395,
        "America/Santiago"
    ),
    (
        "Concepción", "Chile", "🇨🇱",
        -36.8201, -73.0444,
        "America/Santiago"
    ),
    (
        "Iquique", "Chile", "🇨🇱",
        -20.2307, -70.1357,
        "America/Santiago"
    ),
    (
        "Lima", "Perú", "🇵🇪",
        -12.0464, -77.0428,
        "America/Lima"
    ),
    (
        "Arequipa", "Perú", "🇵🇪",
        -16.4090, -71.5375,
        "America/Lima"
    ),
    (
        "Ciudad de México", "México", "🇲🇽",
        19.4326, -99.1332,
        "America/Mexico_City"
    ),
    (
        "Guadalajara", "México", "🇲🇽",
        20.6597, -103.3496,
        "America/Mexico_City"
    ),
    (
        "Cali", "Colombia", "🇨🇴",
        3.4516, -76.5320,
        "America/Bogota"
    ),
    (
        "Bogotá", "Colombia", "🇨🇴",
        4.7110, -74.0721,
        "America/Bogota"
    ),
    (
        "Caracas", "Venezuela", "🇻🇪",
        10.4806, -66.9036,
        "America/Caracas"
    ),
    (
        "Maracaibo", "Venezuela", "🇻🇪",
        10.6427, -71.6125,
        "America/Caracas"
    ),
    (
        "Quito", "Ecuador", "🇪🇨",
        -0.1807, -78.4678,
        "America/Guayaquil"
    ),
    (
        "La Habana", "Cuba", "🇨🇺",
        23.1136, -82.3666,
        "America/Havana"
    ),
    (
        "Los Ángeles", "Estados Unidos", "🇺🇸",
        34.0522, -118.2437,
        "America/Los_Angeles"
    ),
]

S = requests.Session()

S.headers.update({
    "User-Agent": "Sismonauta-Ciudades/1.0",
    "Accept": "application/json",
})


# ============================================================
# UTILIDADES
# ============================================================

def log(*args):
    print(*args, flush=True)


def now():
    return datetime.now(UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat()


def dump(path, obj):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    path.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            default=str
        ),
        encoding="utf-8"
    )


def safe(s):
    return re.sub(
        r"[^a-zA-Z0-9_-]",
        "_",
        str(s)
    )[:90]


def error_text(e):
    text = str(e)

    for key in (
        "GH_TOKEN",
        "INSTAGRAM_TOKEN",
        "FACEBOOK_PAGE_TOKEN"
    ):
        secret = os.getenv(key)

        if secret:
            text = text.replace(secret, "***")

    return text[:250]


# ============================================================
# DISTANCIA GEOGRAFICA
# ============================================================

def haversine(a, b, c, d):
    a, b, c, d = map(
        math.radians,
        (a, b, c, d)
    )

    x = (
        math.sin((c - a) / 2) ** 2
        + math.cos(a)
        * math.cos(c)
        * math.sin((d - b) / 2) ** 2
    )

    return (
        6371.0088
        * 2
        * math.asin(
            min(1, math.sqrt(x))
        )
    )


def matching_cities(lat, lon):
    matches = []

    for city in CITIES:
        distance = haversine(
            lat,
            lon,
            city[3],
            city[4]
        )

        if distance <= RADIUS:
            matches.append(
                (distance, city)
            )

    return sorted(
        matches,
        key=lambda item: item[0]
    )


# ============================================================
# CONSULTAR USGS
# ============================================================

def fetch_events(hours):
    end = now()

    start = end - timedelta(
        hours=hours
    )

    response = S.get(
        API,
        params={
            "format": "geojson",
            "starttime": start.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "endtime": end.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "minmagnitude": 4,
            "maxmagnitude": 6.999,
            "eventtype": "earthquake",
            "orderby": "time-asc",
            "limit": 20000
        },
        timeout=65
    )

    response.raise_for_status()

    result = []

    for feature in response.json().get(
        "features", []
    ):
        properties = (
            feature.get("properties") or {}
        )

        coordinates = (
            (feature.get("geometry") or {})
            .get("coordinates") or []
        )

        if len(coordinates) < 3:
            continue

        event_id = feature.get("id")

        magnitude = properties.get("mag")

        milliseconds = properties.get("time")

        if (
            not event_id
            or magnitude is None
            or milliseconds is None
        ):
            continue

        magnitude = float(magnitude)

        lon, lat, depth = map(
            float,
            coordinates[:3]
        )

        if not (
            MIN_MAG <= magnitude < MAX_MAG
        ):
            continue

        matches = matching_cities(
            lat, lon
        )

        if not matches:
            continue

        distance, city = matches[0]

        result.append({
            "id": str(event_id),
            "mag": magnitude,
            "lat": lat,
            "lon": lon,
            "depth": depth,
            "place": str(
                properties.get("place")
                or "Ubicación no especificada"
            ),
            "time": datetime.fromtimestamp(
                milliseconds / 1000,
                UTC
            ),
            "url": properties.get("url") or "",
            "city": city,
            "distance_km": distance,
            "all_cities": [
                {
                    "city": item[0],
                    "km": round(dist, 1)
                }
                for dist, item in matches
            ]
        })

    return result


# ============================================================
# COLORES POR MAGNITUD
# ============================================================

def band(magnitude):
    if 4 <= magnitude < 5:
        return (
            "#168a45",
            "#ffffff"
        )

    if 5 <= magnitude < 6:
        return (
            "#ffe100",
            "#101010"
        )

    return (
        "#fa8615",
        "#101010"
    )


# ============================================================
# FUENTES Y TEXTOS
# ============================================================

def font(size, bold=True):
    paths = (
        [
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/"
            "LiberationSans-Bold.ttf"
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/"
            "DejaVuSans.ttf"
        ]
    )

    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


def fitted(
    draw,
    text,
    max_width,
    max_height,
    initial=88,
    minsize=22
):
    for size in range(
        initial,
        minsize - 1,
        -2
    ):
        selected = font(size)

        box = draw.textbbox(
            (0, 0),
            text,
            font=selected,
            stroke_width=2
        )

        if (
            box[2] - box[0] <= max_width
            and box[3] - box[1] <= max_height
        ):
            return selected

    return font(minsize)


def centered(
    draw,
    text,
    y,
    color,
    max_width=990,
    max_height=100,
    initial=85
):
    selected = fitted(
        draw,
        text,
        max_width,
        max_height,
        initial
    )

    bounds = draw.textbbox(
        (0, 0),
        text,
        font=selected,
        stroke_width=2
    )

    width = bounds[2] - bounds[0]

    x = (1080 - width) / 2

    draw.text(
        (x, y),
        text,
        font=selected,
        fill=color,
        stroke_width=2,
        stroke_fill=(
            "#090909"
            if color != "#101010"
            else color
        )
    )


# ============================================================
# GENERAR IMAGEN
# ============================================================

def make_image(event, path):
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import matplotlib.patheffects as pe
    import cartopy.crs as ccrs
    import cartopy.feature as cf

    lat = event["lat"]
    lon = event["lon"]

    city = event["city"]

    background, foreground = band(
        event["mag"]
    )

    projection = ccrs.PlateCarree(
        central_longitude=lon
    )

    fig = plt.figure(
        figsize=(9, 9),
        dpi=160,
        facecolor="#071d34"
    )

    ax = fig.add_axes(
        [0, 0, 1, 1],
        projection=projection
    )

    latspan = 3.0

    lonspan = (
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
            lon - lonspan,
            lon + lonspan,
            lat - latspan,
            lat + latspan
        ],
        crs=ccrs.PlateCarree()
    )

    ax.set_facecolor("#073d67")

    ax.add_feature(
        cf.OCEAN.with_scale("110m"),
        facecolor="#06456d",
        zorder=0
    )

    ax.add_feature(
        cf.LAND.with_scale("110m"),
        facecolor="#50764d",
        zorder=1
    )

    ax.add_feature(
        cf.COASTLINE.with_scale("110m"),
        edgecolor="#e4e9dd",
        linewidth=1.4,
        zorder=2
    )

    ax.add_feature(
        cf.BORDERS.with_scale("110m"),
        edgecolor="#cfd5c9",
        linewidth=0.9,
        zorder=2
    )

    ax.gridlines(
        draw_labels=False,
        color="white",
        alpha=0.25,
        linewidth=0.5
    )

    # Anillos alrededor del epicentro.
    for size in (
        2500,
        1300,
        400
    ):
        ax.scatter(
            [lon],
            [lat],
            s=size,
            facecolors="none",
            edgecolors="#fa343a",
            linewidths=2.4,
            transform=ccrs.PlateCarree(),
            zorder=7
        )

    # Epicentro real USGS.
    ax.scatter(
        [lon],
        [lat],
        s=95,
        c="#ff142c",
        edgecolors="white",
        linewidths=1.5,
        transform=ccrs.PlateCarree(),
        zorder=9
    )

    # Centro de la ciudad monitoreada.
    ax.scatter(
        [city[4]],
        [city[3]],
        s=45,
        c="white",
        edgecolors="black",
        transform=ccrs.PlateCarree(),
        zorder=9
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
                foreground="#111111"
            )
        ]
    )

    buffer = io.BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=160,
        facecolor="#071d34"
    )

    plt.close(fig)

    buffer.seek(0)

    image = ImageOps.fit(
        Image.open(buffer).convert("RGB"),
        (1080, 1350)
    )

    draw = ImageDraw.Draw(image)

    # Banner superior.
    draw.rounded_rectangle(
        (26, 28, 1054, 211),
        radius=26,
        fill=background,
        outline="#f7f7f7",
        width=3
    )

    centered(
        draw,
        "ÚLTIMO MINUTO",
        57,
        foreground,
        980,
        125,
        113
    )

    # Banner inferior.
    draw.rounded_rectangle(
        (20, 1060, 1060, 1313),
        radius=24,
        fill=background,
        outline="#f7f7f7",
        width=3
    )

    centered(
        draw,
        (
            "SISMO DE MAGNITUD "
            + f"{event['mag']:.1f}"
        ),
        1090,
        foreground,
        990,
        90,
        75
    )

    centered(
        draw,
        (
            "CERCA DE "
            + city[0].upper()
            + ", "
            + city[1].upper()
        ),
        1190,
        foreground,
        988,
        85,
        54
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    image.save(
        path,
        quality=93,
        optimize=True
    )


# ============================================================
# PIE DE PUBLICACION
# ============================================================

def make_caption(event):
    city = event["city"]

    local = event["time"].astimezone(
        ZoneInfo(city[5])
    )

    recent = (
        now() - event["time"]
    ).total_seconds() < 3600

    if recent:
        first = (
            "Hace unos instantes, "
            "se ha registrado"
        )
    else:
        first = "Se registró"

    country_hashtag = re.sub(
        "[^A-Za-z]",
        "",
        city[1]
    )

    text = (
        f"🚨 ÚLTIMO MINUTO | SISMO DE MAGNITUD "
        f"{event['mag']:.1f} CERCA DE "
        f"{city[0].upper()}, "
        f"{city[1].upper()} {city[2]}\n\n"

        f"{first} un sismo de magnitud "
        f"{event['mag']:.1f} en las cercanías "
        f"de {city[0]}, {city[1]}.\n\n"

        f"📍 Epicentro: {event['place']}\n"

        f"📏 Distancia a {city[0]}: "
        f"{event['distance_km']:.1f} km\n"

        f"📊 Magnitud: {event['mag']:.1f}\n"

        f"⬇️ Profundidad: "
        f"{event['depth']:.1f} km\n"

        f"🕒 Fecha y hora local: "
        f"{local:%d/%m/%Y – %H:%M}\n"

        f"🌐 Coordenadas: "
        f"{event['lat']:.3f}, "
        f"{event['lon']:.3f}\n\n"

        "💬 ¿Sentiste el movimiento? "
        "Comenta desde qué ciudad y qué "
        "intensidad percibiste.\n\n"

        "📲 Sigue a @sismonauta para "
        "conocer los últimos registros "
        "sísmicos.\n\n"

        "#SISMONAUTA #UltimoMinuto "
        "#Sismo #Terremoto "
        f"#{country_hashtag} "
        "#USGS #Sismologia"
    )

    return text[:2200]


# ============================================================
# GITHUB: ESTADO PERSISTENTE
# ============================================================

class Github:

    def __init__(self):
        self.repo = os.environ[
            "GITHUB_REPOSITORY"
        ]

        self.branch = os.getenv(
            "GITHUB_REF_NAME",
            "main"
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
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        })

        response = self.s.get(
            self.base,
            timeout=25
        )

        response.raise_for_status()

        if response.json().get("private"):
            raise RuntimeError(
                "El repositorio debe ser público "
                "para alojar imágenes de Meta"
            )

    def endpoint(self, path):
        return (
            self.base
            + "/contents/"
            + urllib.parse.quote(
                path,
                safe="/"
            )
        )

    def read(self, path):
        response = self.s.get(
            self.endpoint(path),
            params={
                "ref": self.branch
            },
            timeout=30
        )

        if response.status_code == 404:
            return None

        response.raise_for_status()

        data = response.json()

        content = base64.b64decode(
            data["content"]
        ).decode("utf-8")

        return json.loads(content)

    def write(self, path, content):
        url = self.endpoint(path)

        for attempt in range(5):
            previous = self.s.get(
                url,
                params={
                    "ref": self.branch
                },
                timeout=30
            )

            if previous.status_code != 404:
                previous.raise_for_status()

            body = {
                "message": (
                    "SISMONAUTA ciudades: "
                    + path
                ),
                "branch": self.branch,
                "content": base64.b64encode(
                    content
                ).decode("ascii")
            }

            if previous.status_code != 404:
                body["sha"] = previous.json()["sha"]

            response = self.s.put(
                url,
                json=body,
                timeout=50
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
            indent=2
        ).encode("utf-8")

        self.write(
            path,
            raw
        )

    def url(self, path):
        return (
            "https://raw.githubusercontent.com/"
            + self.repo
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

def meta(
    method,
    path,
    token,
    params=None,
    base=IG_BASE
):
    values = dict(params or {})

    values["access_token"] = token

    url = (
        f"{base}/{GRAPH_VER}/{path}"
    )

    if method == "GET":
        response = requests.get(
            url,
            params=values,
            timeout=60
        )
    else:
        response = requests.post(
            url,
            data=values,
            timeout=80
        )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Meta no devolvió JSON"
        )

    if (
        not response.ok
        or data.get("error")
    ):
        error = (
            data.get("error") or {}
        )

        raise RuntimeError(
            "Meta: "
            + str(
                error.get("message", data)
            )[:250]
        )

    return data


# ============================================================
# COMPROBAR CUENTAS
# ============================================================

def credentials():
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

    instagram = meta(
        "GET",
        "me",
        ig_token,
        {
            "fields": "id,username"
        }
    )

    if (
        str(instagram.get("id")) != str(ig_id)
        or str(
            instagram.get("username", "")
        ).lower() != "sismonauta"
    ):
        raise RuntimeError(
            "Instagram no corresponde "
            "a @sismonauta"
        )

    facebook = meta(
        "GET",
        fb_id,
        fb_token,
        {
            "fields": "id,name"
        },
        FB_BASE
    )

    if (
        str(facebook.get("id")) != str(fb_id)
        or "sismonauta" not in str(
            facebook.get("name", "")
        ).lower()
    ):
        raise RuntimeError(
            "Fanpage no corresponde "
            "a SISMONAUTA"
        )

    return (
        ig_id,
        ig_token,
        fb_id,
        fb_token
    )


# ============================================================
# PUBLICAR EN INSTAGRAM
# ============================================================

def publish_ig(
    user_id,
    token,
    url,
    caption
):
    container = meta(
        "POST",
        f"{user_id}/media",
        token,
        {
            "image_url": url,
            "caption": caption
        }
    )["id"]

    for attempt in range(25):
        time.sleep(5)

        result = meta(
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

        if status in (
            "ERROR",
            "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram contenedor: "
                + str(status)
            )
    else:
        raise RuntimeError(
            "Instagram: timeout contenedor"
        )

    result = meta(
        "POST",
        f"{user_id}/media_publish",
        token,
        {
            "creation_id": container
        }
    )

    return str(result["id"])


# ============================================================
# PUBLICAR EN FACEBOOK
# ============================================================

def publish_fb(
    page_id,
    token,
    url,
    caption
):
    result = meta(
        "POST",
        f"{page_id}/photos",
        token,
        {
            "url": url,
            "caption": caption,
            "published": "true"
        },
        FB_BASE
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

def public_image(url):
    for attempt in range(12):
        try:
            response = S.get(
                url,
                timeout=20
            )

            content_type = response.headers.get(
                "Content-Type",
                ""
            )

            if (
                response.ok
                and content_type.startswith("image/")
            ):
                return

        except requests.RequestException:
            pass

        time.sleep(5)

    raise RuntimeError(
        "Imagen todavía no pública"
    )


# ============================================================
# CONTROL DE DUPLICADOS
# ============================================================

def finished(entry):
    targets = entry.get(
        "targets", {}
    )

    return all(
        targets.get(
            platform, {}
        ).get("status") == "published"
        for platform in (
            "instagram",
            "facebook"
        )
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
        default="output_ciudades"
    )

    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24
    )

    args = parser.parse_args()

    out = Path(args.out)

    out.mkdir(
        parents=True,
        exist_ok=True
    )

    receipt_path = (
        out / "estado_publicacion.json"
    )

    receipt = {
        "status": "starting",
        "checked_at": iso(now()),
        "publish_requested": args.publish,
        "events": []
    }

    try:
        if not (
            1 <= args.lookback_hours <= 168
        ):
            raise ValueError(
                "lookback fuera de rango"
            )

        github = (
            Github()
            if args.publish
            else None
        )

        original = (
            github.read(STATE)
            if github
            else None
        )

        state = original or {
            "events": {}
        }

        state.setdefault(
            "events", {}
        )

        events = fetch_events(
            args.lookback_hours
        )

        # En primera instalación, no publicar
        # todo el historial de 24 horas.
        if (
            args.publish
            and original is None
        ):
            limit = (
                now()
                - timedelta(minutes=90)
            )

            events = [
                event
                for event in events
                if event["time"] >= limit
            ]

        pending = [
            event
            for event in events
            if not finished(
                state["events"].get(
                    event["id"], {}
                )
            )
        ]

        log(
            "Eventos en zona:",
            len(events),
            "pendientes:",
            len(pending)
        )

        if not pending:
            receipt["status"] = "no_new_event"

            dump(
                receipt_path,
                receipt
            )

            return 0

        creds = (
            credentials()
            if args.publish
            else None
        )

        statuses = []

        for event in pending:
            event_id = event["id"]

            log(
                "EVENTO",
                event_id,
                "M",
                event["mag"],
                event["city"][0],
                round(
                    event["distance_km"], 1
                ),
                "km"
            )

            entry = state[
                "events"
            ].setdefault(
                event_id,
                {
                    "magnitude": event["mag"],
                    "place": event["place"],
                    "time": iso(
                        event["time"]
                    ),
                    "targets": {
                        "instagram": {
                            "status": "pending"
                        },
                        "facebook": {
                            "status": "pending"
                        }
                    }
                }
            )

            entry.setdefault(
                "targets", {}
            )

            for platform in (
                "instagram",
                "facebook"
            ):
                entry[
                    "targets"
                ].setdefault(
                    platform,
                    {
                        "status": "pending"
                    }
                )

            # No reenviar estados inciertos.
            uncertain = any(
                entry["targets"][platform].get(
                    "status"
                ) == "uncertain"
                for platform in (
                    "instagram",
                    "facebook"
                )
            )

            if uncertain:
                outcome = "partial_or_uncertain"

            else:
                filename = (
                    safe(event_id) + ".jpg"
                )

                filepath = (
                    out / filename
                )

                make_image(
                    event,
                    filepath
                )

                caption = make_caption(
                    event
                )

                dump(
                    out / (
                        safe(event_id)
                        + ".json"
                    ),
                    {
                        "event": event,
                        "caption": caption
                    }
                )

                if not args.publish:
                    outcome = "preview_only"

                else:
                    remote = (
                        IMAGE_DIR
                        + "/"
                        + filename
                    )

                    github.write(
                        remote,
                        filepath.read_bytes()
                    )

                    url = github.url(
                        remote
                    )

                    public_image(url)

                    entry[
                        "image_url"
                    ] = url

                    github.write_json(
                        STATE,
                        state
                    )

                    for platform in (
                        "instagram",
                        "facebook"
                    ):
                        target = entry[
                            "targets"
                        ][platform]

                        if (
                            target.get("status")
                            == "published"
                        ):
                            continue

                        # Antes de contactar Meta,
                        # evitar reenvíos automáticos.
                        target["status"] = "uncertain"

                        github.write_json(
                            STATE,
                            state
                        )

                        try:
                            if platform == "instagram":
                                identifier = publish_ig(
                                    creds[0],
                                    creds[1],
                                    url,
                                    caption
                                )
                            else:
                                identifier = publish_fb(
                                    creds[2],
                                    creds[3],
                                    url,
                                    caption
                                )

                            target.update(
                                status="published",
                                id=identifier
                            )

                            target.pop(
                                "error", None
                            )

                            github.write_json(
                                STATE,
                                state
                            )

                            log(
                                "PUBLICADO",
                                event_id,
                                platform,
                                identifier
                            )

                        except Exception as exc:
                            target["error"] = (
                                error_text(exc)
                            )

                            github.write_json(
                                STATE,
                                state
                            )

                            log(
                                "ERROR",
                                event_id,
                                platform,
                                error_text(exc)
                            )

                    outcome = (
                        "published"
                        if finished(entry)
                        else "partial_or_uncertain"
                    )

            statuses.append(
                outcome
            )

            receipt["events"].append({
                "id": event_id,
                "magnitude": event["mag"],
                "city": event["city"][0],
                "distance_km": round(
                    event["distance_km"], 1
                ),
                "status": outcome,
                "targets": entry["targets"]
            })

            dump(
                receipt_path,
                receipt
            )

        if all(
            status == "preview_only"
            for status in statuses
        ):
            receipt["status"] = "preview_only"

        elif any(
            status == "partial_or_uncertain"
            for status in statuses
        ):
            receipt["status"] = "partial_or_uncertain"

        elif all(
            status == "published"
            for status in statuses
        ):
            receipt["status"] = "published"

        else:
            receipt["status"] = "error"

        dump(
            receipt_path,
            receipt
        )

        if receipt["status"] in (
            "published",
            "preview_only",
            "no_new_event"
        ):
            return 0

        return 1

    except Exception as exc:
        receipt["status"] = "error"

        receipt["error"] = (
            error_text(exc)
        )

        dump(
            receipt_path,
            receipt
        )

        log(
            "ERROR GENERAL:",
            error_text(exc)
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
