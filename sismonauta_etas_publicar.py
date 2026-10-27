
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA ETAS CHILE
=====================

Publicador automático del modelo ETAS original.

MODELO REQUERIDO:
    etas_multipaís_24h_animado.py

WORKFLOW:
    .github/workflows/sismonauta_etas.yml

ACTIVACION:
    cron-job.org
        |
        v
    GitHub workflow_dispatch
        |
        v
    sismonauta_etas_publicar.py
        |
        v
    ETAS CHILE
        |
        v
    25 mapas + video
        |
        +--> Instagram @sismonauta
        |
        +--> Fanpage Sismonauta

ARCHIVOS GENERADOS:
    output_etas/
        ETAS_DOBLE_HEATMAP_CON_LIKE.mp4
        sismonauta_etas_reel_9x16.mp4
        estado_publicacion.json
        _etas_temporal.py

CARACTERISTICAS:
    - Pais fijo: Chile.
    - No requiere input interactivo.
    - Sin /mnt/data.
    - Sin sudo ni permisos de administrador.
    - Salida dentro del workspace.
    - Proteccion contra duplicados.
    - Registro de publicaciones inciertas.
    - Compatible con GitHub Actions.

NOTA:
    El modelo ETAS es experimental.
    No esta calibrado ni validado
    prospectivamente.

    No constituye pronostico oficial
    ni alerta sismica.
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests


# ============================================================
# 1. CONFIGURACION GENERAL
# ============================================================

COUNTRY = "Chile"

ACCOUNT = "sismonauta"

PAGE_NAME = "Sismonauta"

GITHUB_API = "https://api.github.com"

META_VERSION = os.getenv(
    "META_GRAPH_VERSION",
    "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = (
    f"https://graph.facebook.com/{META_VERSION}"
)

# Tiempo normal para solicitudes JSON.
HTTP_TIMEOUT = (20, 180)

# Subidas de videos pueden tardar mas.
UPLOAD_TIMEOUT = (30, 900)

# Tiempo maximo del modelo ETAS.
ETAS_TIMEOUT_SECONDS = int(
    os.getenv(
        "ETAS_TIMEOUT_SECONDS",
        "16500"
    )
)

# Directorio local escribible.
DEFAULT_OUTPUT_DIR = os.getenv(
    "ETAS_OUTPUT_DIR",
    "output_etas"
)

# Nombre original del MP4 cientifico.
ORIGINAL_VIDEO_NAME = (
    "ETAS_DOBLE_HEATMAP_CON_LIKE.mp4"
)

# Nombre de la version Instagram.
REEL_VIDEO_NAME = (
    "sismonauta_etas_reel_9x16.mp4"
)

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Sismonauta-ETAS-Chile/2.0"
    )
})


# ============================================================
# 2. UTILIDADES
# ============================================================

def utcnow():
    return datetime.now(
        timezone.utc
    ).isoformat()


def log(*args):
    print(
        *args,
        flush=True
    )


def safe_error(exc):
    return str(exc)[:500]


def ensure_directory(path):
    path = Path(path).resolve()

    path.mkdir(
        parents=True,
        exist_ok=True
    )

    # Verificar permisos del usuario del runner.
    test = path / ".etas_write_test"

    try:
        test.write_text(
            "OK",
            encoding="utf-8"
        )

        if test.read_text(
            encoding="utf-8"
        ) != "OK":
            raise RuntimeError(
                "No se pudo verificar escritura"
            )

    finally:
        test.unlink(
            missing_ok=True
        )

    return path


def ensure_binary(name):
    import shutil

    executable = shutil.which(name)

    if not executable:
        raise RuntimeError(
            f"No se encontro {name} en PATH. "
            "Revisar instalacion en YAML."
        )

    return executable


def request_json(
    method,
    url,
    *,
    token=None,
    timeout=None,
    **kwargs
):
    """
    Solicitud HTTP JSON.

    Evita imprimir tokens y utiliza
    tiempos de espera definidos.
    """

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
        timeout=timeout or HTTP_TIMEOUT,
        **kwargs
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not response.ok:
        problem = None

        if isinstance(payload, dict):
            error = payload.get("error")

            if isinstance(error, dict):
                problem = error.get("message")

        raise RuntimeError(
            f"HTTP {response.status_code} "
            f"en {url.split('?')[0]}: "
            f"{str(problem or response.reason)[:220]}"
        )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "La API no devolvio un objeto JSON"
        )

    return payload


