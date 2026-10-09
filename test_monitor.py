"""Pruebas focalizadas. La red queda bloqueada para todas las pruebas."""
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
import requests
from obspy import Stream, Trace, UTCDateTime
import monitor as m

NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def event(age=3600):
    return {"id": "385789", "origin": m.iso(NOW-timedelta(seconds=age)),
            "mag": 4.7, "mag_display": "4.7", "reference": "20 km al O de Quintero",
            "depth": 12.0, "lat": -33.1, "lon": -71.2,
            "url": "https://www.sismologia.cl/sismicidad/informes/2026/10/385789.html"}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Red prohibida durante pruebas")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    for name in m.SECRET_NAMES + ("GITHUB_REPOSITORY", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)


def cat(mag="3.0", rows=True):
    body = (f'<tr><td><a href="/sismicidad/informes/2026/10/385789.html">fecha</a></td>'
            f'<td>{mag}</td><td>2026-10-08 11:00:00</td></tr>') if rows else ""
    return ('<table><tr><th>Fecha Local / Lugar</th><th>Magnitud</th><th>Fecha UTC</th></tr>'
            +body+'</table>').encode()


def test_valid_no_eligible_and_one_get_per_page(monkeypatch):
    get = Mock(return_value=cat())
    monkeypatch.setattr(m, "obtener", get)
    events, source = m.discover(m.Config(), m.Clock(100), NOW-timedelta(hours=12), NOW)
    assert events == [] and source["status"] == "valida"
    urls = [a.args[0] for a in get.call_args_list]
    assert len(urls) == len(set(urls)) == 2
    assert m.catalogue_links(cat(rows=False), m.CSN, True, m.Config()) == set()


@pytest.mark.parametrize("kind,expected", [(m.SourceError, "inaccesible"), (m.StructureError, "incompatible")])
def test_source_failure(monkeypatch, kind, expected):
    monkeypatch.setattr(m, "obtener", Mock(side_effect=kind("fallo")))
    es, s = m.discover(m.Config(), m.Clock(50), NOW-timedelta(hours=1), NOW)
    assert not es and s["status"] == expected and s["errors"]


def test_changed_html():
    with pytest.raises(m.StructureError):
        m.catalogue_links(b"<html>maintenance</html>", m.CSN, True, m.Config())


def test_report_missing_coordinates_and_midnight():
    html = '<table>'+''.join(f'<tr><td>{a}</td><td>{b}</td></tr>' for a,b in [
        ('Referencia','Quintero'), ('Hora UTC','01:00:00 08/10/2026'), ('Magnitud','4.7 Mw'),
        ('Latitud','-33.1'), ('Longitud','-71.2')])+'</table>'
    e = m.leer_evento(html, event()["url"])
    assert e["depth"] is None and m.date(e["origin"]).hour == 1
    with pytest.raises(m.StructureError):
        m.leer_evento(html.replace('-71.2','no disponible'), event()["url"])


def test_pending_zero_source_simulation_no_credentials(tmp_path, monkeypatch):
    c = m.Config(state_dir=str(tmp_path/'state'), output=str(tmp_path/'out'))
    st = m.Store(c); s = m.empty_state('simulation')
    e = event(); e['origin'] = m.iso(m.utcnow()-timedelta(hours=1))
    s['events'][e['id']] = m.record(e, m.utcnow()-timedelta(hours=1)); st.save(s)
    monkeypatch.setattr(m, 'discover', lambda *a: ([], {'status':'valida','errors':[]}))
    monkeypatch.setattr(m, 'tweet_text', lambda e:'text')
    monkeypatch.setattr(m, 'build_video', lambda *a: tmp_path/'mock.mp4')
    monkeypatch.setattr(m, 'XClient', Mock(side_effect=AssertionError('X en simulación')))
    assert m.run(c) == 0
    fresh = m.Store(c).load()
    assert fresh['events'][e['id']]['status'] == 'simulado'


def test_pending_source_failure_is_processed_but_red(tmp_path, monkeypatch):
    c = m.Config(state_dir=str(tmp_path/'state'), output=str(tmp_path/'out'))
    e = event(); e['origin'] = m.iso(m.utcnow()-timedelta(hours=1))
    s = m.empty_state('simulation'); s['events'][e['id']] = m.record(e, m.utcnow()-timedelta(hours=1))
    m.Store(c).save(s)
    monkeypatch.setattr(m, 'discover', lambda *a: ([], {'status':'inaccesible','errors':['HTTP']}))
    monkeypatch.setattr(m, 'tweet_text', lambda e:'text')
    monkeypatch.setattr(m, 'build_video', lambda *a: tmp_path/'mock.mp4')
    assert m.run(c) == 1
    assert m.Store(c).load()['events'][e['id']]['status'] == 'simulado'


