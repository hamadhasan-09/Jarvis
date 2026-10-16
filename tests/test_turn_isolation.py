from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import pytest

from backend.request_coordinator import RequestCoordinator
from security_v2.browser_auth import SESSION_KEY


@pytest.fixture
def s(isolated_auth_server):
    server = isolated_auth_server
    server.request_coordinator = RequestCoordinator()
    server.VOICE_LOCAL_TOKEN = "synthetic-stage2-token"
    return server


def browser(s, http=None):
    if http is None:
        http = s.app.test_client()
        token = s.browser_auth.issue_owner_pairing_token()
        assert http.post('/auth/pair', json={'token': token}, headers={'X-Nexus-Browser-Auth': '1'}).status_code == 200
    return s.socketio.test_client(s.app, flask_test_client=http), http


def voice(s):
    return s.socketio.test_client(s.app, auth={
        'role': 'local_voice', 'token': s.VOICE_LOCAL_TOKEN, 'session_id': s.SESSION_ID,
    })


def sid(s, client):
    return s.socketio.server.manager.sid_from_eio_sid(client.eio_sid, '/')


def control(s, client, request_id):
    return s._voice_request_controls[(sid(s, client), request_id)]


def speech(client, request_id, **fields):
    client.emit('user_speech', {'text': 'synthetic request', 'request_id': request_id, **fields})


def current(s, record):
    return s.is_turn_current(record['control_scope'], record['control_turn_id'])


@pytest.mark.parametrize('same_credential', [False, True])
def test_browser_tabs_do_not_cancel_each_other_or_collide_on_request_id(s, same_credential):
    a, http = browser(s)
    b, _ = browser(s, http if same_credential else None)
    a.get_received(); b.get_received()
    speech(a, 'same-id')
    first = control(s, a, 'same-id')
    speech(b, 'same-id')
    second = control(s, b, 'same-id')
    assert first is not second
    assert first['control_scope'] != second['control_scope']
    assert current(s, first) and current(s, second)
    assert not first['cancel_event'].is_set() and not second['cancel_event'].is_set()
    assert s._connection_sessions[sid(s, a)] == s._connection_sessions[sid(s, b)] == s.SESSION_ID
    # Browser replies stay with their own SID even with identical wire IDs.
    assert len([p for p in a.get_received() if p['name'] == 'jarvis_reply']) == 1
    assert len([p for p in b.get_received() if p['name'] == 'jarvis_reply']) == 1
    b.emit('voice_cancel', {'request_id': 'same-id'})
    assert not first['cancel_event'].is_set() and current(s, first)
    a.emit('voice_playback_done', {'request_id': 'same-id'})
    assert not current(s, first)
    assert all(call.args[0] == s.SESSION_ID for call in s.memory.save_message.call_args_list)
    assert all(call.kwargs['session_id'] == s.SESSION_ID for call in s.memory.get_recent.call_args_list)


def test_guest_a_and_b_cannot_register_cancel_or_finish_trusted_turn(s):
    owner, _ = browser(s)
    speech(owner, 'private')
    active = control(s, owner, 'private')
    calls = s.provider_manager.stream_generate.call_count
    for guest in (s.socketio.test_client(s.app), s.socketio.test_client(s.app)):
        guest.get_received()
        speech(guest, 'private', session_id=s.SESSION_ID, source='local_voice', turn_id=active['turn_id'], role='owner')
        guest.emit('voice_cancel', {'request_id': 'private', 'sid': sid(s, owner), 'turn_id': active['turn_id']})
        guest.emit('voice_playback_done', {'request_id': 'private', 'sid': sid(s, owner)})
        assert all(p['name'] != 'jarvis_reply' for p in guest.get_received())
        guest.disconnect()
    assert not active['cancel_event'].is_set() and current(s, active)
    assert s.provider_manager.stream_generate.call_count == calls