# ============================================================
# 3. GITHUB API
# ============================================================

def github_request(
    method,
    suffix,
    **kwargs
):
    token = os.environ[
        "GH_TOKEN"
    ]

    headers = dict(
        kwargs.pop("headers", {})
    )

    headers.update({
        "Accept": (
            "application/vnd.github+json"
        ),
        "X-GitHub-Api-Version": (
            "2022-11-28"
        )
    })

    return request_json(
        method,
        GITHUB_API + suffix,
        token=token,
        headers=headers,
        **kwargs
    )


def github_repository():
    repo = os.environ.get(
        "GITHUB_REPOSITORY",
        ""
    ).strip()

    if "/" not in repo:
        raise RuntimeError(
            "GITHUB_REPOSITORY no configurado"
        )

    return repo


def github_branch():
    return os.getenv(
        "GITHUB_REF_NAME",
        "main"
    )


def check_public_repository():
    repo = github_repository()

    info = github_request(
        "GET",
        f"/repos/{repo}"
    )

    if info.get("private"):
        raise RuntimeError(
            "El repositorio debe ser publico "
            "para alojar el Reel mediante "
            "GitHub Releases."
        )

    log(
        "Repositorio GitHub publico verificado"
    )


# ============================================================
# 4. REGISTRO LOCAL
# ============================================================

