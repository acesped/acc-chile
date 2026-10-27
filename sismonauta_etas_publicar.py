
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SISMONAUTA ETAS CHILE
Modelo científico y publicador unificado.

Incluye:
- Catálogo USGS desde 2018.
- Estimación de Mc, b y ajuste ETAS.
- 25 mapas horarios y dos heatmaps JET.
- Tres zonas de aumento separadas.
- Ciudades GeoNames y cartografía Natural Earth.
- Estrellas parpadeantes.
- Voz chilena natural, sin aceleración.
- Audio sincronizado sin silencios prolongados.
- Música y cierre Dale Like y Comenta.
- Video 1080x1350 y Reel 1080x1920.
- Instagram @sismonauta y Facebook Sismonauta.
- Publicaciones múltiples por día.
- Historial individual por ejecución en GitHub.

Uso:
  python sismonauta_etas_publicar.py --out output_etas
  python sismonauta_etas_publicar.py --out output_etas --publish

MODELO EXPERIMENTAL.
No calibrado ni validado prospectivamente.
NO constituye predicción ni alerta sísmica oficial.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import numpy as np
import pandas as pd
import requests

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

from matplotlib.colors import LogNorm
from matplotlib.ticker import FuncFormatter

from scipy.optimize import minimize
from scipy.ndimage import gaussian_filter
from scipy.io import wavfile

from numba import njit
from PIL import Image, ImageDraw, ImageFont

import cartopy.crs as ccrs
import cartopy.feature as cfeature

from cartopy.io import shapereader

from shapely.geometry import Point, box as shp_box
from shapely.ops import unary_union
from shapely.prepared import prep

import pycountry
from geopy.geocoders import Nominatim


# ============================================================
# 1. CONFIGURACIÓN
# ============================================================

COUNTRY_NAME = "Chile"
COUNTRY_ISO = "CHL"

START = "2018-01-01"
DOWNLOAD_M = 3.0
REQUESTED_M = 4.0

HORIZON_DAYS = 1.0
LOOKBACK_HOURS = 24

VIDEO_SECONDS = 30
FPS = 24

FIT_N = 1300
TRIGGER_DAYS = 365
TRIGGER_MAX = 2500

MAX_GRID_CELLS = 15000

W = 1080
H = 1350

# Voz natural, sin aceleración adicional.
VOICE_RATE = "+0%"
AUDIO_TEMPO = 1.0

ORIGINAL_VIDEO_NAME = (
    "ETAS_DOBLE_HEATMAP_CON_LIKE.mp4"
)

REEL_NAME = (
    "sismonauta_etas_reel_9x16.mp4"
)

GRAPH_VERSION = os.getenv(
    "META_GRAPH_VERSION",
    "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = (
    "https://graph.facebook.com/"
    + GRAPH_VERSION
)

GH_API = "https://api.github.com"

HTTP = requests.Session()
HTTP.headers["User-Agent"] = (
    "Sismonauta-ETAS-Chile/5.0"
)

CUTOFF = None
COUNTRY_GEOM = None

LAT0 = LAT1 = LON0 = LON1 = GRID = None
LATS = LONS = XX = YY = None

CITY = []
CITY_SOURCE = ""
CAPITAL_CITY = None

_DETAIL_CACHE = None

OUT = Path(
    os.getenv(
        "ETAS_OUTPUT_DIR",
        "output_etas"
    )
).resolve()


# ============================================================
# 2. UTILIDADES
# ============================================================

def log(*args):
    print(*args, flush=True)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_name(
        path.name + ".tmp"
    )

    temp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str
        ),
        encoding="utf-8"
    )

    temp.replace(path)


def binary(name):
    executable = shutil.which(name)

    if not executable:
        raise RuntimeError(
            f"{name} no disponible en PATH. "
            "Revisar instalación del workflow."
        )

    return executable


def ffmpeg(*args):
    cmd = [
        binary("ffmpeg"),
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        *map(str, args)
    ]

    process = subprocess.run(
        cmd,
        text=True,
        capture_output=True
    )

    if process.returncode:
        raise RuntimeError(
            "FFmpeg: "
            + (process.stderr or process.stdout)[-1800:]
        )


def probe(path):
    process = subprocess.run(
        [
            binary("ffprobe"),
            "-v", "error",
            "-show_entries",
            (
                "format=duration,size:"
                "stream=codec_type,codec_name,"
                "width,height,r_frame_rate"
            ),
            "-of", "json",
            str(path)
        ],
        text=True,
        capture_output=True
    )

    if process.returncode:
        raise RuntimeError(
            "FFprobe: " + process.stderr[-900:]
        )

    result = json.loads(process.stdout)

    if not any(
        stream.get("codec_type") == "video"
        for stream in result.get("streams", [])
    ):
        raise RuntimeError(
            "MP4 sin pista de vídeo: " + str(path)
        )

    duration = float(
        result.get("format", {}).get("duration") or 0
    )

    if duration <= 0:
        raise RuntimeError(
            "MP4 sin duración válida: " + str(path)
        )

    return result


def audio_duration(path):
    process = subprocess.run(
        [
            binary("ffprobe"),
            "-v", "error",
            "-show_entries", "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path)
        ],
        capture_output=True,
        text=True
    )

    if process.returncode:
        raise RuntimeError(
            "FFprobe audio: " + process.stderr[-500:]
        )

    return float(process.stdout.strip())


# ============================================================
# 3. GEOGRAFÍA DE CHILE
# ============================================================

def setup_country():
    global COUNTRY_GEOM
    global LAT0, LAT1, LON0, LON1, GRID
    global LATS, LONS, XX, YY
    global CITY, CITY_SOURCE, CAPITAL_CITY

    country = pycountry.countries.get(
        alpha_3=COUNTRY_ISO
    )

    shp = shapereader.natural_earth(
        resolution="110m",
        category="cultural",
        name="admin_0_countries"
    )

    reader = shapereader.Reader(shp)

    geoms = [
        record.geometry
        for record in reader.records()
        if COUNTRY_ISO in [
            record.attributes.get(key, "")
            for key in (
                "ADM0_A3",
                "SOV_A3",
                "ISO_A3",
                "GU_A3",
                "SU_A3"
            )
        ]
    ]

    reader.close()

    if not geoms:
        raise RuntimeError(
            "No se encontró Chile en Natural Earth."
        )

    geom = unary_union(geoms)

    parts = (
        list(geom.geoms)
        if hasattr(geom, "geoms")
        else [geom]
    )

    polygons = [
        item
        for item in parts
        if item.geom_type == "Polygon"
    ]

    if polygons:
        biggest = max(
            polygons,
            key=lambda item: item.area
        )

        kept = [
            item
            for item in polygons
            if item.area >= 0.025 * biggest.area
        ]

        if len(kept) < len(polygons):
            log(
                "Geografía: se omiten componentes "
                "menores de Natural Earth."
            )

        geom = unary_union(kept)

    x0, y0, x1, y1 = geom.bounds

    if x1-x0 > 150:
        raise RuntimeError(
            "Extensión geográfica incompatible."
        )

    LAT0 = max(-89.0, y0-2.2)
    LAT1 = min(89.0, y1+2.2)

    LON0 = max(-179.9, x0-2.2)
    LON1 = min(179.9, x1+2.2)

    GRID = round(
        max(
            0.20,
            math.sqrt(
                (LAT1-LAT0)
                * (LON1-LON0)
                / MAX_GRID_CELLS
            )
        ),
        3
    )

    LATS = np.arange(
        LAT0,
        LAT1 + GRID/2,
        GRID
    )

    LONS = np.arange(
        LON0,
        LON1 + GRID/2,
        GRID
    )

    XX, YY = np.meshgrid(
        LONS,
        LATS
    )

    COUNTRY_GEOM = geom

    log(
        f"Chile: {XX.size:,} celdas | "
        f"malla {GRID:.3f}°"
    )

    try:
        response = HTTP.get(
            "https://download.geonames.org/"
            "export/dump/cities15000.zip",
            timeout=(15, 50)
        )

        response.raise_for_status()

        with zipfile.ZipFile(
            io.BytesIO(response.content)
        ) as archive:
            with archive.open(
                "cities15000.txt"
            ) as stream:
                table = pd.read_csv(
                    stream,
                    sep="\t",
                    header=None,
                    usecols=[1, 4, 5, 8],
                    names=[
                        "name",
                        "lat",
                        "lon",
                        "iso2"
                    ],
                    low_memory=False
                )

        table = table[
            table.lat.between(LAT0, LAT1)
            & table.lon.between(LON0, LON1)
        ]

        local = table[
            table.iso2 == country.alpha_2
        ]

        if len(local):
            table = local

        CITY = [
            (
                str(row.name),
                float(row.lat),
                float(row.lon)
            )
            for row in table.itertuples(
                index=False
            )
        ]

        CITY_SOURCE = (
            "GeoNames cities15000 (CC BY 4.0)"
        )

        log(
            "Ciudades de referencia:",
            len(CITY)
        )

        try:
            response = HTTP.get(
                "https://download.geonames.org/"
                "export/dump/countryInfo.txt",
                timeout=(15, 40)
            )

            response.raise_for_status()

            rows = [
                line.split("\t")
                for line in response.text.splitlines()
                if line and not line.startswith("#")
            ]

            matches = [
                value
                for value in rows
                if (
                    len(value) > 5
                    and value[0] == country.alpha_2
                )
            ]

            if matches:
                name = matches[0][5].strip()

                candidates = [
                    city
                    for city in CITY
                    if city[0].casefold() == name.casefold()
                ]

                if candidates:
                    CAPITAL_CITY = candidates[0]

                else:
                    geocoder = Nominatim(
                        user_agent=(
                            "sismonauta-etas-capital/5.0"
                        ),
                        timeout=15
                    )

                    place = geocoder.geocode(
                        name + ", Chile",
                        exactly_one=True
                    )

                    if place:
                        CAPITAL_CITY = (
                            name,
                            float(place.latitude),
                            float(place.longitude)
                        )

                log(
                    "Capital:",
                    CAPITAL_CITY or name
                )

        except Exception as exc:
            log(
                "Capital no disponible:",
                str(exc)[:100]
            )

    except Exception as exc:
        CITY = []
        CITY_SOURCE = "Nominatim/OpenStreetMap"

        log(
            "GeoNames no disponible:",
            str(exc)[:100]
        )


