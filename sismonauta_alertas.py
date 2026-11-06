#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA | ALERTAS M7+

Monitoreo mundial de terremotos M >= 7.0.
Fuentes: USGS y boletines oficiales NOAA/PTWC/NTWC.

Imagen: 1080 x 1350; banner ÚLTIMO MINUTO, mapa y magnitud/región.
Publicaciones independientes: Instagram foto, Facebook foto y Facebook feed
con fotografía adjunta. Persistencia: GitHub Contents API, sin git push.

Dependencias: pip install requests Pillow matplotlib cartopy
Uso: python sismonauta_alertas.py [--publish] [--out output_alertas]
     python sismonauta_alertas.py --test
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import html
import io
import json
import os
import random
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

APP = 'SISMONAUTA ALERTAS M7+'
VERSION = os.getenv('IG_GRAPH_VERSION', 'v25.0')
IG_BASE = os.getenv('IG_GRAPH_BASE', 'https://graph.instagram.com').rstrip('/')
FB_BASE = 'https://graph.facebook.com'
USGS_URL = 'https://earthquake.usgs.gov/fdsnws/event/1/query'
PTWC_FEED = 'https://www.tsunami.gov/events/xml/PHEBAtom.xml'
NTWC_FEED = 'https://www.tsunami.gov/events/xml/PAAQAtom.xml'
STATE_PATH = 'sismonauta_alertas/state.json'
IMAGE_DIR = 'sismonauta_alertas/images'
OUT = Path('output_alertas')
MIN_MAG = 7.0
CHILE = ZoneInfo('America/Santiago')
UTC = timezone.utc
HTTP = requests.Session()
HTTP.headers.update({'User-Agent': 'Sismonauta-Alertas/1.0', 'Accept': '*/*'})
LOOKBACK_HOURS = 24
MAX_EVENTS = 10
PLATFORMS = ('instagram', 'facebook', 'facebook_feed')
RETRY_STATUSES = (429, 500, 502, 503, 504)


def log(*values):
    print(*values, flush=True)


def now_utc():
    return datetime.now(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat()


def clean(value):
    value = html.unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))
    return re.sub(r'\s+', ' ', value).strip()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    tmp.replace(path)


def request_json(url, params=None, timeout=35):
    response = HTTP.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def request_text(url, timeout=35):
    response = HTTP.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def safe_text(value):
    return re.sub(r'[^a-zA-Z0-9_-]', '_', str(value))[:90]


def short_error(exc):
    text = str(exc)
    for secret in (os.getenv('INSTAGRAM_TOKEN', ''), os.getenv('FACEBOOK_PAGE_TOKEN', ''), os.getenv('GH_TOKEN', '')):
        if secret:
            text = text.replace(secret, '***')
    return text[:300]


REGION_NAMES = {
    'japan': 'JAPÓN', 'japan region': 'JAPÓN', 'chile': 'CHILE',
    'peru': 'PERÚ', 'mexico': 'MÉXICO', 'indonesia': 'INDONESIA',
    'vanuatu': 'VANUATU', 'philippines': 'FILIPINAS',
    'papua new guinea': 'PAPÚA NUEVA GUINEA', 'new zealand': 'NUEVA ZELANDA',
    'russia': 'RUSIA', 'turkey': 'TURQUÍA', 'türkiye': 'TURQUÍA',
    'ecuador': 'ECUADOR', 'panama': 'PANAMÁ',
    'alaska': 'ALASKA, EE. UU.', 'california': 'CALIFORNIA, EE. UU.',
    'united states': 'ESTADOS UNIDOS', 'solomon islands': 'ISLAS SALOMÓN',
    'tonga': 'TONGA', 'fiji': 'FIYI', 'taiwan': 'TAIWÁN',
    'greece': 'GRECIA', 'iceland': 'ISLANDIA',
    'afghanistan': 'AFGANISTÁN', 'pakistan': 'PAKISTÁN',
    'haiti': 'HAITÍ', 'colombia': 'COLOMBIA', 'argentina': 'ARGENTINA',
    'bolivia': 'BOLIVIA', 'samoa': 'SAMOA',
    'kamchatka': 'KAMCHATKA, RUSIA', 'kuril islands': 'ISLAS KURILES',
    'south sandwich islands': 'ISLAS SANDWICH DEL SUR',
    'bonin islands': 'ISLAS BONIN, JAPÓN',
    'southern mid-atlantic ridge': 'DORSAL MESOATLÁNTICA SUR',
}