def save_receipt(
    out,
    receipt
):
    out = ensure_directory(out)

    filepath = (
        out / "estado_publicacion.json"
    )

    temporary = filepath.with_suffix(
        ".json.tmp"
    )

    temporary.write_text(
        json.dumps(
            receipt,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temporary.replace(
        filepath
    )


# ============================================================
# 5. ESTADO PERSISTENTE EN GITHUB
# ============================================================

def state_read(path):
    repo = github_repository()

    try:
        result = github_request(
            "GET",
            (
                f"/repos/{repo}/contents/"
                f"{quote(path, safe='/')}"
            ),
            params={
                "ref": github_branch()
            }
        )

    except RuntimeError as exc:
        if "HTTP 404 " in str(exc):
            return {}, None

        raise

    content = base64.b64decode(
        result["content"]
    ).decode("utf-8")

    return (
        json.loads(content),
        result["sha"]
    )


def state_save(
    path,
    data
):
    repo = github_repository()

    encoded = base64.b64encode(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8")
    ).decode("ascii")

    for attempt in range(4):
        _, sha = state_read(path)

        payload = {
            "message": (
                "SISMONAUTA ETAS: "
                "actualizar estado Chile"
            ),
            "content": encoded,
            "branch": github_branch()
        }

        if sha:
            payload["sha"] = sha

        try:
            github_request(
                "PUT",
                (
                    f"/repos/{repo}/contents/"
                    f"{quote(path, safe='/')}"
                ),
                json=payload
            )

            return

        except RuntimeError:
            if attempt == 3:
                raise

            time.sleep(
                2 * (attempt + 1)
            )


# ============================================================
# 6. VERIFICAR CUENTAS META
# ============================================================

def check_accounts(publish):
    if not publish:
        log(
            "Vista previa: no se verifican "
            "tokens de publicacion."
        )
        return

    required = [
        "INSTAGRAM_TOKEN",
        "INSTAGRAM_USER_ID",
        "FACEBOOK_PAGE_ID",
        "FACEBOOK_PAGE_TOKEN",
        "GH_TOKEN",
        "GITHUB_REPOSITORY"
    ]

    missing = [
        key
        for key in required
        if not os.getenv(key)
    ]

    if missing:
        raise RuntimeError(
            "Faltan secretos: "
            + ", ".join(missing)
        )

    # ----------------------------------------
    # INSTAGRAM
    # ----------------------------------------

    ig = request_json(
        "GET",
        (
            f"{IG_BASE}/{META_VERSION}/"
            f"{os.environ['INSTAGRAM_USER_ID']}"
        ),
        token=os.environ[
            "INSTAGRAM_TOKEN"
        ],
        params={
            "fields": "id,username"
        }
    )

    username = str(
        ig.get("username", "")
    ).lower().lstrip("@")

    expected_id = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    if (
        username != ACCOUNT
        or str(ig.get("id")) != expected_id
    ):
        raise RuntimeError(
            "Instagram no corresponde "
            "a @sismonauta"
        )

    log(
        "INSTAGRAM VERIFICADO:",
        "@sismonauta"
    )

    # ----------------------------------------
    # FACEBOOK
    # ----------------------------------------

    fb = request_json(
        "GET",
        (
            f"{FB_BASE}/"
            f"{os.environ['FACEBOOK_PAGE_ID']}"
        ),
        token=os.environ[
            "FACEBOOK_PAGE_TOKEN"
        ],
        params={
            "fields": "id,name"
        }
    )

    expected_page = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    if (
        str(fb.get("id")) != expected_page
        or "sismonauta" not in str(
            fb.get("name", "")
        ).lower()
    ):
        raise RuntimeError(
            "La Fanpage no corresponde "
            "a Sismonauta"
        )

    log(
        "FACEBOOK VERIFICADO:",
        fb.get("name")
    )

    check_public_repository()


# ============================================================
# 7. ADAPTAR MODELO ETAS ORIGINAL
# ============================================================

def adapt_original_source(
    source,
    video_path
):
    """
    Cambia exclusivamente la asignacion
    OUTPUT_MP4 mediante el AST de Python.

    Ventajas:
    - Acepta asignaciones multilinea.
    - Conserva la indentacion.
    - No necesita modificar el original.
    - Se detiene si encuentra ambiguedad.

    Tambien corrige la doble multiplicacion
    por 100 en la locucion, si existe.
    """

    old_expression = (
        "float(porcentaje_original) * 100.0"
    )

    if old_expression in source:
        source = source.replace(
            old_expression,
            "float(porcentaje_original)"
        )

        log(
            "Correccion de porcentaje "
            "de narracion aplicada."
        )

    tree = ast.parse(source)

    assignments = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name)
                and target.id == "OUTPUT_MP4"
                for target in node.targets
            ):
                assignments.append(node)

        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "OUTPUT_MP4"
            ):
                assignments.append(node)

    if len(assignments) != 1:
        raise RuntimeError(
            "Se esperaba una unica asignacion "
            "a OUTPUT_MP4 en el modelo ETAS. "
            f"Se encontraron {len(assignments)}. "
            "Revisar el modelo antes de ejecutarlo."
        )

    node = assignments[0]

    lines = source.splitlines(
        keepends=True
    )

    # No modificar codigo que comparte linea
    # con otras instrucciones.
    first_line = lines[
        node.lineno - 1
    ]

    prefix = first_line[
        :node.col_offset
    ]

    if prefix.strip():
        raise RuntimeError(
            "Asignacion OUTPUT_MP4 "
            "no aislada en una linea."
        )

    # AnnAssign admite anotacion de tipo.
    # Sustituimos la instruccion completa
    # por una asignacion normal.
    new_statement = (
        prefix
        + "OUTPUT_MP4 = "
        + repr(str(video_path))
        + "\n"
    )

    start = node.lineno - 1
    end = node.end_lineno

    lines[start:end] = [
        new_statement
    ]

    modified = "".join(lines)

    # Validar sintaxis antes de ejecutar.
    compile(
        modified,
        "_etas_temporal.py",
        "exec"
    )

    return modified


# ============================================================
# 8. EJECUTAR ETAS ORIGINAL PARA CHILE
# ============================================================