def nearest_city(lat, lon):
    if CITY:
        city_lats = np.radians(
            [city[1] for city in CITY]
        )

        city_lons = np.radians(
            [city[2] for city in CITY]
        )

        dy = city_lats - np.radians(lat)
        dx = city_lons - np.radians(lon)

        a = (
            np.sin(dy/2)**2
            + np.cos(np.radians(lat))
            * np.cos(city_lats)
            * np.sin(dx/2)**2
        )

        distances = 12742 * np.arcsin(
            np.sqrt(np.clip(a, 0, 1))
        )

        index = int(distances.argmin())

        return (
            float(distances[index]),
            CITY[index][0]
        )

    try:
        geocoder = Nominatim(
            user_agent="sismonauta-etas-reverse/5.0",
            timeout=15
        )

        result = geocoder.reverse(
            (lat, lon),
            language="es",
            zoom=10,
            exactly_one=True
        )

        address = (
            result.raw.get("address", {})
            if result
            else {}
        )

        place = next(
            (
                address[key]
                for key in (
                    "city",
                    "town",
                    "village",
                    "municipality",
                    "county"
                )
                if address.get(key)
            ),
            None
        )

        if place:
            return 0.0, str(place)

    except Exception:
        pass

    return None, "localidad no identificada"


# ============================================================
# 4. CATÁLOGO USGS
# ============================================================

def catalog_usgs():
    session = requests.Session()

    session.headers["User-Agent"] = (
        "Sismonauta-ETAS-Chile/5.0"
    )

    begin = pd.Timestamp(
        START,
        tz="UTC"
    )

    blocks = []
    frames = []

    while begin < CUTOFF:
        end = min(
            begin + pd.DateOffset(months=1),
            CUTOFF
        )

        blocks.append((begin, end))
        begin = end

    for index, (start, end) in enumerate(
        blocks,
        1
    ):
        params = {
            "format": "csv",
            "starttime": start.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "endtime": end.strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),
            "minlatitude": LAT0,
            "maxlatitude": LAT1,
            "minlongitude": LON0,
            "maxlongitude": LON1,
            "minmagnitude": DOWNLOAD_M,
            "orderby": "time-asc",
            "limit": 20000
        }

        for attempt in range(4):
            try:
                response = session.get(
                    "https://earthquake.usgs.gov/"
                    "fdsnws/event/1/query",
                    params=params,
                    timeout=(15, 75)
                )

                if response.status_code == 204:
                    part = pd.DataFrame(
                        columns=[
                            "time",
                            "latitude",
                            "longitude",
                            "mag",
                            "id"
                        ]
                    )

                else:
                    response.raise_for_status()

                    part = pd.read_csv(
                        io.StringIO(response.text)
                    )

                    if len(part) >= 20000:
                        raise RuntimeError(
                            "Límite USGS alcanzado: "
                            "dividir intervalo mensual."
                        )

                break

            except Exception as exc:
                if attempt == 3:
                    raise RuntimeError(
                        f"Error USGS {start:%Y-%m}: "
                        f"{exc}"
                    ) from exc

                time.sleep(
                    2 * (attempt+1)
                )

        frames.append(part)

        if (
            index % 4 == 0
            or index == len(blocks)
        ):
            log(
                f"USGS: {index}/{len(blocks)} meses"
            )

    df = pd.concat(
        frames,
        ignore_index=True
    )

    df["time"] = pd.to_datetime(
        df["time"],
        utc=True,
        errors="coerce"
    )

    for key in (
        "latitude",
        "longitude",
        "mag"
    ):
        df[key] = pd.to_numeric(
            df[key],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "time",
            "latitude",
            "longitude",
            "mag"
        ]
    )

    df = df[
        (df.time < CUTOFF)
        & df.latitude.between(LAT0, LAT1)
        & df.longitude.between(LON0, LON1)
        & (df.mag >= DOWNLOAD_M)
    ]

    area = prep(
        COUNTRY_GEOM.buffer(2.5)
    )

    keep = np.fromiter(
        (
            area.covers(
                Point(
                    float(lon),
                    float(lat)
                )
            )
            for lat, lon in zip(
                df.latitude,
                df.longitude
            )
        ),
        dtype=bool,
        count=len(df)
    )

    df = (
        df.loc[keep]
        .drop_duplicates(subset=["id"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    if len(df) < 200:
        raise RuntimeError(
            "Menos de 200 eventos USGS válidos."
        )

    log(
        f"Eventos sísmicos válidos: {len(df):,}"
    )

    return df


# ============================================================
# 5. Mc Y PARÁMETRO b
# ============================================================

def estimate_mc_b(df):
    magnitudes = df.mag.to_numpy()

    edges = np.arange(
        np.floor(magnitudes.min()*10)/10,
        np.ceil(magnitudes.max()*10)/10 + 0.21,
        0.1
    )

    histogram, _ = np.histogram(
        magnitudes,
        bins=edges
    )

    mc = round(
        float(edges[histogram.argmax()]),
        2
    )

    selected = magnitudes[
        magnitudes >= mc
    ]

    if len(selected) < 100:
        raise RuntimeError(
            "Menos de 100 eventos por encima de Mc."
        )

    denominator = (
        selected.mean() - (mc-0.05)
    )

    if denominator <= 0:
        raise RuntimeError(
            "No se pudo estimar parámetro b."
        )

    return (
        mc,
        float(np.log10(np.e) / denominator)
    )


# ============================================================
# 6. MODELO ETAS CON NUMBA
# ============================================================

@njit
def negll(theta, t, m, mc, T):
    mu, K, alpha, c, p = theta

    if (
        mu <= 0
        or K <= 0
        or c <= 0
        or p <= 1
    ):
        return 1e90

    out = mu*T

    for i in range(len(t)):
        rate = mu

        for j in range(i):
            dt = t[i]-t[j]

            if dt > 0:
                rate += (
                    K
                    * np.exp(alpha*(m[j]-mc))
                    * (dt+c)**(-p)
                )

        if rate <= 0 or not np.isfinite(rate):
            return 1e90

        out -= np.log(rate)

    for j in range(len(t)):
        age = T-t[j]

        if age > 0:
            out += (
                K
                * np.exp(alpha*(m[j]-mc))
                * (
                    (age+c)**(1-p)
                    - c**(1-p)
                ) / (1-p)
            )

    return out if np.isfinite(out) else 1e90


def fit_etas(df, mc):
    sub = df[
        df.mag >= mc
    ].tail(FIT_N)

    if len(sub) < 100:
        raise RuntimeError(
            "Eventos insuficientes para ajuste ETAS."
        )

    t = (
        (sub.time-sub.time.iloc[0])
        .dt.total_seconds()
        .to_numpy()
        / 86400.0
    )

    m = sub.mag.to_numpy()
    T = max(float(t[-1]), 1e-6)

    bounds = [
        (1e-6, 100),
        (1e-8, 5),
        (0, 3),
        (0.002, 2),
        (1.01, 2.8)
    ]

    attempts = [
        [0.2, 0.01, 0.8, 0.02, 1.2],
        [0.5, 0.005, 1.0, 0.08, 1.3],
        [1.0, 0.001, 0.6, 0.1, 1.5]
    ]

    fits = []

    for index, x0 in enumerate(attempts, 1):
        log(
            f"Ajuste ETAS {index}/3"
        )

        result = minimize(
            negll,
            x0,
            args=(t, m, mc, T),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 260}
        )

        if (
            result.success
            and np.isfinite(result.fun)
        ):
            fits.append(result)

    if not fits:
        raise RuntimeError(
            "ETAS no convergió."
        )

    best = min(
        fits,
        key=lambda item: item.fun
    )

    return dict(
        zip(
            ("mu", "K", "alpha", "c", "p"),
            map(float, best.x)
        )
    )


# ============================================================
# 7. FONDO ESPACIAL
# ============================================================

def make_background(events):
    bins_lat = np.r_[
        LATS-GRID/2,
        LATS[-1]+GRID/2
    ]

    bins_lon = np.r_[
        LONS-GRID/2,
        LONS[-1]+GRID/2
    ]

    histogram, _, _ = np.histogram2d(
        events.latitude,
        events.longitude,
        bins=[
            bins_lat,
            bins_lon
        ]
    )

    values = (
        gaussian_filter(histogram, 1.7)
        + 1e-10
    )

    return (
        values / values.sum()
    ).astype(np.float32)


# ============================================================
# 8. TRES ZONAS DE MAYOR INCREMENTO
# ============================================================

def three_distinct_increases(
    delta_pp,
    min_km=90.0
):
    flat = np.asarray(
        delta_pp
    ).ravel()

    selected = []

    for index in np.argsort(flat)[::-1]:
        value = float(flat[index])

        if (
            not np.isfinite(value)
            or value <= 0
        ):
            break

        row, col = np.unravel_index(
            int(index),
            delta_pp.shape
        )

        lat = float(LATS[row])
        lon = float(LONS[col])

        distinct = True

        for point in selected:
            dy = np.radians(
                lat-point["lat"]
            )

            dx = np.radians(
                lon-point["lon"]
            )

            a = (
                np.sin(dy/2)**2
                + np.cos(np.radians(lat))
                * np.cos(np.radians(point["lat"]))
                * np.sin(dx/2)**2
            )

            distance = (
                12742
                * np.arcsin(
                    min(1.0, np.sqrt(a))
                )
            )

            if distance < min_km:
                distinct = False
                break

        if distinct:
            selected.append({
                "lat": lat,
                "lon": lon,
                "val": value
            })

            if len(selected) == 3:
                break

    return selected


# ============================================================
# 9. EVOLUCIÓN DE 25 MAPAS HORARIOS
# ============================================================

def evolution_24h(
    df,
    theta,
    mc,
    b,
    magnitude
):
    start = CUTOFF - pd.Timedelta(
        hours=LOOKBACK_HOURS
    )

    times = pd.date_range(
        start=start,
        periods=25,
        freq="1h"
    )

    chosen = df[
        (df.time < CUTOFF)
        & (
            df.time >= (
                start
                - pd.Timedelta(
                    days=TRIGGER_DAYS
                )
            )
        )
        & (df.mag >= mc)
    ].copy().reset_index(drop=True)

    if len(chosen) > TRIGGER_MAX:
        raise RuntimeError(
            f"{len(chosen)} disparadores. "
            "Aumentar TRIGGER_MAX; "
            "no se descartan eventos."
        )

    log(
        f"Preparando {len(times)} mapas; "
        f"{len(chosen)} disparadores ETAS."
    )

    kernels = np.empty(
        (len(chosen), XX.size),
        dtype=np.float32
    )

    lat_flat = YY.ravel().astype(
        np.float32
    )

    lon_flat = XX.ravel().astype(
        np.float32
    )

    for j, event in enumerate(
        chosen.itertuples(index=False)
    ):
        dx = (
            lon_flat-event.longitude
        ) * (
            111.32
            * np.cos(
                np.radians(event.latitude)
            )
        )

        dy = (
            lat_flat-event.latitude
        ) * 111.32

        weights = np.power(
            1 + (
                dx*dx + dy*dy
            ) / (25.0**2),
            -1.5
        )

        kernels[j] = (
            weights / weights.sum()
        )

        if (j+1) % 500 == 0:
            log(
                f"Kernels: {j+1}/{len(chosen)}"
            )

    event_times = (
        chosen.time.astype("int64")
        .to_numpy()
    )

    magnitudes = chosen.mag.to_numpy(
        dtype=np.float64
    )

    productivity = np.exp(
        np.clip(
            theta["alpha"]*(magnitudes-mc),
            -40,
            40
        )
    )

    factor = 10**(
        -b*(magnitude-mc)
    )

    c = theta["c"]
    p = theta["p"]

    maps = []
    maxima = []
    baseline = None

    for k, stamp in enumerate(times):
        ages = (
            (stamp.value-event_times)
            / 1e9
            / 86400.0
        )

        valid = (
            (ages > 0)
            & (ages <= TRIGGER_DAYS)
        )

        masses = np.zeros(
            len(chosen),
            dtype=np.float32
        )

        ages_valid = ages[valid]

        masses[valid] = (
            theta["K"]
            * productivity[valid]
            * (
                (
                    ages_valid
                    + HORIZON_DAYS
                    + c
                )**(1-p)
                - (ages_valid+c)**(1-p)
            ) / (1-p)
        ).astype(np.float32)

        before = df[
            (df.time < stamp)
            & (df.mag >= mc)
        ]

        background = make_background(before)

        expected = (
            theta["mu"]
            * HORIZON_DAYS
            * background.ravel()
        )

        if len(chosen):
            expected = (
                expected
                + masses @ kernels
            )

        forecast = -np.expm1(
            -expected.reshape(background.shape)
            * factor
        )

        if not np.all(
            np.isfinite(forecast)
        ):
            raise RuntimeError(
                "Mapa ETAS no finito."
            )

        forecast = np.clip(
            forecast,
            0,
            1
        ).astype(np.float32)

        if baseline is None:
            baseline = forecast.copy()

        delta = (
            forecast.astype(np.float64)
            - baseline.astype(np.float64)
        ) * 100.0

        row_p, col_p = np.unravel_index(
            np.argmax(forecast),
            forecast.shape
        )

        row_d, col_d = np.unravel_index(
            np.argmax(delta),
            delta.shape
        )

        maps.append(forecast)

        maxima.append({
            "prob_lat": float(LATS[row_p]),
            "prob_lon": float(LONS[col_p]),
            "prob_val": float(
                forecast[row_p, col_p]
            ) * 100.0,

            "delta_lat": float(LATS[row_d]),
            "delta_lon": float(LONS[col_d]),
            "delta_val": max(
                0.0,
                float(delta[row_d, col_d])
            ),

            "delta_points": (
                three_distinct_increases(delta)
            )
        })

        log(
            f"Mapa {k+1}/25 | "
            f"{stamp:%d/%m %H:%M} UTC | "
            f"máx prob "
            f"{maxima[-1]['prob_val']:.4f}% | "
            f"aumento "
            f"{maxima[-1]['delta_val']:.5f} pp"
        )

    return times, maps, maxima


# ============================================================
# 10. TIPOGRAFÍA Y CARTOGRAFÍA
# ============================================================

def font(size, bold=False):
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/"
            + (
                "DejaVuSans-Bold.ttf"
                if bold
                else "DejaVuSans.ttf"
            )
        ),
        (
            "/usr/share/fonts/truetype/liberation2/"
            + (
                "LiberationSans-Bold.ttf"
                if bold
                else "LiberationSans-Regular.ttf"
            )
        )
    ]

    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(
                path,
                size
            )

    return ImageFont.load_default()


