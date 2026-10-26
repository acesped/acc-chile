# ETAS MULTIPAÍS | GOOGLE COLAB, UNA CELDA | 25 MAPAS HORARIOS REALES
# USO: pegar TODO este archivo en una celda de Colab y ejecutarla.
# Alternativamente: %run /content/etas_multipaís_24h_animado.py
# Modelo experimental NO calibrado ni validado prospectivamente. NO ES ALERTA SÍSMICA.

import sys, subprocess, importlib.util
PACKAGES = [('numpy','numpy'),('pandas','pandas'),('scipy','scipy'),
            ('matplotlib','matplotlib'),('requests','requests'),('cartopy','cartopy'),
            ('numba','numba'),('Pillow','PIL'),('edge-tts','edge_tts'),
            ('gTTS','gtts'),('pycountry','pycountry'),('geopy','geopy'),
            ('shapely','shapely')]
for pkg,mod in PACKAGES:
    if importlib.util.find_spec(mod) is None:
        print('Instalando', pkg, flush=True)
        subprocess.check_call([sys.executable,'-m','pip','install','--prefer-binary',pkg])

import io, os, time, math, shutil, tempfile, asyncio, unicodedata, zipfile
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
from numba import njit
from PIL import Image, ImageDraw, ImageFont
from IPython.display import Video, display
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from cartopy.io import shapereader
from shapely.geometry import Point
from shapely.ops import unary_union
import pycountry
from geopy.geocoders import Nominatim

# CONFIGURACIÓN
START = '2018-01-01'
DOWNLOAD_M = 3.0
REQUESTED_M = 4.0
HORIZON_DAYS = 1.0                 # cada fotograma: probabilidad para sus 24 h FUTURAS
LOOKBACK_HOURS = 24                # últimas 24 h de evolución del modelo
SNAPSHOT_MINUTES = 60             # 25 mapas (inicio y final, uno por hora)
VIDEO_SECONDS = 30
FPS = 24
FIT_N = 1300
TRIGGER_DAYS = 365
TRIGGER_MAX = 2500
MAX_GRID_CELLS = 15000
W, H = 1080, 1350                    # Instagram 4:5
OUTPUT_MP4 = ('/content/ETAS_EVOLUCION_24H.mp4' if os.path.isdir('/content')
              else '/mnt/data/ETAS_EVOLUCION_24H.mp4')
CUTOFF = pd.Timestamp.now(tz='UTC')

COUNTRY_NAME = ''; COUNTRY_ISO = ''; COUNTRY_GEOM = None
LAT0=LAT1=LON0=LON1=GRID=None
LATS=LONS=XX=YY=None
CITY=[]; CITY_SOURCE=''
ALIASES = {
    'chile':'CHL','peru':'PER','mexico':'MEX','estados unidos':'USA','eeuu':'USA',
    'united states':'USA','japon':'JPN','japan':'JPN','indonesia':'IDN',
    'turquia':'TUR','turkey':'TUR','argentina':'ARG','ecuador':'ECU',
    'colombia':'COL','nueva zelanda':'NZL','new zealand':'NZL',
    'espana':'ESP','spain':'ESP','brasil':'BRA','brazil':'BRA','italia':'ITA',
    'italy':'ITA','filipinas':'PHL','philippines':'PHL','bolivia':'BOL',
    'guatemala':'GTM','costa rica':'CRI','el salvador':'SLV',
    'venezuela':'VEN','rusia':'RUS','russia':'RUS','islandia':'ISL',
    'iceland':'ISL','haiti':'HTI','grecia':'GRC','republica dominicana':'DOM',
    'reino unido':'GBR','uk':'GBR'
}

def _norm(s):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(s).lower())
                   if not unicodedata.combining(c)).strip()

