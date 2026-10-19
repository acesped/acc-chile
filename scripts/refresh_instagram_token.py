#!/usr/bin/env python3
"""Renueva token Instagram Login y actualiza INSTAGRAM_TOKEN en GitHub Actions Secrets.

No escribe tokens en archivos ni en logs. Requiere requests y PyNaCl.
"""
import base64
import os
import re
import sys

import requests
from nacl import encoding, public

TIMEOUT = 25


def require(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise RuntimeError(f'Falta variable/secret: {name}')
    return value


def api_json(method, url, *, headers=None, params=None, json=None):
    try:
        response = requests.request(method, url, headers=headers, params=params, json=json,
                                    timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        raise RuntimeError(f'Fallo de red en {method} {url.split("?")[0]}: {type(exc).__name__}') from None
    # No exponer la respuesta: algunas APIs pueden incluir información sensible.
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f'{method} {url.split("?")[0]} rechazado: HTTP {response.status_code}')
    if response.status_code == 204 or not response.content:
        return {}
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError('Respuesta JSON inválida') from None
    if not isinstance(result, dict):
        raise RuntimeError('Respuesta JSON inesperada')
    return result


def main():
    if os.getenv('IG_LOGIN_TYPE', 'instagram').strip().lower() != 'instagram':
        raise RuntimeError('Este renovador solo sirve para IG_LOGIN_TYPE=instagram')
    token = require('INSTAGRAM_TOKEN')
    uid = require('INSTAGRAM_USER_ID')
    username = require('IG_EXPECTED_USERNAME').lstrip('@').lower()
    repo = require('GITHUB_REPOSITORY')
    github_token = require('GH_SECRETS_TOKEN')
    if not re.fullmatch(r'\d+', uid):
        raise RuntimeError('INSTAGRAM_USER_ID no es numérico')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise RuntimeError('GITHUB_REPOSITORY inválido')

    # Renovación disponible para tokens LONG-LIVED válidos; no repara revocaciones.
    renewed = api_json('GET', 'https://graph.instagram.com/refresh_access_token',
                       params={'grant_type': 'ig_refresh_token', 'access_token': token})
    new_token = renewed.get('access_token')
    expires = renewed.get('expires_in')
    if not isinstance(new_token, str) or len(new_token) < 30:
        raise RuntimeError('Meta no entregó un token nuevo válido')
    if not isinstance(expires, (int, float)) or expires <= 0:
        raise RuntimeError('Meta no confirmó vigencia del token nuevo')
    # A partir de aquí enmascaramos el valor si GitHub llegase a registrarlo accidentalmente.
    print('::add-mask::' + new_token, flush=True)

    identity = api_json('GET', f'https://graph.instagram.com/v25.0/{uid}',
                        headers={'Authorization': 'Bearer ' + new_token},
                        params={'fields': 'id,username'})
    if str(identity.get('id')) != uid or str(identity.get('username', '')).lower() != username:
        raise RuntimeError('La cuenta del token nuevo no coincide con la cuenta esperada; secreto no modificado')

    headers = {'Authorization': 'Bearer ' + github_token,
               'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28'}
    base = f'https://api.github.com/repos/{repo}/actions/secrets'
    key_data = api_json('GET', base + '/public-key', headers=headers)
    key = public.PublicKey(key_data['key'].encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(key).encrypt(new_token.encode('utf-8'))
    ciphertext = base64.b64encode(sealed).decode('ascii')
    api_json('PUT', base + '/INSTAGRAM_TOKEN', headers=headers,
             json={'encrypted_value': ciphertext, 'key_id': key_data['key_id']})
    print(f'Token renovado y secreto INSTAGRAM_TOKEN actualizado. Vigencia reportada: {int(expires // 86400)} días.')
    print('Identidad confirmada: @' + username)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Los mensajes de excepción de esta aplicación no contienen tokens.
        print(f'ERROR renovación Instagram: {exc}', file=sys.stderr)
        sys.exit(1)