def load_detailed_geography():
    global _DETAIL_CACHE

    if _DETAIL_CACHE is not None:
        return _DETAIL_CACHE

    region = shp_box(
        LON0,
        LAT0,
        LON1,
        LAT1
    )

    layers = {}

    catalog = {
        "coast": (
            "10m",
            "physical",
            "coastline"
        ),
        "country": (
            "10m",
            "cultural",
            "admin_0_boundary_lines_land"
        ),
        "states": (
            "50m",
            "cultural",
            "admin_1_states_provinces_lines"
        )
    }

    for key, (
        resolution,
        category,
        name
    ) in catalog.items():
        try:
            shp = shapereader.natural_earth(
                resolution=resolution,
                category=category,
                name=name
            )

            reader = shapereader.Reader(
                shp,
                bbox=(
                    LON0,
                    LAT0,
                    LON1,
                    LAT1
                )
            )

            geoms = [
                geometry
                for geometry in reader.geometries()
                if (
                    geometry is not None
                    and geometry.intersects(region)
                )
            ]

            reader.close()

            layers[key] = geoms

            log(
                f"Cartografía {name}: "
                f"{len(geoms)} geometrías"
            )

        except Exception as exc:
            layers[key] = []

            log(
                "Aviso cartografía",
                name,
                str(exc)[:100]
            )

    _DETAIL_CACHE = layers

    return layers


# ============================================================
# 11. PANEL HEATMAP
# ============================================================

def draw_heatmap_panel(
    fig,
    rect,
    data,
    clims,
    city_points,
    colorbar_rect,
    colorbar_label,
    colorbar_fmt,
    card_rect,
    card_title,
    card_value,
    card_city,
    scale="log"
):
    detailed = load_detailed_geography()

    ax = fig.add_axes(
        rect,
        projection=ccrs.PlateCarree()
    )

    ax.set_extent(
        [LON0, LON1, LAT0, LAT1],
        crs=ccrs.PlateCarree()
    )

    ax.set_facecolor("#DFEAF1")

    try:
        ax.add_feature(
            cfeature.LAND.with_scale("110m"),
            facecolor="#EDE9E2",
            zorder=0
        )

        ax.add_feature(
            cfeature.OCEAN.with_scale("110m"),
            facecolor="#DFEAF1",
            zorder=0
        )

    except Exception:
        pass

    for layer, color, width, style, zorder in (
        ("coast", "#152C3D", 1.35, "-", 15),
        ("country", "#203747", 0.85, "-", 14),
        ("states", "#6C8190", 0.42, ":", 13)
    ):
        if detailed.get(layer):
            ax.add_geometries(
                detailed[layer],
                crs=ccrs.PlateCarree(),
                facecolor="none",
                edgecolor=color,
                linewidth=width,
                linestyle=style,
                zorder=zorder
            )

    if not detailed.get("coast"):
        ax.coastlines(
            "110m",
            color="#173244",
            linewidth=1.15,
            zorder=15
        )

    if not detailed.get("country"):
        ax.add_feature(
            cfeature.BORDERS.with_scale("110m"),
            edgecolor="#253F50",
            linewidth=0.6,
            linestyle="--",
            zorder=14
        )

    gridlines = ax.gridlines(
        draw_labels=True,
        linewidth=0.30,
        alpha=0.26,
        linestyle=":"
    )

    gridlines.top_labels = False
    gridlines.right_labels = False

    gridlines.xlabel_style = {
        "size": 7.7,
        "color": "#566C7E"
    }

    gridlines.ylabel_style = {
        "size": 7.7,
        "color": "#566C7E"
    }

    for spine in ax.spines.values():
        spine.set_edgecolor("#B5C8D5")

    for name, lat, lon in city_points:
        ax.plot(
            lon,
            lat,
            "o",
            ms=2.8,
            color="#152E3F",
            markeredgecolor="white",
            markeredgewidth=0.45,
            transform=ccrs.PlateCarree(),
            zorder=20
        )

        ax.text(
            lon+0.12,
            lat+0.07,
            name,
            fontsize=7.2,
            color="#183143",
            transform=ccrs.PlateCarree(),
            zorder=21,
            bbox={
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.76,
                "pad": 1.0
            }
        )

    norm = (
        LogNorm(
            vmin=clims[0],
            vmax=clims[1]
        )
        if scale == "log"
        else mcolors.PowerNorm(
            gamma=0.50,
            vmin=0.0,
            vmax=clims[1]
        )
    )

    mesh = ax.pcolormesh(
        LONS,
        LATS,
        data,
        cmap="jet",
        norm=norm,
        shading="nearest",
        transform=ccrs.PlateCarree(),
        zorder=2,
        alpha=0.95
    )

    cax = fig.add_axes(
        colorbar_rect
    )

    bar = fig.colorbar(
        mesh,
        cax=cax,
        orientation="horizontal"
    )

    bar.set_ticks(
        np.geomspace(
            clims[0],
            clims[1],
            5
        )
        if scale == "log"
        else np.linspace(
            0,
            clims[1],
            5
        )
    )

    bar.ax.xaxis.set_major_formatter(
        FuncFormatter(colorbar_fmt)
    )

    bar.ax.tick_params(
        labelsize=8.2,
        colors="#2E4C60"
    )

    bar.set_label(
        colorbar_label,
        fontsize=8.8,
        labelpad=6,
        color="#1D3D52"
    )

    bar.outline.set_visible(False)

    patch = plt.Rectangle(
        (card_rect[0], card_rect[1]),
        card_rect[2],
        card_rect[3],
        transform=fig.transFigure,
        facecolor="#E8EFF5",
        edgecolor="#D1DDE6",
        linewidth=1.0
    )

    fig.patches.append(patch)

    fig.text(
        card_rect[0]+0.015,
        card_rect[1]+0.078,
        card_title,
        fontsize=10.5,
        fontweight="bold",
        color="#4B6477"
    )

    fig.text(
        card_rect[0]+0.015,
        card_rect[1]+0.051,
        card_value,
        fontsize=12,
        fontweight="bold",
        color="#173A52"
    )

    fig.text(
        card_rect[0]+0.015,
        card_rect[1]+0.024,
        card_city,
        fontsize=9,
        color="#415B6D"
    )

    return ax


