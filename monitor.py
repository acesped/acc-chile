#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CSN -> ACELERACIÓN VERTICAL -> MAPA / GRÁFICO / VIDEO -> X

Programa autónomo para GitHub Actions.
Una sola ejecución por invocación.

FUNCIONAMIENTO:
- Busca sismos M >= 4 en las últimas 12 horas.
- Procesa eventos nuevos y pendientes dentro de esa ventana.
- Descarga acelerogramas verticales HNZ / ENZ disponibles en CSN.
- Corrige la respuesta instrumental y trabaja en cm/s².
- Genera mapa.png, grafico.png, resumen.png, video.mp4 y CSV.
- Publica el video en X.
- Conserva el estado en la rama csn-state del repositorio.
- No depende de Colab ni de Google Drive.
- La programación cada 10 minutos pertenece al workflow YAML.

PROCESAMIENTO:
- Componentes verticales de acelerómetros.
- Detrend lineal.
- Corrección de respuesta instrumental a aceleración.
- Prefiltro dependiente de la frecuencia de muestreo.
- Resumen visual: máximo |aZ| por segundo.

Los valores mostrados son picos verticales del registro filtrado.
No representan PGA horizontal ni intensidad macrosísmica.

PROTECCIÓN CONTRA DUPLICADOS:
- Antes de enviar un tweet se persiste el estado "enviando".
- Si el envío queda sin confirmación, no se repite automáticamente.
- Requiere conservar el estado y concurrency en el workflow.

CREDENCIALES:
- Credenciales de X directamente en este archivo.
- GH_TOKEN se recibe del workflow para guardar el estado en GitHub.
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

# Ventana adicional para reducir efectos de borde del procesamiento.
MARGEN_SEG = 30

# Tiempo adicional para permitir que lleguen los datos al servidor.
LATENCIA_SEG = 120

RADIO_KM = 350.0
MAX_ESTACIONES = 12
TRABAJADORES = 4

# Componentes verticales de acelerómetros.
CANALES = "HNZ,ENZ"

# Cada cuadro representa un segundo de datos.
# 180 segundos de datos a 4 FPS producen un video de 45 segundos.
FPS = 4

PUBLICAR_EN_X = os.getenv(
    "PUBLISH_TO_X",
    "true",
).strip().lower() in {"true", "1", "yes"}

SALIDA = Path(
    os.getenv("CSN_OUTPUT", "output")
)

API_X = "https://api.x.com/2"

MAX_PROCESAMIENTO_X_SEG = 15 * 60

STATE_BRANCH = os.getenv(
    "STATE_BRANCH",
    "csn-state",
)

STATE_PATH = (
    "estado_publicaciones.json"
    if PUBLICAR_EN_X
    else "estado_simulaciones.json"
)

# No comenzar eventos adicionales después de este tiempo.
PRESUPUESTO_SEG = 35 * 60

TIMEOUT_HTTP = (15, 75)

LOG = logging.getLogger("csn-monitor")


# =====================================================================
# CREDENCIALES DE X DIRECTAMENTE EN EL CÓDIGO
# =====================================================================

X_API_KEY = "t5792SuVlfx41hDSWYmHVQJiG"
X_API_SECRET = "WCOUY5z1SqlylH1XYQM9P5guowMC3RogGWIF2hLvSFJKna3HVw"
X_ACCESS_TOKEN = "2106457141796052993-NpB8nf6yLTbPjJEu4TIHwJfbCCHU7h"
X_ACCESS_TOKEN_SECRET = "jCDFy4L4suq6Z6qnHOhJ4CuduqWs5173JgriRqn76L5MZ"


# =====================================================================
# EXCEPCIONES
# =====================================================================

class ErrorEstado(RuntimeError):
    pass


class ErrorX(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(
            f"X respondió HTTP {status}"
        )


class SinDatos(RuntimeError):
    pass


# =====================================================================
# UTILIDADES
# =====================================================================

def normalizar(texto):
    texto = unicodedata.normalize(
        "NFKD",
        str(texto),
    )

    return "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    ).strip().lower()


def numero(texto):
    coincidencia = re.search(
        r"[-+]?\d+(?:[.,]\d+)?",
        str(texto).replace("−", "-"),
    )

    if not coincidencia:
        raise ValueError(
            f"No se encontró un número en {texto!r}"
        )

    return float(
        coincidencia.group().replace(",", ".")
    )


def obtener(url, params=None, permitir_vacio=False):
    respuesta = requests.get(
        url,
        params=params,
        headers={
            "User-Agent": "CSN-Earthquake-Monitor/1.0"
        },
        timeout=TIMEOUT_HTTP,
    )

    if (
        permitir_vacio
        and respuesta.status_code in (204, 404)
    ):
        return b""

    respuesta.raise_for_status()

    return respuesta.content


def fecha_fdsn(t):
    return UTCDateTime(t).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )


def identificar_evento(url):
    partes = (
        urlparse(url)
        .path
        .strip("/")
        .split("/")
    )

    return "_".join(
        partes[-3:]
    ).replace(".html", "")