def setup_country():
    global COUNTRY_NAME,COUNTRY_ISO,COUNTRY_GEOM,LAT0,LAT1,LON0,LON1,GRID,LATS,LONS,XX,YY,CITY,CITY_SOURCE
    name=input('País a analizar (Chile, Perú, México, Japón, etc.): ').strip() or 'Chile'
    try:
        found=pycountry.countries.get(alpha_3=ALIASES.get(_norm(name),name.upper()))
        if found is None: found=pycountry.countries.lookup(name)
        code=found.alpha_3
    except LookupError as ex:
        raise ValueError(f'País no reconocido: {name}') from ex
    shp=shapereader.natural_earth(resolution='110m',category='cultural',name='admin_0_countries')
    reader=shapereader.Reader(shp)
    geoms=[r.geometry for r in reader.records() if code in
           [r.attributes.get(k,'') for k in ('ADM0_A3','SOV_A3','ISO_A3','GU_A3','SU_A3')]]
    reader.close()
    if not geoms: raise RuntimeError('No se encontró el país en Natural Earth.')
    geom=unary_union(geoms)
    parts=list(geom.geoms) if hasattr(geom,'geoms') else [geom]
    polys=[g for g in parts if g.geom_type=='Polygon']
    if polys:
        biggest=max(polys,key=lambda g:g.area)
        kept=[g for g in polys if g.area>=.025*biggest.area]
        geom=unary_union(kept)
        if len(kept)<len(polys):
            print('Aviso: representación limitada a componentes geográficas principales.',flush=True)
    x0,y0,x1,y1=geom.bounds
    if x1-x0>150: raise RuntimeError('País demasiado extenso o cruza el antimeridiano.')
    LAT0=max(-89.0,y0-2.2);LAT1=min(89.0,y1+2.2)
    LON0=max(-179.9,x0-2.2);LON1=min(179.9,x1+2.2)
    GRID=round(max(.20,math.sqrt((LAT1-LAT0)*(LON1-LON0)/MAX_GRID_CELLS)),3)
    LATS=np.arange(LAT0,LAT1+GRID/2,GRID)
    LONS=np.arange(LON0,LON1+GRID/2,GRID)
    XX,YY=np.meshgrid(LONS,LATS)
    COUNTRY_GEOM=geom;COUNTRY_ISO=code;COUNTRY_NAME=name.title()
    print(f'{COUNTRY_NAME}: {XX.size:,} celdas | malla {GRID:.3f}°',flush=True)
    try:
        r=requests.get('https://download.geonames.org/export/dump/cities15000.zip',timeout=35)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            with z.open('cities15000.txt') as f:
                table=pd.read_csv(f,sep='\t',header=None,usecols=[1,4,5,8],
                                  names=['name','lat','lon','iso2'],low_memory=False)
        table=table[table.lat.between(LAT0,LAT1)&table.lon.between(LON0,LON1)]
        local=table[table.iso2==found.alpha_2]
        if len(local):table=local
        CITY=[(str(q.name),float(q.lat),float(q.lon)) for q in table.itertuples(index=False)]
        CITY_SOURCE='GeoNames cities15000 (CC BY 4.0)'
        print('Ciudades de referencia:',len(CITY),flush=True)
    except Exception as ex:
        CITY=[]; CITY_SOURCE='Nominatim/OpenStreetMap'
        print('GeoNames no disponible:',str(ex)[:100],flush=True)

def nearest_city(lat,lon):
    if CITY:
        lats=np.radians([c[1] for c in CITY]);lons=np.radians([c[2] for c in CITY])
        dy=lats-np.radians(lat);dx=lons-np.radians(lon)
        a=np.sin(dy/2)**2+np.cos(np.radians(lat))*np.cos(lats)*np.sin(dx/2)**2
        dist=12742*np.arcsin(np.sqrt(np.clip(a,0,1)))
        i=int(dist.argmin());return float(dist[i]),CITY[i][0]
    try:
        geocoder=Nominatim(user_agent='etas-multicountry-animated/2.0',timeout=15)
        res=geocoder.reverse((lat,lon),language='es',zoom=10,exactly_one=True)
        address=res.raw.get('address',{}) if res else {}
        local=next((address.get(k) for k in ('city','town','village','municipality','county') if address.get(k)),None)
        if local:
            dlat=np.radians(res.latitude-lat);dlon=np.radians(res.longitude-lon)
            a=np.sin(dlat/2)**2+np.cos(np.radians(lat))*np.cos(np.radians(res.latitude))*np.sin(dlon/2)**2
            return float(12742*np.arcsin(np.sqrt(a))),str(local)
    except Exception:pass
    return None,'localidad no identificada'

