#!/usr/bin/env python3
"""CSN -> peaks de aceleración horizontal observada -> MP4 -> X. Un ciclo, estado CAS.
Python 3.11+. Pruebas: pytest test_monitor.py. --init-state inicializa explícitamente.
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
STATES = {"pendiente", "procesando", "enviando", "publicado", "simulado",
          "resultado_incierto", "expirado"}
TERMINAL = {"publicado", "simulado", "resultado_incierto", "expirado"}
SECRET_NAMES = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN",
                "X_ACCESS_TOKEN_SECRET", "GITHUB_TOKEN")


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
    s = str(value)
    for name in SECRET_NAMES:
        secret = os.getenv(name, "")
        if secret:
            s = s.replace(secret, "[REDACTADO]")
    s = re.sub(r"(?i)(bearer|oauth_token|oauth_signature|authorization)\s*[:=]?\s*[^\s,]+",
               r"\1 [REDACTADO]", s)
    return s.replace("\r", " ").replace("\n", " ")[:600]


def log(message):
    print(f"{iso(utcnow())} {clean(message)}", flush=True)


def boolean(name, default=False):
    s = os.getenv(name, str(default)).strip().lower()
    if s not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"Booleano inválido: {name}")
    return s in {"true", "1", "yes"}


@dataclass
class Config:
    mag: float = 4.0
    lookback: float = 24
    pre: float = 60
    post: float = 120
    margin: float = 30
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
    stations: int = 15
    candidates: int = 30
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
        names = {"mag": "MAG_MIN", "lookback": "LOOKBACK_HOURS", "pre": "PRE_SEG",
                 "post": "POST_SEG", "margin": "MARGEN_SEG", "latency": "DATA_LATENCY_SEC",
                 "fps": "FPS", "step": "FRAME_STEP_SEC", "workers": "DOWNLOAD_WORKERS",
                 "timeout": "HTTP_TIMEOUT", "max_events": "MAX_EVENTS_PER_RUN",
                 "budget": "MAX_RUNTIME_SEC", "event_budget": "EVENT_TIMEOUT_SEC",
                 "x_wait": "X_PROCESS_TIMEOUT_SEC", "radius": "MAX_DISTANCE_KM",
                 "stations": "MAX_STATIONS", "candidates": "MAX_CANDIDATES",
                 "support": "SUPPORT_KM", "triangle": "MAX_TRIANGLE_KM",
                 "max_attempts": "MAX_ATTEMPTS", "backoff": "RETRY_BASE_SEC",
                 "state_dir": "STATE_DIR", "output": "OUTPUT_DIR", "branch": "STATE_BRANCH",
                 "fdsn": "FDSN_BASE"}
        for field, env in names.items():
            setattr(c, field, (float if cls.__annotations__[field] in (float, "float") else type(getattr(c, field)))(os.getenv(env, getattr(c, field))))
        c.publish = boolean("PUBLISH_TO_X")
        c.reconcile = boolean("RECONCILE_X")
        for field in names:
            v = getattr(c, field)
            if isinstance(v, (float, int)) and (not math.isfinite(v) or v <= 0):
                raise ValueError(f"Configuración positiva/finita requerida: {field}")
        if c.x_wait > 900 or c.margin < 30 or c.budget < 180:
            raise ValueError("X_PROCESS_TIMEOUT_SEC <=900; MARGEN_SEG >=30; presupuesto >=180")
        if c.stations > 30 or c.candidates < c.stations or c.workers > 8:
            raise ValueError("MAX_STATIONS <=30; candidatos >= estaciones; workers <=8")
        n = (c.pre + c.post) / c.step
        if abs(n - round(n)) > 1e-6 or not 0.5 <= n / c.fps <= 140:
            raise ValueError("Ventana/paso debe ser entero; video conservador de 0.5 a 140 s")
        return c


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
    def __init__(self, message, status=0, uncertain=False, reset=None):
        super().__init__(message)
        self.status, self.uncertain, self.reset = status, uncertain, reset
        self.global_stop = status in {401, 402, 403, 429} or status >= 500 or uncertain


class Clock:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds

    def remaining(self):
        return self.end - time.monotonic()

    def check(self, reserve=0):
        if self.remaining() <= reserve:
            raise BudgetError("Presupuesto agotado; trabajo conservado para próximo ciclo")


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        tmp = f.name
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def obtener(url, c, clock, params=None):
    """GET idempotente: 3 intentos como máximo, sin ocultar fallos."""
    for attempt in range(3):
        clock.check(5)
        try:
            with requests.get(url, params=params, timeout=min(c.timeout, clock.remaining()-2),
                              headers={"User-Agent": "CSN-observed-motion/1.0"}, stream=True) as r:
                if r.status_code == 204:
                    return b""
                if r.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    raw_delay = r.headers.get("Retry-After", "2")
                    try:
                        wait = float(raw_delay)
                    except ValueError:
                        try:
                            wait = (parsedate_to_datetime(raw_delay)-utcnow()).total_seconds()
                        except Exception:
                            wait = 2
                    delay = max(2 ** attempt, wait)
                    if delay > 30 or delay + 5 >= clock.remaining():
                        raise SourceError(f"GET HTTP {r.status_code}: espera diferida")
                    time.sleep(delay)
                    continue
                if r.status_code != 200:
                    raise SourceError(f"GET {urlparse(url).netloc}{urlparse(url).path}: HTTP {r.status_code}")
                chunks, size = [], 0
                for chunk in r.iter_content(65536):
                    clock.check(2)
                    size += len(chunk)
                    if size > 100 * 1024 * 1024:
                        raise SourceError("Respuesta excede 100 MiB")
                    chunks.append(chunk)
                return b"".join(chunks)
        except requests.RequestException as exc:
            if attempt == 2:
                raise SourceError(f"GET {urlparse(url).netloc}: {type(exc).__name__}") from None
            time.sleep(2 ** attempt)
    raise SourceError("GET sin resultado")


def key(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", s.lower())
                   if unicodedata.category(ch) != "Mn").strip()


def numero(s):
    m = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(s))
    if not m:
        raise StructureError("Valor numérico ausente")
    x = float(m.group().replace(",", "."))
    if not math.isfinite(x):
        raise StructureError("Número no finito")
    return x


def validate_event(e):
    for name in ("id", "origin", "mag_display", "reference", "url"):
        if not isinstance(e.get(name), str) or not e[name]:
            raise ValueError(f"Evento sin {name}")
    if not re.fullmatch(r"\d+", e["id"]):
        raise ValueError("ID CSN inválido")
    date(e["origin"])
    for name, lo, hi in (("lat", -90, 90), ("lon", -180, 180), ("mag", -2, 10)):
        if isinstance(e.get(name), bool) or not isinstance(e.get(name), (float, int)):
            raise ValueError(f"Evento sin {name} numérico")
        if not math.isfinite(e[name]) or not lo <= e[name] <= hi:
            raise ValueError(f"Evento fuera de rango: {name}")
    if "depth" not in e:
        raise ValueError("Campo profundidad ausente")
    if e.get("depth") is not None and (not math.isfinite(e["depth"]) or not 0 <= e["depth"] <= 800):
        raise ValueError("Profundidad inválida")
    u = urlparse(e["url"])
    if u.scheme != "https" or u.hostname != "www.sismologia.cl" or not re.fullmatch(
            r"/sismicidad/informes/\d{4}/\d{2}/" + e["id"] + r"\.html", u.path):
        raise ValueError("URL CSN inválida")
    return e


def leer_evento(html, url):
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for row in soup.select("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) == 2:
            fields[key(cells[0].get_text(" ", strip=True))] = cells[1].get_text(" ", strip=True)
    try:
        origin = datetime.strptime(fields["hora utc"], "%H:%M:%S %d/%m/%Y").replace(tzinfo=UTC)
        depth = fields.get("profundidad", "")
        e = {"id": Path(urlparse(url).path).stem, "origin": iso(origin),
             "mag": numero(fields["magnitud"]), "mag_display": f"{numero(fields['magnitud']):.1f}",
             "reference": fields["referencia"], "lat": numero(fields["latitud"]),
             "lon": numero(fields["longitud"]),
             "depth": numero(depth) if re.search(r"\d", depth) else None, "url": url}
        return validate_event(e)
    except (KeyError, ValueError, TypeError) as exc:
        raise StructureError(f"Informe incompatible {url}: {type(exc).__name__}") from None


def catalogue_links(html, url, daily, c):
    soup = BeautifulSoup(html, "html.parser")
    tables = [t for t in soup.find_all("table") if any("magnitud" in key(h.get_text())
                                                          for h in t.find_all("th"))]
    if not tables or (daily and "utc" not in key(soup.get_text(" "))):
        raise StructureError("Catálogo sin tabla/convención UTC reconocible")
    links = set()
    for table in tables:
        headers = [key(h.get_text(" ", strip=True)) for h in table.find_all("th")]
        mi = next(i for i, h in enumerate(headers) if "magnitud" in h)
        if daily and not any("fecha utc" in h for h in headers):
            raise StructureError("Catálogo diario perdió columna Fecha UTC")
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            if len(cells) != len(headers):
                raise StructureError("Fila de catálogo incompatible")
            magnitude = numero(cells[mi].get_text(" ", strip=True))
            anchors = [a for a in row.find_all("a", href=True)
                       if re.search(r"/sismicidad/informes/\d{4}/\d{2}/\d+\.html$",
                                    urljoin(url, a["href"]))]
            if not anchors:
                raise StructureError("Fila sin enlace a informe")
            if magnitude >= c.mag:
                for a in anchors:
                    u = urlparse(urljoin(url, a["href"]))
                    if u.hostname not in {"sismologia.cl", "www.sismologia.cl"}:
                        raise StructureError("Enlace fuera de CSN")
                    links.add(CSN + u.path)
    return links


def homepage_covers(html, beginning):
    """Respaldo acotado: lista válida y ordenada que alcanza el intervalo faltante.

    La portada usa hora local. Rechazar horas ambiguas/inexistentes de DST
    en lugar de adivinar su UTC. No filtrar por magnitud para medir cobertura.
    """
    soup = BeautifulSoup(html, "html.parser")
    timestamps = []
    zone = ZoneInfo("America/Santiago")
    tables = [t for t in soup.find_all("table")
              if any("magnitud" in key(h.get_text()) for h in t.find_all("th"))]
    if len(tables) != 1:
        return False
    for row in tables[0].find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        match = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", cells[0].get_text(" ", strip=True))
        if not match:
            return False
        try:
            naive = datetime.strptime(match.group(), "%Y-%m-%d %H:%M:%S")
            first = naive.replace(tzinfo=zone, fold=0)
            second = naive.replace(tzinfo=zone, fold=1)
            if first.utcoffset() != second.utcoffset():
                return False
            instant = first.astimezone(timezone.utc)
            if instant.astimezone(zone).replace(tzinfo=None) != naive:
                return False
            timestamps.append(instant)
        except ValueError:
            return False
    return bool(timestamps and timestamps[-1] <= beginning
                and all(a >= b for a, b in zip(timestamps, timestamps[1:])))


def discover(c, clock, start, end):
    urls = [CSN + "/"]
    day = start.date()
    while day <= end.date():
        urls.append(CSN + day.strftime("/sismicidad/catalogo/%Y/%m/%Y%m%d.html"))
        day += timedelta(days=1)
    links, events, errors, valid = set(), {}, [], 0
    homepage, warnings = None, []
    for i, url in enumerate(urls):
        try:
            html = obtener(url, c, clock)
            links.update(catalogue_links(html, url, i > 0, c))
            if i == 0:
                homepage = html
            valid += 1
        except (SourceError, BudgetError) as exc:
            detail = {"url": url, "type": type(exc).__name__, "error": clean(exc)}
            current_url = CSN + end.strftime("/sismicidad/catalogo/%Y/%m/%Y%m%d.html")
            beginning = max(start, end.replace(hour=0, minute=0, second=0, microsecond=0))
            if (url == current_url and homepage is not None
                    and re.search(r"HTTP (403|404)\b", str(exc))
                    and homepage_covers(homepage, beginning)):
                detail["fallback"] = "Portada válida cubre desde el inicio del intervalo UTC faltante"
                warnings.append(detail)
                log(f"CSN: respaldo por portada para {url}; {clean(exc)}")
            else:
                errors.append(detail)
                log(f"CSN: consulta incompleta {url}; {clean(exc)}")
    for url in sorted(links):
        try:
            e = leer_evento(obtener(url, c, clock), url)
            if start <= date(e["origin"]) <= end and e["mag"] >= c.mag:
                events[e["id"]] = e
        except (SourceError, BudgetError) as exc:
            errors.append({"url": url, "type": type(exc).__name__, "error": clean(exc)})
    status = "valida" if not errors else "parcial" if valid else (
        "incompatible" if any(x["type"] == "StructureError" for x in errors) else "inaccesible")
    return list(events.values()), {"status": status, "pages_ok": valid, "pages": len(urls), "errors": errors, "warnings": warnings}


def empty_state(mode):
    return {"schema": 1, "mode": mode, "account_id": None, "revision": str(uuid.uuid4()),
            "updated": iso(utcnow()), "events": {}}


def validate_state(s, mode):
    try:
        if s["schema"] != 1 or s["mode"] != mode or not isinstance(s["events"], dict):
            raise ValueError("Esquema/modo incorrecto")
        if not isinstance(s["revision"], str):
            raise ValueError("Revisión ausente")
        date(s["updated"])
        if s["account_id"] is not None and not re.fullmatch(r"\d+", s["account_id"]):
            raise ValueError("Cuenta inválida")
        for eid, r in s["events"].items():
            validate_event(r["event"])
            if eid != r["event"]["id"] or r["status"] not in STATES:
                raise ValueError("Registro inválido")
            if not isinstance(r["attempts"], int) or r["attempts"] < 0:
                raise ValueError("Intentos inválidos")
            for name in ("next_attempt", "updated", "created"):
                date(r[name])
            for name in ("media_id", "tweet_id", "text", "error"):
                if name not in r:
                    raise ValueError("Campo de estado ausente")
            if r["status"] == "publicado" and not re.fullmatch(r"\d+", str(r["tweet_id"])):
                raise ValueError("Publicado sin tweet ID")
            if mode == "live" and r["status"] == "simulado":
                raise ValueError("Estado simulado en producción")
        return s
    except (KeyError, ValueError, TypeError) as exc:
        raise PersistenceError(f"Estado corrupto: {clean(exc)}") from None


class Store:
    """Contents API: un archivo por modo, actualización CAS por SHA, sin cache autoritativa."""
    def __init__(self, c):
        self.c, self.mode = c, "live" if c.publish else "simulation"
        self.path = Path(c.state_dir) / (self.mode + ".json")
        self.repo = os.getenv("GITHUB_REPOSITORY", "")
        self.token = os.getenv("GITHUB_TOKEN", "")
        self.sha = None
        if c.publish and (not self.repo or not self.token):
            raise PersistenceError("Publicación requiere GITHUB_REPOSITORY y GITHUB_TOKEN")
        self.remote = bool(self.repo and self.token)
        self.base = f"https://api.github.com/repos/{self.repo}"
        self.file = "/contents/state/" + self.mode + ".json"

    def api(self, method, path, **kwargs):
        try:
            return requests.request(method, self.base + path, timeout=self.c.timeout,
                                    headers={"Authorization": "Bearer " + self.token,
                                             "Accept": "application/vnd.github+json",
                                             "X-GitHub-Api-Version": "2022-11-28"}, **kwargs)
        except requests.RequestException as exc:
            raise PersistenceError(f"GitHub {method}: {type(exc).__name__}") from None

    def load(self, init=False):
        if not self.remote:
            if not self.path.exists():
                s = empty_state(self.mode)
            else:
                try:
                    s = json.loads(self.path.read_text())
                except Exception:
                    raise PersistenceError("JSON local ilegible") from None
        else:
            r = self.api("GET", self.file, params={"ref": self.c.branch})
            if r.status_code == 404 and init:
                branch = self.api("GET", "/git/ref/heads/" + quote(self.c.branch, safe=""))
                if branch.status_code == 404:
                    repo = self.api("GET", "")
                    if repo.status_code != 200:
                        raise PersistenceError(f"Repositorio: HTTP {repo.status_code}")
                    ref = self.api("GET", "/git/ref/heads/" + repo.json()["default_branch"])
                    if ref.status_code != 200:
                        raise PersistenceError("No se pudo leer rama predeterminada")
                    created = self.api("POST", "/git/refs", json={"ref": "refs/heads/" + self.c.branch,
                                                                "sha": ref.json()["object"]["sha"]})
                    if created.status_code != 201:
                        raise PersistenceError(f"Crear rama: HTTP {created.status_code}")
                elif branch.status_code != 200:
                    raise PersistenceError(f"Leer rama: HTTP {branch.status_code}")
                s = empty_state(self.mode)
                self.save(s)
            elif r.status_code == 200:
                try:
                    body = r.json()
                    self.sha = body["sha"]
                    s = json.loads(base64.b64decode(body["content"], validate=False))
                except Exception:
                    raise PersistenceError("Estado remoto ilegible; publicación bloqueada") from None
            else:
                raise PersistenceError(f"Leer estado: HTTP {r.status_code}; inicialice explícitamente si es nuevo")
        validate_state(s, self.mode)
        atomic(self.path, s)
        return s

    def save(self, s):
        try:
            self._save(s)
        except PersistenceError:
            raise
        except Exception as exc:
            raise PersistenceError(f"Guardado fallido: {type(exc).__name__}: {clean(exc)}") from None

    def _save(self, s):
        s["updated"], s["revision"] = iso(utcnow()), str(uuid.uuid4())
        validate_state(s, self.mode)
        atomic(self.path, s)
        if not self.remote:
            return
        raw = json.dumps(s, ensure_ascii=False, allow_nan=False).encode()
        if len(raw) > 900000:
            raise PersistenceError("Estado supera límite conservador; archivar tombstones sin perder IDs")
        body = {"message": "monitor state [skip ci]", "branch": self.c.branch,
                "content": base64.b64encode(raw).decode()}
        if self.sha:
            body["sha"] = self.sha
        # Nunca se repite un PUT ambiguo ni se sustituye el SHA tras un conflicto.
        r = self.api("PUT", self.file, json=body)
        if r.status_code not in {200, 201}:
            raise PersistenceError(f"Guardar estado CAS: HTTP {r.status_code}; se detiene")
        # Verificar el commit inmutable devuelto por PUT, no volver a resolver
        # el nombre mutable de la rama inmediatamente después de escribir.
        try:
            result = r.json()
            commit_sha = result["commit"]["sha"]
            blob_sha = result["content"]["sha"]
            if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40,64}", value)
                       for value in (commit_sha, blob_sha)):
                raise ValueError("SHA inválido")
        except (ValueError, KeyError, TypeError):
            raise PersistenceError(
                f"GitHub PUT HTTP {r.status_code}: respuesta sin SHA de commit/blob válido; "
                "guardado no confirmado, no se repite PUT") from None
        last_error = ""
        for attempt in range(3):
            if attempt:
                time.sleep(attempt)
            try:
                confirm = self.api("GET", self.file, params={"ref": commit_sha})
            except PersistenceError as exc:
                last_error = str(exc)
                continue
            if confirm.status_code != 200:
                last_error = f"GET de verificación HTTP {confirm.status_code}"
                if confirm.status_code == 404 or 500 <= confirm.status_code <= 599:
                    continue
                # Los límites y permisos se informan y detienen; no esperar
                # indiscriminadamente ni repetir operaciones de escritura.
                raise PersistenceError(last_error + "; publicación bloqueada")
            try:
                b = confirm.json()
                actual = json.loads(base64.b64decode(b["content"]))
                matches = b["sha"] == blob_sha and actual == s
            except (ValueError, KeyError, TypeError):
                last_error = "GET HTTP 200: contenido JSON/base64 ilegible"
                continue
            if not matches:
                raise PersistenceError(
                    "GET HTTP 200: SHA o contenido no coincide con el commit guardado; "
                    "publicación bloqueada")
            self.sha = blob_sha
            log(f"Persistencia confirmada: {self.mode}, revisión {s['revision']}, "
                f"commit {commit_sha[:12]}")
            return
        raise PersistenceError(
            f"No se confirmó persistencia remota tras 3 lecturas: {last_error}; "
            "no se repite PUT ni se autoriza publicación")



def record(event, now):
    return {"event": event, "status": "pendiente", "attempts": 0,
            "next_attempt": iso(now), "created": iso(now), "updated": iso(now),
            "error": "", "media_id": None, "tweet_id": None, "text": ""}


def recover(s, start, now):
    counts = {"recovered": 0, "expired": 0}
    for r in s["events"].values():
        if r["status"] == "enviando":
            r.update(status="resultado_incierto", error="Interrupción durante envío; revisión requerida")
        elif r["status"] == "procesando":
            r.update(status="pendiente", next_attempt=iso(now))
            counts["recovered"] += 1
        if r["status"] == "pendiente" and date(r["event"]["origin"]) < start:
            r.update(status="expirado", error="Fuera de ventana temporal")
            counts["expired"] += 1
        r["updated"] = iso(now)
    return counts


def eligible(r, c, start, end):
    return (r["status"] == "pendiente" and r["attempts"] < c.max_attempts
            and start <= date(r["event"]["origin"]) <= end
            and date(r["next_attempt"]) <= end)


def defer_recent(r, c, now):
    ready = date(r["event"]["origin"]) + timedelta(seconds=c.post+c.margin+c.latency)
    if now < ready:
        r.update(next_attempt=iso(ready), error="Esperando ventana POST, margen y latencia")
        return True
    return False


def retry(r, c, exc, reset=None):
    delay = min(3600, c.backoff * 2 ** min(max(r["attempts"]-1, 0), 6))
    nxt = utcnow() + timedelta(seconds=delay)
    if reset:
        nxt = max(nxt, datetime.fromtimestamp(reset, UTC))
    r.update(status="pendiente", next_attempt=iso(nxt), error=clean(exc), updated=iso(utcnow()))


def tweet_text(e):
    """twitter-text oficial (Node). También valida URLs, Unicode NFC y emoji."""
    ref = unicodedata.normalize("NFC", " ".join(e["reference"].split()))[:2000]
    depth = "Profundidad: no informada." if e["depth"] is None else f"Profundidad: {e['depth']:g} km."
    suffix = (f"\n{date(e['origin']).astimezone(ZoneInfo('America/Santiago')):%d/%m/%Y %H:%M:%S}\n"
              f"{depth}\nEpicentro: lat {e['lat']:.4f}°, lon {e['lon']:.4f}°.\n{e['url']}")
    candidates = [f"Sismo M {e['mag_display']} | {ref}" + suffix]
    candidates += [f"Sismo M {e['mag_display']} | {ref[:n].rstrip()}…" + suffix
                   for n in range(min(len(ref)-1, 280), -1, -1)]
    js = ("const fs=require('fs'),t=require('twitter-text');"
          "const a=JSON.parse(fs.readFileSync(0,'utf8'));"
          "process.stdout.write(JSON.stringify(a.find(s=>t.parseTweet(s).valid)||null));")
    p = subprocess.run(["node", "-e", js], input=json.dumps(candidates), text=True,
                       capture_output=True, timeout=30)
    if p.returncode:
        raise Failure("Validación de texto falló; instalar npm twitter-text@3.1.0")
    result = json.loads(p.stdout)
    if not result:
        raise Failure("Texto fijo supera el límite de X")
    return result


class XClient:
    BASE = "https://api.x.com/2"

    def __init__(self, c, clock):
        self.c, self.clock = c, clock
        values = [os.getenv(n) for n in SECRET_NAMES[:4]]
        if not all(values):
            raise XError("Faltan Secrets de X", 401)
        self.auth = OAuth1(values[0], values[1], values[2], values[3])

    def call(self, method, path, empty=False, **kwargs):
        self.clock.check(90)
        posting = method == "POST" and path == "/tweets"
        # Sin reintentos automáticos: tampoco ante desconexión del POST.
        try:
            r = requests.request(method, self.BASE + path, auth=self.auth,
                                 timeout=min(self.c.timeout, self.clock.remaining()-60),
                                 allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise XError(f"X {path}: {type(exc).__name__}", uncertain=posting) from None
        if not 200 <= r.status_code < 300:
            try:
                b = r.json()
                fields = {k: b[k] for k in ("title", "detail", "code", "message", "errors") if k in b}
                detail = clean(json.dumps(fields, ensure_ascii=False))
            except ValueError:
                detail = "Cuerpo no JSON (omitido)"
            category = {401: "autenticación", 402: "acceso/saldo", 403: "permisos/acceso/media",
                        429: "límite"}.get(r.status_code, "servidor" if r.status_code >= 500 else "solicitud/media")
            reset = r.headers.get("x-rate-limit-reset")
            raise XError(f"X {path}: HTTP {r.status_code} {category}; {detail}", r.status_code,
                         uncertain=posting and (r.status_code >= 500 or r.status_code in {408, 409}),
                         reset=float(reset) if reset and reset.isdigit() else None)
        if empty and not r.content:
            return {}
        try:
            b = r.json()
            if b.get("errors") or not isinstance(b, dict):
                raise ValueError()
            return b
        except (ValueError, AttributeError):
            raise XError(f"X {path}: respuesta exitosa inválida", uncertain=posting) from None

    def identity(self):
        data = self.call("GET", "/users/me").get("data", {})
        if not re.fullmatch(r"\d+", str(data.get("id", ""))):
            raise XError("Identidad de X inválida", 401)
        log(f"Cuenta X: @{data.get('username', '?')} / {data['id']}")
        return data["id"]

    def upload(self, video):
        data = self.call("POST", "/media/upload/initialize", json={
            "media_type": "video/mp4", "media_category": "tweet_video",
            "total_bytes": Path(video).stat().st_size}).get("data", {})
        mid = str(data.get("id", ""))
        if not re.fullmatch(r"\d+", mid):
            raise XError("INIT sin media ID")
        with open(video, "rb") as f:
            index = 0
            while chunk := f.read(4 * 1024 * 1024):
                self.call("POST", f"/media/upload/{mid}/append", empty=True,
                          data={"segment_index": str(index)},
                          files={"media": ("segment.mp4", chunk, "application/octet-stream")})
                index += 1
        data = self.call("POST", f"/media/upload/{mid}/finalize").get("data")
        if not isinstance(data, dict) or str(data.get("id", "")) != mid:
            raise XError("FINALIZE incompatible")
        until = min(time.monotonic()+self.c.x_wait, self.clock.end-100)
        while data.get("processing_info"):
            info = data["processing_info"]
            if info.get("state") == "succeeded":
                break
            if info.get("state") == "failed":
                raise XError("Video rechazado: " + clean(info.get("error", {})))
            if info.get("state") not in {"pending", "in_progress"}:
                raise XError("Estado de procesamiento desconocido")
            delay = max(1, float(info.get("check_after_secs", 5)))
            if time.monotonic()+delay >= until:
                raise XError("Timeout de procesamiento del video; tweet no enviado")
            log(f"X video {mid}: {info['state']}; próxima consulta en {delay:g}s")
            time.sleep(delay)
            data = self.call("GET", "/media/upload", params={"command": "STATUS", "media_id": mid}).get("data")
            if not isinstance(data, dict) or "processing_info" not in data:
                raise XError("STATUS incompatible")
        return mid

    def create(self, text, mid):
        data = self.call("POST", "/tweets", json={"text": text, "media": {"media_ids": [mid]}}).get("data", {})
        tid = str(data.get("id", ""))
        if not re.fullmatch(r"\d+", tid):
            raise XError("POST sin identificador confirmable", uncertain=True)
        return tid

    def reconcile(self, s):
        """Sólo confirma coincidencias positivas; ausencia nunca habilita reenvío."""
        uncertain = [r for r in s["events"].values() if r["status"] == "resultado_incierto"]
        if not uncertain:
            return
        posts, token = [], None
        for _ in range(5):
            params = {"max_results": 100, "tweet.fields": "created_at,entities,attachments"}
            if token:
                params["pagination_token"] = token
            b = self.call("GET", f"/users/{s['account_id']}/tweets", params=params)
            posts.extend(b.get("data", []))
            token = b.get("meta", {}).get("next_token")
            if not token:
                break
        for r in uncertain:
            matches = []
            for p in posts:
                text = p.get("text", "")
                for u in p.get("entities", {}).get("urls", []):
                    text = text.replace(u["url"], u.get("expanded_url", u["url"]))
                media = p.get("attachments", {}).get("media_keys", [])
                if (text == r["text"] and r.get("media_id")
                        and any(m.endswith("_"+r["media_id"]) for m in media)
                        and date(p["created_at"]) >= date(r.get("sent_at", r["created"]))-timedelta(seconds=10)):
                    matches.append(p)
            if len(matches) == 1:
                r.update(status="publicado", tweet_id=matches[0]["id"], error="Confirmado por reconciliación")
            else:
                log(f"Evento {r['event']['id']}: incierto bloqueado; revisar cuenta y estado manualmente")


def send_transaction(store, s, r, x, text, mid):
    r.update(status="enviando", text=text, media_id=mid, sent_at=iso(utcnow()), updated=iso(utcnow()))
    store.save(s)  # Obligatorio antes de cualquier POST de creación.
    try:
        tid = x.create(text, mid)
    except Exception as exc:
        if not isinstance(exc, XError) or exc.uncertain:
            r.update(status="resultado_incierto", error=clean(exc), updated=iso(utcnow()))
        else:
            retry(r, store.c, exc, exc.reset)
        store.save(s)
        raise
    r.update(status="publicado", tweet_id=tid, error="", updated=iso(utcnow()))
    log(f"Publicación confirmada: https://x.com/i/web/status/{tid}")
    try:
        store.save(s)
    except PersistenceError:
        # El remoto conserva 'enviando'; siguiente runner lo bloqueará.
        log(f"CRÍTICO: tweet {tid} confirmado; persistencia posterior falló. NO REENVIAR.")
        raise
    return tid


def distance(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b-a)/2)**2 + math.cos(a)*math.cos(b)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def obtener_estaciones(e, c, clock):
    """Parejas horizontales cardinales; nunca sustituir una componente ausente."""
    from obspy import read_inventory, UTCDateTime
    t = UTCDateTime(e["origin"])
    start, end = t-c.pre-c.margin, t+c.post+c.margin
    data = obtener(c.fdsn.rstrip("/")+"/fdsnws/station/1/query", c, clock, {
        "latitude": e["lat"], "longitude": e["lon"], "maxradius": c.radius/111.19,
        "channel": "*N?", "level": "channel", "format": "xml",
        "starttime": str(start), "endtime": str(end)})
    if not data:
        return [], [{"reason": "Inventario sin acelerómetros candidatos"}]
    inv = read_inventory(io.BytesIO(data))
    groups, rejected = {}, []
    for net in inv:
        for sta in net:
            for ch in sta:
                sid = f"{net.code}.{sta.code}.{ch.location_code}.{ch.code}"
                sens = ch.response.instrument_sensitivity if ch.response else None
                units = ((sens.input_units or "") if sens else "").upper().replace(" ", "")
                reason = None
                try:
                    finite_metadata = all(math.isfinite(float(v)) for v in
                                          (ch.latitude, ch.longitude, ch.dip, ch.azimuth, ch.sample_rate))
                except (TypeError, ValueError):
                    finite_metadata = False
                if units not in {"M/S**2", "M/S^2", "M/S/S"}:
                    reason = "Sin sensibilidad de aceleración SI"
                elif not finite_metadata:
                    reason = "Metadatos ausentes/no finitos"
                elif not (-90 <= ch.latitude <= 90 and -180 <= ch.longitude <= 180):
                    reason = "Coordenadas fuera de rango"
                elif abs(ch.dip) > 1 or ch.sample_rate < 50:
                    reason = "No horizontal o frecuencia <50 Hz"
                elif (ch.start_date and ch.start_date > start) or (ch.end_date and ch.end_date < end):
                    reason = "Época instrumental no cubre ventana"
                if reason:
                    rejected.append({"station": sid, "reason": reason})
                    continue
                az = ch.azimuth % 180
                axis = "N" if min(az, 180-az) <= 1 else "E" if abs(az-90) <= 1 else None
                if axis is None:
                    rejected.append({"station": sid, "reason": "Eje no cardinal; requiere rotación explícita"})
                    continue
                d = distance(e["lat"], e["lon"], ch.latitude, ch.longitude)
                if d > c.radius:
                    continue
                item = {"id": sid, "net": net.code, "sta": sta.code,
                        "loc": ch.location_code, "cha": ch.code, "lat": ch.latitude,
                        "lon": ch.longitude, "dip": ch.dip, "azimuth": ch.azimuth,
                        "distance": d}
                key = (net.code, sta.code, ch.location_code, ch.code[:-1])
                group = groups.setdefault(key, {})
                # Determinismo frente a épocas duplicadas.
                group.setdefault(axis, item)
    candidates = []
    for key, pair in groups.items():
        if set(pair) != {"N", "E"}:
            rejected.append({"station": ".".join(key), "reason": "Falta pareja horizontal N/E"})
            continue
        n, east = pair["N"], pair["E"]
        if distance(n["lat"], n["lon"], east["lat"], east["lon"]) > .1:
            rejected.append({"station": ".".join(key), "reason": "Componentes no colocadas"})
            continue
        candidates.append(dict(n, id=".".join(key)+"[N,E]", components=[n, east]))
    candidates.sort(key=lambda x: (x["distance"], x["id"]))
    return candidates[:c.candidates], rejected


def check_raw(st, start, end):
    import numpy as np
    if not st:
        raise Failure("Sin muestras")
    if st.get_gaps():
        raise Failure("Huecos/solapamientos detectados; no se interpolan")
    st.merge(method=0, fill_value=None)
    if len(st) != 1:
        raise Failure("Más de una traza incompatible")
    tr = st[0]
    if tr.stats.starttime > start+tr.stats.delta or tr.stats.endtime < end-tr.stats.delta:
        raise Failure("Cobertura temporal incompleta")
    a = tr.data
    if np.ma.is_masked(a) or not np.isfinite(a).all() or len(a) < 100:
        raise Failure("Muestras ausentes/no finitas")
    if np.ptp(a.astype(float)) == 0:
        raise Failure("Señal constante")
    # Heurística conservadora de clipping: meseta >=3 muestras en extremo global.
    extreme = (a == np.max(a)) | (a == np.min(a))
    if np.any(extreme[:-2] & extreme[1:-1] & extreme[2:]):
        raise Failure("Posible saturación: meseta en extremo")
    if np.issubdtype(a.dtype, np.integer):
        lim = np.iinfo(a.dtype)
        if np.any((a == lim.min) | (a == lim.max)):
            raise Failure("Saturación del contenedor digital")
    return tr


def procesar_componente(station, e, c, clock):
    import numpy as np
    from obspy import read, read_inventory, UTCDateTime
    t = UTCDateTime(e["origin"])
    start, end = t-c.pre-c.margin, t+c.post+c.margin
    params = {"net": station["net"], "sta": station["sta"], "loc": station["loc"] or "--",
              "cha": station["cha"], "starttime": str(start), "endtime": str(end)}
    base = c.fdsn.rstrip("/")+"/fdsnws/"
    metadata = obtener(base+"station/1/query", c, clock, dict(params, level="response", format="xml"))
    if not metadata:
        raise Failure("Sin respuesta instrumental")
    inv = read_inventory(io.BytesIO(metadata))
    response = inv.get_response(station["id"], t)
    sens = response.instrument_sensitivity
    units = sens.input_units.upper().replace(" ", "") if sens else ""
    if units not in {"M/S**2", "M/S^2", "M/S/S"} or not sens or sens.value <= 0:
        raise Failure("Respuesta no calibrada como acelerómetro")
    if not response.response_stages:
        raise Failure("Respuesta sin etapas; no se inventa factor de conversión")
    raw = obtener(base+"dataselect/1/query", c, clock, params)
    if not raw:
        raise Failure("FDSN 204: sin registros")
    st = read(io.BytesIO(raw), format="MSEED").select(id=station["id"])
    tr = check_raw(st, start, end)
    if tr.stats.sampling_rate < 50:
        raise Failure("Muestreo insuficiente para banda común")
    tr.data = tr.data.astype(np.float64)
    tr.detrend("linear")
    tr.remove_response(inventory=inv, output="ACC", pre_filt=(0.1, 0.2, 20, 24),
                       water_level=None, zero_mean=True, taper=True, taper_fraction=0.05)
    tr.filter("bandpass", freqmin=0.2, freqmax=20, corners=4, zerophase=True)
    tr.trim(t-c.pre, t+c.post, nearest_sample=False)
    values = tr.data
    if not np.isfinite(values).all():
        raise Failure("Resultado instrumental no finito")
    times = tr.times() + float(tr.stats.starttime-t)
    return dict(station, times=times, values=values, sample_rate=tr.stats.sampling_rate,
                pga_component_m_s2=float(np.max(np.abs(values))))


def procesar_estacion(station, e, c, clock):
    """Conservar ambas señales SI para calcular peaks sin remuestrear ni rellenar."""
    components = [procesar_componente(part, e, c, clock) for part in station["components"]]
    return dict(station, components=components,
                metric="max(abs(a_N), abs(a_E)) per time bin; percent standard gravity")


def station_peaks(station, centers, step):
    import numpy as np
    components = station.get("components", [])
    if len(components) != 2:
        raise Failure("La métrica horizontal exige dos componentes calibradas")
    peaks = np.empty(len(centers))
    for j, t in enumerate(centers):
        maxima = []
        for component in components:
            times = np.asarray(component["times"])
            values = np.asarray(component["values"])
            v = values[(times >= t-step/2) & (times < t+step/2)]
            if not len(v) or not np.isfinite(v).all():
                raise Failure("Intervalo horizontal incompleto; no se rellena")
            maxima.append(float(np.max(np.abs(v))))
        peaks[j] = max(maxima) * 100 / 9.80665
    return peaks


def spatial_weights(lons, lats, gx, gy, e, c):
    """IDW local en km, dentro del casco y de triángulos con soporte.

    Al menos tres estaciones a SUPPORT_KM; nunca extrapolar ni rellenar con cero.
    """
    import numpy as np
    from scipy.spatial import Delaunay, QhullError
    def xy(lon, lat):
        return np.column_stack(((np.ravel(lon)-e["lon"])*111.19*math.cos(math.radians(e["lat"])),
                                (np.ravel(lat)-e["lat"])*111.19))
    points, grid = xy(lons, lats), xy(gx, gy)
    weights = np.zeros((len(grid), len(points)))
    mask = np.zeros(len(grid), dtype=bool)
    if len(points) < 3:
        return weights, mask, "Menos de tres estaciones distintas"
    try:
        tri = Delaunay(points)
        simplex = tri.find_simplex(grid)
        vertices = tri.simplices[np.maximum(simplex, 0)]
        edge = np.max([np.linalg.norm(points[vertices[:, i]]-points[vertices[:, j]], axis=1)
                       for i, j in ((0, 1), (1, 2), (2, 0))], axis=0)
        d = np.linalg.norm(grid[:, None, :]-points[None, :, :], axis=2)
        near = d <= c.support
        mask = (simplex >= 0) & (near.sum(axis=1) >= 3) & (edge <= c.triangle)
        weights = np.where(near, 1 / np.maximum(d, .001)**2, 0)
        weights /= np.maximum(weights.sum(axis=1, keepdims=True), 1e-30)
        weights[~mask] = 0
    except QhullError:
        return weights, mask, "Estaciones alineadas o geometría degenerada"
    reason = "IDW local con al menos tres estaciones; sin extrapolación" if mask.any() else "Sin área con tres estaciones y límites de soporte válidos"
    return weights, mask, reason


def collect(candidates, processor, workers):
    valid, rejected = [], []
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(processor, s): s for s in candidates}
        for task in futures.as_completed(tasks):
            station = tasks[task]
            try:
                valid.append(task.result())
            except Exception as exc:
                rejected.append({"station": station["id"], "reason": clean(exc)})
    valid.sort(key=lambda s: (s["distance"], s["id"]))
    unique, seen = [], set()
    for station in valid:
        key = (station.get("net", ""), station.get("sta", station["id"]))
        if key not in seen:
            unique.append(station)
            seen.add(key)
        else:
            rejected.append({"station": station["id"], "reason": "Otra pareja válida de la misma estación"})
    return unique, rejected


def prepare_basemap(c, clock):
    # Evita descargas implícitas de Cartopy sin nuestro timeout/reintentos.
    import cartopy
    root = Path(cartopy.config["data_dir"]) / "shapefiles" / "natural_earth"
    for category, name in (("physical", "land"), ("physical", "ocean"),
                           ("physical", "coastline"), ("cultural", "admin_0_boundary_lines_land")):
        stem = "ne_110m_" + name
        target = root/category
        if all((target/(stem+ext)).is_file() for ext in (".shp", ".shx", ".dbf")):
            continue
        data = obtener(f"https://naturalearth.s3.amazonaws.com/110m_{category}/{stem}.zip", c, clock)
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                name_in_zip = stem+ext
                if name_in_zip in z.namelist():
                    (target/name_in_zip).write_bytes(z.read(name_in_zip))
        if not all((target/(stem+ext)).is_file() for ext in (".shp", ".shx", ".dbf")):
            raise Failure("Cartografía incompleta")


def generar_video(e, stations, c, clock, folder):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter
    from matplotlib.colors import LogNorm
    from matplotlib.cm import ScalarMappable
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    prepare_basemap(c, clock)
    n = round((c.pre+c.post)/c.step)
    times = -c.pre + (np.arange(n)+0.5)*c.step
    amps = np.stack([station_peaks(s, times, c.step) for s in stations])
    positive = amps[amps > 0]
    if not len(positive) or not np.isfinite(amps).all():
        raise Failure("Amplitud horizontal no utilizable")
    vmin = float(10 ** np.floor(np.log10(positive.min())))
    vmax = float(10 ** np.ceil(np.log10(positive.max())))
    if vmax <= vmin:
        vmax = vmin * 10
    lons = np.array([s["lon"] for s in stations]); lats = np.array([s["lat"] for s in stations])
    extent = [min(lons.min(), e["lon"])-0.5, max(lons.max(), e["lon"])+0.5,
              min(lats.min(), e["lat"])-0.5, max(lats.max(), e["lat"])+0.5]
    gx, gy = np.meshgrid(np.linspace(*extent[:2], 180), np.linspace(*extent[2:], 180))
    weights, mask, coverage_reason = spatial_weights(lons, lats, gx, gy, e, c)
    has_area = bool(mask.any())
    log(f"Mapa: {len(stations)} estaciones; áreas={has_area}; {coverage_reason}")
    def field(j):
        a = np.full(gx.size, np.nan)
        if has_area:
            a[mask] = weights[mask] @ amps[:, j]
        return a.reshape(gx.shape)
    fig = plt.figure(figsize=(12.8, 7.2), dpi=100, facecolor="white")
    gs = fig.add_gridspec(1, 2, left=.06, right=.97, bottom=.25, top=.80,
                         width_ratios=[1, 1.15], hspace=.48, wspace=.30)
    ax = fig.add_subplot(gs[:, 0], projection=ccrs.PlateCarree())
    ax.set_extent(extent)
    ax.set_title("MAPA DE ACELERACIÓN HORIZONTAL", fontsize=10, pad=15)
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="#eee8da", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="#eef5fa", zorder=0)
    ax.coastlines("110m", linewidth=.6, zorder=3)
    ax.add_feature(cfeature.BORDERS.with_scale("110m"), linewidth=.4, zorder=3)
    gl = ax.gridlines(draw_labels=True, linewidth=.3, alpha=.5)
    gl.top_labels = gl.right_labels = False
    gl.xlabel_style = gl.ylabel_style = {"size": 8}
    norm = LogNorm(vmin, vmax, clip=True)
    mesh = ax.pcolormesh(gx, gy, np.ma.masked_invalid(field(0)), cmap="jet", norm=norm,
                         alpha=.85, shading="auto", edgecolors="none", zorder=2)
    line_colors = [plt.get_cmap("tab20")(i % 20) for i in range(len(stations))]
    if has_area:
        # El área representa amplitud; el borde identifica la estación y su curva.
        dots = ax.scatter(lons, lats, facecolors="white", edgecolors=line_colors,
                          marker="^", linewidths=1.6, s=55, zorder=5)
    else:
        dots = ax.scatter(lons, lats, c=amps[:, 0], cmap="jet", norm=norm,
                          edgecolors="black", s=55, zorder=5)
    ax.scatter([e["lon"]], [e["lat"]], marker="*", c="magenta", edgecolors="black", s=180, zorder=6)
    ax.text(e["lon"]+.03, e["lat"]-.05, "Epicentro", fontsize=8, color="purple", zorder=7)
    for s in stations:
        ax.text(s["lon"]+.025, s["lat"]+.025, s["sta"], fontsize=7, zorder=7)
    bar = fig.colorbar(ScalarMappable(norm=norm, cmap="jet"), ax=ax, orientation="horizontal", pad=.07, fraction=.05)
    bar.set_label(f"Peak horizontal por {c.step:g} s [% g] · escala logarítmica fija", fontsize=8)
    bar.ax.tick_params(labelsize=8)
    # Exactamente la misma magnitud física y los mismos intervalos que el mapa.
    # Cero real no se dibuja en log; nunca se sustituye por una señal inventada.
    p = fig.add_subplot(gs[0, 1])
    for i, station in enumerate(stations):
        p.plot(times, np.ma.masked_less_equal(amps[i], 0), color=line_colors[i],
               linewidth=1.0, alpha=.85,
               label=f"{station['net']}.{station['sta']} · {station['distance']:.0f} km")
    p.set_yscale("log")
    p.axvline(0, color="#ef6351", linestyle="--", linewidth=1)
    p.text(0, 1.015, "Origen del sismo", transform=p.get_xaxis_transform(),
           ha="center", fontsize=7, color="#c5493d")
    cursor = p.axvline(times[0], color="#263449", linewidth=1.5, zorder=10)
    cursor_label = p.text(times[0], .96, "", transform=p.get_xaxis_transform(),
                          ha="center", va="top", fontsize=8, color="white",
                          bbox={"facecolor": "#263449", "edgecolor": "none", "pad": 2})
    p.set(xlim=(-c.pre, c.post), ylim=(vmin, vmax),
          xlabel="Tiempo respecto al origen [s]", ylabel="Peak horizontal por intervalo [% g]")
    p.set_title("ACELERACIÓN DE LAS ESTACIONES", fontsize=10, pad=28)
    p.tick_params(labelsize=8)
    p.grid(alpha=.25, which="major")
    p.legend(loc="upper center", bbox_to_anchor=(.5, -.13), fontsize=6,
             frameon=False, ncol=3)
    depth = "no informada" if e["depth"] is None else f"{e['depth']:g} km"
    fig.suptitle(f"Sismo M {e['mag_display']} | {e['reference'][:85]}\n"
                 f"{date(e['origin']).astimezone(ZoneInfo('America/Santiago')):%d/%m/%Y %H:%M:%S}"
                 f" · Profundidad: {depth}", fontsize=12, y=.96)
    label = fig.text(.06, .855, "", fontsize=10)
    coverage = (
    "Peak = máx. de |N| y |E| por intervalo; "
    "banda 0.2–20 Hz. Áreas: interpolación IDW estimada."
    )
    fig.text(.04, .022, "Fuente: Centro Sismológico Nacional de la Universidad de Chile", fontsize=8)
    video = folder / "video.mp4"
    writer = FFMpegWriter(fps=c.fps, codec="libx264", bitrate=5000,
                         extra_args=["-vf", "fps=30,format=yuv420p", "-profile:v", "high",
                                     "-g", "60", "-flags", "+cgop", "-movflags", "+faststart"])
    peak = int(np.argmax(amps.max(axis=0)))
    try:
        with writer.saving(fig, str(video), dpi=100):
            for j, t in enumerate(times):
                clock.check(10)
                if not has_area:
                    dots.set_array(amps[:, j])
                mesh.set_array(np.ma.masked_invalid(field(j)).ravel())
                cursor.set_xdata([t, t])
                cursor_label.set_position((t, .96))
                cursor_label.set_text(f"{t:+.1f} s")
                label.set_text(f"t = {t:+.1f} s · reproducción ×{c.step*c.fps:g}")
                if j == peak:
                    fig.savefig(folder/"preview.png", dpi=100)
                writer.grab_frame()
                if j % 30 == 0:
                    log(f"Render {j+1}/{n} cuadros")
    finally:
        plt.close(fig)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
                           capture_output=True, text=True, timeout=30, check=True)
    info = json.loads(probe.stdout)
    stream = next(s for s in info["streams"] if s["codec_type"] == "video")
    duration = float(info["format"]["duration"])
    if (stream["codec_name"] != "h264" or stream["pix_fmt"] != "yuv420p"
            or (stream["width"], stream["height"]) != (1280, 720)
            or abs(duration-n/c.fps) > .15 or not .5 <= duration <= 140
            or video.stat().st_size > 512*1024*1024):
        raise Failure("MP4 no supera validación ffprobe")
    return {"physical_seconds": c.pre+c.post, "frame_step_seconds": c.step,
            "render_fps": c.fps, "encoded_fps": 30, "duration_seconds": duration,
            "speed_factor": c.step*c.fps, "bytes": video.stat().st_size,
            "metric": "horizontal_component_peak_percent_g", "standard_gravity_m_s2": 9.80665,
            "color_min_percent_g": vmin, "color_max_percent_g": vmax, "color_scale": "log_fixed", "interpolation": has_area,
            "interpolation_reason": coverage_reason, "stations_used": len(stations),
            "grid_cells_with_support": int(mask.sum()), "signal_layout": "single_overlay", "band_hz": [0.2, 20],
            "spatial_method": "local_IDW_power_2_hull_and_triangle_mask",
            "ffprobe": info}


def render_worker(event_path, c):
    e = validate_event(json.loads(Path(event_path).read_text()))
    folder = Path(event_path).parent
    clock = Clock(c.event_budget)
    quality = {"event": e, "metric": "horizontal component peak percent g, 0.2-20 Hz",
               "clipping_test": "heuristic, not proof of absence of sensor saturation"}
    try:
        candidates, excluded = obtener_estaciones(e, c, clock)
        valid, rejected = collect(candidates, lambda s: procesar_estacion(s, e, c, clock), c.workers)
        quality.update(attempted=len(candidates), valid=len(valid), rejected=excluded+rejected)
        log(f"Estaciones intentadas={len(candidates)}, válidas={len(valid)}, rechazadas={len(rejected)}")
        if not valid:
            raise Failure("Ausencia total de aceleración válida; conservar pendiente")
        valid = valid[:c.stations]
        quality["used"] = [dict(
            {k: v for k, v in station.items() if k != "components"},
            components=[{k: v for k, v in part.items() if k not in {"times", "values"}}
                        for part in station["components"]]) for station in valid]
        quality["video"] = generar_video(e, valid, c, clock, folder)
        atomic(folder/"quality.json", quality)
        return 0
    except Exception as exc:
        quality["error"] = clean(exc)
        atomic(folder/"quality.json", quality)
        log(f"Descarga/procesamiento/render: {type(exc).__name__}: {clean(exc)}")
        return 1


def build_video(e, c, clock):
    clock.check(180)
    folder = Path(c.output) / e["id"]
    folder.mkdir(parents=True, exist_ok=True)
    event_path = folder / "event.json"
    atomic(event_path, e)
    video = folder / "video.mp4"
    for name in ("video.mp4", "preview.png", "quality.json"):
        (folder/name).unlink(missing_ok=True)  # Nunca reutilizar archivos de otro intento.
    env = dict(os.environ)
    for name in SECRET_NAMES:
        env.pop(name, None)
    limit = min(c.event_budget, clock.remaining()-120)
    env["EVENT_TIMEOUT_SEC"] = str(limit)
    try:
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--render", str(event_path.resolve())],
                           env=env, timeout=limit, check=False)
    except subprocess.TimeoutExpired:
        raise Failure("Timeout de descarga/render; evento pendiente") from None
    if p.returncode or not video.is_file():
        raise Failure("Fallo de aceleración/video; revisar quality.json")
    log(f"Video validado: {e['id']}, {video.stat().st_size} bytes")
    return video


def run(c, init=False):
    end = utcnow(); start = end-timedelta(hours=c.lookback)
    clock = Clock(c.budget)
    report = {"start_utc": iso(start), "end_utc": iso(end), "mode": "live" if c.publish else "simulation",
              "found": 0, "new": 0, "pending": 0, "skipped": 0, "expired": 0, "results": [], "errors": []}
    code = 0
    Path(c.output).mkdir(parents=True, exist_ok=True)
    try:
        log(f"Ventana fija: {iso(start)} a {iso(end)}; M >= {c.mag}")
        store = Store(c); s = store.load(init)
        report.update(recover(s, start, end))
        store.save(s)
        x = XClient(c, clock) if c.publish else None
        if x:
            account = x.identity()
            if s["account_id"] not in {None, account}:
                raise PersistenceError("Cuenta X distinta de la asociada al estado real")
            if s["account_id"] is None:
                s["account_id"] = account; store.save(s)
            if c.reconcile:
                x.reconcile(s); store.save(s)
        events, source = discover(c, clock, start, end)
        report["source"], report["found"] = source, len(events)
        log(f"Consulta CSN: {source['status']}; eventos elegibles={len(events)}")
        if source["status"] != "valida":
            code = 1
            report["errors"].append("Consulta CSN incompleta; pendientes se recuperan igualmente")
        for e in events:
            if e["id"] not in s["events"]:
                s["events"][e["id"]] = record(e, end); report["new"] += 1
        store.save(s)
        report["pending"] = sum(r["status"] == "pendiente" for r in s["events"].values())
        queue = sorted(s["events"].values(), key=lambda r: r["event"]["origin"])
        processed = 0
        for r in queue:
            if r["status"] == "resultado_incierto":
                code = 1
                report["errors"].append(f"{r['event']['id']}: resultado incierto bloqueado")
            if r["status"] == "pendiente" and r["attempts"] >= c.max_attempts:
                code = 1
                report["errors"].append(f"{r['event']['id']}: límite de intentos; permanece bloqueado hasta expirar")
            if not eligible(r, c, start, end):
                report["skipped"] += 1
                continue
            if defer_recent(r, c, end):
                store.save(s); report["skipped"] += 1
                log(f"{r['event']['id']}: aplazado hasta {r['next_attempt']}")
                continue
            if processed >= c.max_events:
                break
            clock.check(180)
            processed += 1
            r.update(status="procesando", attempts=r["attempts"]+1, updated=iso(utcnow()))
            store.save(s)
            try:
                text = tweet_text(r["event"])
                video = build_video(r["event"], c, clock)
                r["text"] = text
                if x:
                    mid = x.upload(video)
                    send_transaction(store, s, r, x, text, mid)
                else:
                    r.update(status="simulado", error="", updated=iso(utcnow()))
                    store.save(s)
                    log(f"Simulado {r['event']['id']}; sin llamadas a X")
                report["results"].append({"id": r["event"]["id"], "status": r["status"], "tweet_id": r["tweet_id"]})
            except PersistenceError:
                raise  # Nunca intentar arreglar un CAS fallido sobrescribiendo el estado.
            except XError as exc:
                code = 1
                report["errors"].append(clean(exc))
                if r["status"] == "procesando":
                    retry(r, c, exc, exc.reset); store.save(s)
                if exc.global_stop:
                    break
            except Exception as exc:
                code = 1
                if r["status"] in {"enviando", "resultado_incierto", "publicado"}:
                    if r["status"] == "enviando":
                        r.update(status="resultado_incierto", error=clean(exc))
                        store.save(s)
                    raise
                retry(r, c, exc); store.save(s)
                report["errors"].append(f"{r['event']['id']}: {clean(exc)}")
        if report["found"] == 0 and report["pending"] == 0 and source["status"] == "valida":
            log("Consulta válida sin eventos nuevos ni pendientes")
    except Exception as exc:
        code = 1
        report["errors"].append(f"{type(exc).__name__}: {clean(exc)}")
        log(report["errors"][-1])
    finally:
        report["exit_code"] = code
        atomic(Path(c.output)/"summary.json", report)
        summary = "## CSN → aceleración → X\n\n```json\n"+json.dumps(report, ensure_ascii=False, indent=2)+"\n```\n"
        Path(c.output, "summary.md").write_text(summary, encoding="utf-8")
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(summary)
        log(f"Ciclo terminado: exit={code}; nuevos={report['new']}; pendientes={report['pending']}")
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-state", action="store_true")
    parser.add_argument("--render")
    args = parser.parse_args()
    try:
        c = Config.env()
        if args.render:
            return render_worker(args.render, c)
        return run(c, args.init_state)
    except Exception as exc:
        log(f"Configuración: {type(exc).__name__}: {clean(exc)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
