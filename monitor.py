#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CSN -> aceleración vertical observada -> MP4 -> X.

Un único ciclo por ejecución.
Persistencia durable mediante GitHub Contents API y control CAS por SHA.

Visualización:
- Heatmap JET por áreas donde hay soporte espacial suficiente.
- Todas las estaciones superpuestas en un único gráfico.
- Escala física común y cursor temporal sincronizado.
- No se fabrican señales ni se rellenan huecos con ceros.

Python 3.11+.
Pruebas: python -m pytest -q test_monitor.py
Inicialización explícita: python monitor.py --init-state
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as futures
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
import zipfile

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, quote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests_oauthlib import OAuth1


UTC = timezone.utc

CSN = "https://www.sismologia.cl"

STATES = {
    "pendiente",
    "procesando",
    "enviando",
    "publicado",
    "simulado",
    "resultado_incierto",
    "expirado",
}

TERMINAL = {
    "publicado",
    "simulado",
    "resultado_incierto",
    "expirado",
}

SECRET_NAMES = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
    "GITHUB_TOKEN",
)


# ============================================================
# UTILIDADES
# ============================================================

def utcnow():
    return datetime.now(UTC)


def iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def date(value):
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Fecha sin zona horaria")
    return d.astimezone(UTC)


def clean(value):
    """Sanitiza mensajes antes de guardarlos o imprimirlos."""
    s = str(value)

    for name in SECRET_NAMES:
        secret = os.getenv(name, "")
        if secret:
            s = s.replace(secret, "[REDACTADO]")

    s = re.sub(
        r"(?i)(bearer|oauth_token|oauth_signature|authorization)"
        r"\s*[:=]?\s*[^\s,]+",
        r"\1 [REDACTADO]",
        s,
    )

    return s.replace("\r", " ").replace("\n", " ")[:600]


def log(message):
    print(f"{iso(utcnow())} {clean(message)}", flush=True)


def boolean(name, default=False):
    value = os.getenv(name, str(default)).strip().lower()

    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"Booleano inválido: {name}")

    return value in {"true", "1", "yes"}


