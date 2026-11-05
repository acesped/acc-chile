#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Actualiza el monitor.py COMPLETO de SISMONAUTA sin cambiar el motor editorial.

Uso:
    python ajustar_sismonauta.py monitor.py
    python ajustar_sismonauta.py monitor.py --in-place

Por defecto crea monitor_sismonauta_ajustado.py.
"""
from __future__ import annotations
import argparse
import ast
from pathlib import Path

YOUTUBE_REPLACEMENT = '''# === SISMONAUTA / YOUTUBE DATA API v3 ===
# Los problemas de YouTube no deben abortar publicaciones ya realizadas en Meta.
# OAuth: YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
YOUTUBE_API='https://www.googleapis.com/youtube/v3'
YOUTUBE_UPLOAD='https://www.googleapis.com/upload/youtube/v3/videos'

class YouTubeUploadLimit(RuntimeError):
    """Límite de subida confirmado; seguro aplazar SIN marcar como incierto."""


def youtube_is_upload_limit(status_code, response_text):
    if status_code not in (400, 403, 429):
        return False
    try:
        payload=json.loads(response_text)
        err=payload.get('error') or {}
        reasons=[str(x.get('reason','')).lower() for x in err.get('errors',[]) if isinstance(x,dict)]
        msg=str(err.get('message','')).lower()
        return ('uploadlimitexceeded' in reasons
                or 'the user has exceeded the number of videos they may upload' in msg)
    except (ValueError, TypeError, AttributeError):
        return 'exceeded the number of videos they may upload' in str(response_text).lower()


def youtube_check_error(response, context):
    if youtube_is_upload_limit(response.status_code,response.text):
        raise YouTubeUploadLimit('YouTube: límite de carga de videos alcanzado')
    raise RuntimeError(f'YouTube {context} HTTP {response.status_code}: '+safe_error(response.text)[:180])


def youtube_cooldown_active(state):
    until=str(state.get('youtube_upload_retry_after') or '')
    if not until: return False
    try:
        return datetime.fromisoformat(until.replace('Z','+00:00'))>datetime.now(timezone.utc)
    except (ValueError,TypeError):
        return False


def yt_token():
    required=('YT_CLIENT_ID','YT_CLIENT_SECRET','YT_REFRESH_TOKEN')
    missing=[k for k in required if not os.getenv(k,'').strip()]
    if missing: raise RuntimeError('YouTube: faltan secretos '+', '.join(missing))
    r=requests.post('https://oauth2.googleapis.com/token',data={
        'client_id':os.environ['YT_CLIENT_ID'],
        'client_secret':os.environ['YT_CLIENT_SECRET'],
        'refresh_token':os.environ['YT_REFRESH_TOKEN'],
        'grant_type':'refresh_token'},timeout=30)
    if not r.ok: raise RuntimeError(f'YouTube OAuth HTTP {r.status_code}: '+safe_error(r.text)[:200])
    token=r.json().get('access_token')
    if not token: raise RuntimeError('YouTube OAuth sin access_token')
    return token


def yt_api_get(path,token,params):
    r=requests.get(YOUTUBE_API+'/'+path,headers={'Authorization':'Bearer '+token},params=params,timeout=40)
    if not r.ok: raise RuntimeError(f'YouTube API HTTP {r.status_code}: '+safe_error(r.text)[:200])
    return r.json()


def yt_verify_channel(token):
    info=yt_api_get('channels',token,{'part':'snippet','mine':'true','maxResults':10})
    channels=info.get('items',[])
    channel_id=os.getenv('YT_CHANNEL_ID','').strip()
    if not channel_id: raise RuntimeError('Configure YT_CHANNEL_ID para evitar publicaciones accidentales')
    matches=[x for x in channels if x.get('id')==channel_id]
    if len(matches)!=1: raise RuntimeError('YouTube: OAuth no corresponde al YT_CHANNEL_ID esperado')
    log('YOUTUBE CANAL VERIFICADO:',matches[0]['id'],matches[0].get('snippet',{}).get('title',''))
    return channel_id


def yt_privacy():
    value=os.getenv('YT_PRIVACY','private').strip().lower()
    if value not in ('public','unlisted','private'): raise RuntimeError('YT_PRIVACY debe ser public, unlisted o private')
    return value


def yt_wide_video(vertical,dest):
    vf=('[0:v]split=2[back][front];'
        '[back]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,'
        'boxblur=30:10,eq=brightness=-0.16[bg];'
        '[front]scale=-2:720[fg];'
        '[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[out]')
    ffmpeg(['-i',vertical,'-filter_complex',vf,'-map','[out]','-map','0:a:0',
            '-c:v','libx264','-preset','veryfast','-crf','23','-r',FPS,
            '-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',dest])
    if not dest.exists() or dest.stat().st_size<10000: raise RuntimeError('No se creó video horizontal YouTube')
    return dest


def yt_upload(movie,title,description,token,kind):
    """Subida reanudable. Un resultado de red ambiguo NUNCA se reintenta a ciegas."""
    privacy=yt_privacy()
    marker='#Shorts' if kind=='short' else '#Sismonauta'
    title=clean(title)[:100]
    description=(description+'\\n\\n'+marker)[:5000]
    metadata={'snippet':{'title':title,'description':description,'categoryId':'27',
                         'defaultLanguage':'es','tags':['Sismonauta','Sismos','Sismología','Geología','Ciencia']},
              'status':{'privacyStatus':privacy,'selfDeclaredMadeForKids':False}}
    size=movie.stat().st_size
    if size<=0: raise RuntimeError('Archivo YouTube vacío')
    r=requests.post(YOUTUBE_UPLOAD,params={'uploadType':'resumable','part':'snippet,status'},
        headers={'Authorization':'Bearer '+token,'X-Upload-Content-Type':'video/mp4',
                 'X-Upload-Content-Length':str(size),'Content-Type':'application/json; charset=UTF-8'},
        json=metadata,timeout=45,allow_redirects=False)
    if not r.ok: youtube_check_error(r,'inicio de carga')
    session=r.headers.get('Location','')
    parsed=urllib.parse.urlparse(session)
    if parsed.scheme!='https' or parsed.hostname not in ('www.googleapis.com','youtube.googleapis.com'):
        raise RuntimeError('YouTube: URL de sesión inesperada')
    chunk=8*1024*1024; offset=0; retries=0
    with movie.open('rb') as fh:
        while offset<size:
            fh.seek(offset)
            data=fh.read(min(chunk,size-offset))
            end=offset+len(data)-1
            try:
                r=requests.put(session,headers={'Authorization':'Bearer '+token,
                    'Content-Type':'video/mp4','Content-Length':str(len(data)),
                    'Content-Range':f'bytes {offset}-{end}/{size}'},data=data,timeout=180,allow_redirects=False)
                if r.status_code in (200,201):
                    response=r.json(); video_id=str(response.get('id') or '')
                    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id): raise RuntimeError('YouTube: respuesta sin ID válido')
                    return video_id
                if r.status_code==308:
                    rng=r.headers.get('Range','')
                    match=re.search(r'bytes=0-(\\d+)',rng)
                    offset=int(match.group(1))+1 if match else 0
                    retries=0
                    continue
                if r.status_code not in (429,500,502,503,504):
                    youtube_check_error(r,'carga')
            except (requests.Timeout,requests.ConnectionError):
                pass
            retries+=1
            if retries>7: raise RuntimeError('YouTube: subida inconclusa; NO repetir sin comprobar el canal')
            time.sleep(min(2**retries,30))
            try:
                probe=requests.put(session,headers={'Authorization':'Bearer '+token,
                    'Content-Length':'0','Content-Range':f'bytes */{size}'},timeout=40,allow_redirects=False)
                if probe.status_code in (200,201):
                    video_id=str(probe.json().get('id') or '')
                    if re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id): return video_id
                if probe.status_code==308:
                    match=re.search(r'bytes=0-(\\d+)',probe.headers.get('Range',''))
                    offset=int(match.group(1))+1 if match else 0
                else:
                    if youtube_is_upload_limit(probe.status_code,probe.text):
                        raise YouTubeUploadLimit('YouTube: límite de carga de videos alcanzado')
                    raise RuntimeError('YouTube: estado de sesión incierto; comprobación manual necesaria')
            except requests.RequestException as exc:
                raise RuntimeError('YouTube: no pudo consultarse sesión; comprobación manual necesaria') from exc
    raise RuntimeError('YouTube: carga terminó sin confirmación')


def yt_publish_episode(ep,movie,state,sha,gh,out_dir):
    """Short + horizontal; error aislado, guardando estados en GitHub.

    'aplazado': rechazo definitivo por cuota (seguro reintentar más tarde).
    'incierto': se inició un envío pero no hay confirmación (NO reintentar).
    """
    episode_id=ep['episode_id']
    row=state.setdefault('episodes',{}).setdefault(episode_id,{})
    receipt=Path(out_dir)/'publicacion_confirmada.json'
    report=read_json(receipt,{})

    def save(which,status,video_id=None):
        nonlocal sha
        if video_id: row[which+'_id']=video_id
        row[which]=status; row['updated_at']=utcnow()
        result=gh.write(STATE_PATH,json.dumps(state,ensure_ascii=False,indent=2).encode(),sha)
        sha=result['content']['sha']
        if video_id:
            report[which+'_id']=video_id
            report[which+'_url']='https://www.youtube.com/watch?v='+video_id
            report[which+'_uploaded']=True
            report[which+'_requested_privacy']=yt_privacy()
            write_json(receipt,report)

    for name in ('youtube_short','youtube_video'):
        status=row.get(name)
        if status in ('enviando','incierto'):
            log('YOUTUBE:',name,'en estado incierto; revisar YouTube Studio. Se omite YouTube.')
            report['youtube_warning']=name+': envío anterior incierto'
            write_json(receipt,report)
            return sha
        if status=='publicado' and not re.fullmatch(r'[A-Za-z0-9_-]{11}',str(row.get(name+'_id') or '')):
            raise RuntimeError(name+': publicado sin ID válido')
        if status not in (None,'publicado','aplazado'):
            raise RuntimeError(name+': estado desconocido')

    if youtube_cooldown_active(state):
        log('YOUTUBE APLAZADO: límite de carga; reintento permitido desde',state['youtube_upload_retry_after'])
        report['youtube_warning']='Límite de carga; cooldown activo'
        report['youtube_retry_after']=state['youtube_upload_retry_after']
        write_json(receipt,report)
        return sha

    if all(row.get(name)=='publicado' for name in ('youtube_short','youtube_video')):
        return sha

    # Verificar YouTube SOLO DESPUÉS de publicar en Meta.
    token=yt_token(); yt_verify_channel(token); yt_privacy()
    base=episode_caption_base(ep)+'\\n\\n'+episode_hashtags(ep)
    for name in ('youtube_short','youtube_video'):
        if row.get(name)=='publicado': continue
        target=movie
        if name=='youtube_video':
            target=Path(out_dir)/(episode_id+'_youtube_16x9.mp4')
            if not target.exists(): yt_wide_video(movie,target)
        save(name,'enviando')
        try:
            video_id=yt_upload(target,ep['title'],base,token,'short' if name=='youtube_short' else 'video')
        except YouTubeUploadLimit:
            # Respuesta explícita de YouTube: no se creó una nueva subida.
            # Dejar enfriamiento persistente para evitar fallos en cada ejecución.
            from datetime import timedelta
            state['youtube_upload_retry_after']=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()
            save(name,'aplazado')
            report['youtube_warning']='Límite de subida de YouTube; Meta publicado normalmente'
            report['youtube_retry_after']=state['youtube_upload_retry_after']
            write_json(receipt,report)
            log('YOUTUBE APLAZADO POR LÍMITE:',name,'hasta',state['youtube_upload_retry_after'])
            return sha
        except Exception:
            # Si la solicitud pudo haber llegado a Google, el resultado es incierto.
            save(name,'incierto')
            raise
        save(name,'publicado',video_id)
        log('YOUTUBE',name,'CONFIRMADO:',video_id)
    return sha
# === FIN SISMONAUTA / YOUTUBE ===
'''

OLD_PREFLIGHT = '''        if not args.no_youtube:
            yt_verify_channel(yt_token())
            yt_privacy()'''
NEW_PREFLIGHT = '''        # YouTube se valida después de Meta. Sus fallos no bloquean Instagram/Facebook.'''

OLD_CALL = '''    if youtube:
        # Conservar el sha más reciente del state actualizado por Meta.
        yt_publish_episode(ep,movie,state,sha,gh,Path(out_dir or movie.parent))'''
NEW_CALL = '''    if youtube:
        # YouTube es opcional: ninguna falla suya revierte publicaciones de Meta.
        # Los estados inciertos NO se reintentan a ciegas.
        try:
            sha=yt_publish_episode(ep,movie,state,sha,gh,Path(out_dir or movie.parent))
        except Exception as exc:
            warning='YouTube omitido: '+safe_error(exc)
            log('ADVERTENCIA:',warning)
            receipt['youtube_warning']=warning
            write_json(receipt_path,receipt)'''


def replace_once(src,old,new,tag):
    n=src.count(old)
    if n!=1:
        raise RuntimeError(f'No se encontró un bloque único para {tag} (coincidencias={n}). '
                           'Verifica que sea la versión de monitor.py facilitada.')
    return src.replace(old,new,1)


def patch(source):
    if 'class YouTubeUploadLimit(RuntimeError):' in source:
        raise RuntimeError('El archivo ya parece estar ajustado; no repetir el parche')
    start=source.find('# === SISMONAUTA / YOUTUBE DATA API v3 ===')
    end=source.find('# === FIN SISMONAUTA / YOUTUBE ===',start)
    if start<0 or end<0: raise RuntimeError('No se encontró el bloque YouTube original')
    end+=len('# === FIN SISMONAUTA / YOUTUBE ===')
    source=source[:start]+YOUTUBE_REPLACEMENT.rstrip()+source[end:]
    source=replace_once(source,OLD_PREFLIGHT,NEW_PREFLIGHT,'prevalidación YouTube')
    source=replace_once(source,OLD_CALL,NEW_CALL,'ejecución YouTube')
    ast.parse(source)
    return source


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',nargs='?',default='monitor.py',help='Monitor original')
    ap.add_argument('--in-place',action='store_true',help='Sobrescribe el original, creando copia .bak')
    args=ap.parse_args()
    path=Path(args.source)
    if not path.is_file(): ap.error('No existe '+str(path))
    source=path.read_text(encoding='utf-8')
    updated=patch(source)
    if args.in_place:
        backup=path.with_suffix(path.suffix+'.bak')
        if backup.exists(): ap.error('Ya existe la copia de seguridad: '+str(backup))
        backup.write_text(source,encoding='utf-8')
        dest=path
    else:
        dest=path.with_name('monitor_sismonauta_ajustado.py')
        if dest.exists(): ap.error('Archivo de destino ya existe: '+str(dest))
    dest.write_text(updated,encoding='utf-8')
    print('ARCHIVO COMPLETO AJUSTADO:',dest)
    print('Comprobación sintáctica: OK')
    print('Instagram y Facebook continúan; YouTube queda aislado, con enfriamiento de 24 horas.')

if __name__=='__main__': main()
