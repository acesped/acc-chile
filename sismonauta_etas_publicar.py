
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA | ETAS CHILE | MODELO Y PUBLICADOR UNIFICADOS

Un solo archivo Python para:
  - Obtener el catálogo sísmico USGS desde 2018.
  - Delimitar Chile con Natural Earth.
  - Identificar ciudades mediante GeoNames.
  - Estimar Mc, parámetro b y ajustar ETAS con Numba.
  - Reconstruir 25 mapas horarios.
  - Mostrar dos heatmaps JET simultáneos.
  - Localizar tres incrementos positivos separados.
  - Generar animación con marcadores parpadeantes.
  - Generar voz en español chileno y música original.
  - Incorporar un cierre "Dale Like y Comenta!".
  - Crear video 4:5 y Reel 9:16.
  - Publicar en Instagram y Facebook opcionalmente.
  - Registrar estados en GitHub para evitar duplicados.

EJEMPLOS:
  python sismonauta_etas_publicar.py --out output_etas

  python sismonauta_etas_publicar.py --out output_etas --publish

REQUISITOS:
  numpy pandas scipy matplotlib requests cartopy numba Pillow
  edge-tts gTTS pycountry geopy shapely
  ffmpeg ffprobe

IMPORTANTE:
  ETAS EXPERIMENTAL, NO CALIBRADO NI VALIDADO PROSPECTIVAMENTE.
  NO CONSTITUYE PREDICCIÓN NI ALERTA SÍSMICA OFICIAL.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
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
from urllib.parse import quote

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

from shapely.geometry import Point, box as shapely_box
from shapely.ops import unary_union
from shapely.prepared import prep

import pycountry

from geopy.geocoders import Nominatim


# ============================================================
# 1. CONFIGURACIÓN GLOBAL
# ============================================================

COUNTRY_NAME = "Chile"
COUNTRY_ISO = "CHL"

START = "2018-01-01"

DOWNLOAD_M = 3.0
REQUESTED_M = 4.0

HORIZON_DAYS = 1.0

LOOKBACK_HOURS = 24
SNAPSHOT_MINUTES = 60

VIDEO_SECONDS = 30

VOICE_RATE = "+35%"
AUDIO_TEMPO = 1.25

FPS = 24

FIT_N = 1300

TRIGGER_DAYS = 365
TRIGGER_MAX = 2500

MAX_GRID_CELLS = 15000

W = 1080
H = 1350

OUTPUT_NAME = "ETAS_DOBLE_HEATMAP_CON_LIKE.mp4"
REEL_NAME = "sismonauta_etas_reel_9x16.mp4"

OUTPUT_DIR = Path(
    os.getenv("ETAS_OUTPUT_DIR", "output_etas")
).resolve()

OUTPUT_MP4 = OUTPUT_DIR / OUTPUT_NAME

CUTOFF = None

COUNTRY_GEOM = None

LAT0 = LAT1 = None
LON0 = LON1 = None

GRID = None
LATS = LONS = None
XX = YY = None

CITY = []
CITY_SOURCE = ""

CAPITAL_CITY = None

_DETAIL_CACHE = None

GRAPH_VERSION = os.getenv(
    "META_GRAPH_VERSION", "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = (
    f"https://graph.facebook.com/{GRAPH_VERSION}"
)

GITHUB_API = "https://api.github.com"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "SismonautaETASChile/3.0"
})

ETAS_TIMEOUT = int(
    os.getenv("ETAS_TIMEOUT_SECONDS", "16500")
)

# El límite temporal de GitHub Actions se establece en el YAML.
# Este timeout se aplica a las solicitudes de red individuales.


# ============================================================
# 2. UTILIDADES
# ============================================================

def log(*args):
    print(*args, flush=True)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def ensure_output():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    probe = OUTPUT_DIR / ".test_write"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()

    return OUTPUT_DIR


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(path.suffix + ".tmp")

    tmp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tmp.replace(path)


def check_binary(name):
    if not shutil.which(name):
        raise RuntimeError(
            f"No está disponible {name}. "
            "Revisar el paso de instalación del YAML."
        )


def ffmpeg(*args):
    check_binary("ffmpeg")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        *map(str, args)
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )

    if proc.returncode:
        raise RuntimeError(
            "FFmpeg: " + proc.stderr[-1800:]
        )


def ffprobe(path):
    check_binary("ffprobe")

    proc = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries",
            "format=duration,size:"
            "stream=codec_type,codec_name,width,height",
            "-of", "json",
            str(path)
        ],
        capture_output=True,
        text=True
    )

    if proc.returncode:
        raise RuntimeError(
            "FFprobe: " + proc.stderr[-1000:]
        )

    result = json.loads(proc.stdout)

    duration = float(
        result.get("format", {}).get("duration") or 0
    )

    streams = result.get("streams", [])

    videos = [
        s for s in streams
        if s.get("codec_type") == "video"
    ]

    if duration <= 0 or not videos:
        raise RuntimeError(
            "Video sin pista válida o duración incorrecta."
        )

    return result


def audio_duration(path):
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ],
        capture_output=True,
        text=True
    )

    if proc.returncode:
        raise RuntimeError(
            "No se pudo medir la duración de audio."
        )

    return float(proc.stdout.strip())


# ============================================================
# 3. PAÍS FIJO: CHILE
# ============================================================