# ============================================================
# 12. FOTOGRAMA CON DOS HEATMAPS
# ============================================================

def draw_snapshot(
    prob,
    stamp,
    index,
    total,
    abs_clims,
    delta_clims,
    info,
    work,
    baseline
):
    fig = plt.figure(
        figsize=(10.8, 13.5),
        dpi=100,
        facecolor="#F3F7FA"
    )

    percent = prob*100.0

    delta = (
        prob.astype(np.float64)
        - baseline.astype(np.float64)
    )*100.0

    def labels_near(lat, lon):
        points = []

        if CITY:
            points.append(
                min(
                    CITY,
                    key=lambda city: (
                        (city[1]-lat)**2
                        + (city[2]-lon)**2
                    )
                )
            )

        if CAPITAL_CITY:
            _, capital_lat, capital_lon = CAPITAL_CITY

            if (
                LAT0 <= capital_lat <= LAT1
                and LON0 <= capital_lon <= LON1
            ):
                if all(
                    city[0] != CAPITAL_CITY[0]
                    for city in points
                ):
                    points.append(CAPITAL_CITY)

        return points

    points = info.get(
        "delta_points",
        []
    )[:3]

    for point in points:
        row = int(
            np.argmin(
                np.abs(LATS-point["lat"])
            )
        )

        col = int(
            np.argmin(
                np.abs(LONS-point["lon"])
            )
        )

        actual = float(
            delta[row, col]
        )

        if (
            abs(actual-float(point["val"]))
            > max(1e-5, abs(actual)*1e-3)
        ):
            raise RuntimeError(
                "Marcador de aumento no corresponde "
                "a su fotograma."
            )

    left = draw_heatmap_panel(
        fig,
        [0.055, 0.27, 0.42, 0.57],
        np.ma.masked_less_equal(percent, 0),
        abs_clims,
        labels_near(
            info["prob_lat"],
            info["prob_lon"]
        ),
        [0.08, 0.22, 0.37, 0.016],
        (
            f"Por celda: ≥1 sismo M≥"
            f"{info['mag']:.1f} en 24 h"
        ),
        lambda value, pos: f"{value:.3g}%",
        [0.055, 0.08, 0.42, 0.105],
        "Coordenada de valor máximo",
        (
            f"{info['prob_val']:.4f}% · "
            f"{info['prob_lat']:+.2f}°, "
            f"{info['prob_lon']:+.2f}°"
        ),
        (
            f"Ciudad cercana: {info['prob_city']} "
            f"({info['prob_dist']:.0f} km)"
        )
    )

    right = draw_heatmap_panel(
        fig,
        [0.525, 0.27, 0.42, 0.57],
        np.maximum(delta, 0),
        delta_clims,
        [],
        [0.55, 0.22, 0.37, 0.016],
        "Aumento respecto al inicio (pp)",
        lambda value, pos: f"{value:.3g} pp",
        [0.525, 0.08, 0.42, 0.105],
        "Zonas de mayor aumento positivo",
        "",
        "",
        scale="power"
    )

    # No mostrar título principal ni contador superior.
    fig.text(
        0.265,
        0.863,
        "PROBABILIDAD ACUMULADA",
        ha="center",
        fontsize=11,
        fontweight="bold",
        color="#19374B"
    )

    fig.text(
        0.735,
        0.863,
        "AUMENTO ULTIMAS 24 H",
        ha="center",
        fontsize=11,
        fontweight="bold",
        color="#19374B"
    )

    fig.text(
        0.5,
        0.035,
        "MODELO EXPERIMENTAL · NO ES ALERTA SÍSMICA",
        ha="center",
        fontsize=10,
        fontweight="bold",
        color="#A13A33"
    )

    if CAPITAL_CITY:
        name, lat, lon = CAPITAL_CITY

        if (
            LAT0 <= lat <= LAT1
            and LON0 <= lon <= LON1
        ):
            right.plot(
                lon,
                lat,
                "o",
                ms=2.8,
                color="#152E3F",
                markeredgecolor="white",
                transform=ccrs.PlateCarree(),
                zorder=20
            )

            right.text(
                lon+0.12,
                lat+0.07,
                name,
                fontsize=7.2,
                color="#183143",
                transform=ccrs.PlateCarree(),
                zorder=21,
                bbox={
                    "facecolor": "white",
                    "edgecolor": "none",
                    "alpha": 0.76,
                    "pad": 1.0
                }
            )

    offsets = [
        (20, 23),
        (-23, 23),
        (20, -23)
    ]

    transform = (
        ccrs.PlateCarree()
        ._as_mpl_transform(right)
    )

    for rank, point in enumerate(points, 1):
        lon = float(point["lon"])
        lat = float(point["lat"])

        right.scatter(
            [lon],
            [lat],
            s=92,
            marker="o",
            facecolor="none",
            edgecolor="white",
            linewidth=2.1,
            transform=ccrs.PlateCarree(),
            zorder=25
        )

        dx, dy = offsets[
            (rank-1) % 3
        ]

        right.annotate(
            str(rank),
            xy=(lon, lat),
            xycoords=transform,
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=9,
            fontweight="bold",
            color="white",
            ha="center",
            va="center",
            zorder=26,
            arrowprops={
                "arrowstyle": "-",
                "color": "white",
                "lw": 1.5,
                "shrinkA": 2,
                "shrinkB": 5
            },
            bbox={
                "boxstyle": "circle,pad=.30",
                "facecolor": "#162D43",
                "edgecolor": "white",
                "linewidth": 1.2
            }
        )

        city = str(
            point.get("city")
            or "Localidad no identificada"
        )

        if len(city) > 22:
            city = city[:21].rstrip() + "…"

        fig.text(
            0.54,
            0.131-(rank-1)*0.022,
            (
                f"{rank}. {city} · "
                f"{point['val']:+.4f} pp"
            ),
            fontsize=8.2,
            color="#173A52",
            fontweight="bold",
            zorder=30
        )

    off = (
        Path(work)
        / f"frame_{index:03d}_off.png"
    )

    on = (
        Path(work)
        / f"frame_{index:03d}_on.png"
    )

    fig.savefig(
        off,
        dpi=fig.dpi,
        facecolor=fig.get_facecolor(),
        bbox_inches=None,
        pad_inches=0
    )

    stars = [
        (
            left,
            float(info["prob_lon"]),
            float(info["prob_lat"])
        )
    ]

    stars.extend(
        (
            right,
            float(point["lon"]),
            float(point["lat"])
        )
        for point in points
    )

    for ax, lon, lat in stars:
        ax.scatter(
            [lon],
            [lat],
            s=210,
            marker="*",
            c="#FFE55B",
            edgecolors="#142B3A",
            linewidths=1.3,
            transform=ccrs.PlateCarree(),
            zorder=40
        )

    fig.savefig(
        on,
        dpi=fig.dpi,
        facecolor=fig.get_facecolor(),
        bbox_inches=None,
        pad_inches=0
    )

    plt.close(fig)

    return on, off


# ============================================================
# 13. MÚSICA SINTETIZADA
# ============================================================