@pytest.mark.parametrize('owner_role', ['browser', 'local_voice'])
def test_same_client_new_request_interrupts_active_generation(s, owner_role):
    client = voice(s) if owner_role == 'local_voice' else browser(s)[0]
    started = threading.Event()
    cancelled = threading.Event()

    def stream(_route, messages, **kwargs):
        if messages[-1]['content'] == 'first':
            started.set()
            yield 'A generated prefix.'
            assert kwargs['cancel_event'].wait(4)
            cancelled.set()
        else:
            yield {'__done__': True, 'provider': 'mock', 'full_text': 'second complete'}

    s.provider_manager.stream_generate.side_effect = stream
    with ThreadPoolExecutor(max_workers=1) as workers:
        first_worker = workers.submit(speech, client, 'first', text='first', turn_id='reused-wire-turn')
        assert started.wait(2)
        old = control(s, client, 'first')
        speech(client, 'second', text='second', turn_id='reused-wire-turn')
        first_worker.result(timeout=2)
    assert cancelled.is_set() and old['cancel_event'].is_set()
    assert (sid(s, client), 'first') not in s._voice_request_controls
    if owner_role == 'browser':
        new = control(s, client, 'second')
        assert current(s, new) and new['control_turn_id'] != old['control_turn_id']
    assert any(p['name'] == 'jarvis_reply' and p['args'][0].get('text') == 'second complete' for p in client.get_received())


@pytest.mark.parametrize('owner_role,other_role', [
    ('browser', 'browser'), ('browser', 'local_voice'),
    ('local_voice', 'browser'), ('local_voice', 'local_voice'),
])
def test_voice_and_browser_work_concurrently_without_cross_cancellation(s, owner_role, other_role):
    a = voice(s) if owner_role == 'local_voice' else browser(s)[0]
    b = voice(s) if other_role == 'local_voice' else browser(s)[0]
    started = threading.Event()
    release = threading.Event()

    def stream(_route, messages, **kwargs):
        if messages[-1]['content'] == 'held':
            started.set()
            assert release.wait(4)
            assert not kwargs['cancel_event'].is_set()
        yield {'__done__': True, 'full_text': 'complete', 'provider': 'mock'}

    s.provider_manager.stream_generate.side_effect = stream
    with ThreadPoolExecutor(max_workers=1) as workers:
        running = workers.submit(speech, a, 'held', text='held')
        try:
            assert started.wait(2)
            record = control(s, a, 'held')
            speech(b, 'other', session_id=record['control_scope'], source='local_voice', turn_id=record['control_turn_id'])
            assert not record['cancel_event'].is_set() and current(s, record)
        finally:
            release.set()
        running.result(timeout=2)


@pytest.mark.parametrize('event', ['voice_cancel', 'voice_playback_done'])
def test_spoofed_ids_and_turns_cannot_control_another_socket(s, event):
    a, _ = browser(s)
    b, _ = browser(s)
    speech(a, 'a')
    active = control(s, a, 'a')
    b.emit(event, {'request_id': 'a', 'sid': sid(s, a), 'session_id': active['control_scope'],
                   'turn_id': active['turn_id'], 'source': 'local_voice', 'role': 'local_voice'})
    a.emit(event, {'request_id': 'a', 'turn_id': 'wrong-turn'})
    assert not active['cancel_event'].is_set() and not active['playback_done']
    assert current(s, active)
    # Client wire turn/session/role claims never become the backend identity.
    speech(b, 'b', turn_id=active['control_turn_id'], session_id=active['control_scope'], source='local_voice', role='local_voice')
    other = control(s, b, 'b')
    assert other['turn_id'] != active['control_turn_id']
    assert other['control_scope'] != active['control_scope'] and current(s, active)


def test_replay_rejected_and_reconnection_does_not_revive_old_controls(s):
    a, http = browser(s)
    b, _ = browser(s)
    old_sid = sid(s, a)
    speech(a, 'replay')
    first = control(s, a, 'replay')
    speech(b, 'b')
    other = control(s, b, 'b')
    calls = s.provider_manager.stream_generate.call_count
    speech(a, 'replay')
    assert control(s, a, 'replay') is first and not first['cancel_event'].is_set()
    assert s.provider_manager.stream_generate.call_count == calls
    a.emit('voice_playback_done', {'request_id': 'replay'})
    speech(a, 'replay')
    assert s.provider_manager.stream_generate.call_count == calls
    a.disconnect()
    assert old_sid not in s._connection_control_scopes
    assert old_sid not in s._connection_request_ids
    reconnected, _ = browser(s, http)
    assert sid(s, reconnected) != old_sid
    assert s._connection_control_scopes[sid(s, reconnected)] != first['control_scope']
    reconnected.emit('voice_cancel', {'request_id': 'b', 'sid': sid(s, b)})
    assert not other['cancel_event'].is_set() and current(s, other)
    speech(reconnected, 'replay')  # A new connection has a fresh ID namespace.
    assert (sid(s, reconnected), 'replay') in s._voice_request_controls


