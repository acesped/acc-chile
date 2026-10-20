#!/usr/bin/env python3
"""Comprueba a diario Instagram Login y renueva tras >= 48 h.

Actualiza exclusivamente INSTAGRAM_TOKEN como Repository Secret en GitHub.
Nunca imprime credenciales. No recupera tokens revocados por Meta.
"""
import base64
import os
import re
import sys
from datetime import datetime, timezone

import requests
from nacl import encoding, public

TIMEOUT = 25
GRAPH = 'https://graph.instagram.com'
API_VERSION = 'v25.0'
MIN_HOURS = 48


def required(name):
    value = os.getenv(name, '').strip()
    if not value:
        raise RuntimeError(f'Falta secreto o variable: {name}')
    return value


def call(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=TIMEOUT,
                                    allow_redirects=False, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError(f'Error de conexión: {type(exc).__name__}') from None
    if not 200 <= response.status_code < 300:
        # No imprimir body ni URL con query (podrían contener secretos).
        if response.status_code in (400, 401, 403) and url.startswith(GRAPH):
            try:
                err = response.json().get('error', {})
                if err.get('code') == 190:
                    raise RuntimeError('Meta OAuth 190: token revocado o inválido; requiere nueva autorización manual')
            except ValueError:
                pass
        raise RuntimeError(f'{method} rechazado: HTTP {response.status_code}')
    if response.status_code == 204 or not response.content:
        return {}
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError('Respuesta no JSON') from None
    if not isinstance(data, dict):
        raise RuntimeError('Respuesta de API inesperada')
    return data


def verify_identity(token, uid, username):
    data = call('GET', f'{GRAPH}/{API_VERSION}/{uid}',
                headers={'Authorization': f'Bearer {token}'},
                params={'fields': 'id,username'})
    if str(data.get('id')) != uid or str(data.get('username', '')).lower() != username:
        raise RuntimeError('La identidad de Instagram no coincide; no se modifica el secreto')


def main():
    if os.getenv('IG_LOGIN_TYPE', 'instagram').lower() != 'instagram':
        raise RuntimeError('Se requiere IG_LOGIN_TYPE=instagram')
    token = required('INSTAGRAM_TOKEN')
    uid = required('INSTAGRAM_USER_ID')
    username = required('IG_EXPECTED_USERNAME').lstrip('@').lower()
    gh_token = required('GH_SECRETS_TOKEN')
    repo = required('GITHUB_REPOSITORY')
    if not re.fullmatch(r'\d+', uid) or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo):
        raise RuntimeError('ID o repositorio inválido')
    if repo.lower() != 'acesped/acc-chile':
        raise RuntimeError('Repositorio inesperado: operación bloqueada')

    # Verificar antes de intentar renovar; si ya está revocado, fallar y alertar.
    verify_identity(token, uid, username)
    print(f'Identidad actual confirmada: @{username}')

    headers = {'Authorization': f'Bearer {gh_token}',
               'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    base = f'https://api.github.com/repos/{repo}/actions/secrets'
    # El momento de la última modificación es un límite conservador: puede
    # incluir actualizaciones manuales y por eso a veces pospone la renovación.
    metadata = call('GET', base + '/INSTAGRAM_TOKEN', headers=headers)
    updated = datetime.fromisoformat(metadata['updated_at'].replace('Z', '+00:00'))
    age_hours = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
    if age_hours < MIN_HOURS:
        print(f'OMITIDA: secreto actualizado hace {age_hours:.1f} h; mínimo {MIN_HOURS} h.')
        return

    renewed = call('GET', GRAPH + '/refresh_access_token',
                   params={'grant_type': 'ig_refresh_token', 'access_token': token})
    new_token = renewed.get('access_token')
    expires = renewed.get('expires_in')
    if not isinstance(new_token, str) or len(new_token) < 30:
        raise RuntimeError('Meta no devolvió un token válido')
    if not isinstance(expires, (float, int)) or expires <= 0:
        raise RuntimeError('Meta no devolvió vigencia válida')
    print('::add-mask::' + new_token, flush=True)
    verify_identity(new_token, uid, username)
    key = call('GET', base + '/public-key', headers=headers)
    public_key = public.PublicKey(key['key'].encode('ascii'), encoding.Base64Encoder())
    encrypted = base64.b64encode(public.SealedBox(public_key).encrypt(new_token.encode())).decode('ascii')
    call('PUT', base + '/INSTAGRAM_TOKEN', headers=headers,
         json={'encrypted_value': encrypted, 'key_id': key['key_id']})
    print(f'Renovación exitosa para @{username}. INSTAGRAM_TOKEN actualizado. Vigencia reportada: {int(expires // 86400)} días.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR en renovación Instagram: {exc}', file=sys.stderr)
        sys.exit(1)