def hora_local(evento):
    return UTCDateTime(
        evento["t"]
    ).datetime.replace(
        tzinfo=timezone.utc
    ).astimezone(
        ZoneInfo("America/Santiago")
    )


# =====================================================================
# LECTURA DE INFORMES CSN
# =====================================================================

def leer_evento(url):
    soup = BeautifulSoup(
        obtener(url),
        "html.parser",
    )

    campos = {}

    for fila in soup.select("tr"):
        celdas = fila.find_all(["td", "th"])

        if len(celdas) >= 2:
            etiqueta = normalizar(
                celdas[0].get_text(" ", strip=True)
            ).rstrip(":")

            valor = " ".join(
                celda.get_text(" ", strip=True)
                for celda in celdas[1:]
            )

            campos[etiqueta] = valor

    def campo(nombre, obligatorio=True):
        nombre = normalizar(nombre)

        for etiqueta, valor in campos.items():
            if (
                etiqueta == nombre
                or etiqueta.startswith(nombre)
            ):
                return valor

        if obligatorio:
            raise ValueError(
                "Informe CSN sin campo reconocible: "
                + nombre
            )

        return None

    texto_hora = campo("hora utc")
    fecha = None

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
        coincidencia = re.search(
            patron,
            texto_hora,
        )

        if coincidencia:
            fecha = datetime.strptime(
                coincidencia.group(),
                formato,
            ).replace(
                tzinfo=timezone.utc
            )
            break

    if fecha is None:
        raise ValueError(
            "No se pudo interpretar la hora UTC de CSN"
        )

    latitud = numero(campo("latitud"))
    longitud = numero(campo("longitud"))

    mag_texto = campo("magnitud")
    magnitud = numero(mag_texto)

    if not (
        -90 <= latitud <= 90
        and -180 <= longitud <= 180
    ):
        raise ValueError(
            "Coordenadas inválidas"
        )

    if not 0 <= magnitud <= 10:
        raise ValueError(
            "Magnitud inválida"
        )

    prof_texto = campo(
        "profundidad",
        obligatorio=False,
    )

    try:
        profundidad = numero(prof_texto)
    except (ValueError, TypeError):
        profundidad = None

    return {
        "t": str(UTCDateTime(fecha)),
        "lat": latitud,
        "lon": longitud,
        "mag": magnitud,
        "mag_texto": mag_texto.strip(),
        "prof": profundidad,
        "referencia": campo("referencia"),
        "url": url,
    }


# =====================================================================
# BÚSQUEDA ÚNICA EN CATÁLOGOS CSN
# =====================================================================

def enlaces_m4(contenido, pagina):
    soup = BeautifulSoup(
        contenido,
        "html.parser",
    )

    encontrados = set()
    filas_catalogo = 0

    for fila in soup.select("tr"):
        enlace = fila.find(
            "a",
            href=re.compile(r"/informes/"),
        )

        celdas = fila.find_all("td")

        if enlace is None or not celdas:
            continue

        filas_catalogo += 1

        try:
            magnitud = numero(
                celdas[-1].get_text(" ", strip=True)
            )
        except ValueError:
            # Leer el informe si no se reconoce la magnitud.
            magnitud = MAG_MIN

        if magnitud >= MAG_MIN:
            url = urljoin(
                pagina,
                enlace["href"],
            )

            host = urlparse(url).hostname or ""

            if host in {
                "sismologia.cl",
                "www.sismologia.cl",
            }:
                encontrados.add(url)

    return encontrados, filas_catalogo


def buscar_eventos_una_vez(inicio, fin):
    paginas = [CSN + "/"]
    fechas = set()

    # Cubrir fechas UTC y locales en cruces de medianoche.
    for zona in (
        timezone.utc,
        ZoneInfo("America/Santiago"),
    ):
        primero = inicio.datetime.replace(
            tzinfo=timezone.utc
        ).astimezone(zona).date()

        ultimo = fin.datetime.replace(
            tzinfo=timezone.utc
        ).astimezone(zona).date()

        while primero <= ultimo:
            fechas.add(primero)
            primero += timedelta(days=1)

    for dia in sorted(fechas):
        paginas.append(
            f"{CSN}/sismicidad/catalogo/"
            f"{dia:%Y/%m/%Y%m%d}.html"
        )

    enlaces = set()
    filas_totales = 0
    problemas = 0

    for pagina in dict.fromkeys(paginas):
        try:
            contenido = obtener(
                pagina,
                permitir_vacio=True,
            )

            if not contenido:
                LOG.warning(
                    "Catálogo no disponible: %s",
                    pagina,
                )
                continue

            encontrados, filas = enlaces_m4(
                contenido,
                pagina,
            )

            enlaces.update(encontrados)
            filas_totales += filas

        except Exception as exc:
            problemas += 1

            LOG.warning(
                "Fallo de catálogo: %s (%s)",
                pagina,
                type(exc).__name__,
            )

    if filas_totales == 0:
        raise RuntimeError(
            "No se pudo reconocer ningún catálogo de CSN. "
            "No se interpreta como ausencia de sismos."
        )

    eventos = {}

    for url in sorted(enlaces):
        try:
            evento = leer_evento(url)
            tiempo = UTCDateTime(evento["t"])

            if (
                evento["mag"] >= MAG_MIN
                and inicio <= tiempo <= fin
            ):
                eventos[
                    identificar_evento(url)
                ] = evento

        except Exception as exc:
            problemas += 1

            LOG.warning(
                "No se pudo leer %s (%s)",
                url,
                type(exc).__name__,
            )

    LOG.info(
        "Sismos dentro de la ventana: %d; "
        "problemas de consulta: %d",
        len(eventos),
        problemas,
    )

    return eventos, problemas


