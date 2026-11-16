import subprocess
import sys
import uuid
from datetime import timedelta

import pytest
import requests

from backend.central.app.services import edge_events
from backend.edge.app.core.db import engine, session_scope
from backend.edge.app.models import Outbox
from backend.shared.db import utcnow


def enqueue(system, kind):
    with session_scope() as db:
        system.uplink.emit(db, kind, {'sample': 1})
        db.flush()
        return db.query(Outbox).filter(Outbox.type == kind).order_by(Outbox.id.desc()).first().event_id


def test_offline_queue_is_durable_and_replayed_without_duplicates(system, monkeypatch):
    kind = 'test.offline'
    calls = []
    monkeypatch.setitem(edge_events.HANDLERS, kind, lambda *args: calls.append(1))
    event_id = enqueue(system, kind)
    original = system.net.post
    system.net.down = True
    try:
        with pytest.raises(requests.RequestException):
            system.uplink.flush_once()
        queued = system.uplink.backlog()
        assert queued >= 1
        # A new process sees the queue on disk, independent of API/worker memory.
        output = subprocess.check_output([sys.executable, '-c',
            'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("select count(*) from outbox where event_id=?", (sys.argv[2],)).fetchone()[0])',
            engine.url.database, event_id], timeout=10)
        assert int(output) == 1
    finally:
        system.net.down = False

    def lost_response(*args, **kwargs):
        original(*args, **kwargs)  # central committed, but the response was lost
        raise requests.ConnectionError('response lost')

    monkeypatch.setattr(system.net, 'post', lost_response)
    with pytest.raises(requests.ConnectionError):
        system.uplink.flush_once()
    with session_scope() as db:
        assert db.query(Outbox).filter_by(event_id=event_id).count() == 1
    assert calls == [1]
    monkeypatch.setattr(system.net, 'post', original)
    while system.uplink.flush_once():
        pass
    assert system.uplink.backlog() == 0
    assert calls == [1]  # retransmission ACKed without running the handler twice


def test_http_success_without_ack_keeps_local_data(system, monkeypatch):
    enqueue(system, 'device.event')
    class NoAck:
        def raise_for_status(self): pass
        def json(self): return {'results': {}}
    monkeypatch.setattr(system.net, 'post', lambda *a, **kw: NoAck())
    with pytest.raises(requests.RequestException):
        system.uplink.flush_once()
    assert system.uplink.backlog() >= 1


def test_central_processing_failure_is_not_acknowledged_and_can_retry(system, monkeypatch):
    kind = 'test.retry'
    def fail(*args): raise RuntimeError('temporary processing failure')
    monkeypatch.setitem(edge_events.HANDLERS, kind, fail)
    enqueue(system, kind)
    with pytest.raises(requests.RequestException):
        system.uplink.flush_once()
    calls = []
    monkeypatch.setitem(edge_events.HANDLERS, kind, lambda *args: calls.append(1))
    while system.uplink.flush_once(): pass
    assert calls == [1]
    assert system.uplink.backlog() == 0


def test_expired_live_indicator_is_dropped_and_sync_requires_login(system):
    with session_scope() as db:
        db.add(Outbox(event_id=uuid.uuid4().hex, type='checkin.seen', data={},
                      expires_at=utcnow() - timedelta(seconds=1)))
    assert system.uplink.flush_once() is False
    assert system.uplink.backlog() == 0
    assert system.edge.post('/edge/v1/sync/retry').status_code == 401
    assert system.edge.post('/edge/v1/sync/retry', headers=system.console).status_code == 202
    state = system.edge.get('/edge/v1/status', headers=system.console).json()['central']
    assert state['queue']['persistent'] and state['queue']['pending'] == 0


def test_delayed_checkin_corrects_automatic_absence_after_session_closed(system):
    from backend.central.app.core.db import session_scope as central_db
    from backend.central.app.models import CheckinRecord, ExamSession
    with central_db() as db:
        record = db.query(CheckinRecord).filter_by(status='absent', method='auto').first()
        assert record is not None
        exam = db.get(ExamSession, record.exam_id)
        assert exam.status == 'closed'
        when = exam.started_at + (exam.ended_at - exam.started_at) / 2
        record_id = record.id
        data = {'exam_id': exam.id, 'student_id': record.student_id, 'ts': when.isoformat(), 'confidence': .8}
    with session_scope() as db:
        system.uplink.emit(db, 'checkin.admitted', data)
    while system.uplink.flush_once(): pass
    with central_db() as db:
        record = db.get(CheckinRecord, record_id)
        assert record.status == 'admitted' and record.confidence == .8