def run_original(
    original,
    out
):
    """
    Ejecuta el modelo ETAS original.

    No utiliza /mnt/data.

    Produce:
        output_etas/
        ETAS_DOBLE_HEATMAP_CON_LIKE.mp4
    """

    out = ensure_directory(out)

    original = Path(
        original
    ).resolve()

    if not original.is_file():
        raise RuntimeError(
            "No existe el modelo original: "
            + str(original)
        )

    # Archivo final en el workspace.
    video = (
        out / ORIGINAL_VIDEO_NAME
    )

    # Evitar confundir videos antiguos
    # con resultados de una ejecucion nueva.
    video.unlink(
        missing_ok=True
    )

    source = original.read_text(
        encoding="utf-8"
    )

    # Adaptar la copia temporal.
    modified = adapt_original_source(
        source,
        video
    )

    temporary_script = (
        out / "_etas_temporal.py"
    )

    temporary_script.write_text(
        modified,
        encoding="utf-8"
    )

    env = os.environ.copy()

    env["PYTHONUNBUFFERED"] = "1"
    env["MPLBACKEND"] = "Agg"

    log(
        "==================================="
    )

    log(
        "SISMONAUTA ETAS CHILE"
    )

    log(
        "==================================="
    )

    log(
        "Pais fijo:",
        COUNTRY
    )

    log(
        "Modelo:",
        original.name
    )

    log(
        "Salida:",
        video
    )

    log(
        "25 mapas horarios"
    )

    log(
        "Dos mapas de calor"
    )

    log(
        "Narracion y musica originales"
    )

    log(
        "==================================="
    )

    # El programa original pide el pais
    # mediante input().
    #
    # Se envia Chile automaticamente.

    try:
        process = subprocess.run(
            [
                sys.executable,
                "-u",
                str(temporary_script)
            ],
            input="Chile\n",
            text=True,
            env=env,
            cwd=str(
                Path.cwd()
            ),
            timeout=ETAS_TIMEOUT_SECONDS,
            check=False
        )

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "El calculo ETAS supero el "
            "tiempo maximo permitido."
        ) from exc

    if process.returncode != 0:
        raise RuntimeError(
            "El modelo ETAS termino con "
            f"codigo {process.returncode}"
        )

    if not video.is_file():
        raise RuntimeError(
            "El modelo no genero el MP4 "
            "esperado: "
            + str(video)
        )

    if video.stat().st_size < 10000:
        raise RuntimeError(
            "El video ETAS esta vacio "
            "o incompleto."
        )

    log(
        "VIDEO ETAS GENERADO:",
        video
    )

    log(
        "Tamano en bytes:",
        video.stat().st_size
    )

    return video


# ============================================================
# 9. VALIDAR VIDEO CON FFPROBE
# ============================================================

def video_probe(video):
    """
    Comprueba que el MP4 tenga
    duracion y video detectables.
    """

    ensure_binary(
        "ffprobe"
    )

    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size:"
                "stream=codec_type,"
                "width,height,codec_name"
            ),
            "-of",
            "json",
            str(video)
        ],
        capture_output=True,
        text=True,
        check=False
    )

    if process.returncode != 0:
        raise RuntimeError(
            "FFprobe no pudo leer "
            f"{video}: "
            + process.stderr[-300:]
        )

    info = json.loads(
        process.stdout
    )

    streams = info.get(
        "streams", []
    )

    video_streams = [
        stream
        for stream in streams
        if stream.get("codec_type") == "video"
    ]

    if not video_streams:
        raise RuntimeError(
            "El archivo MP4 no contiene "
            "una pista de video."
        )

    duration = float(
        info.get(
            "format", {}
        ).get(
            "duration", 0
        )
    )

    if duration <= 0:
        raise RuntimeError(
            "Duracion del video invalida."
        )

    return info


# ============================================================
# 10. CREAR REEL INSTAGRAM 9:16
# ============================================================

