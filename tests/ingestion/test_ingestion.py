"""Відкриті контрольні приклади. Невеликі fixtures — тільки для тестів, не нові дані CityFlow."""
import csv,hashlib,io,json,zipfile
from unittest.mock import Mock
from pathlib import Path
import pytest
import requests
import student
from support import CSV_COLUMNS,prepare_records


def response(code=200,body=None,headers=None):
    r=requests.Response();r.status_code=code
    r._content=json.dumps(body if body is not None else {}).encode('utf-8')
    r.headers.update(headers or {})
    r.url='http://localhost/api/v1/delivery-events'
    return r


def envelope(events,next_cursor=None,total=None,**changes):
    d={'student_id':'S01','schema_version':1,'data':events,'count':len(events),
       'total_count':len(events) if total is None else total,'next_cursor':next_cursor}
    d.update(changes);return d


def package(tmp_path,mode='valid'):
    payload={}
    for source,cols in CSV_COLUMNS.items():
        out=io.StringIO(newline='');writer=csv.writer(out)
        writer.writerow(cols if mode!='header' or source!='orders' else cols[::-1])
        writer.writerow(['1']*len(cols))
        payload[source+'.csv']=out.getvalue().encode('utf-8')
    payload['README.txt']=b'public test fixture\n'
    manifest={'student_id':'S01','files':[{'path':p,'size_bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for p,b in payload.items()]}
    if mode=='missing':del payload['order_items.csv']
    if mode=='checksum':payload['README.txt']=b'edited\n'
    p=tmp_path/'source.zip'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('PACKAGE_INFO.json',json.dumps(manifest))
        for name,blob in payload.items():z.writestr(name,blob)
    return p


def test_file_valid_preserves_strings(tmp_path):
    p=package(tmp_path);before=p.read_bytes()
    result=student.read_file_batch(p,'S01')
    assert set(result)=={'orders','order_items'}
    assert len(result['orders'])==len(result['order_items'])==1
    assert all(isinstance(v,str) for v in result['orders'][0].values())
    assert p.read_bytes()==before


@pytest.mark.parametrize('mode',['missing','checksum','header'])
def test_file_rejects_structural_problem(tmp_path,mode):
    with pytest.raises(ValueError):student.read_file_batch(package(tmp_path,mode),'S01')


def test_file_rejects_other_student(tmp_path):
    with pytest.raises(ValueError):student.read_file_batch(package(tmp_path),'S02')


def test_retry_server_error():
    s=Mock();s.get.side_effect=[response(500),response(200)];sleep=Mock()
    assert student.request_page(s,'http://localhost',params={},headers={},sleep=sleep).status_code==200
    assert s.get.call_count==2;sleep.assert_called_once_with(1)
    assert s.get.call_args.kwargs['timeout']==(3,10)
    assert s.get.call_args.kwargs['allow_redirects'] is False


def test_retry_after():
    s=Mock();s.get.side_effect=[response(429,headers={'Retry-After':'3'}),response(200)];sleep=Mock()
    student.request_page(s,'http://localhost',params={},headers={},sleep=sleep)
    assert s.get.call_count==2
    assert sleep.call_args.args[0]>=3


@pytest.mark.parametrize('code',[400,401,403,404,422,301])
def test_no_retry_permanent_or_redirect(code):
    s=Mock();s.get.return_value=response(code);sleep=Mock()
    with pytest.raises(Exception):student.request_page(s,'http://localhost',params={},headers={},sleep=sleep)
    assert s.get.call_count==1;sleep.assert_not_called()


def test_timeout_then_success():
    s=Mock();s.get.side_effect=[requests.Timeout('test timeout'),response(200)];sleep=Mock()
    student.request_page(s,'http://localhost',params={},headers={},sleep=sleep)
    assert s.get.call_count==2;sleep.assert_called_once_with(1)


def test_four_failed_attempts():
    s=Mock();s.get.return_value=response(503);sleep=Mock()
    with pytest.raises(Exception):student.request_page(s,'http://localhost',params={},headers={},sleep=sleep)
    assert s.get.call_count==4
    assert [c.args[0] for c in sleep.call_args_list]==[1,2,4]


def test_tls_error_not_retried():
    s=Mock();s.get.side_effect=requests.exceptions.SSLError('test certificate error');sleep=Mock()
    with pytest.raises(requests.exceptions.SSLError):student.request_page(s,'https://localhost',params={},headers={},sleep=sleep)
    assert s.get.call_count==1;sleep.assert_not_called()


def test_pagination_follows_cursor_not_page_size(tmp_path):
    first={'event_id':'E1','extra':{'test':True}};second={'event_id':'E2'}
    fn=Mock(side_effect=[response(body=envelope([first,second],'opaque-next',3)),response(body=envelope([second],None,3))])
    params={'limit':100}
    result=student.fetch_pages(Mock(),'http://localhost','TEST_KEY','S01',params,tmp_path,request_fn=fn)
    assert result=={'events':[first,second,second],'page_count':2,'total_count':3}
    assert fn.call_args_list[1].kwargs['params']['cursor']=='opaque-next'
    assert fn.call_args_list[0].kwargs['headers']=={'X-API-Key':'TEST_KEY'}
    assert params=={'limit':100}
    assert (tmp_path/'page_0001.json').exists() and (tmp_path/'page_0002.json').exists()
    assert 'TEST_KEY' not in (tmp_path/'page_0001.meta.json').read_text()


@pytest.mark.parametrize('change',[{'student_id':'S02'},{'count':999},{'data':{}},{'total_count':3},{'schema_version':3}])
def test_pagination_detects_invalid_envelope(tmp_path,change):
    fn=Mock(return_value=response(body=envelope([{'event_id':'E1'}],**change)))
    with pytest.raises(Exception):student.fetch_pages(Mock(),'http://localhost','k','S01',{'limit':100},tmp_path,request_fn=fn)
    assert (tmp_path/'page_0001.json').exists()


def test_repeated_cursor_stops(tmp_path):
    fn=Mock(return_value=response(body=envelope([{'event_id':'E1'}],'same',100)))
    with pytest.raises(Exception):student.fetch_pages(Mock(),'http://localhost','k','S01',{'limit':100},tmp_path,request_fn=fn)
    assert fn.call_count<=2


def test_changed_total_stops(tmp_path):
    fn=Mock(side_effect=[response(body=envelope([{'event_id':'E1'}],'c1',2)),response(body=envelope([{'event_id':'E2'}],None,3))])
    with pytest.raises(Exception):student.fetch_pages(Mock(),'http://localhost','k','S01',{'limit':100},tmp_path,request_fn=fn)


def test_partial_api_failure_is_not_empty_success(tmp_path):
    fn=Mock(side_effect=[response(body=envelope([{'event_id':'E1'}],'c1',2)),requests.HTTPError('failed')])
    with pytest.raises(Exception):student.fetch_pages(Mock(),'http://localhost','k','S01',{'limit':100},tmp_path,request_fn=fn)
    assert (tmp_path/'page_0001.json').exists()


def test_deduplication_and_conflict(tmp_path):
    event={'event_id':'E1','event_type':'delivered'}
    records,stats=prepare_records('S01',{'delivery_events':[event,dict(event)]},tmp_path)
    assert len(records)==1 and stats['delivery_events']['duplicates_in_input']==1
    with pytest.raises(ValueError):
        prepare_records('S01',{'delivery_events':[event,dict(event,event_type='cancelled')]},tmp_path)
