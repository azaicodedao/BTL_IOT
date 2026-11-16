from unittest.mock import Mock

from backend.edge.app.services.camera_sources import CameraSources
from backend.edge.app.routers import console


def test_camera_sources_require_console_login(system):
    assert system.edge.get('/edge/v1/camera-sources').status_code == 401
    assert system.edge.post('/edge/v1/camera-sources', json={
        'name': 'Phone', 'url': 'http://camera.local/video'}).status_code == 401


def test_source_persists_identity_and_can_stop_restart(system, tmp_path, monkeypatch):
    manager = CameraSources(tmp_path / 'sources.json')
    monkeypatch.setattr(console, 'camera_sources', manager)
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr('backend.edge.app.services.camera_sources.subprocess.Popen', popen)
    headers = system.console
    url = '/edge/v1/camera-sources'
    result = system.edge.post(url, headers=headers, json={'name': 'Phone', 'url': 'http://camera.local/video'})
    assert result.status_code == 201, result.text
    source = result.json()
    assert source['running'] and source['hardware_uid'] == 'virt:' + source['id']
    args = popen.call_args.args[0]
    assert args[args.index('--name') + 1] == source['id']
    assert args[args.index('--url') + 1] == source['url']
    assert system.edge.post(url, headers=headers, json={'name': 'Duplicate', 'url': source['url']}).status_code == 409
    path = url + '/' + source['id']
    changed = system.edge.put(path, headers=headers, json={
        'name': 'Phone', 'url': 'http://new-camera.local/video', 'enabled': False})
    assert changed.status_code == 200
    assert not changed.json()['running'] and changed.json()['hardware_uid'] == source['hardware_uid']
    process.terminate.assert_called_once()
    restored = CameraSources(manager.path)
    assert restored.list()[0]['url'] == 'http://new-camera.local/video'
    assert restored.list()[0]['hardware_uid'] == source['hardware_uid']
    assert not restored.list()[0]['enabled']
    manager.start()
    assert popen.call_count == 1  # a disabled source stays stopped on server restart
    system.edge.put(path, headers=headers, json={
        'name': 'Phone', 'url': 'http://new-camera.local/video', 'enabled': True}).raise_for_status()
    assert popen.call_count == 2
    assert system.edge.delete(path, headers=headers).status_code == 204
    assert manager.list() == []


def test_source_rejects_nonvideo_and_credential_urls(system):
    for url in ('file:///etc/passwd', '0', 'http://', 'http://user:secret@camera.local/video', 'http://camera.local:bad/video'):
        result = system.edge.post('/edge/v1/camera-sources', headers=system.console,
                                  json={'name': 'Phone', 'url': url})
        assert result.status_code == 422, (url, result.text)