@pytest.mark.parametrize('reason', ['disconnect', 'revoke', 'expire'])
def test_active_turn_cleanup_only_affects_its_own_connection(s, reason):
    a, http = browser(s)
    b, _ = browser(s)
    speech(a, 'a')
    speech(b, 'b')
    first, other = control(s, a, 'a'), control(s, b, 'b')
    a_sid = sid(s, a)
    if reason == 'disconnect':
        a.disconnect()
    elif reason == 'revoke':
        assert http.post('/auth/revoke', headers={'X-Nexus-Browser-Auth': '1'}).status_code == 200
    else:
        # Expire only A; independent grant B remains valid.
        with http.session_transaction() as cookie:
            cid = cookie[SESSION_KEY]
        s.browser_auth._credentials[cid].record = replace(s.browser_auth._credentials[cid].record, expires_at=s.now[0])
        s.browser_auth.expire_and_notify()
    assert first['cancel_event'].is_set() and not current(s, first)
    assert not other['cancel_event'].is_set() and current(s, other)
    assert all(record.get('sid') != a_sid for record in s._voice_request_controls.values())
    b.emit('voice_playback_done', {'request_id': 'b'})


def test_voice_wire_turn_and_primary_registry_are_not_modified_by_browser(s):
    local = voice(s)
    bridge_turn = s.begin_turn(s.SESSION_ID)
    speech(local, 'voice', turn_id=bridge_turn, session_id='forged')
    assert s.is_turn_current(s.SESSION_ID, bridge_turn)
    a, _ = browser(s)
    speech(a, 'browser', turn_id=bridge_turn, session_id=s.SESSION_ID, source='local_voice')
    assert s.is_turn_current(s.SESSION_ID, bridge_turn)
    replies = [p['args'][0] for p in local.get_received() if p['name'] == 'jarvis_reply']
    assert replies[-1]['turn_id'] == bridge_turn and replies[-1]['session_id'] == s.SESSION_ID
    assert all(call.args[0] == s.SESSION_ID for call in s.memory.save_message.call_args_list)
    s.end_turn(s.SESSION_ID, bridge_turn)


def test_voice_handshake_session_cannot_target_browser_control_scope(s):
    a, _ = browser(s)
    speech(a, 'a')
    active = control(s, a, 'a')
    attacker = s.socketio.test_client(s.app, auth={'role': 'local_voice',
        'token': s.VOICE_LOCAL_TOKEN, 'session_id': active['control_scope']})
    speech(attacker, 'spoof', turn_id=active['control_turn_id'], source='browser')
    assert current(s, active) and not active['cancel_event'].is_set()
    assert s._connection_control_scopes[sid(s, attacker)] != active['control_scope']
    assert all(call.args[0] == s.SESSION_ID for call in s.memory.save_message.call_args_list)


@pytest.mark.parametrize('reason', ['disconnect', 'revoke', 'expire'])
def test_disconnect_and_auth_invalidation_cancel_running_generation(s, reason):
    a, http = browser(s)
    b, _ = browser(s)
    speech(b, 'independent')
    independent = control(s, b, 'independent')
    started = threading.Event()
    stopped = threading.Event()

    def stream(_route, _messages, **kwargs):
        started.set()
        assert kwargs['cancel_event'].wait(4)
        stopped.set()
        return
        yield  # generator contract, no real provider

    s.provider_manager.stream_generate.side_effect = stream
    a_sid = sid(s, a)
    with ThreadPoolExecutor(max_workers=1) as workers:
        worker = workers.submit(speech, a, 'running')
        assert started.wait(2)
        active = control(s, a, 'running')
        if reason == 'disconnect':
            a.disconnect()
        elif reason == 'revoke':
            assert http.post('/auth/revoke', headers={'X-Nexus-Browser-Auth': '1'}).status_code == 200
        else:
            with http.session_transaction() as cookie:
                cid = cookie[SESSION_KEY]
            s.browser_auth._credentials[cid].record = replace(s.browser_auth._credentials[cid].record, expires_at=s.now[0])
            s.browser_auth.expire_and_notify()
        worker.result(timeout=2)
    assert stopped.is_set() and active['cancel_event'].is_set()
    assert not current(s, active)
    assert not independent['cancel_event'].is_set() and current(s, independent)
    assert all(record.get('sid') != a_sid for record in s._voice_request_controls.values())