def region_name(place):
    place = clean(place)
    if not place:
        return 'REGIÓN NO DETERMINADA'
    tail = place.split(',')[-1].strip()
    lower = tail.lower()
    if lower in REGION_NAMES:
        return REGION_NAMES[lower]
    for key, value in REGION_NAMES.items():
        if key in lower:
            return value
    return tail.upper()


def fetch_earthquakes(hours=LOOKBACK_HOURS):
    end = now_utc()
    start = end - timedelta(hours=hours)
    data = request_json(USGS_URL, params={
        'format': 'geojson', 'starttime': start.strftime('%Y-%m-%dT%H:%M:%S'),
        'endtime': end.strftime('%Y-%m-%dT%H:%M:%S'),
        'minmagnitude': MIN_MAG, 'eventtype': 'earthquake', 'orderby': 'time-asc',
    }, timeout=50)
    events = []
    for feature in data.get('features', []):
        properties = feature.get('properties') or {}
        geometry = feature.get('geometry') or {}
        coordinates = geometry.get('coordinates') or []
        if len(coordinates) < 3:
            continue
        magnitude = properties.get('mag')
        if magnitude is None or float(magnitude) < MIN_MAG:
            continue
        milliseconds = properties.get('time')
        event_id = feature.get('id')
        if milliseconds is None or not event_id:
            continue
        place = properties.get('place') or ''
        events.append({
            'id': str(event_id), 'mag': float(magnitude), 'place': place,
            'region': region_name(place), 'latitude': float(coordinates[1]),
            'longitude': float(coordinates[0]), 'depth': float(coordinates[2]),
            'time': datetime.fromtimestamp(milliseconds / 1000, UTC),
            'url': properties.get('url') or '',
            'detail_url': properties.get('detail') or '',
            'tsunami_flag': properties.get('tsunami'), 'status': properties.get('status') or '',
        })
    return events


def get_detail(event):
    try:
        if event['detail_url']:
            return request_json(event['detail_url'], timeout=35)
    except Exception as exc:
        log('Detalle USGS:', short_error(exc))
    return {}


def nearby_cities(event, detail):
    products = (detail.get('properties') or {}).get('products') or {}
    for product in products.get('nearby-cities', []):
        contents = product.get('contents') or {}
        for name, value in contents.items():
            if not name.endswith('nearby-cities.json'):
                continue
            url = value.get('url')
            if not url:
                continue
            try:
                data = request_json(url, timeout=25)
                if isinstance(data, dict):
                    data = data.get('cities', [])
                result = []
                for city in data[:4]:
                    name = city.get('name')
                    if not name:
                        continue
                    distance = city.get('distance')
                    if distance is not None:
                        name += f' ({float(distance):.0f} km)'
                    result.append(name)
                if result:
                    return result
            except Exception as exc:
                log('Ciudades USGS:', short_error(exc))
    return []


ATOM_NS = {'a': 'http://www.w3.org/2005/Atom'}


def parse_time(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)
    except Exception:
        return None


def atom_entries(url):
    root = ET.fromstring(request_text(url, timeout=30))
    entries = []
    for item in root.findall('.//a:entry', ATOM_NS):
        def field(name):
            node = item.find('a:' + name, ATOM_NS)
            return clean(''.join(node.itertext())) if node is not None else ''
        link = ''
        for node in item.findall('a:link', ATOM_NS):
            href = node.attrib.get('href', '')
            if href.startswith('https://'):
                link = href
                break
        entries.append({
            'title': field('title'), 'summary': field('summary'),
            'content': field('content'), 'updated': parse_time(field('updated')),
            'link': link,
        })
    return entries


def normalize_words(value):
    return {word.lower() for word in re.findall(r'[A-Za-z]{5,}', value or '')
            if word.lower() not in {'earthquake', 'region', 'magnitude', 'tsunami', 'islands', 'offshore'}}


def bulletin_matches(entry, event):
    """Exige correlación temporal/geográfica y magnitud similar cuando exista."""
    updated = entry.get('updated')
    if updated is None:
        return False
    gap = (updated - event['time']).total_seconds()
    if gap < -600 or gap > 4 * 3600:
        return False
    text = (entry['title'] + ' ' + entry['summary'] + ' ' + entry['content']).lower()
    place_words = normalize_words(event['place'])
    if not place_words or not any(word in text for word in place_words):
        return False
    magnitudes = re.findall(r'(?:magnitude|mag)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)', text, flags=re.I)
    if magnitudes and not any(abs(float(value) - event['mag']) <= 0.6 for value in magnitudes):
        return False
    return True