def music_wav(
    filepath,
    duration=30,
    sr=24000
):
    t = (
        np.arange(
            sr*duration,
            dtype=np.float64
        ) / sr
    )

    audio = np.zeros_like(t)

    chords = [
        (196.0, 246.94, 293.66),
        (174.61, 220.0, 261.63),
        (164.81, 196.0, 246.94),
        (146.83, 185.0, 220.0)
    ]

    for begin in range(0, duration, 4):
        local = t-begin

        active = (
            (local >= 0)
            & (local < 4)
        )

        envelope = (
            np.sin(
                np.pi
                * np.clip(local, 0, 4)
                / 4
            )**1.3
        )*active

        for frequency in chords[
            (begin//4) % len(chords)
        ]:
            phase = (
                2*np.pi*frequency*t
            )

            audio += (
                0.6*np.sin(phase)
                + 0.13*np.sin(phase*2)
            ) * envelope/3

    for beat in np.arange(
        0,
        duration,
        2
    ):
        tau = t-beat

        active = (
            (tau >= 0)
            & (tau < 0.55)
        )

        audio += (
            0.12
            * np.sin(2*np.pi*98*t)
            * np.exp(
                -9*np.maximum(tau, 0)
            )
            * active
        )

    audio *= (
        np.clip(t/1.2, 0, 1)
        * np.clip(
            (duration-t)/2.4,
            0,
            1
        )
    )

    peak = np.max(np.abs(audio))

    if peak > 0:
        audio *= 0.67/peak

    wavfile.write(
        str(filepath),
        sr,
        (
            np.clip(audio, -1, 1)
            * 32767
        ).astype(np.int16)
    )


# ============================================================
# 14. NARRACIÓN
# ============================================================

def narration(final_info):
    def percentage(value):
        value = min(
            100.0,
            max(0.0, float(value))
        )

        return (
            "100 por ciento"
            if value >= 100.0
            else (
                f"{value:.2f}".replace(".", ",")
                + " por ciento"
            )
        )

    areas = final_info.get(
        "delta_points",
        []
    )

    if areas:
        pieces = []

        for rank, point in enumerate(areas, 1):
            city = point.get(
                "city",
                "una zona sin ciudad identificada"
            )

            amount = (
                f"{point['val']:.4f}"
                .replace(".", ",")
            )

            pieces.append(
                f"La zona {rank} está cerca de {city}, "
                f"con un incremento de {amount} "
                "puntos porcentuales."
            )

        delta_text = " ".join(pieces)

        if len(areas) < 3:
            delta_text += (
                " No se identificaron otras zonas "
                "positivas suficientemente separadas."
            )

    else:
        delta_text = (
            "No se detectaron aumentos positivos "
            "durante este período."
        )

    return (
        "Te invitamos a revisar una actualización "
        "del riesgo sísmico en Chile "
        "para las últimas 24 horas. "

        "En el mapa de la izquierda se observa "
        "que el valor de probabilidad modelada "
        "más alto está cerca de "

        + str(final_info["prob_city"])

        + ", con "

        + percentage(final_info["prob_val"])

        + (
            f" para al menos un sismo de magnitud "
            f"{final_info['mag']:.1f} o superior "
            "para las próximas 24 horas. "

            "En el gráfico de la derecha en tanto, "
            "observamos las zonas donde la probabilidad "
            "modelada aumentó con mayor rapidez "
            "durante las últimas horas. "
        )

        + delta_text

        + (
            " IMPORTANTE: los resultados de este modelo son experimentales "
            "y no constituyen una alerta "
            "sísmica ni una predicción oficial."
        )
    )


# ============================================================
# 15. VOZ NATURAL
# ============================================================

def voice(text, work, name):
    path = Path(work)/name

    try:
        import edge_tts

        async def make():
            await edge_tts.Communicate(
                text,
                voice="es-CL-CatalinaNeural",
                rate=VOICE_RATE
            ).save(str(path))

        asyncio.run(make())

        if path.stat().st_size > 1000:
            return path

    except Exception as exc:
        log(
            "Edge TTS no disponible:",
            str(exc)[:100]
        )

    try:
        from gtts import gTTS

        gTTS(
            text,
            lang="es",
            tld="cl",
            slow=False
        ).save(str(path))

        if path.stat().st_size > 1000:
            return path

    except Exception as exc:
        log(
            "gTTS no disponible:",
            str(exc)[:100]
        )

    raise RuntimeError(
        "No se pudo generar voz; "
        "no se publicará vídeo sin narración."
    )


# ============================================================
# 16. ESCENA FINAL
# ============================================================

def create_outro_frame(work, visible):
    path = (
        Path(work)
        / (
            "outro_on.png"
            if visible
            else "outro_off.png"
        )
    )

    image = Image.new(
        "RGB",
        (W, H),
        "#000000"
    )

    draw = ImageDraw.Draw(image)

    if visible:
        hand = Image.new(
            "RGBA",
            (900, 900),
            (0, 0, 0, 0)
        )

        painter = ImageDraw.Draw(hand)

        outline = [
            (180,460),(280,460),(320,420),
            (360,345),(404,248),(446,170),
            (475,158),(512,176),(524,215),
            (520,280),(498,395),(650,395),
            (700,406),(731,437),(732,477),
            (718,511),(724,541),(711,578),
            (700,590),(704,617),(683,652),
            (658,664),(654,694),(622,732),
            (570,745),(390,745),(347,726),
            (285,685),(180,685)
        ]

        painter.line(
            outline,
            fill="#1877F2",
            width=34,
            joint="curve"
        )

        for x, y in outline:
            painter.ellipse(
                (
                    x-17,
                    y-17,
                    x+17,
                    y+17
                ),
                fill="#1877F2"
            )

        painter.line(
            (180,460,180,685),
            fill="#1877F2",
            width=34
        )

        painter.line(
            [(280,468),(280,686)],
            fill="#1877F2",
            width=26
        )

        hand = hand.resize(
            (440, 440),
            Image.Resampling.LANCZOS
        )

        image.paste(
            hand,
            ((W-440)//2, 160),
            hand
        )

    large_font = font(
        120,
        bold=True
    )

    for text, top in [
        ("Dale Like", 690),
        ("y Comenta!", 850)
    ]:
        bbox = draw.textbbox(
            (0, 0),
            text,
            font=large_font
        )

        text_width = bbox[2]-bbox[0]

        if text_width > W-100:
            raise RuntimeError(
                "Texto de cierre no cabe; "
                "verificar fuentes."
            )

        x = int(
            (W-text_width)/2 - bbox[0]
        )

        y = int(
            top-bbox[1]
        )

        draw.text(
            (x, y),
            text,
            font=large_font,
            fill="white"
        )

    image.save(path)

    return path


# ============================================================
# 17. SECUENCIA DE VIDEO SIN -vsync
# ============================================================

def frame_sequence_video(
    frame_paths,
    animation_seconds,
    summary_seconds,
    outro_seconds,
    work
):
    """
    Genera video CFR a 24 FPS usando image2.

    Los mapas parpadean, el último mapa permanece
    durante el resumen y después aparece el cierre.
    """

    all_paths = []
    frame_index = 0

    amount = round(
        animation_seconds*FPS
    )

    quotient, remainder = divmod(
        amount,
        len(frame_paths)
    )

    for index, (
        on_path,
        off_path
    ) in enumerate(frame_paths):
        repeats = (
            quotient
            + (1 if index < remainder else 0)
        )

        for _ in range(repeats):
            visible = (
                frame_index
                // max(1, FPS//2)
            ) % 2 == 0

            all_paths.append(
                on_path if visible else off_path
            )

            frame_index += 1

    final_on, final_off = (
        frame_paths[-1]
    )

    for j in range(
        round(summary_seconds*FPS)
    ):
        visible = (
            (frame_index+j)
            // max(1, FPS//2)
        ) % 2 == 0

        all_paths.append(
            final_on if visible else final_off
        )

    outro_on = create_outro_frame(
        work,
        True
    )

    outro_off = create_outro_frame(
        work,
        False
    )

    for j in range(
        round(outro_seconds*FPS)
    ):
        visible = (
            j // max(1, FPS//2)
        ) % 2 == 0

        all_paths.append(
            outro_on if visible else outro_off
        )

    expected = (
        round(animation_seconds*FPS)
        + round(summary_seconds*FPS)
        + round(outro_seconds*FPS)
    )

    if len(all_paths) != expected:
        raise RuntimeError(
            "Cantidad de fotogramas inconsistente."
        )

    sequence_dir = (
        Path(work)/"secuencia"
    )

    sequence_dir.mkdir()

    for index, source in enumerate(all_paths):
        link = (
            sequence_dir
            / f"f_{index:06d}.png"
        )

        try:
            os.symlink(
                source,
                link
            )

        except (OSError, NotImplementedError):
            shutil.copyfile(
                source,
                link
            )

    silent = (
        Path(work)/"video_sin_audio.mp4"
    )

    # FFmpeg sin -vsync y sin -fps_mode.
    ffmpeg(
        "-framerate", str(FPS),
        "-start_number", "0",
        "-i", sequence_dir/"f_%06d.png",
        "-frames:v", str(expected),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        silent
    )

    result = probe(silent)

    duration = float(
        result["format"]["duration"]
    )

    target = expected/FPS

    if abs(duration-target) > max(
        0.35,
        2/FPS
    ):
        raise RuntimeError(
            "Duración animación incorrecta: "
            f"{duration} vs {target} segundos."
        )

    log(
        "VIDEO ANIMADO SIN AUDIO:",
        silent,
        "duración:",
        round(duration, 2),
        "segundos",
        "fotogramas:",
        expected
    )

    return silent


# ============================================================
# 18. CREAR VIDEO CON AUDIO SIN SILENCIOS LARGOS
# ============================================================

def create_video(
    times,
    maps,
    maxima,
    final_info
):
    binary("ffmpeg")
    binary("ffprobe")

    OUT.mkdir(
        parents=True,
        exist_ok=True
    )

    with tempfile.TemporaryDirectory(
        prefix="sismonauta_etas_"
    ) as temp_dir:

        work = Path(temp_dir)

        positives = [
            frame[frame > 0]
            for frame in maps
            if np.any(frame > 0)
        ]

        if not positives:
            raise RuntimeError(
                "Mapas sin valores positivos."
            )

        values = np.concatenate(
            positives
        )

        vmax = float(
            values.max()*100.0
        )

        vmin = max(
            float(
                np.percentile(values, 2)*100.0
            ),
            vmax/1000.0,
            1e-10
        )

        if vmin >= vmax:
            vmin = max(
                vmax/1000.0,
                1e-10
            )

        abs_clims = (
            vmin,
            vmax
        )

        baseline = maps[0]

        delta_fields = [
            (
                frame.astype(np.float64)
                - baseline.astype(np.float64)
            )*100.0
            for frame in maps
        ]

        dmax = max(
            0.0,
            max(
                float(np.max(delta))
                for delta in delta_fields
            )
        )

        delta_clims = (
            0.0,
            max(dmax, 1e-12)
        )

        log(
            "Escala JET probabilidad:",
            abs_clims,
            "| aumento:",
            delta_clims
        )

        frames = []
        last_info = None

        for index, (
            stamp,
            prob,
            meta
        ) in enumerate(
            zip(times, maps, maxima)
        ):
            prob_dist, prob_city = nearest_city(
                meta["prob_lat"],
                meta["prob_lon"]
            )

            delta_dist, delta_city = nearest_city(
                meta["delta_lat"],
                meta["delta_lon"]
            )

            info = dict(meta)
            info["delta_points"] = []

            for point in meta.get(
                "delta_points",
                []
            ):
                distance, city = nearest_city(
                    point["lat"],
                    point["lon"]
                )

                info["delta_points"].append(
                    dict(
                        point,
                        city=city,
                        dist=(
                            distance
                            if distance is not None
                            else 0.0
                        )
                    )
                )

            info.update({
                "prob_city": prob_city,
                "prob_dist": (
                    prob_dist
                    if prob_dist is not None
                    else 0.0
                ),
                "delta_city": delta_city,
                "delta_dist": (
                    delta_dist
                    if delta_dist is not None
                    else 0.0
                ),
                "mag": final_info["mag"]
            })

            frames.append(
                draw_snapshot(
                    prob,
                    stamp,
                    index,
                    len(times),
                    abs_clims,
                    delta_clims,
                    info,
                    work,
                    baseline
                )
            )

            if index == len(times)-1:
                last_info = info

            if (
                (index+1) % 5 == 0
                or index+1 == len(times)
            ):
                log(
                    f"Fotogramas renderizados: "
                    f"{index+1}/{len(times)}"
                )

        if last_info is None:
            raise RuntimeError(
                "Falta mapa final."
            )

        for plotted, computed in zip(
            last_info["delta_points"],
            maxima[-1]["delta_points"]
        ):
            if not (
                plotted["lat"] == computed["lat"]
                and plotted["lon"] == computed["lon"]
                and np.isclose(
                    plotted["val"],
                    computed["val"]
                )
            ):
                raise RuntimeError(
                    "Marcadores finales no coinciden "
                    "con cálculo."
                )

        narration_text = narration(
            last_info
        )

        log(
            "\nNARRACIÓN FINAL:\n"
            + narration_text
            + "\n"
        )

        marker = (
            "En el gráfico de la derecha en tanto,"
        )

        if marker not in narration_text:
            raise RuntimeError(
                "No se encontró separador de narración."
            )

        first_text, second_text = (
            narration_text.split(marker, 1)
        )

        voice_files = [
            voice(
                first_text.strip(),
                work,
                "voz_animacion.mp3"
            ),
            voice(
                (marker+second_text).strip(),
                work,
                "voz_zonas_ultimo_mapa.mp3"
            ),
            voice(
                "Dale me gusta y comparte para "
                "seguir recibiendo esta importante "
                "información!",
                work,
                "voz_cierre.mp3"
            )
        ]

        # Voz natural: convertir a WAV sin atempo.
        prepared = []

        for source, name in zip(
            voice_files,
            (
                "voz_animacion.wav",
                "voz_zonas.wav",
                "voz_cierre.wav"
            )
        ):
            destination = work/name

            ffmpeg(
                "-i", source,
                "-ar", "24000",
                "-ac", "1",
                destination
            )

            prepared.append(destination)

        first, second, outro = prepared

        # Cada segmento visual sigue su propia voz.
        # La pausa adicional es de sólo 0,25 s.
        def section_duration(
            audio,
            pause=0.25
        ):
            measured = audio_duration(audio)

            if (
                not math.isfinite(measured)
                or measured <= 0
            ):
                raise RuntimeError(
                    "Duración de voz inválida: "
                    + str(audio)
                )

            return (
                math.ceil(
                    (measured+pause)*FPS
                )/FPS
            )

        animation_seconds = section_duration(
            first
        )

        summary_seconds = section_duration(
            second
        )

        outro_seconds = section_duration(
            outro,
            0.35
        )

        total_seconds = (
            animation_seconds
            + summary_seconds
            + outro_seconds
        )

        log(
            "VOZ NATURAL:",
            f"animación={animation_seconds:.2f}s",
            f"zonas={summary_seconds:.2f}s",
            f"cierre={outro_seconds:.2f}s",
            f"total={total_seconds:.2f}s"
        )

        spoken = (
            work/"narracion_completa.wav"
        )

        audio_filter = (
            "[0:a]aformat=sample_rates=24000:"
            "channel_layouts=mono,"
            "apad,"
            f"atrim=duration={animation_seconds:.6f},"
            "asetpts=PTS-STARTPTS[a0];"

            "[1:a]aformat=sample_rates=24000:"
            "channel_layouts=mono,"
            "apad,"
            f"atrim=duration={summary_seconds:.6f},"
            "asetpts=PTS-STARTPTS[a1];"

            "[2:a]aformat=sample_rates=24000:"
            "channel_layouts=mono,"
            "apad,"
            f"atrim=duration={outro_seconds:.6f},"
            "asetpts=PTS-STARTPTS[a2];"

            "[a0][a1][a2]"
            "concat=n=3:v=0:a=1[spoken]"
        )

        ffmpeg(
            "-i", first,
            "-i", second,
            "-i", outro,
            "-filter_complex", audio_filter,
            "-map", "[spoken]",
            "-ar", "24000",
            "-ac", "1",
            spoken
        )

        music = (
            work/"musica.wav"
        )

        music_wav(
            music,
            duration=math.ceil(total_seconds)
        )

        silent = frame_sequence_video(
            frames,
            animation_seconds,
            summary_seconds,
            outro_seconds,
            work
        )

        mixing_filter = (
            "[2:a]aformat=sample_fmts=fltp:"
            "channel_layouts=mono,"
            "volume=0.37[music];"

            "[1:a]aformat=sample_fmts=fltp:"
            "channel_layouts=mono,"
            "volume=1.0,"
            "asplit=2[sc][speech];"

            "[music][sc]"
            "sidechaincompress="
            "threshold=0.025:"
            "ratio=6:"
            "attack=25:"
            "release=350[duck];"

            "[duck][speech]"
            "amix=inputs=2:"
            "duration=longest:"
            "normalize=0,"

            f"atrim=duration={total_seconds},"
            "asetpts=N/SR/TB,"
            "alimiter=limit=0.92[aout]"
        )

        video = (
            OUT/ORIGINAL_VIDEO_NAME
        )

        video.unlink(
            missing_ok=True
        )

        ffmpeg(
            "-i", silent,
            "-i", spoken,
            "-stream_loop", "-1",
            "-i", music,
            "-filter_complex", mixing_filter,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-t", str(total_seconds),
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-movflags", "+faststart",
            video
        )

        if (
            not video.is_file()
            or video.stat().st_size < 10000
        ):
            raise RuntimeError(
                "Video original incompleto."
            )

        result = probe(video)

        if abs(
            float(result["format"]["duration"])
            - total_seconds
        ) > 1.0:
            raise RuntimeError(
                "Duración final no coincide."
            )

        log(
            "VIDEO ORIGINAL FINAL:",
            video
        )

        return video


# ============================================================
# 19. EJECUCIÓN CIENTÍFICA
# ============================================================

def run_scientific_etas():
    global CUTOFF

    CUTOFF = pd.Timestamp.now(
        tz="UTC"
    )

    log(
        f"Corte del catálogo: "
        f"{CUTOFF:%d/%m/%Y %H:%M} UTC"
    )

    setup_country()
    df = catalog_usgs()

    training = df[
        df.time < (
            CUTOFF
            - pd.Timedelta(
                hours=LOOKBACK_HOURS
            )
        )
    ].copy()

    if len(training) < 200:
        raise RuntimeError(
            "Catálogo histórico ETAS insuficiente."
        )

    mc, b = estimate_mc_b(
        training
    )

    magnitude = max(
        REQUESTED_M,
        mc
    )

    if magnitude > REQUESTED_M:
        log(
            f"AVISO: Mc={mc:.2f}. "
            f"Se usa M≥{magnitude:.2f}, "
            "no M≥4."
        )

    log(
        f"Mc={mc:.2f}; "
        f"b={b:.3f}; "
        f"magnitud objetivo M≥{magnitude:.2f}"
    )

    theta = fit_etas(
        training,
        mc
    )

    log(
        "Parámetros ETAS:",
        theta
    )

    times, maps, maxima = evolution_24h(
        df,
        theta,
        mc,
        b,
        magnitude
    )

    if (
        len(times) != 25
        or len(maps) != 25
    ):
        raise RuntimeError(
            "No se produjeron exactamente 25 mapas."
        )

    final = maxima[-1]

    prob_dist, prob_city = nearest_city(
        final["prob_lat"],
        final["prob_lon"]
    )

    delta_dist, delta_city = nearest_city(
        final["delta_lat"],
        final["delta_lon"]
    )

    points = []

    for point in final["delta_points"]:
        distance, city = nearest_city(
            point["lat"],
            point["lon"]
        )

        points.append(
            dict(
                point,
                city=city,
                dist=(
                    distance
                    if distance is not None
                    else 0.0
                )
            )
        )

    info = dict(
        final,
        delta_points=points,
        prob_city=prob_city,
        prob_dist=(
            prob_dist
            if prob_dist is not None
            else 0.0
        ),
        delta_city=delta_city,
        delta_dist=(
            delta_dist
            if delta_dist is not None
            else 0.0
        ),
        mag=magnitude
    )

    log(
        "MÁXIMO ACTUAL MODELADO:",
        f"{info['prob_lat']:.3f}°,",
        f"{info['prob_lon']:.3f}°",
        f"{info['prob_val']:.6f}%",
        prob_city
    )

    log(
        "MAYOR AUMENTO:",
        f"{info['delta_lat']:.3f}°,",
        f"{info['delta_lon']:.3f}°",
        f"{info['delta_val']:.6f} pp",
        delta_city
    )

    log(
        "Valores por celda, no probabilidades "
        "municipales ni alerta oficial."
    )

    video = create_video(
        times,
        maps,
        maxima,
        info
    )

    metadata = {
        "country": COUNTRY_NAME,
        "cutoff_utc": str(CUTOFF),
        "start_catalog": START,
        "events_catalog": len(df),
        "mc": mc,
        "b": b,
        "magnitude_target": magnitude,
        "etas_parameters": theta,
        "grid_degrees": GRID,
        "grid_cells": int(XX.size),
        "snapshots": 25,
        "final": info,
        "city_source": CITY_SOURCE,
        "disclaimer": (
            "Modelo experimental no calibrado "
            "ni validado prospectivamente. "
            "No es alerta."
        )
    }

    atomic_json(
        OUT/"modelo_etas_resultados.json",
        metadata
    )

    return video, metadata


# ============================================================
# 20. REEL 1080x1920
# ============================================================

def make_reel(video):
    destination = (
        OUT/REEL_NAME
    )

    destination.unlink(
        missing_ok=True
    )

    ffmpeg(
        "-i", video,
        "-vf",
        (
            "scale=1080:1920:"
            "force_original_aspect_ratio=decrease,"
            "pad=1080:1920:"
            "(ow-iw)/2:(oh-ih)/2:"
            "color=0x071727,"
            "setsar=1"
        ),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-ar", "48000",
        "-b:a", "128k",
        "-movflags", "+faststart",
        destination
    )

    result = probe(destination)

    video_stream = next(
        item
        for item in result["streams"]
        if item.get("codec_type") == "video"
    )

    if (
        video_stream.get("width"),
        video_stream.get("height")
    ) != (1080, 1920):
        raise RuntimeError(
            "Reel no es 1080x1920."
        )

    log(
        "REEL VERTICAL:",
        destination
    )

    return destination


# ============================================================
# 21. API HTTP
# ============================================================

def api(
    method,
    url,
    *,
    token=None,
    timeout=(20, 180),
    headers=None,
    **kwargs
):
    request_headers = dict(
        headers or {}
    )

    if token:
        request_headers["Authorization"] = (
            "Bearer " + token
        )

    response = HTTP.request(
        method,
        url,
        headers=request_headers,
        timeout=timeout,
        **kwargs
    )

    try:
        body = response.json()
    except ValueError:
        body = {}

    if not response.ok:
        error = (
            body.get("error")
            if isinstance(body, dict)
            else {}
        )

        message = (
            error.get("message")
            if isinstance(error, dict)
            else response.reason
        )

        raise RuntimeError(
            f"HTTP {response.status_code}: "
            f"{str(message)[:240]}"
        )

    if not isinstance(body, dict):
        raise RuntimeError(
            "API respondió JSON inesperado."
        )

    return body


def github_api(
    method,
    suffix,
    **kwargs
):
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    headers.update(
        kwargs.pop("headers", {})
    )

    return api(
        method,
        GH_API+suffix,
        token=os.environ["GH_TOKEN"],
        headers=headers,
        **kwargs
    )


# ============================================================
# 22. HISTORIAL INDIVIDUAL POR EJECUCIÓN
# ============================================================

def state_path(run_id=None):
    """
    Un archivo remoto diferente por ejecución.

    Permite publicaciones múltiples por día sin
    sobrescribir el estado de publicaciones anteriores.

    Ejemplo:
      sismonauta_etas/runs/123456_attempt1.json
    """

    raw = str(
        run_id
        or os.getenv("GITHUB_RUN_ID")
        or datetime.now(
            timezone.utc
        ).strftime("%Y%m%dT%H%M%S%f")
    )

    safe = "".join(
        ch
        for ch in raw
        if (
            ch.isalnum()
            or ch in ("-", "_")
        )
    )

    attempt = "".join(
        ch
        for ch in str(
            os.getenv("GITHUB_RUN_ATTEMPT", "1")
        )
        if ch.isdigit()
    ) or "1"

    if not safe:
        raise RuntimeError(
            "Identificador de ejecución vacío."
        )

    return (
        f"sismonauta_etas/runs/"
        f"{safe}_attempt{attempt}.json"
    )


def state_read(path):
    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    branch = os.getenv(
        "GITHUB_REF_NAME",
        "main"
    )

    url = (
        f"{GH_API}/repos/{repo}/contents/"
        f"{quote(path, safe='/')}"
    )

    response = HTTP.get(
        url,
        params={"ref": branch},
        headers={
            "Authorization": (
                "Bearer "
                + os.environ["GH_TOKEN"]
            ),
            "Accept": (
                "application/vnd.github+json"
            )
        },
        timeout=(15, 65)
    )

    if response.status_code == 404:
        return {}, None

    response.raise_for_status()

    data = response.json()

    return (
        json.loads(
            base64.b64decode(
                data["content"]
            ).decode("utf-8")
        ),
        data["sha"]
    )


def state_save(path, data):
    """
    Guarda el estado de esta ejecución.

    No aplica restricciones de publicaciones
    diarias ni sobrescribe registros de otros runs.
    """

    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    branch = os.getenv(
        "GITHUB_REF_NAME",
        "main"
    )

    for attempt in range(4):
        previous, sha = state_read(path)

        if (
            previous
            and previous.get("run_id")
            not in (
                None,
                data.get("run_id")
            )
        ):
            raise RuntimeError(
                "Colisión de identificador de ejecución. "
                "No se sobrescribirá un estado ajeno."
            )

        payload = {
            "message": (
                "SISMONAUTA ETAS Chile: "
                "actualizar estado de ejecución"
            ),
            "content": base64.b64encode(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                    default=str
                ).encode("utf-8")
            ).decode("ascii"),
            "branch": branch
        }

        if sha:
            payload["sha"] = sha

        try:
            github_api(
                "PUT",
                (
                    f"/repos/{repo}/contents/"
                    f"{quote(path, safe='/')}"
                ),
                json=payload
            )

            return

        except RuntimeError as exc:
            if (
                "HTTP 409" not in str(exc)
                and "HTTP 422" not in str(exc)
            ):
                raise

            if attempt == 3:
                raise

            time.sleep(
                2*(attempt+1)
            )


# ============================================================
# 23. VERIFICAR CUENTAS
# ============================================================

def verify_accounts():
    required = [
        "GH_TOKEN",
        "GITHUB_REPOSITORY",
        "INSTAGRAM_TOKEN",
        "INSTAGRAM_USER_ID",
        "FACEBOOK_PAGE_ID",
        "FACEBOOK_PAGE_TOKEN"
    ]

    missing = [
        key
        for key in required
        if not os.getenv(key)
    ]

    if missing:
        raise RuntimeError(
            "Faltan variables: "
            + ", ".join(missing)
        )

    uid = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    ig = api(
        "GET",
        f"{IG_BASE}/{GRAPH_VERSION}/{uid}",
        token=os.environ["INSTAGRAM_TOKEN"],
        params={"fields": "id,username"}
    )

    if (
        str(ig.get("id")) != uid
        or str(
            ig.get("username", "")
        ).lower() != "sismonauta"
    ):
        raise RuntimeError(
            "Token Instagram no corresponde "
            "a @sismonauta."
        )

    page = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    fb = api(
        "GET",
        f"{FB_BASE}/{page}",
        token=os.environ["FACEBOOK_PAGE_TOKEN"],
        params={"fields": "id,name"}
    )

    if (
        str(fb.get("id")) != page
        or "sismonauta" not in str(
            fb.get("name", "")
        ).lower()
    ):
        raise RuntimeError(
            "Token Facebook no corresponde "
            "a Sismonauta."
        )

    repo = github_api(
        "GET",
        "/repos/"
        + os.environ["GITHUB_REPOSITORY"]
    )

    if repo.get("private"):
        raise RuntimeError(
            "El repositorio debe ser público "
            "para GitHub Releases."
        )

    log(
        "Cuentas verificadas: "
        "@sismonauta / Sismonauta"
    )


# ============================================================
# 24. SUBIDA A GITHUB RELEASES
# ============================================================

def release_video(
    video,
    persist,
    target
):
    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    tag = (
        "sismonauta-etas-"
        + datetime.now(
            timezone.utc
        ).strftime("%Y%m%d-%H%M%S")
        + "-"
        + str(
            os.getenv(
                "GITHUB_RUN_ID",
                "local"
            )
        )
    )

    release = github_api(
        "POST",
        f"/repos/{repo}/releases",
        json={
            "tag_name": tag,
            "target_commitish": os.getenv(
                "GITHUB_REF_NAME",
                "main"
            ),
            "name": "SISMONAUTA ETAS Chile",
            "draft": False,
            "prerelease": True,
            "body": (
                "Modelo experimental. "
                "No constituye alerta sísmica."
            )
        }
    )

    target["release_id"] = str(
        release["id"]
    )

    persist()

    upload_url = (
        release["upload_url"].split("{")[0]
        + "?name="
        + quote(video.name)
    )

    with video.open("rb") as stream:
        asset = api(
            "POST",
            upload_url,
            token=os.environ["GH_TOKEN"],
            timeout=(30, 900),
            headers={
                "Accept": (
                    "application/vnd.github+json"
                ),
                "Content-Type": "video/mp4"
            },
            data=stream
        )

    url = asset.get(
        "browser_download_url"
    )

    if (
        not url
        or not url.startswith(
            "https://github.com/"
        )
    ):
        raise RuntimeError(
            "GitHub no devolvió URL pública "
            "de Release."
        )

    target["video_url"] = url

    persist()

    return url


# ============================================================
# 25. PUBLICAR INSTAGRAM
# ============================================================

def publish_instagram(
    reel,
    caption,
    target,
    persist
):
    token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    uid = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    base = (
        f"{IG_BASE}/{GRAPH_VERSION}"
    )

    mode = os.getenv(
        "IG_UPLOAD_MODE",
        "url"
    ).lower()

    if mode not in (
        "url",
        "resumable"
    ):
        raise RuntimeError(
            "IG_UPLOAD_MODE inválido."
        )

    target["status"] = "uncertain"
    persist()

    data = {
        "media_type": "REELS",
        "caption": caption[:2200],
        "share_to_feed": "true"
    }

    if mode == "url":
        data["video_url"] = release_video(
            reel,
            persist,
            target
        )
    else:
        data["upload_type"] = "resumable"

    container = api(
        "POST",
        f"{base}/{uid}/media",
        token=token,
        data=data
    )

    container_id = str(
        container["id"]
    )

    target["container_id"] = container_id

    persist()

    if mode == "resumable":
        upload_url = (
            container.get("uri")
            or (
                "https://rupload.facebook.com/"
                f"ig-api-upload/{GRAPH_VERSION}/"
                f"{container_id}"
            )
        )

        parsed = urlparse(upload_url)

        if (
            parsed.scheme != "https"
            or parsed.hostname
            != "rupload.facebook.com"
        ):
            raise RuntimeError(
                "URL de subida Instagram no confiable."
            )

        with reel.open("rb") as stream:
            response = HTTP.post(
                upload_url,
                headers={
                    "Authorization": (
                        "OAuth " + token
                    ),
                    "offset": "0",
                    "file_size": str(
                        reel.stat().st_size
                    ),
                    "Content-Type": (
                        "application/octet-stream"
                    )
                },
                data=stream,
                timeout=(30, 900)
            )

            response.raise_for_status()

    for attempt in range(30):
        status = api(
            "GET",
            f"{base}/{container_id}",
            token=token,
            params={
                "fields": "status_code,status"
            }
        )

        code = status.get(
            "status_code"
        )

        log(
            "Instagram procesamiento:",
            code,
            f"{attempt+1}/30"
        )

        if code == "FINISHED":
            break

        if code in (
            "ERROR",
            "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram no procesó video: "
                + str(status)[:160]
            )

        time.sleep(20)

    else:
        raise RuntimeError(
            "Instagram no confirmó procesamiento. "
            "Revisar Meta antes de reintentar."
        )

    published = api(
        "POST",
        f"{base}/{uid}/media_publish",
        token=token,
        data={
            "creation_id": container_id
        }
    )

    media_id = str(
        published.get("id") or ""
    )

    if not media_id.isdigit():
        raise RuntimeError(
            "Instagram no devolvió ID válido."
        )

    target.update({
        "status": "published",
        "id": media_id
    })

    persist()

    log(
        "INSTAGRAM REEL ACEPTADO:",
        media_id
    )


# ============================================================
# 26. PUBLICAR FACEBOOK
# ============================================================

def publish_facebook(
    video,
    caption,
    target,
    persist
):
    target["status"] = "uncertain"
    persist()

    page_id = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    token = os.environ[
        "FACEBOOK_PAGE_TOKEN"
    ]

    with video.open("rb") as stream:
        result = api(
            "POST",
            f"{FB_BASE}/{page_id}/videos",
            token=token,
            timeout=(30, 900),
            data={
                "description": caption[:5000],
                "title": (
                    "SISMONAUTA | "
                    "ETAS Chile experimental"
                ),
                "published": "true"
            },
            files={
                "source": (
                    video.name,
                    stream,
                    "video/mp4"
                )
            }
        )

    media_id = str(
        result.get("id") or ""
    )

    if not media_id.isdigit():
        raise RuntimeError(
            "Facebook no devolvió ID válido."
        )

    target.update({
        "status": "published",
        "id": media_id
    })

    persist()

    log(
        "FACEBOOK VIDEO ACEPTADO:",
        media_id
    )


# ============================================================
# 27. PIE DE PUBLICACIÓN CON FECHA DE CHILE
# ============================================================

def caption(metadata):
    cutoff = pd.Timestamp(
        metadata["cutoff_utc"]
    )

    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(
            "UTC"
        )

    local = cutoff.tz_convert(
        "America/Santiago"
    )

    day = local.strftime(
        "%d/%m/%Y"
    )

    clock = local.strftime(
        "%H:%M"
    )

    magnitude = float(
        metadata["magnitude_target"]
    )

    return (
        f"🌎 SISMONAUTA | Actualización: "
        f"{day}, {clock} h (Chile)\n\n"

        "ETAS (Epidemic-Type Aftershock Sequence) "
        "es un modelo estadístico que evalúa "
        "la tasa sísmica de fondo y la "
        "influencia temporal de los terremotos, "
        "detectando patrones probabilísticos.\n\n"

        "El mapa de la izquierda muestra el riesgo sísmico "
        "acumulado para las próximas 24 horas. "
        "El mapa derecho en tanto, muestra las zonas"
        "donde la probabilidad de riesgo aumentó "
        "con mayor rapidez en las últimas horas.\n\n "

        "⚠️ IMPORTANTE: ETAS es un modelo experimental, "
        "no constituye una predicción operativa de terremotos "
        "ni una alerta sísmica oficial.\n\n"

        "#Sismonauta #Chile #Sismologia "
        "#ETAS #Terremotos #Ciencia"
    )


# ============================================================
# 28. PROGRAMA PRINCIPAL
# ============================================================

def main():
    global OUT

    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA ETAS Chile unificado"
        )
    )

    parser.add_argument(
        "--out",
        default=os.getenv(
            "ETAS_OUTPUT_DIR",
            "output_etas"
        )
    )

    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Publicar en Instagram y Facebook. "
            "Permite múltiples publicaciones diarias."
        )
    )

    parser.add_argument(
        "--original",
        default="",
        help=argparse.SUPPRESS
    )

    args = parser.parse_args()

    OUT = Path(
        args.out
    ).resolve()

    OUT.mkdir(
        parents=True,
        exist_ok=True
    )

    binary("ffmpeg")
    binary("ffprobe")

    receipt = {
        "status": "starting",
        "country": COUNTRY_NAME,
        "created_at": utcnow(),
        "run_id": (
            os.getenv("GITHUB_RUN_ID")
            or datetime.now(
                timezone.utc
            ).strftime("%Y%m%dT%H%M%S%f")
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

    # IMPORTANTE:
    # Cada ejecución escribe un registro distinto.
    # No hay bloqueo por día UTC.
    run_state_path = state_path(
        receipt["run_id"]
    )

    def persist():
        atomic_json(
            OUT/"estado_publicacion.json",
            receipt
        )

        if args.publish:
            state_save(
                run_state_path,
                receipt
            )

    try:
        if args.publish:
            verify_accounts()

            log(
                "MODO PRUEBAS ACTIVADO: "
                "se permiten varias publicaciones al día."
            )

            log(
                "Registro individual GitHub:",
                run_state_path
            )

        receipt["status"] = "generating"
        persist()

        # ------------------------------------
        # CALCULAR MODELO Y GENERAR MP4
        # ------------------------------------

        video, metadata = run_scientific_etas()

        duration = float(
            probe(video)["format"]["duration"]
        )

        if not 3 <= duration <= 900:
            raise RuntimeError(
                "Duración no compatible con Reel: "
                f"{duration:.1f}s"
            )

        receipt.update({
            "duration_seconds": duration,
            "video_file": str(video),
            "scientific": {
                key: metadata[key]
                for key in (
                    "snapshots",
                    "mc",
                    "b",
                    "magnitude_target"
                )
            }
        })

        persist()

        # ------------------------------------
        # GENERAR REEL INSTAGRAM
        # ------------------------------------

        reel = make_reel(
            video
        )

        receipt["reel_file"] = str(reel)

        # ------------------------------------
        # MODO SIN PUBLICAR
        # ------------------------------------

        if not args.publish:
            receipt["status"] = "preview_only"
            persist()

            log(
                "VISTA PREVIA: video original",
                video,
                "| Reel",
                reel
            )

            return

        # ------------------------------------
        # PUBLICAR EN AMBAS PLATAFORMAS
        # ------------------------------------

        receipt["status"] = "publishing"
        persist()

        errors = []

        destinations = [
            (
                "instagram",
                publish_instagram,
                reel
            ),
            (
                "facebook",
                publish_facebook,
                video
            )
        ]

        for name, callback, filepath in destinations:
            target = receipt["targets"][name]

            try:
                callback(
                    filepath,
                    caption(metadata),
                    target,
                    persist
                )

            except Exception as exc:
                target["error"] = str(exc)[:400]

                if target.get("status") != "published":
                    target["status"] = "uncertain"

                persist()

                errors.append(
                    name + ": " + str(exc)
                )

        if errors:
            receipt["status"] = (
                "partial_or_uncertain"
            )

            persist()

            raise RuntimeError(
                " | ".join(errors)
            )

        receipt["status"] = "published"
        persist()

        log(
            "PUBLICACIONES ACEPTADAS POR META:",
            "Instagram",
            receipt["targets"]["instagram"]["id"],
            "| Facebook",
            receipt["targets"]["facebook"]["id"]
        )

    except Exception as exc:
        uncertain = any(
            target.get("status") in (
                "published",
                "uncertain"
            )
            for target in receipt[
                "targets"
            ].values()
        )

        if receipt["status"] != "published":
            receipt["status"] = (
                "partial_or_uncertain"
                if uncertain
                else "error"
            )

        receipt["error"] = str(exc)[:500]

        atomic_json(
            OUT/"estado_publicacion.json",
            receipt
        )

        log(
            "ERROR:",
            str(exc)
        )

        raise


# ============================================================
# 29. INICIO
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        print(
            "SISMONAUTA ETAS ERROR:",
            str(exc),
            file=sys.stderr,
            flush=True
        )

        sys.exit(1)