def atomic(path, value):
    """Escritura local atómica de JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as f:
        tmp = f.name
        json.dump(
            value,
            f,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, path)


# ============================================================
# CONFIGURACIÓN
# ============================================================

@dataclass
class Config:
    mag: float = 4.0

    # La variable LOOKBACK_HOURS del workflow tiene prioridad.
    lookback: float = 24

    pre: float = 60
    post: float = 120

    # Margen adicional por extremo para procesamiento instrumental.
    margin: float = 30

    # Espera lógica adicional para disponibilidad de registros.
    # No mantiene el runner esperando: programa next_attempt.
    latency: float = 180

    fps: int = 4
    step: float = 1

    workers: int = 4
    timeout: float = 30

    max_events: int = 2
    budget: float = 2100
    event_budget: float = 800
    x_wait: float = 900

    radius: float = 250
    stations: int = 6
    candidates: int = 12

    # Soporte espacial del heatmap.
    support: float = 100
    triangle: float = 150

    max_attempts: int = 12
    backoff: float = 600

    publish: bool = False
    reconcile: bool = False

    state_dir: str = ".state"
    output: str = "output"
    branch: str = "monitor-state"

    fdsn: str = "https://eew.csn.uchile.cl"

    @classmethod
    def env(cls):
        c = cls()

        names = {
            "mag": "MAG_MIN",
            "lookback": "LOOKBACK_HOURS",
            "pre": "PRE_SEG",
            "post": "POST_SEG",
            "margin": "MARGEN_SEG",
            "latency": "DATA_LATENCY_SEC",
            "fps": "FPS",
            "step": "FRAME_STEP_SEC",
            "workers": "DOWNLOAD_WORKERS",
            "timeout": "HTTP_TIMEOUT",
            "max_events": "MAX_EVENTS_PER_RUN",
            "budget": "MAX_RUNTIME_SEC",
            "event_budget": "EVENT_TIMEOUT_SEC",
            "x_wait": "X_PROCESS_TIMEOUT_SEC",
            "radius": "MAX_DISTANCE_KM",
            "stations": "MAX_STATIONS",
            "candidates": "MAX_CANDIDATES",
            "support": "SUPPORT_KM",
            "triangle": "MAX_TRIANGLE_KM",
            "max_attempts": "MAX_ATTEMPTS",
            "backoff": "RETRY_BASE_SEC",
            "state_dir": "STATE_DIR",
            "output": "OUTPUT_DIR",
            "branch": "STATE_BRANCH",
            "fdsn": "FDSN_BASE",
        }

        for field, env_name in names.items():
            default = getattr(c, field)
            setattr(
                c,
                field,
                type(default)(os.getenv(env_name, default)),
            )

        c.publish = boolean("PUBLISH_TO_X")
        c.reconcile = boolean("RECONCILE_X")

        for field in names:
            value = getattr(c, field)
            if isinstance(value, (float, int)):
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(
                        f"Configuración positiva/finita requerida: {field}"
                    )

        if c.x_wait > 900 or c.margin < 30 or c.budget < 180:
            raise ValueError(
                "X_PROCESS_TIMEOUT_SEC <=900; "
                "MARGEN_SEG >=30; presupuesto >=180"
            )

        if (
            c.stations > 8
            or c.candidates < c.stations
            or c.workers > 8
        ):
            raise ValueError(
                "MAX_STATIONS <=8; "
                "candidatos >= estaciones; workers <=8"
            )

        n = (c.pre + c.post) / c.step

        if (
            abs(n - round(n)) > 1e-6
            or not 0.5 <= n / c.fps <= 140
        ):
            raise ValueError(
                "Ventana/paso debe ser entero; "
                "video conservador de 0.5 a 140 s"
            )

        return c


# ============================================================
# EXCEPCIONES Y PRESUPUESTO
# ============================================================

class Failure(RuntimeError):
    pass


class SourceError(Failure):
    pass


class StructureError(SourceError):
    pass


class PersistenceError(Failure):
    pass


class BudgetError(Failure):
    pass


class XError(Failure):
    def __init__(
        self,
        message,
        status=0,
        uncertain=False,
        reset=None,
    ):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain
        self.reset = reset

        self.global_stop = (
            status in {401, 402, 403, 429}
            or status >= 500
            or uncertain
        )


class Clock:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return self.end - time.monotonic()

    def check(self, reserve=0):
        if self.remaining() <= reserve:
            raise BudgetError(
                "Presupuesto agotado; "
                "trabajo conservado para próximo ciclo"
            )


# ============================================================
# HTTP DE LECTURA
# ============================================================

def obtener(url, c, clock, params=None):
    """GET idempotente: hasta tres intentos ante fallos transitorios."""
    for attempt in range(3):
        clock.check(5)

        try:
            with requests.get(
                url,
                params=params,
                timeout=min(c.timeout, clock.remaining() - 2),
                headers={
                    "User-Agent": "CSN-observed-motion/1.0",
                },
                stream=True,
            ) as r:
                if r.status_code == 204:
                    return b""

                if (
                    r.status_code in {429, 500, 502, 503, 504}
                    and attempt < 2
                ):
                    raw_delay = r.headers.get("Retry-After", "2")

                    try:
                        wait = float(raw_delay)
                    except ValueError:
                        try:
                            wait = (
                                parsedate_to_datetime(raw_delay)
                                - utcnow()
                            ).total_seconds()
                        except Exception:
                            wait = 2

                    delay = max(2 ** attempt, wait)

                    if (
                        delay > 30
                        or delay + 5 >= clock.remaining()
                    ):
                        raise SourceError(
                            f"GET HTTP {r.status_code}: "
                            "espera diferida"
                        )

                    time.sleep(delay)
                    continue

                if r.status_code != 200:
                    parsed = urlparse(url)
                    raise SourceError(
                        f"GET {parsed.netloc}{parsed.path}: "
                        f"HTTP {r.status_code}"
                    )

                chunks = []
                size = 0

                for chunk in r.iter_content(65536):
                    clock.check(2)
                    size += len(chunk)

                    if size > 100 * 1024 * 1024:
                        raise SourceError(
                            "Respuesta excede 100 MiB"
                        )

                    chunks.append(chunk)

                return b"".join(chunks)

        except requests.RequestException as exc:
            if attempt == 2:
                raise SourceError(
                    f"GET {urlparse(url).netloc}: "
                    f"{type(exc).__name__}"
                ) from None

            time.sleep(2 ** attempt)

    raise SourceError("GET sin resultado")


# ============================================================
# CATÁLOGO CSN
# ============================================================

def key(s):
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(ch) != "Mn"
    ).strip()


def numero(s):
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(s))

    if not match:
        raise StructureError("Valor numérico ausente")

    value = float(match.group().replace(",", "."))

    if not math.isfinite(value):
        raise StructureError("Número no finito")

    return value


def validate_event(e):
    for name in (
        "id",
        "origin",
        "mag_display",
        "reference",
        "url",
    ):
        if not isinstance(e.get(name), str) or not e[name]:
            raise ValueError(f"Evento sin {name}")

    if not re.fullmatch(r"\d+", e["id"]):
        raise ValueError("ID CSN inválido")

    date(e["origin"])

    for name, lo, hi in (
        ("lat", -90, 90),
        ("lon", -180, 180),
        ("mag", -2, 10),
    ):
        value = e.get(name)

        if isinstance(value, bool) or not isinstance(
            value, (float, int)
        ):
            raise ValueError(
                f"Evento sin {name} numérico"
            )

        if (
            not math.isfinite(value)
            or not lo <= value <= hi
        ):
            raise ValueError(
                f"Evento fuera de rango: {name}"
            )

    if "depth" not in e:
        raise ValueError("Campo profundidad ausente")

    if e["depth"] is not None:
        if (
            not math.isfinite(e["depth"])
            or not 0 <= e["depth"] <= 800
        ):
            raise ValueError("Profundidad inválida")

    u = urlparse(e["url"])

    if (
        u.scheme != "https"
        or u.hostname != "www.sismologia.cl"
        or not re.fullmatch(
            r"/sismicidad/informes/\d{4}/\d{2}/"
            + e["id"]
            + r"\.html",
            u.path,
        )
    ):
        raise ValueError("URL CSN inválida")

    return e


def leer_evento(html, url):
    soup = BeautifulSoup(html, "html.parser")
    fields = {}

    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"])

        if len(cells) == 2:
            label = key(
                cells[0].get_text(" ", strip=True)
            )
            fields[label] = cells[1].get_text(
                " ", strip=True
            )

    try:
        origin = datetime.strptime(
            fields["hora utc"],
            "%H:%M:%S %d/%m/%Y",
        ).replace(tzinfo=UTC)

        depth = fields.get("profundidad", "")

        e = {
            "id": Path(urlparse(url).path).stem,
            "origin": iso(origin),
            "mag": numero(fields["magnitud"]),
            "mag_display": (
                f"{numero(fields['magnitud']):.1f}"
            ),
            "reference": fields["referencia"],
            "lat": numero(fields["latitud"]),
            "lon": numero(fields["longitud"]),
            "depth": (
                numero(depth)
                if re.search(r"\d", depth)
                else None
            ),
            "url": url,
        }

        return validate_event(e)

    except (KeyError, ValueError, TypeError) as exc:
        raise StructureError(
            f"Informe incompatible {url}: "
            f"{type(exc).__name__}"
        ) from None


def catalogue_links(html, url, daily, c):
    soup = BeautifulSoup(html, "html.parser")

    tables = [
        table
        for table in soup.find_all("table")
        if any(
            "magnitud" in key(h.get_text())
            for h in table.find_all("th")
        )
    ]

    if not tables or (
        daily
        and "utc" not in key(soup.get_text(" "))
    ):
        raise StructureError(
            "Catálogo sin tabla/convención UTC reconocible"
        )

    links = set()

    for table in tables:
        headers = [
            key(h.get_text(" ", strip=True))
            for h in table.find_all("th")
        ]

        magnitude_index = next(
            i
            for i, header in enumerate(headers)
            if "magnitud" in header
        )

        if daily and not any(
            "fecha utc" in header for header in headers
        ):
            raise StructureError(
                "Catálogo diario perdió columna Fecha UTC"
            )

        for row in table.find_all("tr"):
            cells = row.find_all(
                "td", recursive=False
            )

            if not cells:
                continue

            if len(cells) != len(headers):
                raise StructureError(
                    "Fila de catálogo incompatible"
                )

            magnitude = numero(
                cells[magnitude_index].get_text(
                    " ", strip=True
                )
            )

            anchors = [
                anchor
                for anchor in row.find_all("a", href=True)
                if re.search(
                    r"/sismicidad/informes/"
                    r"\d{4}/\d{2}/\d+\.html$",
                    urljoin(url, anchor["href"]),
                )
            ]

            if not anchors:
                raise StructureError(
                    "Fila sin enlace a informe"
                )

            if magnitude >= c.mag:
                for anchor in anchors:
                    u = urlparse(
                        urljoin(url, anchor["href"])
                    )

                    if u.hostname not in {
                        "sismologia.cl",
                        "www.sismologia.cl",
                    }:
                        raise StructureError(
                            "Enlace fuera de CSN"
                        )

                    links.add(CSN + u.path)

    return links


def discover(c, clock, start, end):
    urls = [CSN + "/"]

    day = start.date()
    while day <= end.date():
        urls.append(
            CSN
            + day.strftime(
                "/sismicidad/catalogo/%Y/%m/%Y%m%d.html"
            )
        )
        day += timedelta(days=1)

    links = set()
    events = {}
    errors = []
    valid = 0

    for i, url in enumerate(urls):
        try:
            links.update(
                catalogue_links(
                    obtener(url, c, clock),
                    url,
                    i > 0,
                    c,
                )
            )
            valid += 1

        except (SourceError, BudgetError) as exc:
            errors.append({
                "url": url,
                "type": type(exc).__name__,
                "error": clean(exc),
            })

    for url in sorted(links):
        try:
            e = leer_evento(
                obtener(url, c, clock),
                url,
            )

            if (
                start <= date(e["origin"]) <= end
                and e["mag"] >= c.mag
            ):
                events[e["id"]] = e

        except (SourceError, BudgetError) as exc:
            errors.append({
                "url": url,
                "type": type(exc).__name__,
                "error": clean(exc),
            })

    if not errors:
        status = "valida"
    elif valid:
        status = "parcial"
    elif any(
        item["type"] == "StructureError"
        for item in errors
    ):
        status = "incompatible"
    else:
        status = "inaccesible"

    return list(events.values()), {
        "status": status,
        "pages_ok": valid,
        "pages": len(urls),
        "errors": errors,
    }


# ============================================================
# ESTADO DURABLE
# ============================================================

def empty_state(mode):
    return {
        "schema": 1,
        "mode": mode,
        "account_id": None,
        "revision": str(uuid.uuid4()),
        "updated": iso(utcnow()),
        "events": {},
    }


def validate_state(s, mode):
    try:
        if (
            s["schema"] != 1
            or s["mode"] != mode
            or not isinstance(s["events"], dict)
        ):
            raise ValueError(
                "Esquema/modo incorrecto"
            )

        if not isinstance(s["revision"], str):
            raise ValueError("Revisión ausente")

        date(s["updated"])

        if s["account_id"] is not None:
            if not re.fullmatch(
                r"\d+", s["account_id"]
            ):
                raise ValueError("Cuenta inválida")

        for event_id, r in s["events"].items():
            validate_event(r["event"])

            if (
                event_id != r["event"]["id"]
                or r["status"] not in STATES
            ):
                raise ValueError(
                    "Registro inválido"
                )

            if (
                not isinstance(r["attempts"], int)
                or r["attempts"] < 0
            ):
                raise ValueError(
                    "Intentos inválidos"
                )

            for name in (
                "next_attempt",
                "updated",
                "created",
            ):
                date(r[name])

            for name in (
                "media_id",
                "tweet_id",
                "text",
                "error",
            ):
                if name not in r:
                    raise ValueError(
                        "Campo de estado ausente"
                    )

            if r["status"] == "publicado":
                if not re.fullmatch(
                    r"\d+", str(r["tweet_id"])
                ):
                    raise ValueError(
                        "Publicado sin tweet ID"
                    )

            if (
                mode == "live"
                and r["status"] == "simulado"
            ):
                raise ValueError(
                    "Estado simulado en producción"
                )

        return s

    except (KeyError, ValueError, TypeError) as exc:
        raise PersistenceError(
            f"Estado corrupto: {clean(exc)}"
        ) from None


class Store:
    """
    Estado autoritativo en rama GitHub.
    Cada escritura utiliza el SHA anterior y se verifica remotamente.
    """

    def __init__(self, c):
        self.c = c
        self.mode = (
            "live" if c.publish else "simulation"
        )

        self.path = (
            Path(c.state_dir)
            / (self.mode + ".json")
        )

        self.repo = os.getenv(
            "GITHUB_REPOSITORY", ""
        )
        self.token = os.getenv(
            "GITHUB_TOKEN", ""
        )
        self.sha = None

        if c.publish and (
            not self.repo or not self.token
        ):
            raise PersistenceError(
                "Publicación requiere GITHUB_REPOSITORY "
                "y GITHUB_TOKEN"
            )

        self.remote = bool(
            self.repo and self.token
        )

        self.base = (
            f"https://api.github.com/repos/{self.repo}"
        )
        self.file = (
            "/contents/state/"
            + self.mode
            + ".json"
        )

    def api(self, method, path, **kwargs):
        try:
            return requests.request(
                method,
                self.base + path,
                timeout=self.c.timeout,
                headers={
                    "Authorization": (
                        "Bearer " + self.token
                    ),
                    "Accept": (
                        "application/vnd.github+json"
                    ),
                    "X-GitHub-Api-Version": (
                        "2022-11-28"
                    ),
                },
                **kwargs,
            )

        except requests.RequestException as exc:
            raise PersistenceError(
                f"GitHub {method}: "
                f"{type(exc).__name__}"
            ) from None

    def load(self, init=False):
        if not self.remote:
            if not self.path.exists():
                s = empty_state(self.mode)
            else:
                try:
                    s = json.loads(
                        self.path.read_text()
                    )
                except Exception:
                    raise PersistenceError(
                        "JSON local ilegible"
                    ) from None

        else:
            r = self.api(
                "GET",
                self.file,
                params={"ref": self.c.branch},
            )

            if r.status_code == 404 and init:
                branch = self.api(
                    "GET",
                    "/git/ref/heads/"
                    + quote(self.c.branch, safe=""),
                )

                if branch.status_code == 404:
                    repo = self.api("GET", "")

                    if repo.status_code != 200:
                        raise PersistenceError(
                            f"Repositorio: HTTP "
                            f"{repo.status_code}"
                        )

                    ref = self.api(
                        "GET",
                        "/git/ref/heads/"
                        + repo.json()["default_branch"],
                    )

                    if ref.status_code != 200:
                        raise PersistenceError(
                            "No se pudo leer rama "
                            "predeterminada"
                        )

                    created = self.api(
                        "POST",
                        "/git/refs",
                        json={
                            "ref": (
                                "refs/heads/"
                                + self.c.branch
                            ),
                            "sha": (
                                ref.json()["object"]["sha"]
                            ),
                        },
                    )

                    if created.status_code != 201:
                        raise PersistenceError(
                            f"Crear rama: HTTP "
                            f"{created.status_code}"
                        )

                elif branch.status_code != 200:
                    raise PersistenceError(
                        f"Leer rama: HTTP "
                        f"{branch.status_code}"
                    )

                s = empty_state(self.mode)
                self.save(s)

            elif r.status_code == 200:
                try:
                    body = r.json()
                    self.sha = body["sha"]

                    s = json.loads(
                        base64.b64decode(
                            body["content"],
                            validate=False,
                        )
                    )

                except Exception:
                    raise PersistenceError(
                        "Estado remoto ilegible; "
                        "publicación bloqueada"
                    ) from None

            else:
                raise PersistenceError(
                    f"Leer estado: HTTP {r.status_code}; "
                    "inicialice explícitamente si es nuevo"
                )

        validate_state(s, self.mode)
        atomic(self.path, s)

        return s

    def save(self, s):
        try:
            self._save(s)

        except PersistenceError:
            raise

        except Exception as exc:
            raise PersistenceError(
                f"Guardado fallido: "
                f"{type(exc).__name__}: {clean(exc)}"
            ) from None

    def _save(self, s):
        s["updated"] = iso(utcnow())
        s["revision"] = str(uuid.uuid4())

        validate_state(s, self.mode)
        atomic(self.path, s)

        if not self.remote:
            return

        raw = json.dumps(
            s,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()

        if len(raw) > 900000:
            raise PersistenceError(
                "Estado supera límite conservador; "
                "archivar tombstones sin perder IDs"
            )

        body = {
            "message": "monitor state [skip ci]",
            "branch": self.c.branch,
            "content": (
                base64.b64encode(raw).decode()
            ),
        }

        if self.sha:
            body["sha"] = self.sha

        # No repetir PUT ambiguos ni cambiar el SHA tras conflicto.
        r = self.api(
            "PUT",
            self.file,
            json=body,
        )

        if r.status_code not in {200, 201}:
            raise PersistenceError(
                f"Guardar estado CAS: HTTP "
                f"{r.status_code}; se detiene"
            )

        confirm = self.api(
            "GET",
            self.file,
            params={"ref": self.c.branch},
        )

        try:
            body_confirmed = confirm.json()

            actual = json.loads(
                base64.b64decode(
                    body_confirmed["content"]
                )
            )

            if (
                confirm.status_code != 200
                or actual != s
            ):
                raise ValueError()

            self.sha = body_confirmed["sha"]

        except Exception:
            raise PersistenceError(
                "No se confirmó persistencia remota"
            ) from None

        log(
            f"Persistencia confirmada: {self.mode}, "
            f"revisión {s['revision']}"
        )


def record(event, now):
    return {
        "event": event,
        "status": "pendiente",
        "attempts": 0,
        "next_attempt": iso(now),
        "created": iso(now),
        "updated": iso(now),
        "error": "",
        "media_id": None,
        "tweet_id": None,
        "text": "",
    }


def recover(s, start, now):
    counts = {
        "recovered": 0,
        "expired": 0,
    }

    for r in s["events"].values():
        if r["status"] == "enviando":
            r.update(
                status="resultado_incierto",
                error=(
                    "Interrupción durante envío; "
                    "revisión requerida"
                ),
            )

        elif r["status"] == "procesando":
            r.update(
                status="pendiente",
                next_attempt=iso(now),
            )
            counts["recovered"] += 1

        if (
            r["status"] == "pendiente"
            and date(r["event"]["origin"]) < start
        ):
            r.update(
                status="expirado",
                error="Fuera de ventana temporal",
            )
            counts["expired"] += 1

        r["updated"] = iso(now)

    return counts


def eligible(r, c, start, end):
    return (
        r["status"] == "pendiente"
        and r["attempts"] < c.max_attempts
        and start <= date(r["event"]["origin"]) <= end
        and date(r["next_attempt"]) <= end
    )


def defer_recent(r, c, now):
    ready = date(r["event"]["origin"]) + timedelta(
        seconds=c.post + c.margin + c.latency
    )

    if now < ready:
        r.update(
            next_attempt=iso(ready),
            error=(
                "Esperando ventana POST, margen "
                "y latencia"
            ),
        )
        return True

    return False


def retry(r, c, exc, reset=None):
    delay = min(
        3600,
        c.backoff
        * 2 ** min(max(r["attempts"] - 1, 0), 6),
    )

    nxt = utcnow() + timedelta(seconds=delay)

    if reset:
        nxt = max(
            nxt,
            datetime.fromtimestamp(reset, UTC),
        )

    r.update(
        status="pendiente",
        next_attempt=iso(nxt),
        error=clean(exc),
        updated=iso(utcnow()),
    )


# ============================================================
# TEXTO Y API DE X
# ============================================================

def tweet_text(e):
    """
    Valida mediante twitter-text oficial instalado con npm.
    Considera enlaces, Unicode y emojis.
    """
    reference = unicodedata.normalize(
        "NFC",
        " ".join(e["reference"].split()),
    )[:2000]

    if e["depth"] is None:
        depth = "Profundidad: no informada."
    else:
        depth = f"Profundidad: {e['depth']:g} km."

    local_time = date(e["origin"]).astimezone(
        ZoneInfo("America/Santiago")
    )

    suffix = (
        f"\n{local_time:%d/%m/%Y %H:%M:%S}\n"
        f"{depth}\n"
        f"Epicentro: lat {e['lat']:.4f}°, "
        f"lon {e['lon']:.4f}°.\n"
        f"{e['url']}"
    )

    candidates = [
        f"Sismo M {e['mag_display']} | "
        f"{reference}"
        + suffix
    ]

    candidates += [
        f"Sismo M {e['mag_display']} | "
        f"{reference[:n].rstrip()}…"
        + suffix
        for n in range(
            min(len(reference) - 1, 280),
            -1,
            -1,
        )
    ]

    javascript = (
        "const fs=require('fs'),t=require('twitter-text');"
        "const a=JSON.parse(fs.readFileSync(0,'utf8'));"
        "process.stdout.write(JSON.stringify("
        "a.find(s=>t.parseTweet(s).valid)||null));"
    )

    process = subprocess.run(
        ["node", "-e", javascript],
        input=json.dumps(candidates),
        text=True,
        capture_output=True,
        timeout=30,
    )

    if process.returncode:
        raise Failure(
            "Validación de texto falló; "
            "instalar npm twitter-text@3.1.0"
        )

    result = json.loads(process.stdout)

    if not result:
        raise Failure(
            "Texto fijo supera el límite de X"
        )

    return result


class XClient:
    BASE = "https://api.x.com/2"

    def __init__(self, c, clock):
        self.c = c
        self.clock = clock

        values = [
            os.getenv(name)
            for name in SECRET_NAMES[:4]
        ]

        if not all(values):
            raise XError(
                "Faltan Secrets de X",
                401,
            )

        self.auth = OAuth1(
            values[0],
            values[1],
            values[2],
            values[3],
        )

    def call(
        self,
        method,
        path,
        empty=False,
        **kwargs,
    ):
        self.clock.check(90)

        posting = (
            method == "POST"
            and path == "/tweets"
        )

        # No hay reintentos automáticos de creación de tweets.
        try:
            r = requests.request(
                method,
                self.BASE + path,
                auth=self.auth,
                timeout=min(
                    self.c.timeout,
                    self.clock.remaining() - 60,
                ),
                allow_redirects=False,
                **kwargs,
            )

        except requests.RequestException as exc:
            raise XError(
                f"X {path}: {type(exc).__name__}",
                uncertain=posting,
            ) from None

        if not 200 <= r.status_code < 300:
            try:
                body = r.json()

                fields = {
                    name: body[name]
                    for name in (
                        "title",
                        "detail",
                        "code",
                        "message",
                        "errors",
                    )
                    if name in body
                }

                detail = clean(
                    json.dumps(
                        fields,
                        ensure_ascii=False,
                    )
                )

            except ValueError:
                detail = "Cuerpo no JSON (omitido)"

            category = {
                401: "autenticación",
                402: "acceso/saldo",
                403: "permisos/acceso/media",
                429: "límite",
            }.get(
                r.status_code,
                (
                    "servidor"
                    if r.status_code >= 500
                    else "solicitud/media"
                ),
            )

            reset = r.headers.get(
                "x-rate-limit-reset"
            )

            raise XError(
                f"X {path}: HTTP {r.status_code} "
                f"{category}; {detail}",
                r.status_code,
                uncertain=posting and (
                    r.status_code >= 500
                    or r.status_code in {408, 409}
                ),
                reset=(
                    float(reset)
                    if reset and reset.isdigit()
                    else None
                ),
            )

        if empty and not r.content:
            return {}

        try:
            body = r.json()

            if (
                body.get("errors")
                or not isinstance(body, dict)
            ):
                raise ValueError()

            return body

        except (ValueError, AttributeError):
            raise XError(
                f"X {path}: respuesta exitosa inválida",
                uncertain=posting,
            ) from None

    def identity(self):
        data = self.call(
            "GET",
            "/users/me",
        ).get("data", {})

        if not re.fullmatch(
            r"\d+",
            str(data.get("id", "")),
        ):
            raise XError(
                "Identidad de X inválida",
                401,
            )

        log(
            f"Cuenta X: @{data.get('username', '?')} "
            f"/ {data['id']}"
        )

        return data["id"]

    def upload(self, video):
        data = self.call(
            "POST",
            "/media/upload/initialize",
            json={
                "media_type": "video/mp4",
                "media_category": "tweet_video",
                "total_bytes": (
                    Path(video).stat().st_size
                ),
            },
        ).get("data", {})

        media_id = str(data.get("id", ""))

        if not re.fullmatch(r"\d+", media_id):
            raise XError("INIT sin media ID")

        with open(video, "rb") as f:
            index = 0

            while chunk := f.read(4 * 1024 * 1024):
                self.call(
                    "POST",
                    f"/media/upload/{media_id}/append",
                    empty=True,
                    data={
                        "segment_index": str(index),
                    },
                    files={
                        "media": (
                            "segment.mp4",
                            chunk,
                            "application/octet-stream",
                        ),
                    },
                )
                index += 1

        data = self.call(
            "POST",
            f"/media/upload/{media_id}/finalize",
        ).get("data")

        if (
            not isinstance(data, dict)
            or str(data.get("id", "")) != media_id
        ):
            raise XError(
                "FINALIZE incompatible"
            )

        until = min(
            time.monotonic() + self.c.x_wait,
            self.clock.end - 100,
        )

        while data.get("processing_info"):
            info = data["processing_info"]
            state = info.get("state")

            if state == "succeeded":
                break

            if state == "failed":
                raise XError(
                    "Video rechazado: "
                    + clean(info.get("error", {}))
                )

            if state not in {
                "pending",
                "in_progress",
            }:
                raise XError(
                    "Estado de procesamiento desconocido"
                )

            delay = max(
                1,
                float(
                    info.get("check_after_secs", 5)
                ),
            )

            if time.monotonic() + delay >= until:
                raise XError(
                    "Timeout de procesamiento del video; "
                    "tweet no enviado"
                )

            log(
                f"X video {media_id}: {state}; "
                f"próxima consulta en {delay:g}s"
            )

            time.sleep(delay)

            data = self.call(
                "GET",
                "/media/upload",
                params={
                    "command": "STATUS",
                    "media_id": media_id,
                },
            ).get("data")

            if (
                not isinstance(data, dict)
                or "processing_info" not in data
            ):
                raise XError(
                    "STATUS incompatible"
                )

        return media_id

    def create(self, text, media_id):
        data = self.call(
            "POST",
            "/tweets",
            json={
                "text": text,
                "media": {
                    "media_ids": [media_id],
                },
            },
        ).get("data", {})

        tweet_id = str(data.get("id", ""))

        if not re.fullmatch(r"\d+", tweet_id):
            raise XError(
                "POST sin identificador confirmable",
                uncertain=True,
            )

        return tweet_id

    def reconcile(self, s):
        """
        Sólo confirma coincidencias positivas y únicas.
        No encontrar un tweet nunca habilita un reenvío.
        """
        uncertain = [
            r
            for r in s["events"].values()
            if r["status"] == "resultado_incierto"
        ]

        if not uncertain:
            return

        posts = []
        token = None

        for _ in range(5):
            params = {
                "max_results": 100,
                "tweet.fields": (
                    "created_at,entities,attachments"
                ),
            }

            if token:
                params["pagination_token"] = token

            body = self.call(
                "GET",
                f"/users/{s['account_id']}/tweets",
                params=params,
            )

            posts.extend(body.get("data", []))

            token = body.get(
                "meta", {}
            ).get("next_token")

            if not token:
                break

        for r in uncertain:
            matches = []

            for post in posts:
                text = post.get("text", "")

                for url in post.get(
                    "entities", {}
                ).get("urls", []):
                    text = text.replace(
                        url["url"],
                        url.get(
                            "expanded_url",
                            url["url"],
                        ),
                    )

                media_keys = post.get(
                    "attachments", {}
                ).get("media_keys", [])

                if (
                    text == r["text"]
                    and r.get("media_id")
                    and any(
                        media_key.endswith(
                            "_" + r["media_id"]
                        )
                        for media_key in media_keys
                    )
                    and date(post["created_at"])
                    >= (
                        date(
                            r.get(
                                "sent_at",
                                r["created"],
                            )
                        )
                        - timedelta(seconds=10)
                    )
                ):
                    matches.append(post)

            if len(matches) == 1:
                r.update(
                    status="publicado",
                    tweet_id=matches[0]["id"],
                    error=(
                        "Confirmado por reconciliación"
                    ),
                )
            else:
                log(
                    f"Evento {r['event']['id']}: "
                    "incierto bloqueado; revisar "
                    "cuenta y estado manualmente"
                )


def send_transaction(
    store,
    s,
    r,
    x,
    text,
    mid,
):
    r.update(
        status="enviando",
        text=text,
        media_id=mid,
        sent_at=iso(utcnow()),
        updated=iso(utcnow()),
    )

    # Debe confirmarse remotamente antes del POST.
    store.save(s)

    try:
        tweet_id = x.create(text, mid)

    except Exception as exc:
        if (
            not isinstance(exc, XError)
            or exc.uncertain
        ):
            r.update(
                status="resultado_incierto",
                error=clean(exc),
                updated=iso(utcnow()),
            )
        else:
            retry(
                r,
                store.c,
                exc,
                exc.reset,
            )

        store.save(s)
        raise

    r.update(
        status="publicado",
        tweet_id=tweet_id,
        error="",
        updated=iso(utcnow()),
    )

    log(
        "Publicación confirmada: "
        f"https://x.com/i/web/status/{tweet_id}"
    )

    try:
        store.save(s)

    except PersistenceError:
        # El estado remoto conserva enviando.
        # El siguiente runner lo bloqueará como resultado_incierto.
        log(
            f"CRÍTICO: tweet {tweet_id} confirmado; "
            "persistencia posterior falló. NO REENVIAR."
        )
        raise

    return tweet_id


# ============================================================
# ESTACIONES Y PROCESAMIENTO CIENTÍFICO
# ============================================================

def distance(lat1, lon1, lat2, lon2):
    a = math.radians(lat1)
    b = math.radians(lat2)

    h = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a)
        * math.cos(b)
        * math.sin(
            math.radians(lon2 - lon1) / 2
        ) ** 2
    )

    return (
        6371
        * 2
        * math.asin(min(1, math.sqrt(h)))
    )


def obtener_estaciones(e, c, clock):
    from obspy import read_inventory, UTCDateTime

    origin = UTCDateTime(e["origin"])
    start = origin - c.pre - c.margin
    end = origin + c.post + c.margin

    data = obtener(
        c.fdsn.rstrip("/")
        + "/fdsnws/station/1/query",
        c,
        clock,
        {
            "latitude": e["lat"],
            "longitude": e["lon"],
            "maxradius": c.radius / 111.19,
            "channel": "*NZ",
            "level": "channel",
            "format": "xml",
            "starttime": str(start),
            "endtime": str(end),
        },
    )

    if not data:
        return [], [{
            "reason": (
                "Inventario sin canales candidatos"
            ),
        }]

    inventory = read_inventory(
        io.BytesIO(data)
    )

    candidates = []
    rejected = []

    for network in inventory:
        for station in network:
            for channel in station:
                station_id = (
                    f"{network.code}.{station.code}."
                    f"{channel.location_code}."
                    f"{channel.code}"
                )

                reason = None

                sensitivity = (
                    channel.response.instrument_sensitivity
                    if channel.response
                    else None
                )

                units = (
                    sensitivity.input_units
                    if sensitivity else ""
                ).upper().replace(" ", "")

                km = distance(
                    e["lat"],
                    e["lon"],
                    channel.latitude,
                    channel.longitude,
                )

                if units not in {
                    "M/S**2",
                    "M/S^2",
                    "M/S/S",
                }:
                    reason = (
                        "Sin sensibilidad en aceleración SI"
                    )

                elif (
                    channel.sample_rate < 50
                    or abs(abs(channel.dip) - 90) > 1
                ):
                    reason = (
                        "Frecuencia <50 Hz "
                        "o componente no vertical"
                    )

                elif km > c.radius:
                    reason = "Fuera de radio"

                elif (
                    (
                        channel.start_date
                        and channel.start_date > start
                    )
                    or (
                        channel.end_date
                        and channel.end_date < end
                    )
                ):
                    reason = (
                        "Época instrumental no cubre ventana"
                    )

                if reason:
                    rejected.append({
                        "station": station_id,
                        "reason": reason,
                    })

                else:
                    candidates.append({
                        "id": station_id,
                        "net": network.code,
                        "sta": station.code,
                        "loc": channel.location_code,
                        "cha": channel.code,
                        "lat": channel.latitude,
                        "lon": channel.longitude,
                        "dip": channel.dip,
                        "distance": km,
                    })

    candidates.sort(
        key=lambda item: (
            item["distance"],
            item["id"],
        )
    )

    selected = []
    seen = set()

    for candidate in candidates:
        station_key = (
            candidate["net"],
            candidate["sta"],
        )

        if station_key not in seen:
            selected.append(candidate)
            seen.add(station_key)

    return selected[:c.candidates], rejected


def check_raw(stream, start, end):
    import numpy as np

    if not stream:
        raise Failure("Sin muestras")

    if stream.get_gaps():
        raise Failure(
            "Huecos/solapamientos detectados; "
            "no se interpolan"
        )

    stream.merge(
        method=0,
        fill_value=None,
    )

    if len(stream) != 1:
        raise Failure(
            "Más de una traza incompatible"
        )

    trace = stream[0]

    if (
        trace.stats.starttime
        > start + trace.stats.delta
        or trace.stats.endtime
        < end - trace.stats.delta
    ):
        raise Failure(
            "Cobertura temporal incompleta"
        )

    data = trace.data

    if (
        np.ma.is_masked(data)
        or not np.isfinite(data).all()
        or len(data) < 100
    ):
        raise Failure(
            "Muestras ausentes/no finitas"
        )

    if np.ptp(data.astype(float)) == 0:
        raise Failure("Señal constante")

    # Heurística conservadora de clipping:
    # al menos tres muestras consecutivas en un extremo global.
    extreme = (
        (data == np.max(data))
        | (data == np.min(data))
    )

    if np.any(
        extreme[:-2]
        & extreme[1:-1]
        & extreme[2:]
    ):
        raise Failure(
            "Posible saturación: meseta en extremo"
        )

    if np.issubdtype(data.dtype, np.integer):
        limits = np.iinfo(data.dtype)

        if np.any(
            (data == limits.min)
            | (data == limits.max)
        ):
            raise Failure(
                "Saturación del contenedor digital"
            )

    return trace


def procesar_estacion(station, e, c, clock):
    import numpy as np
    from obspy import (
        read,
        read_inventory,
        UTCDateTime,
    )

    origin = UTCDateTime(e["origin"])
    start = origin - c.pre - c.margin
    end = origin + c.post + c.margin

    params = {
        "net": station["net"],
        "sta": station["sta"],
        "loc": station["loc"] or "--",
        "cha": station["cha"],
        "starttime": str(start),
        "endtime": str(end),
    }

    base = (
        c.fdsn.rstrip("/")
        + "/fdsnws/"
    )

    metadata = obtener(
        base + "station/1/query",
        c,
        clock,
        dict(
            params,
            level="response",
            format="xml",
        ),
    )

    if not metadata:
        raise Failure(
            "Sin respuesta instrumental"
        )

    inventory = read_inventory(
        io.BytesIO(metadata)
    )

    response = inventory.get_response(
        station["id"],
        origin,
    )

    sensitivity = response.instrument_sensitivity

    units = (
        sensitivity.input_units.upper().replace(" ", "")
        if sensitivity
        else ""
    )

    if (
        units not in {
            "M/S**2",
            "M/S^2",
            "M/S/S",
        }
        or not sensitivity
        or sensitivity.value <= 0
    ):
        raise Failure(
            "Respuesta no calibrada como acelerómetro"
        )

    if not response.response_stages:
        raise Failure(
            "Respuesta sin etapas; "
            "no se inventa factor de conversión"
        )

    raw = obtener(
        base + "dataselect/1/query",
        c,
        clock,
        params,
    )

    if not raw:
        raise Failure(
            "FDSN 204: sin registros"
        )

    stream = read(
        io.BytesIO(raw),
        format="MSEED",
    ).select(id=station["id"])

    trace = check_raw(
        stream,
        start,
        end,
    )

    if trace.stats.sampling_rate < 50:
        raise Failure(
            "Muestreo insuficiente para banda común"
        )

    trace.data = trace.data.astype(
        np.float64
    )

    trace.detrend("linear")

    trace.remove_response(
        inventory=inventory,
        output="ACC",
        pre_filt=(0.05, 0.1, 15, 20),
        water_level=None,
        zero_mean=True,
        taper=True,
        taper_fraction=0.05,
    )

    trace.filter(
        "bandpass",
        freqmin=0.1,
        freqmax=15,
        corners=4,
        zerophase=True,
    )

    trace.trim(
        origin - c.pre,
        origin + c.post,
        nearest_sample=False,
    )

    # Convención común: positivo hacia arriba.
    values = trace.data * (
        -1 if station["dip"] > 0 else 1
    )

    if not np.isfinite(values).all():
        raise Failure(
            "Resultado instrumental no finito"
        )

    times = trace.times() + float(
        trace.stats.starttime - origin
    )

    return dict(
        station,
        times=times,
        values=values,
        sample_rate=trace.stats.sampling_rate,
        pga_vertical=float(
            np.max(np.abs(values))
        ),
    )


def collect(candidates, processor, workers):
    valid = []
    rejected = []

    with futures.ThreadPoolExecutor(
        max_workers=workers
    ) as pool:
        tasks = {
            pool.submit(processor, station): station
            for station in candidates
        }

        for task in futures.as_completed(tasks):
            station = tasks[task]

            try:
                valid.append(task.result())

            except Exception as exc:
                rejected.append({
                    "station": station["id"],
                    "reason": clean(exc),
                })

    valid.sort(
        key=lambda station: station["distance"]
    )

    return valid, rejected


# ============================================================
# CARTOGRAFÍA
# ============================================================

def prepare_basemap(c, clock):
    """
    Descarga controlada de Natural Earth.
    Evita descargas implícitas de Cartopy sin nuestro timeout.
    """
    import cartopy

    root = (
        Path(cartopy.config["data_dir"])
        / "shapefiles"
        / "natural_earth"
    )

    datasets = (
        ("physical", "land"),
        ("physical", "ocean"),
        ("physical", "coastline"),
        ("cultural", "admin_0_boundary_lines_land"),
    )

    for category, name in datasets:
        stem = "ne_110m_" + name
        target = root / category

        required = (
            ".shp",
            ".shx",
            ".dbf",
        )

        if all(
            (target / (stem + ext)).is_file()
            for ext in required
        ):
            continue

        data = obtener(
            "https://naturalearth.s3.amazonaws.com/"
            f"110m_{category}/{stem}.zip",
            c,
            clock,
        )

        target.mkdir(
            parents=True,
            exist_ok=True,
        )

        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for ext in (
                ".shp",
                ".shx",
                ".dbf",
                ".prj",
                ".cpg",
            ):
                name_in_zip = stem + ext

                if name_in_zip in z.namelist():
                    (target / name_in_zip).write_bytes(
                        z.read(name_in_zip)
                    )

        if not all(
            (target / (stem + ext)).is_file()
            for ext in required
        ):
            raise Failure(
                "Cartografía incompleta"
            )


# ============================================================
# VIDEO: HEATMAP DE ÁREAS + GRÁFICO ÚNICO
# ============================================================

def generar_video(e, stations, c, clock, folder):
    import numpy as np
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    from matplotlib.colors import Normalize
    from matplotlib.cm import ScalarMappable
    from scipy.spatial import Delaunay, QhullError

    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    prepare_basemap(c, clock)

    # Número de cuadros fuente.
    n = round((c.pre + c.post) / c.step)

    # Cada cuadro representa un intervalo físico.
    # El cursor se sitúa en el centro del intervalo.
    times = (
        -c.pre
        + (np.arange(n) + 0.5) * c.step
    )

    amplitudes = np.empty(
        (len(stations), n)
    )

    # Métrica común:
    # máximo absoluto vertical en cada intervalo temporal.
    for i, station in enumerate(stations):
        for j, t in enumerate(times):
            selected = station["values"][
                (
                    station["times"]
                    >= t - c.step / 2
                )
                & (
                    station["times"]
                    < t + c.step / 2
                )
            ]

            if not len(selected):
                raise Failure(
                    "Intervalo visual sin muestras; "
                    "no se rellena"
                )

            amplitudes[i, j] = np.max(
                np.abs(selected)
            )

    vmax = float(amplitudes.max())

    if not math.isfinite(vmax) or vmax <= 0:
        raise Failure(
            "Amplitud física no utilizable"
        )

    longitudes = np.array([
        station["lon"]
        for station in stations
    ])

    latitudes = np.array([
        station["lat"]
        for station in stations
    ])

    extent = [
        min(longitudes.min(), e["lon"]) - 0.5,
        max(longitudes.max(), e["lon"]) + 0.5,
        min(latitudes.min(), e["lat"]) - 0.5,
        max(latitudes.max(), e["lat"]) + 0.5,
    ]

    # Malla más fina para visualizar áreas.
    gx, gy = np.meshgrid(
        np.linspace(*extent[:2], 180),
        np.linspace(*extent[2:], 180),
    )

    def xy(lon, lat):
        """
        Coordenadas locales aproximadas en km.
        Adecuadas para el radio regional configurado.
        """
        return np.column_stack((
            (
                np.ravel(lon) - e["lon"]
            )
            * 111.19
            * math.cos(math.radians(e["lat"])),

            (
                np.ravel(lat) - e["lat"]
            )
            * 111.19,
        ))

    points = xy(longitudes, latitudes)
    grid = xy(gx, gy)

    vertices = None
    weights = None
    mask = np.zeros(
        len(grid),
        dtype=bool,
    )

    coverage_reason = (
        "Menos de tres estaciones válidas"
    )

    if len(stations) >= 3:
        coverage_reason = (
            "Sin área que cumpla los límites "
            "de soporte espacial"
        )

        try:
            triangulation = Delaunay(points)

            simplex = triangulation.find_simplex(grid)
            safe = np.maximum(simplex, 0)

            vertices = triangulation.simplices[safe]

            delta = (
                grid
                - triangulation.transform[safe, 2]
            )

            barycentric = np.einsum(
                "ijk,ik->ij",
                triangulation.transform[safe, :2],
                delta,
            )

            weights = np.c_[
                barycentric,
                1 - barycentric.sum(axis=1),
            ]

            distances = np.linalg.norm(
                points[vertices] - grid[:, None, :],
                axis=2,
            )

            longest_edge = np.max(
                [
                    np.linalg.norm(
                        points[vertices[:, i]]
                        - points[vertices[:, j]],
                        axis=1,
                    )
                    for i, j in (
                        (0, 1),
                        (1, 2),
                        (2, 0),
                    )
                ],
                axis=0,
            )

            # No extrapolar fuera del conjunto de estaciones.
            # No rellenar triángulos demasiado extensos.
            mask = (
                (simplex >= 0)
                & (
                    distances.min(axis=1)
                    <= c.support
                )
                & (
                    longest_edge <= c.triangle
                )
            )

        except QhullError:
            coverage_reason = (
                "Estaciones alineadas "
                "o geometría degenerada"
            )

    has_area = bool(mask.any())

    if has_area:
        coverage_reason = (
            "Interpolación lineal dentro de "
            "triángulos con soporte"
        )

    log(
        f"Mapa: {len(stations)} estaciones; "
        f"heatmap de áreas={has_area}; "
        f"{coverage_reason}"
    )

    def field(frame_index):
        values = np.full(
            len(grid),
            np.nan,
        )

        if mask.any():
            values[mask] = np.sum(
                amplitudes[
                    vertices[mask],
                    frame_index,
                ]
                * weights[mask],
                axis=1,
            )

        return values.reshape(gx.shape)

    # ========================================================
    # DISEÑO: MAPA IZQUIERDA / GRÁFICO ÚNICO DERECHA
    # ========================================================

    fig = plt.figure(
        figsize=(12.8, 7.2),
        dpi=100,
        facecolor="white",
    )

    gs = fig.add_gridspec(
        1,
        2,
        left=.06,
        right=.97,
        bottom=.16,
        top=.84,
        width_ratios=[1, 1.15],
        hspace=.48,
        wspace=.30,
    )

    map_ax = fig.add_subplot(
        gs[:, 0],
        projection=ccrs.PlateCarree(),
    )

    map_ax.set_extent(extent)

    map_ax.add_feature(
        cfeature.LAND.with_scale("110m"),
        facecolor="#eee8da",
        zorder=0,
    )

    map_ax.add_feature(
        cfeature.OCEAN.with_scale("110m"),
        facecolor="#eef5fa",
        zorder=0,
    )

    map_ax.coastlines(
        "110m",
        linewidth=.6,
        zorder=3,
    )

    map_ax.add_feature(
        cfeature.BORDERS.with_scale("110m"),
        linewidth=.4,
        zorder=3,
    )

    gridlines = map_ax.gridlines(
        draw_labels=True,
        linewidth=.3,
        alpha=.5,
    )

    gridlines.top_labels = False
    gridlines.right_labels = False
    gridlines.xlabel_style = {"size": 8}
    gridlines.ylabel_style = {"size": 8}

    # Escala fija para todos los cuadros.
    norm = Normalize(0, vmax)

    mesh = map_ax.pcolormesh(
        gx,
        gy,
        np.ma.masked_invalid(field(0)),
        cmap="jet",
        norm=norm,
        alpha=.85,
        shading="auto",
        edgecolors="none",
        zorder=2,
    )

    line_colors = [
        plt.get_cmap("tab10")(i % 10)
        for i in range(len(stations))
    ]

    if has_area:
        # Las áreas muestran amplitud.
        # Los bordes de estaciones identifican sus curvas.
        dots = map_ax.scatter(
            longitudes,
            latitudes,
            facecolors="white",
            edgecolors=line_colors,
            marker="^",
            linewidths=1.6,
            s=55,
            zorder=5,
        )

    else:
        # Sin soporte para áreas, mostrar sólo mediciones.
        dots = map_ax.scatter(
            longitudes,
            latitudes,
            c=amplitudes[:, 0],
            cmap="jet",
            norm=norm,
            edgecolors="black",
            s=55,
            zorder=5,
        )

    map_ax.scatter(
        [e["lon"]],
        [e["lat"]],
        marker="*",
        c="magenta",
        edgecolors="black",
        s=180,
        zorder=6,
    )

    map_ax.text(
        e["lon"] + .03,
        e["lat"] - .05,
        "Epicentro",
        fontsize=8,
        color="purple",
        zorder=7,
    )

    for station in stations:
        map_ax.text(
            station["lon"] + .025,
            station["lat"] + .025,
            station["sta"],
            fontsize=7,
            zorder=7,
        )

    colorbar = fig.colorbar(
        ScalarMappable(
            norm=norm,
            cmap="jet",
        ),
        ax=map_ax,
        orientation="horizontal",
        pad=.07,
        fraction=.05,
    )

    colorbar.set_label(
        f"Máx. |a vertical| por {c.step:g} s "
        "[m/s²] · escala fija",
        fontsize=8,
    )

    colorbar.ax.tick_params(labelsize=8)

    # ========================================================
    # UN SOLO GRÁFICO PARA TODAS LAS ESTACIONES
    # ========================================================

    signal_ax = fig.add_subplot(gs[0, 1])

    ymax = max(
        float(
            np.max(
                np.abs(station["values"])
            )
        )
        for station in stations
    ) * 1.08

    for i, station in enumerate(stations):
        signal_ax.plot(
            station["times"],
            station["values"],
            color=line_colors[i],
            linewidth=.8,
            alpha=.85,
            label=(
                f"{station['id']} · "
                f"{station['distance']:.0f} km"
            ),
        )

    signal_ax.axvline(
        0,
        color="black",
        linestyle="--",
        linewidth=.8,
        alpha=.65,
    )

    cursors = [
        signal_ax.axvline(
            times[0],
            color="crimson",
            linewidth=1.5,
            zorder=10,
        )
    ]

    signal_ax.set(
        xlim=(-c.pre, c.post),
        ylim=(-ymax, ymax),
        xlabel="Tiempo relativo al origen [s]",
        ylabel="Aceleración vertical [m/s²]",
    )

    signal_ax.set_title(
        "Aceleración observada · todas las estaciones\n"
        "Componente vertical · banda 0.1–15 Hz",
        fontsize=10,
    )

    signal_ax.tick_params(labelsize=8)
    signal_ax.grid(alpha=.25)

    signal_ax.legend(
        loc="upper right",
        fontsize=7,
        framealpha=.9,
        ncol=1,
    )

    # ========================================================
    # ENCABEZADO Y NOTAS
    # ========================================================

    depth = (
        "no informada"
        if e["depth"] is None
        else f"{e['depth']:g} km"
    )

    local_time = date(e["origin"]).astimezone(
        ZoneInfo("America/Santiago")
    )

    fig.suptitle(
        f"Sismo M {e['mag_display']} | "
        f"{e['reference'][:85]}\n"
        f"{local_time:%d/%m/%Y %H:%M:%S}"
        f" · Profundidad: {depth}",
        fontsize=12,
        y=.96,
    )

    time_label = fig.text(
        .06,
        .875,
        "",
        fontsize=10,
    )

    coverage = (
        "Heatmap JET de áreas con soporte"
        if has_area
        else (
            "Cobertura insuficiente: "
            "sólo mediciones en estaciones"
        )
    )

    fig.text(
        .04,
        .07,
        f"{coverage}. "
        "No es intensidad oficial ni pronóstico.\n"
        "Aceleración vertical observada, banda 0.1–15 Hz; "
        "el color entre estaciones es una estimación.",
        fontsize=8,
    )

    fig.text(
        .04,
        .022,
        "Fuente: Centro Sismológico Nacional "
        "de la Universidad de Chile · Visualización propia",
        fontsize=8,
    )

    # ========================================================
    # CODIFICACIÓN
    # ========================================================

    video = folder / "video.mp4"

    writer = FFMpegWriter(
        fps=c.fps,
        codec="libx264",
        bitrate=5000,
        extra_args=[
            "-vf", "fps=30,format=yuv420p",
            "-profile:v", "high",
            "-g", "60",
            "-flags", "+cgop",
            "-movflags", "+faststart",
        ],
    )

    peak_frame = int(
        np.argmax(
            amplitudes.max(axis=0)
        )
    )

    try:
        with writer.saving(
            fig,
            str(video),
            dpi=100,
        ):
            for j, t in enumerate(times):
                clock.check(10)

                if not has_area:
                    dots.set_array(
                        amplitudes[:, j]
                    )

                mesh.set_array(
                    np.ma.masked_invalid(
                        field(j)
                    ).ravel()
                )

                for cursor in cursors:
                    cursor.set_xdata([t, t])

                time_label.set_text(
                    f"t = {t:+.1f} s · "
                    f"reproducción ×{c.step*c.fps:g}"
                )

                if j == peak_frame:
                    fig.savefig(
                        folder / "preview.png",
                        dpi=100,
                    )

                writer.grab_frame()

                if j % 30 == 0:
                    log(
                        f"Render {j+1}/{n} cuadros"
                    )

    finally:
        plt.close(fig)

    # ========================================================
    # VALIDACIÓN DEL MP4
    # ========================================================

    probe = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_streams",
            "-show_format",
            "-of", "json",
            str(video),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )

    info = json.loads(probe.stdout)

    video_stream = next(
        stream
        for stream in info["streams"]
        if stream["codec_type"] == "video"
    )

    duration = float(
        info["format"]["duration"]
    )

    if (
        video_stream["codec_name"] != "h264"
        or video_stream["pix_fmt"] != "yuv420p"
        or (
            video_stream["width"],
            video_stream["height"],
        ) != (1280, 720)
        or abs(duration - n / c.fps) > .15
        or not .5 <= duration <= 140
        or video.stat().st_size > 512 * 1024 * 1024
    ):
        raise Failure(
            "MP4 no supera validación ffprobe"
        )

    return {
        "physical_seconds": c.pre + c.post,
        "frame_step_seconds": c.step,
        "render_fps": c.fps,
        "encoded_fps": 30,
        "duration_seconds": duration,
        "speed_factor": c.step * c.fps,
        "bytes": video.stat().st_size,
        "color_max_m_s2": vmax,
        "interpolation": has_area,
        "interpolation_reason": coverage_reason,
        "stations_used": len(stations),
        "grid_cells_with_support": int(mask.sum()),
        "signal_layout": "single_overlay",
        "ffprobe": info,
    }


# ============================================================
# SUBPROCESO DE DESCARGA Y RENDER
# ============================================================

def render_worker(event_path, c):
    event_path = Path(event_path)

    e = validate_event(
        json.loads(event_path.read_text())
    )

    folder = event_path.parent
    clock = Clock(c.event_budget)

    quality = {
        "event": e,
        "metric": (
            "vertical acceleration m/s^2, 0.1-15 Hz"
        ),
        "clipping_test": (
            "heuristic, not proof of absence "
            "of sensor saturation"
        ),
    }

    try:
        candidates, excluded = obtener_estaciones(
            e,
            c,
            clock,
        )

        valid, rejected = collect(
            candidates,
            lambda station: procesar_estacion(
                station,
                e,
                c,
                clock,
            ),
            c.workers,
        )

        quality.update(
            attempted=len(candidates),
            valid=len(valid),
            rejected=excluded + rejected,
        )

        log(
            f"Estaciones intentadas={len(candidates)}, "
            f"válidas={len(valid)}, "
            f"rechazadas={len(rejected)}"
        )

        if not valid:
            raise Failure(
                "Ausencia total de aceleración válida; "
                "conservar pendiente"
            )

        valid = valid[:c.stations]

        quality["used"] = [
            {
                name: value
                for name, value in station.items()
                if name not in {
                    "times",
                    "values",
                }
            }
            for station in valid
        ]

        quality["video"] = generar_video(
            e,
            valid,
            c,
            clock,
            folder,
        )

        atomic(
            folder / "quality.json",
            quality,
        )

        return 0

    except Exception as exc:
        quality["error"] = clean(exc)

        atomic(
            folder / "quality.json",
            quality,
        )

        log(
            "Descarga/procesamiento/render: "
            f"{type(exc).__name__}: {clean(exc)}"
        )

        return 1


def build_video(e, c, clock):
    clock.check(180)

    folder = Path(c.output) / e["id"]
    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    event_path = folder / "event.json"
    atomic(event_path, e)

    video = folder / "video.mp4"

    # Nunca reutilizar archivos de un intento anterior.
    for name in (
        "video.mp4",
        "preview.png",
        "quality.json",
    ):
        (folder / name).unlink(
            missing_ok=True
        )

    env = dict(os.environ)

    # El render no necesita acceso a credenciales.
    for name in SECRET_NAMES:
        env.pop(name, None)

    limit = min(
        c.event_budget,
        clock.remaining() - 120,
    )

    env["EVENT_TIMEOUT_SEC"] = str(limit)

    try:
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--render",
                str(event_path.resolve()),
            ],
            env=env,
            timeout=limit,
            check=False,
        )

    except subprocess.TimeoutExpired:
        raise Failure(
            "Timeout de descarga/render; "
            "evento pendiente"
        ) from None

    if (
        process.returncode
        or not video.is_file()
    ):
        raise Failure(
            "Fallo de aceleración/video; "
            "revisar quality.json"
        )

    log(
        f"Video validado: {e['id']}, "
        f"{video.stat().st_size} bytes"
    )

    return video


# ============================================================
# CICLO PRINCIPAL
# ============================================================

def run(c, init=False):
    # Esta ventana permanece fija durante toda la ejecución.
    end = utcnow()
    start = end - timedelta(
        hours=c.lookback
    )

    clock = Clock(c.budget)

    report = {
        "start_utc": iso(start),
        "end_utc": iso(end),
        "mode": (
            "live" if c.publish else "simulation"
        ),
        "found": 0,
        "new": 0,
        "pending": 0,
        "skipped": 0,
        "expired": 0,
        "results": [],
        "errors": [],
    }

    code = 0

    Path(c.output).mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        log(
            f"Ventana fija: {iso(start)} "
            f"a {iso(end)}; M >= {c.mag}"
        )

        # Recuperar estado antes de consultar o decidir que no hay trabajo.
        store = Store(c)
        state = store.load(init)

        report.update(
            recover(state, start, end)
        )

        store.save(state)

        x = (
            XClient(c, clock)
            if c.publish
            else None
        )

        if x:
            account = x.identity()

            if state["account_id"] not in {
                None,
                account,
            }:
                raise PersistenceError(
                    "Cuenta X distinta de la "
                    "asociada al estado real"
                )

            if state["account_id"] is None:
                state["account_id"] = account
                store.save(state)

            if c.reconcile:
                x.reconcile(state)
                store.save(state)

        events, source = discover(
            c,
            clock,
            start,
            end,
        )

        report["source"] = source
        report["found"] = len(events)

        log(
            f"Consulta CSN: {source['status']}; "
            f"eventos elegibles={len(events)}"
        )

        if source["status"] != "valida":
            code = 1
            report["errors"].append(
                "Consulta CSN incompleta; "
                "pendientes se recuperan igualmente"
            )

        for event in events:
            if event["id"] not in state["events"]:
                state["events"][event["id"]] = record(
                    event,
                    end,
                )
                report["new"] += 1

        store.save(state)

        report["pending"] = sum(
            r["status"] == "pendiente"
            for r in state["events"].values()
        )

        queue = sorted(
            state["events"].values(),
            key=lambda r: r["event"]["origin"],
        )

        processed = 0

        for r in queue:
            if r["status"] == "resultado_incierto":
                code = 1
                report["errors"].append(
                    f"{r['event']['id']}: "
                    "resultado incierto bloqueado"
                )

            if (
                r["status"] == "pendiente"
                and r["attempts"] >= c.max_attempts
            ):
                code = 1
                report["errors"].append(
                    f"{r['event']['id']}: "
                    "límite de intentos; permanece "
                    "bloqueado hasta expirar"
                )

            if not eligible(
                r,
                c,
                start,
                end,
            ):
                report["skipped"] += 1
                continue

            if defer_recent(r, c, end):
                store.save(state)
                report["skipped"] += 1

                log(
                    f"{r['event']['id']}: "
                    f"aplazado hasta {r['next_attempt']}"
                )
                continue

            if processed >= c.max_events:
                break

            clock.check(180)
            processed += 1

            r.update(
                status="procesando",
                attempts=r["attempts"] + 1,
                updated=iso(utcnow()),
            )

            store.save(state)

            try:
                text = tweet_text(r["event"])

                video = build_video(
                    r["event"],
                    c,
                    clock,
                )

                r["text"] = text

                if x:
                    media_id = x.upload(video)

                    send_transaction(
                        store,
                        state,
                        r,
                        x,
                        text,
                        media_id,
                    )

                else:
                    r.update(
                        status="simulado",
                        error="",
                        updated=iso(utcnow()),
                    )

                    store.save(state)

                    log(
                        f"Simulado {r['event']['id']}; "
                        "sin llamadas a X"
                    )

                report["results"].append({
                    "id": r["event"]["id"],
                    "status": r["status"],
                    "tweet_id": r["tweet_id"],
                })

            except PersistenceError:
                # No sobrescribir estado para reparar un CAS fallido.
                raise

            except XError as exc:
                code = 1
                report["errors"].append(
                    clean(exc)
                )

                if r["status"] == "procesando":
                    retry(
                        r,
                        c,
                        exc,
                        exc.reset,
                    )
                    store.save(state)

                if exc.global_stop:
                    break

            except Exception as exc:
                code = 1

                # Nunca devolver un envío ambiguo a la cola.
                if r["status"] in {
                    "enviando",
                    "resultado_incierto",
                    "publicado",
                }:
                    if r["status"] == "enviando":
                        r.update(
                            status="resultado_incierto",
                            error=clean(exc),
                        )
                        store.save(state)
                    raise

                retry(r, c, exc)
                store.save(state)

                report["errors"].append(
                    f"{r['event']['id']}: "
                    f"{clean(exc)}"
                )

        if (
            report["found"] == 0
            and report["pending"] == 0
            and source["status"] == "valida"
        ):
            log(
                "Consulta válida sin eventos "
                "nuevos ni pendientes"
            )

    except Exception as exc:
        code = 1

        report["errors"].append(
            f"{type(exc).__name__}: "
            f"{clean(exc)}"
        )

        log(report["errors"][-1])

    finally:
        report["exit_code"] = code

        atomic(
            Path(c.output) / "summary.json",
            report,
        )

        summary = (
            "## CSN → aceleración → X\n\n"
            "```json\n"
            + json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            )
            + "\n```\n"
        )

        Path(
            c.output,
            "summary.md",
        ).write_text(
            summary,
            encoding="utf-8",
        )

        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(
                os.environ["GITHUB_STEP_SUMMARY"],
                "a",
                encoding="utf-8",
            ) as f:
                f.write(summary)

        log(
            f"Ciclo terminado: exit={code}; "
            f"nuevos={report['new']}; "
            f"pendientes={report['pending']}"
        )

    return code


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--init-state",
        action="store_true",
    )

    parser.add_argument("--render")

    args = parser.parse_args()

    try:
        config = Config.env()

        if args.render:
            return render_worker(
                args.render,
                config,
            )

        return run(
            config,
            args.init_state,
        )

    except Exception as exc:
        log(
            f"Configuración: {type(exc).__name__}: "
            f"{clean(exc)}"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