def test_published_recent_backoff_expiration():
    c = m.Config(); r = m.record(event(), NOW)
    r.update(status='publicado', tweet_id='42')
    assert not m.eligible(r,c,NOW-timedelta(hours=12),NOW)
    r = m.record(event(30), NOW)
    assert m.defer_recent(r,c,NOW) and m.date(r['next_attempt']) > NOW
    assert not m.eligible(r,c,NOW-timedelta(hours=12),NOW)
    s = m.empty_state('live'); r = m.record(event(50000),NOW); s['events']['385789'] = r
    m.recover(s,NOW-timedelta(hours=12),NOW)
    assert r['status'] == 'expirado'
    r['status'] = 'resultado_incierto'
    m.recover(s,NOW-timedelta(hours=12),NOW)
    assert r['status'] == 'resultado_incierto'


def test_station_isolation_and_none():
    stations = [{'id':'bad','distance':1}, {'id':'good','distance':2}]
    def process(s):
        if s['id']=='bad': raise m.Failure('sin muestras')
        return s
    valid, rejected = m.collect(stations,process,2)
    assert len(valid)==len(rejected)==1
    valid, rejected = m.collect(stations[:1],process,1)
    assert not valid and rejected


def test_no_acceleration_remains_pending(tmp_path, monkeypatch):
    c = m.Config(state_dir=str(tmp_path/'state'),output=str(tmp_path/'out'))
    e = event(); e['origin'] = m.iso(m.utcnow()-timedelta(hours=1))
    monkeypatch.setattr(m,'discover',lambda *a:([e],{'status':'valida'}))
    monkeypatch.setattr(m,'tweet_text',lambda e:'text')
    monkeypatch.setattr(m,'build_video',Mock(side_effect=m.Failure('Sin aceleración')))
    assert m.run(c)==1
    r=m.Store(c).load()['events'][e['id']]
    assert r['status']=='pendiente' and r['attempts']==1 and m.date(r['next_attempt'])>m.utcnow()


def test_raw_gap_nonfinite_saturation():
    tr=Trace(np.sin(np.arange(1000)*.1));tr.stats.sampling_rate=100
    start=tr.stats.starttime;end=tr.stats.endtime
    assert m.check_raw(Stream([tr.copy()]),start,end)
    bad=tr.copy();bad.data[10]=np.nan
    with pytest.raises(m.Failure):m.check_raw(Stream([bad]),start,end)
    bad=tr.copy();bad.data[10:15]=100
    with pytest.raises(m.Failure):m.check_raw(Stream([bad]),start,end)
    a=tr.copy().trim(start,start+4);b=tr.copy().trim(start+5,end)
    with pytest.raises(m.Failure):m.check_raw(Stream([a,b]),start,end)


class MemoryStore:
    def __init__(self, fail=0):
        self.c=m.Config();self.calls=0;self.fail=fail;self.remote=None
    def save(self,s):
        self.calls+=1
        if self.calls==self.fail:raise m.PersistenceError('fallo CAS')
        self.remote=copy.deepcopy(s)


def transaction(fail=0, effect=None):
    store=MemoryStore(fail);s=m.empty_state('live');s['account_id']='123'
    r=m.record(event(),NOW);s['events'][r['event']['id']]=r
    x=Mock();x.create.return_value='456'
    if effect:x.create.side_effect=effect
    return store,s,r,x


def test_persist_before_send():
    st,s,r,x=transaction(fail=1)
    with pytest.raises(m.PersistenceError):m.send_transaction(st,s,r,x,'text','111')
    x.create.assert_not_called()


def test_confirmed_then_persistence_failure_fresh_runner():
    st,s,r,x=transaction(fail=2)
    with pytest.raises(m.PersistenceError):m.send_transaction(st,s,r,x,'text','111')
    assert r['status']=='publicado' and r['tweet_id']=='456'
    fresh=copy.deepcopy(st.remote)
    assert fresh['events']['385789']['status']=='enviando'
    m.recover(fresh,NOW-timedelta(hours=12),NOW)
    assert fresh['events']['385789']['status']=='resultado_incierto'
    assert not m.eligible(fresh['events']['385789'],m.Config(),NOW-timedelta(hours=12),NOW)
    x.create.assert_called_once()


@pytest.mark.parametrize('effect',[m.XError('timeout',uncertain=True), RuntimeError('respuesta extraña')])
def test_uncertain_post(effect):
    st,s,r,x=transaction(effect=effect)
    with pytest.raises(Exception):m.send_transaction(st,s,r,x,'text','111')
    assert st.remote['events']['385789']['status']=='resultado_incierto'
    x.create.assert_called_once()