def reel_version(
    original,
    destination
):
    """
    Crea un MP4 1080 x 1920.

    El mapa original se conserva completo.
    No se cortan los heatmaps.

    Se utilizan bandas de fondo oscuro
    cuando cambia la relacion de aspecto.
    """

    ensure_binary(
        "ffmpeg"
    )

    destination = Path(
        destination
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    destination.unlink(
        missing_ok=True
    )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",

        "-i",
        str(original),

        "-vf",
        (
            "scale=1080:1920:"
            "force_original_aspect_ratio=decrease,"
            "pad=1080:1920:"
            "(ow-iw)/2:(oh-ih)/2:"
            "color=0x071727,"
            "setsar=1"
        ),

        "-r",
        "24",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "22",

        "-pix_fmt",
        "yuv420p",

        "-c:a",
        "aac",

        "-ar",
        "48000",

        "-b:a",
        "128k",

        "-movflags",
        "+faststart",

        str(destination)
    ]

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False
    )

    if process.returncode != 0:
        raise RuntimeError(
            "FFmpeg no pudo crear el Reel: "
            + process.stderr[-900:]
        )

    if (
        not destination.is_file()
        or destination.stat().st_size < 10000
    ):
        raise RuntimeError(
            "Reel vertical vacio o incompleto."
        )

    info = video_probe(
        destination
    )

    videos = [
        stream
        for stream in info["streams"]
        if stream.get("codec_type") == "video"
    ]

    stream = videos[0]

    if (
        stream.get("width") != 1080
        or stream.get("height") != 1920
    ):
        raise RuntimeError(
            "Resolucion del Reel incorrecta."
        )

    log(
        "REEL VERTICAL CREADO:",
        destination
    )

    return destination


# ============================================================
# 11. PUBLICAR REEL EN GITHUB RELEASES
# ============================================================

def create_release_asset(
    path,
    unique_tag
):
    """
    Alojamiento publico para que Meta
    pueda descargar el Reel mediante URL.

    Los assets de Releases permanecen
    publicos hasta su eliminacion.
    """

    repo = github_repository()

    release = github_request(
        "POST",
        f"/repos/{repo}/releases",
        json={
            "tag_name": unique_tag,
            "target_commitish": github_branch(),
            "name": (
                "SISMONAUTA ETAS Chile - "
                + unique_tag
            ),
            "draft": False,
            "prerelease": True,
            "generate_release_notes": False,
            "body": (
                "Modelo ETAS experimental. "
                "No constituye alerta sismica."
            )
        }
    )

    release_id = str(
        release["id"]
    )

    upload_url = (
        release["upload_url"].split("{")[0]
        + "?name="
        + quote(path.name)
    )

    try:
        with path.open("rb") as stream:
            asset = request_json(
                "POST",
                upload_url,
                token=os.environ["GH_TOKEN"],
                timeout=UPLOAD_TIMEOUT,
                headers={
                    "Accept": (
                        "application/vnd.github+json"
                    ),
                    "Content-Type": "video/mp4"
                },
                data=stream
            )

    except Exception as exc:
        raise RuntimeError(
            "Error alojando Reel en "
            f"GitHub Release {release_id}: "
            + safe_error(exc)
        ) from exc

    if not asset.get(
        "browser_download_url"
    ):
        raise RuntimeError(
            "GitHub no devolvio URL publica "
            "para el video."
        )

    public_url = asset[
        "browser_download_url"
    ]

    log(
        "GITHUB RELEASE CREADO:",
        release_id
    )

    return (
        public_url,
        release_id
    )


# ============================================================
# 12. INSTAGRAM REEL
# ============================================================