def catalog_usgs():
    frames=[]; session=requests.Session()
    session.headers.update({'User-Agent':'ETAS-Multicountry-Animation/2.0 (research)'})
    begin=pd.Timestamp(START,tz='UTC');blocks=[]
    while begin<CUTOFF:
        end=min(begin+pd.DateOffset(months=1),CUTOFF)
        blocks.append((begin,end));begin=end
    for i,(start,end) in enumerate(blocks,1):
        params=dict(format='csv',starttime=start.strftime('%Y-%m-%dT%H:%M:%S'),
                    endtime=end.strftime('%Y-%m-%dT%H:%M:%S'),minlatitude=LAT0,
                    maxlatitude=LAT1,minlongitude=LON0,maxlongitude=LON1,
                    minmagnitude=DOWNLOAD_M,orderby='time-asc',limit=20000)
        for attempt in range(4):
            try:
                r=session.get('https://earthquake.usgs.gov/fdsnws/event/1/query',params=params,timeout=60)
                if r.status_code==204:
                    part=pd.DataFrame(columns=['time','latitude','longitude','mag','id'])
                else:
                    r.raise_for_status();part=pd.read_csv(io.StringIO(r.text))
                    if len(part)>=20000:raise RuntimeError('Límite USGS: dividir el bloque mensual.')
                break
            except Exception as ex:
                if attempt==3:raise RuntimeError(f'Error USGS {start:%Y-%m}: {ex}') from ex
                time.sleep(2*(attempt+1))
        frames.append(part)
        if i%4==0 or i==len(blocks):print(f'USGS: {i}/{len(blocks)} meses',flush=True)
    df=pd.concat(frames,ignore_index=True)
    df['time']=pd.to_datetime(df['time'],utc=True,errors='coerce')
    for field in ['latitude','longitude','mag']:
        df[field]=pd.to_numeric(df[field],errors='coerce')
    df=df.dropna(subset=['time','latitude','longitude','mag'])
    df=df[(df.time<CUTOFF)&df.latitude.between(LAT0,LAT1)&
          df.longitude.between(LON0,LON1)&(df.mag>=DOWNLOAD_M)]
    area=COUNTRY_GEOM.buffer(2.5)
    from shapely.prepared import prep
    prepared=prep(area)
    keep=np.fromiter((prepared.covers(Point(float(lon),float(lat)))
                      for lat,lon in zip(df.latitude,df.longitude)),dtype=bool,count=len(df))
    df=df.loc[keep].drop_duplicates(subset=['id']).sort_values('time').reset_index(drop=True)
    if len(df)<200:raise RuntimeError('Menos de 200 eventos en el catálogo filtrado.')
    print(f'Eventos válidos: {len(df):,}',flush=True)
    return df

def estimate_mc_b(df):
    m=df.mag.to_numpy()
    edges=np.arange(np.floor(m.min()*10)/10,np.ceil(m.max()*10)/10+.21,.1)
    hist,_=np.histogram(m,bins=edges);mc=round(float(edges[hist.argmax()]),2)
    selected=m[m>=mc]
    if len(selected)<100:raise RuntimeError('Menos de 100 eventos por encima de Mc.')
    denom=selected.mean()-(mc-.05)
    if denom<=0:raise RuntimeError('No se pudo estimar b.')
    return mc,float(np.log10(np.e)/denom)

@njit
def negll(th,t,m,mc,T):
    mu,K,alpha,c,p=th
    if mu<=0 or K<=0 or c<=0 or p<=1:return 1e90
    out=mu*T
    for i in range(len(t)):
        rate=mu
        for j in range(i):
            dt=t[i]-t[j]
            if dt>0:rate+=K*np.exp(alpha*(m[j]-mc))*(dt+c)**(-p)
        if rate<=0 or not np.isfinite(rate):return 1e90
        out-=np.log(rate)
    for j in range(len(t)):
        age=T-t[j]
        if age>0:
            out+=K*np.exp(alpha*(m[j]-mc))*((age+c)**(1-p)-c**(1-p))/(1-p)
    return out if np.isfinite(out) else 1e90

def fit_etas(df,mc):
    sub=df[df.mag>=mc].tail(FIT_N)
    if len(sub)<100:raise RuntimeError('Eventos insuficientes para ETAS.')
    t=(sub.time-sub.time.iloc[0]).dt.total_seconds().to_numpy()/86400.
    m=sub.mag.to_numpy();T=max(float(t[-1]),1e-6)
    bounds=[(1e-6,100),(1e-8,5),(0,3),(.002,2),(1.01,2.8)]
    fits=[]
    for i,x0 in enumerate(([.2,.01,.8,.02,1.2],[.5,.005,1.,.08,1.3],[1.,.001,.6,.1,1.5]),1):
        print(f'Ajuste ETAS {i}/3',flush=True)
        r=minimize(negll,x0,args=(t,m,mc,T),method='L-BFGS-B',bounds=bounds,options={'maxiter':260})
        if r.success and np.isfinite(r.fun):fits.append(r)
    if not fits:raise RuntimeError('ETAS no convergió.')
    return dict(zip(['mu','K','alpha','c','p'],map(float,min(fits,key=lambda x:x.fun).x)))