# =====================================================================
# ESTADO PERSISTENTE EN GITHUB
# =====================================================================

class EstadoGitHub:
    """
    Guarda el estado mediante la API Contents de GitHub.

    El SHA evita sobreescrituras silenciosas.
    Si falla el guardado, el programa se detiene.

    La rama de estado se crea desde la rama predeterminada
    cuando todavía no existe.
    """

    def __init__(self):
        self.repo = os.environ.get(
            "GITHUB_REPOSITORY",
            "",
        )

        token = os.environ.get(
            "GH_TOKEN",
            "",
        )

        self.api = os.environ.get(
            "GITHUB_API_URL",
            "https://api.github.com",
        ).rstrip("/")

        if not self.repo or not token:
            raise ErrorEstado(
                "Faltan GITHUB_REPOSITORY o GH_TOKEN. "
                "Ejecuta con el workflow de GitHub Actions."
            )

        self.sesion = requests.Session()

        self.sesion.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

        self.base = (
            f"{self.api}/repos/{self.repo}"
        )

        self.sha = None

        self.datos = {
            "cuenta_id": None,
            "eventos": {},
        }

        self.crear_rama_si_falta()
        self.cargar()

    def comprobar(self, respuesta):
        if not respuesta.ok:
            raise ErrorEstado(
                f"GitHub respondió HTTP {respuesta.status_code}. "
                "Verifica contents: write y permisos de la rama "
                f"{STATE_BRANCH}."
            )

        return respuesta.json()

    def crear_rama_si_falta(self):
        rama = quote(
            STATE_BRANCH,
            safe="",
        )

        respuesta = self.sesion.get(
            f"{self.base}/git/ref/heads/{rama}",
            timeout=TIMEOUT_HTTP,
        )

        if respuesta.status_code != 404:
            self.comprobar(respuesta)
            return

        repo = self.comprobar(
            self.sesion.get(
                self.base,
                timeout=TIMEOUT_HTTP,
            )
        )

        predeterminada = quote(
            repo["default_branch"],
            safe="",
        )

        referencia = self.comprobar(
            self.sesion.get(
                f"{self.base}/git/ref/heads/{predeterminada}",
                timeout=TIMEOUT_HTTP,
            )
        )

        self.comprobar(
            self.sesion.post(
                f"{self.base}/git/refs",
                json={
                    "ref": f"refs/heads/{STATE_BRANCH}",
                    "sha": referencia["object"]["sha"],
                },
                timeout=TIMEOUT_HTTP,
            )
        )

    def cargar(self):
        respuesta = self.sesion.get(
            f"{self.base}/contents/{STATE_PATH}",
            params={
                "ref": STATE_BRANCH,
            },
            timeout=TIMEOUT_HTTP,
        )

        if respuesta.status_code == 404:
            return

        archivo = self.comprobar(respuesta)

        self.sha = archivo["sha"]

        self.datos = json.loads(
            base64.b64decode(
                archivo["content"]
            ).decode("utf-8")
        )

        if not isinstance(
            self.datos.get("eventos"),
            dict,
        ):
            raise ErrorEstado(
                "El estado remoto tiene formato inválido"
            )

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
            "content": base64.b64encode(
                contenido
            ).decode("ascii"),
        }

        if self.sha:
            payload["sha"] = self.sha

        try:
            respuesta = self.sesion.put(
                f"{self.base}/contents/{STATE_PATH}",
                json=payload,
                timeout=TIMEOUT_HTTP,
            )

            datos = self.comprobar(respuesta)
            self.sha = datos["content"]["sha"]

        except ErrorEstado:
            raise

        except Exception as exc:
            raise ErrorEstado(
                "No se confirmó el guardado del estado en GitHub"
            ) from exc

    def actualizar(self, clave, **cambios):
        self.datos["eventos"][clave].update(cambios)
        self.guardar()

    def cerrar(self):
        self.sesion.close()


# =====================================================================
# INVENTARIO DE ACELERÓMETROS
# =====================================================================

