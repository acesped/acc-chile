#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
============================================================
DESPLAZAMIENTO GEODÉSICO RESIDUAL GNSS OBSERVADO
============================================================

Fuente sísmica:
    Centro Sismológico Nacional de Chile
    https://www.sismologia.cl

Fuente GNSS:
    CSN / OWL
    Red C1
    Canales LXE / LXN / LXZ

Ejecución:
    UN SOLO CICLO

Diseñado para:
    GitHub Actions

Flujo:
    CSN sismologia.cl
        ↓
    detección de sismos recientes
        ↓
    estaciones GNSS cercanas
        ↓
    PRE robusto
        ↓
    POST temprano
        ↓
    POST tardío
        ↓
    RES = POST tardío - PRE
        ↓
    estabilidad + incertidumbre + SNR
        ↓
    persistencia temporal
        ↓
    coherencia espacial
        ↓
    VALID_RES
        ↓
    mapa
        ↓
    publicación en X

============================================================
"""


# ============================================================
# IMPORTS
# ============================================================

import io
import os
import re
import json
import math
import hashlib
import zipfile
import textwrap
import traceback
import subprocess

from pathlib import Path

import requests
import numpy as np
import pandas as pd

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.patches as patches

import geopandas as gpd

from bs4 import BeautifulSoup
from obspy import read as obspy_read

import tweepy


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

# ------------------------------------------------------------
# PUBLICACIÓN
# ------------------------------------------------------------

ENABLE_X = True

# False = publica realmente
# True  = sólo prueba
DRY_RUN = False

TEST_LATEST_EVENT = False


# ============================================================
# CREDENCIALES X
# ============================================================
#
# REEMPLAZAR LOS 4 VALORES.
#
# IMPORTANTE:
# El repositorio debe ser PRIVADO.
#
# ============================================================

X_API_KEY = "t5792SuVlfx41hDSWYmHVQJiG"
X_API_SECRET = "WCOUY5z1SqlylH1XYQM9P5guowMC3RogGWIF2hLvSFJKna3HVw"
X_ACCESS_TOKEN = "2106457141796052993-NpB8nf6yLTbPjJEu4TIHwJfbCCHU7h"
X_ACCESS_TOKEN_SECRET = "jCDFy4L4suq6Z6qnHOhJ4CuduqWs5173JgriRqn76L5MZ"


# ============================================================
# DETECCIÓN SÍSMICA
# ============================================================
#
# GitHub Actions puede ejecutarse cada 5 minutos.
#
# Se mantiene una ventana de 10 minutos para evitar perder
# eventos si el scheduler se retrasa.
#
# ============================================================

RECENT_EVENT_WINDOW_MINUTES = 10

# El RES final requiere datos hasta +600 s.

MIN_ANALYSIS_AGE_SECONDS = 615

# Tiempo máximo para continuar intentando procesar un evento
# después de haber sido detectado.

MAX_PENDING_RETRY_MINUTES = 90


# ============================================================
# ESCALA GNSS
# ============================================================
#
# Actualmente asumimos:
#
# 1 metro = 1.000.000 counts
#
# La publicación está permitida aunque SCALE_VERIFIED=False.
#
# ============================================================

COUNTS_PER_METER = 1_000_000.0

COUNTS_PER_MM = (
    COUNTS_PER_METER
    /
    1000.0
)

SCALE_VERIFIED = False

REQUIRE_SCALE_VERIFIED_FOR_REAL_POST = False


# ============================================================
# SISMOLOGIA.CL
# ============================================================

SISMOLOGIA_BASE = (
    "https://www.sismologia.cl"
)

CATALOG_TEMPLATE = (
    SISMOLOGIA_BASE
    + "/sismicidad/catalogo/"
      "{year}/{month}/{yyyymmdd}.html"
)


# ============================================================
# GNSS CSN
# ============================================================

FDSN_DATASELECT_URL = (
    "https://owl.csn.uchile.cl/"
    "fdsnws/dataselect/1/query"
)

FDSN_STATION_URL = (
    "https://owl.csn.uchile.cl/"
    "fdsnws/station/1/query"
)

GNSS_NETWORK = "C1"

GNSS_CHANNEL_PATTERN = "LX?"


# ============================================================
# ESTACIONES
# ============================================================

MAX_STATION_DISTANCE_KM = 300.0

MAX_NEAREST_STATIONS = 15


# ============================================================
# VENTANAS TEMPORALES
# ============================================================

# PRE:
# 10 minutos antes → 2 minutos antes

PRE_START_SECONDS = -600

PRE_END_SECONDS = -120


# POST temprano

EARLY_POST_MIN_START_SECONDS = 180

EARLY_POST_END_SECONDS = 300

EARLY_POST_AFTER_SLOW_WAVE_SECONDS = 45


# POST tardío
#
# Es el utilizado para calcular el RES publicado.

LATE_POST_START_SECONDS = 480

LATE_POST_END_SECONDS = 600


# ============================================================
# BLOQUES ROBUSTOS
# ============================================================

BLOCK_SECONDS = 30

MIN_PRE_BLOCKS = 8

MIN_EARLY_POST_BLOCKS = 2

MIN_LATE_POST_BLOCKS = 3

MIN_SAMPLES_PER_BLOCK = 10


# ============================================================
# VELOCIDADES HEURÍSTICAS
# ============================================================

FAST_WAVE_VELOCITY_KM_S = 8.0

SLOW_WAVE_VELOCITY_KM_S = 2.5


# ============================================================
# INCERTIDUMBRE
# ============================================================

MIN_COMPONENT_CENTER_UNCERTAINTY_MM = 0.50


# ============================================================
# ESTABILIDAD
# ============================================================

MAX_PRE_BLOCK_SCATTER_H_MM = 12.0

MAX_POST_BLOCK_SCATTER_H_MM = 12.0

MAX_PRE_DRIFT_H_MM_PER_MIN = 5.0

MAX_POST_DRIFT_H_MM_PER_MIN = 5.0


# ============================================================
# DETECCIÓN RES
# ============================================================

MIN_RES_HORIZONTAL_MM = 1.0

MIN_RES_SNR_CANDIDATE = 2.0

MIN_RES_SNR_MODERATE = 2.5

MIN_RES_SNR_HIGH = 3.5


# ============================================================
# PERSISTENCIA TEMPORAL
# ============================================================

PERSISTENCE_ABS_TOL_MM = 5.0

PERSISTENCE_REL_TOL = 0.75

PERSISTENCE_MAX_AZIMUTH_DIFF_DEG = 75.0

PERSISTENCE_DIRECTION_MIN_MM = 3.0


# ============================================================
# COHERENCIA ESPACIAL
# ============================================================

COHERENCE_MAX_DISTANCE_KM = 180.0

COHERENCE_MAX_VECTOR_DIFF_MM = 12.0

COHERENCE_REL_VECTOR_DIFF = 1.0

COHERENCE_MAX_AZIMUTH_DIFF_DEG = 80.0

COHERENCE_DIRECTION_MIN_MM = 3.0

MIN_COHERENT_NEIGHBORS_HIGH = 1


# ============================================================
# PLAUSIBILIDAD
# ============================================================

ENABLE_PLAUSIBILITY_FILTER = True


# ============================================================
# PUBLICACIÓN
# ============================================================

PUBLISH_QC_LEVELS = {
    "ALTO",
    "MODERADO"
}


# ============================================================
# MAPA
# ============================================================

MAP_LON_MIN = -77.5

MAP_LON_MAX = -65.0

MAP_LAT_MIN = -58.0

MAP_LAT_MAX = -17.0

VECTOR_TARGET_DEGREES = 0.42


# ============================================================
# TEXTOS DE LA LÁMINA
# ============================================================

BRAND_TITLE = (
    "Desplazamiento geodésico residual GNSS observado"
)

BRAND_SUBTITLE = (
    "Reporte automático generado por fuente externa"
)

MAP_TITLE = (
    "Vectores RES estaciones cercanas"
)


# ============================================================
# COLORES
# ============================================================

COLOR_NAVY = "#0F172A"

COLOR_RED = "#DC2626"

COLOR_ORANGE = "#F59E0B"

COLOR_GREEN = "#059669"

COLOR_BLUE = "#2563EB"

COLOR_GRAY = "#64748B"

COLOR_LIGHT_GRAY = "#F1F5F9"

COLOR_BORDER = "#CBD5E1"

COLOR_MUTED = "#475569"

COLOR_WHITE = "#FFFFFF"

COLOR_SEA = "#F8FAFC"

COLOR_LAND = "#E2E8F0"


# ============================================================
# NATURAL EARTH
# ============================================================

NATURAL_EARTH_URL = (
    "https://naciscdn.org/"
    "naturalearth/10m/cultural/"
    "ne_10m_admin_0_countries.zip"
)


# ============================================================
# DIRECTORIOS
# ============================================================

BASE_DIR = Path(
    os.getenv(
        "GITHUB_WORKSPACE",
        Path(__file__).resolve().parent
    )
)

DATA_DIR = (
    BASE_DIR
    /
    "data"
)

OUTPUT_DIR = (
    BASE_DIR
    /
    "output"
)

CACHE_DIR = (
    BASE_DIR
    /
    ".cache_csn"
)

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

CACHE_DIR.mkdir(
    parents=True,
    exist_ok=True
)

STATE_FILE = (
    DATA_DIR
    /
    "monitor_state.json"
)

NE_ZIP_FILE = (
    CACHE_DIR
    /
    "naturalearth.zip"
)

NE_DIR = (
    CACHE_DIR
    /
    "naturalearth"
)


# ============================================================
# PERSISTENCIA DEL ESTADO EN GITHUB
# ============================================================

PERSIST_STATE_TO_GIT = True


# ============================================================
# HTTP
# ============================================================

http = requests.Session()

http.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "CSN-GNSS-RES-Monitor/1.0"
    }
)


# ============================================================
# UTILIDADES
# ============================================================

def finite(value):

    try:

        return bool(
            np.isfinite(
                float(value)
            )
        )

    except Exception:

        return False


def numeric(value):

    try:

        if value is None:

            return np.nan

        if isinstance(
            value,
            (
                int,
                float,
                np.integer,
                np.floating
            )
        ):

            value = float(
                value
            )

            return (
                value
                if np.isfinite(value)
                else np.nan
            )

        text = (
            str(value)
            .strip()
            .replace(",", ".")
        )

        match = re.search(
            r"[-+]?\d+(?:\.\d+)?",
            text
        )

        if not match:

            return np.nan

        return float(
            match.group()
        )

    except Exception:

        return np.nan


def ensure_utc(value):

    timestamp = pd.Timestamp(
        value
    )

    if timestamp.tzinfo is None:

        timestamp = timestamp.tz_localize(
            "UTC"
        )

    else:

        timestamp = timestamp.tz_convert(
            "UTC"
        )

    return timestamp


def repair_text(text):

    if text is None:

        return ""

    text = str(
        text
    )

    if (
        "Ã" in text
        or
        "Â" in text
    ):

        try:

            text = (
                text
                .encode(
                    "latin1"
                )
                .decode(
                    "utf-8"
                )
            )

        except Exception:

            pass

    return text.strip()


def fmt(
    value,
    decimals=1,
    suffix=""
):

    if not finite(
        value
    ):

        return "-"

    return (
        f"{float(value):.{decimals}f}"
        f"{suffix}"
    )


def format_age(seconds):

    if not finite(
        seconds
    ):

        return "-"

    seconds = max(
        0,
        int(seconds)
    )

    if seconds < 60:

        return (
            f"{seconds} s"
        )

    minutes = (
        seconds
        //
        60
    )

    if minutes < 60:

        return (
            f"{minutes} min"
        )

    hours = (
        minutes
        //
        60
    )

    minutes = (
        minutes
        %
        60
    )

    return (
        f"{hours} h "
        f"{minutes} min"
    )


def haversine_km(
    lat1,
    lon1,
    lat2,
    lon2
):

    radius = 6371.0088

    lat1 = math.radians(
        float(lat1)
    )

    lon1 = math.radians(
        float(lon1)
    )

    lat2 = math.radians(
        float(lat2)
    )

    lon2 = math.radians(
        float(lon2)
    )

    delta_lat = (
        lat2
        -
        lat1
    )

    delta_lon = (
        lon2
        -
        lon1
    )

    a = (
        math.sin(
            delta_lat / 2
        ) ** 2
        +
        math.cos(
            lat1
        )
        *
        math.cos(
            lat2
        )
        *
        math.sin(
            delta_lon / 2
        ) ** 2
    )

    return (
        2
        *
        radius
        *
        math.asin(
            math.sqrt(a)
        )
    )


def vector_azimuth(
    east_mm,
    north_mm
):

    if (
        not finite(east_mm)
        or
        not finite(north_mm)
    ):

        return np.nan

    return (
        np.degrees(
            np.arctan2(
                east_mm,
                north_mm
            )
        )
        +
        360
    ) % 360


def angular_difference_deg(
    angle_a,
    angle_b
):

    if (
        not finite(angle_a)
        or
        not finite(angle_b)
    ):

        return np.nan

    difference = (
        abs(
            float(angle_a)
            -
            float(angle_b)
        )
        %
        360
    )

    return min(
        difference,
        360 - difference
    )


def mad(values):

    values = np.asarray(
        values,
        dtype=float
    )

    values = values[
        np.isfinite(
            values
        )
    ]

    if len(values) == 0:

        return np.nan

    center = np.median(
        values
    )

    return np.median(
        np.abs(
            values
            -
            center
        )
    )


def robust_sigma(values):

    value = mad(
        values
    )

    if not finite(
        value
    ):

        return np.nan

    return (
        1.4826
        *
        value
    )


def plausibility_limit_mm(
    magnitude
):

    if not finite(
        magnitude
    ):

        return np.inf

    magnitude = float(
        magnitude
    )

    if magnitude < 4.0:

        return 50.0

    if magnitude < 5.0:

        return 150.0

    if magnitude < 6.0:

        return 500.0

    return np.inf


# ============================================================
# ESTADO
# ============================================================

def default_state():

    return {
        "detected": {},
        "posted": {},
        "dry_run_seen": {},
        "expired": {}
    }


def load_state():

    if not STATE_FILE.exists():

        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            state = json.load(
                file
            )

    except Exception as exc:

        print(
            "No se pudo leer estado:",
            exc
        )

        state = default_state()

    for key in [
        "detected",
        "posted",
        "dry_run_seen",
        "expired"
    ]:

        state.setdefault(
            key,
            {}
        )

    return state


def save_state(
    state
):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# PERSISTIR ESTADO EN GITHUB
# ============================================================

def persist_state_to_git():

    if not PERSIST_STATE_TO_GIT:

        return

    if (
        os.getenv(
            "GITHUB_ACTIONS",
            ""
        ).lower()
        !=
        "true"
    ):

        return

    if not STATE_FILE.exists():

        return

    try:

        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "github-actions[bot]"
            ],
            cwd=BASE_DIR,
            check=True
        )

        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com"
            ],
            cwd=BASE_DIR,
            check=True
        )

        relative_state = str(
            STATE_FILE.relative_to(
                BASE_DIR
            )
        )

        subprocess.run(
            [
                "git",
                "add",
                relative_state
            ],
            cwd=BASE_DIR,
            check=True
        )

        diff = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--quiet"
            ],
            cwd=BASE_DIR
        )

        if diff.returncode == 0:

            print(
                "Estado sin cambios."
            )

            return

        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update GNSS monitor state [skip ci]"
            ],
            cwd=BASE_DIR,
            check=True
        )

        subprocess.run(
            [
                "git",
                "push"
            ],
            cwd=BASE_DIR,
            check=True
        )

        print(
            "Estado persistido en GitHub."
        )

    except Exception as exc:

        print(
            "ADVERTENCIA: "
            "no se pudo persistir monitor_state.json:"
        )

        print(
            exc
        )


# ============================================================
# EVENTOS
# ============================================================

def make_event_id(
    event_time,
    latitude,
    longitude,
    depth,
    magnitude
):

    fingerprint = (
        f"{ensure_utc(event_time)}|"
        f"{float(latitude):.4f}|"
        f"{float(longitude):.4f}|"
        f"{depth}|"
        f"{magnitude}"
    )

    return (
        "csn_"
        +
        hashlib.sha1(
            fingerprint.encode(
                "utf-8"
            )
        ).hexdigest()[:16]
    )


def serialize_event(
    event
):

    return {

        "event_id":
            str(
                event[
                    "event_id"
                ]
            ),

        "time":
            str(
                ensure_utc(
                    event[
                        "time"
                    ]
                )
            ),

        "latitude":
            float(
                event[
                    "latitude"
                ]
            ),

        "longitude":
            float(
                event[
                    "longitude"
                ]
            ),

        "depth_km":
            (
                float(
                    event[
                        "depth_km"
                    ]
                )
                if finite(
                    event[
                        "depth_km"
                    ]
                )
                else
                None
            ),

        "magnitude":
            (
                float(
                    event[
                        "magnitude"
                    ]
                )
                if finite(
                    event[
                        "magnitude"
                    ]
                )
                else
                None
            ),

        "mag_type":
            str(
                event.get(
                    "mag_type",
                    ""
                )
            ),

        "region":
            repair_text(
                event.get(
                    "region",
                    ""
                )
            )
    }


def deserialize_event(
    data
):

    return {

        "event_id":
            str(
                data[
                    "event_id"
                ]
            ),

        "time":
            ensure_utc(
                data[
                    "time"
                ]
            ),

        "latitude":
            float(
                data[
                    "latitude"
                ]
            ),

        "longitude":
            float(
                data[
                    "longitude"
                ]
            ),

        "depth_km":
            numeric(
                data.get(
                    "depth_km"
                )
            ),

        "magnitude":
            numeric(
                data.get(
                    "magnitude"
                )
            ),

        "mag_type":
            str(
                data.get(
                    "mag_type",
                    ""
                )
            ),

        "region":
            repair_text(
                data.get(
                    "region",
                    ""
                )
            )
    }


# ============================================================
# CATÁLOGO CSN
# ============================================================

def catalog_url_for_date(
    date_utc
):

    date_utc = pd.Timestamp(
        date_utc
    )

    return CATALOG_TEMPLATE.format(

        year=
            date_utc.strftime(
                "%Y"
            ),

        month=
            date_utc.strftime(
                "%m"
            ),

        yyyymmdd=
            date_utc.strftime(
                "%Y%m%d"
            )
    )


def parse_daily_catalog(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    events = []

    for row in soup.find_all(
        "tr"
    ):

        cells = [
            repair_text(
                cell.get_text(
                    " ",
                    strip=True
                )
            )
            for cell in
            row.find_all(
                [
                    "td",
                    "th"
                ]
            )
        ]

        if len(cells) < 5:

            continue

        utc_match = re.search(
            r"\d{4}-\d{2}-\d{2}\s+"
            r"\d{2}:\d{2}:\d{2}",
            cells[1]
        )

        if not utc_match:

            continue

        event_time = pd.to_datetime(
            utc_match.group(),
            format=
                "%Y-%m-%d %H:%M:%S",
            utc=True,
            errors="coerce"
        )

        if pd.isna(
            event_time
        ):

            continue

        region = re.sub(
            r"^\d{4}-\d{2}-\d{2}\s+"
            r"\d{2}:\d{2}:\d{2}\s*",
            "",
            cells[0]
        ).strip()

        region = repair_text(
            region
        )

        if not region:

            region = "Chile"

        coordinates = re.findall(
            r"[-+]?\d+(?:[.,]\d+)?",
            cells[2]
        )

        if len(
            coordinates
        ) < 2:

            continue

        latitude = numeric(
            coordinates[0]
        )

        longitude = numeric(
            coordinates[1]
        )

        if (
            not finite(latitude)
            or
            not finite(longitude)
        ):

            continue

        depth = numeric(
            cells[3]
        )

        magnitude_match = re.search(
            r"([-+]?\d+(?:[.,]\d+)?)"
            r"(?:\s+([A-Za-z0-9]+))?",
            cells[4]
        )

        if magnitude_match:

            magnitude = numeric(
                magnitude_match.group(
                    1
                )
            )

            magnitude_type = (
                magnitude_match.group(
                    2
                )
                or
                ""
            )

        else:

            magnitude = np.nan

            magnitude_type = ""

        event_id = make_event_id(
            event_time,
            latitude,
            longitude,
            depth,
            magnitude
        )

        events.append(
            {
                "event_id":
                    event_id,

                "time":
                    event_time,

                "latitude":
                    float(
                        latitude
                    ),

                "longitude":
                    float(
                        longitude
                    ),

                "depth_km":
                    depth,

                "magnitude":
                    magnitude,

                "mag_type":
                    magnitude_type,

                "region":
                    region
            }
        )

    return events


def fetch_daily_catalog(
    date_utc
):

    url = catalog_url_for_date(
        date_utc
    )

    print(
        "Catálogo:",
        url
    )

    try:

        response = http.get(
            url,
            timeout=30
        )

        response.raise_for_status()

    except Exception as exc:

        print(
            "ERROR catálogo:",
            exc
        )

        return []

    events = parse_daily_catalog(
        response.text
    )

    print(
        "Eventos:",
        len(
            events
        )
    )

    return events


def get_current_earthquakes():

    now = pd.Timestamp.now(
        tz="UTC"
    )

    dates = [

        now.normalize(),

        (
            now
            -
            pd.Timedelta(
                days=1
            )
        ).normalize()
    ]

    rows = []

    for date_utc in dates:

        rows.extend(
            fetch_daily_catalog(
                date_utc
            )
        )

    if not rows:

        return pd.DataFrame()

    dataframe = pd.DataFrame(
        rows
    )

    dataframe[
        "time"
    ] = pd.to_datetime(
        dataframe[
            "time"
        ],
        utc=True,
        errors="coerce"
    )

    return (
        dataframe
        .dropna(
            subset=[
                "time",
                "latitude",
                "longitude"
            ]
        )
        .drop_duplicates(
            subset=[
                "event_id"
            ]
        )
        .sort_values(
            "time",
            ascending=False
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# INVENTARIO GNSS
# ============================================================

def get_gnss_station_inventory():

    params = {

        "network":
            GNSS_NETWORK,

        "channel":
            GNSS_CHANNEL_PATTERN,

        "level":
            "channel",

        "format":
            "text",

        "nodata":
            204
    }

    try:

        response = http.get(
            FDSN_STATION_URL,
            params=params,
            timeout=45
        )

    except Exception as exc:

        print(
            "Error inventario GNSS:",
            exc
        )

        return pd.DataFrame()

    if response.status_code != 200:

        print(
            "FDSN Station HTTP:",
            response.status_code
        )

        return pd.DataFrame()

    rows = []

    header = None

    for raw_line in (
        response.text
        .splitlines()
    ):

        line = raw_line.strip()

        if not line:

            continue

        if line.startswith(
            "#"
        ):

            possible = [
                item.strip()
                for item in
                line
                .lstrip("#")
                .split("|")
            ]

            if "Network" in possible:

                header = possible

            continue

        parts = [
            item.strip()
            for item in
            line.split("|")
        ]

        if header:

            record = dict(
                zip(
                    header,
                    parts
                )
            )

            network = record.get(
                "Network"
            )

            station = record.get(
                "Station"
            )

            latitude = numeric(
                record.get(
                    "Latitude"
                )
            )

            longitude = numeric(
                record.get(
                    "Longitude"
                )
            )

        else:

            if len(parts) < 6:

                continue

            network = parts[0]

            station = parts[1]

            latitude = numeric(
                parts[4]
            )

            longitude = numeric(
                parts[5]
            )

        if (
            station
            and
            finite(latitude)
            and
            finite(longitude)
        ):

            network = (
                network
                or
                GNSS_NETWORK
            )

            rows.append(
                {
                    "network":
                        str(
                            network
                        ),

                    "station":
                        str(
                            station
                        ),

                    "key":
                        f"{network}.{station}",

                    "latitude":
                        float(
                            latitude
                        ),

                    "longitude":
                        float(
                            longitude
                        )
                }
            )

    if not rows:

        return pd.DataFrame()

    return (
        pd.DataFrame(
            rows
        )
        .drop_duplicates(
            subset=[
                "network",
                "station"
            ]
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# ESTACIONES CERCANAS
# ============================================================

def select_nearby_stations(
    event,
    stations
):

    work = stations.copy()

    work[
        "distance_km"
    ] = [

        haversine_km(
            event[
                "latitude"
            ],
            event[
                "longitude"
            ],
            row[
                "latitude"
            ],
            row[
                "longitude"
            ]
        )

        for _, row in
        work.iterrows()
    ]

    return (
        work[
            work[
                "distance_km"
            ]
            <=
            MAX_STATION_DISTANCE_KM
        ]
        .sort_values(
            "distance_km"
        )
        .head(
            MAX_NEAREST_STATIONS
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# DESCARGA HISTÓRICA GNSS
# ============================================================

def fetch_station_history(
    network,
    station,
    start_time,
    end_time
):

    params = {

        "network":
            network,

        "station":
            station,

        "location":
            "*",

        "channel":
            GNSS_CHANNEL_PATTERN,

        "starttime":
            ensure_utc(
                start_time
            ).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),

        "endtime":
            ensure_utc(
                end_time
            ).strftime(
                "%Y-%m-%dT%H:%M:%S"
            ),

        "nodata":
            204
    }

    try:

        response = http.get(
            FDSN_DATASELECT_URL,
            params=params,
            timeout=45
        )

    except Exception as exc:

        return (
            None,
            f"HTTP {exc}"
        )

    if response.status_code == 204:

        return (
            None,
            "sin datos"
        )

    if response.status_code != 200:

        return (
            None,
            f"HTTP {response.status_code}"
        )

    try:

        stream = obspy_read(
            io.BytesIO(
                response.content
            )
        )

    except Exception as exc:

        return (
            None,
            f"MiniSEED {exc}"
        )

    if len(stream) == 0:

        return (
            None,
            "vacío"
        )

    return (
        stream,
        "OK"
    )


# ============================================================
# STREAM → E / N / Z
# ============================================================

def stream_components(
    stream
):

    components = {
        "E": [],
        "N": [],
        "Z": []
    }

    for trace in stream:

        channel = (
            str(
                trace.stats.channel
            )
            .upper()
        )

        if channel.endswith(
            "E"
        ):

            component = "E"

        elif channel.endswith(
            "N"
        ):

            component = "N"

        elif channel.endswith(
            "Z"
        ):

            component = "Z"

        else:

            continue

        dataframe = pd.DataFrame(
            {
                "time":
                    pd.to_datetime(
                        trace.times(
                            "timestamp"
                        ),
                        unit="s",
                        utc=True
                    ),

                component:
                    np.asarray(
                        trace.data,
                        dtype=float
                    )
            }
        )

        components[
            component
        ].append(
            dataframe
        )

    output = {}

    for component in [
        "E",
        "N",
        "Z"
    ]:

        if not components[
            component
        ]:

            output[
                component
            ] = pd.DataFrame(
                columns=[
                    "time",
                    component
                ]
            )

        else:

            output[
                component
            ] = (
                pd.concat(
                    components[
                        component
                    ],
                    ignore_index=True
                )
                .drop_duplicates(
                    subset=[
                        "time"
                    ]
                )
                .sort_values(
                    "time"
                )
                .reset_index(
                    drop=True
                )
            )

    return output


def align_components(
    components
):

    east = components[
        "E"
    ].copy()

    north = components[
        "N"
    ].copy()

    up = components[
        "Z"
    ].copy()

    if (
        east.empty
        or
        north.empty
    ):

        return pd.DataFrame()

    merged = pd.merge_asof(

        east.sort_values(
            "time"
        ),

        north.sort_values(
            "time"
        ),

        on="time",

        direction="nearest",

        tolerance=
            pd.Timedelta(
                seconds=1
            )
    )

    merged = merged.dropna(
        subset=[
            "E",
            "N"
        ]
    )

    if not up.empty:

        merged = pd.merge_asof(

            merged.sort_values(
                "time"
            ),

            up.sort_values(
                "time"
            ),

            on="time",

            direction="nearest",

            tolerance=
                pd.Timedelta(
                    seconds=1
                )
        )

    else:

        merged[
            "Z"
        ] = np.nan

    return (
        merged
        .sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# TIEMPOS DE PROPAGACIÓN
# ============================================================

def calculate_wave_times(
    epicentral_distance_km,
    depth_km
):

    if not finite(
        depth_km
    ):

        depth_km = 0.0

    hypocentral_distance = math.sqrt(

        float(
            epicentral_distance_km
        ) ** 2

        +

        float(
            depth_km
        ) ** 2
    )

    fast_arrival = (
        hypocentral_distance
        /
        FAST_WAVE_VELOCITY_KM_S
    )

    slow_arrival = (
        hypocentral_distance
        /
        SLOW_WAVE_VELOCITY_KM_S
    )

    early_start = max(

        EARLY_POST_MIN_START_SECONDS,

        slow_arrival
        +
        EARLY_POST_AFTER_SLOW_WAVE_SECONDS
    )

    return {

        "hypocentral_distance_km":
            hypocentral_distance,

        "fast_arrival_seconds":
            fast_arrival,

        "slow_arrival_seconds":
            slow_arrival,

        "early_post_start_seconds":
            early_start
    }


# ============================================================
# BLOQUES
# ============================================================

def make_block_medians(
    data,
    start_seconds,
    end_seconds
):

    work = data[
        (
            data[
                "seconds_from_event"
            ]
            >=
            start_seconds
        )
        &
        (
            data[
                "seconds_from_event"
            ]
            <
            end_seconds
        )
    ].copy()

    if work.empty:

        return pd.DataFrame()

    work[
        "block_id"
    ] = np.floor(
        (
            work[
                "seconds_from_event"
            ]
            -
            start_seconds
        )
        /
        BLOCK_SECONDS
    ).astype(
        int
    )

    rows = []

    for block_id, block in (
        work.groupby(
            "block_id"
        )
    ):

        if (
            len(block)
            <
            MIN_SAMPLES_PER_BLOCK
        ):

            continue

        row = {

            "block_id":
                int(
                    block_id
                ),

            "time_seconds":
                float(
                    np.nanmedian(
                        block[
                            "seconds_from_event"
                        ]
                    )
                ),

            "sample_count":
                len(
                    block
                ),

            "E":
                float(
                    np.nanmedian(
                        block[
                            "E"
                        ]
                    )
                ),

            "N":
                float(
                    np.nanmedian(
                        block[
                            "N"
                        ]
                    )
                )
        }

        if (
            "Z" in block.columns
            and
            block[
                "Z"
            ].notna().any()
        ):

            row[
                "Z"
            ] = float(
                np.nanmedian(
                    block[
                        "Z"
                    ]
                )
            )

        else:

            row[
                "Z"
            ] = np.nan

        rows.append(
            row
        )

    if not rows:

        return pd.DataFrame()

    return (
        pd.DataFrame(
            rows
        )
        .sort_values(
            "time_seconds"
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# POSICIÓN ROBUSTA
# ============================================================

def robust_block_component(
    blocks,
    component
):

    values = np.asarray(
        blocks[
            component
        ],
        dtype=float
    )

    values = values[
        np.isfinite(
            values
        )
    ]

    count = len(
        values
    )

    if count == 0:

        return {
            "center": np.nan,
            "scatter_mm": np.nan,
            "center_unc_mm": np.nan,
            "n": 0
        }

    center = float(
        np.median(
            values
        )
    )

    scatter_counts = robust_sigma(
        values
    )

    scatter_mm = (
        scatter_counts
        /
        COUNTS_PER_MM
        if finite(
            scatter_counts
        )
        else
        np.nan
    )

    if finite(
        scatter_mm
    ):

        center_uncertainty = (
            scatter_mm
            /
            math.sqrt(
                max(
                    count,
                    1
                )
            )
        )

        center_uncertainty = max(
            center_uncertainty,
            MIN_COMPONENT_CENTER_UNCERTAINTY_MM
        )

    else:

        center_uncertainty = (
            MIN_COMPONENT_CENTER_UNCERTAINTY_MM
        )

    return {
        "center":
            center,

        "scatter_mm":
            scatter_mm,

        "center_unc_mm":
            center_uncertainty,

        "n":
            count
    }


def robust_block_position(
    blocks
):

    east = robust_block_component(
        blocks,
        "E"
    )

    north = robust_block_component(
        blocks,
        "N"
    )

    up = robust_block_component(
        blocks,
        "Z"
    )

    scatter_horizontal = (
        math.sqrt(
            east[
                "scatter_mm"
            ] ** 2
            +
            north[
                "scatter_mm"
            ] ** 2
        )
        if (
            finite(
                east[
                    "scatter_mm"
                ]
            )
            and
            finite(
                north[
                    "scatter_mm"
                ]
            )
        )
        else
        np.nan
    )

    return {
        "E":
            east,

        "N":
            north,

        "Z":
            up,

        "scatter_H_mm":
            scatter_horizontal
    }


# ============================================================
# DERIVA ROBUSTA
# ============================================================
#
# La deriva sólo se usa para QC.
#
# NO se resta.
# NO se extrapola.
#
# ============================================================

def block_slope_mm_per_min(
    blocks,
    component
):

    work = blocks[
        [
            "time_seconds",
            component
        ]
    ].dropna()

    if len(work) < 3:

        return np.nan

    times = np.asarray(
        work[
            "time_seconds"
        ],
        dtype=float
    )

    values = np.asarray(
        work[
            component
        ],
        dtype=float
    )

    midpoint = np.median(
        times
    )

    first = (
        times
        <=
        midpoint
    )

    second = (
        times
        >
        midpoint
    )

    if (
        first.sum() < 1
        or
        second.sum() < 1
    ):

        return np.nan

    time_1 = np.median(
        times[
            first
        ]
    )

    time_2 = np.median(
        times[
            second
        ]
    )

    value_1 = np.median(
        values[
            first
        ]
    )

    value_2 = np.median(
        values[
            second
        ]
    )

    if time_2 == time_1:

        return np.nan

    counts_per_second = (
        value_2
        -
        value_1
    ) / (
        time_2
        -
        time_1
    )

    return (
        counts_per_second
        /
        COUNTS_PER_MM
        *
        60.0
    )


def horizontal_block_drift(
    blocks
):

    east = block_slope_mm_per_min(
        blocks,
        "E"
    )

    north = block_slope_mm_per_min(
        blocks,
        "N"
    )

    if (
        not finite(east)
        or
        not finite(north)
    ):

        return np.nan

    return math.sqrt(
        east ** 2
        +
        north ** 2
    )


# ============================================================
# CALCULAR RES
# ============================================================

def calculate_residual(
    pre_position,
    post_position
):

    east_pre = (
        pre_position[
            "E"
        ][
            "center"
        ]
    )

    north_pre = (
        pre_position[
            "N"
        ][
            "center"
        ]
    )

    up_pre = (
        pre_position[
            "Z"
        ][
            "center"
        ]
    )

    east_post = (
        post_position[
            "E"
        ][
            "center"
        ]
    )

    north_post = (
        post_position[
            "N"
        ][
            "center"
        ]
    )

    up_post = (
        post_position[
            "Z"
        ][
            "center"
        ]
    )

    if (
        not finite(
            east_pre
        )
        or
        not finite(
            north_pre
        )
        or
        not finite(
            east_post
        )
        or
        not finite(
            north_post
        )
    ):

        return None

    delta_east = (
        east_post
        -
        east_pre
    ) / COUNTS_PER_MM

    delta_north = (
        north_post
        -
        north_pre
    ) / COUNTS_PER_MM

    delta_up = (
        (
            up_post
            -
            up_pre
        )
        /
        COUNTS_PER_MM
        if (
            finite(
                up_post
            )
            and
            finite(
                up_pre
            )
        )
        else
        np.nan
    )

    delta_horizontal = math.sqrt(
        delta_east ** 2
        +
        delta_north ** 2
    )

    delta_3d = (
        math.sqrt(
            delta_horizontal ** 2
            +
            delta_up ** 2
        )
        if finite(
            delta_up
        )
        else
        np.nan
    )

    sigma_east = math.sqrt(

        pre_position[
            "E"
        ][
            "center_unc_mm"
        ] ** 2

        +

        post_position[
            "E"
        ][
            "center_unc_mm"
        ] ** 2
    )

    sigma_north = math.sqrt(

        pre_position[
            "N"
        ][
            "center_unc_mm"
        ] ** 2

        +

        post_position[
            "N"
        ][
            "center_unc_mm"
        ] ** 2
    )

    sigma_horizontal = math.sqrt(
        sigma_east ** 2
        +
        sigma_north ** 2
    )

    snr = (
        delta_horizontal
        /
        sigma_horizontal
        if (
            finite(
                sigma_horizontal
            )
            and
            sigma_horizontal > 0
        )
        else
        np.nan
    )

    return {

        "dE_mm":
            delta_east,

        "dN_mm":
            delta_north,

        "dU_mm":
            delta_up,

        "dH_mm":
            delta_horizontal,

        "d3D_mm":
            delta_3d,

        "azimuth_deg":
            vector_azimuth(
                delta_east,
                delta_north
            ),

        "sigma_H_mm":
            sigma_horizontal,

        "snr":
            snr
    }


# ============================================================
# PERSISTENCIA TEMPORAL
# ============================================================

def evaluate_persistence(
    early_res,
    late_res
):

    if (
        early_res is None
        or
        late_res is None
    ):

        return {
            "available": False,
            "valid": False,
            "vector_difference_mm": np.nan,
            "azimuth_difference_deg": np.nan
        }

    vector_difference = math.sqrt(

        (
            early_res[
                "dE_mm"
            ]
            -
            late_res[
                "dE_mm"
            ]
        ) ** 2

        +

        (
            early_res[
                "dN_mm"
            ]
            -
            late_res[
                "dN_mm"
            ]
        ) ** 2
    )

    allowed_difference = max(

        PERSISTENCE_ABS_TOL_MM,

        PERSISTENCE_REL_TOL
        *
        max(
            late_res[
                "dH_mm"
            ],
            1.0
        )
    )

    amplitude_valid = (
        vector_difference
        <=
        allowed_difference
    )

    azimuth_difference = angular_difference_deg(

        early_res[
            "azimuth_deg"
        ],

        late_res[
            "azimuth_deg"
        ]
    )

    if (
        early_res[
            "dH_mm"
        ]
        >=
        PERSISTENCE_DIRECTION_MIN_MM
        and
        late_res[
            "dH_mm"
        ]
        >=
        PERSISTENCE_DIRECTION_MIN_MM
    ):

        direction_valid = (
            finite(
                azimuth_difference
            )
            and
            azimuth_difference
            <=
            PERSISTENCE_MAX_AZIMUTH_DIFF_DEG
        )

    else:

        direction_valid = True

    return {

        "available":
            True,

        "valid":
            bool(
                amplitude_valid
                and
                direction_valid
            ),

        "vector_difference_mm":
            vector_difference,

        "azimuth_difference_deg":
            azimuth_difference
    }


# ============================================================
# RESULTADO VACÍO
# ============================================================

def empty_station_result(
    station
):

    return {

        "station_key":
            station[
                "key"
            ],

        "station_lat":
            station[
                "latitude"
            ],

        "station_lon":
            station[
                "longitude"
            ],

        "distance_km":
            station[
                "distance_km"
            ],

        "valid":
            False,

        "res_valid":
            False,

        "detection_state":
            "NO_DATA",

        "qc_level":
            "SIN DATOS",

        "pre_blocks":
            0,

        "early_blocks":
            0,

        "late_blocks":
            0,

        "pre_scatter_H_mm":
            np.nan,

        "late_scatter_H_mm":
            np.nan,

        "pre_drift_H_mm_min":
            np.nan,

        "late_drift_H_mm_min":
            np.nan,

        "pre_stable":
            False,

        "late_stable":
            False,

        "early_res_dH_mm":
            np.nan,

        "residual_dE_mm":
            np.nan,

        "residual_dN_mm":
            np.nan,

        "residual_dU_mm":
            np.nan,

        "residual_dH_mm":
            np.nan,

        "residual_d3D_mm":
            np.nan,

        "residual_azimuth_deg":
            np.nan,

        "residual_sigma_H_mm":
            np.nan,

        "residual_snr":
            np.nan,

        "persistence_valid":
            False,

        "persistence_vector_diff_mm":
            np.nan,

        "persistence_azimuth_diff_deg":
            np.nan,

        "plausibility_valid":
            False,

        "coherent":
            False,

        "coherent_neighbors":
            0,

        "status":
            ""
    }


# ============================================================
# ANALIZAR ESTACIÓN
# ============================================================

def analyze_station_res(
    event,
    station,
    current_time
):

    result = empty_station_result(
        station
    )

    origin = ensure_utc(
        event[
            "time"
        ]
    )

    if (
        current_time
        <
        origin
        +
        pd.Timedelta(
            seconds=
                LATE_POST_END_SECONDS
        )
    ):

        result[
            "status"
        ] = (
            "POST tardío aún incompleto"
        )

        return result

    wave = calculate_wave_times(
        station[
            "distance_km"
        ],
        event[
            "depth_km"
        ]
    )

    early_start = wave[
        "early_post_start_seconds"
    ]

    start_time = (
        origin
        +
        pd.Timedelta(
            seconds=
                PRE_START_SECONDS
        )
    )

    end_time = (
        origin
        +
        pd.Timedelta(
            seconds=
                LATE_POST_END_SECONDS
        )
    )

    stream, status = fetch_station_history(
        station[
            "network"
        ],
        station[
            "station"
        ],
        start_time,
        end_time
    )

    result[
        "status"
    ] = status

    if stream is None:

        return result

    data = align_components(
        stream_components(
            stream
        )
    )

    if data.empty:

        result[
            "status"
        ] = "sin E/N"

        return result

    data[
        "seconds_from_event"
    ] = (
        data[
            "time"
        ]
        -
        origin
    ).dt.total_seconds()

    # --------------------------------------------------------
    # PRE
    # --------------------------------------------------------

    pre_blocks = make_block_medians(
        data,
        PRE_START_SECONDS,
        PRE_END_SECONDS
    )

    result[
        "pre_blocks"
    ] = len(
        pre_blocks
    )

    if (
        len(
            pre_blocks
        )
        <
        MIN_PRE_BLOCKS
    ):

        result[
            "status"
        ] = "PRE insuficiente"

        return result

    # --------------------------------------------------------
    # POST TARDÍO
    # --------------------------------------------------------

    late_blocks = make_block_medians(
        data,
        LATE_POST_START_SECONDS,
        LATE_POST_END_SECONDS
    )

    result[
        "late_blocks"
    ] = len(
        late_blocks
    )

    if (
        len(
            late_blocks
        )
        <
        MIN_LATE_POST_BLOCKS
    ):

        result[
            "status"
        ] = (
            "POST tardío insuficiente"
        )

        return result

    # --------------------------------------------------------
    # POST TEMPRANO
    # --------------------------------------------------------

    early_blocks = pd.DataFrame()

    if (
        early_start
        <
        EARLY_POST_END_SECONDS
        -
        BLOCK_SECONDS
    ):

        early_blocks = make_block_medians(
            data,
            early_start,
            EARLY_POST_END_SECONDS
        )

    result[
        "early_blocks"
    ] = len(
        early_blocks
    )

    # --------------------------------------------------------
    # POSICIONES
    # --------------------------------------------------------

    pre_position = robust_block_position(
        pre_blocks
    )

    late_position = robust_block_position(
        late_blocks
    )

    early_position = (
        robust_block_position(
            early_blocks
        )
        if (
            len(
                early_blocks
            )
            >=
            MIN_EARLY_POST_BLOCKS
        )
        else
        None
    )

    pre_scatter = (
        pre_position[
            "scatter_H_mm"
        ]
    )

    late_scatter = (
        late_position[
            "scatter_H_mm"
        ]
    )

    result[
        "pre_scatter_H_mm"
    ] = pre_scatter

    result[
        "late_scatter_H_mm"
    ] = late_scatter

    # --------------------------------------------------------
    # DERIVA
    # --------------------------------------------------------

    pre_drift = horizontal_block_drift(
        pre_blocks
    )

    late_drift = horizontal_block_drift(
        late_blocks
    )

    result[
        "pre_drift_H_mm_min"
    ] = pre_drift

    result[
        "late_drift_H_mm_min"
    ] = late_drift

    pre_stable = (
        finite(
            pre_scatter
        )
        and
        pre_scatter
        <=
        MAX_PRE_BLOCK_SCATTER_H_MM
        and
        finite(
            pre_drift
        )
        and
        pre_drift
        <=
        MAX_PRE_DRIFT_H_MM_PER_MIN
    )

    late_stable = (
        finite(
            late_scatter
        )
        and
        late_scatter
        <=
        MAX_POST_BLOCK_SCATTER_H_MM
        and
        finite(
            late_drift
        )
        and
        late_drift
        <=
        MAX_POST_DRIFT_H_MM_PER_MIN
    )

    result[
        "pre_stable"
    ] = bool(
        pre_stable
    )

    result[
        "late_stable"
    ] = bool(
        late_stable
    )

    # --------------------------------------------------------
    # RES TARDÍO
    # --------------------------------------------------------

    late_res = calculate_residual(
        pre_position,
        late_position
    )

    if late_res is None:

        result[
            "status"
        ] = "RES no calculable"

        return result

    # --------------------------------------------------------
    # RES TEMPRANO
    # --------------------------------------------------------

    early_res = None

    if early_position is not None:

        early_res = calculate_residual(
            pre_position,
            early_position
        )

    persistence = evaluate_persistence(
        early_res,
        late_res
    )

    # --------------------------------------------------------
    # PLAUSIBILIDAD
    # --------------------------------------------------------

    limit = plausibility_limit_mm(
        event[
            "magnitude"
        ]
    )

    plausibility_valid = (
        True
        if not ENABLE_PLAUSIBILITY_FILTER
        else
        late_res[
            "dH_mm"
        ]
        <=
        limit
    )

    res_valid = (
        finite(
            late_res[
                "dH_mm"
            ]
        )
        and
        late_res[
            "dH_mm"
        ]
        >=
        MIN_RES_HORIZONTAL_MM
    )

    if (
        not res_valid
        or
        not finite(
            late_res[
                "snr"
            ]
        )
        or
        late_res[
            "snr"
        ]
        <
        MIN_RES_SNR_CANDIDATE
    ):

        detection_state = (
            "NO_DETECTION"
        )

    elif not plausibility_valid:

        detection_state = (
            "REJECTED_PLAUSIBILITY"
        )

    else:

        detection_state = (
            "CANDIDATE"
        )

    result.update(
        {
            "valid":
                True,

            "res_valid":
                bool(
                    res_valid
                ),

            "detection_state":
                detection_state,

            "early_res_dH_mm":
                (
                    early_res[
                        "dH_mm"
                    ]
                    if early_res
                    else
                    np.nan
                ),

            "residual_dE_mm":
                late_res[
                    "dE_mm"
                ],

            "residual_dN_mm":
                late_res[
                    "dN_mm"
                ],

            "residual_dU_mm":
                late_res[
                    "dU_mm"
                ],

            "residual_dH_mm":
                late_res[
                    "dH_mm"
                ],

            "residual_d3D_mm":
                late_res[
                    "d3D_mm"
                ],

            "residual_azimuth_deg":
                late_res[
                    "azimuth_deg"
                ],

            "residual_sigma_H_mm":
                late_res[
                    "sigma_H_mm"
                ],

            "residual_snr":
                late_res[
                    "snr"
                ],

            "persistence_valid":
                persistence[
                    "valid"
                ],

            "persistence_vector_diff_mm":
                persistence[
                    "vector_difference_mm"
                ],

            "persistence_azimuth_diff_deg":
                persistence[
                    "azimuth_difference_deg"
                ],

            "plausibility_valid":
                bool(
                    plausibility_valid
                ),

            "status":
                "OK"
        }
    )

    return result


# ============================================================
# COHERENCIA ESPACIAL
# ============================================================

def stations_are_spatially_coherent(
    station_a,
    station_b
):

    if (
        station_a[
            "detection_state"
        ]
        !=
        "CANDIDATE"
        or
        station_b[
            "detection_state"
        ]
        !=
        "CANDIDATE"
    ):

        return False

    distance = haversine_km(

        station_a[
            "station_lat"
        ],
        station_a[
            "station_lon"
        ],

        station_b[
            "station_lat"
        ],
        station_b[
            "station_lon"
        ]
    )

    if (
        distance
        >
        COHERENCE_MAX_DISTANCE_KM
    ):

        return False

    vector_difference = math.sqrt(

        (
            station_a[
                "residual_dE_mm"
            ]
            -
            station_b[
                "residual_dE_mm"
            ]
        ) ** 2

        +

        (
            station_a[
                "residual_dN_mm"
            ]
            -
            station_b[
                "residual_dN_mm"
            ]
        ) ** 2
    )

    reference_amplitude = max(

        min(
            station_a[
                "residual_dH_mm"
            ],
            station_b[
                "residual_dH_mm"
            ]
        ),

        1.0
    )

    allowed_difference = max(

        COHERENCE_MAX_VECTOR_DIFF_MM,

        COHERENCE_REL_VECTOR_DIFF
        *
        reference_amplitude
    )

    if (
        vector_difference
        >
        allowed_difference
    ):

        return False

    if (
        station_a[
            "residual_dH_mm"
        ]
        >=
        COHERENCE_DIRECTION_MIN_MM
        and
        station_b[
            "residual_dH_mm"
        ]
        >=
        COHERENCE_DIRECTION_MIN_MM
    ):

        azimuth_difference = angular_difference_deg(

            station_a[
                "residual_azimuth_deg"
            ],

            station_b[
                "residual_azimuth_deg"
            ]
        )

        if (
            not finite(
                azimuth_difference
            )
            or
            azimuth_difference
            >
            COHERENCE_MAX_AZIMUTH_DIFF_DEG
        ):

            return False

    return True


def evaluate_spatial_coherence(
    results
):

    work = results.copy()

    work[
        "coherent"
    ] = False

    work[
        "coherent_neighbors"
    ] = 0

    candidates = work[
        work[
            "detection_state"
        ]
        ==
        "CANDIDATE"
    ].index.tolist()

    for index_a in candidates:

        neighbors = 0

        for index_b in candidates:

            if (
                index_a
                ==
                index_b
            ):

                continue

            if stations_are_spatially_coherent(
                work.loc[
                    index_a
                ],
                work.loc[
                    index_b
                ]
            ):

                neighbors += 1

        work.at[
            index_a,
            "coherent_neighbors"
        ] = neighbors

        work.at[
            index_a,
            "coherent"
        ] = (
            neighbors
            >=
            MIN_COHERENT_NEIGHBORS_HIGH
        )

    return work


# ============================================================
# QC FINAL
# ============================================================

def classify_final_qc(
    row
):

    if not bool(
        row.get(
            "valid",
            False
        )
    ):

        return (
            "SIN DATOS",
            "NO_DATA"
        )

    if (
        row.get(
            "detection_state"
        )
        ==
        "REJECTED_PLAUSIBILITY"
    ):

        return (
            "RECHAZADO",
            "REJECTED_PLAUSIBILITY"
        )

    snr = numeric(
        row.get(
            "residual_snr"
        )
    )

    if (
        not finite(
            snr
        )
        or
        snr
        <
        MIN_RES_SNR_CANDIDATE
    ):

        return (
            "BAJO",
            "NO_DETECTION"
        )

    persistence = bool(
        row.get(
            "persistence_valid",
            False
        )
    )

    pre_stable = bool(
        row.get(
            "pre_stable",
            False
        )
    )

    late_stable = bool(
        row.get(
            "late_stable",
            False
        )
    )

    coherent = bool(
        row.get(
            "coherent",
            False
        )
    )

    # QC ALTO

    if (
        snr
        >=
        MIN_RES_SNR_HIGH
        and
        persistence
        and
        pre_stable
        and
        late_stable
        and
        coherent
    ):

        return (
            "ALTO",
            "VALID_RES"
        )

    # QC MODERADO

    if (
        snr
        >=
        MIN_RES_SNR_MODERATE
        and
        persistence
        and
        pre_stable
        and
        late_stable
    ):

        return (
            "MODERADO",
            "VALID_RES"
        )

    return (
        "BAJO",
        "CANDIDATE"
    )


def finalize_results(
    results
):

    work = evaluate_spatial_coherence(
        results
    )

    classifications = work.apply(
        classify_final_qc,
        axis=1
    )

    work[
        "qc_level"
    ] = [
        item[0]
        for item in
        classifications
    ]

    work[
        "detection_state"
    ] = [
        item[1]
        for item in
        classifications
    ]

    return work


# ============================================================
# ANALIZAR EVENTO
# ============================================================

def analyze_event(
    event,
    stations
):

    nearby = select_nearby_stations(
        event,
        stations
    )

    if nearby.empty:

        print(
            "No existen estaciones GNSS cercanas."
        )

        return pd.DataFrame()

    current_time = pd.Timestamp.now(
        tz="UTC"
    )

    rows = []

    print()

    print(
        "Analizando",
        len(
            nearby
        ),
        "estaciones GNSS..."
    )

    for number, (_, station) in enumerate(
        nearby.iterrows(),
        1
    ):

        print(
            f"[{number:02d}/{len(nearby):02d}] "
            f"{station['key']} · "
            f"{station['distance_km']:.1f} km"
        )

        try:

            result = analyze_station_res(
                event,
                station,
                current_time
            )

        except Exception as exc:

            result = empty_station_result(
                station
            )

            result[
                "status"
            ] = (
                f"ERROR {exc}"
            )

        rows.append(
            result
        )

    results = pd.DataFrame(
        rows
    )

    if results.empty:

        return results

    return finalize_results(
        results
    )


# ============================================================
# PUBLICABLES
# ============================================================

def station_is_publishable(
    row
):

    if not bool(
        row.get(
            "valid",
            False
        )
    ):

        return False

    if (
        row.get(
            "detection_state"
        )
        !=
        "VALID_RES"
    ):

        return False

    if (
        row.get(
            "qc_level"
        )
        not in
        PUBLISH_QC_LEVELS
    ):

        return False

    if not bool(
        row.get(
            "persistence_valid",
            False
        )
    ):

        return False

    if not bool(
        row.get(
            "plausibility_valid",
            False
        )
    ):

        return False

    fields = [
        "residual_dE_mm",
        "residual_dN_mm",
        "residual_dH_mm",
        "residual_sigma_H_mm",
        "residual_snr"
    ]

    for field in fields:

        if not finite(
            row.get(
                field
            )
        ):

            return False

    return True


def get_publishable_stations(
    results
):

    if (
        results is None
        or
        results.empty
    ):

        return pd.DataFrame()

    mask = results.apply(
        station_is_publishable,
        axis=1
    )

    work = results[
        mask
    ].copy()

    if work.empty:

        return work

    quality_rank = {
        "ALTO": 2,
        "MODERADO": 1
    }

    work[
        "_rank"
    ] = (
        work[
            "qc_level"
        ]
        .map(
            quality_rank
        )
        .fillna(
            0
        )
    )

    return (
        work
        .sort_values(
            [
                "_rank",
                "coherent_neighbors",
                "residual_snr",
                "residual_dH_mm"
            ],
            ascending=[
                False,
                False,
                False,
                False
            ]
        )
        .drop(
            columns=[
                "_rank"
            ]
        )
        .reset_index(
            drop=True
        )
    )


# ============================================================
# MAPA BASE
# ============================================================

def load_chile_outline():

    try:

        if not NE_ZIP_FILE.exists():

            response = http.get(
                NATURAL_EARTH_URL,
                timeout=60
            )

            response.raise_for_status()

            NE_ZIP_FILE.write_bytes(
                response.content
            )

        NE_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        shapefiles = list(
            NE_DIR.glob(
                "*.shp"
            )
        )

        if not shapefiles:

            with zipfile.ZipFile(
                NE_ZIP_FILE,
                "r"
            ) as archive:

                archive.extractall(
                    NE_DIR
                )

            shapefiles = list(
                NE_DIR.glob(
                    "*.shp"
                )
            )

        if not shapefiles:

            return None

        world = gpd.read_file(
            shapefiles[0]
        )

        for column in [
            "ADMIN",
            "NAME",
            "NAME_LONG",
            "SOVEREIGNT"
        ]:

            if column not in world.columns:

                continue

            chile = world[
                world[
                    column
                ]
                .astype(str)
                .str.lower()
                .eq(
                    "chile"
                )
            ].copy()

            if chile.empty:

                continue

            if chile.crs is None:

                chile = chile.set_crs(
                    4326
                )

            else:

                chile = chile.to_crs(
                    4326
                )

            return chile

    except Exception as exc:

        print(
            "Mapa base no disponible:",
            exc
        )

    return None


def result_color(
    row
):

    if (
        row.get(
            "qc_level"
        )
        ==
        "ALTO"
    ):

        return COLOR_GREEN

    return COLOR_ORANGE


# ============================================================
# CREAR LÁMINA
# ============================================================

def create_social_sheet(
    event,
    publishable,
    chile
):

    if publishable.empty:

        raise ValueError(
            "Sin estaciones VALID_RES."
        )

    primary = publishable.iloc[
        0
    ]

    figure = plt.figure(
        figsize=(
            16,
            9
        ),
        facecolor=
            COLOR_WHITE
    )

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    header = figure.add_axes(
        [
            0,
            0.85,
            1,
            0.15
        ]
    )

    header.axis(
        "off"
    )

    header.add_patch(
        patches.Rectangle(
            (
                0,
                0
            ),
            1,
            1,
            transform=
                header.transAxes,
            color=
                COLOR_NAVY
        )
    )

    header.text(
        0.035,
        0.64,
        BRAND_TITLE,
        fontsize=22,
        fontweight="bold",
        color=COLOR_WHITE,
        va="center"
    )

    header.text(
        0.035,
        0.25,
        BRAND_SUBTITLE,
        fontsize=11.5,
        color="#CBD5E1",
        va="center"
    )

    header.text(
        0.96,
        0.64,
        "PRELIMINAR",
        fontsize=12,
        fontweight="bold",
        color=COLOR_WHITE,
        ha="right",
        va="center",
        bbox=dict(
            boxstyle=
                "round,pad=0.5",
            facecolor=
                COLOR_RED,
            edgecolor=
                "none"
        )
    )

    # --------------------------------------------------------
    # MAPA
    # --------------------------------------------------------

    axis = figure.add_axes(
        [
            0.035,
            0.11,
            0.61,
            0.69
        ]
    )

    axis.set_facecolor(
        COLOR_SEA
    )

    if (
        chile is not None
        and
        not chile.empty
    ):

        chile.plot(
            ax=axis,
            facecolor=
                COLOR_LAND,
            edgecolor=
                COLOR_GRAY,
            linewidth=0.7,
            zorder=1
        )

    # Epicentro

    axis.scatter(
        event[
            "longitude"
        ],
        event[
            "latitude"
        ],
        marker="*",
        s=650,
        color=COLOR_RED,
        edgecolor=COLOR_NAVY,
        linewidth=1.5,
        zorder=30
    )

    longitudes = [
        event[
            "longitude"
        ]
    ]

    latitudes = [
        event[
            "latitude"
        ]
    ]

    maximum_res = (
        publishable[
            "residual_dH_mm"
        ]
        .max()
    )

    vector_scale = (
        VECTOR_TARGET_DEGREES
        /
        maximum_res
        if (
            finite(
                maximum_res
            )
            and
            maximum_res > 0
        )
        else
        0.02
    )

    # --------------------------------------------------------
    # VECTORES RES
    # --------------------------------------------------------

    for _, row in (
        publishable.iterrows()
    ):

        longitude = float(
            row[
                "station_lon"
            ]
        )

        latitude = float(
            row[
                "station_lat"
            ]
        )

        longitudes.append(
            longitude
        )

        latitudes.append(
            latitude
        )

        color = result_color(
            row
        )

        axis.scatter(
            longitude,
            latitude,
            s=90,
            color=color,
            edgecolor=COLOR_NAVY,
            linewidth=0.9,
            zorder=15
        )

        delta_x = (
            row[
                "residual_dE_mm"
            ]
            *
            vector_scale
        )

        delta_y = (
            row[
                "residual_dN_mm"
            ]
            *
            vector_scale
        )

        axis.arrow(
            longitude,
            latitude,
            delta_x,
            delta_y,
            width=0.0035,
            head_width=0.04,
            head_length=0.05,
            length_includes_head=True,
            color=color,
            zorder=14
        )

        axis.annotate(
            (
                f"{row['station_key']}\n"
                f"RES {row['residual_dH_mm']:.1f}"
                f" ± {row['residual_sigma_H_mm']:.1f} mm\n"
                f"SNR {row['residual_snr']:.1f} · "
                f"QC {row['qc_level']}"
            ),
            (
                longitude,
                latitude
            ),
            xytext=(
                5,
                5
            ),
            textcoords=
                "offset points",
            fontsize=7,
            bbox=dict(
                boxstyle=
                    "round,pad=0.23",
                facecolor=
                    COLOR_WHITE,
                edgecolor=
                    color,
                alpha=
                    0.95
            ),
            zorder=20
        )

    # --------------------------------------------------------
    # ZOOM
    # --------------------------------------------------------

    longitude_margin = max(
        0.65,
        (
            max(
                longitudes
            )
            -
            min(
                longitudes
            )
        )
        *
        0.30
    )

    latitude_margin = max(
        0.65,
        (
            max(
                latitudes
            )
            -
            min(
                latitudes
            )
        )
        *
        0.30
    )

    axis.set_xlim(
        max(
            MAP_LON_MIN,
            min(
                longitudes
            )
            -
            longitude_margin
        ),
        min(
            MAP_LON_MAX,
            max(
                longitudes
            )
            +
            longitude_margin
        )
    )

    axis.set_ylim(
        max(
            MAP_LAT_MIN,
            min(
                latitudes
            )
            -
            latitude_margin
        ),
        min(
            MAP_LAT_MAX,
            max(
                latitudes
            )
            +
            latitude_margin
        )
    )

    axis.grid(
        alpha=0.25
    )

    axis.set_xlabel(
        "Longitud"
    )

    axis.set_ylabel(
        "Latitud"
    )

    axis.set_title(
        MAP_TITLE,
        loc="left",
        fontsize=13,
        fontweight="bold"
    )

    # --------------------------------------------------------
    # PANEL DERECHO
    # --------------------------------------------------------

    info = figure.add_axes(
        [
            0.675,
            0.14,
            0.29,
            0.63
        ]
    )

    info.axis(
        "off"
    )

    # Datos sísmicos

    info.add_patch(
        patches.FancyBboxPatch(
            (
                0,
                0.54
            ),
            1,
            0.43,
            boxstyle=
                "round,pad=0.014",
            facecolor=
                COLOR_LIGHT_GRAY,
            edgecolor=
                COLOR_BORDER
        )
    )

    magnitude_text = (
        f"M {event['magnitude']:.1f}"
        if finite(
            event[
                "magnitude"
            ]
        )
        else
        "M ?"
    )

    info.text(
        0.05,
        0.88,
        magnitude_text,
        fontsize=34,
        fontweight="bold",
        color=COLOR_RED
    )

    info.text(
        0.05,
        0.80,
        ensure_utc(
            event[
                "time"
            ]
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        fontsize=10.5,
        fontweight="bold"
    )

    info.text(
        0.05,
        0.72,
        (
            f"Profundidad: "
            f"{fmt(event['depth_km'], 1, ' km')}"
        ),
        fontsize=10.5
    )

    info.text(
        0.05,
        0.65,
        (
            f"Latitud: "
            f"{event['latitude']:.3f}°"
        ),
        fontsize=9.5,
        color=COLOR_MUTED
    )

    info.text(
        0.05,
        0.59,
        (
            f"Longitud: "
            f"{event['longitude']:.3f}°"
        ),
        fontsize=9.5,
        color=COLOR_MUTED
    )

    # RES

    info.add_patch(
        patches.FancyBboxPatch(
            (
                0,
                0.17
            ),
            1,
            0.28,
            boxstyle=
                "round,pad=0.014",
            facecolor=
                COLOR_WHITE,
            edgecolor=
                COLOR_BORDER
        )
    )

    info.text(
        0.05,
        0.395,
        "MÁXIMO RES VALIDADO",
        fontsize=9.5,
        fontweight="bold",
        color=COLOR_MUTED
    )

    info.text(
        0.05,
        0.30,
        (
            f"{primary['residual_dH_mm']:.1f}"
        ),
        fontsize=32,
        fontweight="bold",
        color=result_color(
            primary
        )
    )

    info.text(
        0.32,
        0.30,
        "mm",
        fontsize=12,
        fontweight="bold"
    )

    info.text(
        0.05,
        0.245,
        (
            f"{primary['station_key']} · "
            f"Az "
            f"{primary['residual_azimuth_deg']:.0f}°"
        ),
        fontsize=10
    )

    info.text(
        0.05,
        0.195,
        (
            f"±{primary['residual_sigma_H_mm']:.1f} mm · "
            f"SNR {primary['residual_snr']:.1f} · "
            f"QC {primary['qc_level']}"
        ),
        fontsize=9.5,
        fontweight="bold",
        color=result_color(
            primary
        )
    )

    # Lugar

    info.text(
        0.02,
        0.08,
        textwrap.fill(
            repair_text(
                event[
                    "region"
                ]
            ),
            42
        ),
        fontsize=10.5,
        fontweight="bold",
        va="top",
        color=COLOR_NAVY
    )

    # --------------------------------------------------------
    # FOOTER
    # --------------------------------------------------------

    footer = figure.add_axes(
        [
            0.035,
            0.015,
            0.93,
            0.06
        ]
    )

    footer.axis(
        "off"
    )

    footer.text(
        0,
        0.65,
        (
            "RES = cambio robusto de posición GNSS "
            "entre ventanas PRE y POST."
        ),
        fontsize=8,
        color=COLOR_MUTED
    )

    footer.text(
        0,
        0.20,
        "Resultado automático preliminar.",
        fontsize=7.7,
        color=COLOR_MUTED
    )

    footer.text(
        1,
        0.20,
        (
            "Datos: Centro Sismológico Nacional · Chile"
        ),
        fontsize=7.4,
        color=COLOR_MUTED,
        ha="right"
    )

    output = (
        OUTPUT_DIR
        /
        (
            f"res_"
            f"{event['event_id']}"
            ".png"
        )
    )

    plt.savefig(
        output,
        dpi=180,
        bbox_inches="tight",
        facecolor=COLOR_WHITE
    )

    plt.close(
        figure
    )

    return output


# ============================================================
# TEXTO DEL TWEET
# ============================================================

def build_x_text(
    event
):

    event_time = ensure_utc(
        event[
            "time"
        ]
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    magnitude = (
        f"{event['magnitude']:.1f}"
        if finite(
            event[
                "magnitude"
            ]
        )
        else
        "-"
    )

    magnitude_type = (
        str(
            event.get(
                "mag_type",
                ""
            )
        ).strip()
    )

    depth = (
        f"{event['depth_km']:.1f}"
        if finite(
            event[
                "depth_km"
            ]
        )
        else
        "-"
    )

    region = repair_text(
        event.get(
            "region",
            "Chile"
        )
    )

    text = (
        "🇨🇱 Desplazamiento geodésico residual observado\n\n"
        f"UTC: {event_time}\n"
        f"Magnitud: {magnitude}"
        f"{(' ' + magnitude_type) if magnitude_type else ''}\n"
        f"Profundidad: {depth} km\n"
        f"{region}"
    )

    return text[:280]


# ============================================================
# CONFIGURACIÓN X
# ============================================================

def configure_x():

    credentials = {

        "X_API_KEY":
            X_API_KEY,

        "X_API_SECRET":
            X_API_SECRET,

        "X_ACCESS_TOKEN":
            X_ACCESS_TOKEN,

        "X_ACCESS_TOKEN_SECRET":
            X_ACCESS_TOKEN_SECRET
    }

    missing = []

    for name, value in credentials.items():

        if (
            not value
            or
            str(
                value
            ).startswith(
                "TU_"
            )
        ):

            missing.append(
                name
            )

    if missing:

        raise RuntimeError(
            "Faltan credenciales X: "
            +
            ", ".join(
                missing
            )
        )

    auth = tweepy.OAuth1UserHandler(
        X_API_KEY,
        X_API_SECRET,
        X_ACCESS_TOKEN,
        X_ACCESS_TOKEN_SECRET
    )

    media_api = tweepy.API(
        auth,
        wait_on_rate_limit=True
    )

    client = tweepy.Client(
        consumer_key=
            X_API_KEY,
        consumer_secret=
            X_API_SECRET,
        access_token=
            X_ACCESS_TOKEN,
        access_token_secret=
            X_ACCESS_TOKEN_SECRET,
        wait_on_rate_limit=True
    )

    return (
        media_api,
        client
    )


# ============================================================
# PUBLICAR X
# ============================================================

def publish_to_x(
    image_path,
    text,
    media_api,
    client
):

    print()

    print(
        "=" * 80
    )

    print(
        "POST PREPARADO"
    )

    print(
        "=" * 80
    )

    print(
        text
    )

    print()

    print(
        "Imagen:",
        image_path
    )

    if DRY_RUN:

        print()

        print(
            "DRY_RUN=True → NO PUBLICADO EN X"
        )

        return None

    if (
        REQUIRE_SCALE_VERIFIED_FOR_REAL_POST
        and
        not SCALE_VERIFIED
    ):

        raise RuntimeError(
            "Publicación bloqueada: "
            "SCALE_VERIFIED=False."
        )

    media = media_api.media_upload(
        filename=
            str(
                image_path
            )
    )

    try:

        media_api.create_media_metadata(
            media.media_id,
            (
                "Mapa de desplazamiento geodésico "
                "residual GNSS observado alrededor "
                "de un evento sísmico en Chile."
            )
        )

    except Exception as exc:

        print(
            "Alt text no agregado:",
            exc
        )

    response = client.create_tweet(
        text=text,
        media_ids=[
            media.media_id
        ]
    )

    post_id = None

    try:

        post_id = (
            response.data[
                "id"
            ]
        )

    except Exception:

        pass

    print()

    print(
        "PUBLICADO EN X"
    )

    print(
        "Post ID:",
        post_id
    )

    return post_id


# ============================================================
# TABLA DE RESULTADOS
# ============================================================

def print_results_table(
    results
):

    columns = [

        "station_key",

        "distance_km",

        "pre_blocks",

        "early_blocks",

        "late_blocks",

        "pre_scatter_H_mm",

        "late_scatter_H_mm",

        "pre_drift_H_mm_min",

        "late_drift_H_mm_min",

        "early_res_dH_mm",

        "residual_dE_mm",

        "residual_dN_mm",

        "residual_dU_mm",

        "residual_dH_mm",

        "residual_sigma_H_mm",

        "residual_snr",

        "persistence_vector_diff_mm",

        "persistence_azimuth_diff_deg",

        "persistence_valid",

        "pre_stable",

        "late_stable",

        "plausibility_valid",

        "coherent_neighbors",

        "coherent",

        "detection_state",

        "qc_level",

        "status"
    ]

    columns = [
        column
        for column in columns
        if column in
        results.columns
    ]

    with pd.option_context(
        "display.max_columns",
        None,
        "display.width",
        250,
        "display.max_rows",
        100
    ):

        print()

        print(
            results[
                columns
            ].to_string(
                index=False
            )
        )


# ============================================================
# PROCESAR EVENTO
# ============================================================

def process_event(
    event,
    stations,
    chile,
    state,
    media_api,
    client
):

    event_id = str(
        event[
            "event_id"
        ]
    )

    print()

    print(
        "=" * 80
    )

    print(
        "ANÁLISIS RES",
        event_id
    )

    print(
        "=" * 80
    )

    print(
        "UTC:",
        event[
            "time"
        ]
    )

    print(
        "Magnitud:",
        event[
            "magnitude"
        ],
        event[
            "mag_type"
        ]
    )

    print(
        "Profundidad:",
        event[
            "depth_km"
        ],
        "km"
    )

    print(
        "Lugar:",
        event[
            "region"
        ]
    )

    results = analyze_event(
        event,
        stations
    )

    if results.empty:

        print(
            "Sin resultados GNSS."
        )

        return False

    print_results_table(
        results
    )

    publishable = get_publishable_stations(
        results
    )

    valid_count = int(
        results[
            "valid"
        ]
        .fillna(
            False
        )
        .sum()
    )

    valid_res_count = int(
        (
            results[
                "detection_state"
            ]
            ==
            "VALID_RES"
        ).sum()
    )

    print()

    print(
        "RESUMEN"
    )

    print(
        "Históricos válidos:",
        valid_count
    )

    print(
        "VALID_RES:",
        valid_res_count
    )

    print(
        "Estaciones publicables:",
        len(
            publishable
        )
    )

    if publishable.empty:

        print()

        print(
            "NO SE GENERA MAPA / TWEET."
        )

        print(
            "No existe un RES suficientemente robusto."
        )

        return False

    image_path = create_social_sheet(
        event,
        publishable,
        chile
    )

    if not image_path.exists():

        print(
            "Mapa no generado."
        )

        return False

    print()

    print(
        "MAPA GENERADO:",
        image_path
    )

    tweet_text = build_x_text(
        event
    )

    post_id = publish_to_x(
        image_path,
        tweet_text,
        media_api,
        client
    )

    now = pd.Timestamp.now(
        tz="UTC"
    )

    if DRY_RUN:

        state[
            "dry_run_seen"
        ][event_id] = {

            "processed_at":
                str(
                    now
                ),

            "map":
                str(
                    image_path
                )
        }

    else:

        state[
            "posted"
        ][event_id] = {

            "posted_at":
                str(
                    now
                ),

            "post_id":
                post_id,

            "map":
                str(
                    image_path
                )
        }

    state[
        "detected"
    ].pop(
        event_id,
        None
    )

    save_state(
        state
    )

    persist_state_to_git()

    return True


# ============================================================
# LIMPIAR PENDIENTES VIEJOS
# ============================================================

def prune_old_pending(
    state,
    now
):

    expired_ids = []

    for event_id, record in list(
        state[
            "detected"
        ].items()
    ):

        try:

            detected_at = ensure_utc(
                record[
                    "detected_at"
                ]
            )

        except Exception:

            expired_ids.append(
                event_id
            )

            continue

        pending_age = (
            now
            -
            detected_at
        ).total_seconds()

        if (
            pending_age
            >
            MAX_PENDING_RETRY_MINUTES
            *
            60
        ):

            expired_ids.append(
                event_id
            )

    for event_id in expired_ids:

        record = state[
            "detected"
        ].pop(
            event_id,
            None
        )

        state[
            "expired"
        ][event_id] = {

            "expired_at":
                str(
                    now
                ),

            "event":
                (
                    record.get(
                        "event"
                    )
                    if record
                    else
                    None
                )
        }

    if expired_ids:

        print(
            "Pendientes expirados:",
            len(
                expired_ids
            )
        )

        save_state(
            state
        )


# ============================================================
# DETECTAR NUEVOS EVENTOS
# ============================================================

def detect_new_events(
    earthquakes,
    state,
    now
):

    if earthquakes.empty:

        print(
            "Eventos CSN recuperados: 0"
        )

        return 0

    print(
        "Eventos CSN recuperados:",
        len(
            earthquakes
        )
    )

    latest = earthquakes.iloc[
        0
    ]

    latest_age = (
        now
        -
        ensure_utc(
            latest[
                "time"
            ]
        )
    ).total_seconds()

    print()

    print(
        (
            f"Último evento: "
            f"M{fmt(latest['magnitude'], 1)} · "
            f"{ensure_utc(latest['time']).strftime('%H:%M:%S UTC')} · "
            f"edad {format_age(latest_age)}"
        )
    )

    print(
        "Lugar:",
        latest[
            "region"
        ]
    )

    recent = earthquakes.copy()

    recent[
        "age_seconds"
    ] = (
        now
        -
        recent[
            "time"
        ]
    ).dt.total_seconds()

    recent = recent[
        (
            recent[
                "age_seconds"
            ]
            >=
            0
        )
        &
        (
            recent[
                "age_seconds"
            ]
            <=
            RECENT_EVENT_WINDOW_MINUTES
            *
            60
        )
    ].copy()

    print()

    print(
        (
            f"Eventos dentro de "
            f"{RECENT_EVENT_WINDOW_MINUTES} min:"
        ),
        len(
            recent
        )
    )

    for _, row in (
        recent.iterrows()
    ):

        print(
            (
                f"M{fmt(row['magnitude'], 1)} · "
                f"{ensure_utc(row['time']).strftime('%H:%M:%S UTC')} · "
                f"{format_age(row['age_seconds'])} · "
                f"{row['region']}"
            )
        )

    if (
        TEST_LATEST_EVENT
        and
        recent.empty
    ):

        recent = pd.DataFrame(
            [
                latest.to_dict()
            ]
        )

        recent[
            "age_seconds"
        ] = latest_age

    new_count = 0

    for _, row in (
        recent.iterrows()
    ):

        event = row.to_dict()

        event_id = str(
            event[
                "event_id"
            ]
        )

        if (
            event_id
            in
            state[
                "posted"
            ]
        ):

            continue

        if (
            event_id
            in
            state[
                "detected"
            ]
        ):

            continue

        if (
            event_id
            in
            state[
                "expired"
            ]
        ):

            continue

        if (
            DRY_RUN
            and
            event_id
            in
            state[
                "dry_run_seen"
            ]
        ):

            continue

        state[
            "detected"
        ][event_id] = {

            "detected_at":
                str(
                    now
                ),

            "event":
                serialize_event(
                    event
                )
        }

        new_count += 1

        print()

        print(
            ">>> NUEVO EVENTO"
        )

        print(
            "ID:",
            event_id
        )

        print(
            "M:",
            event[
                "magnitude"
            ]
        )

        print(
            "UTC:",
            event[
                "time"
            ]
        )

        print(
            "Lugar:",
            event[
                "region"
            ]
        )

    if new_count:

        save_state(
            state
        )

    print()

    print(
        "Eventos nuevos:",
        new_count
    )

    return new_count


# ============================================================
# PROCESAR EVENTOS PENDIENTES
# ============================================================

def process_pending_events(
    stations,
    chile,
    state,
    media_api,
    client,
    now
):

    pending_ids = list(
        state[
            "detected"
        ].keys()
    )

    print()

    print(
        "Eventos pendientes:",
        len(
            pending_ids
        )
    )

    for event_id in pending_ids:

        record = state[
            "detected"
        ].get(
            event_id
        )

        if not record:

            continue

        event = deserialize_event(
            record[
                "event"
            ]
        )

        event_age = (
            now
            -
            event[
                "time"
            ]
        ).total_seconds()

        detected_at = ensure_utc(
            record[
                "detected_at"
            ]
        )

        retry_age = (
            now
            -
            detected_at
        ).total_seconds()

        print()

        print(
            "-" * 80
        )

        print(
            "Pendiente:",
            event_id
        )

        print(
            "Edad del sismo:",
            format_age(
                event_age
            )
        )

        print(
            "Tiempo pendiente:",
            format_age(
                retry_age
            )
        )

        # Esperar hasta disponer del POST tardío

        if (
            event_age
            <
            MIN_ANALYSIS_AGE_SECONDS
        ):

            print(
                "Esperando POST hasta +600 s."
            )

            continue

        # Expiración

        if (
            retry_age
            >
            MAX_PENDING_RETRY_MINUTES
            *
            60
        ):

            record = state[
                "detected"
            ].pop(
                event_id,
                None
            )

            state[
                "expired"
            ][event_id] = {

                "expired_at":
                    str(
                        now
                    ),

                "event":
                    (
                        record.get(
                            "event"
                        )
                        if record
                        else
                        None
                    )
            }

            save_state(
                state
            )

            print(
                "Evento expirado."
            )

            continue

        try:

            success = process_event(
                event,
                stations,
                chile,
                state,
                media_api,
                client
            )

            if not success:

                print(
                    "Sin VALID_RES por ahora."
                )

        except Exception as exc:

            print()

            print(
                "ERROR EVENTO:",
                event_id
            )

            print(
                exc
            )

            traceback.print_exc()


# ============================================================
# CICLO ÚNICO
# ============================================================

def run_single_cycle(
    stations,
    chile,
    state,
    media_api,
    client
):

    now = pd.Timestamp.now(
        tz="UTC"
    )

    print()

    print(
        "=" * 80
    )

    print(
        now.strftime(
            "[%Y-%m-%d %H:%M:%S UTC]"
        ),
        "consultando sismologia.cl..."
    )

    print(
        "=" * 80
    )

    prune_old_pending(
        state,
        now
    )

    earthquakes = (
        get_current_earthquakes()
    )

    detect_new_events(
        earthquakes,
        state,
        now
    )

    process_pending_events(
        stations,
        chile,
        state,
        media_api,
        client,
        now
    )

    # Siempre guardar estado aunque no exista publicación.

    save_state(
        state
    )

    # Persistir detected/posted/expired para la siguiente
    # ejecución de GitHub Actions.

    persist_state_to_git()

    print()

    print(
        "=" * 80
    )

    print(
        "CICLO FINALIZADO"
    )

    print(
        "=" * 80
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()

    print(
        "=" * 80
    )

    print(
        "Desplazamiento geodésico residual GNSS observado"
    )

    print(
        "=" * 80
    )

    print(
        "Ejecución: UN SOLO CICLO"
    )

    print(
        "Ventana detección:",
        RECENT_EVENT_WINDOW_MINUTES,
        "min"
    )

    print(
        "Análisis desde:",
        MIN_ANALYSIS_AGE_SECONDS,
        "s"
    )

    print(
        "DRY_RUN:",
        DRY_RUN
    )

    print(
        "ENABLE_X:",
        ENABLE_X
    )

    print(
        "SCALE_VERIFIED:",
        SCALE_VERIFIED
    )

    print(
        "Estado:",
        STATE_FILE
    )

    # --------------------------------------------------------
    # INVENTARIO
    # --------------------------------------------------------

    print()

    print(
        "Obteniendo inventario GNSS CSN..."
    )

    stations = (
        get_gnss_station_inventory()
    )

    if stations.empty:

        raise RuntimeError(
            "No se pudo obtener inventario GNSS."
        )

    print(
        "Estaciones GNSS:",
        len(
            stations
        )
    )

    # --------------------------------------------------------
    # MAPA
    # --------------------------------------------------------

    print()

    print(
        "Preparando mapa base..."
    )

    chile = (
        load_chile_outline()
    )

    if chile is None:

        print(
            "Mapa Natural Earth no disponible."
        )

    # --------------------------------------------------------
    # ESTADO
    # --------------------------------------------------------

    state = load_state()

    print()

    print(
        "Pendientes:",
        len(
            state[
                "detected"
            ]
        )
    )

    print(
        "Publicados:",
        len(
            state[
                "posted"
            ]
        )
    )

    print(
        "Expirados:",
        len(
            state[
                "expired"
            ]
        )
    )

    # --------------------------------------------------------
    # X
    # --------------------------------------------------------

    media_api = None

    client = None

    if (
        ENABLE_X
        and
        not DRY_RUN
    ):

        print()

        print(
            "Configurando X..."
        )

        media_api, client = (
            configure_x()
        )

        print(
            "X configurado."
        )

    # --------------------------------------------------------
    # EJECUCIÓN
    # --------------------------------------------------------

    run_single_cycle(
        stations,
        chile,
        state,
        media_api,
        client
    )

    print()

    print(
        "EJECUCIÓN TERMINADA."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