def test_interruption_after_sending():
    st,s,r,x=transaction(effect=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):m.send_transaction(st,s,r,x,'text','111')
    m.recover(st.remote,NOW-timedelta(hours=12),NOW)
    assert st.remote['events']['385789']['status']=='resultado_incierto'


@pytest.mark.parametrize('status',[401,402,403,429,500])
def test_x_errors(status,monkeypatch):
    for name in m.SECRET_NAMES[:4]:monkeypatch.setenv(name,'fake-secret')
    response=Mock(status_code=status,headers={'x-rate-limit-reset':'1800000000'})
    response.json.return_value={'title':'Rejected','detail':'useful detail','code':99}
    monkeypatch.setattr(requests,'request',Mock(return_value=response))
    x=m.XClient(m.Config(),m.Clock(200))
    with pytest.raises(m.XError) as caught:x.call('POST','/tweets',json={})
    assert caught.value.status==status and '99' in str(caught.value)
    assert caught.value.global_stop
    assert caught.value.uncertain == (status==500)
    requests.request.assert_called_once()


def test_timeout_after_post(monkeypatch):
    for name in m.SECRET_NAMES[:4]:monkeypatch.setenv(name,'fake-secret')
    monkeypatch.setattr(requests,'request',Mock(side_effect=requests.Timeout))
    x=m.XClient(m.Config(),m.Clock(200))
    with pytest.raises(m.XError) as caught:x.create('text','1')
    assert caught.value.uncertain
    requests.request.assert_called_once()


def test_upload_protocol_and_empty_append(tmp_path,monkeypatch):
    for name in m.SECRET_NAMES[:4]:monkeypatch.setenv(name,'fake-secret')
    x=m.XClient(m.Config(),m.Clock(200));video=tmp_path/'v.mp4';video.write_bytes(b'1234')
    x.call=Mock(side_effect=[{'data':{'id':'12'}},{},{'data':{'id':'12'}}])
    assert x.upload(video)=='12'
    assert [a.args[1] for a in x.call.call_args_list]==[
        '/media/upload/initialize','/media/upload/12/append','/media/upload/12/finalize']
    response=Mock(status_code=204,content=b'')
    monkeypatch.setattr(requests,'request',Mock(return_value=response))
    assert m.XClient(m.Config(),m.Clock(200)).call('POST','/media/upload/12/append',empty=True)=={}


def test_corrupt_state_fails_closed(tmp_path):
    c=m.Config(state_dir=str(tmp_path));(tmp_path/'simulation.json').write_text('{}')
    with pytest.raises(m.PersistenceError):m.Store(c).load()


def test_github_cas_and_new_runner(tmp_path,monkeypatch):
    import base64
    monkeypatch.setenv('GITHUB_REPOSITORY','owner/repo');monkeypatch.setenv('GITHUB_TOKEN','fake')
    c=m.Config(state_dir=str(tmp_path));remote={'state':m.empty_state('simulation'),'sha':'one'}
    def api(method,path,**kw):
        if method=='GET':
            return Mock(status_code=200,json=lambda:{'sha':remote['sha'],'content':base64.b64encode(json.dumps(remote['state']).encode()).decode()})
        assert kw['json']['sha']==remote['sha']
        remote['state']=json.loads(base64.b64decode(kw['json']['content']));remote['sha']='b'*40
        return Mock(status_code=200,json=lambda:{'commit':{'sha':'c'*40},'content':{'sha':'b'*40}})
    a=m.Store(c);a.api=api;s=a.load();s['events']['385789']=m.record(event(),NOW);a.save(s)
    b=m.Store(c);b.api=api;assert b.load()==s and b.sha=='b'*40
    b.api=Mock(return_value=Mock(status_code=409))
    with pytest.raises(m.PersistenceError):b.save(s)


def test_boolean_timezone_long_text(monkeypatch):
    monkeypatch.setenv('PUBLISH_TO_X','false');assert not m.Config.env().publish
    e=event();e['origin']='2026-07-01T03:00:00Z';e['depth']=None;e['reference']='Quintero '*100
    text=m.tweet_text(e)
    assert '30/06/2026 23:00:00' in text and 'Profundidad: no informada.' in text
    assert text.endswith(e['url']) and 'lat -33.1000°' in text
    e['origin']='2026-01-01T03:00:00Z'
    assert '01/01/2026 00:00:00' in m.tweet_text(e)


