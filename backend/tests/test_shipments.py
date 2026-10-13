from datetime import timedelta
from io import BytesIO
from zipfile import ZipFile
from xml.sax.saxutils import escape

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app import config, qq_bridge, simple_ai, handoff_summary
from app.main import app
from app.models import Shipment, ShipmentImport, Conversation, Message, AiReply, now
from app.shipments import parse_excel, lookup_reply
from app.conversations import add_message
from test_handoff import merchant, visitor, payload
from test_qq import event


def excel(rows):
    # Minimal synthetic fixture; never copies the customer's personal data.
    output = BytesIO()
    with ZipFile(output, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        body = []
        for i, row in enumerate(rows, 1):
            cells = ''.join('<c r="' + chr(65+j) + str(i) + '" t="inlineStr"><is><t>' + escape(str(v)) + '</t></is></c>' for j,v in enumerate(row))
            body.append('<row r="' + str(i) + '">' + cells + '</row>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + ''.join(body) + '</sheetData></worksheet>')
    return output.getvalue()


def upload(client, rows):
    return client.post('/api/merchant/shipments/preview', content=excel(rows), headers={'Content-Type': 'application/octet-stream'})


def test_preview_confirm_upsert_and_keep_old(database):
    m = merchant()
    rows = [['快递单号','手机','固话','收件人','地址'],['SF000001','','13800000001','测试收件人','测试地址'],['SF000002','13900000002','','其他人','其他地址']]
    p = upload(m, rows)
    assert p.status_code == 200
    assert p.json()['total'] == 2 and p.json()['new'] == 2
    assert p.json()['sample'][0]['phone'] == '138****0001'
    assert '测试地址' not in p.text
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Shipment)) == 0
        stored = db.scalar(select(ShipmentImport)).payload
        assert '测试地址' not in stored and '测试收件人' not in stored
    first = m.post('/api/merchant/shipments/confirm', json={'import_id': p.json()['import_id']})
    assert first.status_code == 200
    assert m.post('/api/merchant/shipments/confirm', json={'import_id': p.json()['import_id']}).json() == first.json()
    second = upload(m, [rows[0],['SF000001','13700000003','','','']]).json()
    assert second['updated'] == 1 and second['new'] == 0
    assert m.post('/api/merchant/shipments/confirm', json={'import_id': second['import_id']}).status_code == 200
    with database() as db:
        assert db.get(Shipment, 'SF000001').phone == '13700000003'
        assert db.get(Shipment, 'SF000002').phone == '13900000002'
        assert db.scalar(select(ShipmentImport.payload).where(ShipmentImport.id == p.json()['import_id'])) == '[]'


@pytest.mark.parametrize('rows', [
    [['快递单号','手机'],['SF000001','13800000001'],['SF000001','13900000002']],
    [['快递单号','手机','固话'],['SF000001','13800000001','13900000002']],
    [['快递单号','手机'],['SF000001','0000']],
    [['快递单号','手机'],['','13800000001']],
    [['其他列','手机'],['SF000001','13800000001']],
])
def test_invalid_import_rejected_as_whole(database, rows):
    assert upload(merchant(), rows).status_code == 400
    with database() as db:
        assert db.scalar(select(func.count()).select_from(ShipmentImport)) == 0
        assert db.scalar(select(func.count()).select_from(Shipment)) == 0


def test_import_auth_csrf_and_expiration(database):
    assert TestClient(app).post('/api/merchant/shipments/preview', content=b'bad').status_code == 401
    m = merchant()
    rows = [['快递单号','手机'],['SF000001','13800000001']]
    p = upload(m, rows).json()
    with database() as db:
        db.get(ShipmentImport,p['import_id']).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert m.post('/api/merchant/shipments/confirm',json={'import_id':p['import_id']}).status_code == 409
    m.headers['x-csrf-token'] = 'wrong'
    assert upload(m,rows).status_code == 403


def test_phone_lookup_multiple_records_no_private_fields(database):
    with database() as db:
        db.add_all([Shipment(tracking_number='SF000001',phone='13800000001'),Shipment(tracking_number='SF000002',phone='13800000001')])
        db.commit()
        answer = lookup_reply(db,'13800000001')
        assert '2 条' in answer and 'SF000001' in answer and 'SF000002' in answer
        assert '13800000001' not in answer and '实时运输状态' in answer
        assert '私聊' in lookup_reply(db,'13800000001',group=True)
        assert 'SF000001' not in lookup_reply(db,'13800000001',group=True)
        assert '没有匹配' in lookup_reply(db,'13900000002')
        assert '11位' in lookup_reply(db,'查物流')
        assert lookup_reply(db,'这是订单号991380000000199') is None


def test_web_and_qq_lookup_bypass_model_and_human_pause(database, monkeypatch):
    monkeypatch.setattr(config,'AI_WEB_MODE','mock')
    monkeypatch.setattr(qq_bridge,'SessionLocal',database)
    monkeypatch.setattr(simple_ai,'generate',lambda *_: pytest.fail('phone lookup must not call AI'))
    with database() as db:
        db.add(Shipment(tracking_number='SF000001',phone='13800000001'))
        db.commit()
    v,cid = visitor()
    assert v.post('/api/visitor/messages',json=payload('13800000001')).status_code == 200
    assert 'SF000001' in v.get('/api/visitor/conversation').json()['messages'][-1]['content']
    result = qq_bridge.ingest('app',*event(text='13800000001'))
    assert result[0] is None
    v.post('/api/visitor/handoff')
    v.post('/api/visitor/messages',json=payload('13800000001'))
    assert v.get('/api/visitor/conversation').json()['messages'][-1]['role'] == 'user'
    with database() as db:
        assert db.scalar(select(AiReply)) is None


def test_phone_is_hidden_from_later_ai_and_summary(database,monkeypatch):
    with database() as db:
        c=Conversation()
        db.add(c);db.flush()
        add_message(db,c,'user','手机号13800000001，想核实一下')
        db.commit()
        assert '13800000001' not in str(simple_ai.round_messages(db,c))
    monkeypatch.setattr(config,'AI_WEB_MODE','deepseek')
    def call(system,messages,*args,**kwargs):
        assert '13800000001' not in str(messages)
        return '{"request":[],"known":[],"attempted":[],"materials":[]}'
    monkeypatch.setattr(simple_ai,'call_model',call)
    handoff_summary.generate_summary([{'sequence':1,'role':'user','content':'手机号13800000001，伞坏了'}],'test')


def test_invalid_zip_and_size():
    for data in (b'not excel', b'x' * (5 * 1024 * 1024 + 1)):
        with pytest.raises(HTTPException):
            parse_excel(data)
