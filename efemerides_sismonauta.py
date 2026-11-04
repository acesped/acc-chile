#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA | Efemérides sísmicas verificables.

Dependencias: pip install requests pillow
Uso:
  python efemerides_sismonauta.py --test
  python efemerides_sismonauta.py --out output_efemerides
  python efemerides_sismonauta.py --publish --out output_efemerides
  python efemerides_sismonauta.py --date 2026-05-22 --min-mag 8

Comportamiento: coincidencia ESTRICTA de día y mes, sin inventar aniversarios.
El catálogo NOAA/Zenodo contiene registros desde 1900 hasta 2024.
Exige magnitud >=8.0 y fatalidades documentadas; configurable con --min-mag.
Para publicaciones automáticas necesita repositorio GitHub público y las
credenciales de Meta que ya utiliza SISMONAUTA NOTICIAS.
"""
from __future__ import annotations
import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ACCOUNT = 'sismonauta'
STATE_FILE = 'sismonauta_efemerides/state.json'  # independiente del historial de noticias
CATALOG_URL = 'https://zenodo.org/records/14108596/files/earthquakes_1900_2024.csv?download=1'
CATALOG_INFO = 'https://zenodo.org/records/14108596'
CATALOG_SOURCE = 'NOAA NCEI/WDS, Significant Earthquake Database; compilación 1900–2024 (Zenodo DOI 10.5281/zenodo.14108596)'
USGS_EVENT_QUERY = 'https://earthquake.usgs.gov/fdsnws/event/1/query'
IG_BASE = os.getenv('IG_GRAPH_BASE', 'https://graph.instagram.com').rstrip('/')
FB_BASE = 'https://graph.facebook.com'
GRAPH_VERSION = os.getenv('IG_GRAPH_VERSION', 'v25.0')
TIMEZONE = os.getenv('EFEMERIDES_TIMEZONE', 'America/Santiago')
W, H = 1080, 1350
HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'SismonautaEfemerides/1.3 (+https://github.com/acesped/acc-chile)', 'Accept': '*/*'})


def log(*args):
    print(*args, flush=True)


def atomic_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def safe_error(exc):
    text = str(exc)
    for name in ('GH_TOKEN', 'INSTAGRAM_TOKEN', 'FACEBOOK_PAGE_TOKEN'):
        value = os.getenv(name, '')
        if value:
            text = text.replace(value, '***')
    return text[:300]


def number(value):
    text = str(value or '').strip()
    if not text or text.upper() in {'NA', 'NAN', 'NULL', 'NONE', '-'}:
        return None
    try:
        return float(text.replace(',', ''))
    except ValueError:
        return None


def integer(value):
    n = number(value)
    return int(n) if n is not None and n >= 0 and n.is_integer() else None


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def normalize_text(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    return ''.join(c for c in value if not unicodedata.combining(c)).lower()


def fetch_catalog(cache: Path, offline=False):
    if offline:
        if not cache.exists():
            raise RuntimeError('No existe el CSV local para --offline')
        return cache.read_text(encoding='utf-8-sig')
    try:
        res = HTTP.get(CATALOG_URL, timeout=(12, 55))
        res.raise_for_status()
        raw = res.content
        if len(raw) < 1000 or len(raw) > 15_000_000:
            raise RuntimeError('Tamaño de CSV inesperado')
        decoded = raw.decode('utf-8-sig')
        if 'Year,Mo,Dy' not in decoded.splitlines()[0]:
            raise RuntimeError('Cabeceras del catálogo no reconocidas')
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
        return decoded
    except Exception as exc:
        if cache.exists():
            log('CATALOGO REMOTO NO DISPONIBLE; usando CSV local:', safe_error(exc))
            return cache.read_text(encoding='utf-8-sig')
        raise RuntimeError('No fue posible obtener el catálogo histórico: ' + safe_error(exc))


def parse_catalog(csv_text: str):
    """Columnas verificadas: Year,Mo,Dy,Mag,Location_Name,Deaths,Total_Deaths."""
    reader = csv.DictReader(io.StringIO(csv_text))
    required = {'Year', 'Mo', 'Dy', 'Mag', 'Location_Name', 'Deaths', 'Total_Deaths'}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise RuntimeError('Formato CSV NOAA/Zenodo no reconocido')
    events = []
    for row in reader:
        year = integer(row.get('Year'))
        month = integer(row.get('Mo'))
        day = integer(row.get('Dy'))
        magnitude = number(row.get('Mag'))
        if not year or not month or not day or magnitude is None:
            continue
        try:
            event_date = date(year, month, day)
        except ValueError:
            continue
        total = integer(row.get('Total_Deaths'))
        direct = integer(row.get('Deaths'))
        deaths = total if total is not None else direct
        location = clean(row.get('Location_Name'))
        if not location:
            continue
        event_id = hashlib.sha256(
            f'{event_date.isoformat()}|{location}|{magnitude}'.encode('utf-8')
        ).hexdigest()[:18]
        events.append({
            'id': event_id,
            'date': event_date,
            'magnitude': magnitude,
            'location': location,
            'deaths': deaths,
            'fatalities_type': 'total' if total is not None else 'earthquake',
            'tsunami_flag': clean(row.get('Tsu')).upper() not in ('', 'NA'),
            'depth_km': number(row.get('Focal_Depth_(km)')),
            'latitude': number(row.get('Latitude')),
            'longitude': number(row.get('Longitude')),
        })
    return events


COUNTRIES = {
    'CHILE': 'Chile', 'JAPAN': 'Japón', 'INDONESIA': 'Indonesia',
    'PERU': 'Perú', 'ECUADOR': 'Ecuador', 'MEXICO': 'México',
    'USA': 'Estados Unidos', 'ALASKA': 'Estados Unidos',
    'CALIFORNIA': 'Estados Unidos', 'RUSSIA': 'Rusia',
    'RUSSIAN FEDERATION': 'Rusia', 'TURKEY': 'Turquía',
    'TURKIYE': 'Turquía', 'CHINA': 'China', 'NEPAL': 'Nepal',
    'HAITI': 'Haití', 'INDIA': 'India', 'PAKISTAN': 'Pakistán',
    'BOLIVIA': 'Bolivia', 'COLOMBIA': 'Colombia',
    'VENEZUELA': 'Venezuela', 'ARGENTINA': 'Argentina',
    'NICARAGUA': 'Nicaragua', 'COSTA RICA': 'Costa Rica',
    'PAPUA NEW GUINEA': 'Papúa Nueva Guinea',
    'SOLOMON ISLANDS': 'Islas Salomón', 'PHILIPPINES': 'Filipinas',
    'NEW ZEALAND': 'Nueva Zelanda', 'TAIWAN': 'Taiwán',
    'PORTUGAL': 'Portugal', 'GREECE': 'Grecia',
}


def location_label(raw):
    """No inventa una ciudad cuando NOAA describe una región oceánica."""
    raw = clean(raw)
    parts = [x.strip() for x in raw.split(':') if x.strip()]
    if not parts:
        return raw
    country_key = parts[0].upper()
    country = COUNTRIES.get(country_key, parts[0].title())
    if len(parts) > 1:
        region = parts[-1].replace(',', ', ').strip().title()
        return f'{region}, {country}'
    return country


def choose_event(events, target_day: date, min_mag: float, state):
    candidates = [
        e for e in events
        if e['date'].month == target_day.month
        and e['date'].day == target_day.day
        and e['date'].year < target_day.year
        and e['magnitude'] >= min_mag
        and e['deaths'] is not None
    ]
    used = state.get('posts', {})
    candidates = [e for e in candidates if f"{target_day.isoformat()}:{e['id']}" not in used]
    candidates.sort(key=lambda e: (-e['magnitude'], -e['deaths'], -e['date'].year))
    return candidates[0] if candidates else None


def event_caption(event, today):
    years = today.year - event['date'].year
    mag = f"{event['magnitude']:.1f}"
    place = location_label(event['location'])
    death_text = f"{event['deaths']:,}".replace(',', '.')
    date_text = event['date'].strftime('%d/%m/%Y')
    if event['deaths'] == 0:
        fatalities = 'El registro histórico consigna 0 fallecidos.'
    else:
        fatalities = f'El registro histórico consigna {death_text} personas fallecidas'
        fatalities += (' considerando los efectos asociados, incluido el tsunami cuando corresponde.'
                       if event['fatalities_type'] == 'total' else ' a causa del terremoto.')
    paragraph = (
        f"El {date_text} ocurrió un terremoto de magnitud {mag} "
        f"en la zona de {place}. {fatalities} "
        'Este episodio forma parte del registro histórico de terremotos significativos '
        'y permite recordar el impacto que pueden producir los grandes movimientos de la Tierra.'
    )
    return (
        f"UN DÍA COMO HOY, pero hace {years} años...\n\n"
        f"{paragraph}\n\n"
        'Te leemos en los comentarios.\n\n'
        '#SISMONAUTA #EfemeridesSismicas #Terremotos #Sismologia '
        '#HistoriaSismica #CienciaDeLaTierra #PrevencionSismica'
    )


def font(size, bold=True):
    files = ([
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    ] if bold else [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ])
    for file in files:
        if Path(file).exists():
            return ImageFont.truetype(file, size)
    return ImageFont.load_default()


def wrap(draw, phrase, ft, max_width):
    lines = []
    current = ''
    for word in phrase.split():
        trial = (current + ' ' + word).strip()
        if draw.textbbox((0, 0), trial, font=ft)[2] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def haversine_km(lat1, lon1, lat2, lon2):
    """Distancia geodésica y manejo correcto de la línea de cambio de fecha."""
    radius_km = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians((lon2 - lon1 + 180) % 360 - 180)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius_km * math.asin(min(1, math.sqrt(a)))


def fetch_usgs_epicenter(event):
    """Devuelve SOLO un origen USGS concordante; nunca finge que NOAA es USGS.

    Para eventos históricos la cobertura USGS puede ser incompleta. Si no se
    verifica el candidato, no se genera el mapa, pero sí la publicación.
    """
    # Margen UTC para cubrir efemérides fechadas según hora local.
    starttime = (event['date'] - timedelta(days=1)).isoformat()
    endtime = (event['date'] + timedelta(days=2)).isoformat()
    params = {
        'format': 'geojson', 'starttime': starttime, 'endtime': endtime,
        'minmagnitude': max(0, round(event['magnitude'] - 0.40, 2)),
        'maxmagnitude': min(10, round(event['magnitude'] + 0.40, 2)),
        'orderby': 'magnitude', 'limit': 100,
    }
    ref_lat, ref_lon = event.get('latitude'), event.get('longitude')
    if ref_lat is not None and ref_lon is not None:
        params.update(latitude=ref_lat, longitude=ref_lon, maxradiuskm=450)
    try:
        res = HTTP.get(USGS_EVENT_QUERY, params=params, timeout=(10, 35))
        res.raise_for_status()
        features = res.json().get('features', [])
    except Exception as exc:
        log('USGS NO DISPONIBLE:', safe_error(exc))
        features = []

    pieces = [normalize_text(p) for p in event['location'].split(':')]
    tokens = {t for part in pieces for t in re.findall(r'[a-z]{4,}', part)}
    tokens -= {'islands', 'island', 'region', 'earthquake', 'near', 'offshore',
               'peninsula', 'coast', 'russia', 'chile', 'peru', 'japan'}
    matches = []
    for feature in features:
        props = feature.get('properties') or {}
        coords = (feature.get('geometry') or {}).get('coordinates') or []
        if len(coords) < 2:
            continue
        lon, lat = number(coords[0]), number(coords[1])
        mag = number(props.get('mag'))
        if lon is None or lat is None or mag is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        magnitude_delta = abs(mag - event['magnitude'])
        if magnitude_delta > 0.4:
            continue
        distance = None
        if ref_lat is not None and ref_lon is not None:
            distance = haversine_km(ref_lat, ref_lon, lat, lon)
            if distance > 350:
                continue
        place = clean(props.get('place'))
        overlap = len(tokens & set(re.findall(r'[a-z]{4,}', normalize_text(place))))
        # Con referencia geográfica, se puede aceptar un nombre de lugar
        # diferente; sin ella se exige coincidencia regional explícita.
        if distance is None and overlap == 0:
            continue
        score = magnitude_delta * 200 + (distance or 0) - overlap * 15
        matches.append((score, feature, lat, lon, mag, distance))

    matches.sort(key=lambda item: item[0])
    if not matches:
        log('SIN COINCIDENCIA USGS EN CONSULTA; evaluando referencia histórica.')
        return historical_epicenter_fallback(event)
    # Evita seleccionar arbitrariamente entre dos eventos comparables.
    if len(matches) > 1 and matches[1][0] - matches[0][0] < 15:
        log('COINCIDENCIA USGS AMBIGUA; evaluando referencia histórica.')
        return historical_epicenter_fallback(event)
    _, feature, lat, lon, mag, distance = matches[0]
    props = feature['properties']
    coords = feature['geometry']['coordinates']
    return {
        'source': 'USGS', 'event_id': feature.get('id'),
        'place': clean(props.get('place')), 'magnitude': mag,
        'latitude': lat, 'longitude': lon,
        'depth_km': number(coords[2]) if len(coords) > 2 else None,
        'url': props.get('url', ''),
        'time_ms': props.get('time'),
        'tsunami': props.get('tsunami'),
        'distance_to_noaa_km': round(distance, 1) if distance is not None else None,
    }



def historical_epicenter_fallback(event):
    """Referencia USGS publicada para Kamchatka 1952; NOAA para otros sismos.

    La reserva NOAA está identificada como tal y solo se usa si el registro
    histórico incluye coordenadas. Nunca se etiqueta como obtenido de USGS.
    """
    if (event['date'] == date(1952, 11, 4)
            and abs(event['magnitude'] - 9.0) <= 0.2
            and 'KAMCHATKA' in event['location'].upper()):
        return {
            'source': 'USGS', 'event_id': 'official19521104165830_30',
            'place': 'Kamchatka, Rusia', 'magnitude': 9.0,
            'latitude': 52.623, 'longitude': 159.779,
            'depth_km': 21.6,
            'url': 'https://earthquake.usgs.gov/earthquakes/eventpage/official19521104165830_30',
            'time_ms': None, 'tsunami': None,
            'coordinate_note': 'Referencia histórica USGS',
        }
    lat, lon = event.get('latitude'), event.get('longitude')
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        log('SIN COORDENADAS VERIFICABLES: no se dibujará un epicentro inventado.')
        return None
    return {
        'source': 'NOAA', 'event_id': None,
        'place': location_label(event['location']),
        'magnitude': event['magnitude'],
        'latitude': lat, 'longitude': lon,
        'depth_km': event.get('depth_km'), 'url': CATALOG_INFO,
        'time_ms': None, 'tsunami': None,
        'coordinate_note': 'Coordenadas del catálogo NOAA/Zenodo',
    }

def latlon_to_global_pixels(lat, lon, zoom):
    lat = max(min(lat, 85.05112878), -85.05112878)
    sin_lat = math.sin(math.radians(lat))
    x = (lon + 180.0) / 360.0
    y = 0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)
    scale = 256 * (2 ** zoom)
    return x * scale, y * scale


def fetch_tile(z, x, y):
    """Descarga solo las teselas necesarias y conserva caché >=7 días."""
    n = 2 ** z
    if y < 0 or y >= n:
        raise RuntimeError('Tesela fuera del rango cartográfico')
    x = x % n
    cache = Path(os.getenv('SISMONAUTA_MAP_CACHE', 'output_efemerides/map_cache'))
    path = cache / str(z) / str(x) / f'{y}.png'
    if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        with Image.open(path) as im:
            return im.convert('RGB')
    url = f'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
    res = HTTP.get(url, timeout=(8, 20))
    res.raise_for_status()
    with Image.open(io.BytesIO(res.content)) as im:
        image = im.convert('RGB')
    if image.size != (256, 256):
        raise RuntimeError('Tesela cartográfica con dimensiones incorrectas')
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, 'PNG')
    return image


def build_full_map(epicenter, width, height, marker_x, marker_y):
    """Renderiza únicamente los tiles del encuadre solicitado.

    El epicentro se posiciona en (marker_x, marker_y) del lienzo, usando
    coordenadas Web Mercator y considerando cruce del antimeridiano.
    """
    lat, lon = epicenter['latitude'], epicenter['longitude']
    mag = epicenter.get('magnitude') or 8.0
    # Mapa amplio para reconocer la costa, pero preciso en el epicentro.
    # 1080 px abarcan unos 800-1400 km según magnitud.
    map_span_km = 900 if mag < 8.5 else 1300
    meters_per_px = (map_span_km * 1000) / width
    z_real = math.log2(156543.03392 * max(0.12, math.cos(math.radians(lat))) / meters_per_px)
    zoom = max(2, min(9, round(z_real)))
    gx, gy = latlon_to_global_pixels(lat, lon, zoom)
    left = gx - marker_x
    top = gy - marker_y
    tx0 = math.floor(left / 256)
    ty0 = math.floor(top / 256)
    tx1 = math.floor((left + width - 1) / 256)
    ty1 = math.floor((top + height - 1) / 256)
    canvas = Image.new('RGB', ((tx1 - tx0 + 1) * 256,
                               (ty1 - ty0 + 1) * 256))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            canvas.paste(fetch_tile(zoom, tx, ty),
                         ((tx - tx0) * 256, (ty - ty0) * 256))
    # Pillow permite fracciones negativas y corrige clipping del recorte.
    x = round(left - tx0 * 256)
    y = round(top - ty0 * 256)
    return canvas.crop((x, y, x + width, y + height)), zoom


def draw_epicenter(image, epicenter, cx, cy):
    """Círculos decorativos concéntricos; no representan ondas ni intensidad."""
    d = ImageDraw.Draw(image)
    cx, cy = round(cx), round(cy)
    for radius in (45, 85, 135):
        d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                  outline=(255, 255, 255), width=9)
        d.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                  outline=(245, 0, 128), width=6)
    d.line((cx-24, cy, cx+24, cy), fill='white', width=7)
    d.line((cx, cy-24, cx, cy+24), fill='white', width=7)
    d.ellipse((cx-13, cy-13, cx+13, cy+13), fill='white', outline=(215, 0, 110), width=6)


def create_image(event, path):
    """Mapa de la zona del terremoto como fondo, sin fotos ni inset duplicado."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    epicenter = fetch_usgs_epicenter(event)
    if not epicenter:
        # No publicar una tarjeta con un epicentro inventado.
        raise RuntimeError('Sin coordenadas fiables USGS/NOAA para generar el mapa del epicentro')

    # El mapa ocupa todo el fondo; el marcador va sobre la parte visible,
    # por encima del banner magenta. Debajo se mantiene el mapa real.
    cx, cy = W // 2, 465
    image, zoom = build_full_map(epicenter, W, H, cx, cy)
    image = image.convert('RGB')
    # Velo leve para integrar colores sin ocultar carreteras y topónimos.
    image = Image.blend(image, Image.new('RGB', (W, H), (17, 21, 38)), 0.12)
    draw_epicenter(image, epicenter, cx, cy)
    d = ImageDraw.Draw(image)

    label = f"TERREMOTO {event['magnitude']:.1f} en {location_label(event['location'])}"
    heading = 'UN DÍA COMO HOY'
    heading_font = font(61)
    heading_box = d.textbbox((0, 0), heading, font=heading_font)
    heading_height = heading_box[3] - heading_box[1]
    chosen = None
    for size in range(62, 27, -2):
        ft = font(size)
        lines = wrap(d, label, ft, W - 115)
        step = int(size * 1.08)
        if len(lines) <= 5 and len(lines) * step <= 330:
            chosen = (ft, lines, step)
            break
    if chosen is None:
        ft = font(27)
        lines = wrap(d, label, ft, W - 115)
        step = 30
    else:
        ft, lines, step = chosen
    banner_height = 28 + heading_height + 15 + len(lines) * step + 34
    footer_top = 1235
    banner_top = footer_top - banner_height

    # Información geográfica: fuente exacta de la coordenada, no de foto.
    # Solo se mantienen las coordenadas históricas: sin rótulo "EPICENTRO".
    source = epicenter['source']
    coords = (f"{abs(epicenter['latitude']):.3f}°{'N' if epicenter['latitude'] >= 0 else 'S'}  "
              f"{abs(epicenter['longitude']):.3f}°{'E' if epicenter['longitude'] >= 0 else 'O'}")
    coord_font = font(23)
    panel_w = d.textbbox((0, 0), coords, font=coord_font)[2] + 52
    panel_y = max(30, cy - 240)
    d.rounded_rectangle((26, panel_y, 26 + panel_w, panel_y + 54),
                        radius=17, fill=(7, 12, 36), outline=(255, 255, 255), width=2)
    d.text((50, panel_y + 12), coords, font=coord_font, fill='white')

    # Atribución abreviada admitida por OpenStreetMap para mapas estáticos.
    # El texto "OpenStreetMap contributors" ya no se dibuja.
    attribution = '© OpenStreetMap'
    at_font = font(19, bold=False)
    at_bb = d.textbbox((0, 0), attribution, font=at_font)
    d.rounded_rectangle((25, banner_top - 51, at_bb[2] + 49, banner_top - 10),
                        radius=9, fill=(7, 12, 36))
    d.text((38, banner_top - 42), attribution, font=at_font, fill='white')

    overlay = Image.new('RGBA', (W, banner_height), (183, 0, 112, 225))
    image = image.convert('RGBA')
    image.paste(overlay, (0, banner_top), overlay)
    d = ImageDraw.Draw(image)
    d.rectangle((0, banner_top, W, banner_top + 7), fill=(255, 76, 180))

    def centered_text(y, text, used_font, stroke=2):
        bb = d.textbbox((0, 0), text, font=used_font, stroke_width=stroke)
        tw = bb[2] - bb[0]
        d.text(((W - tw) / 2, y), text, font=used_font,
               fill='white', stroke_width=stroke, stroke_fill=(17, 8, 30))

    y = banner_top + 28
    centered_text(y, heading, heading_font, stroke=2)
    y += heading_height + 15
    for line in lines:
        centered_text(y, line, ft, stroke=2)
        y += step

    d.rectangle((0, footer_top, W, H), fill=(7, 12, 36))
    d.text((42, 1270), 'SISMONAUTA  |  EFEMÉRIDES SÍSMICAS',
           font=font(29), fill='white')
    image.convert('RGB').save(path, 'JPEG', quality=93, optimize=True)
    log('MAPA GENERADO:', source, coords, '| zoom:', zoom)
    return epicenter


