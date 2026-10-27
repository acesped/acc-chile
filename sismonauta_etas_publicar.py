
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SISMONAUTA ETAS CHILE

Ejecuta el modelo ETAS original sin interacción y
publica el video en Instagram y Facebook.

Requiere en el repositorio:
    etas_multipaís_24h_animado.py

Activación automática:
    cron-job.org -> GitHub workflow_dispatch

El país está fijado exclusivamente en Chile.

El modelo ETAS es experimental, no calibrado ni
validado prospectivamente. No constituye alerta sísmica.
"""

import argparse
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
# CONFIGURACION GENERAL
# ============================================================

COUNTRY = "Chile"

GITHUB_API = "https://api.github.com"

META_VERSION = os.getenv(
    "META_GRAPH_VERSION", "v25.0"
)

IG_BASE = os.getenv(
    "IG_GRAPH_BASE",
    "https://graph.instagram.com"
).rstrip("/")

FB_BASE = (
    f"https://graph.facebook.com/{META_VERSION}"
)

TIMEOUT = (20, 180)

SESSION = requests.Session()

SESSION.headers["User-Agent"] = (
    "Sismonauta-ETAS-Publisher/1.0"
)


# ============================================================
# 1. SOLICITUDES HTTP
# ============================================================

def request_json(method, url, *, token=None, **kwargs):

    headers = dict(kwargs.pop("headers", {}))

    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = SESSION.request(
        method,
        url,
        headers=headers,
        timeout=TIMEOUT,
        **kwargs
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not response.ok:

        problem = None

        if isinstance(payload, dict):
            problem = (
                payload.get("error") or {}
            ).get("message")

        raise RuntimeError(
            f"HTTP {response.status_code} "
            f"en {url.split('?')[0]}: "
            f"{str(problem or response.reason)[:220]}"
        )

    return payload


def github_request(method, suffix, **kwargs):

    token = os.environ["GH_TOKEN"]

    headers = dict(
        kwargs.pop("headers", {})
    )

    headers.update({
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    })

    return request_json(
        method,
        GITHUB_API + suffix,
        token=token,
        headers=headers,
        **kwargs
    )


# ============================================================
# 2. REGISTRO DE RESULTADOS
# ============================================================

def save_receipt(out, receipt):

    out.mkdir(
        parents=True,
        exist_ok=True
    )

    filepath = (
        out / "estado_publicacion.json"
    )

    filepath.write_text(
        json.dumps(
            receipt,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )


# ============================================================
# 3. VERIFICAR CUENTAS META
# ============================================================

def check_accounts(publish):

    if not publish:
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
            "Faltan secretos o variables: "
            + ", ".join(missing)
        )

    # Verificar Instagram @sismonauta

    ig = request_json(
        "GET",
        (
            f"{IG_BASE}/{META_VERSION}/"
            f"{os.environ['INSTAGRAM_USER_ID']}"
        ),
        token=os.environ["INSTAGRAM_TOKEN"],
        params={
            "fields": "id,username"
        }
    )

    username = (
        ig.get("username", "")
        .lower()
        .lstrip("@")
    )

    if username != "sismonauta":
        raise RuntimeError(
            "La cuenta de Instagram no corresponde "
            "a @sismonauta"
        )

    print(
        "Instagram verificado: @sismonauta",
        flush=True
    )

    # Verificar Fanpage Sismonauta

    fb = request_json(
        "GET",
        (
            f"{FB_BASE}/"
            f"{os.environ['FACEBOOK_PAGE_ID']}"
        ),
        token=os.environ["FACEBOOK_PAGE_TOKEN"],
        params={
            "fields": "id,name"
        }
    )

    expected_page = (
        os.environ["FACEBOOK_PAGE_ID"]
    )

    if (
        str(fb.get("id")) != expected_page
        or "sismonauta" not in (
            fb.get("name", "").lower()
        )
    ):
        raise RuntimeError(
            "La página de Facebook no corresponde "
            "a Sismonauta"
        )

    print(
        "Facebook verificado: Sismonauta",
        flush=True
    )


# ============================================================
# 4. ESTADO PERSISTENTE EN GITHUB
# ============================================================

def state_read(path):

    import base64

    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    branch = os.getenv(
        "GITHUB_REF_NAME",
        "main"
    )

    try:

        result = github_request(
            "GET",
            (
                f"/repos/{repo}/contents/"
                f"{quote(path, safe='/')}"
            ),
            params={
                "ref": branch
            }
        )

        content = base64.b64decode(
            result["content"]
        ).decode("utf-8")

        return (
            json.loads(content),
            result["sha"]
        )

    except RuntimeError as exc:

        if "HTTP 404 " in str(exc):
            return {}, None

        raise


def state_save(path, data):

    import base64

    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    branch = os.getenv(
        "GITHUB_REF_NAME",
        "main"
    )

    for attempt in range(4):

        _, sha = state_read(path)

        encoded = base64.b64encode(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ).encode("utf-8")
        ).decode("ascii")

        body = {
            "message": (
                "SISMONAUTA: actualizar estado ETAS"
            ),
            "content": encoded,
            "branch": branch
        }

        if sha:
            body["sha"] = sha

        try:

            github_request(
                "PUT",
                (
                    f"/repos/{repo}/contents/"
                    f"{quote(path, safe='/')}"
                ),
                json=body
            )

            return

        except RuntimeError:

            if attempt == 3:
                raise

            time.sleep(
                2 * (attempt + 1)
            )


# ============================================================
# 5. EJECUTAR ETAS ORIGINAL PARA CHILE
# ============================================================

def run_original(original, out):

    # El script ETAS original guarda su video
    # en /mnt/data cuando no ejecuta en Colab.

    Path("/mnt/data").mkdir(
        parents=True,
        exist_ok=True
    )

    env = os.environ.copy()

    env["PYTHONUNBUFFERED"] = "1"

    source = original.read_text(
        encoding="utf-8"
    )

    # Corrección científica:
    #
    # El script original recibe un porcentaje.
    # No debe volver a multiplicarlo por 100
    # al convertirlo en texto narrado.
    #
    # Se corrige solamente la copia temporal.

    old_expression = (
        "float(porcentaje_original) * 100.0"
    )

    if old_expression in source:

        source = source.replace(
            old_expression,
            "float(porcentaje_original)"
        )

        print(
            "Corrección de unidades aplicada "
            "a la narración.",
            flush=True
        )

    # Conservamos los cálculos, los heatmaps,
    # la música y las animaciones originales.

    temporary_script = (
        out / "_etas_temporal.py"
    )

    temporary_script.write_text(
        source,
        encoding="utf-8"
    )

    print(
        "===================================",
        flush=True
    )

    print(
        "SISMONAUTA ETAS - CHILE",
        flush=True
    )

    print(
        "País seleccionado: Chile",
        flush=True
    )

    print(
        "Generación de 25 mapas horarios",
        flush=True
    )

    print(
        "===================================",
        flush=True
    )

    # El código original pide el país usando input().
    # Respondemos automáticamente Chile.

    result = subprocess.run(
        [
            sys.executable,
            "-u",
            str(temporary_script)
        ],
        input="Chile\n",
        text=True,
        env=env,
        timeout=int(
            os.getenv(
                "ETAS_TIMEOUT_SECONDS",
                "16500"
            )
        )
    )

    if result.returncode != 0:

        raise RuntimeError(
            "ETAS terminó con código "
            f"{result.returncode}"
        )

    video = Path(
        "/mnt/data/"
        "ETAS_DOBLE_HEATMAP_CON_LIKE.mp4"
    )

    if not video.exists():

        raise RuntimeError(
            "ETAS no generó el video esperado: "
            + str(video)
        )

    if video.stat().st_size < 10000:

        raise RuntimeError(
            "El archivo MP4 generado es inválido "
            "o está incompleto."
        )

    print(
        "Video ETAS generado correctamente:",
        video,
        flush=True
    )

    return video


# ============================================================
# 6. COMPROBAR VIDEO CON FFPROBE
# ============================================================

def video_probe(video):

    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=codec_type,width,height,codec_name"
            ),
            "-of",
            "json",
            str(video)
        ],
        text=True
    )

    return json.loads(raw)


# ============================================================
# 7. CREAR REEL VERTICAL 9:16
# ============================================================

def reel_version(original, destination):

    # El video original es 1080x1350.
    #
    # Instagram recibe 1080x1920.
    # No recortamos ninguno de los heatmaps.
    #
    # Se preserva el diseño original,
    # añadiendo bandas de fondo.

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
            "scale=1080:1350:"
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

    subprocess.run(
        command,
        check=True
    )

    print(
        "Reel vertical preparado:",
        destination,
        flush=True
    )

    return destination


# ============================================================
# 8. ALOJAR REEL EN GITHUB RELEASES
# ============================================================

def create_release_asset(path, unique_tag):

    # El Reel se aloja públicamente para que
    # Instagram pueda descargarlo mediante
    # video_url.
    #
    # El repositorio y el release deben ser
    # accesibles públicamente.

    repo = os.environ[
        "GITHUB_REPOSITORY"
    ]

    release = github_request(
        "POST",
        f"/repos/{repo}/releases",
        json={
            "tag_name": unique_tag,
            "name": (
                f"ETAS {unique_tag}"
            ),
            "draft": False,
            "prerelease": True,
            "generate_release_notes": False,
            "body": (
                "Video científico experimental "
                "de SISMONAUTA. "
                "No constituye alerta sísmica."
            )
        }
    )

    release_id = release["id"]

    upload_url = (
        release["upload_url"]
        .split("{")[0]
        + "?name="
        + quote(path.name)
    )

    try:

        with path.open("rb") as video_file:

            asset = request_json(
                "POST",
                upload_url,
                token=os.environ["GH_TOKEN"],
                headers={
                    "Content-Type": "video/mp4",
                    "Accept": (
                        "application/vnd.github+json"
                    )
                },
                data=video_file
            )

    except Exception as exc:

        raise RuntimeError(
            "No se pudo alojar el Reel "
            f"en GitHub Release {release_id}: "
            f"{str(exc)[:180]}"
        ) from exc

    public_url = asset[
        "browser_download_url"
    ]

    print(
        "Reel alojado en GitHub Releases",
        flush=True
    )

    return public_url, release_id


# ============================================================
# 9. PUBLICAR REEL EN INSTAGRAM
# ============================================================

def instagram_reel(
    video,
    caption,
    result,
    persist
):

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

    # ------------------------------------------
    # ALTERNATIVA: CARGA REANUDABLE
    # ------------------------------------------

    if mode == "resumable":

        data["upload_type"] = "resumable"

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
                f"ig-api-upload/{META_VERSION}/"
                f"{container_id}"
            )
        )

        with video.open("rb") as video_file:

            request_json(
                "POST",
                upload_url,
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
                data=video_file
            )

    # ------------------------------------------
    # MODO PREDETERMINADO: URL PÚBLICA
    # ------------------------------------------

    else:

        unique_tag = (
            "sismonauta-etas-"
            + str(int(time.time()))
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

    # ------------------------------------------
    # ESPERAR PROCESAMIENTO
    # ------------------------------------------

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

        print(
            f"Instagram: procesamiento {state}",
            flush=True
        )

        if state == "FINISHED":
            break

        if state in (
            "ERROR",
            "EXPIRED"
        ):

            raise RuntimeError(
                "Instagram no procesó el Reel: "
                + str(
                    status.get("status")
                )[:160]
            )

        time.sleep(20)

    else:

        raise RuntimeError(
            "Instagram no terminó de procesar "
            "el video en 10 minutos."
        )

    # ------------------------------------------
    # PUBLICAR CONTENEDOR
    # ------------------------------------------

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

    result.update({
        "status": "published",
        "id": str(
            published["id"]
        )
    })

    persist()

    print(
        "REEL PUBLICADO EN INSTAGRAM:",
        published["id"],
        flush=True
    )


# ============================================================
# 10. PUBLICAR VIDEO EN FACEBOOK
# ============================================================

def facebook_video(
    video,
    caption,
    result,
    persist
):

    result["status"] = "uncertain"

    persist()

    page_id = os.environ[
        "FACEBOOK_PAGE_ID"
    ]

    token = os.environ[
        "FACEBOOK_PAGE_TOKEN"
    ]

    url = (
        f"{FB_BASE}/{page_id}/videos"
    )

    # Se utiliza el video original 4:5,
    # conservando su resolución 1080x1350.

    with video.open("rb") as video_file:

        posted = request_json(
            "POST",
            url,
            token=token,
            data={
                "description": caption,
                "title": (
                    "SISMONAUTA | "
                    "ETAS experimental"
                ),
                "published": "true"
            },
            files={
                "source": (
                    video.name,
                    video_file,
                    "video/mp4"
                )
            }
        )

    result.update({
        "status": "published",
        "id": str(
            posted["id"]
        )
    })

    persist()

    print(
        "VIDEO ENVIADO A FACEBOOK:",
        posted["id"],
        flush=True
    )


# ============================================================
# 11. PROGRAMA PRINCIPAL
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
        default="output_etas"
    )

    parser.add_argument(
        "--publish",
        action="store_true"
    )

    args = parser.parse_args()

    out = Path(
        args.out
    ).resolve()

    out.mkdir(
        parents=True,
        exist_ok=True
    )

    receipt = {
        "status": "starting",
        "country": COUNTRY,
        "created_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
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

    state_path = (
        "sismonauta_etas/"
        "state_"
        + hashlib.sha256(
            COUNTRY.casefold().encode()
        ).hexdigest()[:12]
        + ".json"
    )

    # ------------------------------------------
    # GUARDAR ESTADO
    # ------------------------------------------

    def persist():

        save_receipt(
            out,
            receipt
        )

        if args.publish:

            state_save(
                state_path,
                receipt
            )

    try:

        # --------------------------------------
        # COMPROBAR CUENTAS
        # --------------------------------------

        check_accounts(
            args.publish
        )

        # --------------------------------------
        # EVITAR DUPLICACIONES DIARIAS
        # --------------------------------------

        if args.publish:

            old, _ = state_read(
                state_path
            )

            today = (
                receipt["created_at"][:10]
            )

            if (
                old.get("status")
                in (
                    "published",
                    "partial_or_uncertain"
                )
                and old.get(
                    "created_at", ""
                )[:10] == today
            ):

                receipt = old

                save_receipt(
                    out,
                    receipt
                )

                print(
                    "Ya existe una publicación "
                    "o estado incierto hoy.",
                    flush=True
                )

                print(
                    "Se evita la duplicación.",
                    flush=True
                )

                return

        # --------------------------------------
        # VERIFICAR MODELO ORIGINAL
        # --------------------------------------

        original = Path(
            args.original
        ).resolve()

        if not original.is_file():

            raise RuntimeError(
                "Falta el archivo ETAS original: "
                + str(original)
            )

        # --------------------------------------
        # GENERAR VIDEO
        # --------------------------------------

        receipt["status"] = "generating"

        persist()

        video = run_original(
            original,
            out
        )

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
                "Duración incompatible con "
                "Reel de Instagram: "
                f"{duration:.1f} segundos"
            )

        receipt["duration_seconds"] = (
            duration
        )

        receipt["video_file"] = (
            str(video)
        )

        # --------------------------------------
        # PREPARAR VERSIÓN VERTICAL
        # --------------------------------------

        reel = reel_version(
            video,
            out / (
                "sismonauta_etas_"
                "reel_9x16.mp4"
            )
        )

        # --------------------------------------
        # TEXTO DE PUBLICACIÓN
        # --------------------------------------

        caption = (
            "🌎 SISMONAUTA | "
            "Evolución ETAS experimental "
            "en Chile\n\n"

            "25 instantáneas de la actividad "
            "sísmica de las últimas 24 horas. "

            "Cada mapa estima, mediante un "
            "modelo no calibrado "
            "prospectivamente, valores para "
            "las 24 horas posteriores "
            "a esa instantánea.\n\n"

            "Se muestra la probabilidad "
            "modelada por celda y su variación "
            "respecto al inicio del análisis.\n\n"

            "⚠️ Resultados experimentales. "
            "No equivalen a probabilidades "
            "sísmicas oficiales, no predicen "
            "un terremoto específico y "
            "no constituyen alerta sísmica.\n\n"

            "Consulta siempre las fuentes "
            "oficiales de protección civil.\n\n"

            "Fuente: USGS. "
            "Cartografía: Natural Earth. "
            "Ciudades: GeoNames/OpenStreetMap.\n\n"

            "#Sismonauta #Sismologia "
            "#Terremotos #ETAS #Ciencia "
            "#Chile"
        )

        # --------------------------------------
        # VISTA PREVIA
        # --------------------------------------

        receipt["status"] = "preview_only"

        persist()

        if not args.publish:

            print(
                "===================================",
                flush=True
            )

            print(
                "VIDEO GENERADO - SIN PUBLICAR",
                flush=True
            )

            print(
                "Archivo:",
                video,
                flush=True
            )

            print(
                "===================================",
                flush=True
            )

            return

        # --------------------------------------
        # PUBLICACIÓN REAL
        # --------------------------------------

        receipt["status"] = "publishing"

        persist()

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

            target = (
                receipt["targets"][site]
            )

            try:

                action(
                    file,
                    caption,
                    target,
                    persist
                )

                print(
                    "PUBLICADO:",
                    site,
                    target.get("id"),
                    flush=True
                )

            except Exception as exc:

                errors.append(
                    f"{site}: {exc}"
                )

                target["error"] = (
                    str(exc)[:250]
                )

                persist()

        # --------------------------------------
        # ERRORES PARCIALES
        # --------------------------------------

        if errors:

            receipt["status"] = (
                "partial_or_uncertain"
            )

            persist()

            raise RuntimeError(
                " / ".join(errors)
            )

        # --------------------------------------
        # ÉXITO
        # --------------------------------------

        receipt["status"] = "published"

        persist()

        print(
            "===================================",
            flush=True
        )

        print(
            "SISMONAUTA ETAS CHILE",
            flush=True
        )

        print(
            "PUBLICACIÓN COMPLETADA",
            flush=True
        )

        print(
            "Instagram: publicado",
            flush=True
        )

        print(
            "Facebook: enviado",
            flush=True
        )

        print(
            "===================================",
            flush=True
        )

    except Exception as exc:

        if receipt["status"] not in (
            "partial_or_uncertain",
            "published"
        ):

            has_uncertain_target = any(
                item.get("status") in (
                    "uncertain",
                    "published"
                )
                for item in (
                    receipt["targets"].values()
                )
            )

            receipt["status"] = (
                "partial_or_uncertain"
                if has_uncertain_target
                else "error"
            )

        receipt["error"] = (
            str(exc)[:500]
        )

        save_receipt(
            out,
            receipt
        )

        print(
            "ERROR:",
            str(exc),
            file=sys.stderr
        )

        raise


# ============================================================
# INICIO
# ============================================================

if __name__ == "__main__":
    main()