def make_background(events):
    binslat=np.r_[LATS-GRID/2,LATS[-1]+GRID/2]
    binslon=np.r_[LONS-GRID/2,LONS[-1]+GRID/2]
    hist,_,_=np.histogram2d(events.latitude,events.longitude,bins=[binslat,binslon])
    a=gaussian_filter(hist,1.7)+1e-10
    return (a/a.sum()).astype(np.float32)

def evolution_24h(df,theta,mc,b,mag):
    """Reconstrucciones causales: solo eventos anteriores a cada hora simulada.

    Parámetros ETAS calibrados exclusivamente con datos anteriores a la
    primera hora mostrada, para evitar usar información futura en el ajuste.
    El valor mostrado en cada instante es el pronóstico para las siguientes 24 h.
    """
    start=CUTOFF-pd.Timedelta(hours=LOOKBACK_HOURS)
    times=pd.date_range(start=start,end=CUTOFF,freq='1h')
    if times[-1] != CUTOFF:times=times.append(pd.DatetimeIndex([CUTOFF]))
    chosen=df[(df.time<CUTOFF)&(df.time>=start-pd.Timedelta(days=TRIGGER_DAYS))&
              (df.mag>=mc)].copy().reset_index(drop=True)
    if len(chosen)>TRIGGER_MAX:
        raise RuntimeError(f'{len(chosen)} disparadores en 365 días; aumentar TRIGGER_MAX. No se descartan eventos.')
    print(f'Preparando {len(times)} instantáneas; {len(chosen)} disparadores.',flush=True)
    # Cada kernel conserva exactamente la normalización espacial del programa original.
    kernels=np.empty((len(chosen),XX.size),dtype=np.float32)
    lat_flat=YY.ravel().astype(np.float32)
    lon_flat=XX.ravel().astype(np.float32)
    for j,e in enumerate(chosen.itertuples(index=False)):
        dx=(lon_flat-e.longitude)*(111.32*np.cos(np.radians(e.latitude)))
        dy=(lat_flat-e.latitude)*111.32
        weight=np.power(1+(dx*dx+dy*dy)/(25.**2),-1.5)
        kernels[j]=weight/weight.sum()
        if (j+1)%500==0:print(f'Kernels: {j+1}/{len(chosen)}',flush=True)
    event_times=chosen.time.astype('int64').to_numpy()
    magnitudes=chosen.mag.to_numpy(dtype=np.float64)
    prod=np.exp(np.clip(theta['alpha']*(magnitudes-mc),-40,40))
    factor=10**(-b*(mag-mc))
    c,p=theta['c'],theta['p']
    probabilities=[]; maxima=[]
    for k,stamp in enumerate(times):
        # A cada hora, SOLO sismos ya ocurridos alimentan el mapa.
        timestamp=stamp.value
        ages=(timestamp-event_times)/1e9/86400.
        valid=(ages>0)&(ages<=TRIGGER_DAYS)
        masses=np.zeros(len(chosen),dtype=np.float32)
        a=ages[valid]
        masses[valid]=(theta['K']*prod[valid]*
                       ((a+HORIZON_DAYS+c)**(1-p)-(a+c)**(1-p))/(1-p)).astype(np.float32)
        before=df[(df.time<stamp)&(df.mag>=mc)]
        bg=make_background(before)
        expected=theta['mu']*HORIZON_DAYS*bg.ravel()
        if len(chosen):expected=expected+(masses@kernels)
        forecast=-np.expm1(-expected.reshape(bg.shape)*factor)
        if not np.all(np.isfinite(forecast)):raise RuntimeError('Mapa no finito.')
        forecast=np.clip(forecast,0,1).astype(np.float32)
        row,col=np.unravel_index(np.argmax(forecast),forecast.shape)
        probabilities.append(forecast)
        maxima.append((float(LATS[row]),float(LONS[col]),float(forecast[row,col])*100))
        print(f'Mapa {k+1}/{len(times)} · {stamp:%d/%m %H:%M} UTC · máximo {maxima[-1][2]:.4f}%',flush=True)
    deltas=[float(np.max(np.abs(probabilities[k]-probabilities[k-1]))*100)
            for k in range(1,len(probabilities))]
    print(f'Cambio máximo entre mapas consecutivos: {max(deltas,default=0):.8f} puntos porcentuales.',flush=True)
    print(f'Intervalos con cambios detectables (>1e-8 pp): {sum(d>1e-8 for d in deltas)}/{len(deltas)}',flush=True)
    return times,probabilities,maxima