class Github:
    def __init__(self):
        self.repo = os.environ['GITHUB_REPOSITORY']
        self.branch = os.getenv('GITHUB_REF_NAME', 'main')
        self.base = 'https://api.github.com/repos/' + self.repo
        self.http = requests.Session()
        self.http.headers.update({
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        res = self.http.get(self.base, timeout=25)
        res.raise_for_status()
        if res.json().get('private'):
            raise RuntimeError('El repositorio debe ser público para que Meta recupere imágenes')

    def endpoint(self, path):
        return self.base + '/contents/' + urllib.parse.quote(path, safe='/')

    def read(self, path):
        res = self.http.get(self.endpoint(path), params={'ref': self.branch}, timeout=30)
        if res.status_code == 404:
            return None
        res.raise_for_status()
        return base64.b64decode(res.json()['content'])

    def write(self, path, raw):
        for i in range(3):
            res = self.http.get(self.endpoint(path), params={'ref': self.branch}, timeout=30)
            sha = None if res.status_code == 404 else res.json().get('sha')
            if res.status_code != 404:
                res.raise_for_status()
            body = {
                'message': 'SISMONAUTA efemérides: ' + path,
                'branch': self.branch,
                'content': base64.b64encode(raw).decode('ascii'),
            }
            if sha:
                body['sha'] = sha
            put = self.http.put(self.endpoint(path), json=body, timeout=60)
            if put.status_code in (409, 422) and i < 2:
                time.sleep(2 + i)
                continue
            put.raise_for_status()
            return put.json()

    def image_url(self, path, commit):
        return 'https://raw.githubusercontent.com/' + self.repo + '/' + commit + '/' + urllib.parse.quote(path, safe='/')


def graph(method, path, token, fields=None, base=IG_BASE):
    url = f'{base}/{GRAPH_VERSION}/{path}'
    payload = dict(fields or {}, access_token=token)
    res = requests.get(url, params=payload, timeout=50) if method == 'GET' else requests.post(url, data=payload, timeout=75)
    try:
        data = res.json()
    except ValueError:
        raise RuntimeError('Meta no devolvió JSON')
    if not res.ok or data.get('error'):
        detail = (data.get('error') or {}).get('message') or str(data)
        raise RuntimeError(f'Meta HTTP {res.status_code}: {detail[:200]}')
    return data


def validate_meta():
    ig_user = os.environ['INSTAGRAM_USER_ID']
    ig_token = os.environ['INSTAGRAM_TOKEN']
    fb_page = os.environ['FACEBOOK_PAGE_ID']
    fb_token = os.environ['FACEBOOK_PAGE_TOKEN']
    me = graph('GET', 'me', ig_token, {'fields': 'id,username'})
    if str(me.get('id')) != ig_user or str(me.get('username', '')).lower() != ACCOUNT:
        raise RuntimeError('Cuenta de Instagram distinta de @sismonauta')
    page = graph('GET', fb_page, fb_token, {'fields': 'id,name,username'}, FB_BASE)
    if str(page.get('id')) != fb_page:
        raise RuntimeError('Identificación de fanpage incorrecta')
    if page.get('username') and str(page['username']).lower() != ACCOUNT:
        raise RuntimeError('Fanpage distinta de SISMONAUTA')
    return (ig_user, ig_token), (fb_page, fb_token)


def publish_ig(user, token, image_url, caption):
    box = graph('POST', user + '/media', token, {'image_url': image_url, 'caption': caption})['id']
    for _ in range(20):
        time.sleep(7)
        status = graph('GET', box, token, {'fields': 'status_code'}).get('status_code')
        if status == 'FINISHED':
            break
        if status in ('ERROR', 'EXPIRED'):
            raise RuntimeError('Instagram rechazó la imagen: ' + str(status))
    else:
        raise RuntimeError('Instagram no confirmó procesamiento')
    result = graph('POST', user + '/media_publish', token, {'creation_id': box})
    if not result.get('id'):
        raise RuntimeError('Instagram no devolvió ID')
    return str(result['id'])


def publish_fb(page, token, image_url, caption):
    result = graph('POST', page + '/photos', token, {'url': image_url, 'caption': caption, 'published': 'true'}, FB_BASE)
    identifier = result.get('post_id') or result.get('id')
    if not identifier:
        raise RuntimeError('Facebook no devolvió ID')
    return str(identifier)


def run_tests():
    sample = ('index,Year,Mo,Dy,Mag,Location_Name,Deaths,Total_Deaths,Tsu,Focal_Depth_(km),Latitude,Longitude\n'
              '0,1960,5,22,9.5,CHILE: VALDIVIA,1600,1655,NA,30,-39,-73\n'
              '1,1900,5,22,8.5,JAPAN,NA,NA,NA,50,35,139\n'
              '2,1930,2,28,8.2,PERU: LIMA,120,120,NA,40,-12,-77\n')
    events = parse_catalog(sample)
    assert len(events) == 3
    event = choose_event(events, date(2026, 5, 22), 8.0, {'posts': {}})
    assert event and event['deaths'] == 1655 and event['magnitude'] == 9.5
    text = event_caption(event, date(2026, 5, 22))
    assert 'hace 66 años' in text and '1.655' in text and 'magnitud 9.5' in text
    assert choose_event(events, date(2026, 1, 1), 8.0, {'posts': {}}) is None
    assert choose_event(events, date(2026, 5, 22), 8.0,
                        {'posts': {f"2026-05-22:{event['id']}": {}}}) is None
    log('PRUEBAS APROBADAS: fecha exacta, magnitud, fatalidades y antiduplicados')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--publish', action='store_true')
    ap.add_argument('--test', action='store_true')
    ap.add_argument('--out', default='output_efemerides')
    ap.add_argument('--date', help='YYYY-MM-DD; por defecto fecha en America/Santiago')
    ap.add_argument('--min-mag', type=float, default=8.0)
    ap.add_argument('--offline', action='store_true')
    args = ap.parse_args()
    run_tests()
    if args.test:
        return 0
    if args.min_mag < 0 or args.min_mag > 10:
        raise ValueError('--min-mag fuera de rango')
    today = date.fromisoformat(args.date) if args.date else datetime.now(ZoneInfo(TIMEZONE)).date()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    gh = Github() if args.publish else None
    if gh:
        old = gh.read(STATE_FILE)
        state = json.loads(old) if old else {'posts': {}}
    else:
        state = {'posts': {}}
    state.setdefault('posts', {})
    cache = out / 'catalogo_noaa_1900_2024.csv'
    events = parse_catalog(fetch_catalog(cache, args.offline))
    log('CATALOGO:', len(events), 'registros | FECHA:', today, '| MAG MIN:', args.min_mag)
    event = choose_event(events, today, args.min_mag, state)
    if not event:
        report = {
            'status': 'no_anniversary', 'published': False,
            'reason': 'No hay terremoto documentado en esta fecha con magnitud y fatalidades verificables',
            'date': today.isoformat(), 'min_magnitude': args.min_mag,
            'source': CATALOG_INFO,
        }
        atomic_json(out / 'estado_publicacion.json', report)
        log('SIN PUBLICACIÓN:', report['reason'])
        return 0
    label = location_label(event['location'])
    caption = event_caption(event, today)
    image_path = out / f"efemeride_{today.isoformat()}_{event['id']}.jpg"
    epicenter_info = create_image(event, image_path)
    payload = {
        'event': {**event, 'date': event['date'].isoformat()},
        'anniversary_years': today.year - event['date'].year,
        'location_display': label,
        'caption': caption,
        'source': CATALOG_SOURCE,
        'source_url': CATALOG_INFO,
        'publication_date': today.isoformat(),
        'image': str(image_path),
        'epicenter': epicenter_info,
    }
    atomic_json(out / 'efemeride.json', payload)
    log('EFEMÉRIDE:', event['date'], 'M', event['magnitude'], label, 'fallecidos:', event['deaths'])
    if not args.publish:
        atomic_json(out / 'estado_publicacion.json', {'status': 'preview_only', 'published': False, 'image': str(image_path)})
        log('VISTA PREVIA: ninguna publicación enviada')
        return 0
    key = f"{today.isoformat()}:{event['id']}"
    row = state['posts'].setdefault(key, {'targets': {'instagram': {'status': 'pending'}, 'facebook': {'status': 'pending'}}})
    targets = row.setdefault('targets', {})
    for platform in ('instagram', 'facebook'):
        targets.setdefault(platform, {'status': 'pending'})
        if targets[platform].get('status') == 'uncertain':
            raise RuntimeError('Publicación previa incierta: ' + platform)
    if all(targets[p].get('status') == 'published' for p in targets):
        log('EFEMÉRIDE YA PUBLICADA EN AMBAS REDES')
        return 0
    ig_creds, fb_creds = validate_meta()
    remote = f"sismonauta_efemerides/images/{today.isoformat()}_{event['id']}.jpg"
    uploaded = gh.write(remote, image_path.read_bytes())
    commit = uploaded['commit']['sha']
    image_url = gh.image_url(remote, commit)
    good = False
    for attempt in range(7):
        try:
            check = HTTP.get(image_url, timeout=20)
            if check.ok and check.headers.get('Content-Type', '').startswith('image/'):
                good = True
                break
        except requests.RequestException:
            pass
        time.sleep(4)
    if not good:
        raise RuntimeError('La imagen pública no está accesible para Meta')
    row['event'] = {'date': event['date'].isoformat(), 'magnitude': event['magnitude'],
                    'location': event['location'], 'deaths': event['deaths']}
    row['image_url'] = image_url
    gh.write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2).encode())
    errors = []
    for platform, creds in [('instagram', ig_creds), ('facebook', fb_creds)]:
        target = targets[platform]
        if target.get('status') == 'published':
            continue
        target['status'] = 'uncertain'
        gh.write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2).encode())
        try:
            identifier = (publish_ig(*creds, image_url, caption)
                          if platform == 'instagram' else publish_fb(*creds, image_url, caption))
            target.update(status='published', id=str(identifier))
            gh.write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2).encode())
            log('PUBLICADO:', platform, identifier)
        except Exception as exc:
            target['error'] = safe_error(exc)
            gh.write(STATE_FILE, json.dumps(state, ensure_ascii=False, indent=2).encode())
            errors.append(f'{platform}: {safe_error(exc)}')
    complete = all(targets[p].get('status') == 'published' for p in ('instagram', 'facebook'))
    atomic_json(out / 'estado_publicacion.json', {
        'status': 'published' if complete else 'partial_or_uncertain', 'published': complete,
        'date': today.isoformat(), 'event_id': event['id'], 'targets': targets,
    })
    if errors:
        raise RuntimeError('Publicación parcial; revisar Meta: ' + ' / '.join(errors))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        log('ERROR:', safe_error(exc))
        sys.exit(1)