def classify_bulletin(text):
    upper = text.upper()
    negatives = (
        'NO TSUNAMI WARNING, ADVISORY, WATCH, OR THREAT', 'NO TSUNAMI THREAT',
        'NO TSUNAMI WARNING', 'NO DESTRUCTIVE TSUNAMI THREAT',
    )
    if any(value in upper for value in negatives):
        return 'no_threat'
    positives = (
        'TSUNAMI THREAT MESSAGE', 'POTENTIAL THREAT',
        'TSUNAMI WARNING IS IN EFFECT', 'TSUNAMI WARNING IN EFFECT',
        'TSUNAMI WATCH IS IN EFFECT', 'TSUNAMI ADVISORY IS IN EFFECT',
    )
    if any(value in upper for value in positives):
        return 'threat'
    return 'information'


def parse_risk_areas(text):
    upper = html.unescape(text).upper()
    patterns = [
        r'TSUNAMI WAVES ARE FORECAST TO BE HAZARDOUS ALONG THE COASTS OF(.*?)(?:RECOMMENDED ACTIONS|NEXT UPDATE|PRELIMINARY EARTHQUAKE|$)',
        r'TSUNAMI THREAT FORECAST(.*?)(?:RECOMMENDED ACTIONS|PRELIMINARY EARTHQUAKE|$)',
    ]
    for pattern in patterns:
        found = re.search(pattern, upper, re.S)
        if not found:
            continue
        chunk = re.sub(r'\s+', ' ', found.group(1)).strip()
        if 15 <= len(chunk) <= 650:
            return chunk.capitalize()
    return ''


def tsunami_status(event):
    """'unknown' nunca debe interpretarse como inexistencia de amenaza."""
    all_bulletins = []
    successful = 0
    for center, url in (('PTWC', PTWC_FEED), ('NTWC', NTWC_FEED)):
        try:
            entries = atom_entries(url)
            successful += 1
            for entry in entries:
                entry['center'] = center
                all_bulletins.append(entry)
        except Exception as exc:
            log('Boletines', center, short_error(exc))
    matches = [entry for entry in all_bulletins if bulletin_matches(entry, event)]
    matches.sort(key=lambda item: item['updated'], reverse=True)
    if matches:
        newest = matches[0]
        content = newest['title'] + '\n' + newest['summary'] + '\n' + newest['content']
        status = classify_bulletin(content)
        if status == 'threat':
            areas = parse_risk_areas(content)
            message = ('El centro ' + newest['center'] +
                       ' publicó un boletín de amenaza o alerta de tsunami asociado al evento.')
            message += (' Zonas mencionadas: ' + areas[:400] if areas else
                        ' Las zonas específicas deben consultarse en el boletín oficial.')
            return {'status': 'threat', 'message': message, 'areas': areas,
                    'center': newest['center'], 'url': newest['link']}
        if status == 'no_threat':
            return {'status': 'no_threat', 'message':
                    'El boletín más reciente de ' + newest['center'] +
                    ' asociado al evento no indica una amenaza o alerta de tsunami en su ámbito de cobertura.',
                    'areas': '', 'center': newest['center'], 'url': newest['link']}
    return {'status': 'unknown',
            'message': 'Estado de tsunami pendiente de confirmación oficial. Consulta los centros de alerta y las autoridades costeras.',
            'areas': '', 'center': 'PTWC/NTWC' if successful else 'no disponible',
            'url': 'https://www.tsunami.gov/'}