def test_successful_transaction():
    st,s,r,x=transaction()
    assert m.send_transaction(st,s,r,x,'text','111')=='456'
    assert st.calls==2 and st.remote['events']['385789']['status']=='publicado'


def test_partial_catalogue(monkeypatch):
    monkeypatch.setattr(m,'obtener',Mock(side_effect=[cat(),m.SourceError('HTTP 503')]))
    es,source=m.discover(m.Config(),m.Clock(50),NOW-timedelta(hours=1),NOW)
    assert not es and source['status']=='parcial'


def test_reconciliation_only_positive(monkeypatch):
    for name in m.SECRET_NAMES[:4]:monkeypatch.setenv(name,'fake-secret')
    x=m.XClient(m.Config(),m.Clock(200));s=m.empty_state('live');s['account_id']='123'
    r=m.record(event(),NOW);r.update(status='resultado_incierto',text='text',media_id='111',sent_at=m.iso(NOW))
    s['events']['385789']=r
    x.call=Mock(return_value={'data':[]})
    x.reconcile(s);assert r['status']=='resultado_incierto'
    x.call=Mock(return_value={'data':[{'id':'456','text':'text','created_at':m.iso(NOW),
                                      'attachments':{'media_keys':['13_111']}}]})
    x.reconcile(s);assert r['status']=='publicado' and r['tweet_id']=='456'


def test_horizontal_peak_uses_both_components_and_percent_g():
    import numpy as np
    t = np.array([-.5, -.25, 0, .25])
    s = {'components': [{'times': t, 'values': np.array([1., -2., 0, 1.])},
                        {'times': t, 'values': np.array([3., -4., 2., 0])}]}
    assert m.station_peaks(s, np.array([0.]), 1)[0] == pytest.approx(400/9.80665)
    s['components'][1]['times'] = t+10
    with pytest.raises(m.Failure, match='incompleto'):
        m.station_peaks(s, np.array([0.]), 1)


def test_area_interpolation_support_and_constant_field():
    import numpy as np
    e = event()
    e.update(lat=0., lon=0.)
    x, y = np.meshgrid(np.linspace(-.1, 1.1, 20), np.linspace(-.1, 1.1, 20))
    c = m.Config(support=200, triangle=200)
    w, mask, reason = m.spatial_weights([0, 1, 0], [0, 0, 1], x, y, e, c)
    assert mask.any()
    assert np.allclose(w[mask] @ np.array([2., 2., 2.]), 2.)
    assert not mask.reshape(x.shape)[-1, -1]
    for lons, lats in [([0, 1], [0, 0]), ([0, .5, 1], [0, 0, 0])]:
        _, mask, _ = m.spatial_weights(lons, lats, x, y, e, c)
        assert not mask.any()
    _, mask, _ = m.spatial_weights([0, 1, 0], [0, 0, 1], x, y, e, m.Config(support=1))
    assert not mask.any()


def test_decimal_lookback(monkeypatch):
    monkeypatch.setenv('LOOKBACK_HOURS', '0.1666667')
    assert m.Config.env().lookback == pytest.approx(.1666667)


@pytest.mark.parametrize("scenario", ["ok", "transient", "mismatch", "forbidden", "bad_json", "unavailable"])
def test_commit_confirmation(tmp_path, monkeypatch, scenario):
    import base64
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setenv('GITHUB_TOKEN', 'fake')
    monkeypatch.setattr(m.time, 'sleep', lambda _: None)
    store = m.Store(m.Config(state_dir=str(tmp_path)))
    state = m.empty_state('simulation')
    calls = []
    def api(method, path, **kw):
        calls.append(method)
        if method == 'PUT':
            return Mock(status_code=200, json=lambda: {
                'commit': {'sha': 'c'*40}, 'content': {'sha': 'b'*40}})
        assert kw['params']['ref'] == 'c'*40
        if scenario == 'transient' and calls.count('GET') == 1:
            return Mock(status_code=404)
        if scenario in ('forbidden', 'unavailable'):
            return Mock(status_code=403 if scenario == 'forbidden' else 503)
        if scenario == 'bad_json':
            return Mock(status_code=200, json=Mock(side_effect=ValueError()))
        actual = copy.deepcopy(state)
        if scenario == 'mismatch':
            actual['revision'] = 'another-revision'
        return Mock(status_code=200, json=lambda: {
            'sha': 'b'*40, 'content': base64.b64encode(json.dumps(actual).encode()).decode()})
    store.api = api
    if scenario in ('ok', 'transient'):
        store.save(state)
        assert store.sha == 'b'*40
    else:
        with pytest.raises(m.PersistenceError):
            store.save(state)
    assert calls.count('PUT') == 1
    assert calls.count('GET') <= 3
