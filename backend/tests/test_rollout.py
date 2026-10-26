"""Rollout guardrails: explicit origins, readiness and production access control."""
from fastapi.testclient import TestClient
from finwhiz.api import create_app
import httpx
import pytest
from finwhiz.config import AppError


def test_forwarded_host_cannot_authorize_a_foreign_origin(settings):
    with TestClient(create_app(settings, disable_workers=True)) as client:
        result = client.post('/api/scope', json={}, headers={
            'Authorization': 'Bearer baseline-password', 'Origin': 'https://attacker.example',
            'X-Forwarded-Host': 'attacker.example'})
        assert result.status_code == 403
        assert result.json()['error'] == 'Origin not allowed.'


def test_explicit_origin_reaches_normal_request_validation(settings):
    allowed = settings.model_copy(update={'allowed_origins': ['https://research.example']})
    with TestClient(create_app(allowed, disable_workers=True)) as client:
        headers = {'Authorization': 'Bearer baseline-password', 'Origin': 'https://research.example'}
        assert client.post('/api/scope', json={}, headers=headers).status_code == 400
        headers['Origin'] = 'https://research.example.evil'
        assert client.post('/api/scope', json={}, headers=headers).status_code == 403


def test_health_is_not_a_false_readiness_signal(settings):
    with TestClient(create_app(settings, disable_workers=True)) as client:
        assert client.get('/api/health').json() == {'ok': True}
        result = client.get('/api/ready')
        assert result.status_code == 503
        assert result.json()['checks']['storage']
        assert not result.json()['checks']['credentials']
        assert 'baseline-password' not in result.text


def test_configured_workspace_can_report_ready(settings):
    configured = settings.model_copy(update={'openai_api_key': 'offline-secret', 'pinecone_api_key': 'offline-secret'})
    with TestClient(create_app(configured, disable_workers=True)) as client:
        result = client.get('/api/ready')
        assert result.status_code == 200 and result.json()['ready']
        assert 'offline-secret' not in result.text


def test_production_rejects_weak_workspace_secret(settings):
    production = settings.model_copy(update={'node_env': 'production', 'app_password': 'short'})
    with TestClient(create_app(production, disable_workers=True)) as client:
        assert client.get('/api/state', headers={'Authorization': 'Bearer short'}).status_code == 503
        assert not client.get('/api/ready').json()['checks']['accessControl']


def test_sec_download_rejects_oversized_stream(settings, monkeypatch):
    from finwhiz import catalog
    client_type = httpx.Client
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(21):
                yield b'x' * 1024 * 1024
    monkeypatch.setattr(catalog.httpx, 'Client', lambda **kwargs: client_type(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())), **kwargs))
    with pytest.raises(AppError, match='size limit') as error:
        catalog.sec_request('https://www.sec.gov/report.html', settings.model_copy(update={'sec_user_agent': 'DeepWhiz test@example.com'}), True)
    assert error.value.status == 413


def test_sec_import_deadline_releases_admission_lock(settings, monkeypatch):
    from finwhiz import sec_import
    import time
    sleep = time.sleep
    monkeypatch.setattr(sec_import.time, 'sleep', lambda _: sleep(.02))
    limited = settings.model_copy(update={'sec_import_timeout_seconds': .001})
    with pytest.raises(AppError, match='SEC import timed out'):
        sec_import.import_reports({'companies': 'TSLA'}, limited, None, None, None)
    assert not sec_import._lock.locked()
