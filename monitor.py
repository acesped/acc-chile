#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CSN -> HEATMAP + ESTACIONES + VIDEO -> X
Ejecución autónoma en GitHub Actions.

- Un ciclo por ejecución.
- Sismos M >= 4 en las últimas 12 horas.
- Consulta nacional de acelerómetros ?N?.
- Procesa componentes verticales.
- Sin límite fijo de estaciones.
- Conserva tramos válidos de registros parciales.
- Los huecos permanecen como NaN, nunca como ceros.
- Heatmap dinámico con cobertura espacial suficiente.
- Gráfico PNG con todas las estaciones.
- Video con páginas de estaciones y mapa completo.
- Estado persistente en GitHub.
- Recuperación ante errores transitorios de GitHub.
- Protección frente a publicaciones duplicadas.

Interpretación:
Los valores son máximos absolutos por segundo de aceleración
vertical filtrada, expresados en cm/s².

La interpolación espacial es una estimación.
No representa una simulación de propagación de ondas.

La programación cada 10 minutos pertenece al workflow YAML.
"""

import base64
import csv
import io
import json
import logging
import math
import os
import re
import subprocess
import sys
import time
import unicodedata

from collections import OrderedDict
from concurrent.futures import (
    ThreadPoolExecutor,
    wait,
    FIRST_COMPLETED,
)
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import requests
import imageio_ffmpeg

from bs4 import BeautifulSoup
from matplotlib.animation import FFMpegWriter
from matplotlib.colors import PowerNorm
from obspy import UTCDateTime, read, read_inventory
from requests_oauthlib import OAuth1
from scipy.spatial import cKDTree, Delaunay, QhullError


# =====================================================================
# CONFIGURACIÓN
# =====================================================================

CSN = "https://www.sismologia.cl"

FDSN = os.getenv(
    "CSN_FDSN",
    "https://owl.csn.uchile.cl",
).rstrip("/")

MAG_MIN = 4.0
HORAS_BUSQUEDA = 20

PRE_SEG = 60
POST_SEG = 120
MARGEN_SEG = 30
LATENCIA_SEG = 120

# Extensión geográfica de consulta.
# Incluye Chile continental y áreas limítrofes dentro del rectángulo.
MIN_LAT = -56.0
MAX_LAT = -17.0
MIN_LON = -78.0
MAX_LON = -65.0

# Segundo carácter N: acelerómetro.
# Después se seleccionan componentes verticales por código/orientación.
CANALES = "?N?"

TRABAJADORES = 6

# Límites operativos, no límites de cantidad de estaciones.
PRESUPUESTO_DESCARGA_SEG = 20 * 60
PRESUPUESTO_LOTE_SEG = 35 * 60

# Procesamiento de registros parciales.
MIN_SEGMENTO_SEG = 12.0
BORDE_DESCARTADO_SEG = 2.0
COBERTURA_MIN_POR_SEGUNDO = 0.80

# Un cuadro por segundo observado.
FPS = 4

# Video: dos paneles de 20 estaciones.
FILAS_POR_PANEL = 20
ESTACIONES_POR_PAGINA = 2 * FILAS_POR_PANEL

# Interpolación.
HEATMAP_RESOLUCION_X = 140
HEATMAP_RESOLUCION_Y = 300
HEATMAP_VECINOS = 8
HEATMAP_MIN_VECINOS = 3
HEATMAP_RADIO_KM = 150.0
HEATMAP_POTENCIA = 2.0

# Escala fija de colores durante el video.
# Gamma < 1 mejora visibilidad de amplitudes pequeñas.
HEATMAP_GAMMA = 0.40

PUBLICAR_EN_X = os.getenv(
    "PUBLISH_TO_X", "true"
).strip().lower() in {"true", "1", "yes"}

SALIDA = Path(os.getenv("CSN_OUTPUT", "output"))

STATE_BRANCH = os.getenv("STATE_BRANCH", "csn-state")
STATE_PATH = (
    "estado_publicaciones.json"
    if PUBLICAR_EN_X
    else "estado_simulaciones.json"
)

API_X = "https://api.x.com/2"
MAX_PROCESAMIENTO_X_SEG = 15 * 60

TIMEOUT_HTTP = (10, 45)
GH_INTENTOS = 4

LOG = logging.getLogger("csn-monitor")

SEGUNDOS = np.arange(-PRE_SEG, POST_SEG, dtype=float)


# =====================================================================
# CREDENCIALES DIRECTAS DE X
# =====================================================================

X_API_KEY = "t5792SuVlfx41hDSWYmHVQJiG"
X_API_SECRET = "WCOUY5z1SqlylH1XYQM9P5guowMC3RogGWIF2hLvSFJKna3HVw"
X_ACCESS_TOKEN = "2106457141796052993-NpB8nf6yLTbPjJEu4TIHwJfbCCHU7h"
X_ACCESS_TOKEN_SECRET = "jCDFy4L4suq6Z6qnHOhJ4CuduqWs5173JgriRqn76L5MZ"


# =====================================================================
# EXCEPCIONES Y UTILIDADES
# =====================================================================

class ErrorEstado(RuntimeError):
    pass


class ErrorX(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"X respondió HTTP {status}")


class SinDatos(RuntimeError):
    pass


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(
        c for c in texto if not unicodedata.combining(c)
    ).strip().lower()


def numero(texto):
    encontrado = re.search(
        r"[-+]?\d+(?:[.,]\d+)?",
        str(texto).replace("−", "-"),
    )
    if not encontrado:
        raise ValueError("Número no reconocido")
    return float(encontrado.group().replace(",", "."))


def obtener(url, params=None, permitir_vacio=False):
    r = requests.get(
        url,
        params=params,
        headers={"User-Agent": "CSN-Earthquake-Monitor/3.0"},
        timeout=TIMEOUT_HTTP,
    )
    if permitir_vacio and r.status_code in (204, 404):
        return b""
    r.raise_for_status()
    return r.content


def fecha_fdsn(t):
    return UTCDateTime(t).strftime("%Y-%m-%dT%H:%M:%S.%f")


def identificar_evento(url):
    partes = urlparse(url).path.strip("/").split("/")
    return "_".join(partes[-3:]).replace(".html", "")


def hora_local(evento):
    return UTCDateTime(evento["t"]).datetime.replace(
        tzinfo=timezone.utc
    ).astimezone(ZoneInfo("America/Santiago"))


def distancia_km(lat1, lon1, lat2, lon2):
    a1, a2 = np.radians([lat1, lat2])
    da = a2 - a1
    dl = np.radians(lon2 - lon1)
    a = (
        np.sin(da / 2) ** 2
        + np.cos(a1) * np.cos(a2) * np.sin(dl / 2) ** 2
    )
    return float(6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1))))


# =====================================================================
# CSN
# =====================================================================

def leer_evento(url):
    soup = BeautifulSoup(obtener(url), "html.parser")
    campos = {}

    for fila in soup.select("tr"):
        celdas = fila.find_all(["td", "th"])
        if len(celdas) >= 2:
            clave = normalizar(
                celdas[0].get_text(" ", strip=True)
            ).rstrip(":")
            campos[clave] = " ".join(
                c.get_text(" ", strip=True) for c in celdas[1:]
            )

    def campo(nombre, obligatorio=True):
        for clave, valor in campos.items():
            if clave == nombre or clave.startswith(nombre):
                return valor
        if obligatorio:
            raise ValueError(f"Informe sin campo: {nombre}")
        return None

    fecha = None
    texto_hora = campo("hora utc")

    for patron, formato in (
        (
            r"\d{2}:\d{2}:\d{2}\s+\d{2}/\d{2}/\d{4}",
            "%H:%M:%S %d/%m/%Y",
        ),
        (
            r"\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}",
            "%d/%m/%Y %H:%M:%S",
        ),
        (
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
            "%Y-%m-%d %H:%M:%S",
        ),
    ):
        encontrado = re.search(patron, texto_hora)
        if encontrado:
            fecha = datetime.strptime(
                " ".join(encontrado.group().split()), formato
            ).replace(tzinfo=timezone.utc)
            break

    if fecha is None:
        raise ValueError("Hora UTC no reconocida")

    lat = numero(campo("latitud"))
    lon = numero(campo("longitud"))
    mag_texto = campo("magnitud")
    mag = numero(mag_texto)

    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordenadas inválidas")
    if not 0 <= mag <= 10:
        raise ValueError("Magnitud inválida")

    try:
        prof = numero(campo("profundidad", False))
    except (TypeError, ValueError):
        prof = None

    return {
        "t": str(UTCDateTime(fecha)),
        "lat": lat,
        "lon": lon,
        "mag": mag,
        "mag_texto": mag_texto.strip(),
        "prof": prof,
        "referencia": campo("referencia"),
        "url": url,
    }


def enlaces_m4(contenido, pagina):
    soup = BeautifulSoup(contenido, "html.parser")
    enlaces = set()
    filas = 0

    for fila in soup.select("tr"):
        enlace = fila.find("a", href=re.compile(r"/informes/"))
        celdas = fila.find_all("td")

        if enlace is None or not celdas:
            continue

        filas += 1

        try:
            mag = numero(celdas[-1].get_text(" ", strip=True))
        except ValueError:
            mag = MAG_MIN

        if mag >= MAG_MIN:
            url = urljoin(pagina, enlace["href"])
            if urlparse(url).hostname in {
                "sismologia.cl", "www.sismologia.cl"
            }:
                enlaces.add(url)

    return enlaces, filas


def buscar_eventos_una_vez(inicio, fin):
    fechas = set()

    for zona in (timezone.utc, ZoneInfo("America/Santiago")):
        dia = inicio.datetime.replace(
            tzinfo=timezone.utc
        ).astimezone(zona).date()
        ultimo = fin.datetime.replace(
            tzinfo=timezone.utc
        ).astimezone(zona).date()

        while dia <= ultimo:
            fechas.add(dia)
            dia += timedelta(days=1)

    paginas = [CSN + "/"] + [
        f"{CSN}/sismicidad/catalogo/{dia:%Y/%m/%Y%m%d}.html"
        for dia in sorted(fechas)
    ]

    enlaces = set()
    filas_totales = 0
    problemas = 0

    for pagina in dict.fromkeys(paginas):
        try:
            contenido = obtener(pagina, permitir_vacio=True)
            if not contenido:
                LOG.warning("Catálogo no disponible: %s", pagina)
                continue

            nuevos, filas = enlaces_m4(contenido, pagina)
            enlaces.update(nuevos)
            filas_totales += filas

        except Exception as exc:
            problemas += 1
            LOG.warning(
                "Catálogo fallido: %s", type(exc).__name__
            )

    if filas_totales == 0:
        raise RuntimeError(
            "No se reconocieron catálogos CSN; "
            "no se interpreta como ausencia de sismos."
        )

    eventos = {}

    for url in sorted(enlaces):
        try:
            evento = leer_evento(url)
            if (
                evento["mag"] >= MAG_MIN
                and inicio <= UTCDateTime(evento["t"]) <= fin
            ):
                eventos[identificar_evento(url)] = evento
        except Exception as exc:
            problemas += 1
            LOG.warning(
                "Informe no leído %s: %s",
                url, type(exc).__name__,
            )

    LOG.info("Eventos encontrados: %d", len(eventos))
    return eventos, problemas


# =====================================================================
# ESTADO GITHUB CON RECONCILIACIÓN TRAS ERRORES TRANSITORIOS
# =====================================================================

class EstadoGitHub:
    def __init__(self):
        repo = os.getenv("GITHUB_REPOSITORY", "")
        token = os.getenv("GH_TOKEN", "")
        api = os.getenv(
            "GITHUB_API_URL", "https://api.github.com"
        ).rstrip("/")

        if not repo or not token:
            raise ErrorEstado("Faltan GITHUB_REPOSITORY o GH_TOKEN")

        self.base = f"{api}/repos/{repo}"
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self.sha = None
        self.datos = {"cuenta_id": None, "eventos": {}}

        self.crear_rama()
        self.cargar()

        LOG.info(
            "Estado: repo=%s rama=%s archivo=%s registros=%d",
            repo, STATE_BRANCH, STATE_PATH,
            len(self.datos["eventos"]),
        )

    def get(self, url, params=None):
        ultimo = "sin respuesta"

        for intento in range(GH_INTENTOS):
            try:
                r = self.sesion.get(
                    url, params=params, timeout=TIMEOUT_HTTP
                )
                if r.status_code not in {
                    408, 429, 500, 502, 503, 504
                }:
                    return r
                ultimo = f"HTTP {r.status_code}"
            except requests.RequestException as exc:
                ultimo = type(exc).__name__

            if intento + 1 < GH_INTENTOS:
                time.sleep(min(2 ** (intento + 1), 15))

        raise ErrorEstado(f"Lectura GitHub agotada: {ultimo}")

    def json_ok(self, r):
        if not r.ok:
            raise ErrorEstado(f"GitHub HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise ErrorEstado(
                "GitHub devolvió una respuesta no JSON"
            ) from exc

    def crear_rama(self):
        url_ref = (
            f"{self.base}/git/ref/heads/"
            f"{quote(STATE_BRANCH, safe='')}"
        )
        r = self.get(url_ref)

        if r.status_code != 404:
            self.json_ok(r)
            return

        repo = self.json_ok(self.get(self.base))
        principal = quote(repo["default_branch"], safe="")
        ref = self.json_ok(
            self.get(f"{self.base}/git/ref/heads/{principal}")
        )

        for intento in range(GH_INTENTOS):
            try:
                r = self.sesion.post(
                    f"{self.base}/git/refs",
                    json={
                        "ref": f"refs/heads/{STATE_BRANCH}",
                        "sha": ref["object"]["sha"],
                    },
                    timeout=TIMEOUT_HTTP,
                )
                if r.ok:
                    return
                codigo = r.status_code
            except requests.RequestException:
                codigo = None

            # Comprobar si la creación ocurrió aunque la respuesta fallara.
            comprobacion = self.get(url_ref)
            if comprobacion.ok:
                return
            if comprobacion.status_code != 404:
                self.json_ok(comprobacion)

            if codigo not in {None, 408, 429, 500, 502, 503, 504}:
                raise ErrorEstado(
                    f"No se pudo crear {STATE_BRANCH}: HTTP {codigo}"
                )

            time.sleep(min(2 ** (intento + 1), 15))

        raise ErrorEstado("No se confirmó la creación de la rama")

    def leer_remoto(self):
        r = self.get(
            f"{self.base}/contents/{STATE_PATH}",
            params={"ref": STATE_BRANCH},
        )
        if r.status_code == 404:
            return None, None

        archivo = self.json_ok(r)

        try:
            texto = base64.b64decode(
                archivo["content"]
            ).decode("utf-8")

            if not texto.strip():
                raise ErrorEstado(
                    "El archivo de estado está vacío. "
                    'Debe contener {"cuenta_id": null, "eventos": {}} '
                    "si realmente es un registro nuevo."
                )

            datos = json.loads(texto)
        except ErrorEstado:
            raise
        except Exception as exc:
            raise ErrorEstado("Archivo de estado inválido") from exc

        if not isinstance(datos.get("eventos"), dict):
            raise ErrorEstado("El JSON no contiene eventos válidos")

        return archivo["sha"], datos

    def cargar(self):
        self.sha, datos = self.leer_remoto()
        if datos is None:
            return

        self.datos = datos
        cambio = False

        for r in self.datos["eventos"].values():
            if r.get("estado") == "enviando":
                r["estado"] = "resultado_incierto"
                cambio = True
            elif r.get("estado") == "procesando":
                r["estado"] = "pendiente"
                cambio = True

        if cambio:
            self.guardar()

    def guardar(self):
        deseado = json.loads(json.dumps(
            self.datos, ensure_ascii=False, allow_nan=False
        ))
        contenido = json.dumps(
            deseado, ensure_ascii=False, indent=2
        ).encode("utf-8")

        sha_base = self.sha

        for intento in range(GH_INTENTOS):
            payload = {
                "message": "Actualizar estado CSN [skip ci]",
                "branch": STATE_BRANCH,
                "content": base64.b64encode(contenido).decode("ascii"),
            }
            if sha_base:
                payload["sha"] = sha_base

            codigo = None

            try:
                r = self.sesion.put(
                    f"{self.base}/contents/{STATE_PATH}",
                    json=payload,
                    timeout=TIMEOUT_HTTP,
                )
                codigo = r.status_code

                if r.ok:
                    try:
                        self.sha = r.json()["content"]["sha"]
                        return
                    except (ValueError, KeyError, TypeError):
                        # Reconciliar abajo si la confirmación es irregular.
                        codigo = None

            except requests.RequestException:
                pass

            # Antes de repetir, averiguar si la escritura ya se completó.
            sha_remoto, datos_remotos = self.leer_remoto()

            if datos_remotos == deseado:
                self.sha = sha_remoto
                return

            if sha_remoto != sha_base:
                raise ErrorEstado(
                    "El estado cambió en otra ejecución. "
                    "Se detiene para evitar sobreescribirlo."
                )

            if codigo not in {
                None, 408, 409, 429, 500, 502, 503, 504
            }:
                raise ErrorEstado(
                    f"Guardado GitHub HTTP {codigo}. "
                    "Verifica permisos si es 401/403."
                )

            if intento + 1 < GH_INTENTOS:
                LOG.warning(
                    "Guardado sin confirmar; reintento %d/%d",
                    intento + 2, GH_INTENTOS,
                )
                time.sleep(min(2 ** (intento + 1), 15))

        raise ErrorEstado("No se confirmó el guardado del estado")

    def actualizar(self, clave, **cambios):
        self.datos["eventos"][clave].update(cambios)
        self.guardar()

    def cerrar(self):
        self.sesion.close()


# =====================================================================
# INVENTARIO NACIONAL
# =====================================================================

def obtener_estaciones(evento):
    t = UTCDateTime(evento["t"])

    contenido = obtener(
        FDSN + "/fdsnws/station/1/query",
        params={
            "network": "*",
            "station": "*",
            "location": "*",
            "channel": CANALES,
            "starttime": fecha_fdsn(t - PRE_SEG - MARGEN_SEG),
            "endtime": fecha_fdsn(t + POST_SEG + MARGEN_SEG),
            "minlatitude": MIN_LAT,
            "maxlatitude": MAX_LAT,
            "minlongitude": MIN_LON,
            "maxlongitude": MAX_LON,
            "level": "channel",
            "format": "xml",
            "nodata": 204,
        },
        permitir_vacio=True,
    )

    if not contenido:
        raise SinDatos("CSN no devolvió inventario nacional")

    inventario = read_inventory(io.BytesIO(contenido))
    estaciones = {}

    for red in inventario:
        for estacion in red:
            for canal in estacion:
                lat = float(canal.latitude)
                lon = float(canal.longitude)

                if not np.isfinite([lat, lon]).all():
                    continue

                clave = f"{red.code}.{estacion.code}"
                loc = canal.location_code or ""

                try:
                    dip = float(canal.dip)
                except (TypeError, ValueError):
                    dip = np.nan

                # Orientación casi vertical; si no hay orientación,
                # aceptar el código Z.
                vertical = (
                    abs(dip) >= 75
                    if np.isfinite(dip)
                    else canal.code.endswith("Z")
                )

                opcion = {
                    "estacion_id": clave,
                    "id": f"{clave}.{loc}.{canal.code}",
                    "red": red.code,
                    "estacion": estacion.code,
                    "loc": loc,
                    "canal": canal.code,
                    "lat": lat,
                    "lon": lon,
                    "vertical": bool(vertical),
                    "distancia_km": distancia_km(
                        evento["lat"], evento["lon"], lat, lon
                    ),
                }

                if clave not in estaciones:
                    estaciones[clave] = {
                        "base": opcion,
                        "opciones": {},
                    }

                if vertical:
                    estaciones[clave]["opciones"][opcion["id"]] = opcion

    grupos = []
    for grupo in estaciones.values():
        opciones = list(grupo["opciones"].values())
        opciones.sort(
            key=lambda o: (
                not o["canal"].startswith("HN"),
                not o["canal"].endswith("Z"),
                o["id"],
            )
        )
        grupos.append({
            "base": grupo["base"],
            "opciones": opciones,
        })

    grupos.sort(key=lambda g: g["base"]["distancia_km"])

    if not grupos:
        raise SinDatos("No hay estaciones de acelerómetros en el inventario")

    LOG.info(
        "Inventario: %d estaciones; %d con componente vertical; "
        "sin límite de cantidad ni radio epicentral.",
        len(grupos),
        sum(bool(g["opciones"]) for g in grupos),
    )

    return grupos


def resultado_vacio(grupo, motivo):
    r = dict(grupo["base"])
    r.update({
        "valida": False,
        "serie": np.full(len(SEGUNDOS), np.nan),
        "motivo": motivo,
        "pico_cm_s2": None,
        "cobertura_pct": 0.0,
        "fs": None,
        "f3": None,
        "f4": None,
    })
    return r


# =====================================================================
# PROCESAMIENTO DE TRAMOS PARCIALES
# =====================================================================

def procesar_canal(opcion, evento):
    t = UTCDateTime(evento["t"])
    inicio = t - PRE_SEG - MARGEN_SEG
    fin = t + POST_SEG + MARGEN_SEG

    parametros = {
        "network": opcion["red"],
        "station": opcion["estacion"],
        "location": opcion["loc"] or "--",
        "channel": opcion["canal"],
        "starttime": fecha_fdsn(inicio),
        "endtime": fecha_fdsn(fin),
        "nodata": 204,
    }

    contenido = obtener(
        FDSN + "/fdsnws/dataselect/1/query",
        params=parametros,
        permitir_vacio=True,
    )

    if not contenido:
        raise SinDatos("Sin registros")

    st = read(io.BytesIO(contenido), format="MSEED")
    st = st.select(
        network=opcion["red"],
        station=opcion["estacion"],
        location=opcion["loc"],
        channel=opcion["canal"],
    )

    if not st:
        raise SinDatos("Canal ausente")

    # Los huecos quedan enmascarados. split() crea tramos continuos.
    st.sort()
    st.merge(method=0, fill_value=None)
    segmentos = st.split()

    parametros_respuesta = dict(parametros)
    parametros_respuesta.update({
        "level": "response",
        "format": "xml",
    })

    xml = obtener(
        FDSN + "/fdsnws/station/1/query",
        params=parametros_respuesta,
        permitir_vacio=True,
    )

    if not xml:
        raise SinDatos("Sin respuesta instrumental")

    inv = read_inventory(io.BytesIO(xml))

    serie = np.full(len(SEGUNDOS), np.nan)
    frecuencias = []
    filtros3 = []
    filtros4 = []
    errores = []

    for segmento in segmentos:
        try:
            tr = segmento.copy()
            fs = float(tr.stats.sampling_rate)

            if fs < 1:
                raise SinDatos("Muestreo menor a 1 Hz")

            duracion = float(tr.stats.endtime - tr.stats.starttime)
            if duracion < MIN_SEGMENTO_SEG:
                raise SinDatos("Tramo demasiado corto")

            tr.data = np.asarray(tr.data, dtype=np.float64)
            if not np.isfinite(tr.data).all():
                raise SinDatos("Muestras no finitas")
            if np.ptp(tr.data) == 0:
                raise SinDatos("Señal constante")

            respuesta = inv.get_response(tr.id, tr.stats.starttime)
            sensibilidad = respuesta.instrument_sensitivity
            if sensibilidad is None:
                raise SinDatos("Sin sensibilidad instrumental")

            unidades = str(
                sensibilidad.input_units
            ).upper().replace(" ", "")

            if unidades not in {
                "M/S**2", "M/S^2", "M/S/S", "M/S2"
            }:
                raise SinDatos("Unidades de aceleración no reconocidas")

            nyquist = fs / 2
            f3 = min(20.0, nyquist * 0.70)
            f4 = min(25.0, nyquist * 0.90)

            if not 0.05 < 0.10 < f3 < f4:
                raise SinDatos("Muestreo incompatible con prefiltro")

            tr.detrend("linear")
            tr.remove_response(
                inventory=inv,
                output="ACC",
                pre_filt=(0.05, 0.10, f3, f4),
                water_level=None,
                zero_mean=True,
                taper=True,
                taper_fraction=0.05,
            )

            # Excluir bordes de cada tramo para reducir efectos
            # del taper y la deconvolución alrededor de los huecos.
            borde = max(
                BORDE_DESCARTADO_SEG,
                0.05 * duracion,
            )
            util_inicio = max(
                tr.stats.starttime + borde,
                t - PRE_SEG,
            )
            util_fin = min(
                tr.stats.endtime - borde,
                t + POST_SEG,
            )

            if util_fin <= util_inicio:
                continue

            tr.trim(util_inicio, util_fin)

            valores = np.asarray(tr.data, dtype=float) * 100.0
            tiempos = tr.times() + float(tr.stats.starttime - t)

            if not np.isfinite(valores).all():
                raise SinDatos("Calibración no finita")

            minimo = max(1, int(math.ceil(
                fs * COBERTURA_MIN_POR_SEGUNDO
            )))

            for i, segundo in enumerate(SEGUNDOS):
                seleccion = (
                    (tiempos >= segundo)
                    & (tiempos < segundo + 1)
                )

                if int(seleccion.sum()) >= minimo:
                    pico = float(np.max(np.abs(valores[seleccion])))
                    if not np.isfinite(serie[i]) or pico > serie[i]:
                        serie[i] = pico

            frecuencias.append(fs)
            filtros3.append(f3)
            filtros4.append(f4)

        except SinDatos as exc:
            errores.append(str(exc))
        except Exception as exc:
            errores.append(type(exc).__name__)

    validos = np.isfinite(serie)

    if not validos.any():
        detalle = "; ".join(dict.fromkeys(errores))
        raise SinDatos(detalle or "Sin segundos utilizables")

    resultado = dict(opcion)
    resultado.update({
        "valida": True,
        "serie": serie,
        "pico_cm_s2": float(np.nanmax(serie)),
        "cobertura_pct": float(validos.mean() * 100),
        "fs": min(frecuencias),
        "f3": min(filtros3),
        "f4": min(filtros4),
        "motivo": (
            "Registro parcial; huecos conservados"
            if not validos.all()
            else ""
        ),
    })
    return resultado


def procesar_estacion(grupo, evento, limite):
    if not grupo["opciones"]:
        return resultado_vacio(
            grupo, "Sin componente vertical identificable"
        )

    mejor = None
    errores = []

    # Elegir el canal con mayor cobertura. No combinar sensores
    # distintos para inventar una continuidad temporal.
    for opcion in grupo["opciones"]:
        if time.monotonic() >= limite:
            errores.append("Presupuesto de descarga alcanzado")
            break

        try:
            r = procesar_canal(opcion, evento)

            if (
                mejor is None
                or r["cobertura_pct"] > mejor["cobertura_pct"]
            ):
                mejor = r

            if r["cobertura_pct"] >= 99.99:
                break

        except Exception as exc:
            errores.append(
                f"{opcion['id']}: "
                + (
                    str(exc)
                    if isinstance(exc, SinDatos)
                    else type(exc).__name__
                )
            )

    if mejor is not None:
        return mejor

    return resultado_vacio(
        grupo,
        "; ".join(dict.fromkeys(errores)) or "Sin datos utilizables",
    )


def descargar_estaciones(grupos, evento):
    limite = time.monotonic() + PRESUPUESTO_DESCARGA_SEG
    resultados = []
    siguiente = 0

    # Mantener solo un número acotado de solicitudes activas.
    with ThreadPoolExecutor(max_workers=TRABAJADORES) as executor:
        activos = {}

        while siguiente < len(grupos) or activos:
            while (
                siguiente < len(grupos)
                and len(activos) < TRABAJADORES
                and time.monotonic() < limite
            ):
                grupo = grupos[siguiente]
                futuro = executor.submit(
                    procesar_estacion, grupo, evento, limite
                )
                activos[futuro] = grupo
                siguiente += 1

            if not activos:
                break

            terminados, _ = wait(
                activos,
                timeout=10,
                return_when=FIRST_COMPLETED,
            )

            for futuro in terminados:
                grupo = activos.pop(futuro)
                try:
                    r = futuro.result()
                except Exception as exc:
                    r = resultado_vacio(
                        grupo, f"Fallo de procesamiento: {type(exc).__name__}"
                    )

                resultados.append(r)
                LOG.info(
                    "Estación %d/%d: %s · cobertura %.1f%% · %s",
                    len(resultados), len(grupos),
                    r["estacion_id"], r["cobertura_pct"],
                    r["motivo"] or "válida",
                )

        # Mantener identificadas también las estaciones no consultadas
        # por agotamiento del tiempo operativo.
        for grupo in grupos[siguiente:]:
            resultados.append(resultado_vacio(
                grupo,
                "No consultada: presupuesto de descarga alcanzado",
            ))

    resultados.sort(key=lambda r: r["distancia_km"])
    return resultados


# =====================================================================
# HEATMAP DINÁMICO: SOLO ESTACIONES CON DATOS EN ESE SEGUNDO
# =====================================================================

class CampoEspacial:
    def __init__(self, resultados):
        self.resultados = resultados
        self.cache = OrderedDict()

        self.lons = np.linspace(
            MIN_LON, MAX_LON, HEATMAP_RESOLUCION_X
        )
        self.lats = np.linspace(
            MIN_LAT, MAX_LAT, HEATMAP_RESOLUCION_Y
        )
        xx, yy = np.meshgrid(self.lons, self.lats)

        # Proyección azimutal equidistante centrada en Chile.
        from pyproj import CRS, Transformer

        destino = CRS.from_proj4(
            "+proj=aeqd +lat_0=-36.5 +lon_0=-71 "
            "+datum=WGS84 +units=m +no_defs"
        )
        self.transformar = Transformer.from_crs(
            "EPSG:4326", destino, always_xy=True
        )

        x, y = self.transformar.transform(xx.ravel(), yy.ravel())
        self.consultas = np.column_stack([x, y]) / 1000.0
        self.forma = xx.shape

    def preparar(self, mascara):
        clave = tuple(np.flatnonzero(mascara).tolist())

        if clave in self.cache:
            self.cache.move_to_end(clave)
            return self.cache[clave]

        grupos = {}
        for indice in clave:
            r = self.resultados[indice]
            ubicacion = (round(r["lon"], 4), round(r["lat"], 4))
            grupos.setdefault(ubicacion, []).append(indice)

        if len(grupos) < HEATMAP_MIN_VECINOS:
            return None

        coords = list(grupos)
        x, y = self.transformar.transform(
            [p[0] for p in coords],
            [p[1] for p in coords],
        )
        puntos = np.column_stack([x, y]) / 1000.0

        try:
            hull = Delaunay(puntos)
        except QhullError:
            return None

        k = min(HEATMAP_VECINOS, len(coords))
        d, ix = cKDTree(puntos).query(self.consultas, k=k)

        cobertura = (
            (hull.find_simplex(self.consultas) >= 0)
            & (
                (d <= HEATMAP_RADIO_KM).sum(axis=1)
                >= HEATMAP_MIN_VECINOS
            )
        )

        pesos = np.where(
            d <= HEATMAP_RADIO_KM,
            1 / np.maximum(d, 0.05) ** HEATMAP_POTENCIA,
            0.0,
        )
        pesos /= np.maximum(
            pesos.sum(axis=1, keepdims=True), 1e-30
        )

        geometria = (
            list(grupos.values()), ix, pesos, cobertura
        )
        self.cache[clave] = geometria

        # No acumular matrices grandes para todos los patrones de huecos.
        while len(self.cache) > 4:
            self.cache.popitem(last=False)

        return geometria

    def calcular(self, valores):
        mascara = np.isfinite(valores)
        geometria = self.preparar(mascara)

        if geometria is None:
            return np.ma.masked_all(self.forma)

        grupos, ix, pesos, cobertura = geometria
        agrupados = np.array([
            float(np.mean(valores[g])) for g in grupos
        ])
        z = (pesos * agrupados[ix]).sum(axis=1)

        return np.ma.array(
            z.reshape(self.forma),
            mask=(~cobertura).reshape(self.forma),
        )


# =====================================================================
# EXPORTACIÓN CSV Y GRÁFICOS
# =====================================================================

def guardar_resultados(evento, resultados, carpeta):
    carpeta.mkdir(parents=True, exist_ok=True)

    campos = [
        "estacion_id", "id", "lat", "lon", "distancia_km",
        "valida", "cobertura_pct", "pico_cm_s2",
        "fs", "f3", "f4", "motivo",
    ]

    with (carpeta / "estaciones.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        escritor = csv.DictWriter(f, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows({
            c: r.get(c) for c in campos
        } for r in resultados)

    with (carpeta / "aceleracion_por_segundo.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        escritor = csv.writer(f)
        escritor.writerow(
            ["segundos_desde_origen"]
            + [r["estacion_id"] for r in resultados]
        )
        for i, segundo in enumerate(SEGUNDOS):
            escritor.writerow(
                [int(segundo)]
                + [
                    float(r["serie"][i])
                    if np.isfinite(r["serie"][i])
                    else ""
                    for r in resultados
                ]
            )

    (carpeta / "evento.json").write_text(
        json.dumps(evento, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def dibujar_filas(ax, filas, inicio_numeracion=0):
    ax.clear()
    etiquetas = []

    for j, r in enumerate(filas):
        base = float(j)
        ax.axhline(base, color="#dfe5ed", linewidth=0.5)

        serie = r["serie"]
        validos = np.isfinite(serie)

        if validos.any():
            pico = max(float(np.nanmax(serie)), 1e-30)
            y = base - 0.70 * serie / pico

            # NaN corta la curva y no dibuja un puente sobre huecos.
            ax.plot(
                SEGUNDOS + 0.5, y,
                color="#146a91", linewidth=0.8,
            )
            ax.fill_between(
                SEGUNDOS + 0.5,
                base, y,
                where=validos,
                color="#3098bd", alpha=0.25,
            )
            etiqueta = (
                f"{inicio_numeracion + j + 1:03d} "
                f"{r['estacion_id']} "
                f"{r['pico_cm_s2']:.2g} | "
                f"{r['cobertura_pct']:.0f}%"
            )
        else:
            ax.text(
                30, base - 0.20,
                "SIN DATOS VÁLIDOS",
                fontsize=7, color="#8993a0",
                ha="center", va="center",
            )
            etiqueta = (
                f"{inicio_numeracion + j + 1:03d} "
                f"{r['estacion_id']} —"
            )

        etiquetas.append(etiqueta)

    ax.set_yticks(np.arange(len(filas)))
    ax.set_yticklabels(etiquetas, fontsize=7.5)
    ax.set_xlim(-PRE_SEG, POST_SEG)
    ax.set_ylim(max(len(filas), 1) - 0.30, -1.0)
    ax.axvline(0, color="#187fbd", linestyle="--", linewidth=0.8)
    ax.grid(axis="x", alpha=0.20)
    ax.set_xlabel("Segundos desde el origen", fontsize=9)

    return ax.axvline(
        -PRE_SEG, color="#cf4226", linewidth=1.2
    )


def guardar_grafico_completo(resultados, carpeta):
    # Varias columnas si hay muchas estaciones.
    filas_columna = 70
    columnas = max(1, math.ceil(len(resultados) / filas_columna))
    filas_max = min(filas_columna, len(resultados))

    fig, axes = plt.subplots(
        1, columnas,
        figsize=(10 * columnas, max(7, filas_max * 0.24 + 2)),
        squeeze=False,
    )

    for columna, ax in enumerate(axes[0]):
        inicio = columna * filas_columna
        filas = resultados[inicio:inicio + filas_columna]
        dibujar_filas(ax, filas, inicio)
        ax.set_title(
            "Estación · pico filtrado [cm/s²] · cobertura",
            fontsize=10,
        )

    fig.suptitle(
        "Todas las estaciones · escala individual · "
        "huecos sin rellenar",
        fontsize=13,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(carpeta / "grafico.png", dpi=120)
    plt.close(fig)


# =====================================================================
# VIDEO
# =====================================================================

def generar_video(evento, resultados, carpeta):
    matriz = np.array([r["serie"] for r in resultados])
    if not np.isfinite(matriz).any():
        raise SinDatos(
            "Sin muestras calibradas. Revisar estaciones.csv."
        )

    guardar_grafico_completo(resultados, carpeta)

    campo = CampoEspacial(resultados)

    maximos = np.max(
        np.where(np.isfinite(matriz), matriz, -np.inf),
        axis=0,
    )
    indice_resumen = int(np.argmax(maximos))
    maximo = max(float(np.max(maximos)), 1e-12)

    norma = PowerNorm(
        gamma=HEATMAP_GAMMA, vmin=0, vmax=maximo
    )
    cmap = plt.get_cmap("YlOrRd").copy()
    cmap.set_bad((1, 1, 1, 0))

    costa = []
    try:
        from cartopy.io import shapereader
        ruta = shapereader.natural_earth(
            resolution="110m",
            category="physical",
            name="coastline",
        )
        lector = shapereader.Reader(ruta)
        for geom in lector.geometries():
            partes = geom.geoms if hasattr(geom, "geoms") else [geom]
            for parte in partes:
                costa.append(parte.xy)
        lector.close()
    except Exception as exc:
        LOG.warning("Costa no disponible: %s", type(exc).__name__)

    plt.rcParams.update({
        "figure.facecolor": "#f4f7fb",
        "axes.facecolor": "white",
        "font.size": 9,
    })

    fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
    ax_mapa = fig.add_axes([0.025, 0.16, 0.27, 0.67])
    ax1 = fig.add_axes([0.465, 0.17, 0.19, 0.64])
    ax2 = fig.add_axes([0.795, 0.17, 0.19, 0.64])

    fig.suptitle(
        f"Sismo M {evento['mag_texto']} · {evento['referencia']}",
        fontsize=17, fontweight="bold", y=0.965,
    )

    prof = (
        f"{evento['prof']:g} km"
        if evento["prof"] is not None
        else "no informada"
    )

    fig.text(
        0.5, 0.92,
        f"{hora_local(evento):%d/%m/%Y %H:%M:%S} · "
        f"Profundidad: {prof} · "
        f"Epicentro: {evento['lat']:.4f}, {evento['lon']:.4f}",
        ha="center", fontsize=11,
    )

    paginas = max(
        1, math.ceil(len(resultados) / ESTACIONES_POR_PAGINA)
    )

    # Garantizar al menos un cuadro por página, incluso con inventarios
    # muy grandes. Las muestras temporales siguen siendo las mismas.
    numero_frames = max(len(SEGUNDOS), paginas)
    indices_tiempo = np.minimum(
        (
            np.arange(numero_frames)
            * len(SEGUNDOS)
            / numero_frames
        ).astype(int),
        len(SEGUNDOS) - 1,
    )

    pagina_texto = fig.text(
        0.66, 0.865, "", ha="center", fontsize=11
    )
    estado_texto = fig.text(
        0.5, 0.105, "", ha="center", fontsize=10
    )
    tiempo_texto = fig.text(
        0.5, 0.072, "", ha="center", fontsize=11
    )

    fig.text(
        0.5, 0.038,
        "Mapa: estimación espacial de |aZ| [cm/s²], "
        "escala de colores fija · Gráficos: normalización individual",
        ha="center", fontsize=10,
    )
    fig.text(
        0.5, 0.017,
        "Datos: CSN · Huecos no rellenados · "
        "La interpolación no representa propagación de ondas",
        ha="center", fontsize=9,
    )

    ax_mapa.set_xlim(MIN_LON, MAX_LON)
    ax_mapa.set_ylim(MIN_LAT, MAX_LAT)
    ax_mapa.set_aspect(1 / math.cos(math.radians(-36.5)))
    ax_mapa.set_xlabel("Longitud")
    ax_mapa.set_ylabel("Latitud")
    ax_mapa.grid(alpha=0.2)

    imagen = ax_mapa.imshow(
        campo.calcular(matriz[:, indice_resumen]),
        origin="lower",
        extent=(MIN_LON, MAX_LON, MIN_LAT, MAX_LAT),
        cmap=cmap, norm=norma,
        interpolation="nearest", alpha=0.85,
        aspect=ax_mapa.get_aspect(), zorder=2,
    )

    for x, y in costa:
        ax_mapa.plot(
            x, y, color="#617082", linewidth=0.6, zorder=3
        )

    lons = np.array([r["lon"] for r in resultados])
    lats = np.array([r["lat"] for r in resultados])

    # Todas las estaciones permanecen visibles en gris.
    ax_mapa.scatter(
        lons, lats,
        marker="x", s=15, color="#8b95a1",
        linewidths=0.6, zorder=4,
    )

    puntos = ax_mapa.scatter(
        lons, lats,
        c=np.ma.masked_invalid(matriz[:, indice_resumen]),
        cmap=cmap, norm=norma,
        s=24, edgecolors="#27323d",
        linewidths=0.3, zorder=5,
    )

    ax_mapa.scatter(
        [evento["lon"]], [evento["lat"]],
        marker="*", s=190, color="#1486d1",
        edgecolors="white", linewidths=0.7, zorder=7,
    )

    # Resaltar en azul las estaciones de la página del gráfico.
    destacados = ax_mapa.scatter(
        [], [], facecolors="none",
        edgecolors="#146aca", s=65,
        linewidths=0.9, zorder=6,
    )

    barra = fig.colorbar(
        imagen, ax=ax_mapa, fraction=0.045, pad=0.045
    )
    barra.set_label("Máximo |aZ| por segundo [cm/s²]", fontsize=9)

    ax_mapa.set_title(
        "Acelerómetros en extensión de Chile\n"
        "Gris: sin muestra válida en este segundo",
        fontsize=10,
    )

    pagina_actual = -1
    cursores = []
    etiquetas_mapa = []

    def dibujar_pagina(pagina):
        nonlocal pagina_actual, cursores, etiquetas_mapa

        if pagina == pagina_actual:
            return

        pagina_actual = pagina
        inicio = pagina * ESTACIONES_POR_PAGINA

        cursores = []

        for panel, ax in enumerate((ax1, ax2)):
            primero = inicio + panel * FILAS_POR_PANEL
            filas = resultados[primero:primero + FILAS_POR_PANEL]
            cursor = dibujar_filas(ax, filas, primero)
            ax.set_title(
                "Estación · pico [cm/s²] · cobertura",
                fontsize=10,
            )
            cursores.append(cursor)

        pagina_texto.set_text(
            f"Estaciones {inicio + 1}–"
            f"{min(inicio + ESTACIONES_POR_PAGINA, len(resultados))}"
            f" de {len(resultados)} · Página {pagina + 1}/{paginas}"
        )

        for etiqueta in etiquetas_mapa:
            etiqueta.remove()
        etiquetas_mapa = []

        seleccion = resultados[
            inicio:inicio + ESTACIONES_POR_PAGINA
        ]

        destacados.set_offsets(np.array([
            [r["lon"], r["lat"]] for r in seleccion
        ]).reshape(-1, 2))

        for j, r in enumerate(seleccion, start=inicio + 1):
            etiquetas_mapa.append(
                ax_mapa.annotate(
                    str(j), (r["lon"], r["lat"]),
                    xytext=(3, 3), textcoords="offset points",
                    fontsize=6, color="#13529a", zorder=8,
                )
            )

    def actualizar(indice, pagina):
        dibujar_pagina(pagina)

        valores = matriz[:, indice]
        interpolado = campo.calcular(valores)

        imagen.set_data(interpolado)
        puntos.set_array(np.ma.masked_invalid(valores))

        segundo = SEGUNDOS[indice]
        for cursor in cursores:
            cursor.set_xdata([segundo + 0.5, segundo + 0.5])

        n = int(np.isfinite(valores).sum())
        cobertura = int(interpolado.count())

        estado_texto.set_text(
            f"{n}/{len(resultados)} estaciones con dato en este segundo · "
            + (
                "Interpolación con cobertura suficiente"
                if cobertura
                else "Sin cobertura suficiente para interpolar: "
                     "se muestran las estaciones"
            )
        )

        velocidad = len(SEGUNDOS) * FPS / numero_frames
        tiempo_texto.set_text(
            f"Intervalo {segundo:+.0f} a {segundo + 1:+.0f} s · "
            f"Reproducción {velocidad:.2g}×"
        )

    actualizar(indice_resumen, 0)
    fig.canvas.draw()
    fig.savefig(carpeta / "resumen.png", dpi=120)

    # Exportar mapa del instante de mayor amplitud observada.
    from matplotlib.transforms import Bbox

    renderer = fig.canvas.get_renderer()
    caja = Bbox.union([
        ax_mapa.get_tightbbox(renderer),
        barra.ax.get_tightbbox(renderer),
    ]).transformed(
        fig.dpi_scale_trans.inverted()
    ).expanded(1.05, 1.05)

    fig.savefig(
        carpeta / "mapa.png", dpi=160, bbox_inches=caja
    )

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    matplotlib.rcParams["animation.ffmpeg_path"] = ffmpeg

    temporal = carpeta / "video_base.mp4"
    destino = carpeta / "video.mp4"

    writer = FFMpegWriter(
        fps=FPS,
        codec="libx264",
        extra_args=[
            "-pix_fmt", "yuv420p",
            "-crf", "21",
        ],
    )

    LOG.info(
        "Video: %d estaciones, %d páginas, %d cuadros",
        len(resultados), paginas, numero_frames,
    )

    try:
        with writer.saving(fig, str(temporal), dpi=100):
            for frame, indice in enumerate(indices_tiempo):
                pagina = min(
                    paginas - 1,
                    frame * paginas // numero_frames,
                )
                actualizar(int(indice), pagina)
                writer.grab_frame()
    finally:
        plt.close(fig)

    p = subprocess.run(
        [
            ffmpeg, "-y",
            "-i", str(temporal),
            "-vf", "fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",
            str(destino),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )

    if p.returncode != 0:
        raise RuntimeError("FFmpeg no pudo preparar el MP4")
    if not destino.is_file() or destino.stat().st_size == 0:
        raise RuntimeError("Video vacío")

    temporal.unlink(missing_ok=True)
    return destino


def video_del_evento(evento, carpeta):
    grupos = obtener_estaciones(evento)
    resultados = descargar_estaciones(grupos, evento)
    guardar_resultados(evento, resultados, carpeta)

    validas = sum(r["valida"] for r in resultados)
    LOG.info(
        "Resultado: %d/%d estaciones con al menos un segundo válido",
        validas, len(resultados),
    )

    return generar_video(evento, resultados, carpeta)


# =====================================================================
# X
# =====================================================================

def crear_sesion_x():
    valores = [
        X_API_KEY,
        X_API_SECRET,
        X_ACCESS_TOKEN,
        X_ACCESS_TOKEN_SECRET,
    ]
    if any(
        not isinstance(v, str)
        or not v.strip()
        or v.startswith("PEGA_AQUI")
        for v in valores
    ):
        raise RuntimeError("Credenciales de X incompletas")

    s = requests.Session()
    s.auth = OAuth1(*(v.strip() for v in valores))
    return s


def respuesta_x(r):
    if not r.ok:
        raise ErrorX(r.status_code)
    if not r.content:
        return {}

    contenido = r.json()
    if not isinstance(contenido, dict) or contenido.get("errors"):
        raise RuntimeError("Respuesta de X no válida")

    datos = contenido.get("data", {})
    if not isinstance(datos, dict):
        raise RuntimeError("Datos de X no válidos")
    return datos


def peso_texto(texto):
    texto = re.sub(r"https?://\S+", "u" * 23, texto)
    return sum(1 if ord(c) <= 0x10FF else 2 for c in texto)


def texto_publicacion(evento):
    profundidad = (
        f"{evento['prof']:g} km"
        if evento["prof"] is not None
        else "no informada"
    )
    referencia = " ".join(evento["referencia"].split())

    def construir(lugar):
        return (
            f"Sismo M {evento['mag_texto']} | {lugar}\n"
            f"{hora_local(evento):%d/%m/%Y %H:%M:%S}\n"
            f"Profundidad: {profundidad}.\n"
            f"Epicentro: lat {evento['lat']:.4f}°, "
            f"lon {evento['lon']:.4f}°.\n"
            f"{evento['url']}"
        )

    texto = construir(referencia)
    while peso_texto(texto) > 275 and referencia:
        referencia = referencia[:-1]
        texto = construir(referencia.rstrip() + "…")

    if peso_texto(texto) > 280:
        raise RuntimeError("Texto demasiado largo")

    return texto


def subir_video_x(sesion, ruta):
    datos = respuesta_x(
        sesion.post(
            API_X + "/media/upload/initialize",
            json={
                "media_type": "video/mp4",
                "total_bytes": ruta.stat().st_size,
                "media_category": "tweet_video",
            },
            timeout=(15, 90),
        )
    )
    media_id = str(datos["id"])

    with ruta.open("rb") as f:
        segmento = 0
        while True:
            bloque = f.read(4 * 1024 * 1024)
            if not bloque:
                break

            respuesta_x(
                sesion.post(
                    API_X + f"/media/upload/{media_id}/append",
                    data={"segment_index": str(segmento)},
                    files={
                        "media": ("segmento.mp4", bloque, "video/mp4")
                    },
                    timeout=(15, 120),
                )
            )
            segmento += 1

    datos = respuesta_x(
        sesion.post(
            API_X + f"/media/upload/{media_id}/finalize",
            timeout=(15, 90),
        )
    )
    info = datos.get("processing_info")
    limite = time.monotonic() + MAX_PROCESAMIENTO_X_SEG

    while info:
        estado = info.get("state")
        if estado == "succeeded":
            return media_id
        if estado == "failed":
            raise RuntimeError("X rechazó el video")
        if estado not in {"pending", "in_progress"}:
            raise RuntimeError("Estado de procesamiento desconocido")

        restante = limite - time.monotonic()
        if restante <= 0:
            raise RuntimeError("Tiempo de procesamiento X agotado")

        time.sleep(min(
            max(1, int(info.get("check_after_secs", 5))),
            restante,
        ))

        if time.monotonic() >= limite:
            raise RuntimeError("Tiempo de procesamiento X agotado")

        datos = respuesta_x(
            sesion.get(
                API_X + "/media/upload",
                params={"command": "STATUS", "media_id": media_id},
                timeout=(15, 60),
            )
        )
        info = datos.get("processing_info")
        if not info:
            raise RuntimeError("X no devolvió el estado del video")

    return media_id


def publicar_evento(estado, sesion, clave, evento, ruta):
    texto = texto_publicacion(evento)
    LOG.info("Texto:\n%s", texto)

    if not PUBLICAR_EN_X:
        estado.actualizar(
            clave, estado="simulado", texto=texto, ultimo_error=None
        )
        return

    media_id = subir_video_x(sesion, ruta)

    # Confirmar el guardado antes de enviar el tweet.
    estado.actualizar(
        clave,
        estado="enviando",
        texto=texto,
        media_id=media_id,
    )

    try:
        r = sesion.post(
            API_X + "/tweets",
            json={
                "text": texto,
                "media": {"media_ids": [media_id]},
            },
            timeout=(15, 90),
        )
    except requests.RequestException:
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error="POST sin confirmación",
        )
        raise RuntimeError("Publicación incierta; revisar X")

    if r.status_code >= 500 or r.status_code == 408:
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error=f"HTTP {r.status_code} tras POST",
        )
        raise RuntimeError("Publicación incierta")

    if not r.ok:
        estado.actualizar(
            clave,
            estado="pendiente",
            ultimo_error=f"X HTTP {r.status_code}",
        )
        raise ErrorX(r.status_code)

    try:
        tweet_id = str(respuesta_x(r)["id"])
    except Exception:
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error="Respuesta POST no reconocida",
        )
        raise RuntimeError("ID del tweet no confirmado")

    estado.actualizar(
        clave,
        estado="publicado",
        tweet_id=tweet_id,
        publicado=time.time(),
        ultimo_error=None,
    )

    LOG.info("Publicado: https://x.com/i/status/%s", tweet_id)


# =====================================================================
# EJECUCIÓN ÚNICA
# =====================================================================

def ejecutar_una_vez():
    inicio_ejecucion = time.monotonic()
    fin = UTCDateTime()
    inicio = fin - HORAS_BUSQUEDA * 3600

    LOG.info(
        "CSN -> HEATMAP NACIONAL -> VIDEO -> X | M >= %.1f",
        MAG_MIN,
    )
    LOG.info("Ventana UTC: %s a %s", inicio, fin)

    estado = EstadoGitHub()
    sesion_x = None
    fallos = 0

    try:
        try:
            eventos, problemas = buscar_eventos_una_vez(inicio, fin)
            if problemas:
                fallos += 1
        except Exception as exc:
            eventos = {}
            fallos += 1
            LOG.error("Búsqueda CSN fallida: %s", exc)

        registros = estado.datos["eventos"]
        cambios = False

        for clave, evento in eventos.items():
            if clave not in registros:
                registros[clave] = {
                    "evento": evento,
                    "estado": "pendiente",
                    "detectado": time.time(),
                    "intentos": 0,
                    "tweet_id": None,
                }
                cambios = True
            elif registros[clave]["estado"] == "pendiente":
                if registros[clave]["evento"] != evento:
                    registros[clave]["evento"] = evento
                    cambios = True
            else:
                LOG.info(
                    "Omitido %s: estado=%s tweet_id=%s",
                    clave,
                    registros[clave].get("estado"),
                    registros[clave].get("tweet_id"),
                )

        if cambios:
            estado.guardar()

        cola = []
        for clave, registro in registros.items():
            if registro.get("estado") != "pendiente":
                continue

            evento = registro["evento"]
            t = UTCDateTime(evento["t"])
            if inicio <= t <= fin and evento["mag"] >= MAG_MIN:
                disponible = (
                    t + POST_SEG + MARGEN_SEG + LATENCIA_SEG
                )
                if disponible <= fin:
                    cola.append((clave, registro))
                else:
                    LOG.info(
                        "%s: ventana posterior aún no disponible",
                        clave,
                    )

        cola.sort(key=lambda item: item[1]["evento"]["t"])

        if not cola:
            LOG.info("Sin eventos nuevos o pendientes listos. Fin.")
            return 1 if fallos else 0

        if PUBLICAR_EN_X:
            sesion_x = crear_sesion_x()
            cuenta = respuesta_x(
                sesion_x.get(
                    API_X + "/users/me",
                    timeout=(15, 60),
                )
            )
            cuenta_id = str(cuenta["id"])
            LOG.info(
                "Cuenta: @%s",
                cuenta.get("username", cuenta_id),
            )
        else:
            cuenta_id = "SIMULACION"

        anterior = estado.datos.get("cuenta_id")
        if anterior is not None and anterior != cuenta_id:
            raise ErrorEstado("El estado pertenece a otra cuenta")

        if anterior is None:
            estado.datos["cuenta_id"] = cuenta_id
            estado.guardar()

        SALIDA.mkdir(parents=True, exist_ok=True)

        for posicion, (clave, registro) in enumerate(cola, 1):
            if (
                time.monotonic() - inicio_ejecucion
                > PRESUPUESTO_LOTE_SEG
            ):
                LOG.info("Restantes pendientes para otra ejecución")
                break

            evento = registro["evento"]
            estado.actualizar(
                clave,
                estado="procesando",
                intentos=registro.get("intentos", 0) + 1,
            )

            LOG.info(
                "[%d/%d] %s · M %s · %s",
                posicion, len(cola), clave,
                evento["mag_texto"], evento["referencia"],
            )

            try:
                texto_publicacion(evento)
                ruta = video_del_evento(evento, SALIDA / clave)
                publicar_evento(
                    estado, sesion_x, clave, evento, ruta
                )

            except ErrorEstado:
                raise

            except Exception as exc:
                fallos += 1
                LOG.exception("No se completó el evento %s", clave)

                actual = registros[clave]["estado"]
                if actual == "procesando":
                    estado.actualizar(
                        clave,
                        estado="pendiente",
                        ultimo_error=str(exc)[:250],
                    )
                elif actual == "enviando":
                    estado.actualizar(
                        clave,
                        estado="resultado_incierto",
                        ultimo_error=type(exc).__name__,
                    )

                if isinstance(exc, ErrorX) and exc.status in {
                    401, 402, 403, 429
                }:
                    break

        LOG.info("Ciclo finalizado")
        return 1 if fallos else 0

    finally:
        if sesion_x is not None:
            sesion_x.close()
        estado.cerrar()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("matplotlib").setLevel(logging.WARNING)

    try:
        return ejecutar_una_vez()
    except KeyboardInterrupt:
        LOG.warning("Interrumpido")
        return 130
    except Exception:
        LOG.exception("Ejecución detenida")
        return 1


if __name__ == "__main__":
    sys.exit(main())
