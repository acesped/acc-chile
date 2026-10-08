#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CSN -> MAPA DE CALOR + GRÁFICO POR ESTACIÓN + VIDEO -> X

Ejecución autónoma en GitHub Actions.
Un ciclo por invocación. La programación está en el YAML.

- Busca sismos M >= 4 en las últimas 12 horas.
- Procesa nuevos y pendientes.
- Consulta acelerómetros verticales HNZ / ENZ dentro de 350 km.
- Sin límite fijo de cantidad de estaciones.
- Una fila por estación en el gráfico.
- Estaciones sin datos identificadas.
- Interpolación espacial IDW, limitada por cobertura.
- Conserva estado en GitHub antes de publicar.
- Credenciales de X directamente en el código.

IMPORTANTE:
El mapa de calor es una estimación espacial a partir de mediciones.
No simula propagación de ondas ni representa intensidad macrosísmica.

La señal mostrada es máximo absoluto de aceleración vertical filtrada
por segundo, en cm/s².

Los gráficos por estación usan normalización individual para mostrar
señales pequeñas. El pico físico aparece junto al nombre de la estación.
El mapa conserva una escala física común durante todo el video.
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

from concurrent.futures import ThreadPoolExecutor, as_completed
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
from matplotlib.colors import Normalize
from matplotlib.transforms import Bbox
from obspy import UTCDateTime, read, read_inventory
from obspy.geodetics import gps2dist_azimuth
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
HORAS_BUSQUEDA = 12

PRE_SEG = 60
POST_SEG = 120
MARGEN_SEG = 30
LATENCIA_SEG = 120

RADIO_KM = 350.0

# No se limita la cantidad de estaciones.
TRABAJADORES = 4
CANALES = "HNZ,ENZ"

# Un cuadro representa un segundo de datos.
# 180 segundos / 4 FPS = 45 segundos de video.
FPS = 4

# Configuración espacial.
HEATMAP_RESOLUCION = 180
HEATMAP_VECINOS = 8
HEATMAP_POTENCIA = 2.0

# Cada celda debe tener al menos tres estaciones válidas
# dentro de esta distancia, además de estar dentro de su envolvente.
HEATMAP_MIN_VECINOS = 3
HEATMAP_RADIO_KM = 150.0

PUBLICAR_EN_X = os.getenv(
    "PUBLISH_TO_X", "true"
).strip().lower() in {"true", "1", "yes"}

SALIDA = Path(os.getenv("CSN_OUTPUT", "output"))

API_X = "https://api.x.com/2"
MAX_PROCESAMIENTO_X_SEG = 15 * 60

STATE_BRANCH = os.getenv("STATE_BRANCH", "csn-state")
STATE_PATH = (
    "estado_publicaciones.json"
    if PUBLICAR_EN_X
    else "estado_simulaciones.json"
)

PRESUPUESTO_SEG = 35 * 60
TIMEOUT_HTTP = (15, 75)

LOG = logging.getLogger("csn-monitor")


# =====================================================================
# CREDENCIALES DE X
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
    respuesta = requests.get(
        url,
        params=params,
        headers={"User-Agent": "CSN-Earthquake-Monitor/2.0"},
        timeout=TIMEOUT_HTTP,
    )
    if permitir_vacio and respuesta.status_code in (204, 404):
        return b""
    respuesta.raise_for_status()
    return respuesta.content


def fecha_fdsn(t):
    return UTCDateTime(t).strftime("%Y-%m-%dT%H:%M:%S.%f")


def identificar_evento(url):
    partes = urlparse(url).path.strip("/").split("/")
    return "_".join(partes[-3:]).replace(".html", "")


def hora_local(evento):
    return UTCDateTime(evento["t"]).datetime.replace(
        tzinfo=timezone.utc
    ).astimezone(ZoneInfo("America/Santiago"))


# =====================================================================
# CSN: INFORMES Y CATÁLOGOS
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
        nombre = normalizar(nombre)
        for clave, valor in campos.items():
            if clave == nombre or clave.startswith(nombre):
                return valor
        if obligatorio:
            raise ValueError(f"Informe sin campo: {nombre}")
        return None

    fecha = None
    texto_hora = campo("hora utc")

    patrones = (
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
    )

    for patron, formato in patrones:
        coincidencia = re.search(patron, texto_hora)
        if coincidencia:
            texto = " ".join(coincidencia.group().split())
            fecha = datetime.strptime(texto, formato).replace(
                tzinfo=timezone.utc
            )
            break

    if fecha is None:
        raise ValueError("Hora UTC de CSN no reconocida")

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
    except (ValueError, TypeError):
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
                "Fallo de catálogo %s: %s",
                pagina, type(exc).__name__,
            )

    if filas_totales == 0:
        raise RuntimeError(
            "No se reconocieron catálogos de CSN. "
            "No equivale a ausencia de sismos."
        )

    eventos = {}

    for url in sorted(enlaces):
        try:
            evento = leer_evento(url)
            t = UTCDateTime(evento["t"])
            if evento["mag"] >= MAG_MIN and inicio <= t <= fin:
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
# ESTADO PERSISTENTE EN GITHUB
# =====================================================================