def instagram_reel(
    video,
    caption,
    result,
    persist
):
    """
    Publica un Reel en @sismonauta.

    Antes de las operaciones con efecto
    externo, registra estado incierto.

    No debe reintentarse automaticamente
    si Meta no confirma el resultado.
    """

    token = os.environ[
        "INSTAGRAM_TOKEN"
    ]

    user_id = os.environ[
        "INSTAGRAM_USER_ID"
    ]

    graph = (
        f"{IG_BASE}/{META_VERSION}"
    )

    result["status"] = "uncertain"

    persist()

    data = {
        "media_type": "REELS",
        "caption": caption[:2200],
        "share_to_feed": "true"
    }

    mode = os.getenv(
        "IG_UPLOAD_MODE",
        "url"
    ).lower()

    if mode not in (
        "url",
        "resumable"
    ):
        raise RuntimeError(
            "IG_UPLOAD_MODE invalido: " + mode
        )

    # ----------------------------------------
    # OPCION A: CARGA REANUDABLE
    # ----------------------------------------

    if mode == "resumable":
        data["upload_type"] = (
            "resumable"
        )

        container = request_json(
            "POST",
            f"{graph}/{user_id}/media",
            token=token,
            data=data
        )

        container_id = str(
            container["id"]
        )

        result["container_id"] = (
            container_id
        )

        persist()

        upload_url = (
            container.get("uri")
            or (
                "https://rupload.facebook.com/"
                "ig-api-upload/"
                f"{META_VERSION}/{container_id}"
            )
        )

        if not str(upload_url).startswith(
            "https://rupload.facebook.com/"
        ):
            raise RuntimeError(
                "Instagram devolvio una URL "
                "de carga inesperada."
            )

        with video.open("rb") as stream:
            request_json(
                "POST",
                upload_url,
                timeout=UPLOAD_TIMEOUT,
                headers={
                    "Authorization": (
                        "OAuth " + token
                    ),
                    "offset": "0",
                    "file_size": str(
                        video.stat().st_size
                    ),
                    "Content-Type": (
                        "application/octet-stream"
                    )
                },
                data=stream
            )

    # ----------------------------------------
    # OPCION B: URL PUBLICA
    # ----------------------------------------

    else:
        unique_tag = (
            "sismonauta-etas-"
            + datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%d-%H%M%S"
            )
            + "-"
            + os.getenv(
                "GITHUB_RUN_ID",
                str(int(time.time()))
            )
        )

        public_url, release_id = (
            create_release_asset(
                video,
                unique_tag
            )
        )

        result["release_id"] = (
            release_id
        )

        result["video_url"] = (
            public_url
        )

        persist()

        data["video_url"] = (
            public_url
        )

        container = request_json(
            "POST",
            f"{graph}/{user_id}/media",
            token=token,
            data=data
        )

        container_id = str(
            container["id"]
        )

        result["container_id"] = (
            container_id
        )

        persist()

    # ----------------------------------------
    # ESPERAR PROCESAMIENTO META
    # ----------------------------------------

    for attempt in range(30):
        status = request_json(
            "GET",
            f"{graph}/{container_id}",
            token=token,
            params={
                "fields": (
                    "status_code,status"
                )
            }
        )

        state = status.get(
            "status_code"
        )

        log(
            "INSTAGRAM:",
            "procesamiento",
            state,
            f"intento {attempt+1}/30"
        )

        if state == "FINISHED":
            break

        if state in (
            "ERROR",
            "EXPIRED"
        ):
            raise RuntimeError(
                "Instagram no proceso el Reel: "
                + str(
                    status.get("status")
                )[:200]
            )

        time.sleep(20)

    else:
        raise RuntimeError(
            "Instagram no termino de procesar "
            "el Reel en el tiempo esperado. "
            "Revisar estado manualmente."
        )

    # ----------------------------------------
    # PUBLICACION FINAL
    # ----------------------------------------

    published = request_json(
        "POST",
        (
            f"{graph}/{user_id}/"
            "media_publish"
        ),
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
            "Instagram no devolvio un "
            "identificador de publicacion valido."
        )

    result.update({
        "status": "published",
        "id": media_id
    })

    persist()

    log(
        "INSTAGRAM REEL ACEPTADO:",
        media_id
    )


# ============================================================
# 13. FACEBOOK VIDEO ORIGINAL
# ============================================================

def facebook_video(
    video,
    caption,
    result,
    persist
):
    """
    Publica video nativo en la Fanpage.

    Utiliza el video original, sin
    cambiar su relacion de aspecto.

    Un ID devuelto significa aceptacion
    de Meta, no garantiza procesamiento
    ni visibilidad final inmediata.
    """

    page_id = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    token = os.environ[
        "FACEBOOK_PAGE_TOKEN"
    ]

    result["status"] = "uncertain"

    persist()

    url = (
        f"{FB_BASE}/{page_id}/videos"
    )

    with video.open("rb") as stream:
        posted = request_json(
            "POST",
            url,
            token=token,
            timeout=UPLOAD_TIMEOUT,
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
        posted.get("id") or ""
    )

    if not media_id.isdigit():
        raise RuntimeError(
            "Facebook no devolvio "
            "un identificador de video valido."
        )

    result.update({
        "status": "published",
        "id": media_id
    })

    persist()

    log(
        "FACEBOOK VIDEO ACEPTADO:",
        media_id
    )


