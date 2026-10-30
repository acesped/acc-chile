#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SISMONAUTA ETAS CHILE | modelo experimental y publicador unificado.

Uso:
 python sismonauta_etas_publicar.py --out output_etas
 python sismonauta_etas_publicar.py --out output_etas --publish
 python sismonauta_etas_publicar.py --test-narration

Conserva mapas 25 horarios, heatmaps JET, tres zonas, narración chilena,
música, animación de cierre, Reel, Instagram/Facebook e historial GitHub.
ETAS experimental, no calibrado prospectivamente; NO es una alerta sísmica.
"""
from __future__ import annotations
import argparse, asyncio, base64, io, json, math, os, shutil, subprocess, sys, tempfile, time, zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
import numpy as np
import pandas as pd
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.colors import LogNorm
from matplotlib.ticker import FuncFormatter
from scipy.optimize import minimize
from scipy.ndimage import gaussian_filter
from scipy.io import wavfile
from numba import njit
from PIL import Image, ImageDraw, ImageFont
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.io import shapereader
from shapely.geometry import Point, box as shp_box
from shapely.ops import unary_union
from shapely.prepared import prep
import pycountry
from geopy.geocoders import Nominatim

COUNTRY_NAME='Chile'; COUNTRY_ISO='CHL'; START='2018-01-01'; DOWNLOAD_M=3.; REQUESTED_M=4.
HORIZON_DAYS=1.; LOOKBACK_HOURS=24; FPS=24; FIT_N=1300; TRIGGER_DAYS=365; TRIGGER_MAX=2500
MAX_GRID_CELLS=15000; W=1080; H=1350; VOICE_RATE='+0%'
ORIGINAL_VIDEO_NAME='ETAS_DOBLE_HEATMAP_CON_LIKE.mp4'; REEL_NAME='sismonauta_etas_reel_9x16.mp4'
GRAPH_VERSION=os.getenv('META_GRAPH_VERSION','v25.0'); IG_BASE=os.getenv('IG_GRAPH_BASE','https://graph.instagram.com').rstrip('/')
FB_BASE='https://graph.facebook.com/'+GRAPH_VERSION; GH_API='https://api.github.com'
HTTP=requests.Session(); HTTP.headers['User-Agent']='Sismonauta-ETAS-Chile/5.2'
CUTOFF=None; COUNTRY_GEOM=None; LAT0=LAT1=LON0=LON1=GRID=None; LATS=LONS=XX=YY=None
CITY=[]; CITY_SOURCE=''; CAPITAL_CITY=None; _DETAIL_CACHE=None
OUT=Path(os.getenv('ETAS_OUTPUT_DIR','output_etas')).resolve()

def log(*args): print(*args,flush=True)
def utcnow(): return datetime.now(timezone.utc).isoformat()
def atomic_json(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8'); tmp.replace(path)
def binary(name):
    p=shutil.which(name)
    if not p: raise RuntimeError(f'{name} no disponible en PATH. Revisar workflow.')
    return p
def ffmpeg(*args):
    r=subprocess.run([binary('ffmpeg'),'-hide_banner','-loglevel','error','-y',*map(str,args)],capture_output=True,text=True)
    if r.returncode: raise RuntimeError('FFmpeg: '+(r.stderr or r.stdout)[-1800:])
def probe(path):
    r=subprocess.run([binary('ffprobe'),'-v','error','-show_entries','format=duration,size:stream=codec_type,codec_name,width,height,r_frame_rate','-of','json',str(path)],capture_output=True,text=True)
    if r.returncode: raise RuntimeError('FFprobe: '+r.stderr[-900:])
    data=json.loads(r.stdout)
    if not any(s.get('codec_type')=='video' for s in data.get('streams',[])): raise RuntimeError('MP4 sin pista de vídeo')
    if float(data.get('format',{}).get('duration') or 0)<=0: raise RuntimeError('MP4 sin duración válida')
    return data
def audio_duration(path):
    r=subprocess.run([binary('ffprobe'),'-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True)
    if r.returncode: raise RuntimeError('FFprobe audio: '+r.stderr[-500:])
    return float(r.stdout.strip())

# Geografía y referencias a ciudades (GeoNames; Nominatim como respaldo).
def setup_country():
    global COUNTRY_GEOM,LAT0,LAT1,LON0,LON1,GRID,LATS,LONS,XX,YY,CITY,CITY_SOURCE,CAPITAL_CITY
    country=pycountry.countries.get(alpha_3=COUNTRY_ISO)
    shp=shapereader.natural_earth(resolution='110m',category='cultural',name='admin_0_countries')
    reader=shapereader.Reader(shp)
    geoms=[rec.geometry for rec in reader.records() if COUNTRY_ISO in [rec.attributes.get(key,'') for key in ('ADM0_A3','SOV_A3','ISO_A3','GU_A3','SU_A3')]]
    reader.close()
    if not geoms: raise RuntimeError('No se encontró Chile en Natural Earth')
    geom=unary_union(geoms); parts=list(geom.geoms) if hasattr(geom,'geoms') else [geom]
    polygons=[g for g in parts if g.geom_type=='Polygon']
    if polygons:
        biggest=max(polygons,key=lambda g:g.area); kept=[g for g in polygons if g.area>=.025*biggest.area]
        if len(kept)<len(polygons): log('Aviso: se omiten componentes menores del mapa Natural Earth.')
        geom=unary_union(kept)
    x0,y0,x1,y1=geom.bounds
    if x1-x0>150: raise RuntimeError('Extensión geográfica incompatible')
    LAT0,LAT1=max(-89.,y0-2.2),min(89.,y1+2.2)
    LON0,LON1=max(-179.9,x0-2.2),min(179.9,x1+2.2)
    GRID=round(max(.20,math.sqrt((LAT1-LAT0)*(LON1-LON0)/MAX_GRID_CELLS)),3)
    LATS=np.arange(LAT0,LAT1+GRID/2,GRID); LONS=np.arange(LON0,LON1+GRID/2,GRID)
    XX,YY=np.meshgrid(LONS,LATS); COUNTRY_GEOM=geom
    log(f'Chile: {XX.size:,} celdas | malla {GRID:.3f}°')
    try:
        response=HTTP.get('https://download.geonames.org/export/dump/cities15000.zip',timeout=(15,50)); response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            with z.open('cities15000.txt') as f:
                table=pd.read_csv(f,sep='\t',header=None,usecols=[1,4,5,8],names=['name','lat','lon','iso2'],low_memory=False)
        table=table[table.lat.between(LAT0,LAT1)&table.lon.between(LON0,LON1)]
        local=table[table.iso2==country.alpha_2]
        if len(local): table=local
        CITY=[(str(r.name),float(r.lat),float(r.lon)) for r in table.itertuples(index=False)]
        CITY_SOURCE='GeoNames cities15000 (CC BY 4.0)'; log('Ciudades de referencia:',len(CITY))
        try:
            r=HTTP.get('https://download.geonames.org/export/dump/countryInfo.txt',timeout=(15,40)); r.raise_for_status()
            rows=[s.split('\t') for s in r.text.splitlines() if s and not s.startswith('#')]
            matches=[row for row in rows if len(row)>5 and row[0]==country.alpha_2]
            if matches:
                name=matches[0][5].strip(); candidates=[c for c in CITY if c[0].casefold()==name.casefold()]
                if candidates: CAPITAL_CITY=candidates[0]
                else:
                    place=Nominatim(user_agent='sismonauta-etas-capital/5.2',timeout=15).geocode(name+', Chile',exactly_one=True)
                    if place: CAPITAL_CITY=(name,float(place.latitude),float(place.longitude))
                log('Capital:',CAPITAL_CITY or name)
        except Exception as exc: log('Capital no disponible:',str(exc)[:100])
    except Exception as exc:
        CITY=[]; CITY_SOURCE='Nominatim/OpenStreetMap'; log('GeoNames no disponible:',str(exc)[:100])

def nearest_city(lat,lon):
    if CITY:
        clat=np.radians([c[1] for c in CITY]); clon=np.radians([c[2] for c in CITY])
        dy=clat-np.radians(lat); dx=clon-np.radians(lon)
        a=np.sin(dy/2)**2+np.cos(np.radians(lat))*np.cos(clat)*np.sin(dx/2)**2
        distances=12742*np.arcsin(np.sqrt(np.clip(a,0,1)))
        index=int(distances.argmin()); return float(distances[index]),CITY[index][0]
    try:
        geo=Nominatim(user_agent='sismonauta-etas-reverse/5.2',timeout=15)
        res=geo.reverse((lat,lon),language='es',zoom=10,exactly_one=True)
        address=res.raw.get('address',{}) if res else {}
        place=next((address[k] for k in ('city','town','village','municipality','county') if address.get(k)),None)
        if place: return None,str(place)  # Nominatim no proporciona distancia al centro urbano.
    except Exception: pass
    return None,'localidad no identificada'

def catalog_usgs():
    session=requests.Session(); session.headers['User-Agent']='Sismonauta-ETAS-Chile/5.2'
    begin=pd.Timestamp(START,tz='UTC'); blocks=[]; frames=[]
    while begin<CUTOFF:
        end=min(begin+pd.DateOffset(months=1),CUTOFF); blocks.append((begin,end)); begin=end
    for i,(start,end) in enumerate(blocks,1):
        params={'format':'csv','starttime':start.strftime('%Y-%m-%dT%H:%M:%S'),'endtime':end.strftime('%Y-%m-%dT%H:%M:%S'),'minlatitude':LAT0,'maxlatitude':LAT1,'minlongitude':LON0,'maxlongitude':LON1,'minmagnitude':DOWNLOAD_M,'orderby':'time-asc','limit':20000}
        for attempt in range(4):
            try:
                r=session.get('https://earthquake.usgs.gov/fdsnws/event/1/query',params=params,timeout=(15,75))
                if r.status_code==204: part=pd.DataFrame(columns=['time','latitude','longitude','mag','id'])
                else:
                    r.raise_for_status(); part=pd.read_csv(io.StringIO(r.text))
                    if len(part)>=20000: raise RuntimeError('Límite USGS alcanzado; dividir intervalo')
                break
            except Exception as exc:
                if attempt==3: raise RuntimeError(f'Error USGS {start:%Y-%m}: {exc}') from exc
                time.sleep(2*(attempt+1))
        frames.append(part)
        if i%4==0 or i==len(blocks): log(f'USGS: {i}/{len(blocks)} meses')
    df=pd.concat(frames,ignore_index=True); df['time']=pd.to_datetime(df['time'],utc=True,errors='coerce')
    for k in ('latitude','longitude','mag'): df[k]=pd.to_numeric(df[k],errors='coerce')
    df=df.dropna(subset=['time','latitude','longitude','mag'])
    df=df[(df.time<CUTOFF)&df.latitude.between(LAT0,LAT1)&df.longitude.between(LON0,LON1)&(df.mag>=DOWNLOAD_M)]
    area=prep(COUNTRY_GEOM.buffer(2.5))
    keep=np.fromiter((area.covers(Point(float(lon),float(lat))) for lat,lon in zip(df.latitude,df.longitude)),dtype=bool,count=len(df))
    df=df.loc[keep].drop_duplicates(subset=['id']).sort_values('time').reset_index(drop=True)
    if len(df)<200: raise RuntimeError('Menos de 200 eventos USGS válidos')
    log(f'Eventos sísmicos válidos: {len(df):,}'); return df

def estimate_mc_b(df):
    mags=df.mag.to_numpy(); edges=np.arange(np.floor(mags.min()*10)/10,np.ceil(mags.max()*10)/10+.21,.1)
    hist,_=np.histogram(mags,bins=edges); mc=round(float(edges[hist.argmax()]),2)
    selected=mags[mags>=mc]
    if len(selected)<100: raise RuntimeError('Menos de 100 eventos por encima de Mc')
    denominator=selected.mean()-(mc-.05)
    if denominator<=0: raise RuntimeError('No se pudo estimar b')
    return mc,float(np.log10(np.e)/denominator)

@njit
def negll(theta,t,m,mc,T):
    mu,K,alpha,c,p=theta
    if mu<=0 or K<=0 or c<=0 or p<=1: return 1e90
    out=mu*T
    for i in range(len(t)):
        rate=mu
        for j in range(i):
            dt=t[i]-t[j]
            if dt>0: rate+=K*np.exp(alpha*(m[j]-mc))*(dt+c)**(-p)
        if rate<=0 or not np.isfinite(rate): return 1e90
        out-=np.log(rate)
    for j in range(len(t)):
        age=T-t[j]
        if age>0: out+=K*np.exp(alpha*(m[j]-mc))*((age+c)**(1-p)-c**(1-p))/(1-p)
    return out if np.isfinite(out) else 1e90

def fit_etas(df,mc):
    sub=df[df.mag>=mc].tail(FIT_N)
    if len(sub)<100: raise RuntimeError('Eventos insuficientes para ETAS')
    t=(sub.time-sub.time.iloc[0]).dt.total_seconds().to_numpy()/86400.; m=sub.mag.to_numpy(); T=max(float(t[-1]),1e-6)
    bounds=[(1e-6,100),(1e-8,5),(0,3),(.002,2),(1.01,2.8)]
    attempts=[[.2,.01,.8,.02,1.2],[.5,.005,1.,.08,1.3],[1.,.001,.6,.1,1.5]]; fits=[]
    for i,x0 in enumerate(attempts,1):
        log(f'Ajuste ETAS {i}/3')
        result=minimize(negll,x0,args=(t,m,mc,T),method='L-BFGS-B',bounds=bounds,options={'maxiter':260})
        if result.success and np.isfinite(result.fun): fits.append(result)
    if not fits: raise RuntimeError('ETAS no convergió')
    return dict(zip(('mu','K','alpha','c','p'),map(float,min(fits,key=lambda x:x.fun).x)))

def make_background(events):
    bl=np.r_[LATS-GRID/2,LATS[-1]+GRID/2]; bo=np.r_[LONS-GRID/2,LONS[-1]+GRID/2]
    hist,_,_=np.histogram2d(events.latitude,events.longitude,bins=[bl,bo]); vals=gaussian_filter(hist,1.7)+1e-10
    return (vals/vals.sum()).astype(np.float32)

def three_distinct_increases(delta_pp,min_km=90.):
    selected=[]
    for idx in np.argsort(np.asarray(delta_pp).ravel())[::-1]:
        val=float(np.asarray(delta_pp).ravel()[idx])
        if not np.isfinite(val) or val<=0: break
        row,col=np.unravel_index(int(idx),delta_pp.shape); lat,lon=float(LATS[row]),float(LONS[col])
        distinct=True
        for pt in selected:
            dy=np.radians(lat-pt['lat']); dx=np.radians(lon-pt['lon'])
            a=np.sin(dy/2)**2+np.cos(np.radians(lat))*np.cos(np.radians(pt['lat']))*np.sin(dx/2)**2
            if 12742*np.arcsin(min(1.,np.sqrt(a)))<min_km: distinct=False; break
        if distinct:
            selected.append({'lat':lat,'lon':lon,'val':val})
            if len(selected)==3: break
    return selected

def evolution_24h(df,theta,mc,b,magnitude):
    start=CUTOFF-pd.Timedelta(hours=LOOKBACK_HOURS); times=pd.date_range(start=start,periods=25,freq='1h')
    chosen=df[(df.time<CUTOFF)&(df.time>=start-pd.Timedelta(days=TRIGGER_DAYS))&(df.mag>=mc)].copy().reset_index(drop=True)
    if len(chosen)>TRIGGER_MAX: raise RuntimeError(f'{len(chosen)} disparadores: aumentar TRIGGER_MAX')
    log(f'Preparando 25 mapas; {len(chosen)} disparadores ETAS.')
    kernels=np.empty((len(chosen),XX.size),dtype=np.float32); lat_flat=YY.ravel().astype(np.float32); lon_flat=XX.ravel().astype(np.float32)
    for j,event in enumerate(chosen.itertuples(index=False)):
        dx=(lon_flat-event.longitude)*111.32*np.cos(np.radians(event.latitude)); dy=(lat_flat-event.latitude)*111.32
        weights=np.power(1+(dx*dx+dy*dy)/25.**2,-1.5); kernels[j]=weights/weights.sum()
        if (j+1)%500==0: log(f'Kernels: {j+1}/{len(chosen)}')
    event_times=chosen.time.astype('int64').to_numpy(); mags=chosen.mag.to_numpy(dtype=np.float64)
    productivity=np.exp(np.clip(theta['alpha']*(mags-mc),-40,40))
    factor=10**(-b*(magnitude-mc)); c,p=theta['c'],theta['p']; maps=[]; maxima=[]; baseline=None
    for k,stamp in enumerate(times):
        ages=(stamp.value-event_times)/1e9/86400.; valid=(ages>0)&(ages<=TRIGGER_DAYS)
        masses=np.zeros(len(chosen),dtype=np.float32); a=ages[valid]
        masses[valid]=(theta['K']*productivity[valid]*((a+HORIZON_DAYS+c)**(1-p)-(a+c)**(1-p))/(1-p)).astype(np.float32)
        before=df[(df.time<stamp)&(df.mag>=mc)]; background=make_background(before)
        expected=theta['mu']*HORIZON_DAYS*background.ravel()
        if len(chosen): expected=expected+(masses@kernels)
        forecast=-np.expm1(-expected.reshape(background.shape)*factor)
        if not np.all(np.isfinite(forecast)): raise RuntimeError('Mapa ETAS no finito')
        forecast=np.clip(forecast,0,1).astype(np.float32)
        if baseline is None: baseline=forecast.copy()
        delta=(forecast.astype(np.float64)-baseline.astype(np.float64))*100.
        rp,cp=np.unravel_index(np.argmax(forecast),forecast.shape)
        rd,cd=np.unravel_index(np.argmax(delta),delta.shape)
        maps.append(forecast)
        maxima.append({'prob_lat':float(LATS[rp]),'prob_lon':float(LONS[cp]),'prob_val':float(forecast[rp,cp])*100.,'delta_lat':float(LATS[rd]),'delta_lon':float(LONS[cd]),'delta_val':max(0.,float(delta[rd,cd])),'delta_points':three_distinct_increases(delta)})
        info=maxima[-1]; log(f"Mapa {k+1}/25 | {stamp:%d/%m %H:%M} UTC | máx prob {info['prob_val']:.4f}% | aumento {info['delta_val']:.5f} pp")
    return times,maps,maxima

def font(size,bold=False):
    paths=['/usr/share/fonts/truetype/dejavu/'+('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),'/usr/share/fonts/truetype/liberation2/'+('LiberationSans-Bold.ttf' if bold else 'LiberationSans-Regular.ttf')]
    for path in paths:
        if Path(path).is_file(): return ImageFont.truetype(path,size)
    return ImageFont.load_default()

def load_detailed_geography():
    global _DETAIL_CACHE
    if _DETAIL_CACHE is not None: return _DETAIL_CACHE
    region=shp_box(LON0,LAT0,LON1,LAT1); layers={}
    catalog={'coast':('10m','physical','coastline'),'country':('10m','cultural','admin_0_boundary_lines_land'),'states':('50m','cultural','admin_1_states_provinces_lines')}
    for key,(res,cat,name) in catalog.items():
        try:
            shp=shapereader.natural_earth(resolution=res,category=cat,name=name)
            reader=shapereader.Reader(shp,bbox=(LON0,LAT0,LON1,LAT1)); geoms=[g for g in reader.geometries() if g is not None and g.intersects(region)]; reader.close()
            layers[key]=geoms; log(f'Cartografía {name}: {len(geoms)} geometrías')
        except Exception as exc: layers[key]=[]; log('Aviso cartografía:',name,str(exc)[:100])
    _DETAIL_CACHE=layers; return layers

def draw_heatmap_panel(fig,rect,data,clims,city_points,colorbar_rect,colorbar_label,colorbar_fmt,card_rect,card_title,card_value,card_city,scale='log'):
    detailed=load_detailed_geography(); ax=fig.add_axes(rect,projection=ccrs.PlateCarree())
    ax.set_extent([LON0,LON1,LAT0,LAT1],crs=ccrs.PlateCarree()); ax.set_facecolor('#DFEAF1')
    try:
        ax.add_feature(cfeature.LAND.with_scale('110m'),facecolor='#EDE9E2',zorder=0)
        ax.add_feature(cfeature.OCEAN.with_scale('110m'),facecolor='#DFEAF1',zorder=0)
    except Exception: pass
    for layer,color,width,style,z in (('coast','#152C3D',1.35,'-',15),('country','#203747',.85,'-',14),('states','#6C8190',.42,':',13)):
        if detailed.get(layer): ax.add_geometries(detailed[layer],crs=ccrs.PlateCarree(),facecolor='none',edgecolor=color,linewidth=width,linestyle=style,zorder=z)
    if not detailed.get('coast'): ax.coastlines('110m',color='#173244',linewidth=1.15,zorder=15)
    if not detailed.get('country'): ax.add_feature(cfeature.BORDERS.with_scale('110m'),edgecolor='#253F50',linewidth=.6,linestyle='--',zorder=14)
    gl=ax.gridlines(draw_labels=True,linewidth=.30,alpha=.26,linestyle=':')
    gl.top_labels=False; gl.right_labels=False; gl.xlabel_style={'size':7.7,'color':'#566C7E'}; gl.ylabel_style={'size':7.7,'color':'#566C7E'}
    for spine in ax.spines.values(): spine.set_edgecolor('#B5C8D5')
    for name,lat,lon in city_points:
        ax.plot(lon,lat,'o',ms=2.8,color='#152E3F',markeredgecolor='white',markeredgewidth=.45,transform=ccrs.PlateCarree(),zorder=20)
        ax.text(lon+.12,lat+.07,name,fontsize=7.2,color='#183143',transform=ccrs.PlateCarree(),zorder=21,bbox={'facecolor':'white','edgecolor':'none','alpha':.76,'pad':1.})
    normalization=LogNorm(vmin=clims[0],vmax=clims[1]) if scale=='log' else mcolors.PowerNorm(gamma=.50,vmin=0.,vmax=clims[1])
    mesh=ax.pcolormesh(LONS,LATS,data,cmap='jet',norm=normalization,shading='nearest',transform=ccrs.PlateCarree(),zorder=2,alpha=.95)
    cax=fig.add_axes(colorbar_rect); bar=fig.colorbar(mesh,cax=cax,orientation='horizontal')
    bar.set_ticks(np.geomspace(clims[0],clims[1],5) if scale=='log' else np.linspace(0,clims[1],5))
    bar.ax.xaxis.set_major_formatter(FuncFormatter(colorbar_fmt)); bar.ax.tick_params(labelsize=8.2,colors='#2E4C60')
    bar.set_label(colorbar_label,fontsize=8.8,labelpad=6,color='#1D3D52'); bar.outline.set_visible(False)
    patch=plt.Rectangle((card_rect[0],card_rect[1]),card_rect[2],card_rect[3],transform=fig.transFigure,facecolor='#E8EFF5',edgecolor='#D1DDE6',linewidth=1.)
    fig.patches.append(patch)
    fig.text(card_rect[0]+.015,card_rect[1]+.078,card_title,fontsize=10.5,fontweight='bold',color='#4B6477')
    fig.text(card_rect[0]+.015,card_rect[1]+.051,card_value,fontsize=12,fontweight='bold',color='#173A52')
    fig.text(card_rect[0]+.015,card_rect[1]+.024,card_city,fontsize=9,color='#415B6D')
    return ax

def draw_snapshot(prob,stamp,index,total,abs_clims,delta_clims,info,work,baseline):
    fig=plt.figure(figsize=(10.8,13.5),dpi=100,facecolor='#F3F7FA')
    percent=prob*100.; delta=(prob.astype(np.float64)-baseline.astype(np.float64))*100.
    def labels_near(lat,lon):
        pts=[]
        if CITY: pts.append(min(CITY,key=lambda c:(c[1]-lat)**2+(c[2]-lon)**2))
        if CAPITAL_CITY and LAT0<=CAPITAL_CITY[1]<=LAT1 and LON0<=CAPITAL_CITY[2]<=LON1:
            if all(c[0]!=CAPITAL_CITY[0] for c in pts): pts.append(CAPITAL_CITY)
        return pts
    points=info.get('delta_points',[])[:3]
    for pt in points:
        row=int(np.argmin(np.abs(LATS-pt['lat']))); col=int(np.argmin(np.abs(LONS-pt['lon'])))
        actual=float(delta[row,col])
        if abs(actual-pt['val'])>max(1e-5,abs(actual)*1e-3): raise RuntimeError('Marcador de aumento no corresponde al mapa')
    left=draw_heatmap_panel(fig,[.055,.27,.42,.57],np.ma.masked_less_equal(percent,0),abs_clims,labels_near(info['prob_lat'],info['prob_lon']),[.08,.22,.37,.016],f'Por celda: ≥1 sismo M≥{info["mag"]:.1f} en 24 h',lambda v,p:f'{v:.3g}%',[.055,.08,.42,.105],'Coordenada de valor máximo',f'{info["prob_val"]:.4f}% · {info["prob_lat"]:+.2f}°, {info["prob_lon"]:+.2f}°',f'Ciudad cercana: {info["prob_city"]} ({info["prob_dist"]:.0f} km)' if info.get('prob_dist') is not None else f'Localidad de referencia: {info["prob_city"]}')
    right=draw_heatmap_panel(fig,[.525,.27,.42,.57],np.maximum(delta,0),delta_clims,[],[.55,.22,.37,.016],'Aumento respecto al inicio (pp)',lambda v,p:f'{v:.3g} pp',[.525,.08,.42,.105],'Zonas de mayor aumento positivo','','',scale='power')
    fig.text(.265,.863,'PROBABILIDAD ACUMULADA',ha='center',fontsize=11,fontweight='bold',color='#19374B')
    fig.text(.735,.863,'VARIACIÓN PROBABILIDAD',ha='center',fontsize=11,fontweight='bold',color='#19374B')
    fig.text(.5,.035,'MODELO EXPERIMENTAL · NO ES ALERTA SÍSMICA',ha='center',fontsize=10,fontweight='bold',color='#A13A33')
    if CAPITAL_CITY and LAT0<=CAPITAL_CITY[1]<=LAT1 and LON0<=CAPITAL_CITY[2]<=LON1:
        name,lat,lon=CAPITAL_CITY
        right.plot(lon,lat,'o',ms=2.8,color='#152E3F',markeredgecolor='white',transform=ccrs.PlateCarree(),zorder=20)
        right.text(lon+.12,lat+.07,name,fontsize=7.2,color='#183143',transform=ccrs.PlateCarree(),zorder=21,bbox={'facecolor':'white','edgecolor':'none','alpha':.76,'pad':1.})
    offsets=[(20,23),(-23,23),(20,-23)]; transform=ccrs.PlateCarree()._as_mpl_transform(right)
    for rank,pt in enumerate(points,1):
        lon,lat=float(pt['lon']),float(pt['lat'])
        right.scatter([lon],[lat],s=92,marker='o',facecolor='none',edgecolor='white',linewidth=2.1,transform=ccrs.PlateCarree(),zorder=25)
        dx,dy=offsets[(rank-1)%3]
        right.annotate(str(rank),xy=(lon,lat),xycoords=transform,xytext=(dx,dy),textcoords='offset points',fontsize=9,fontweight='bold',color='white',ha='center',va='center',zorder=26,arrowprops={'arrowstyle':'-','color':'white','lw':1.5,'shrinkA':2,'shrinkB':5},bbox={'boxstyle':'circle,pad=.30','facecolor':'#162D43','edgecolor':'white','linewidth':1.2})
        city=str(pt.get('city') or 'Localidad no identificada')
        if len(city)>22: city=city[:21].rstrip()+'…'
        fig.text(.54,.131-(rank-1)*.022,f'{rank}. {city} · {pt["val"]:+.4f} pp',fontsize=8.2,color='#173A52',fontweight='bold',zorder=30)
    off=Path(work)/f'frame_{index:03d}_off.png'; on=Path(work)/f'frame_{index:03d}_on.png'
    fig.savefig(off,dpi=fig.dpi,facecolor=fig.get_facecolor(),bbox_inches=None,pad_inches=0)
    stars=[(left,float(info['prob_lon']),float(info['prob_lat']))]+[(right,float(p['lon']),float(p['lat'])) for p in points]
    for ax,lon,lat in stars: ax.scatter([lon],[lat],s=210,marker='*',c='#FFE55B',edgecolors='#142B3A',linewidths=1.3,transform=ccrs.PlateCarree(),zorder=40)
    fig.savefig(on,dpi=fig.dpi,facecolor=fig.get_facecolor(),bbox_inches=None,pad_inches=0)
    plt.close(fig); return on,off

def music_wav(filepath,duration=30,sr=24000):
    t=np.arange(sr*duration,dtype=np.float64)/sr; audio=np.zeros_like(t)
    chords=[(196.,246.94,293.66),(174.61,220.,261.63),(164.81,196.,246.94),(146.83,185.,220.)]
    for begin in range(0,duration,4):
        local=t-begin; active=(local>=0)&(local<4)
        envelope=(np.sin(np.pi*np.clip(local,0,4)/4)**1.3)*active
        for frequency in chords[(begin//4)%len(chords)]:
            phase=2*np.pi*frequency*t; audio+=(.6*np.sin(phase)+.13*np.sin(phase*2))*envelope/3
    for beat in np.arange(0,duration,2):
        tau=t-beat; active=(tau>=0)&(tau<.55)
        audio+=.12*np.sin(2*np.pi*98*t)*np.exp(-9*np.maximum(tau,0))*active
    audio*=np.clip(t/1.2,0,1)*np.clip((duration-t)/2.4,0,1)
    peak=np.max(np.abs(audio))
    if peak>0: audio*=.67/peak
    wavfile.write(str(filepath),sr,(np.clip(audio,-1,1)*32767).astype(np.int16))

# Narración: el mapa de la izquierda ya calcula prob_city y prob_dist.
# NUEVO: se dice explícitamente esa localidad y su distancia desde el máximo.
def narration(final_info):
    def scaled_index(value):
        value=min(100.,max(0.,float(value)*100.))
        return '100 sobre 100' if value>=100. else f'{value:.2f}'.replace('.',',')+' sobre 100'
    city=str(final_info.get('prob_city') or '').strip()
    dist=final_info.get('prob_dist')
    if city and city.casefold() not in ('localidad no identificada','una zona sin ciudad identificada'):
        if dist is not None and math.isfinite(float(dist)) and float(dist)>=0:
            # GeoNames devuelve distancia geodésica al centro de la localidad.
            location=f'La coordenada del máximo se ubica aproximadamente a {float(dist):.0f} kilómetros de {city}. '
        else:
            location=f'La localidad de referencia más cercana identificada es {city}. '
    else:
        location='No fue posible identificar una localidad cercana a esa coordenada. '
    areas=final_info.get('delta_points',[]); pieces=[]
    for rank,point in enumerate(areas,1):
        city_delta=str(point.get('city') or 'una zona sin localidad identificada')
        amount=f'{point["val"]:.4f}'.replace('.',',')
        pieces.append(f'La zona {rank} está cerca de {city_delta}, con un incremento de {amount} puntos porcentuales.')
    delta_text=' '.join(pieces) if pieces else 'No se detectaron aumentos positivos durante este período.'
    if pieces and len(areas)<3: delta_text+=' No se identificaron otras zonas positivas suficientemente separadas.'
    magnitude=f'{final_info["mag"]:.1f}'.replace('.',',')
    return (
        'Hola, a continuación te invitamos a revisar una actualización del modelo sísmico experimental en Chile para las próximas 24 horas. '
        'En el mapa de la izquierda, la celda con el valor modelado más alto tiene un índice de presentación de '
        +scaled_index(final_info['prob_val'])+'. '
        +location
        +f'El umbral evaluado corresponde a sismos de magnitud {magnitude} o superior durante las próximas 24 horas. '
        'Este índice reescalado no es un porcentaje de probabilidad ni una predicción de un terremoto. '
        'En el gráfico de la derecha en tanto, observamos las zonas donde la probabilidad modelada aumentó más durante el período. '
        +delta_text
        +'\n\nIMPORTANTE: los resultados de este modelo son experimentales, no constituyen una alerta sísmica ni una predicción oficial.'
    )

def test_narration():
    sample={'prob_val':.9212,'prob_lat':-33.5,'prob_lon':-70.7,'prob_city':'Santiago','prob_dist':18.4,'mag':4.0,'delta_points':[{'val':.0031,'city':'Valparaíso'}]}
    script=narration(sample)
    assert 'Santiago' in script and '18 kilómetros' in script
    assert 'Valparaíso' in script and 'índice de presentación' in script
    assert '92,12 sobre 100' in script and 'por ciento' not in script.split('En el gráfico de la derecha')[0]
    missing=narration(dict(sample,prob_city='localidad no identificada',prob_dist=None))
    assert 'No fue posible identificar una localidad' in missing
    assert 'En el gráfico de la derecha en tanto,' in script
    log('PRUEBAS NARRACIÓN APROBADAS: ciudad, distancia, índice y separación audiovisual')

def voice(text,work,name):
    path=Path(work)/name
    try:
        import edge_tts
        async def make(): await edge_tts.Communicate(text,voice='es-CL-CatalinaNeural',rate=VOICE_RATE).save(str(path))
        asyncio.run(make())
        if path.stat().st_size>1000: return path
    except Exception as exc: log('Edge TTS no disponible:',str(exc)[:100])
    try:
        from gtts import gTTS
        gTTS(text,lang='es',tld='cl',slow=False).save(str(path))
        if path.stat().st_size>1000: return path
    except Exception as exc: log('gTTS no disponible:',str(exc)[:100])
    raise RuntimeError('No se pudo generar voz; no se publicará video sin narración.')

def create_outro_frame(work,visible):
    path=Path(work)/('outro_on.png' if visible else 'outro_off.png'); image=Image.new('RGB',(W,H),'#000000'); draw=ImageDraw.Draw(image)
    if visible:
        hand=Image.new('RGBA',(900,900),(0,0,0,0)); painter=ImageDraw.Draw(hand)
        outline=[(180,460),(280,460),(320,420),(360,345),(404,248),(446,170),(475,158),(512,176),(524,215),(520,280),(498,395),(650,395),(700,406),(731,437),(732,477),(718,511),(724,541),(711,578),(700,590),(704,617),(683,652),(658,664),(654,694),(622,732),(570,745),(390,745),(347,726),(285,685),(180,685)]
        painter.line(outline,fill='#1877F2',width=34,joint='curve')
        for x,y in outline: painter.ellipse((x-17,y-17,x+17,y+17),fill='#1877F2')
        painter.line((180,460,180,685),fill='#1877F2',width=34); painter.line([(280,468),(280,686)],fill='#1877F2',width=26)
        hand=hand.resize((440,440),Image.Resampling.LANCZOS); image.paste(hand,((W-440)//2,160),hand)
    ft=font(120,bold=True)
    for text,top in [('Dale Like',690),('y Comenta!',850)]:
        bbox=draw.textbbox((0,0),text,font=ft); width=bbox[2]-bbox[0]
        if width>W-100: raise RuntimeError('Texto de cierre no cabe')
        draw.text((int((W-width)/2-bbox[0]),int(top-bbox[1])),text,font=ft,fill='white')
    image.save(path); return path

def frame_sequence_video(frame_paths,animation_seconds,summary_seconds,outro_seconds,work):
    all_paths=[]; frame_index=0; amount=round(animation_seconds*FPS); quotient,remainder=divmod(amount,len(frame_paths))
    for i,(on,off) in enumerate(frame_paths):
        for _ in range(quotient+(1 if i<remainder else 0)):
            all_paths.append(on if (frame_index//max(1,FPS//2))%2==0 else off); frame_index+=1
    final_on,final_off=frame_paths[-1]
    for j in range(round(summary_seconds*FPS)):
        all_paths.append(final_on if ((frame_index+j)//max(1,FPS//2))%2==0 else final_off)
    outro_on=create_outro_frame(work,True); outro_off=create_outro_frame(work,False)
    for j in range(round(outro_seconds*FPS)): all_paths.append(outro_on if (j//max(1,FPS//2))%2==0 else outro_off)
    expected=round(animation_seconds*FPS)+round(summary_seconds*FPS)+round(outro_seconds*FPS)
    if len(all_paths)!=expected: raise RuntimeError('Cantidad de fotogramas inconsistente')
    seq=Path(work)/'secuencia'; seq.mkdir()
    for idx,source in enumerate(all_paths):
        dest=seq/f'f_{idx:06d}.png'
        try: os.symlink(source,dest)
        except (OSError,NotImplementedError): shutil.copyfile(source,dest)
    silent=Path(work)/'video_sin_audio.mp4'
    ffmpeg('-framerate',str(FPS),'-start_number','0','-i',seq/'f_%06d.png','-frames:v',str(expected),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-movflags','+faststart',silent)
    duration=float(probe(silent)['format']['duration']); target=expected/FPS
    if abs(duration-target)>max(.35,2/FPS): raise RuntimeError(f'Duración incorrecta: {duration} vs {target}')
    log('VIDEO SIN AUDIO:',silent,'duración:',round(duration,2),'fotogramas:',expected); return silent

def create_video(times,maps,maxima,final_info):
    binary('ffmpeg'); binary('ffprobe'); OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sismonauta_etas_') as td:
        work=Path(td); positives=[f[f>0] for f in maps if np.any(f>0)]
        if not positives: raise RuntimeError('Mapas sin valores positivos')
        values=np.concatenate(positives); vmax=float(values.max()*100.); vmin=max(float(np.percentile(values,2)*100.),vmax/1000.,1e-10)
        if vmin>=vmax: vmin=max(vmax/1000.,1e-10)
        abs_clims=(vmin,vmax); baseline=maps[0]
        delta_fields=[(f.astype(np.float64)-baseline.astype(np.float64))*100. for f in maps]
        dmax=max(0.,max(float(np.max(d)) for d in delta_fields)); delta_clims=(0.,max(dmax,1e-12))
        log('Escala JET probabilidad:',abs_clims,'| aumento:',delta_clims)
        frames=[]; last_info=None
        for i,(stamp,prob,meta) in enumerate(zip(times,maps,maxima)):
            prob_dist,prob_city=nearest_city(meta['prob_lat'],meta['prob_lon'])
            delta_dist,delta_city=nearest_city(meta['delta_lat'],meta['delta_lon'])
            info=dict(meta); info['delta_points']=[]
            for pt in meta.get('delta_points',[]):
                dist,city=nearest_city(pt['lat'],pt['lon'])
                info['delta_points'].append(dict(pt,city=city,dist=dist))
            info.update({'prob_city':prob_city,'prob_dist':prob_dist,'delta_city':delta_city,'delta_dist':delta_dist,'mag':final_info['mag']})
            frames.append(draw_snapshot(prob,stamp,i,len(times),abs_clims,delta_clims,info,work,baseline))
            if i==len(times)-1: last_info=info
            if (i+1)%5==0 or i+1==len(times): log(f'Fotogramas renderizados: {i+1}/{len(times)}')
        if last_info is None: raise RuntimeError('Falta mapa final')
        for plotted,computed in zip(last_info['delta_points'],maxima[-1]['delta_points']):
            if not (plotted['lat']==computed['lat'] and plotted['lon']==computed['lon'] and np.isclose(plotted['val'],computed['val'])): raise RuntimeError('Marcadores no coinciden con el cálculo')
        script=narration(last_info); log('\nNARRACIÓN FINAL:\n'+script+'\n')
        # Auditoría adicional: la narración tiene que usar exactamente la ciudad mostrada en el último mapa izquierdo.
        if last_info['prob_city']!='localidad no identificada' and last_info['prob_city'] not in script: raise RuntimeError('Speech no menciona la ciudad del mapa izquierdo')
        marker='En el gráfico de la derecha en tanto,'
        if marker not in script: raise RuntimeError('No se encontró separador de narración')
        first_text,second_text=script.split(marker,1)
        files=[voice(first_text.strip(),work,'voz_animacion.mp3'),voice((marker+second_text).strip(),work,'voz_zonas_ultimo_mapa.mp3'),voice('Dale me gusta y comparte para seguir recibiendo esta importante información!',work,'voz_cierre.mp3')]
        prepared=[]
        for src,name in zip(files,('voz_animacion.wav','voz_zonas.wav','voz_cierre.wav')):
            dest=work/name; ffmpeg('-i',src,'-ar','24000','-ac','1',dest); prepared.append(dest)
        first,second,outro=prepared
        def section_duration(audio,pause=.25):
            measured=audio_duration(audio)
            if not math.isfinite(measured) or measured<=0: raise RuntimeError('Duración de voz inválida: '+str(audio))
            return math.ceil((measured+pause)*FPS)/FPS
        animation_seconds=section_duration(first); summary_seconds=section_duration(second); outro_seconds=section_duration(outro,.35)
        total_seconds=animation_seconds+summary_seconds+outro_seconds
        log(f'Voz natural: animación={animation_seconds:.2f}s, zonas={summary_seconds:.2f}s, cierre={outro_seconds:.2f}s, total={total_seconds:.2f}s')
        spoken=work/'narracion_completa.wav'
        audio_filter=('[0:a]aformat=sample_rates=24000:channel_layouts=mono,apad,'+f'atrim=duration={animation_seconds:.6f},asetpts=PTS-STARTPTS[a0];'+'[1:a]aformat=sample_rates=24000:channel_layouts=mono,apad,'+f'atrim=duration={summary_seconds:.6f},asetpts=PTS-STARTPTS[a1];'+'[2:a]aformat=sample_rates=24000:channel_layouts=mono,apad,'+f'atrim=duration={outro_seconds:.6f},asetpts=PTS-STARTPTS[a2];'+'[a0][a1][a2]concat=n=3:v=0:a=1[spoken]')
        ffmpeg('-i',first,'-i',second,'-i',outro,'-filter_complex',audio_filter,'-map','[spoken]','-ar','24000','-ac','1',spoken)
        music=work/'musica.wav'; music_wav(music,duration=math.ceil(total_seconds))
        silent=frame_sequence_video(frames,animation_seconds,summary_seconds,outro_seconds,work)
        mixing_filter=('[2:a]aformat=sample_fmts=fltp:channel_layouts=mono,volume=0.37[music];'+'[1:a]aformat=sample_fmts=fltp:channel_layouts=mono,volume=1.0,asplit=2[sc][speech];'+'[music][sc]sidechaincompress=threshold=0.025:ratio=6:attack=25:release=350[duck];'+'[duck][speech]amix=inputs=2:duration=longest:normalize=0,'+f'atrim=duration={total_seconds},asetpts=N/SR/TB,alimiter=limit=0.92[aout]')
        video=OUT/ORIGINAL_VIDEO_NAME; video.unlink(missing_ok=True)
        ffmpeg('-i',silent,'-i',spoken,'-stream_loop','-1','-i',music,'-filter_complex',mixing_filter,'-map','0:v:0','-map','[aout]','-t',str(total_seconds),'-c:v','copy','-c:a','aac','-b:a','160k','-movflags','+faststart',video)
        if not video.is_file() or video.stat().st_size<10000: raise RuntimeError('Video original incompleto')
        if abs(float(probe(video)['format']['duration'])-total_seconds)>1.: raise RuntimeError('La duración final del video no coincide')
        log('VIDEO ORIGINAL FINAL:',video); return video

def run_scientific_etas():
    global CUTOFF
    CUTOFF=pd.Timestamp.now(tz='UTC'); log(f'Corte del catálogo: {CUTOFF:%d/%m/%Y %H:%M} UTC')
    setup_country(); df=catalog_usgs()
    training=df[df.time<CUTOFF-pd.Timedelta(hours=LOOKBACK_HOURS)].copy()
    if len(training)<200: raise RuntimeError('Catálogo histórico ETAS insuficiente')
    mc,b=estimate_mc_b(training); magnitude=max(REQUESTED_M,mc)
    if magnitude>REQUESTED_M: log(f'AVISO: Mc={mc:.2f}. Se usa M≥{magnitude:.2f}, no M≥4.')
    log(f'Mc={mc:.2f}; b={b:.3f}; magnitud objetivo M≥{magnitude:.2f}')
    theta=fit_etas(training,mc); log('Parámetros ETAS:',theta)
    times,maps,maxima=evolution_24h(df,theta,mc,b,magnitude)
    if len(times)!=25 or len(maps)!=25: raise RuntimeError('No se produjeron exactamente 25 mapas')
    final=maxima[-1]; prob_dist,prob_city=nearest_city(final['prob_lat'],final['prob_lon']); delta_dist,delta_city=nearest_city(final['delta_lat'],final['delta_lon'])
    points=[]
    for pt in final['delta_points']:
        dist,city=nearest_city(pt['lat'],pt['lon']); points.append(dict(pt,city=city,dist=dist))
    info=dict(final,delta_points=points,prob_city=prob_city,prob_dist=prob_dist,delta_city=delta_city,delta_dist=delta_dist,mag=magnitude)
    log('MÁXIMO ACTUAL MODELADO:',f'{info["prob_lat"]:.3f}°',f'{info["prob_lon"]:.3f}°',f'{info["prob_val"]:.6f}%',prob_city)
    log('MAYOR AUMENTO:',f'{info["delta_lat"]:.3f}°',f'{info["delta_lon"]:.3f}°',f'{info["delta_val"]:.6f} pp',delta_city)
    log('Valores por celda; no son probabilidades municipales ni alertas oficiales.')
    video=create_video(times,maps,maxima,info)
    metadata={'country':COUNTRY_NAME,'cutoff_utc':str(CUTOFF),'start_catalog':START,'events_catalog':len(df),'mc':mc,'b':b,'magnitude_target':magnitude,'etas_parameters':theta,'grid_degrees':GRID,'grid_cells':int(XX.size),'snapshots':25,'final':info,'city_source':CITY_SOURCE,'disclaimer':'Modelo experimental no calibrado ni validado prospectivamente. No constituye alerta.'}
    atomic_json(OUT/'modelo_etas_resultados.json',metadata); return video,metadata

def make_reel(video):
    dest=OUT/REEL_NAME; dest.unlink(missing_ok=True)
    ffmpeg('-i',video,'-vf','scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x071727,setsar=1','-r',str(FPS),'-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-b:a','128k','-movflags','+faststart',dest)
    result=probe(dest); vs=next(s for s in result['streams'] if s.get('codec_type')=='video')
    if (vs.get('width'),vs.get('height'))!=(1080,1920): raise RuntimeError('Reel no es 1080x1920')
    log('REEL VERTICAL:',dest); return dest

def api(method,url,*,token=None,timeout=(20,180),headers=None,**kwargs):
    h=dict(headers or {})
    if token: h['Authorization']='Bearer '+token
    res=HTTP.request(method,url,headers=h,timeout=timeout,**kwargs)
    try: body=res.json()
    except ValueError: body={}
    if not res.ok:
        err=body.get('error') if isinstance(body,dict) else {}
        msg=err.get('message') if isinstance(err,dict) else res.reason
        raise RuntimeError(f'HTTP {res.status_code}: {str(msg)[:240]}')
    if not isinstance(body,dict): raise RuntimeError('Respuesta JSON inesperada')
    return body

def github_api(method,suffix,**kwargs):
    headers={'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    headers.update(kwargs.pop('headers',{}))
    return api(method,GH_API+suffix,token=os.environ['GH_TOKEN'],headers=headers,**kwargs)

def state_path(run_id=None):
    raw=str(run_id or os.getenv('GITHUB_RUN_ID') or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    safe=''.join(ch for ch in raw if ch.isalnum() or ch in '-_')
    attempt=''.join(ch for ch in str(os.getenv('GITHUB_RUN_ATTEMPT','1')) if ch.isdigit()) or '1'
    if not safe: raise RuntimeError('Identificador de ejecución vacío')
    return f'sismonauta_etas/runs/{safe}_attempt{attempt}.json'

def state_read(path):
    repo=os.environ['GITHUB_REPOSITORY']; branch=os.getenv('GITHUB_REF_NAME','main')
    url=f'{GH_API}/repos/{repo}/contents/{quote(path,safe="/")}'
    r=HTTP.get(url,params={'ref':branch},headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'},timeout=(15,65))
    if r.status_code==404: return {},None
    r.raise_for_status(); data=r.json(); return json.loads(base64.b64decode(data['content']).decode()),data['sha']

def state_save(path,data):
    repo=os.environ['GITHUB_REPOSITORY']; branch=os.getenv('GITHUB_REF_NAME','main')
    for attempt in range(4):
        prev,sha=state_read(path)
        if prev and prev.get('run_id') not in (None,data.get('run_id')): raise RuntimeError('Colisión del identificador de ejecución')
        payload={'message':'SISMONAUTA ETAS Chile: actualizar estado individual','content':base64.b64encode(json.dumps(data,ensure_ascii=False,indent=2,default=str).encode()).decode(),'branch':branch}
        if sha: payload['sha']=sha
        try:
            github_api('PUT',f'/repos/{repo}/contents/{quote(path,safe="/")}',json=payload); return
        except RuntimeError as exc:
            if 'HTTP 409' not in str(exc) and 'HTTP 422' not in str(exc): raise
            if attempt==3: raise
            time.sleep(2*(attempt+1))

def verify_accounts():
    required=['GH_TOKEN','GITHUB_REPOSITORY','INSTAGRAM_TOKEN','INSTAGRAM_USER_ID','FACEBOOK_PAGE_ID','FACEBOOK_PAGE_TOKEN']
    missing=[k for k in required if not os.getenv(k)]
    if missing: raise RuntimeError('Faltan variables: '+', '.join(missing))
    uid=os.environ['INSTAGRAM_USER_ID']; ig=api('GET',f'{IG_BASE}/{GRAPH_VERSION}/{uid}',token=os.environ['INSTAGRAM_TOKEN'],params={'fields':'id,username'})
    if str(ig.get('id'))!=uid or str(ig.get('username','')).lower()!='sismonauta': raise RuntimeError('El token Instagram no corresponde a @sismonauta')
    page=os.environ['FACEBOOK_PAGE_ID']; fb=api('GET',f'{FB_BASE}/{page}',token=os.environ['FACEBOOK_PAGE_TOKEN'],params={'fields':'id,name'})
    if str(fb.get('id'))!=page or 'sismonauta' not in str(fb.get('name','')).lower(): raise RuntimeError('El token Facebook no corresponde a Sismonauta')
    repo=github_api('GET','/repos/'+os.environ['GITHUB_REPOSITORY'])
    if repo.get('private'): raise RuntimeError('El repositorio debe ser público para usar el video desde GitHub Releases')
    log('Cuentas verificadas: @sismonauta / Sismonauta')

def release_video(video,persist,target):
    repo=os.environ['GITHUB_REPOSITORY']
    tag='sismonauta-etas-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+str(os.getenv('GITHUB_RUN_ID','local'))
    release=github_api('POST',f'/repos/{repo}/releases',json={'tag_name':tag,'target_commitish':os.getenv('GITHUB_REF_NAME','main'),'name':'SISMONAUTA ETAS Chile','draft':False,'prerelease':True,'body':'Modelo experimental. No constituye alerta sísmica.'})
    target['release_id']=str(release['id']); persist(); upload_url=release['upload_url'].split('{')[0]+'?name='+quote(video.name)
    with video.open('rb') as stream:
        asset=api('POST',upload_url,token=os.environ['GH_TOKEN'],timeout=(30,900),headers={'Accept':'application/vnd.github+json','Content-Type':'video/mp4'},data=stream)
    url=asset.get('browser_download_url')
    if not url or not url.startswith('https://github.com/'): raise RuntimeError('GitHub no devolvió URL pública')
    target['video_url']=url; persist(); return url

def publish_instagram(reel,caption,target,persist):
    token=os.environ['INSTAGRAM_TOKEN']; uid=os.environ['INSTAGRAM_USER_ID']; base=f'{IG_BASE}/{GRAPH_VERSION}'
    mode=os.getenv('IG_UPLOAD_MODE','url').lower()
    if mode not in ('url','resumable'): raise RuntimeError('IG_UPLOAD_MODE inválido')
    target['status']='uncertain'; persist()
    data={'media_type':'REELS','caption':caption[:2200],'share_to_feed':'true'}
    if mode=='url': data['video_url']=release_video(reel,persist,target)
    else: data['upload_type']='resumable'
    container=api('POST',f'{base}/{uid}/media',token=token,data=data); cid=str(container['id']); target['container_id']=cid; persist()
    if mode=='resumable':
        url=container.get('uri') or f'https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{cid}'
        parsed=urlparse(url)
        if parsed.scheme!='https' or parsed.hostname!='rupload.facebook.com': raise RuntimeError('URL Instagram no confiable')
        with reel.open('rb') as stream:
            r=HTTP.post(url,headers={'Authorization':'OAuth '+token,'offset':'0','file_size':str(reel.stat().st_size),'Content-Type':'application/octet-stream'},data=stream,timeout=(30,900))
        r.raise_for_status()
    for i in range(30):
        status=api('GET',f'{base}/{cid}',token=token,params={'fields':'status_code,status'}); code=status.get('status_code')
        log('Instagram procesamiento:',code,f'{i+1}/30')
        if code=='FINISHED': break
        if code in ('ERROR','EXPIRED'): raise RuntimeError('Instagram no procesó el video: '+str(status)[:160])
        time.sleep(20)
    else: raise RuntimeError('Instagram no confirmó procesamiento; revisar antes de reintentar')
    published=api('POST',f'{base}/{uid}/media_publish',token=token,data={'creation_id':cid}); mid=str(published.get('id') or '')
    if not mid.isdigit(): raise RuntimeError('Instagram no devolvió ID válido')
    target.update({'status':'published','id':mid}); persist(); log('INSTAGRAM REEL ACEPTADO:',mid)

def publish_facebook(video,caption,target,persist):
    target['status']='uncertain'; persist()
    with video.open('rb') as stream:
        result=api('POST',f'{FB_BASE}/{os.environ["FACEBOOK_PAGE_ID"]}/videos',token=os.environ['FACEBOOK_PAGE_TOKEN'],timeout=(30,900),data={'description':caption[:5000],'title':'SISMONAUTA | ETAS Chile experimental','published':'true'},files={'source':(video.name,stream,'video/mp4')})
    mid=str(result.get('id') or '')
    if not mid.isdigit(): raise RuntimeError('Facebook no devolvió ID válido')
    target.update({'status':'published','id':mid}); persist(); log('FACEBOOK VIDEO ACEPTADO:',mid)

def caption(metadata):
    cutoff=pd.Timestamp(metadata['cutoff_utc'])
    if cutoff.tzinfo is None: cutoff=cutoff.tz_localize('UTC')
    local=cutoff.tz_convert('America/Santiago'); day=local.strftime('%d/%m/%Y'); clock=local.strftime('%H:%M'); mag=float(metadata['magnitude_target'])
    return (f'🌎 SISMONAUTA | Actualización: {day}, {clock} h (Chile)\n\n'
            'ETAS (Epidemic-Type Aftershock Sequence) es un modelo estadístico que evalúa la tasa sísmica de fondo y la influencia temporal de los terremotos, permitiendo explorar patrones de actividad sísmica.\n\n'
            'El mapa de la izquierda muestra la probabilidad modelada por celda para las próximas 24 horas. El mapa derecho muestra las zonas con mayor aumento respecto al inicio del período analizado. '
            f'Umbral analizado: magnitud M≥{mag:.2f}.\n\n'
            '⚠️ IMPORTANTE: ETAS es un modelo experimental. No constituye una predicción de terremotos ni una alerta sísmica oficial.\n\n'
            '#Sismonauta #Chile #Sismologia #ETAS #Terremotos #Ciencia')

def main():
    global OUT
    parser=argparse.ArgumentParser(description='SISMONAUTA ETAS Chile unificado')
    parser.add_argument('--out',default=os.getenv('ETAS_OUTPUT_DIR','output_etas'))
    parser.add_argument('--publish',action='store_true',help='Publicar en Instagram y Facebook')
    parser.add_argument('--original',default='',help=argparse.SUPPRESS)
    parser.add_argument('--test-narration',action='store_true',help='Probar ciudad y distancia en speech sin acceder a la red')
    args=parser.parse_args()
    if args.test_narration: test_narration(); return
    OUT=Path(args.out).resolve(); OUT.mkdir(parents=True,exist_ok=True); binary('ffmpeg'); binary('ffprobe')
    receipt={'status':'starting','country':COUNTRY_NAME,'created_at':utcnow(),'run_id':os.getenv('GITHUB_RUN_ID') or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'),'targets':{'instagram':{'status':'pending'},'facebook':{'status':'pending'}}}
    individual_path=state_path(receipt['run_id'])
    def persist():
        atomic_json(OUT/'estado_publicacion.json',receipt)
        if args.publish: state_save(individual_path,receipt)
    try:
        if args.publish:
            verify_accounts(); log('MODO PRUEBAS: múltiples publicaciones diarias permitidas.'); log('Registro individual:',individual_path)
        receipt['status']='generating'; persist()
        video,metadata=run_scientific_etas(); duration=float(probe(video)['format']['duration'])
        if not 3<=duration<=900: raise RuntimeError(f'Duración no compatible con Reel: {duration:.1f}s')
        receipt.update({'duration_seconds':duration,'video_file':str(video),'scientific':{key:metadata[key] for key in ('snapshots','mc','b','magnitude_target')}}); persist()
        reel=make_reel(video); receipt['reel_file']=str(reel)
        if not args.publish:
            receipt['status']='preview_only'; persist(); log('VISTA PREVIA:',video,'| Reel:',reel); return
        receipt['status']='publishing'; persist(); errors=[]
        destinations=[('instagram',publish_instagram,reel),('facebook',publish_facebook,video)]
        for name,callback,filepath in destinations:
            target=receipt['targets'][name]
            try: callback(filepath,caption(metadata),target,persist)
            except Exception as exc:
                target['error']=str(exc)[:400]
                if target.get('status')!='published': target['status']='uncertain'
                persist(); errors.append(name+': '+str(exc))
        if errors:
            receipt['status']='partial_or_uncertain'; persist(); raise RuntimeError(' | '.join(errors))
        receipt['status']='published'; persist()
        log('PUBLICACIONES ACEPTADAS POR META: Instagram',receipt['targets']['instagram']['id'],'| Facebook',receipt['targets']['facebook']['id'])
    except Exception as exc:
        uncertain=any(t.get('status') in ('published','uncertain') for t in receipt['targets'].values())
        if receipt['status']!='published': receipt['status']='partial_or_uncertain' if uncertain else 'error'
        receipt['error']=str(exc)[:500]; atomic_json(OUT/'estado_publicacion.json',receipt)
        log('ERROR:',str(exc)); raise

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('SISMONAUTA ETAS ERROR:',str(exc),file=sys.stderr,flush=True); sys.exit(1)