def build_map(event, path):
    """Mapa original Cartopy con coordenadas reales USGS."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    latitude, longitude = event['latitude'], event['longitude']
    projection = ccrs.PlateCarree(central_longitude=longitude)
    fig = plt.figure(figsize=(9, 9), dpi=160, facecolor='#061c33')
    ax = fig.add_axes([0, 0, 1, 1], projection=projection)
    ax.set_extent([-13, 13, latitude - 12, latitude + 12], crs=projection)
    ax.set_facecolor('#063962')
    ax.add_feature(cfeature.OCEAN.with_scale('110m'), facecolor='#063962', zorder=0)
    ax.add_feature(cfeature.LAND.with_scale('110m'), facecolor='#386944', zorder=1)
    ax.add_feature(cfeature.COASTLINE.with_scale('110m'), edgecolor='#e0e9db', linewidth=1.25, zorder=2)
    ax.add_feature(cfeature.BORDERS.with_scale('110m'), edgecolor='#c8d4cf', linewidth=0.65, zorder=2)
    ax.gridlines(draw_labels=False, linewidth=0.35, color='white', alpha=0.18)
    ax.scatter([longitude], [latitude], transform=ccrs.PlateCarree(), s=1800,
               facecolors='none', edgecolors='#ff2638', linewidths=2, zorder=8)
    ax.scatter([longitude], [latitude], transform=ccrs.PlateCarree(), s=650,
               facecolors='none', edgecolors='#ff3440', linewidths=3, zorder=9)
    ax.scatter([longitude], [latitude], transform=ccrs.PlateCarree(), s=120,
               color='#ff142c', edgecolors='white', linewidths=1.8, zorder=10)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, facecolor='#061c33')
    plt.close(fig)


def font(size):
    paths = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
             '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf']
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def split_lines(draw, text, selected_font, width):
    words = text.split()
    lines = []
    current = ''
    for word in words:
        candidate = (current + ' ' + word).strip()
        bbox = draw.textbbox((0, 0), candidate, font=selected_font, stroke_width=3)
        if bbox[2] - bbox[0] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def banner_text(draw, text, bounds, max_size):
    x1, y1, x2, y2 = bounds
    available_w = x2 - x1 - 48
    available_h = y2 - y1 - 24
    for size in range(max_size, 23, -2):
        selected = font(size)
        lines = split_lines(draw, text, selected, available_w)
        line_height = int(size * 1.25)
        if len(lines) * line_height <= available_h:
            break
    else:
        raise RuntimeError('El titular no cabe en el banner')
    top = y1 + (y2 - y1 - len(lines) * line_height) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=selected, stroke_width=3)
        width = bbox[2] - bbox[0]
        x = (x1 + x2 - width) // 2
        draw.text((x, top), line, font=selected, fill='white', stroke_width=3, stroke_fill='black')
        top += line_height


def make_image(event, output_path):
    """Diseño original 1080x1350, sin cambios en banners y tipografías."""
    width, height = 1080, 1350
    temp_map = OUT / (safe_text(event['id']) + '_map.png')
    build_map(event, temp_map)
    image = Image.new('RGB', (width, height), '#071d34')
    with Image.open(temp_map) as im:
        map_image = im.convert('RGB')
    map_image = ImageOps.fit(map_image, (width, height), Image.Resampling.LANCZOS)
    image.paste(map_image, (0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((23, 30, 1057, 205), radius=18, fill='#d40a16', outline='#ff3444', width=5)
    banner_text(draw, 'ÚLTIMO MINUTO', (35, 37, 1045, 195), 94)
    draw.rounded_rectangle((18, 1105, 1062, 1315), radius=16, fill='#a20812', outline='#ff2335', width=5)
    headline = f"SISMO DE MAGNITUD {event['mag']:.1f} EN {event['region']}"
    banner_text(draw, headline, (35, 1120, 1045, 1298), 62)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, 'JPEG', quality=93, optimize=True)


def make_caption(event, cities, tsunami):
    local_time = event['time'].astimezone(CHILE)
    formatted_date = local_time.strftime('%d/%m/%Y a las %H:%M')
    cities_text = ', '.join(cities[:4]) if cities else 'USGS no identifica ciudades cercanas en los datos consultados.'
    text = (
        '🚨 ÚLTIMO MINUTO | SISMONAUTA\n\n'
        f"Un terremoto de magnitud {event['mag']:.1f} fue registrado por el Servicio Geológico de "
        f"Estados Unidos (USGS) en {event['region']}.\n\n"
        f"📍 Ubicación: {event['place']}\n"
        f"📊 Magnitud: {event['mag']:.1f}\n"
        f"⬇️ Profundidad: {event['depth']:.1f} km\n"
        f"🕒 Hora: {formatted_date} (hora de Chile)\n"
        f"🌐 Coordenadas: {event['latitude']:.3f}, {event['longitude']:.3f}\n\n"
        f"🏙️ Ciudades cercanas: {cities_text}\n\n"
        f"🌊 SITUACIÓN DE TSUNAMI\n{tsunami['message']}\n\n"
        f"Datos sísmicos: USGS\n{event['url']}\n\n"
        f"Boletines de tsunami:\n{tsunami['url'] or 'https://www.tsunami.gov/'}\n\n"
        '#SISMONAUTA #UltimoMinuto #Terremoto #Sismo #USGS #Tsunami #Sismologia'
    )
    if len(text) > 2200:
        text = text[:2197] + '...'
    return text


class Github:
    """GitHub Contents API: verifica SHA, reintentos seguros y sin sobrescribir historial concurrente."""
    def __init__(self):
        self.repository = os.environ['GITHUB_REPOSITORY']
        self.branch = os.getenv('GITHUB_REF_NAME') or 'main'
        token = os.environ['GH_TOKEN']
        self.base = 'https://api.github.com/repos/' + self.repository
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        })
        info = self._request('GET', self.base, timeout=25)
        info.raise_for_status()
        if info.json().get('private'):
            raise RuntimeError('El repositorio debe ser público para alojar imágenes accesibles desde Meta.')

    def endpoint(self, path):
        return self.base + '/contents/' + urllib.parse.quote(path, safe='/')

    def _request(self, method, url, **kwargs):
        for attempt in range(5):
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.RequestException:
                if method != 'GET' or attempt == 4:
                    raise
                time.sleep(min(12, 1.5 * (attempt + 1)))
                continue
            if response.status_code in RETRY_STATUSES and attempt < 4 and method == 'GET':
                time.sleep(min(12, 1.5 * (attempt + 1)))
                continue
            return response
        raise RuntimeError('GitHub: demasiados intentos de consulta')

    def _read_raw_sha(self, path):
        response = self._request('GET', self.endpoint(path), params={'ref': self.branch}, timeout=30)
        if response.status_code == 404:
            return None, None
        response.raise_for_status()
        data = response.json()
        return base64.b64decode(data['content']), data.get('sha')

    def read(self, path):
        raw, _ = self._read_raw_sha(path)
        return json.loads(raw.decode('utf-8')) if raw is not None else None

    def write_bytes(self, path, content):
        endpoint = self.endpoint(path)
        target_hash = hashlib.sha256(content).hexdigest()
        for attempt in range(6):
            previous, sha = self._read_raw_sha(path)
            if previous is not None and hashlib.sha256(previous).hexdigest() == target_hash:
                return {'status': 'unchanged'}
            payload = {
                'message': 'SISMONAUTA alerta: ' + path,
                'branch': self.branch,
                'content': base64.b64encode(content).decode('ascii'),
            }
            if sha:
                payload['sha'] = sha
            try:
                response = self.session.put(endpoint, json=payload, timeout=60)
            except requests.RequestException:
                # PUT puede haberse completado antes de un timeout; confirmar primero.
                current, _ = self._read_raw_sha(path)
                if current is not None and hashlib.sha256(current).hexdigest() == target_hash:
                    return {'status': 'verified_after_timeout'}
                if attempt == 5:
                    raise
                time.sleep(2 + attempt)
                continue
            if response.ok:
                return response.json()
            if response.status_code in (409, 422, 429, 500, 502, 503, 504):
                current, _ = self._read_raw_sha(path)
                if current is not None and hashlib.sha256(current).hexdigest() == target_hash:
                    return {'status': 'verified_after_conflict'}
                if path == STATE_PATH and response.status_code in (409, 422):
                    # Evitar sobrescribir otro estado completo en un conflicto.
                    raise RuntimeError('GitHub: historial modificado por otra ejecución; reintentar tras serializar workflow')
                if attempt < 5:
                    time.sleep(2 + attempt)
                    continue
            response.raise_for_status()
        raise RuntimeError('GitHub: conflictos o errores persistentes al escribir ' + path)

    def write_json(self, path, data):
        raw = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8')
        return self.write_bytes(path, raw)

    def raw_url(self, path):
        return ('https://raw.githubusercontent.com/' + self.repository + '/' +
                urllib.parse.quote(self.branch, safe='') + '/' + urllib.parse.quote(path, safe='/'))


def graph(method, path, token, parameters=None, base=None):
    base = base or IG_BASE
    endpoint = f'{base}/{VERSION}/{path}'
    values = dict(parameters or {})
    values['access_token'] = token
    response = requests.get(endpoint, params=values, timeout=55) if method == 'GET' else requests.post(endpoint, data=values, timeout=75)
    try:
        data = response.json()
    except Exception:
        raise RuntimeError('Meta respondió sin JSON; HTTP ' + str(response.status_code))
    if not response.ok or data.get('error'):
        error = data.get('error') or {}
        raise RuntimeError('Meta API HTTP ' + str(response.status_code) + ': ' + str(error.get('message', data))[:200])
    return data


def validate_instagram():
    """Mantener el IG ID configurado original como destino de publicación."""
    ig_token = os.environ['INSTAGRAM_TOKEN']
    ig_id = os.environ['INSTAGRAM_USER_ID']
    ig = graph('GET', 'me', ig_token, {'fields': 'id,username'})
    if ig.get('username', '').lower() != 'sismonauta':
        raise RuntimeError('Instagram no corresponde a @sismonauta')
    if str(ig.get('id')) != str(ig_id):
        # Identificadores id/user_id pueden variar por API. Para evitar publicar
        # en otra cuenta, consultar el destino configurado y validar username.
        try:
            target = graph('GET', ig_id, ig_token, {'fields': 'id,username'})
        except Exception as exc:
            raise RuntimeError('INSTAGRAM_USER_ID no coincide con /me y no se pudo verificar: ' + short_error(exc))
        if str(target.get('id')) != str(ig_id) or str(target.get('username', '')).lower() != 'sismonauta':
            raise RuntimeError('INSTAGRAM_USER_ID no corresponde a @sismonauta')
    log('Instagram: @sismonauta verificado | ID configurado:', ig_id)
    return ig_id, ig_token


def validate_facebook():
    fb_token = os.environ['FACEBOOK_PAGE_TOKEN']
    fb_id = os.environ['FACEBOOK_PAGE_ID']
    fb = graph('GET', fb_id, fb_token, {'fields': 'id,name,username'}, FB_BASE)
    if str(fb.get('id')) != str(fb_id):
        raise RuntimeError('Fanpage de Facebook incorrecta')
    if fb.get('username') and str(fb['username']).lower() != 'sismonauta':
        raise RuntimeError('Fanpage distinta de SISMONAUTA')
    log('Facebook:', fb.get('name'))
    return fb_id, fb_token


def validate_accounts():
    ig_id, ig_token = validate_instagram()
    fb_id, fb_token = validate_facebook()
    return ig_id, ig_token, fb_id, fb_token


def publish_instagram(user_id, token, url, caption):
    container = graph('POST', f'{user_id}/media', token, {'image_url': url, 'caption': caption})['id']
    finished = False
    for attempt in range(20):
        time.sleep(6)
        result = graph('GET', container, token, {'fields': 'status_code'})
        status = result.get('status_code')
        if status == 'FINISHED':
            finished = True
            break
        if status in ('ERROR', 'EXPIRED'):
            raise RuntimeError('Instagram: contenedor ' + str(status))
    if not finished:
        raise RuntimeError('Instagram no terminó de procesar el contenedor')
    result = graph('POST', f'{user_id}/media_publish', token, {'creation_id': container})
    if not result.get('id'):
        raise RuntimeError('Instagram no confirmó publicación')
    return str(result['id'])


def publish_facebook(page_id, token, url, caption):
    result = graph('POST', f'{page_id}/photos', token,
                   {'url': url, 'caption': caption, 'published': 'true'}, FB_BASE)
    identifier = result.get('post_id') or result.get('id')
    if not identifier:
        raise RuntimeError('Facebook no confirmó publicación fotográfica')
    return str(identifier)


def publish_facebook_feed(page_id, token, url, caption):
    """Post público con foto adjunta, sin reemplazar /photos público.

    Foto unpublished -> ID -> feed attached_media[0]. Si el post se crea pero
    no es verificable, se considera incierto y no se reintenta automáticamente.
    """
    uploaded = graph('POST', f'{page_id}/photos', token,
                     {'url': url, 'published': 'false'}, FB_BASE)
    photo_id = str(uploaded.get('id') or '')
    if not photo_id.isdigit():
        raise RuntimeError('Facebook feed: carga de foto sin identificador válido')
    result = graph('POST', f'{page_id}/feed', token, {
        'message': caption,
        'published': 'true',
        'attached_media[0]': json.dumps({'media_fbid': photo_id}, separators=(',', ':')),
    }, FB_BASE)
    post_id = str(result.get('id') or '')
    if not re.fullmatch(r'\d+_\d+', post_id):
        raise RuntimeError('Facebook feed: respuesta sin ID válido, revisar post antes de reintentar')
    try:
        detail = graph('GET', post_id, token,
                       {'fields': 'id,is_published,attachments{media_type,target,subattachments}'}, FB_BASE)
        attachments = (detail.get('attachments') or {}).get('data') or []
        def image_present(items):
            for item in items:
                if not isinstance(item, dict):
                    continue
                target_id = str((item.get('target') or {}).get('id') or '')
                media_type = str(item.get('media_type') or '').lower()
                if target_id == photo_id or 'photo' in media_type or 'image' in media_type:
                    return True
                subs = (item.get('subattachments') or {}).get('data') or []
                if image_present(subs):
                    return True
            return False
        if detail.get('is_published') is False or not image_present(attachments):
            raise RuntimeError('El post fue creado, pero la imagen no pudo confirmarse en sus adjuntos')
    except Exception as exc:
        raise RuntimeError('Facebook feed: post posiblemente creado, verificación pendiente: ' + short_error(exc))
    return post_id


def new_state():
    return {'events': {}}


def event_is_finished(entry):
    targets = entry.get('targets') or {}
    return all(targets.get(platform, {}).get('status') == 'published' for platform in PLATFORMS)


def ensure_entry(state, event):
    entry = state['events'].setdefault(event['id'], {
        'magnitude': event['mag'], 'place': event['place'], 'time': iso(event['time']),
        'targets': {},
    })
    targets = entry.setdefault('targets', {})
    # Migración: entradas antiguas sin facebook_feed se consideran pendientes allí.
    for platform in PLATFORMS:
        targets.setdefault(platform, {'status': 'pending'})
    return entry


def wait_public_image(url):
    for attempt in range(12):
        try:
            response = HTTP.get(url, timeout=20)
            if response.ok and response.headers.get('Content-Type', '').startswith('image/'):
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    raise RuntimeError('Imagen todavía no accesible públicamente')


def pending_destinations(entry):
    targets = entry.get('targets') or {}
    return [p for p in PLATFORMS if targets.get(p, {}).get('status', 'pending') == 'pending']


def process_event(event, github, state, publish, credentials=None):
    event_id = event['id']
    # Preview nunca escribe el estado remoto.
    entry = ensure_entry(state, event) if publish else None
    if publish and event_is_finished(entry):
        log('YA PUBLICADO:', event_id)
        return 'already_published'
    if publish and not pending_destinations(entry):
        log('REVISIÓN MANUAL: ningún destino pendiente; hay destinos confirmados o inciertos', event_id)
        return 'partial_or_uncertain'

    detail = get_detail(event)
    cities = nearby_cities(event, detail)
    tsunami = tsunami_status(event)
    filename = safe_text(event_id) + '.jpg'
    image_path = OUT / filename
    make_image(event, image_path)
    caption = make_caption(event, cities, tsunami)
    save_json(OUT / (safe_text(event_id) + '.json'), {
        'event': event, 'nearby_cities': cities, 'tsunami': tsunami, 'caption': caption,
    })
    if not publish:
        log('VISTA PREVIA:', image_path)
        return 'preview_only'

    remote_path = IMAGE_DIR + '/' + filename
    github.write_bytes(remote_path, image_path.read_bytes())
    image_url = github.raw_url(remote_path)
    wait_public_image(image_url)
    entry['image_url'] = image_url
    github.write_json(STATE_PATH, state)

    # Ningún fallo de validación Facebook debe impedir intentar Instagram.
    for platform in PLATFORMS:
        target = entry['targets'][platform]
        status = target.get('status', 'pending')
        if status == 'published':
            log('OMITIDO', platform, 'confirmado anteriormente')
            continue
        if status == 'uncertain':
            log('REVISIÓN MANUAL', platform, event_id, target.get('error', ''))
            continue
        if status != 'pending':
            log('REVISIÓN MANUAL', platform, 'estado inesperado:', status)
            continue

        try:
            if platform == 'instagram':
                platform_credentials = validate_instagram()
            else:
                platform_credentials = validate_facebook()
        except Exception as exc:
            # Sin enviar una petición de publicación no se marca uncertain.
            target['error'] = 'Validación: ' + short_error(exc)
            github.write_json(STATE_PATH, state)
            log('VALIDACIÓN FALLIDA', platform, short_error(exc))
            continue

        # Marcar incierto solamente cuando vamos a llamar a Meta.
        target['status'] = 'uncertain'
        target.pop('error', None)
        github.write_json(STATE_PATH, state)
        try:
            if platform == 'instagram':
                identifier = publish_instagram(*platform_credentials, image_url, caption)
            elif platform == 'facebook':
                identifier = publish_facebook(*platform_credentials, image_url, caption)
            else:
                identifier = publish_facebook_feed(*platform_credentials, image_url, caption)
            target['status'] = 'published'
            target['id'] = str(identifier)
            target['published_at'] = iso(now_utc())
            github.write_json(STATE_PATH, state)
            log('PUBLICADO', platform, identifier)
        except Exception as exc:
            target['error'] = short_error(exc)
            github.write_json(STATE_PATH, state)
            log('ERROR', platform, short_error(exc))
    return 'published' if event_is_finished(entry) else 'partial_or_uncertain'


def run_tests():
    from unittest.mock import patch
    dt = datetime(2026, 11, 5, tzinfo=UTC)
    example = {'id': 'us-test', 'mag': 7.5, 'place': 'Offshore Chile', 'region': 'CHILE',
               'latitude': -30.0, 'longitude': -72.0, 'depth': 20.0, 'time': dt,
               'url': 'https://earthquake.usgs.gov/', 'detail_url': '', 'tsunami_flag': 0}
    assert region_name('100 km near Japan') == 'JAPÓN'
    assert 'ÚLTIMO MINUTO' in make_caption(example, [], {'message': 'Pendiente de confirmación.', 'url': ''})
    state = new_state()
    entry = ensure_entry(state, example)
    assert set(entry['targets']) == set(PLATFORMS)
    assert not event_is_finished(entry)
    entry['targets']['instagram']['status'] = 'published'
    entry['targets']['facebook']['status'] = 'published'
    assert pending_destinations(entry) == ['facebook_feed']
    entry['targets']['facebook_feed']['status'] = 'published'
    assert event_is_finished(entry)
    sent = []
    def graph_mock(method, path, token, parameters=None, base=None):
        sent.append((method, path, dict(parameters or {})))
        if path.endswith('/photos'):
            return {'id': '12345678'}
        if path.endswith('/feed'):
            return {'id': '123_456'}
        if path == '123_456':
            return {'id': '123_456', 'is_published': True,
                    'attachments': {'data': [{'media_type': 'photo', 'target': {'id': '12345678'}}]}}
        raise AssertionError(path)
    with patch(__name__ + '.graph', side_effect=graph_mock):
        assert publish_facebook_feed('123', 'fake', 'https://example.org/image.jpg', 'mensaje') == '123_456'
    assert sent[0][2]['published'] == 'false'
    assert json.loads(sent[1][2]['attached_media[0]'])['media_fbid'] == '12345678'
    assert sent[1][2]['published'] == 'true'
    log('PRUEBAS APROBADAS: titulares, estados, migración y post de Facebook con foto')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--out', default='output_alertas')
    parser.add_argument('--lookback-hours', type=int, default=24)
    parser.add_argument('--test', action='store_true')
    args = parser.parse_args()
    if args.test:
        run_tests()
        return 0
    if args.lookback_hours < 1:
        raise ValueError('--lookback-hours debe ser positivo')
    global OUT
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    receipt_path = OUT / 'estado_publicacion.json'
    receipt = {'status': 'starting', 'publish_requested': args.publish,
               'checked_at': iso(now_utc()), 'events': []}
    try:
        github = Github() if args.publish else None
        existing_state = github.read(STATE_PATH) if github else None
        state = existing_state or new_state()
        state.setdefault('events', {})
        events = fetch_earthquakes(hours=args.lookback_hours)
        if args.publish and existing_state is None:
            threshold = now_utc() - timedelta(minutes=90)
            events = [event for event in events if event['time'] >= threshold]
        events = sorted(events, key=lambda item: item['time'])[:MAX_EVENTS]
        pending = [event for event in events if not event_is_finished(state['events'].get(event['id'], {}))]
        log('USGS M7+:', len(events), '| Pendientes:', len(pending))
        if not pending:
            receipt['status'] = 'no_new_event'
            save_json(receipt_path, receipt)
            return 0
        statuses = []
        for event in pending:
            log('SISMO:', event['id'], f"M{event['mag']:.1f}", event['region'])
            result = process_event(event, github, state, args.publish)
            statuses.append(result)
            receipt['events'].append({
                'id': event['id'], 'magnitude': event['mag'], 'region': event['region'],
                'status': result,
                'targets': state['events'][event['id']]['targets'] if args.publish else {},
            })
        if all(result == 'preview_only' for result in statuses):
            receipt['status'] = 'preview_only'
        elif any(result == 'partial_or_uncertain' for result in statuses):
            receipt['status'] = 'partial_or_uncertain'
        elif all(result in ('published', 'already_published') for result in statuses):
            receipt['status'] = 'published'
        else:
            receipt['status'] = 'error'
        save_json(receipt_path, receipt)
        log('ESTADO FINAL:', receipt['status'])
        return 0 if receipt['status'] in ('published', 'preview_only', 'no_new_event') else 1
    except Exception as exc:
        receipt['status'] = 'error'
        receipt['error'] = short_error(exc)
        save_json(receipt_path, receipt)
        log('ERROR GENERAL:', short_error(exc))
        return 1


if __name__ == '__main__':
    sys.exit(main())