class EstadoGitHub:
    def __init__(self):
        repo = os.getenv("GITHUB_REPOSITORY", "")
        token = os.getenv("GH_TOKEN", "")
        api = os.getenv(
            "GITHUB_API_URL", "https://api.github.com"
        ).rstrip("/")

        if not repo or not token:
            raise ErrorEstado(
                "Faltan GITHUB_REPOSITORY o GH_TOKEN en Actions"
            )

        self.base = f"{api}/repos/{repo}"
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self.sha = None
        self.datos = {"cuenta_id": None, "eventos": {}}

        self.crear_rama_si_falta()
        self.cargar()

    def comprobar(self, respuesta):
        if not respuesta.ok:
            raise ErrorEstado(
                f"GitHub HTTP {respuesta.status_code}. "
                f"Verifica contents: write y la rama {STATE_BRANCH}."
            )
        return respuesta.json()

    def crear_rama_si_falta(self):
        rama = quote(STATE_BRANCH, safe="")
        r = self.sesion.get(
            f"{self.base}/git/ref/heads/{rama}",
            timeout=TIMEOUT_HTTP,
        )
        if r.status_code != 404:
            self.comprobar(r)
            return

        repo = self.comprobar(
            self.sesion.get(self.base, timeout=TIMEOUT_HTTP)
        )
        principal = quote(repo["default_branch"], safe="")
        ref = self.comprobar(
            self.sesion.get(
                f"{self.base}/git/ref/heads/{principal}",
                timeout=TIMEOUT_HTTP,
            )
        )
        self.comprobar(
            self.sesion.post(
                f"{self.base}/git/refs",
                json={
                    "ref": f"refs/heads/{STATE_BRANCH}",
                    "sha": ref["object"]["sha"],
                },
                timeout=TIMEOUT_HTTP,
            )
        )

    def cargar(self):
        r = self.sesion.get(
            f"{self.base}/contents/{STATE_PATH}",
            params={"ref": STATE_BRANCH},
            timeout=TIMEOUT_HTTP,
        )
        if r.status_code == 404:
            return

        archivo = self.comprobar(r)
        self.sha = archivo["sha"]
        self.datos = json.loads(
            base64.b64decode(archivo["content"]).decode("utf-8")
        )

        if not isinstance(self.datos.get("eventos"), dict):
            raise ErrorEstado("Estado remoto inválido")

        cambio = False
        for registro in self.datos["eventos"].values():
            if registro.get("estado") == "enviando":
                registro["estado"] = "resultado_incierto"
                cambio = True
            elif registro.get("estado") == "procesando":
                registro["estado"] = "pendiente"
                cambio = True

        if cambio:
            self.guardar()

    def guardar(self):
        contenido = json.dumps(
            self.datos,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")

        payload = {
            "message": "Actualizar estado CSN [skip ci]",
            "branch": STATE_BRANCH,
            "content": base64.b64encode(contenido).decode("ascii"),
        }
        if self.sha:
            payload["sha"] = self.sha

        try:
            resultado = self.comprobar(
                self.sesion.put(
                    f"{self.base}/contents/{STATE_PATH}",
                    json=payload,
                    timeout=TIMEOUT_HTTP,
                )
            )
            self.sha = resultado["content"]["sha"]
        except ErrorEstado:
            raise
        except Exception as exc:
            raise ErrorEstado(
                "No se confirmó el guardado del estado"
            ) from exc

    def actualizar(self, clave, **cambios):
        self.datos["eventos"][clave].update(cambios)
        self.guardar()

    def cerrar(self):
        self.sesion.close()


# =====================================================================
# INVENTARIO: TODAS LAS ESTACIONES CANDIDATAS EN EL RADIO
# =====================================================================

def obtener_estaciones(evento):
    t = UTCDateTime(evento["t"])
    inicio = t - PRE_SEG - MARGEN_SEG
    fin = t + POST_SEG + MARGEN_SEG

    contenido = obtener(
        FDSN + "/fdsnws/station/1/query",
        params={
            "network": "*",
            "station": "*",
            "location": "*",
            "channel": CANALES,
            "starttime": fecha_fdsn(inicio),
            "endtime": fecha_fdsn(fin),
            "latitude": evento["lat"],
            "longitude": evento["lon"],
            "maxradius": RADIO_KM / 111.195,
            "level": "response",
            "format": "xml",
            "nodata": 204,
        },
        permitir_vacio=True,
    )

    if not contenido:
        raise SinDatos("CSN no devolvió inventario")

    inventario = read_inventory(io.BytesIO(contenido))
    estaciones = {}

    for red in inventario:
        for estacion in red:
            for canal in estacion:
                if canal.code not in {"HNZ", "ENZ"}:
                    continue

                lat = float(canal.latitude)
                lon = float(canal.longitude)

                if not np.isfinite([lat, lon]).all():
                    continue

                distancia = gps2dist_azimuth(
                    evento["lat"], evento["lon"], lat, lon
                )[0] / 1000.0

                if distancia > RADIO_KM:
                    continue

                clave = f"{red.code}.{estacion.code}"
                loc = canal.location_code or ""

                motivo = ""

                if canal.start_date and canal.start_date > inicio:
                    motivo = "Metadatos no cubren el inicio"
                elif canal.end_date and canal.end_date < fin:
                    motivo = "Metadatos no cubren el final"
                elif canal.response is None:
                    motivo = "Sin respuesta instrumental"
                else:
                    sensibilidad = canal.response.instrument_sensitivity
                    if sensibilidad is None:
                        motivo = "Sin sensibilidad instrumental"
                    else:
                        unidades = str(
                            sensibilidad.input_units
                        ).upper().replace(" ", "")

                        if unidades not in {
                            "M/S**2", "M/S^2", "M/S/S", "M/S2"
                        }:
                            motivo = "Unidades de aceleración no reconocidas"

                opcion = {
                    "estacion_id": clave,
                    "red": red.code,
                    "estacion": estacion.code,
                    "loc": loc,
                    "canal": canal.code,
                    "id": f"{clave}.{loc}.{canal.code}",
                    "lat": lat,
                    "lon": lon,
                    "distancia_km": distancia,
                    "problema_inventario": motivo,
                }

                estaciones.setdefault(clave, []).append(opcion)

    grupos = sorted(
        estaciones.values(),
        key=lambda opciones: opciones[0]["distancia_km"],
    )

    if not grupos:
        raise SinDatos(
            f"No hay estaciones HNZ/ENZ dentro de {RADIO_KM:g} km"
        )

    LOG.info(
        "Estaciones candidatas: %d. Sin límite fijo.",
        len(grupos),
    )
    return inventario, grupos


# =====================================================================
# PROCESAMIENTO: REGISTRAR TAMBIÉN LOS DESCARTES
# =====================================================================

def procesar_estacion(opciones, inventario, evento):
    t = UTCDateTime(evento["t"])
    inicio = t - PRE_SEG - MARGEN_SEG
    fin = t + POST_SEG + MARGEN_SEG

    opciones = sorted(
        opciones,
        key=lambda o: (
            bool(o["problema_inventario"]),
            o["canal"] != "HNZ",
            o["loc"],
        ),
    )

    errores = []

    for opcion in opciones:
        if opcion["problema_inventario"]:
            errores.append(
                f"{opcion['id']}: {opcion['problema_inventario']}"
            )
            continue

        try:
            contenido = obtener(
                FDSN + "/fdsnws/dataselect/1/query",
                params={
                    "network": opcion["red"],
                    "station": opcion["estacion"],
                    "location": opcion["loc"] or "--",
                    "channel": opcion["canal"],
                    "starttime": fecha_fdsn(inicio),
                    "endtime": fecha_fdsn(fin),
                    "nodata": 204,
                },
                permitir_vacio=True,
            )

            if not contenido:
                raise SinDatos("Servidor sin registros")

            st = read(io.BytesIO(contenido), format="MSEED")
            st = st.select(
                network=opcion["red"],
                station=opcion["estacion"],
                location=opcion["loc"],
                channel=opcion["canal"],
            )
            if not st:
                raise SinDatos("Canal solicitado ausente")

            st.sort()
            st.merge(method=0, fill_value=None)

            if len(st) != 1:
                raise SinDatos("Trazas incompatibles")

            tr = st[0]

            if np.ma.isMaskedArray(tr.data):
                if np.ma.getmaskarray(tr.data).any():
                    raise SinDatos("Huecos o solapamientos inconsistentes")
                tr.data = np.asarray(tr.data)

            fs = float(tr.stats.sampling_rate)
            if fs < 10:
                raise SinDatos("Frecuencia de muestreo menor a 10 Hz")

            tolerancia = 1.5 / fs
            if (
                tr.stats.starttime > inicio + tolerancia
                or tr.stats.endtime < fin - tolerancia
            ):
                raise SinDatos("Ventana temporal incompleta")

            tr.data = np.asarray(tr.data, dtype=np.float64)

            if not np.isfinite(tr.data).all():
                raise SinDatos("Muestras no finitas")
            if np.ptp(tr.data) == 0:
                raise SinDatos("Señal constante")

            tr.detrend("linear")

            nyquist = fs / 2
            f3 = min(20.0, nyquist * 0.70)
            f4 = min(25.0, nyquist * 0.90)

            tr.remove_response(
                inventory=inventario,
                output="ACC",
                pre_filt=(0.05, 0.10, f3, f4),
                water_level=None,
                zero_mean=True,
                taper=True,
                taper_fraction=0.05,
            )

            tr.trim(t - PRE_SEG, t + POST_SEG)

            aceleracion = np.asarray(tr.data, dtype=float) * 100.0
            tiempos = tr.times() + float(tr.stats.starttime - t)

            if not len(aceleracion) or not np.isfinite(aceleracion).all():
                raise SinDatos("Resultado de calibración inválido")

            segundos = np.arange(-PRE_SEG, POST_SEG, dtype=float)
            serie = np.full(len(segundos), np.nan)

            for i, segundo in enumerate(segundos):
                seleccion = (
                    (tiempos >= segundo)
                    & (tiempos < segundo + 1)
                )
                if seleccion.any():
                    serie[i] = np.max(np.abs(aceleracion[seleccion]))

            if not np.isfinite(serie).all():
                raise SinDatos("Intervalos de un segundo sin datos")

            resultado = dict(opcion)
            resultado.update({
                "valida": True,
                "motivo": "",
                "serie": serie,
                "pico_cm_s2": float(np.max(np.abs(aceleracion))),
                "fs": fs,
                "f3": f3,
                "f4": f4,
            })
            return resultado

        except SinDatos as exc:
            errores.append(f"{opcion['id']}: {exc}")
        except Exception as exc:
            errores.append(
                f"{opcion['id']}: {type(exc).__name__}"
            )

    resultado = dict(opciones[0])
    resultado.update({
        "valida": False,
        "motivo": "; ".join(dict.fromkeys(errores)),
        "serie": None,
        "pico_cm_s2": None,
        "fs": None,
        "f3": None,
        "f4": None,
    })
    return resultado


# =====================================================================
# INTERPOLACIÓN ESPACIAL IDW CON MÁSCARA
# =====================================================================

class CampoEspacial:
    """
    Interpolación ponderada por distancia.

    Una celda solo se muestra cuando:
    - Está dentro de la envolvente convexa de estaciones válidas.
    - Tiene al menos tres ubicaciones dentro del radio establecido.

    No se extrapola fuera de esa cobertura.
    Estaciones prácticamente coincidentes se agrupan.
    """

    def __init__(self, validos, evento, limites):
        self.forma = (
            HEATMAP_RESOLUCION,
            HEATMAP_RESOLUCION,
        )
        self.disponible = False
        self.motivo = ""
        self.grupos = []

        lon_min, lon_max, lat_min, lat_max = limites
        self.lons = np.linspace(lon_min, lon_max, self.forma[1])
        self.lats = np.linspace(lat_min, lat_max, self.forma[0])
        self.lon_grid, self.lat_grid = np.meshgrid(
            self.lons, self.lats
        )

        # Proyección local aproximada a kilómetros.
        coslat = max(0.2, math.cos(math.radians(evento["lat"])))

        def proyectar(lon, lat):
            return np.column_stack([
                (np.asarray(lon) - evento["lon"]) * 111.195 * coslat,
                (np.asarray(lat) - evento["lat"]) * 111.195,
            ])

        ubicaciones = {}
        for i, r in enumerate(validos):
            clave = (round(r["lon"], 4), round(r["lat"], 4))
            ubicaciones.setdefault(clave, []).append(i)

        coordenadas = list(ubicaciones)
        self.grupos = list(ubicaciones.values())

        if len(coordenadas) < HEATMAP_MIN_VECINOS:
            self.motivo = "Menos de 3 ubicaciones válidas"
            return

        xy = proyectar(
            [c[0] for c in coordenadas],
            [c[1] for c in coordenadas],
        )
        consultas = proyectar(
            self.lon_grid.ravel(),
            self.lat_grid.ravel(),
        )

        try:
            triangulacion = Delaunay(xy)
        except QhullError:
            self.motivo = "Ubicaciones alineadas: sin área interpolable"
            return

        k = min(HEATMAP_VECINOS, len(coordenadas))
        distancias, indices = cKDTree(xy).query(consultas, k=k)

        dentro = triangulacion.find_simplex(consultas) >= 0
        suficientes = (
            (distancias <= HEATMAP_RADIO_KM).sum(axis=1)
            >= HEATMAP_MIN_VECINOS
        )

        self.mascara = dentro & suficientes

        pesos = np.where(
            distancias <= HEATMAP_RADIO_KM,
            1.0 / np.maximum(distancias, 0.05) ** HEATMAP_POTENCIA,
            0.0,
        )
        suma = pesos.sum(axis=1, keepdims=True)
        pesos = pesos / np.maximum(suma, 1e-30)

        self.indices = indices
        self.pesos = pesos
        self.disponible = bool(self.mascara.any())

        if not self.disponible:
            self.motivo = "Separación excesiva entre estaciones válidas"

    def calcular(self, valores):
        if not self.disponible:
            return np.ma.masked_all(self.forma)

        valores = np.asarray(valores, dtype=float)

        # Promediar observaciones prácticamente coincidentes.
        agrupados = np.array([
            float(np.mean(valores[grupo]))
            for grupo in self.grupos
        ])

        campo = np.sum(
            self.pesos * agrupados[self.indices],
            axis=1,
        )

        return np.ma.array(
            campo.reshape(self.forma),
            mask=(~self.mascara).reshape(self.forma),
        )


# =====================================================================
# EXPORTAR RESULTADOS TABULARES
# =====================================================================

def guardar_resultados(evento, resultados, carpeta):
    carpeta.mkdir(parents=True, exist_ok=True)

    campos = [
        "estacion_id", "id", "lat", "lon", "distancia_km",
        "valida", "motivo", "pico_cm_s2", "fs", "f3", "f4",
    ]

    with (carpeta / "estaciones.csv").open(
        "w", newline="", encoding="utf-8"
    ) as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows({
            campo: r.get(campo) for campo in campos
        } for r in resultados)

    segundos = np.arange(-PRE_SEG, POST_SEG)

    with (carpeta / "aceleracion_por_segundo.csv").open(
        "w", newline="", encoding="utf-8"
    ) as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow(
            ["segundos_desde_origen"]
            + [r["estacion_id"] for r in resultados]
        )
        for i, segundo in enumerate(segundos):
            escritor.writerow(
                [int(segundo)]
                + [
                    float(r["serie"][i]) if r["valida"] else ""
                    for r in resultados
                ]
            )

    (carpeta / "evento.json").write_text(
        json.dumps(evento, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# =====================================================================
# MAPA DE CALOR, GRÁFICO POR FILAS Y VIDEO
# =====================================================================

def generar_video(evento, resultados, carpeta):
    validos = [r for r in resultados if r["valida"]]
    invalidos = [r for r in resultados if not r["valida"]]

    if not validos:
        raise SinDatos(
            "Ninguna estación tiene registros completos y calibrables. "
            "Consulta estaciones.csv."
        )

    matriz = np.array([r["serie"] for r in validos])
    segundos = np.arange(-PRE_SEG, POST_SEG, dtype=float)
    tiempos_centro = segundos + 0.5

    # Un instante real para las imágenes estáticas:
    # segundo que contiene el mayor valor observado.
    indice_resumen = int(np.argmax(matriz.max(axis=0)))

    todas_lats = [evento["lat"]] + [r["lat"] for r in resultados]
    todas_lons = [evento["lon"]] + [r["lon"] for r in resultados]

    limites = (
        min(todas_lons) - 0.35,
        max(todas_lons) + 0.35,
        min(todas_lats) - 0.30,
        max(todas_lats) + 0.30,
    )

    campo = CampoEspacial(validos, evento, limites)

    LOG.info(
        "Mapa: %d válidas, %d sin datos. Interpolación: %s",
        len(validos),
        len(invalidos),
        "activa" if campo.disponible else campo.motivo,
    )

    (carpeta / "procesamiento.json").write_text(
        json.dumps({
            "componente": "vertical",
            "unidad": "cm/s²",
            "resumen_temporal": "maximo absoluto por segundo",
            "normalizacion_grafico": "individual por estación",
            "radio_estaciones_km": RADIO_KM,
            "estaciones_candidatas": len(resultados),
            "estaciones_validas": len(validos),
            "interpolacion": "IDW",
            "interpolacion_disponible": campo.disponible,
            "motivo_sin_interpolacion": campo.motivo,
            "radio_interpolacion_km": HEATMAP_RADIO_KM,
            "minimo_vecinos": HEATMAP_MIN_VECINOS,
            "potencia_idw": HEATMAP_POTENCIA,
            "mascara": "envolvente convexa y cobertura mínima",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    geometria = None
    try:
        from cartopy.io import shapereader

        ruta = shapereader.natural_earth(
            resolution="110m",
            category="physical",
            name="coastline",
        )
        lector = shapereader.Reader(ruta)
        geometria = list(lector.geometries())
        lector.close()
    except Exception as exc:
        LOG.warning("Sin costa: %s", type(exc).__name__)

    plt.rcParams.update({
        "font.size": 10,
        "axes.titlesize": 12,
        "figure.facecolor": "#f4f7fb",
        "axes.facecolor": "white",
    })

    cantidad = len(resultados)

    # La altura aumenta con el número de estaciones.
    # Las imágenes PNG conservan todas las filas.
    alto = max(8.6, 2.8 + cantidad * 0.24)

    fig = plt.figure(figsize=(16, alto), dpi=100)
    ax_mapa = fig.add_axes([0.055, 0.17, 0.35, 0.64])
    ax_grafico = fig.add_axes([0.61, 0.15, 0.36, 0.67])

    fig.suptitle(
        f"Sismo M {evento['mag_texto']} · {evento['referencia']}",
        fontsize=16, fontweight="bold", y=0.975,
    )

    profundidad = (
        f"{evento['prof']:g} km"
        if evento["prof"] is not None
        else "no informada"
    )

    fig.text(
        0.5, 0.925,
        f"{hora_local(evento):%d/%m/%Y %H:%M:%S}  |  "
        f"Profundidad: {profundidad}  |  "
        f"Epicentro: {evento['lat']:.4f}, {evento['lon']:.4f}",
        ha="center", fontsize=10,
    )

    fig.text(
        0.5, 0.885,
        f"{cantidad} estaciones candidatas · "
        f"{len(validos)} con datos válidos · "
        f"{len(invalidos)} sin datos utilizables · "
        f"Radio {RADIO_KM:g} km",
        ha="center", fontsize=10,
    )

    lon_min, lon_max, lat_min, lat_max = limites
    ax_mapa.set_xlim(lon_min, lon_max)
    ax_mapa.set_ylim(lat_min, lat_max)
    ax_mapa.set_aspect(
        1 / max(0.2, math.cos(math.radians(evento["lat"])))
    )
    ax_mapa.set_xlabel("Longitud")
    ax_mapa.set_ylabel("Latitud")
    ax_mapa.grid(alpha=0.20, zorder=1)

    maximo = max(float(matriz.max()), 1e-12)
    norma = Normalize(vmin=0, vmax=maximo)

    cmap = plt.get_cmap("YlOrRd").copy()
    cmap.set_bad((1, 1, 1, 0))

    imagen = ax_mapa.imshow(
        campo.calcular(matriz[:, indice_resumen]),
        origin="lower",
        extent=limites,
        cmap=cmap,
        norm=norma,
        interpolation="nearest",
        alpha=0.80,
        zorder=2,
        aspect=ax_mapa.get_aspect(),
    )

    if geometria is not None:
        for geom in geometria:
            partes = geom.geoms if hasattr(geom, "geoms") else [geom]
            for parte in partes:
                x, y = parte.xy
                ax_mapa.plot(
                    x, y, color="#465569", linewidth=0.7, zorder=3
                )

    puntos = ax_mapa.scatter(
        [r["lon"] for r in validos],
        [r["lat"] for r in validos],
        c=matriz[:, indice_resumen],
        cmap=cmap,
        norm=norma,
        s=42,
        edgecolors="#202a35",
        linewidths=0.6,
        zorder=5,
        label="Con datos",
    )

    if invalidos:
        ax_mapa.scatter(
            [r["lon"] for r in invalidos],
            [r["lat"] for r in invalidos],
            marker="x", s=34, color="#697586",
            linewidths=1.1, zorder=5, label="Sin datos válidos",
        )

    ax_mapa.scatter(
        [evento["lon"]], [evento["lat"]],
        marker="*", s=230, color="#137bd0",
        edgecolors="white", linewidths=0.7,
        zorder=6, label="Epicentro",
    )

    # Usar números en el mapa; se corresponden con las filas.
    for i, r in enumerate(resultados, start=1):
        ax_mapa.annotate(
            str(i), (r["lon"], r["lat"]),
            xytext=(3, 3), textcoords="offset points",
            fontsize=6.5, zorder=7,
        )

    ax_mapa.legend(loc="best", fontsize=8)

    barra = fig.colorbar(
        imagen, ax=ax_mapa, fraction=0.043, pad=0.035
    )
    barra.set_label(
        "Máximo |aZ| por segundo [cm/s²]",
        fontsize=9,
    )

    if campo.disponible:
        titulo_mapa = "Campo espacial estimado · IDW"
    else:
        titulo_mapa = "Observaciones · sin cobertura para interpolar"

    if geometria is None:
        titulo_mapa += "\nCosta no disponible"

    ax_mapa.set_title(titulo_mapa, fontsize=11)

    if not campo.disponible:
        ax_mapa.text(
            0.5, 0.02,
            campo.motivo,
            transform=ax_mapa.transAxes,
            ha="center", va="bottom", fontsize=8,
            bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
            zorder=8,
        )

    # Una fila por estación. No hay superposición entre estaciones.
    etiquetas = []

    for i, r in enumerate(resultados):
        base = float(i)

        ax_grafico.axhline(
            base, color="#dfe5ed", linewidth=0.5, zorder=0
        )

        if r["valida"]:
            pico = max(float(np.max(r["serie"])), 1e-30)
            curva = r["serie"] / pico

            # Cada estación ocupa solo su propia fila.
            y = base - 0.72 * curva

            ax_grafico.fill_between(
                tiempos_centro, base, y,
                color="#2689b6", alpha=0.30,
            )
            ax_grafico.plot(
                tiempos_centro, y,
                color="#126186", linewidth=0.75,
            )

            etiquetas.append(
                f"{i + 1:02d}  {r['estacion_id']}  "
                f"[{r['canal']}]  "
                f"{r['pico_cm_s2']:.3g}"
            )
        else:
            ax_grafico.text(
                (POST_SEG - PRE_SEG) / 2,
                base - 0.18,
                "SIN DATOS VÁLIDOS",
                ha="center", va="center",
                fontsize=7, color="#858e9c",
            )
            etiquetas.append(
                f"{i + 1:02d}  {r['estacion_id']}  SIN DATOS"
            )

    ax_grafico.set_yticks(np.arange(cantidad))
    ax_grafico.set_yticklabels(etiquetas, fontsize=8)
    ax_grafico.set_ylim(cantidad - 0.35, -1.0)
    ax_grafico.set_xlim(-PRE_SEG, POST_SEG)
    ax_grafico.set_xlabel("Segundos respecto del origen")
    ax_grafico.set_title(
        "Todas las estaciones · amplitud normalizada por fila\n"
        "Etiqueta: estación / canal / pico filtrado [cm/s²]",
        fontsize=11,
    )
    ax_grafico.axvline(
        0, color="#177dc2", linestyle="--", linewidth=1
    )
    ax_grafico.grid(axis="x", alpha=0.20)

    for tick, r in zip(ax_grafico.get_yticklabels(), resultados):
        if not r["valida"]:
            tick.set_color("#858e9c")

    cursor = ax_grafico.axvline(
        tiempos_centro[indice_resumen],
        color="#c23b22", linewidth=1.3,
    )

    fig.text(
        0.5, 0.065,
        "CSN · Aceleración vertical filtrada · "
        "Mapa: escala física común · Gráfico: escala individual",
        ha="center", fontsize=9,
    )
    fig.text(
        0.5, 0.043,
        "Interpolación espacial estimada; no representa propagación "
        "de ondas. Zonas sin cobertura suficiente enmascaradas.",
        ha="center", fontsize=8,
    )

    etiqueta_tiempo = fig.text(
        0.5, 0.020, "", ha="center", fontsize=10
    )

    def actualizar_frame(indice):
        valores = matriz[:, indice]
        imagen.set_data(campo.calcular(valores))
        puntos.set_array(valores)

        segundo = segundos[indice]
        centro = segundo + 0.5
        cursor.set_xdata([centro, centro])

        etiqueta_tiempo.set_text(
            f"Intervalo {segundo:+.0f} a {segundo + 1:+.0f} s · "
            f"Reproducción {FPS:g}× · "
            f"Máximo observado: {valores.max():.4g} cm/s²"
        )

    actualizar_frame(indice_resumen)

    # Imágenes completas y paneles.
    fig.canvas.draw()
    fig.savefig(carpeta / "resumen.png", dpi=130)

    renderer = fig.canvas.get_renderer()

    caja_mapa = Bbox.union([
        ax_mapa.get_tightbbox(renderer),
        barra.ax.get_tightbbox(renderer),
    ]).transformed(
        fig.dpi_scale_trans.inverted()
    ).expanded(1.04, 1.04)

    caja_grafico = ax_grafico.get_tightbbox(
        renderer
    ).transformed(
        fig.dpi_scale_trans.inverted()
    ).expanded(1.03, 1.03)

    fig.savefig(
        carpeta / "mapa.png",
        dpi=150, bbox_inches=caja_mapa,
    )
    fig.savefig(
        carpeta / "grafico.png",
        dpi=150, bbox_inches=caja_grafico,
    )

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    matplotlib.rcParams["animation.ffmpeg_path"] = ffmpeg

    temporal = carpeta / "video_base.mp4"
    destino = carpeta / "video.mp4"

    # Mantener dimensiones del video acotadas aunque haya muchas filas.
    # Los PNG se guardaron arriba con su resolución completa.
    dpi_video = min(100.0, 1900.0 / max(16.0, alto))

    writer = FFMpegWriter(
        fps=FPS,
        codec="libx264",
        extra_args=[
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-pix_fmt", "yuv420p",
            "-crf", "20",
        ],
    )

    LOG.info(
        "Generando video con %d filas y %d cuadros",
        cantidad, len(segundos),
    )

    try:
        with writer.saving(fig, str(temporal), dpi=dpi_video):
            for indice in range(len(segundos)):
                actualizar_frame(indice)
                writer.grab_frame()
    finally:
        plt.close(fig)

    proceso = subprocess.run(
        [
            ffmpeg, "-y",
            "-i", str(temporal),
            "-vf", "fps=30,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-an",
            str(destino),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )

    if proceso.returncode != 0:
        raise RuntimeError("FFmpeg no pudo preparar el MP4")

    if not destino.is_file() or destino.stat().st_size == 0:
        raise RuntimeError("MP4 vacío")

    temporal.unlink(missing_ok=True)
    return destino


def video_del_evento(evento, carpeta):
    inventario, grupos = obtener_estaciones(evento)
    resultados = []

    with ThreadPoolExecutor(max_workers=TRABAJADORES) as executor:
        futuros = {
            executor.submit(
                procesar_estacion, grupo, inventario, evento
            ): grupo
            for grupo in grupos
        }

        for numero_finalizado, futuro in enumerate(
            as_completed(futuros), start=1
        ):
            resultado = futuro.result()
            resultados.append(resultado)

            LOG.info(
                "Estación %d/%d: %s · %s",
                numero_finalizado,
                len(grupos),
                resultado["estacion_id"],
                "válida" if resultado["valida"] else resultado["motivo"],
            )

    resultados.sort(key=lambda r: r["distancia_km"])

    # Guardar CSV incluso cuando ninguna estación resulte válida.
    guardar_resultados(evento, resultados, carpeta)

    return generar_video(evento, resultados, carpeta)


# =====================================================================
# X: AUTENTICACIÓN Y TEXTO
# =====================================================================

def crear_sesion_x():
    credenciales = {
        "X_API_KEY": X_API_KEY,
        "X_API_SECRET": X_API_SECRET,
        "X_ACCESS_TOKEN": X_ACCESS_TOKEN,
        "X_ACCESS_TOKEN_SECRET": X_ACCESS_TOKEN_SECRET,
    }

    faltantes = [
        nombre for nombre, valor in credenciales.items()
        if (
            not isinstance(valor, str)
            or not valor.strip()
            or valor.strip().startswith("PEGA_AQUI")
        )
    ]
    if faltantes:
        raise RuntimeError(
            "Completa las credenciales: " + ", ".join(faltantes)
        )

    sesion = requests.Session()
    sesion.auth = OAuth1(
        X_API_KEY.strip(),
        X_API_SECRET.strip(),
        X_ACCESS_TOKEN.strip(),
        X_ACCESS_TOKEN_SECRET.strip(),
    )
    return sesion


def respuesta_x(respuesta):
    if not respuesta.ok:
        raise ErrorX(respuesta.status_code)
    if not respuesta.content:
        return {}

    contenido = respuesta.json()
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
    local = hora_local(evento)
    profundidad = (
        f"{evento['prof']:g} km"
        if evento["prof"] is not None
        else "no informada"
    )
    referencia = " ".join(evento["referencia"].split())

    def construir(lugar):
        return (
            f"Sismo M {evento['mag_texto']} | {lugar}\n"
            f"{local:%d/%m/%Y %H:%M:%S}\n"
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


# =====================================================================
# X: CARGA Y PUBLICACIÓN
# =====================================================================

def subir_video_x(sesion, ruta):
    inicial = respuesta_x(
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
    media_id = str(inicial["id"])

    with ruta.open("rb") as archivo:
        segmento = 0
        while True:
            bloque = archivo.read(4 * 1024 * 1024)
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
            raise RuntimeError("Tiempo de procesamiento en X agotado")

        espera = max(1, int(info.get("check_after_secs", 5)))
        time.sleep(min(espera, restante))

        if time.monotonic() >= limite:
            raise RuntimeError("Tiempo de procesamiento en X agotado")

        datos = respuesta_x(
            sesion.get(
                API_X + "/media/upload",
                params={"command": "STATUS", "media_id": media_id},
                timeout=(15, 60),
            )
        )
        info = datos.get("processing_info")
        if not info:
            raise RuntimeError("X no devolvió estado de procesamiento")

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

    # Confirmar persistencia ANTES de enviar el tweet.
    estado.actualizar(
        clave,
        estado="enviando",
        texto=texto,
        media_id=media_id,
    )

    try:
        respuesta = sesion.post(
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
        raise RuntimeError(
            "Publicación incierta. Revisar X antes de reintentar."
        )

    if respuesta.status_code >= 500 or respuesta.status_code == 408:
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error=f"HTTP {respuesta.status_code} tras POST",
        )
        raise RuntimeError("Publicación incierta")

    if not respuesta.ok:
        estado.actualizar(
            clave,
            estado="pendiente",
            ultimo_error=f"X HTTP {respuesta.status_code}",
        )
        raise ErrorX(respuesta.status_code)

    try:
        tweet_id = str(respuesta_x(respuesta)["id"])
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
        "CSN -> HEATMAP -> VIDEO -> X | M >= %.1f | %d horas",
        MAG_MIN, HORAS_BUSQUEDA,
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
            LOG.error("Búsqueda CSN fallida: %s", exc)
            eventos = {}
            fallos += 1

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

        if cambios:
            estado.guardar()

        cola = []
        for clave, registro in registros.items():
            if registro.get("estado") != "pendiente":
                continue

            evento = registro["evento"]
            t = UTCDateTime(evento["t"])

            if inicio <= t <= fin and evento["mag"] >= MAG_MIN:
                cola.append((clave, registro))

        cola.sort(key=lambda item: item[1]["evento"]["t"])

        if not cola:
            LOG.info("Sin eventos nuevos o pendientes. Fin.")
            return 1 if fallos else 0

        listos = []
        for clave, registro in cola:
            disponible = (
                UTCDateTime(registro["evento"]["t"])
                + POST_SEG + MARGEN_SEG + LATENCIA_SEG
            )
            if disponible <= fin:
                listos.append((clave, registro))
            else:
                LOG.info(
                    "%s: esperando ventana posterior; "
                    "queda para la próxima ejecución.",
                    clave,
                )

        if not listos:
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
            raise ErrorEstado("Estado perteneciente a otra cuenta")

        if anterior is None:
            estado.datos["cuenta_id"] = cuenta_id
            estado.guardar()

        SALIDA.mkdir(parents=True, exist_ok=True)

        for posicion, (clave, registro) in enumerate(listos, 1):
            if time.monotonic() - inicio_ejecucion > PRESUPUESTO_SEG:
                LOG.info(
                    "Presupuesto alcanzado. Restantes pendientes."
                )
                break

            evento = registro["evento"]

            estado.actualizar(
                clave,
                estado="procesando",
                intentos=registro.get("intentos", 0) + 1,
            )

            LOG.info(
                "[%d/%d] %s · M %s · %s",
                posicion, len(listos), clave,
                evento["mag_texto"], evento["referencia"],
            )

            try:
                texto_publicacion(evento)
                carpeta = SALIDA / clave
                ruta = video_del_evento(evento, carpeta)

                publicar_evento(
                    estado, sesion_x, clave, evento, ruta
                )

            except ErrorEstado:
                raise

            except Exception as exc:
                fallos += 1
                LOG.error(
                    "Evento %s incompleto: %s: %s",
                    clave, type(exc).__name__, exc,
                )

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
                    LOG.error("Lote detenido por acceso o límites de X")
                    break

        for clave, _ in listos:
            LOG.info("%s: %s", clave, registros[clave]["estado"])

        LOG.info(
            "Fin del ciclo. La próxima ejecución depende de Actions."
        )
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
    except Exception as exc:
        LOG.error(
            "Ejecución detenida: %s: %s",
            type(exc).__name__, exc,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