def _font(size,bold=False):
    files=('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else
           '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    try:return ImageFont.truetype(files,size)
    except Exception:return ImageFont.load_default()

def _label_box(draw,xy,text,font,fill='#FFFFFF',bg='#102638'):
    x,y=xy
    bb=draw.textbbox((x,y),text,font=font)
    draw.rounded_rectangle((x-9,y-4,bb[2]+9,bb[3]+5),radius=9,fill=bg)
    draw.text((x,y),text,font=font,fill=fill)

def draw_snapshot(prob,stamp,idx,total,clims,final_info,work,baseline,delta_scale):
    """Mapa principal: variación horaria ETAS vs -24 h, escala fija en pp."""
    fig=plt.figure(figsize=(10.8,13.5),dpi=100,facecolor='#F3F7FA')
    ax=fig.add_axes([.065,.185,.87,.668],projection=ccrs.PlateCarree())
    ax.set_extent([LON0,LON1,LAT0,LAT1],crs=ccrs.PlateCarree())
    ax.set_facecolor('#DFEAF1')
    try:
        ax.add_feature(cfeature.LAND.with_scale('110m'),facecolor='#EDE9E2',zorder=0)
        ax.add_feature(cfeature.OCEAN.with_scale('110m'),facecolor='#DFEAF1',zorder=0)
    except Exception:pass
    # El mapa principal es el CAMBIO REAL del ETAS respecto al inicio.
    # Se emplea un solo campo de color con una escala divergente FIJA
    # calculada para toda la secuencia; no existe mapa superpuesto.
    percent=prob*100.0
    change=(prob.astype(np.float64)-baseline.astype(np.float64))*100.0
    cmap_change=plt.get_cmap('RdBu_r')
    mesh=ax.pcolormesh(
        LONS,LATS,change,cmap=cmap_change,
        norm=mcolors.TwoSlopeNorm(vmin=-delta_scale,vcenter=0.0,vmax=delta_scale),
        shading='nearest',transform=ccrs.PlateCarree(),zorder=2,alpha=.98
    )
    ax.coastlines('110m',color='#173244',linewidth=1.3,zorder=8)
    ax.add_feature(cfeature.BORDERS.with_scale('110m'),edgecolor='#253F50',
                   linewidth=.65,linestyle='--',zorder=8)
    # Contornos de mayor resolución opcionales, sin descargarlos en cada fotograma.
    selected=sorted(CITY,key=lambda c:(c[1]-final_info['lat'])**2+
                    (c[2]-final_info['lon'])**2)[:11]
    for name,lat,lon in selected:
        ax.plot(lon,lat,'o',ms=3.4,color='#152E3F',markeredgecolor='white',
                markeredgewidth=.5,transform=ccrs.PlateCarree(),zorder=10)
        ax.text(lon+.14,lat+.08,name,fontsize=8,color='#183143',
                transform=ccrs.PlateCarree(),zorder=11,
                bbox=dict(facecolor='white',edgecolor='none',alpha=.8,pad=1.2))
    rr,cc=np.unravel_index(np.argmax(prob),prob.shape)
    latpeak=float(LATS[rr]);lonpeak=float(LONS[cc]);maxval=float(percent[rr,cc])
    ax.plot(lonpeak,latpeak,marker='*',ms=20,markerfacecolor='#FFE55B',
            markeredgecolor='#142B3A',markeredgewidth=1.6,zorder=20,
            transform=ccrs.PlateCarree())
    gl=ax.gridlines(draw_labels=True,linewidth=.35,alpha=.3,linestyle=':')
    gl.top_labels=False;gl.right_labels=False
    gl.xlabel_style={'size':9,'color':'#566C7E'}
    gl.ylabel_style={'size':9,'color':'#566C7E'}
    for spine in ax.spines.values():spine.set_edgecolor('#B5C8D5')
    cax=fig.add_axes([.10,.129,.80,.019])
    bar=fig.colorbar(mesh,cax=cax,orientation='horizontal')
    bar.set_ticks([-delta_scale,0,delta_scale])
    bar.ax.xaxis.set_major_formatter(FuncFormatter(lambda v,pos:f'{v:+.3g} pp' if v else '0 pp'))
    bar.ax.tick_params(labelsize=10,colors='#2E4C60')
    bar.set_label('Cambio de probabilidad ETAS desde hace 24 h (puntos porcentuales)',
                  fontsize=11,labelpad=8,color='#1D3D52')
    bar.outline.set_visible(False)
    # Cabecera y leyendas. Texto exterior se dibuja en PIL para mejorar legibilidad.
    path=os.path.join(work,f'ancla_{idx:03d}.png')
    fig.savefig(path,dpi=100,facecolor=fig.get_facecolor())
    plt.close(fig)
    image=Image.open(path).convert('RGB')
    dr=ImageDraw.Draw(image)
    dr.text((72,35),'CAMBIO HORARIO ETAS · ÚLTIMAS 24 HORAS',font=_font(33,True),fill='#173247')
    dr.text((72,91),f'{COUNTRY_NAME.upper()}   ·   {stamp:%d/%m/%Y %H:%M} UTC',
            font=_font(24),fill='#395D74')
    dr.rounded_rectangle((65,1105,1015,1224),radius=18,fill='#E4EDF4',outline='#D0DEE9',width=2)
    dr.text((89,1117),'MÁXIMO ABSOLUTO ETAS EN ESTA HORA',font=_font(18,True),fill='#49657A')
    dr.text((89,1150),f'{maxval:.6f}%     |     {latpeak:+.2f}°, {lonpeak:+.2f}°',
            font=_font(25,True),fill='#173A52')
    difference=np.abs(change)
    changed=float(difference.max())
    changed_cells=int(np.count_nonzero(difference>max(delta_scale*1e-6,1e-12)))
    dr.text((89,1190),f'Cambio máximo vs -24 h: {changed:.8g} pp  |  celdas con cambio: {changed_cells:,}',
            font=_font(17),fill='#314D60')
    # Línea de tiempo representa posición cronológica, no un gráfico X-Y.
    left,right=94,986;y=1260
    dr.line((left,y,right,y),fill='#C4D5E0',width=12)
    progress=idx/max(total-1,1)
    dr.line((left,y,left+round((right-left)*progress),y),fill='#1A729F',width=12)
    x=left+round((right-left)*progress)
    dr.ellipse((x-13,y-13,x+13,y+13),fill='#FFC736',outline='#193F53',width=3)
    dr.text((91,1285),'HACE 24 H',font=_font(18,True),fill='#4B6477')
    dr.text((843,1285),'AHORA',font=_font(18,True),fill='#4B6477')
    dr.text((70,1323),'Modelo experimental · No constituye una alerta sísmica oficial',
            font=_font(16),fill='#667A89')
    image.save(path,optimize=True)
    return path

def _music_wav(filepath,duration=30,sr=24000):
    from scipy.io import wavfile
    t=np.arange(sr*duration,dtype=np.float64)/sr
    audio=np.zeros_like(t)
    chords=[(196.,246.94,293.66),(174.61,220.,261.63),
            (164.81,196.,246.94),(146.83,185.,220.)]
    for begin in range(0,duration,4):
        local=t-begin;active=(local>=0)&(local<4)
        env=(np.sin(np.pi*np.clip(local,0,4)/4)**1.3)*active
        for f in chords[(begin//4)%len(chords)]:
            phase=2*np.pi*f*t
            audio+=(.6*np.sin(phase)+.13*np.sin(phase*2))*env/3
    for beat in np.arange(0,duration,2):
        tau=t-beat;active=(tau>=0)&(tau<.55)
        audio+=.12*np.sin(2*np.pi*98*t)*np.exp(-9*np.maximum(tau,0))*active
    audio*=np.clip(t/1.2,0,1)*np.clip((duration-t)/2.4,0,1)
    if np.max(np.abs(audio))>0:audio*=.67/np.max(np.abs(audio))
    wavfile.write(filepath,sr,(np.clip(audio,-1,1)*32767).astype(np.int16))

def narration(final_info,first_max,last_max):
    change=last_max-first_max
    if abs(change)<.00005:
        trend='La estimación en el máximo del mapa terminó prácticamente estable.'
    elif change>0:
        trend='Durante las últimas veinticuatro horas, el máximo estimado del mapa aumentó.'
    else:
        trend='Durante las últimas veinticuatro horas, el máximo estimado del mapa disminuyó.'
    location=(f'La ciudad de referencia más cercana es {final_info["city"]}, '
              f'a unos {final_info["distance"]:.0f} kilómetros.'
              if final_info['city']!='localidad no identificada'
              else 'No fue posible identificar una ciudad de referencia cercana.')
    def decimal(n,digits=4):return f'{n:.{digits}f}'.replace('.',',')
    lat=final_info['lat'];lon=final_info['lon']
    ns='sur' if lat<0 else 'norte';ew='oeste' if lon<0 else 'este'
    return (f'Así cambió el mapa ETAS de {COUNTRY_NAME} en las últimas veinticuatro horas. '
            f'{trend} '
            f'Ahora, el valor más alto se localiza en {decimal(abs(lat),2)} grados {ns}, '
            f'{decimal(abs(lon),2)} grados {ew}. '
            f'{location} '
            f'En esa celda, la probabilidad experimental estimada de al menos un sismo '
            f'de magnitud {final_info["mag"]:.1f} o superior, en las próximas veinticuatro horas, '
            f'es {decimal(last_max)} por ciento. '
            'No se refiere a toda la ciudad, ni permite predecir el momento de un terremoto. '
            'No es una alerta sísmica oficial.')

def _voice(text,work):
    filepath=os.path.join(work,'voz.mp3')
    try:
        import edge_tts
        async def speak():
            await edge_tts.Communicate(text,voice='es-CL-CatalinaNeural',rate='+24%').save(filepath)
        try:asyncio.run(speak())
        except RuntimeError:
            loop=asyncio.new_event_loop()
            try:loop.run_until_complete(speak())
            finally:loop.close()
        if os.path.getsize(filepath)>1000:return filepath
    except Exception as ex:print('Edge TTS no disponible:',str(ex)[:90],flush=True)
    try:
        from gtts import gTTS
        gTTS(text,lang='es',tld='cl').save(filepath)
        if os.path.getsize(filepath)>1000:return filepath
    except Exception as ex:print('gTTS no disponible:',str(ex)[:90],flush=True)
    raise RuntimeError('No se pudo generar la voz; comprueba la conexión a internet.')

def _ffmpeg(*args):
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y',*map(str,args)]
    p=subprocess.run(cmd,capture_output=True,text=True)
    if p.returncode:raise RuntimeError('FFmpeg: '+p.stderr[-1600:])

def create_video(times,maps,maxima,final_info):
    if not shutil.which('ffmpeg'):
        subprocess.run(['apt-get','update','-qq'],check=True)
        subprocess.run(['apt-get','install','-y','-qq','ffmpeg'],check=True)
    work=tempfile.mkdtemp(prefix='etas_24h_')
    try:
        positive=np.concatenate([z[z>0] for z in maps])
        vmax=float(positive.max()*100)
        vmin=max(float(np.percentile(positive,2)*100),vmax/1000,1e-10)
        if vmin>=vmax:vmin=max(vmax/1000,1e-10)
        clims=(vmin,vmax)
        # La imagen principal muestra diferencia vs primer mapa (NO probabilidad absoluta).
        # La magnitud de probabilidad real está en el texto y la estrella.
        # La escala se fija para las 25 horas; si todos son iguales permanece gris/blanco.
        baseline=maps[0]
        changes=np.array([np.max(np.abs((z.astype(np.float64)-baseline)*100.0)) for z in maps])
        delta_scale=max(float(changes.max()),1e-12)
        print(f'Cambio acumulado máximo real: {changes.max():.9g} puntos porcentuales.',flush=True)
        anchors=[]
        for i,(stamp,prob) in enumerate(zip(times,maps)):
            anchors.append(draw_snapshot(prob,stamp,i,len(times),clims,final_info,work,baseline,delta_scale))
        narration_text=narration(final_info,maxima[0][2],maxima[-1][2])
        print('\nNARRACIÓN:\n'+narration_text+'\n',flush=True)
        voice=_voice(narration_text,work)
        soundtrack=os.path.join(work,'musica.wav')
        _music_wav(soundtrack,duration=VIDEO_SECONDS)
        # Ajusta la duración de voz para que el cierre sobre 'alerta oficial' no se corte.
        p=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration',
                          '-of','default=noprint_wrappers=1:nokey=1',voice],
                         capture_output=True,text=True)
        try:voice_seconds=float(p.stdout.strip())
        except ValueError:voice_seconds=0.
        if voice_seconds>VIDEO_SECONDS-1.0:
            factor=voice_seconds/(VIDEO_SECONDS-1.5)
            # atempo acepta 0.5-2 por cada filtro; encadenamos cuando corresponde.
            filters=[]
            while factor>2.:
                filters.append('atempo=2.0');factor/=2.
            filters.append(f'atempo={factor:.4f}')
            fastvoice=os.path.join(work,'voz_acelerada.wav')
            _ffmpeg('-i',voice,'-af',','.join(filters),fastvoice)
            voice=fastvoice
        # MAPAS HORARIOS, sin interpolación entre horas. Principal = diferencia desde -24 h.
        # Cada PNG procede de UN pronóstico ETAS completo distinto. La escala
        # cromática se mantiene fija para TODOS los mapas. El color muestra
        # diferencia (pp) y NO el valor absoluto. Si no hubo cambios, colores similares
        # son un resultado legítimo del modelo, no un fallo de animación.
        silent=os.path.join(work,'video_sin_audio.mp4')
        cmd=['ffmpeg','-hide_banner','-loglevel','error','-y',
             '-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}',
             '-r',str(FPS),'-i','pipe:0','-c:v','libx264','-preset','veryfast',
             '-crf','19','-pix_fmt','yuv420p','-movflags','+faststart',silent]
        process=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            count=VIDEO_SECONDS*FPS
            # 25 capturas repartidas a lo largo de 30 segundos (~1,2 s cada una).
            for j,path in enumerate(anchors):
                first=(j*count)//len(anchors)
                last=((j+1)*count)//len(anchors)
                with Image.open(path) as im:
                    frame=im.convert('RGB')
                    if frame.size!=(W,H):
                        raise RuntimeError(f'Tamaño de frame inesperado: {frame.size}')
                    pixels=frame.tobytes()
                for _ in range(last-first):
                    process.stdin.write(pixels)
                print(f'Video: mapa horario {j+1}/{len(anchors)} · fotogramas {first}-{last-1}',flush=True)
            process.stdin.close();process.stdin=None
            stderr=process.stderr.read().decode('utf-8',errors='replace')
            if process.wait():raise RuntimeError('Codificación MP4: '+stderr[-1600:])
        except Exception:
            process.kill();process.wait();raise
        filters=('[2:a]aformat=sample_fmts=fltp:channel_layouts=mono,volume=0.37[music];'
                 '[1:a]aformat=sample_fmts=fltp:channel_layouts=mono,'
                 'adelay=350,volume=1.0,asplit=2[sc][speech];'
                 '[music][sc]sidechaincompress=threshold=0.025:ratio=6:attack=25:release=350[duck];'
                 '[duck][speech]amix=inputs=2:duration=longest:normalize=0,'
                 f'atrim=duration={VIDEO_SECONDS},asetpts=N/SR/TB,alimiter=limit=0.92[aout]')
        _ffmpeg('-i',silent,'-i',voice,'-stream_loop','-1','-i',soundtrack,
                '-filter_complex',filters,'-map','0:v:0','-map','[aout]',
                '-t',VIDEO_SECONDS,'-c:v','copy','-c:a','aac','-b:a','160k',
                '-movflags','+faststart',OUTPUT_MP4)
        print('VIDEO FINAL:',OUTPUT_MP4,flush=True)
        display(Video(OUTPUT_MP4,embed=True,width=540))
        if os.path.isdir('/content'):
            print('Archivo disponible en el panel Archivos de Google Colab.',flush=True)
    finally:
        shutil.rmtree(work,ignore_errors=True)

def main():
    global CUTOFF
    CUTOFF=pd.Timestamp.now(tz='UTC')
    print(f'Corte del catálogo: {CUTOFF:%d/%m/%Y %H:%M} UTC',flush=True)
    setup_country()
    df=catalog_usgs()
    # Este es el cambio metodológico clave: ajustamos ANTES de la ventana animada.
    # Por tanto, parámetros Mc, b y ETAS no utilizan ningún sismo de las 24 h animadas.
    start=CUTOFF-pd.Timedelta(hours=LOOKBACK_HOURS)
    training=df[df.time<start].copy()
    if len(training)<200:raise RuntimeError('Catálogo histórico insuficiente para ajustar ETAS.')
    mc,b=estimate_mc_b(training)
    mag=max(REQUESTED_M,mc)
    if mag>REQUESTED_M:
        print(f'AVISO: Mc={mc:.2f}, se usa M≥{mag:.2f}; no exactamente M≥4.',flush=True)
    print(f'Mc={mc:.2f}; b={b:.3f}; magnitud objetivo M≥{mag:.2f}',flush=True)
    theta=fit_etas(training,mc)
    times,maps,maxima=evolution_24h(df,theta,mc,b,mag)
    lat,lon,pmax=maxima[-1]
    distance,city=nearest_city(lat,lon)
    info={'lat':lat,'lon':lon,'pmax':pmax,'city':city,
          'distance':distance if distance is not None else 0.,'mag':mag}
    print(f'MÁXIMO ACTUAL: {lat:.3f}°, {lon:.3f}° · {pmax:.6f}% · {city}',flush=True)
    print('Nota: porcentaje local de una celda; no riesgo de daño, ni probabilidad municipal.',flush=True)
    create_video(times,maps,maxima,info)

main()