def obtener_estaciones(evento):
    tiempo = UTCDateTime(evento["t"])

    inicio = tiempo - PRE_SEG - MARGEN_SEG
    fin = tiempo + POST_SEG + MARGEN_SEG

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
        raise SinDatos(
            "CSN no devolvió inventario de acelerómetros"
        )

    inventario = read_inventory(
        io.BytesIO(contenido)
    )

    estaciones = {}

    for red in inventario:
        for estacion in red:
            for canal in estacion:
                if canal.code not in {"HNZ", "ENZ"}:
                    continue

                if (
                    canal.start_date
                    and canal.start_date > inicio
                ):
                    continue

                if (
                    canal.end_date
                    and canal.end_date < fin
                ):
                    continue

                latitud = float(canal.latitude)
                longitud = float(canal.longitude)

                distancia = gps2dist_azimuth(
                    evento["lat"],
                    evento["lon"],
                    latitud,
                    longitud,
                )[0] / 1000.0

                if distancia > RADIO_KM:
                    continue

                if canal.response is None:
                    continue

                sensibilidad = (
                    canal.response.instrument_sensitivity
                )

                if sensibilidad is None:
                    continue

                unidades = str(
                    sensibilidad.input_units
                ).upper().replace(" ", "")

                if unidades not in {
                    "M/S**2",
                    "M/S^2",
                    "M/S/S",
                    "M/S2",
                }:
                    continue

                clave = (
                    f"{red.code}.{estacion.code}"
                )

                opcion = {
                    "red": red.code,
                    "estacion": estacion.code,
                    "loc": canal.location_code or "",
                    "canal": canal.code,
                    "lat": latitud,
                    "lon": longitud,
                    "distancia_km": distancia,
                    "id": (
                        f"{red.code}.{estacion.code}."
                        f"{canal.location_code or ''}."
                        f"{canal.code}"
                    ),
                }

                estaciones.setdefault(
                    clave,
                    [],
                ).append(opcion)

    grupos = sorted(
        estaciones.values(),
        key=lambda opciones: opciones[0]["distancia_km"],
    )[:MAX_ESTACIONES]

    if not grupos:
        raise SinDatos(
            "No hay canales verticales con respuesta válida "
            f"dentro de {RADIO_KM:g} km"
        )

    return inventario, grupos


# =====================================================================
# DESCARGA Y PROCESAMIENTO DE ESTACIONES
# =====================================================================

def procesar_estacion(opciones, inventario, evento):
    tiempo = UTCDateTime(evento["t"])

    inicio = tiempo - PRE_SEG - MARGEN_SEG
    fin = tiempo + POST_SEG + MARGEN_SEG

    opciones = sorted(
        opciones,
        key=lambda opcion: (
            opcion["canal"] != "HNZ",
            opcion["loc"],
        ),
    )

    for opcion in opciones:
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
                continue

            stream = read(
                io.BytesIO(contenido),
                format="MSEED",
            )

            stream = stream.select(
                network=opcion["red"],
                station=opcion["estacion"],
                location=opcion["loc"],
                channel=opcion["canal"],
            )

            if not stream:
                continue

            stream.sort()

            stream.merge(
                method=0,
                fill_value=None,
            )

            if len(stream) != 1:
                continue

            traza = stream[0]

            # Descartar huecos; no rellenarlos con señales artificiales.
            if np.ma.isMaskedArray(traza.data):
                if np.ma.getmaskarray(traza.data).any():
                    continue

                traza.data = np.asarray(traza.data)

            fs = float(
                traza.stats.sampling_rate
            )

            if fs < 10:
                continue

            tolerancia = 1.5 / fs

            if (
                traza.stats.starttime > inicio + tolerancia
                or traza.stats.endtime < fin - tolerancia
            ):
                continue

            traza.data = np.asarray(
                traza.data,
                dtype=np.float64,
            )

            if not np.isfinite(traza.data).all():
                continue

            if np.ptp(traza.data) == 0:
                continue

            traza.detrend("linear")

            nyquist = fs / 2.0
            f3 = min(20.0, nyquist * 0.70)
            f4 = min(25.0, nyquist * 0.90)

            traza.remove_response(
                inventory=inventario,
                output="ACC",
                pre_filt=(0.05, 0.10, f3, f4),
                water_level=None,
                zero_mean=True,
                taper=True,
                taper_fraction=0.05,
            )

            traza.trim(
                tiempo - PRE_SEG,
                tiempo + POST_SEG,
            )

            # ObsPy devuelve m/s²; convertir a cm/s².
            aceleracion = np.asarray(
                traza.data,
                dtype=float,
            ) * 100.0

            tiempos = (
                traza.times()
                + float(traza.stats.starttime - tiempo)
            )

            if (
                len(aceleracion) == 0
                or not np.isfinite(aceleracion).all()
            ):
                continue

            bordes = np.arange(
                -PRE_SEG,
                POST_SEG + 1,
                dtype=float,
            )

            serie = np.full(
                len(bordes) - 1,
                np.nan,
            )

            for indice, (a, b) in enumerate(
                zip(bordes[:-1], bordes[1:])
            ):
                seleccion = (
                    (tiempos >= a)
                    & (tiempos < b)
                )

                if seleccion.any():
                    serie[indice] = np.max(
                        np.abs(aceleracion[seleccion])
                    )

            if not np.isfinite(serie).all():
                continue

            resultado = dict(opcion)

            resultado.update({
                "serie": serie,
                "pico_cm_s2": float(
                    np.max(np.abs(aceleracion))
                ),
                "fs": fs,
                "f3": f3,
                "f4": f4,
            })

            return resultado

        except Exception as exc:
            LOG.warning(
                "Canal %s descartado (%s)",
                opcion["id"],
                type(exc).__name__,
            )

    return None