# ============================================================
# 14. TEXTO CIENTIFICO DE PUBLICACION
# ============================================================

def build_caption():
    return (
        "🌎 SISMONAUTA | "
        "Evolucion ETAS experimental en Chile\n\n"

        "25 instantaneas horarias basadas "
        "en datos de actividad sismica.\n\n"

        "Cada mapa presenta estimaciones "
        "del modelo ETAS para las 24 horas "
        "posteriores a esa instantanea.\n\n"

        "El video compara valores modelados "
        "y sus cambios espaciales.\n\n"

        "⚠️ ATENCION: son resultados "
        "experimentales, no calibrados ni "
        "validados prospectivamente. "
        "No representan probabilidades "
        "sismicas oficiales, no predicen "
        "un terremoto especifico y "
        "NO CONSTITUYEN ALERTA SISMICA.\n\n"

        "Consulta los organismos oficiales "
        "de sismologia y proteccion civil.\n\n"

        "Datos sismicos: USGS. "
        "Cartografia: Natural Earth.\n\n"

        "#Sismonauta #Chile #Sismologia "
        "#Terremotos #ETAS #Ciencia "
        "#Geologia #PlacasTectonicas"
    )


# ============================================================
# 15. PROGRAMA PRINCIPAL
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "SISMONAUTA ETAS Chile"
        )
    )

    parser.add_argument(
        "--original",
        default=(
            "etas_multipaís_24h_animado.py"
        )
    )

    parser.add_argument(
        "--out",
        default=DEFAULT_OUTPUT_DIR
    )

    parser.add_argument(
        "--publish",
        action="store_true"
    )

    args = parser.parse_args()

    # ----------------------------------------
    # DIRECTORIO DE SALIDA
    # ----------------------------------------

    out = ensure_directory(
        args.out
    )

    original = Path(
        args.original
    ).resolve()

    # Verificar dependencias externas antes
    # de iniciar un calculo potencialmente largo.
    ensure_binary(
        "ffmpeg"
    )

    ensure_binary(
        "ffprobe"
    )

    receipt = {
        "status": "starting",
        "country": COUNTRY,
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

    # Historial persistente de Chile.
    state_path = (
        "sismonauta_etas/"
        "state_"
        + hashlib.sha256(
            COUNTRY.casefold().encode()
        ).hexdigest()[:12]
        + ".json"
    )

    # ----------------------------------------
    # PERSISTENCIA LOCAL Y REMOTA
    # ----------------------------------------

    def persist():
        # Registrar localmente primero.
        save_receipt(
            out,
            receipt
        )

        # Solo publicar cambios remotos
        # cuando el workflow esta autorizado.
        if args.publish:
            state_save(
                state_path,
                receipt
            )

    try:
        # ------------------------------------
        # VALIDAR MODELO
        # ------------------------------------

        if not original.is_file():
            raise RuntimeError(
                "Falta el archivo ETAS original: "
                + str(original)
            )

        # ------------------------------------
        # VALIDAR DESTINOS
        # ------------------------------------

        check_accounts(
            args.publish
        )

        # ------------------------------------
        # CONTROL ANTIDUPLICADOS
        # ------------------------------------

        if args.publish:
            old, _ = state_read(
                state_path
            )

            today = receipt[
                "created_at"
            ][:10]

            old_date = str(
                old.get("created_at", "")
            )[:10]

            old_status = old.get(
                "status"
            )

            if (
                old_date == today
                and old_status in (
                    "published",
                    "partial_or_uncertain",
                    "publishing"
                )
            ):
                # No generar un nuevo video
                # ni repetir solicitudes a Meta.
                receipt["status"] = (
                    "duplicate_blocked"
                )

                receipt["error"] = (
                    "Ya existe una publicacion "
                    "o un envio no conciliado "
                    "para Chile en esta fecha UTC."
                )

                receipt["previous_status"] = (
                    old_status
                )

                save_receipt(
                    out,
                    receipt
                )

                raise RuntimeError(
                    "Publicacion duplicada o incierta. "
                    "Revisar historial antes "
                    "de volver a publicar."
                )

        # ------------------------------------
        # ETAPA DE GENERACION
        # ------------------------------------

        receipt["status"] = "generating"

        persist()

        log(
            "==================================="
        )

        log(
            "INICIANDO ETAS CHILE"
        )

        log(
            "Publicar:",
            args.publish
        )

        log(
            "Directorio:",
            out
        )

        log(
            "==================================="
        )

        video = run_original(
            original,
            out
        )

        # ------------------------------------
        # VALIDAR MP4 ORIGINAL
        # ------------------------------------

        info = video_probe(
            video
        )

        duration = float(
            info["format"]["duration"]
        )

        if (
            duration < 3
            or duration > 15 * 60
        ):
            raise RuntimeError(
                "Duracion incompatible "
                "con Instagram Reel: "
                f"{duration:.1f} segundos"
            )

        receipt["duration_seconds"] = (
            duration
        )

        receipt["video_file"] = (
            str(video)
        )

        log(
            "VIDEO ORIGINAL:",
            video
        )

        log(
            "DURACION:",
            round(duration, 2),
            "segundos"
        )

        # ------------------------------------
        # CREAR REEL VERTICAL
        # ------------------------------------

        reel = reel_version(
            video,
            out / REEL_VIDEO_NAME
        )

        receipt["reel_file"] = (
            str(reel)
        )

        # ------------------------------------
        # VISTA PREVIA
        # ------------------------------------

        if not args.publish:
            receipt["status"] = (
                "preview_only"
            )

            persist()

            log(
                "==================================="
            )

            log(
                "SISMONAUTA ETAS CHILE"
            )

            log(
                "VIDEO GENERADO SIN PUBLICAR"
            )

            log(
                "Original:",
                video
            )

            log(
                "Reel:",
                reel
            )

            log(
                "==================================="
            )

            return

        # ------------------------------------
        # PUBLICACION EN META
        # ------------------------------------

        receipt["status"] = "publishing"

        persist()

        caption = build_caption()

        errors = []

        targets = [
            (
                "instagram",
                instagram_reel,
                reel
            ),
            (
                "facebook",
                facebook_video,
                video
            )
        ]

        for site, action, file in targets:
            target = receipt[
                "targets"
            ][site]

            try:
                action(
                    file,
                    caption,
                    target,
                    persist
                )

                log(
                    "PUBLICACION ACEPTADA:",
                    site,
                    target.get("id")
                )

            except Exception as exc:
                errors.append(
                    f"{site}: {safe_error(exc)}"
                )

                target["error"] = (
                    safe_error(exc)
                )

                # Mantener estado incierto si
                # ya se realizo una solicitud
                # potencialmente publicada.
                if target.get("status") != "published":
                    target["status"] = (
                        "uncertain"
                    )

                persist()

        # ------------------------------------
        # CONTROL DE ERRORES
        # ------------------------------------

        if errors:
            receipt["status"] = (
                "partial_or_uncertain"
            )

            persist()

            raise RuntimeError(
                " / ".join(errors)
            )

        # ------------------------------------
        # EXITO
        # ------------------------------------

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
            receipt["targets"][
                "instagram"
            ].get("id")
        )

        log(
            "FACEBOOK:",
            receipt["targets"][
                "facebook"
            ].get("id")
        )

        log(
            "==================================="
        )

    except Exception as exc:
        # ------------------------------------
        # ESTADO DE FALLO
        # ------------------------------------

        if receipt["status"] not in (
            "partial_or_uncertain",
            "published",
            "duplicate_blocked"
        ):
            uncertain = any(
                item.get("status") in (
                    "uncertain",
                    "published"
                )
                for item in receipt[
                    "targets"
                ].values()
            )

            receipt["status"] = (
                "partial_or_uncertain"
                if uncertain
                else "error"
            )

        receipt["error"] = (
            safe_error(exc)
        )

        save_receipt(
            out,
            receipt
        )

        log(
            "ERROR:",
            safe_error(exc)
        )

        raise


# ============================================================
# INICIO
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