def setup_country():
    global COUNTRY_GEOM
    global LAT0, LAT1, LON0, LON1
    global GRID, LATS, LONS, XX, YY
    global CITY, CITY_SOURCE, CAPITAL_CITY

    country = pycountry.countries.get(alpha_3="CHL")

    if country is None:
        raise RuntimeError("Chile no aparece en pycountry.")

    shp = shapereader.natural_earth(
        resolution="110m",
        category="cultural",
        name="admin_0_countries"
    )

    reader = shapereader.Reader(shp)

    geoms = []

    for record in reader.records():
        attributes = record.attributes

        codes = [
            str(attributes.get(key, ""))
            for key in (
                "ADM0_A3",
                "SOV_A3",
                "ISO_A3",
                "GU_A3",
                "SU_A3"
            )
        ]

        if "CHL" in codes:
            geoms.append(record.geometry)

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
        g for g in parts
        if g.geom_type == "Polygon"
    ]

    if polygons:
        biggest = max(polygons, key=lambda g: g.area)

        kept = [
            g for g in polygons
            if g.area >= 0.025 * biggest.area
        ]

        geom = unary_union(kept)

        if len(kept) < len(polygons):
            log(
                "Aviso: representación limitada "
                "a componentes geográficas principales."
            )

    x0, y0, x1, y1 = geom.bounds

    if x1 - x0 > 150:
        raise RuntimeError(
            "Extensión geográfica no compatible "
            "con la malla ETAS."
        )

    LAT0 = max(-89.0, y0 - 2.2)
    LAT1 = min(89.0, y1 + 2.2)

    LON0 = max(-179.9, x0 - 2.2)
    LON1 = min(179.9, x1 + 2.2)

    GRID = round(
        max(
            0.20,
            math.sqrt(
                (LAT1-LAT0) * (LON1-LON0)
                / MAX_GRID_CELLS
            )
        ),
        3
    )

    LATS = np.arange(
        LAT0, LAT1 + GRID/2, GRID
    )

    LONS = np.arange(
        LON0, LON1 + GRID/2, GRID
    )

    XX, YY = np.meshgrid(LONS, LATS)

    COUNTRY_GEOM = geom

    log(
        f"Chile: {XX.size:,} celdas | "
        f"malla {GRID:.3f}°"
    )

    # --------------------------------------------------------
    # GEONAMES
    # --------------------------------------------------------

    try:
        response = SESSION.get(
            "https://download.geonames.org/"
            "export/dump/cities15000.zip",
            timeout=(15, 45)
        )

        response.raise_for_status()

        with zipfile.ZipFile(
            io.BytesIO(response.content)
        ) as archive:

            with archive.open("cities15000.txt") as stream:
                table = pd.read_csv(
                    stream,
                    sep="\t",
                    header=None,
                    usecols=[1, 4, 5, 8],
                    names=[
                        "name", "lat", "lon", "iso2"
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
            for row in table.itertuples(index=False)
        ]

        CITY_SOURCE = "GeoNames cities15000 (CC BY 4.0)"

        log(
            "Ciudades de referencia:",
            len(CITY)
        )

        # Capital obtenida de GeoNames countryInfo.
        try:
            response = SESSION.get(
                "https://download.geonames.org/"
                "export/dump/countryInfo.txt",
                timeout=(15, 35)
            )

            response.raise_for_status()

            rows = [
                line.split("\t")
                for line in response.text.splitlines()
                if line and not line.startswith("#")
            ]

            matches = [
                fields for fields in rows
                if len(fields) > 5
                and fields[0] == country.alpha_2
            ]

            if matches:
                capital_name = matches[0][5].strip()

                candidates = [
                    c for c in CITY
                    if c[0].casefold() == capital_name.casefold()
                ]

                if candidates:
                    CAPITAL_CITY = candidates[0]

                elif capital_name:
                    geocoder = Nominatim(
                        user_agent="sismonauta-etas-capital/3.0",
                        timeout=15
                    )

                    place = geocoder.geocode(
                        f"{capital_name}, Chile",
                        exactly_one=True
                    )

                    if place:
                        CAPITAL_CITY = (
                            capital_name,
                            float(place.latitude),
                            float(place.longitude)
                        )

                log(
                    "Capital:",
                    CAPITAL_CITY or capital_name
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
            str(exc)[:110]
        )


def nearest_city(lat, lon):
    if CITY:
        lats = np.radians(
            [city[1] for city in CITY]
        )

        lons = np.radians(
            [city[2] for city in CITY]
        )

        dy = lats - np.radians(lat)
        dx = lons - np.radians(lon)

        a = (
            np.sin(dy/2)**2
            + np.cos(np.radians(lat))
            * np.cos(lats)
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
            user_agent="sismonauta-etas-reverse/3.0",
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

        locality = next(
            (
                address.get(key)
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

        if locality:
            dlat = np.radians(
                result.latitude - lat
            )

            dlon = np.radians(
                result.longitude - lon
            )

            a = (
                np.sin(dlat/2)**2
                + np.cos(np.radians(lat))
                * np.cos(np.radians(result.latitude))
                * np.sin(dlon/2)**2
            )

            distance = float(
                12742
                * np.arcsin(
                    np.sqrt(np.clip(a, 0, 1))
                )
            )

            return distance, str(locality)

    except Exception:
        pass

    return None, "localidad no identificada"


# ============================================================
# 4. CATÁLOGO USGS DESDE 2018
# ============================================================

def catalog_usgs():
    frames = []

    session = requests.Session()

    session.headers.update({
        "User-Agent": (
            "SismonautaETASChile/3.0 research"
        )
    })

    begin = pd.Timestamp(
        START, tz="UTC"
    )

    blocks = []

    while begin < CUTOFF:
        end = min(
            begin + pd.DateOffset(months=1),
            CUTOFF
        )

        blocks.append((begin, end))
        begin = end

    for index, (start, end) in enumerate(blocks, 1):
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

        part = None

        for attempt in range(4):
            try:
                response = session.get(
                    "https://earthquake.usgs.gov/"
                    "fdsnws/event/1/query",
                    params=params,
                    timeout=(15, 70)
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
                            "Bloque USGS de 20.000 eventos: "
                            "es necesario subdividirlo."
                        )

                break

            except Exception as exc:
                if attempt == 3:
                    raise RuntimeError(
                        f"Error USGS {start:%Y-%m}: {exc}"
                    ) from exc

                time.sleep(
                    2 * (attempt + 1)
                )

        frames.append(part)

        if index % 4 == 0 or index == len(blocks):
            log(
                f"USGS: {index}/{len(blocks)} meses"
            )

    if not frames:
        raise RuntimeError(
            "El catálogo USGS no contiene bloques."
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

    for field in (
        "latitude",
        "longitude",
        "mag"
    ):
        df[field] = pd.to_numeric(
            df[field],
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

    # Mantener área sísmica terrestre y marina
    # cercana a Chile, según el modelo original.
    area = COUNTRY_GEOM.buffer(2.5)

    prepared = prep(area)

    mask = np.fromiter(
        (
            prepared.covers(
                Point(float(lon), float(lat))
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
        df.loc[mask]
        .drop_duplicates(subset=["id"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    if len(df) < 200:
        raise RuntimeError(
            "Menos de 200 eventos válidos USGS."
        )

    log(
        f"Eventos sísmicos válidos: {len(df):,}"
    )

    return df


# ============================================================
# 5. ESTIMAR Mc Y PARÁMETRO b
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
        selected.mean() - (mc - 0.05)
    )

    if denominator <= 0:
        raise RuntimeError(
            "No fue posible estimar el parámetro b."
        )

    b = float(
        np.log10(np.e) / denominator
    )

    return mc, b


# ============================================================
# 6. VEROSIMILITUD ETAS CON NUMBA
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

    result = mu * T

    for i in range(len(t)):
        rate = mu

        for j in range(i):
            dt = t[i] - t[j]

            if dt > 0:
                rate += (
                    K
                    * np.exp(alpha*(m[j]-mc))
                    * (dt+c)**(-p)
                )

        if rate <= 0 or not np.isfinite(rate):
            return 1e90

        result -= np.log(rate)

    for j in range(len(t)):
        age = T - t[j]

        if age > 0:
            result += (
                K
                * np.exp(alpha*(m[j]-mc))
                * (
                    (age+c)**(1-p)
                    - c**(1-p)
                ) / (1-p)
            )

    if not np.isfinite(result):
        return 1e90

    return result


def fit_etas(df, mc):
    sub = df[
        df.mag >= mc
    ].tail(FIT_N)

    if len(sub) < 100:
        raise RuntimeError(
            "Eventos insuficientes para ajustar ETAS."
        )

    t = (
        (sub.time - sub.time.iloc[0])
        .dt.total_seconds()
        .to_numpy()
        / 86400.0
    )

    m = sub.mag.to_numpy()

    T = max(
        float(t[-1]),
        1e-6
    )

    bounds = [
        (1e-6, 100),
        (1e-8, 5),
        (0, 3),
        (0.002, 2),
        (1.01, 2.8)
    ]

    initial_values = [
        [0.2, 0.01, 0.8, 0.02, 1.2],
        [0.5, 0.005, 1.0, 0.08, 1.3],
        [1.0, 0.001, 0.6, 0.1, 1.5]
    ]

    fits = []

    for index, x0 in enumerate(initial_values, 1):
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
            "El ajuste ETAS no convergió."
        )

    best = min(
        fits,
        key=lambda item: item.fun
    )

    names = [
        "mu",
        "K",
        "alpha",
        "c",
        "p"
    ]

    return dict(
        zip(names, map(float, best.x))
    )


# ============================================================
# 7. FONDO ESPACIAL Y TRES MÁXIMOS
# ============================================================

def make_background(events):
    bins_lat = np.r_[
        LATS - GRID/2,
        LATS[-1] + GRID/2
    ]

    bins_lon = np.r_[
        LONS - GRID/2,
        LONS[-1] + GRID/2
    ]

    histogram, _, _ = np.histogram2d(
        events.latitude,
        events.longitude,
        bins=[bins_lat, bins_lon]
    )

    smoothed = (
        gaussian_filter(histogram, 1.7)
        + 1e-10
    )

    return (
        smoothed / smoothed.sum()
    ).astype(np.float32)


def three_distinct_increases(
    delta_pp,
    min_km=90.0
):
    """
    Seleccionar hasta tres máximos positivos
    separados espacialmente 90 km como mínimo.
    """

    flat = np.asarray(
        delta_pp
    ).ravel()

    order = np.argsort(flat)[::-1]

    selected = []

    for index in order:
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
            dlat = np.radians(
                lat - point["lat"]
            )

            dlon = np.radians(
                lon - point["lon"]
            )

            a = (
                np.sin(dlat/2)**2
                + np.cos(np.radians(lat))
                * np.cos(np.radians(point["lat"]))
                * np.sin(dlon/2)**2
            )

            km = (
                6371.0 * 2
                * np.arcsin(
                    min(
                        1.0,
                        np.sqrt(a)
                    )
                )
            )

            if km < min_km:
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
# 8. EVOLUCIÓN DE 25 MAPAS HORARIOS
# ============================================================

def evolution_24h(df, theta, mc, b, magnitude):
    """
    Reconstruye 25 mapas desde T-24h hasta T.

    Cada fotograma utiliza exclusivamente
    eventos anteriores a su fecha de corte.

    Cada valor corresponde al resultado
    experimental ETAS para las siguientes 24 h.
    """

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
            f"Hay {len(chosen)} disparadores "
            "en la ventana de 365 días. "
            "No se descartarán silenciosamente."
        )

    log(
        f"Preparando {len(times)} mapas; "
        f"{len(chosen)} disparadores ETAS."
    )

    kernels = np.empty(
        (len(chosen), XX.size),
        dtype=np.float32
    )

    lat_flat = YY.ravel().astype(np.float32)
    lon_flat = XX.ravel().astype(np.float32)

    for j, event in enumerate(
        chosen.itertuples(index=False)
    ):
        dx = (
            lon_flat - event.longitude
        ) * (
            111.32
            * np.cos(
                np.radians(event.latitude)
            )
        )

        dy = (
            lat_flat - event.latitude
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
            theta["alpha"] * (magnitudes-mc),
            -40,
            40
        )
    )

    factor = 10**(
        -b * (magnitude-mc)
    )

    c = theta["c"]
    p = theta["p"]

    probabilities = []
    maxima = []

    baseline = None

    for k, stamp in enumerate(times):
        timestamp = stamp.value

        ages = (
            (timestamp - event_times)
            / 1e9 / 86400.0
        )

        valid = (
            (ages > 0)
            & (ages <= TRIGGER_DAYS)
        )

        masses = np.zeros(
            len(chosen),
            dtype=np.float32
        )

        a = ages[valid]

        masses[valid] = (
            theta["K"]
            * productivity[valid]
            * (
                (
                    a + HORIZON_DAYS + c
                )**(1-p)
                - (a+c)**(1-p)
            ) / (1-p)
        ).astype(np.float32)

        before = df[
            (df.time < stamp)
            & (df.mag >= mc)
        ]

        bg = make_background(before)

        expected = (
            theta["mu"]
            * HORIZON_DAYS
            * bg.ravel()
        )

        if len(chosen):
            expected = (
                expected
                + (masses @ kernels)
            )

        forecast = -np.expm1(
            -expected.reshape(bg.shape)
            * factor
        )

        if not np.all(
            np.isfinite(forecast)
        ):
            raise RuntimeError(
                "El mapa ETAS contiene "
                "valores no finitos."
            )

        forecast = np.clip(
            forecast, 0, 1
        ).astype(np.float32)

        if baseline is None:
            baseline = forecast.copy()

        delta_pp = (
            forecast.astype(np.float64)
            - baseline.astype(np.float64)
        ) * 100.0

        row_p, col_p = np.unravel_index(
            np.argmax(forecast),
            forecast.shape
        )

        row_d, col_d = np.unravel_index(
            np.argmax(delta_pp),
            delta_pp.shape
        )

        delta_points = three_distinct_increases(
            delta_pp
        )

        probabilities.append(
            forecast
        )

        maxima.append({
            "prob_lat": float(LATS[row_p]),
            "prob_lon": float(LONS[col_p]),
            "prob_val": float(
                forecast[row_p, col_p]
            ) * 100.0,

            "delta_lat": float(LATS[row_d]),
            "delta_lon": float(LONS[col_d]),
            "delta_val": float(
                delta_pp[row_d, col_d]
            ),

            "delta_points": delta_points
        })

        log(
            f"Mapa {k+1}/{len(times)} | "
            f"{stamp:%d/%m %H:%M} UTC | "
            f"máx prob "
            f"{maxima[-1]['prob_val']:.4f}% | "
            f"aumento "
            f"{maxima[-1]['delta_val']:.5f} pp"
        )

    return times, probabilities, maxima


# ============================================================
# 9. TIPOGRAFÍA Y CARTOGRAFÍA
# ============================================================

def font(size, bold=False):
    files = (
        "/usr/share/fonts/truetype/dejavu/"
        + (
            "DejaVuSans-Bold.ttf"
            if bold
            else "DejaVuSans.ttf"
        )
    )

    try:
        return ImageFont.truetype(files, size)
    except Exception:
        return ImageFont.load_default()


def load_detailed_geography():
    global _DETAIL_CACHE

    if _DETAIL_CACHE is not None:
        return _DETAIL_CACHE

    region = shapely_box(
        LON0, LAT0, LON1, LAT1
    )

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
        ),
    }

    layers = {}

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
                bbox=(LON0, LAT0, LON1, LAT1)
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
                f"Aviso cartografía {name}:",
                str(exc)[:120]
            )

    _DETAIL_CACHE = layers

    return layers


# ============================================================
# 10. PLANTILLA DE LOS DOS HEATMAPS
# ============================================================

def draw_heatmap_panel(
    fig,
    rect,
    title,
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

    for layer, color, width, style, level in (
        ("coast", "#152C3D", 1.35, "-", 15),
        ("country", "#203747", 0.85, "-", 14),
        ("states", "#6C8190", 0.42, ":", 13),
    ):
        if detailed.get(layer):
            ax.add_geometries(
                detailed[layer],
                crs=ccrs.PlateCarree(),
                facecolor="none",
                edgecolor=color,
                linewidth=width,
                linestyle=style,
                zorder=level
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
            lon, lat,
            "o",
            ms=2.8,
            color="#152E3F",
            markeredgecolor="white",
            markeredgewidth=0.45,
            transform=ccrs.PlateCarree(),
            zorder=10
        )

        ax.text(
            lon + 0.12,
            lat + 0.07,
            name,
            fontsize=7.2,
            color="#183143",
            transform=ccrs.PlateCarree(),
            zorder=11,
            bbox={
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.76,
                "pad": 1.0
            }
        )

    if scale == "log":
        norm = LogNorm(
            vmin=clims[0],
            vmax=clims[1]
        )
    else:
        norm = mcolors.PowerNorm(
            gamma=0.50,
            vmin=0,
            vmax=clims[1]
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

    try:
        ticks = (
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

        bar.set_ticks(ticks)
    except Exception:
        pass

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
        (
            card_rect[0],
            card_rect[1]
        ),
        card_rect[2],
        card_rect[3],
        transform=fig.transFigure,
        facecolor="#E8EFF5",
        edgecolor="#D1DDE6",
        linewidth=1.0
    )

    fig.patches.append(patch)

    fig.text(
        card_rect[0] + 0.015,
        card_rect[1] + 0.078,
        card_title,
        fontsize=10.5,
        fontweight="bold",
        color="#4B6477"
    )

    fig.text(
        card_rect[0] + 0.015,
        card_rect[1] + 0.051,
        card_value,
        fontsize=14,
        fontweight="bold",
        color="#173A52"
    )

    fig.text(
        card_rect[0] + 0.015,
        card_rect[1] + 0.024,
        card_city,
        fontsize=9.2,
        color="#415B6D"
    )

    return ax, mesh


# ============================================================
# 11. DIBUJAR UNA INSTANTÁNEA
# ============================================================

def draw_snapshot(
    prob,
    stamp,
    index,
    total,
    abs_clims,
    delta_clims,
    frame_info,
    work,
    baseline
):
    fig = plt.figure(
        figsize=(10.8, 13.5),
        dpi=100,
        facecolor="#F3F7FA"
    )

    percent = prob * 100.0

    masked_prob = np.ma.masked_less_equal(
        percent, 0
    )

    delta_pp = (
        prob.astype(np.float64)
        - baseline.astype(np.float64)
    ) * 100.0

    delta_color = np.maximum(
        delta_pp,
        0.0
    )

    def labels_near(lat, lon):
        points = []

        candidates = sorted(
            CITY,
            key=lambda c: (
                (c[1]-lat)**2
                + (c[2]-lon)**2
            )
        )

        if candidates:
            points.append(candidates[0])

        if CAPITAL_CITY:
            _, capital_lat, capital_lon = CAPITAL_CITY

            if (
                LAT0 <= capital_lat <= LAT1
                and LON0 <= capital_lon <= LON1
            ):
                if not any(
                    city[0] == CAPITAL_CITY[0]
                    for city in points
                ):
                    points.append(CAPITAL_CITY)

        return points

    selected_prob = labels_near(
        frame_info["prob_lat"],
        frame_info["prob_lon"]
    )

    delta_points = frame_info.get(
        "delta_points", []
    )[:3]

    # Comprobar que marcadores y valores coincidan.
    for point in delta_points:
        lat = float(point["lat"])
        lon = float(point["lon"])

        row = int(
            np.argmin(
                np.abs(LATS-lat)
            )
        )

        col = int(
            np.argmin(
                np.abs(LONS-lon)
            )
        )

        actual = float(
            delta_pp[row, col]
        )

        tolerance = max(
            1e-5,
            abs(actual) * 1e-3
        )

        if abs(
            float(point["val"]) - actual
        ) > tolerance:
            raise RuntimeError(
                f"Marcador desalineado en mapa {index}."
            )

    ax_left, _ = draw_heatmap_panel(
        fig=fig,
        rect=[0.055, 0.27, 0.42, 0.57],
        title="PROBABILIDAD MODELADA",
        data=masked_prob,
        clims=abs_clims,
        city_points=selected_prob,
        colorbar_rect=[0.08, 0.22, 0.37, 0.016],
        colorbar_label=(
            f"Por celda: ≥1 sismo M≥"
            f"{frame_info['mag']:.1f} en 24 h"
        ),
        colorbar_fmt=lambda value, pos: f"{value:.3g}%",
        card_rect=[0.055, 0.08, 0.42, 0.105],
        card_title="Coordenada del valor absoluto más alto",
        card_value=(
            f"{frame_info['prob_val']:.4f}% · "
            f"{frame_info['prob_lat']:+.2f}°, "
            f"{frame_info['prob_lon']:+.2f}°"
        ),
        card_city=(
            f"Ciudad cercana: "
            f"{frame_info['prob_city']} "
            f"({frame_info['prob_dist']:.0f} km)"
        )
    )

    ax_right, _ = draw_heatmap_panel(
        fig=fig,
        rect=[0.525, 0.27, 0.42, 0.57],
        title="MAYOR AUMENTO",
        data=delta_color,
        clims=delta_clims,
        city_points=[],
        colorbar_rect=[0.55, 0.22, 0.37, 0.016],
        colorbar_label=(
            "Variación respecto al comienzo (pp)"
        ),
        colorbar_fmt=lambda value, pos: f"{value:.3g} pp",
        card_rect=[0.525, 0.08, 0.42, 0.105],
        card_title="Tres zonas de mayor incremento positivo",
        card_value="",
        card_city="",
        scale="power"
    )

    # Encabezados.
    fig.text(
        0.5, 0.953,
        "SISMONAUTA · ETAS CHILE",
        ha="center",
        fontsize=20,
        fontweight="bold",
        color="#19374B"
    )

    fig.text(
        0.5, 0.921,
        (
            f"Instantánea {index+1}/{total} · "
            f"{stamp:%d/%m/%Y %H:%M} UTC"
        ),
        ha="center",
        fontsize=12,
        color="#436277"
    )

    fig.text(
        0.265, 0.863,
        "PROBABILIDAD MODELADA",
        ha="center",
        fontsize=11,
        fontweight="bold",
        color="#19374B"
    )

    fig.text(
        0.735, 0.863,
        "AUMENTO DESDE HACE 24 H",
        ha="center",
        fontsize=11,
        fontweight="bold",
        color="#19374B"
    )

    fig.text(
        0.5, 0.035,
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
            ax_right.plot(
                lon, lat,
                "o",
                ms=2.8,
                color="#152E3F",
                markeredgecolor="white",
                markeredgewidth=0.45,
                transform=ccrs.PlateCarree(),
                zorder=10
            )

            ax_right.text(
                lon + 0.12,
                lat + 0.07,
                name,
                fontsize=7.2,
                color="#183143",
                transform=ccrs.PlateCarree(),
                zorder=11,
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

    geo_transform = (
        ccrs.PlateCarree()
        ._as_mpl_transform(ax_right)
    )

    for rank, point in enumerate(delta_points, 1):
        lon = float(point["lon"])
        lat = float(point["lat"])

        ax_right.scatter(
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
            (rank-1) % len(offsets)
        ]

        ax_right.annotate(
            str(rank),
            xy=(lon, lat),
            xycoords=geo_transform,
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
                "boxstyle": "circle,pad=0.30",
                "facecolor": "#162D43",
                "edgecolor": "white",
                "linewidth": 1.2,
                "alpha": 0.98
            }
        )

    for rank, point in enumerate(delta_points, 1):
        city = str(
            point.get("city")
            or "Localidad no identificada"
        )

        if len(city) > 22:
            city = city[:21].rstrip() + "…"

        fig.text(
            0.54,
            0.131 - (rank-1)*0.022,
            (
                f"{rank}. {city} · "
                f"{point['val']:+.4f} pp"
            ),
            fontsize=8.2,
            color="#173A52",
            fontweight="bold",
            zorder=30
        )

    def add_blink_stars():
        stars = [
            (
                ax_left,
                float(frame_info["prob_lon"]),
                float(frame_info["prob_lat"])
            )
        ]

        stars.extend(
            (
                ax_right,
                float(point["lon"]),
                float(point["lat"])
            )
            for point in delta_points
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

    off_path = (
        Path(work)
        / f"frame_{index:03d}_off.png"
    )

    on_path = (
        Path(work)
        / f"frame_{index:03d}_on.png"
    )

    fig.savefig(
        off_path,
        dpi=fig.dpi,
        facecolor=fig.get_facecolor(),
        bbox_inches=None,
        pad_inches=0
    )

    add_blink_stars()

    fig.savefig(
        on_path,
        dpi=fig.dpi,
        facecolor=fig.get_facecolor(),
        bbox_inches=None,
        pad_inches=0
    )

    plt.close(fig)

    return on_path, off_path


# ============================================================
# 12. MÚSICA SINTETIZADA
# ============================================================

def music_wav(filepath, duration=30, sr=24000):
    samples = int(sr * duration)

    t = np.arange(
        samples,
        dtype=np.float64
    ) / sr

    audio = np.zeros_like(t)

    chords = [
        (196.0, 246.94, 293.66),
        (174.61, 220.0, 261.63),
        (164.81, 196.0, 246.94),
        (146.83, 185.0, 220.0)
    ]

    for begin in range(0, duration, 4):
        local = t - begin

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
        ) * active

        for frequency in chords[
            (begin//4) % len(chords)
        ]:
            phase = (
                2 * np.pi
                * frequency * t
            )

            audio += (
                0.6 * np.sin(phase)
                + 0.13 * np.sin(phase*2)
            ) * envelope / 3

    for beat in np.arange(0, duration, 2):
        tau = t - beat

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
        audio *= 0.67 / peak

    wavfile.write(
        str(filepath),
        sr,
        (
            np.clip(audio, -1, 1)
            * 32767
        ).astype(np.int16)
    )


# ============================================================
# 13. NARRACIÓN CIENTÍFICA
# ============================================================

def narration(final_info, first_prob, last_prob):
    """
    Narración basada en el último mapa real.

    Corrección importante:
    prob_val ya está expresado en porcentaje.
    No multiplicarlo nuevamente por 100.
    """

    def number(value, digits=4):
        return (
            f"{value:.{digits}f}"
            .replace(".", ",")
        )

    def percentage_speech(value):
        value = max(
            0.0,
            min(100.0, float(value))
        )

        if value >= 100.0:
            return "100 por ciento"

        return (
            f"{value:.2f}".replace(".", ",")
            + " por ciento"
        )

    areas = final_info.get(
        "delta_points", []
    )

    if areas:
        pieces = []

        for rank, point in enumerate(areas, 1):
            label = point.get(
                "city",
                "una zona sin ciudad cercana"
            )

            pieces.append(
                f"La zona {rank} está cerca "
                f"de la ciudad de {label}, "
                f"con un incremento de "
                f"{number(point['val'])} "
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
        "Te invitamos a revisar la actualización "
        "del modelo sísmico experimental ETAS "
        "en Chile durante las últimas 24 horas. "

        "En el mapa de la izquierda se observa "
        "que el valor de probabilidad modelada "
        "más alto está cerca de la ciudad de "

        f"{final_info['prob_city']}, "
        f"con {percentage_speech(final_info['prob_val'])} "

        "para al menos un sismo de magnitud "
        f"{final_info['mag']:.1f} o superior "
        "en las próximas 24 horas, según este modelo. "

        "En el gráfico de la derecha en tanto, "
        "observamos las zonas donde la probabilidad "
        "modelada aumentó más durante el período. "

        f"{delta_text} "

        "IMPORTANTE: estos resultados son "
        "experimentales y no están calibrados "
        "ni validados prospectivamente. "
        "No constituyen una alerta sísmica "
        "ni una predicción oficial."
    )


# ============================================================
# 14. VOZ EDGE-TTS CON RESPALDO GTTS
# ============================================================

def voice(text, work, filename="voz.mp3"):
    filepath = (
        Path(work) / filename
    )

    try:
        import edge_tts

        async def speak():
            await edge_tts.Communicate(
                text,
                voice="es-CL-CatalinaNeural",
                rate=VOICE_RATE
            ).save(str(filepath))

        asyncio.run(speak())

        if (
            filepath.is_file()
            and filepath.stat().st_size > 1000
        ):
            return filepath

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
        ).save(str(filepath))

        if (
            filepath.is_file()
            and filepath.stat().st_size > 1000
        ):
            return filepath

    except Exception as exc:
        log(
            "gTTS no disponible:",
            str(exc)[:100]
        )

    raise RuntimeError(
        "No fue posible generar la locución."
    )


# ============================================================
# 15. CIERRE: DALE LIKE Y COMENTA
# ============================================================

def create_outro_frame(work, visible):
    filename = (
        Path(work)
        / (
            "outro_on.png"
            if visible
            else "outro_off.png"
        )
    )

    im = Image.new(
        "RGB",
        (W, H),
        "#000000"
    )

    draw = ImageDraw.Draw(im)

    if visible:
        hand = Image.new(
            "RGBA",
            (900, 900),
            (0, 0, 0, 0)
        )

        d = ImageDraw.Draw(hand)

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

        d.line(
            outline,
            fill="#1877F2",
            width=34,
            joint="curve"
        )

        for x, y in outline:
            d.ellipse(
                (
                    x-17,
                    y-17,
                    x+17,
                    y+17
                ),
                fill="#1877F2"
            )

        d.line(
            (180,460,180,685),
            fill="#1877F2",
            width=34
        )

        d.line(
            [(280,468),(280,686)],
            fill="#1877F2",
            width=26
        )

        hand = hand.resize(
            (440,440),
            Image.Resampling.LANCZOS
        )

        im.paste(
            hand,
            (
                (W-440)//2,
                160
            ),
            hand
        )

    large_font = font(
        120,
        bold=True
    )

    for line, top in [
        ("Dale Like", 690),
        ("y Comenta!", 850)
    ]:
        bbox = draw.textbbox(
            (0,0),
            line,
            font=large_font
        )

        width = bbox[2] - bbox[0]

        if width > W - 100:
            raise RuntimeError(
                "Texto de cierre demasiado ancho. "
                "Comprobar disponibilidad de fuentes."
            )

        x = int(
            (W-width)/2 - bbox[0]
        )

        y = int(
            top - bbox[1]
        )

        draw.text(
            (x,y),
            line,
            font=large_font,
            fill="white"
        )

    im.save(filename)

    return filename


# ============================================================
# 16. GENERAR VIDEO CIENTÍFICO ORIGINAL
# ============================================================

def create_video(times, maps, maxima, final_info):
    """
    Renderiza el contenido audiovisual original:
      * 25 mapas con escalas fijas.
      * Estrellas parpadeantes.
      * Tres marcadores de aumento.
      * Voz sincronizada.
      * Último mapa congelado en el resumen.
      * Música.
      * Cierre animado.

    No utiliza /mnt/data ni apt-get.
    """

    check_binary("ffmpeg")
    check_binary("ffprobe")

    ensure_output()

    work = tempfile.mkdtemp(
        prefix="sismonauta_etas_"
    )

    try:
        positive_arrays = [
            frame[frame > 0]
            for frame in maps
            if np.any(frame > 0)
        ]

        if not positive_arrays:
            raise RuntimeError(
                "Los mapas no tienen valores positivos."
            )

        positive = np.concatenate(
            positive_arrays
        )

        vmax = float(
            positive.max() * 100.0
        )

        vmin = max(
            float(
                np.percentile(
                    positive, 2
                ) * 100.0
            ),
            vmax/1000.0,
            1e-10
        )

        if vmin >= vmax:
            vmin = max(
                vmax/1000.0,
                1e-10
            )

        abs_clims = (vmin, vmax)

        baseline = maps[0]

        delta_fields = [
            (
                frame.astype(np.float64)
                - baseline.astype(np.float64)
            ) * 100.0
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

        frame_paths = []
        last_frame_info = None

        # ----------------------------------------------------
        # RENDERIZAR LOS 25 MAPAS
        # ----------------------------------------------------

        for index, (stamp, prob, meta) in enumerate(
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

            frame_info = dict(meta)

            frame_info["delta_points"] = []

            for point in meta.get(
                "delta_points", []
            ):
                distance, city = nearest_city(
                    point["lat"],
                    point["lon"]
                )

                frame_info["delta_points"].append(
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

            frame_info.update({
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

            if index == len(times)-1:
                last_frame_info = frame_info

            frame_paths.append(
                draw_snapshot(
                    prob,
                    stamp,
                    index,
                    len(times),
                    abs_clims,
                    delta_clims,
                    frame_info,
                    work,
                    baseline
                )
            )

            if (
                (index+1) % 5 == 0
                or index+1 == len(times)
            ):
                log(
                    f"Fotogramas: {index+1}/{len(times)}"
                )

        if last_frame_info is None:
            raise RuntimeError(
                "No se creó el último mapa."
            )

        # Validar puntos del mapa y la voz.
        final_info = dict(last_frame_info)

        assert len(
            final_info["delta_points"]
        ) == len(
            maxima[-1]["delta_points"]
        )

        for displayed, calculated in zip(
            final_info["delta_points"],
            maxima[-1]["delta_points"]
        ):
            assert (
                float(displayed["lat"])
                == float(calculated["lat"])
            )

            assert (
                float(displayed["lon"])
                == float(calculated["lon"])
            )

            assert np.isclose(
                float(displayed["val"]),
                float(calculated["val"])
            )

        # ----------------------------------------------------
        # NARRACIÓN
        # ----------------------------------------------------

        narration_text = narration(
            final_info,
            maxima[0]["prob_val"],
            maxima[-1]["prob_val"]
        )

        log(
            "\nNARRACIÓN FINAL:\n"
            + narration_text + "\n"
        )

        separator = (
            "En el gráfico de la derecha en tanto,"
        )

        if separator not in narration_text:
            raise RuntimeError(
                "No se encontró el separador "
                "de la narración geográfica."
            )

        first_words, second_words = (
            narration_text.split(
                separator,
                1
            )
        )

        second_words = (
            separator + second_words
        )

        first_voice = voice(
            first_words.strip(),
            work,
            "voz_animacion.mp3"
        )

        second_voice = voice(
            second_words.strip(),
            work,
            "voz_zonas_ultimo_mapa.mp3"
        )

        closing_words = (
            "Dale me gusta y comparte para "
            "seguir recibiendo esta importante "
            "información!"
        )

        outro_voice = voice(
            closing_words,
            work,
            "voz_cierre.mp3"
        )

        # ----------------------------------------------------
        # ACELERACIÓN DE VOZ
        # ----------------------------------------------------

        fast_paths = []

        for original_audio, name in [
            (first_voice, "voz_animacion_rapida.wav"),
            (second_voice, "voz_zonas_rapida.wav"),
            (outro_voice, "voz_cierre_rapida.wav")
        ]:
            destination = (
                Path(work) / name
            )

            ffmpeg(
                "-i", original_audio,
                "-af", f"atempo={AUDIO_TEMPO:.3f}",
                "-ar", "24000",
                "-ac", "1",
                destination
            )

            fast_paths.append(destination)

        first_fast, second_fast, outro_fast = (
            fast_paths
        )

        animation_duration = max(
            VIDEO_SECONDS,
            int(
                math.ceil(
                    audio_duration(first_fast) + 0.6
                )
            )
        )

        summary_duration = max(
            2,
            int(
                math.ceil(
                    audio_duration(second_fast) + 0.8
                )
            )
        )

        outro_duration = max(
            7,
            int(
                math.ceil(
                    audio_duration(outro_fast) + 0.7
                )
            )
        )

        video_seconds = (
            animation_duration
            + summary_duration
            + outro_duration
        )

        # ----------------------------------------------------
        # UNIR LAS TRES VOCES
        # ----------------------------------------------------

        narration_track = (
            Path(work) / "narracion_completa.wav"
        )

        audio_concat = (
            f"[0:a]aformat=sample_rates=24000:"
            f"channel_layouts=mono,"
            f"adelay=350,apad,"
            f"atrim=duration={animation_duration},"
            f"asetpts=PTS-STARTPTS[a0];"

            f"[1:a]aformat=sample_rates=24000:"
            f"channel_layouts=mono,"
            f"adelay=150,apad,"
            f"atrim=duration={summary_duration},"
            f"asetpts=PTS-STARTPTS[a1];"

            f"[2:a]aformat=sample_rates=24000:"
            f"channel_layouts=mono,"
            f"adelay=200,apad,"
            f"atrim=duration={outro_duration},"
            f"asetpts=PTS-STARTPTS[a2];"

            "[a0][a1][a2]"
            "concat=n=3:v=0:a=1[spoken]"
        )

        ffmpeg(
            "-i", first_fast,
            "-i", second_fast,
            "-i", outro_fast,
            "-filter_complex", audio_concat,
            "-map", "[spoken]",
            "-ar", "24000",
            "-ac", "1",
            narration_track
        )

        # ----------------------------------------------------
        # MÚSICA ORIGINAL
        # ----------------------------------------------------

        soundtrack = (
            Path(work) / "musica.wav"
        )

        music_wav(
            soundtrack,
            duration=video_seconds
        )

        log(
            "Duraciones:",
            f"animación={animation_duration}s",
            f"resumen={summary_duration}s",
            f"cierre={outro_duration}s",
            f"total={video_seconds}s"
        )

        # ----------------------------------------------------
        # ANIMACIÓN CON PARPADEO
        # ----------------------------------------------------

        total_maps = len(frame_paths)

        total_video_frames = (
            animation_duration * FPS
        )

        frames_per_map = (
            total_video_frames // total_maps
        )

        extra = (
            total_video_frames % total_maps
        )

        expanded = []
        frame_index = 0

        for index, (
            on_path, off_path
        ) in enumerate(frame_paths):

            repeats = (
                frames_per_map
                + (1 if index < extra else 0)
            )

            for _ in range(repeats):
                visible = (
                    (
                        frame_index
                        // max(1, int(FPS*0.5))
                    ) % 2
                ) == 0

                expanded.append(
                    on_path if visible else off_path
                )

                frame_index += 1

        final_on, final_off = (
            frame_paths[-1]
        )

        for j in range(
            summary_duration * FPS
        ):
            visible = (
                (
                    (frame_index+j)
                    // max(1, int(FPS*0.5))
                ) % 2
            ) == 0

            expanded.append(
                final_on if visible else final_off
            )

        outro_on = create_outro_frame(
            work, True
        )

        outro_off = create_outro_frame(
            work, False
        )

        for j in range(
            outro_duration * FPS
        ):
            visible = (
                (
                    j // max(1, int(FPS*0.5))
                ) % 2
            ) == 0

            expanded.append(
                outro_on if visible else outro_off
            )

        if not expanded:
            raise RuntimeError(
                "La animación no contiene fotogramas."
            )

        # ----------------------------------------------------
        # LISTA CONCAT FFMPEG
        # ----------------------------------------------------

        list_txt = (
            Path(work) / "frames.txt"
        )

        with list_txt.open(
            "w", encoding="utf-8"
        ) as handle:

            for path in expanded:
                safe_path = str(path).replace(
                    "'", "'\\''"
                )

                handle.write(
                    f"file '{safe_path}'\n"
                )

                handle.write(
                    f"duration {1/FPS:.8f}\n"
                )

            last_path = str(
                expanded[-1]
            ).replace("'", "'\\''")

            handle.write(
                f"file '{last_path}'\n"
            )

        silent = (
            Path(work) / "video_sin_audio.mp4"
        )

        ffmpeg(
            "-f", "concat",
            "-safe", "0",
            "-i", list_txt,
            "-vsync", "vfr",
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "21",
            "-movflags", "+faststart",
            silent
        )

        # ----------------------------------------------------
        # MEZCLA AUDIO CON DUCKING
        # ----------------------------------------------------

        filters = (
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

            f"atrim=duration={video_seconds},"
            "asetpts=N/SR/TB,"
            "alimiter=limit=0.92[aout]"
        )

        OUTPUT_MP4.unlink(
            missing_ok=True
        )

        ffmpeg(
            "-i", silent,
            "-i", narration_track,
            "-stream_loop", "-1",
            "-i", soundtrack,
            "-filter_complex", filters,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-t", video_seconds,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "160k",
            "-movflags", "+faststart",
            OUTPUT_MP4
        )

        if (
            not OUTPUT_MP4.is_file()
            or OUTPUT_MP4.stat().st_size < 10000
        ):
            raise RuntimeError(
                "No se generó el MP4 científico."
            )

        log(
            "VIDEO FINAL:",
            OUTPUT_MP4
        )

        return OUTPUT_MP4

    finally:
        shutil.rmtree(
            work,
            ignore_errors=True
        )


# ============================================================
# 17. EJECUTAR TODO EL MODELO CIENTÍFICO
# ============================================================

def run_scientific_etas():
    global CUTOFF

    CUTOFF = pd.Timestamp.now(tz="UTC")

    log(
        f"Corte del catálogo: "
        f"{CUTOFF:%d/%m/%Y %H:%M} UTC"
    )

    ensure_output()

    setup_country()

    df = catalog_usgs()

    start = CUTOFF - pd.Timedelta(
        hours=LOOKBACK_HOURS
    )

    training = df[
        df.time < start
    ].copy()

    if len(training) < 200:
        raise RuntimeError(
            "Catálogo histórico insuficiente."
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
            f"Se utiliza M≥{magnitude:.2f} "
            "y no exactamente M≥4."
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
        "Parámetros ETAS ajustados:",
        theta
    )

    times, maps, maxima = evolution_24h(
        df,
        theta,
        mc,
        b,
        magnitude
    )

    if len(times) != 25 or len(maps) != 25:
        raise RuntimeError(
            "Se esperaban exactamente 25 mapas."
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

    for point in final.get(
        "delta_points", []
    ):
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

    info = {
        "delta_points": points,

        "prob_lat": final["prob_lat"],
        "prob_lon": final["prob_lon"],
        "prob_val": final["prob_val"],
        "prob_city": prob_city,
        "prob_dist": (
            prob_dist
            if prob_dist is not None
            else 0.0
        ),

        "delta_lat": final["delta_lat"],
        "delta_lon": final["delta_lon"],
        "delta_val": final["delta_val"],
        "delta_city": delta_city,
        "delta_dist": (
            delta_dist
            if delta_dist is not None
            else 0.0
        ),

        "mag": magnitude
    }

    log(
        "MÁXIMO ACTUAL MODELADO:",
        f"{info['prob_lat']:.3f}°,",
        f"{info['prob_lon']:.3f}°,",
        f"{info['prob_val']:.6f}%,",
        info["prob_city"]
    )

    log(
        "MAYOR AUMENTO ACUMULADO:",
        f"{info['delta_lat']:.3f}°,",
        f"{info['delta_lon']:.3f}°,",
        f"{info['delta_val']:.6f} pp,",
        info["delta_city"]
    )

    log(
        "Los valores son locales por celda. "
        "No representan probabilidades "
        "municipales ni alertas oficiales."
    )

    video = create_video(
        times,
        maps,
        maxima,
        info
    )

    # Metadatos científicos de la corrida.
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
        "snapshots": len(times),
        "final": info,
        "city_source": CITY_SOURCE,
        "disclaimer": (
            "Modelo experimental, sin calibración "
            "ni validación prospectiva."
        )
    }

    atomic_json(
        OUTPUT_DIR / "modelo_etas_resultados.json",
        metadata
    )

    return video, metadata


# ============================================================
# 18. CREAR REEL 9:16
# ============================================================

def make_reel(original):
    destination = (
        OUTPUT_DIR / REEL_NAME
    )

    destination.unlink(
        missing_ok=True
    )

    ffmpeg(
        "-i", original,
        "-vf",
        (
            "scale=1080:1920:"
            "force_original_aspect_ratio=decrease,"
            "pad=1080:1920:"
            "(ow-iw)/2:(oh-ih)/2:"
            "color=0x071727,"
            "setsar=1"
        ),
        "-r", "24",
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

    probe = ffprobe(destination)

    video_stream = next(
        stream for stream in probe["streams"]
        if stream.get("codec_type") == "video"
    )

    if (
        video_stream.get("width") != 1080
        or video_stream.get("height") != 1920
    ):
        raise RuntimeError(
            "El Reel no tiene resolución 1080x1920."
        )

    log(
        "REEL INSTAGRAM:",
        destination
    )

    return destination


# ============================================================
# 19. API HTTP PARA GITHUB Y META
# ============================================================

def api_json(
    method,
    url,
    *,
    token=None,
    timeout=(20, 180),
    **kwargs
):
    headers = dict(
        kwargs.pop("headers", {})
    )

    if token:
        headers["Authorization"] = (
            "Bearer " + token
        )

    response = SESSION.request(
        method,
        url,
        headers=headers,
        timeout=timeout,
        **kwargs
    )

    try:
        data = response.json()
    except ValueError:
        data = {}

    if not response.ok:
        message = response.reason

        error = data.get("error")

        if isinstance(error, dict):
            message = error.get(
                "message",
                message
            )

        raise RuntimeError(
            f"HTTP {response.status_code}: "
            f"{str(message)[:250]}"
        )

    return data


def github_api(method, path, **kwargs):
    token = os.environ["GH_TOKEN"]

    headers = dict(
        kwargs.pop("headers", {})
    )

    headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    })

    return api_json(
        method,
        GITHUB_API + path,
        token=token,
        headers=headers,
        **kwargs
    )


def github_state_read(path):
    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    branch = os.getenv(
        "GITHUB_REF_NAME", "main"
    )

    url = (
        f"{GITHUB_API}/repos/{repo}/contents/"
        f"{quote(path, safe='/')}"
    )

    headers = {
        "Authorization": (
            "Bearer " + os.environ["GH_TOKEN"]
        ),
        "Accept": "application/vnd.github+json"
    }

    response = SESSION.get(
        url,
        headers=headers,
        params={"ref": branch},
        timeout=(15, 60)
    )

    if response.status_code == 404:
        return {}, None

    response.raise_for_status()

    data = response.json()

    content = base64.b64decode(
        data["content"]
    ).decode("utf-8")

    return json.loads(content), data["sha"]


def github_state_save(path, state):
    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    branch = os.getenv(
        "GITHUB_REF_NAME", "main"
    )

    for attempt in range(4):
        _, sha = github_state_read(path)

        body = {
            "message": (
                "SISMONAUTA ETAS Chile: "
                "actualizar estado"
            ),
            "content": base64.b64encode(
                json.dumps(
                    state,
                    ensure_ascii=False,
                    indent=2
                ).encode()
            ).decode(),
            "branch": branch
        }

        if sha:
            body["sha"] = sha

        try:
            github_api(
                "PUT",
                f"/repos/{repo}/contents/"
                f"{quote(path, safe='/')}",
                json=body
            )

            return

        except RuntimeError:
            if attempt == 3:
                raise

            time.sleep(
                2 * (attempt+1)
            )


# ============================================================
# 20. VALIDACIÓN DE CUENTAS
# ============================================================

def validate_accounts():
    required = [
        "GH_TOKEN",
        "GITHUB_REPOSITORY",
        "INSTAGRAM_TOKEN",
        "INSTAGRAM_USER_ID",
        "FACEBOOK_PAGE_TOKEN",
        "FACEBOOK_PAGE_ID"
    ]

    missing = [
        key for key in required
        if not os.getenv(key)
    ]

    if missing:
        raise RuntimeError(
            "Faltan variables: "
            + ", ".join(missing)
        )

    ig_id = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    ig = api_json(
        "GET",
        f"{IG_BASE}/{GRAPH_VERSION}/{ig_id}",
        token=os.environ["INSTAGRAM_TOKEN"],
        params={
            "fields": "id,username"
        }
    )

    if (
        str(ig.get("id")) != ig_id
        or str(
            ig.get("username", "")
        ).lower() != "sismonauta"
    ):
        raise RuntimeError(
            "Instagram no corresponde a @sismonauta."
        )

    page_id = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    fb = api_json(
        "GET",
        f"{FB_BASE}/{page_id}",
        token=os.environ["FACEBOOK_PAGE_TOKEN"],
        params={
            "fields": "id,name"
        }
    )

    if (
        str(fb.get("id")) != page_id
        or "sismonauta" not in str(
            fb.get("name", "")
        ).lower()
    ):
        raise RuntimeError(
            "La Fanpage no corresponde a Sismonauta."
        )

    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    info = github_api(
        "GET",
        f"/repos/{repo}"
    )

    if info.get("private"):
        raise RuntimeError(
            "GitHub Releases requiere que el video "
            "sea accesible públicamente por Meta."
        )

    log(
        "Cuentas verificadas: @sismonauta / Sismonauta"
    )


# ============================================================
# 21. SUBIR REEL A GITHUB RELEASES
# ============================================================

def release_video(video):
    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    tag = (
        "sismonauta-etas-"
        + datetime.now(
            timezone.utc
        ).strftime("%Y%m%d-%H%M%S")
        + "-"
        + os.getenv(
            "GITHUB_RUN_ID",
            str(int(time.time()))
        )
    )

    release = github_api(
        "POST",
        f"/repos/{repo}/releases",
        json={
            "tag_name": tag,
            "name": "SISMONAUTA ETAS Chile",
            "draft": False,
            "prerelease": True,
            "body": (
                "Animación ETAS experimental. "
                "No es alerta sísmica."
            )
        }
    )

    upload_url = (
        release["upload_url"].split("{")[0]
        + "?name="
        + quote(video.name)
    )

    with video.open("rb") as stream:
        asset = api_json(
            "POST",
            upload_url,
            token=os.environ["GH_TOKEN"],
            timeout=(30, 900),
            headers={
                "Content-Type": "video/mp4",
                "Accept": "application/vnd.github+json"
            },
            data=stream
        )

    public_url = asset.get(
        "browser_download_url"
    )

    if not public_url:
        raise RuntimeError(
            "GitHub Release no devolvió "
            "URL pública del video."
        )

    return public_url, str(release["id"])


# ============================================================
# 22. PUBLICAR INSTAGRAM
# ============================================================

def publish_instagram(reel, caption, target, persist):
    token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    user = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    graph = (
        f"{IG_BASE}/{GRAPH_VERSION}"
    )

    target["status"] = "uncertain"
    persist()

    mode = os.getenv(
        "IG_UPLOAD_MODE", "url"
    ).lower()

    data = {
        "media_type": "REELS",
        "caption": caption[:2200],
        "share_to_feed": "true"
    }

    if mode == "url":
        public_url, release_id = release_video(reel)

        target["release_id"] = release_id
        target["video_url"] = public_url

        persist()

        data["video_url"] = public_url

        container = api_json(
            "POST",
            f"{graph}/{user}/media",
            token=token,
            data=data
        )

    elif mode == "resumable":
        data["upload_type"] = "resumable"

        container = api_json(
            "POST",
            f"{graph}/{user}/media",
            token=token,
            data=data
        )

    else:
        raise RuntimeError(
            "IG_UPLOAD_MODE debe ser url o resumable."
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

        if not upload_url.startswith(
            "https://rupload.facebook.com/"
        ):
            raise RuntimeError(
                "URL de carga Instagram no reconocida."
            )

        with reel.open("rb") as stream:
            response = SESSION.post(
                upload_url,
                headers={
                    "Authorization": "OAuth " + token,
                    "offset": "0",
                    "file_size": str(
                        reel.stat().st_size
                    ),
                    "Content-Type": "application/octet-stream"
                },
                data=stream,
                timeout=(30, 900)
            )

            response.raise_for_status()

    for attempt in range(30):
        state = api_json(
            "GET",
            f"{graph}/{container_id}",
            token=token,
            params={
                "fields": "status_code,status"
            }
        )

        status = state.get(
            "status_code"
        )

        log(
            "Instagram procesamiento:",
            status,
            f"{attempt+1}/30"
        )

        if status == "FINISHED":
            break

        if status in (
            "ERROR",
            "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram no pudo procesar el Reel."
            )

        time.sleep(20)

    else:
        raise RuntimeError(
            "Instagram no confirmó el procesamiento."
        )

    published = api_json(
        "POST",
        f"{graph}/{user}/media_publish",
        token=token,
        data={
            "creation_id": container_id
        }
    )

    media_id = str(
        published.get("id") or ""
    )

    if not media_id:
        raise RuntimeError(
            "Instagram no devolvió ID de publicación."
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
# 23. PUBLICAR FACEBOOK
# ============================================================

def publish_facebook(video, caption, target, persist):
    page = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    token = os.environ[
        "FACEBOOK_PAGE_TOKEN"
    ]

    target["status"] = "uncertain"
    persist()

    with video.open("rb") as stream:
        posted = api_json(
            "POST",
            f"{FB_BASE}/{page}/videos",
            token=token,
            timeout=(30, 900),
            data={
                "description": caption[:5000],
                "title": (
                    "SISMONAUTA | ETAS Chile experimental"
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
        posted.get("id") or ""
    )

    if not media_id:
        raise RuntimeError(
            "Facebook no devolvió ID del video."
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
# 24. DESCRIPCIÓN CIENTÍFICA
# ============================================================

def publication_caption():
    return (
        "🌎 SISMONAUTA | ETAS CHILE\n\n"

        "25 mapas horarios que muestran la evolución "
        "de estimaciones de un modelo ETAS experimental.\n\n"

        "El mapa izquierdo presenta la probabilidad "
        "modelada por celda para las siguientes 24 horas. "
        "El derecho muestra su variación respecto "
        "al inicio del período observado.\n\n"

        "Los marcadores destacan hasta tres zonas "
        "con mayores incrementos positivos "
        "espacialmente separados.\n\n"

        "⚠️ MODELO EXPERIMENTAL: estos resultados "
        "no están calibrados ni validados "
        "prospectivamente, y NO constituyen "
        "predicción ni alerta sísmica oficial.\n\n"

        "Datos: USGS. Cartografía: Natural Earth. "
        "Ciudades: GeoNames / OpenStreetMap.\n\n"

        "#Sismonauta #Chile #Sismologia "
        "#ETAS #Terremotos #Ciencia "
        "#Geologia #Tectonica"
    )


# ============================================================
# 25. PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA ETAS Chile: modelo "
            "científico y publicación en un archivo"
        )
    )

    parser.add_argument(
        "--out",
        default="output_etas"
    )

    parser.add_argument(
        "--publish",
        action="store_true"
    )

    # Compatibilidad con YAML anterior:
    # la opción --original ya no se necesita.
    parser.add_argument(
        "--original",
        default="",
        help=argparse.SUPPRESS
    )

    args = parser.parse_args()

    global OUTPUT_DIR, OUTPUT_MP4

    OUTPUT_DIR = Path(
        args.out
    ).resolve()

    OUTPUT_MP4 = (
        OUTPUT_DIR / OUTPUT_NAME
    )

    ensure_output()

    state_path = (
        "sismonauta_etas/state_"
        + hashlib.sha256(
            COUNTRY_NAME.casefold().encode()
        ).hexdigest()[:12]
        + ".json"
    )

    receipt = {
        "country": COUNTRY_NAME,
        "status": "starting",
        "created_at": utcnow(),
        "targets": {
            "instagram": {
                "status": "pending"
            },
            "facebook": {
                "status": "pending"
            }
        }
    }

    def persist():
        atomic_json(
            OUTPUT_DIR / "estado_publicacion.json",
            receipt
        )

        if args.publish:
            github_state_save(
                state_path,
                receipt
            )

    try:
        check_binary("ffmpeg")
        check_binary("ffprobe")

        # ----------------------------------------------------
        # MODO DE PUBLICACIÓN
        # ----------------------------------------------------

        if args.publish:
            validate_accounts()

            previous, _ = github_state_read(
                state_path
            )

            same_day = (
                str(
                    previous.get("created_at", "")
                )[:10]
                == receipt["created_at"][:10]
            )

            if same_day and previous.get("status") in (
                "published",
                "partial_or_uncertain",
                "publishing"
            ):
                raise RuntimeError(
                    "Ya existe una publicación o "
                    "un envío incierto de ETAS Chile "
                    "para esta fecha UTC. "
                    "Se bloquea para evitar duplicados."
                )

        # ----------------------------------------------------
        # CÁLCULO CIENTÍFICO
        # ----------------------------------------------------

        receipt["status"] = "generating"
        persist()

        video, metadata = run_scientific_etas()

        original_probe = ffprobe(video)

        duration = float(
            original_probe["format"]["duration"]
        )

        if duration < 3 or duration > 900:
            raise RuntimeError(
                f"Duración del MP4 no compatible: "
                f"{duration:.1f} segundos."
            )

        receipt["duration_seconds"] = duration

        receipt["video_file"] = str(video)

        receipt["scientific"] = {
            "snapshots": metadata["snapshots"],
            "mc": metadata["mc"],
            "b": metadata["b"],
            "magnitude_target": (
                metadata["magnitude_target"]
            )
        }

        persist()

        # ----------------------------------------------------
        # REEL INSTAGRAM
        # ----------------------------------------------------

        reel = make_reel(
            video
        )

        receipt["reel_file"] = str(reel)

        if not args.publish:
            receipt["status"] = "preview_only"
            persist()

            log(
                "==================================="
            )

            log(
                "ETAS CHILE GENERADO SIN PUBLICAR"
            )

            log(
                "Video original:", video
            )

            log(
                "Reel Instagram:", reel
            )

            log(
                "==================================="
            )

            return

        # ----------------------------------------------------
        # PUBLICACIÓN REAL
        # ----------------------------------------------------

        receipt["status"] = "publishing"
        persist()

        caption = publication_caption()

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

        for name, function, file in destinations:
            target = receipt["targets"][name]

            try:
                function(
                    file,
                    caption,
                    target,
                    persist
                )

            except Exception as exc:
                target["error"] = str(exc)[:400]

                if target.get("status") != "published":
                    target["status"] = "uncertain"

                persist()

                errors.append(
                    f"{name}: {exc}"
                )

        if errors:
            receipt["status"] = "partial_or_uncertain"
            persist()

            raise RuntimeError(
                " | ".join(errors)
            )

        receipt["status"] = "published"
        persist()

        log(
            "==================================="
        )

        log(
            "SISMONAUTA ETAS CHILE"
        )

        log(
            "PUBLICACIONES ACEPTADAS POR META"
        )

        log(
            "INSTAGRAM:",
            receipt["targets"]["instagram"].get("id")
        )

        log(
            "FACEBOOK:",
            receipt["targets"]["facebook"].get("id")
        )

        log(
            "==================================="
        )

    except Exception as exc:
        uncertain = any(
            target.get("status") in (
                "published",
                "uncertain"
            )
            for target in receipt["targets"].values()
        )

        if receipt["status"] != "published":
            receipt["status"] = (
                "partial_or_uncertain"
                if uncertain
                else "error"
            )

        receipt["error"] = str(exc)[:500]

        atomic_json(
            OUTPUT_DIR / "estado_publicacion.json",
            receipt
        )

        log(
            "ERROR:",
            str(exc)
        )

        raise


# ============================================================
# 26. INICIO
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