# =====================================================================
# MAPA, GRÁFICO, CSV Y VIDEO
# =====================================================================

def generar_video(evento, resultados, carpeta):
    carpeta.mkdir(
        parents=True,
        exist_ok=True,
    )

    campos = [
        "id",
        "lat",
        "lon",
        "distancia_km",
        "pico_cm_s2",
        "fs",
        "f3",
        "f4",
    ]

    with (carpeta / "estaciones.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as archivo:
        escritor = csv.DictWriter(
            archivo,
            fieldnames=campos,
        )

        escritor.writeheader()

        escritor.writerows(
            {
                campo: resultado[campo]
                for campo in campos
            }
            for resultado in resultados
        )

    segundos = np.arange(
        -PRE_SEG,
        POST_SEG,
        dtype=float,
    )

    matriz = np.array([
        resultado["serie"]
        for resultado in resultados
    ])

    with (
        carpeta / "aceleracion_por_segundo.csv"
    ).open(
        "w",
        newline="",
        encoding="utf-8",
    ) as archivo:
        escritor = csv.writer(archivo)

        escritor.writerow(
            ["segundos_desde_origen"]
            + [
                resultado["id"]
                for resultado in resultados
            ]
        )

        for indice, segundo in enumerate(segundos):
            escritor.writerow(
                [segundo]
                + matriz[:, indice].tolist()
            )

    (carpeta / "evento.json").write_text(
        json.dumps(
            evento,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    geometria = None

    try:
        from cartopy.io import shapereader

        ruta_costa = shapereader.natural_earth(
            resolution="110m",
            category="physical",
            name="coastline",
        )

        lector = shapereader.Reader(ruta_costa)
        geometria = list(lector.geometries())
        lector.close()

    except Exception as exc:
        LOG.warning(
            "Costa no disponible; mapa con coordenadas (%s)",
            type(exc).__name__,
        )

    plt.rcParams.update({
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "figure.facecolor": "#f5f7fb",
        "axes.facecolor": "white",
    })

    figura = plt.figure(
        figsize=(12.8, 7.2),
        dpi=100,
    )

    ax_mapa = figura.add_axes([
        0.07, 0.17, 0.34, 0.65
    ])

    ax_grafico = figura.add_axes([
        0.49, 0.17, 0.46, 0.65
    ])

    titulo = (
        f"Sismo M {evento['mag_texto']} · "
        f"{evento['referencia']}"
    )

    figura.suptitle(
        titulo,
        fontsize=15,
        fontweight="bold",
        y=0.97,
    )

    profundidad = (
        f"{evento['prof']:g} km"
        if evento["prof"] is not None
        else "no informada"
    )

    figura.text(
        0.5,
        0.90,
        f"{hora_local(evento):%d/%m/%Y %H:%M:%S}  |  "
        f"Profundidad: {profundidad}  |  "
        f"Epicentro: {evento['lat']:.4f}, "
        f"{evento['lon']:.4f}",
        ha="center",
        fontsize=10,
    )

    latitudes = np.array([
        resultado["lat"]
        for resultado in resultados
    ])

    longitudes = np.array([
        resultado["lon"]
        for resultado in resultados
    ])

    ax_mapa.set_xlim(
        min(longitudes.min(), evento["lon"]) - 0.6,
        max(longitudes.max(), evento["lon"]) + 0.6,
    )

    ax_mapa.set_ylim(
        min(latitudes.min(), evento["lat"]) - 0.5,
        max(latitudes.max(), evento["lat"]) + 0.5,
    )

    if geometria is not None:
        for elemento in geometria:
            partes = (
                elemento.geoms
                if hasattr(elemento, "geoms")
                else [elemento]
            )

            for parte in partes:
                x, y = parte.xy

                ax_mapa.plot(
                    x,
                    y,
                    color="#697586",
                    linewidth=0.8,
                    zorder=1,
                )

    ax_mapa.set_aspect(
        1 / max(
            0.2,
            math.cos(
                math.radians(evento["lat"])
            ),
        )
    )

    ax_mapa.grid(alpha=0.25)
    ax_mapa.set_xlabel("Longitud")
    ax_mapa.set_ylabel("Latitud")

    ax_mapa.set_title(
        "Estaciones observadas"
        if geometria is not None
        else "Estaciones · mapa sin costa"
    )

    maximo = max(
        float(matriz.max()),
        1e-9,
    )

    norma = Normalize(
        vmin=0,
        vmax=maximo,
    )

    puntos = ax_mapa.scatter(
        longitudes,
        latitudes,
        c=matriz.max(axis=1),
        cmap="YlOrRd",
        norm=norma,
        s=85,
        edgecolors="#333333",
        linewidths=0.6,
        zorder=3,
    )

    ax_mapa.scatter(
        [evento["lon"]],
        [evento["lat"]],
        marker="*",
        s=240,
        color="#1478ce",
        edgecolors="white",
        zorder=4,
        label="Epicentro",
    )

    ax_mapa.legend(
        loc="best",
        fontsize=8,
    )

    for resultado in resultados:
        ax_mapa.annotate(
            resultado["estacion"],
            (
                resultado["lon"],
                resultado["lat"],
            ),
            xytext=(4, 4),
            textcoords="offset points",
            fontsize=7,
        )

    barra = figura.colorbar(
        puntos,
        ax=ax_mapa,
        fraction=0.045,
        pad=0.04,
    )

    barra.set_label(
        "|aZ| filtrada [cm/s²]",
        fontsize=9,
    )

    for resultado in resultados:
        ax_grafico.plot(
            segundos + 0.5,
            resultado["serie"],
            linewidth=0.9,
            label=resultado["id"],
        )

    ax_grafico.axvline(
        0,
        color="#1478ce",
        linestyle="--",
        linewidth=1,
    )

    ax_grafico.set_xlim(
        -PRE_SEG,
        POST_SEG,
    )

    ax_grafico.set_ylim(
        0,
        maximo * 1.10,
    )

    ax_grafico.set_xlabel(
        "Segundos respecto del origen"
    )

    ax_grafico.set_ylabel(
        "Máximo |aZ| por segundo [cm/s²]"
    )

    ax_grafico.set_title(
        "Aceleración vertical filtrada"
    )

    ax_grafico.grid(alpha=0.2)

    ax_grafico.legend(
        fontsize=7,
        ncol=2,
        loc="upper right",
    )

    figura.text(
        0.5,
        0.065,
        "Datos: CSN · Respuesta instrumental corregida · "
        "Componente vertical, sin interpolación espacial",
        ha="center",
        fontsize=9,
    )

    etiqueta = figura.text(
        0.5,
        0.025,
        "Resumen de la ventana completa",
        ha="center",
        fontsize=10,
    )

    figura.canvas.draw()

    figura.savefig(
        carpeta / "resumen.png",
        dpi=150,
    )

    renderer = figura.canvas.get_renderer()

    caja_mapa = ax_mapa.get_tightbbox(renderer)
    caja_color = barra.ax.get_tightbbox(renderer)

    caja_mapa = Bbox.union([
        caja_mapa,
        caja_color,
    ])

    caja_mapa = caja_mapa.transformed(
        figura.dpi_scale_trans.inverted()
    ).expanded(
        1.04,
        1.04,
    )

    caja_grafico = ax_grafico.get_tightbbox(
        renderer
    ).transformed(
        figura.dpi_scale_trans.inverted()
    ).expanded(
        1.04,
        1.04,
    )

    figura.savefig(
        carpeta / "mapa.png",
        dpi=150,
        bbox_inches=caja_mapa,
    )

    figura.savefig(
        carpeta / "grafico.png",
        dpi=150,
        bbox_inches=caja_grafico,
    )

    cursor = ax_grafico.axvline(
        -PRE_SEG,
        color="#111111",
        linewidth=1.5,
    )

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    matplotlib.rcParams[
        "animation.ffmpeg_path"
    ] = ffmpeg

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

    try:
        with writer.saving(
            figura,
            str(temporal),
            dpi=100,
        ):
            for indice, segundo in enumerate(segundos):
                puntos.set_array(
                    matriz[:, indice]
                )

                cursor.set_xdata([
                    segundo + 0.5,
                    segundo + 0.5,
                ])

                etiqueta.set_text(
                    f"Intervalo: {segundo:+.0f} a "
                    f"{segundo + 1:+.0f} s · "
                    f"Reproducción {FPS:g}×"
                )

                writer.grab_frame()

    finally:
        plt.close(figura)

    proceso = subprocess.run(
        [
            ffmpeg,
            "-y",
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
        timeout=300,
    )

    if proceso.returncode != 0:
        raise RuntimeError(
            "FFmpeg no pudo preparar el video"
        )

    if (
        not destino.is_file()
        or destino.stat().st_size == 0
    ):
        raise RuntimeError(
            "El video generado está vacío"
        )

    temporal.unlink(missing_ok=True)

    return destino


def video_del_evento(evento, carpeta):
    inventario, grupos = obtener_estaciones(evento)
    resultados = []

    with ThreadPoolExecutor(
        max_workers=TRABAJADORES
    ) as executor:
        futuros = [
            executor.submit(
                procesar_estacion,
                grupo,
                inventario,
                evento,
            )
            for grupo in grupos
        ]

        for futuro in as_completed(futuros):
            resultado = futuro.result()

            if resultado is not None:
                resultados.append(resultado)

    resultados.sort(
        key=lambda resultado: resultado["distancia_km"]
    )

    LOG.info(
        "Estaciones válidas: %d de %d",
        len(resultados),
        len(grupos),
    )

    if not resultados:
        raise SinDatos(
            "No hay acelerogramas completos y calibrables. "
            "Se reintentará en otra ejecución mientras el evento "
            "permanezca dentro de la ventana de búsqueda."
        )

    return generar_video(
        evento,
        resultados,
        carpeta,
    )


# =====================================================================
# AUTENTICACIÓN EN X CON CREDENCIALES DIRECTAS
# =====================================================================

def crear_sesion_x():
    credenciales = {
        "X_API_KEY": X_API_KEY,
        "X_API_SECRET": X_API_SECRET,
        "X_ACCESS_TOKEN": X_ACCESS_TOKEN,
        "X_ACCESS_TOKEN_SECRET": X_ACCESS_TOKEN_SECRET,
    }

    faltantes = [
        nombre
        for nombre, valor in credenciales.items()
        if (
            not isinstance(valor, str)
            or not valor.strip()
            or valor.strip().startswith("PEGA_AQUI")
        )
    ]

    if faltantes:
        raise RuntimeError(
            "Completa las credenciales directamente en monitor.py: "
            + ", ".join(faltantes)
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

    if (
        not isinstance(contenido, dict)
        or contenido.get("errors")
    ):
        raise RuntimeError(
            "Respuesta de X no válida"
        )

    datos = contenido.get("data", {})

    if not isinstance(datos, dict):
        raise RuntimeError(
            "Datos de X no válidos"
        )

    return datos


# =====================================================================
# TEXTO DEL TWEET
# =====================================================================

def peso_texto(texto):
    # Cota conservadora para este texto:
    # enlaces t.co = 23 caracteres.
    texto = re.sub(
        r"https?://\S+",
        "u" * 23,
        texto,
    )

    return sum(
        1 if ord(caracter) <= 0x10FF else 2
        for caracter in texto
    )


def texto_publicacion(evento):
    local = hora_local(evento)

    profundidad = (
        f"{evento['prof']:g} km"
        if evento["prof"] is not None
        else "no informada"
    )

    referencia = " ".join(
        evento["referencia"].split()
    )

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

        texto = construir(
            referencia.rstrip() + "…"
        )

    if peso_texto(texto) > 280:
        raise RuntimeError(
            "El texto del tweet es demasiado largo"
        )

    return texto


# =====================================================================
# SUBIDA DEL VIDEO A X
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
            bloque = archivo.read(
                4 * 1024 * 1024
            )

            if not bloque:
                break

            respuesta_x(
                sesion.post(
                    API_X + f"/media/upload/{media_id}/append",
                    data={
                        "segment_index": str(segmento),
                    },
                    files={
                        "media": (
                            "segmento.mp4",
                            bloque,
                            "video/mp4",
                        )
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

    limite = (
        time.monotonic()
        + MAX_PROCESAMIENTO_X_SEG
    )

    while info:
        estado = info.get("state")

        if estado == "succeeded":
            return media_id

        if estado == "failed":
            raise RuntimeError(
                "X rechazó el procesamiento del video"
            )

        if estado not in {
            "pending",
            "in_progress",
        }:
            raise RuntimeError(
                "Estado de procesamiento de X desconocido"
            )

        restante = limite - time.monotonic()

        if restante <= 0:
            raise RuntimeError(
                "Tiempo de procesamiento en X agotado"
            )

        espera = max(
            1,
            int(info.get("check_after_secs", 5)),
        )

        time.sleep(
            min(espera, restante)
        )

        datos = respuesta_x(
            sesion.get(
                API_X + "/media/upload",
                params={
                    "command": "STATUS",
                    "media_id": media_id,
                },
                timeout=(15, 60),
            )
        )

        info = datos.get("processing_info")

        if not info:
            raise RuntimeError(
                "X no devolvió estado de procesamiento"
            )

    return media_id


# =====================================================================
# PUBLICACIÓN Y PROTECCIÓN CONTRA DUPLICADOS
# =====================================================================

def publicar_evento(
    estado,
    sesion,
    clave,
    evento,
    ruta,
):
    texto = texto_publicacion(evento)

    LOG.info(
        "Texto de publicación:\n%s",
        texto,
    )

    if not PUBLICAR_EN_X:
        estado.actualizar(
            clave,
            estado="simulado",
            texto=texto,
            ultimo_error=None,
        )
        return

    media_id = subir_video_x(
        sesion,
        ruta,
    )

    # Debe quedar guardado en GitHub antes del POST a X.
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
                "media": {
                    "media_ids": [media_id],
                },
            },
            timeout=(15, 90),
        )

    except requests.RequestException:
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error=(
                "No se recibió confirmación del POST"
            ),
        )

        raise RuntimeError(
            "Publicación incierta: revisar X antes de reintentar"
        )

    if (
        respuesta.status_code >= 500
        or respuesta.status_code == 408
    ):
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error=(
                f"HTTP {respuesta.status_code} después del POST"
            ),
        )

        raise RuntimeError(
            "Publicación incierta por respuesta de X"
        )

    if not respuesta.ok:
        estado.actualizar(
            clave,
            estado="pendiente",
            ultimo_error=(
                f"X HTTP {respuesta.status_code}"
            ),
        )

        raise ErrorX(
            respuesta.status_code
        )

    try:
        tweet_id = str(
            respuesta_x(respuesta)["id"]
        )

    except Exception:
        estado.actualizar(
            clave,
            estado="resultado_incierto",
            ultimo_error=(
                "Respuesta del POST no reconocida"
            ),
        )

        raise RuntimeError(
            "No se pudo confirmar el ID del tweet"
        )

    estado.actualizar(
        clave,
        estado="publicado",
        tweet_id=tweet_id,
        publicado=time.time(),
        ultimo_error=None,
    )

    LOG.info(
        "Publicado: https://x.com/i/status/%s",
        tweet_id,
    )


# =====================================================================
# EJECUCIÓN ÚNICA
# =====================================================================

def ejecutar_una_vez():
    inicio_ejecucion = time.monotonic()

    fin = UTCDateTime()
    inicio = fin - HORAS_BUSQUEDA * 3600

    LOG.info(
        "CSN -> VIDEO -> X | M >= %.1f | últimas %d horas",
        MAG_MIN,
        HORAS_BUSQUEDA,
    )

    LOG.info(
        "Desde %s hasta %s UTC",
        inicio,
        fin,
    )

    estado = EstadoGitHub()
    sesion_x = None
    fallos = 0

    try:
        # Recuperar pendientes incluso si el catálogo actual
        # deja de enumerar un evento registrado previamente.
        try:
            eventos, problemas = buscar_eventos_una_vez(
                inicio,
                fin,
            )

            if problemas:
                fallos += 1

        except Exception as exc:
            LOG.error(
                "Falló la búsqueda CSN: %s",
                exc,
            )

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
                # Actualizar datos revisados por CSN antes de publicar.
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
            tiempo = UTCDateTime(evento["t"])

            if (
                inicio <= tiempo <= fin
                and evento["mag"] >= MAG_MIN
            ):
                cola.append(
                    (clave, registro)
                )

        cola.sort(
            key=lambda item: item[1]["evento"]["t"]
        )

        if not cola:
            LOG.info(
                "No hay eventos nuevos o pendientes procesables. Fin."
            )

            return 1 if fallos else 0

        # No mantener el runner esperando un evento recién ocurrido.
        # Queda pendiente hasta la siguiente invocación del workflow.
        listos = []

        for clave, registro in cola:
            disponible = (
                UTCDateTime(registro["evento"]["t"])
                + POST_SEG
                + MARGEN_SEG
                + LATENCIA_SEG
            )

            if disponible <= fin:
                listos.append(
                    (clave, registro)
                )
            else:
                LOG.info(
                    "%s: ventana posterior aún incompleta; "
                    "queda para otra ejecución.",
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

        anterior = estado.datos.get(
            "cuenta_id"
        )

        if (
            anterior is not None
            and anterior != cuenta_id
        ):
            raise ErrorEstado(
                "El estado pertenece a otra cuenta de X"
            )

        if anterior is None:
            estado.datos["cuenta_id"] = cuenta_id
            estado.guardar()

        SALIDA.mkdir(
            parents=True,
            exist_ok=True,
        )

        for posicion, (clave, registro) in enumerate(
            listos,
            start=1,
        ):
            if (
                time.monotonic() - inicio_ejecucion
                > PRESUPUESTO_SEG
            ):
                LOG.info(
                    "Presupuesto de ejecución alcanzado. "
                    "Los eventos restantes quedan pendientes."
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
                posicion,
                len(listos),
                clave,
                evento["mag_texto"],
                evento["referencia"],
            )

            try:
                texto_publicacion(evento)

                carpeta = SALIDA / clave

                ruta = video_del_evento(
                    evento,
                    carpeta,
                )

                publicar_evento(
                    estado,
                    sesion_x,
                    clave,
                    evento,
                    ruta,
                )

            except ErrorEstado:
                # No seguir publicando si falla la persistencia.
                raise

            except Exception as exc:
                fallos += 1

                LOG.error(
                    "No se completó %s: %s: %s",
                    clave,
                    type(exc).__name__,
                    exc,
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

                if (
                    isinstance(exc, ErrorX)
                    and exc.status in {
                        401,
                        402,
                        403,
                        429,
                    }
                ):
                    LOG.error(
                        "Se detiene el lote por acceso o límites de X"
                    )
                    break

        for clave, _ in listos:
            LOG.info(
                "%s: %s",
                clave,
                registros[clave]["estado"],
            )

        LOG.info(
            "Ejecución finalizada. "
            "La siguiente consulta depende del workflow de Actions."
        )

        return 1 if fallos else 0

    finally:
        if sesion_x is not None:
            sesion_x.close()

        estado.cerrar()


# =====================================================================
# PUNTO DE ENTRADA
# =====================================================================

def main():
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | %(message)s"
        ),
        stream=sys.stdout,
    )

    logging.getLogger(
        "matplotlib"
    ).setLevel(logging.WARNING)

    try:
        return ejecutar_una_vez()

    except KeyboardInterrupt:
        LOG.warning(
            "Ejecución interrumpida"
        )
        return 130

    except Exception as exc:
        LOG.error(
            "Ejecución detenida: %s: %s",
            type(exc).__name__,
            exc,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
