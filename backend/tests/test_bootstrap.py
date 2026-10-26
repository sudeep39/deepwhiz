import base64
import gzip
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from finwhiz.api import create_app
from finwhiz.bootstrap import recover_empty_library, load_bundle
from finwhiz.config import AppError, compact, digest
from finwhiz.storage import Storage, Jobs


def bundle(settings, tmp_path):
    config = settings.provider()
    raw = b'Original source bytes.'
    objects, keys, jobs = {}, {}, []
    for job_id in ['keep', 'remove']:
        file_id = 'a' * 32 if job_id == 'keep' else 'b' * 32
        def put(key, data):
            objects[digest(data)] = base64.b64encode(data).decode()
            keys[key] = digest(data)
        key = 'upload/' + job_id
        put(key, raw)
        put(f'chunks/{job_id}/fixed', compact([dict(id=job_id, metadata=dict(corpus_id=job_id, strategy='fixed'))]).encode())
        jobs.append(dict(id=job_id, status='ready', aiConfig=config.model_dump(), strategies=['fixed'],
                         namespaces={'fixed': job_id}, files=[dict(id=file_id, key=key, name='source.txt', size=len(raw), checksum=digest(raw))],
                         documents=[dict(id=file_id, checksum=digest(raw), ticker='TSLA', company='Tesla', form='10-Q', reportDate='2026-06-30', sourceUrl='/api/sources/' + file_id)]))
    data = dict(version=1, createdAt='2026-10-22', jobs=jobs, objects=objects, keys=keys,
                indexes=[dict(provider=config.provider, indexName=config.indexName, fingerprint=config.fingerprint,
                              namespaces={'keep': 1, 'remove': 1})])
    path = tmp_path / 'bundle.json.gz'
    path.write_bytes(gzip.compress(compact(data).encode()))
    return path, digest(path.read_bytes())


class Provider:
    counts = {'keep': {'vectorCount': 1}, 'remove': {'vectorCount': 1}}
    calls = []
    def __init__(self, *args):
        pass
    def pinecone(self, path, body):
        assert path == 'describe_index_stats'  # Recovery must never write vectors or call models.
        self.calls.append(path)
        return {'namespaces': self.counts}
    def close(self):
        pass


def test_restore_empty_registry_and_skip_existing_data(settings, tmp_path):
    path, sha = bundle(settings, tmp_path)
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    before = len(Provider.calls)
    plan = recover_empty_library(store, jobs, settings, path, sha, Provider)
    assert plan['status'] == 'preview' and not jobs.all() and len(Provider.calls) == before
    result = recover_empty_library(store, jobs, settings, path, sha, Provider, True)
    assert result['status'] == 'restored' and result['documents'] == 2
    assert len(jobs.all()) == 2 and store.get('upload/keep') == b'Original source bytes.'
    def forbidden(*args):
        raise AssertionError('Do not reopen providers for an existing library')
    assert recover_empty_library(store, jobs, settings, path, 'invalid', forbidden, True)['status'] == 'skipped'


def test_checksum_artifact_conflicts_and_remote_count_changes_fail_safely(settings, tmp_path):
    path, sha = bundle(settings, tmp_path)
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    with pytest.raises(AppError, match='checksum'):
        recover_empty_library(store, jobs, settings, path, '0' * 64, Provider, True)
    store.put('upload/keep', b'existing unrelated bytes')
    with pytest.raises(AppError, match='conflicts'):
        recover_empty_library(store, jobs, settings, path, sha, Provider, True)
    assert not jobs.all() and store.get('upload/keep') == b'existing unrelated bytes'
    class Changed(Provider):
        counts = {'keep': {'vectorCount': 2}, 'remove': {'vectorCount': 1}}
    with pytest.raises(AppError, match='counts changed'):
        recover_empty_library(store, jobs, settings, path, sha, Changed, True)


def test_bootstrap_does_not_resurrect_a_deleted_duplicate(settings, tmp_path):
    path, sha = bundle(settings, tmp_path)
    class Cleaned(Provider):
        counts = {'keep': {'vectorCount': 1}}
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    result = recover_empty_library(store, jobs, settings, path, sha, Cleaned, True)
    assert result['documents'] == 1 and result['sourceAliases'] == 1
    alias = next(job for job in jobs.all() if job['id'] == 'remove')
    assert alias['documents'] == [] and alias['namespaces'] == {} and alias['files'][0]['key'] == 'upload/keep'


def test_startup_import_makes_documents_and_sources_available(settings, tmp_path):
    path, sha = bundle(settings, tmp_path)
    configured = settings.model_copy(update={'finwhiz_bootstrap_library': path, 'finwhiz_bootstrap_sha256': sha})
    with TestClient(create_app(configured, disable_workers=True, provider_factory=Provider)) as client:
        response = client.get('/api/state', headers={'Authorization': 'Bearer baseline-password'})
        assert response.status_code == 200 and len(response.json()['documents']) == 2
        assert response.json()['recovery']['status'] == 'restored'
        source = client.get('/api/sources/' + 'a' * 32, headers={'Authorization': 'Bearer baseline-password'})
        assert source.status_code == 200 and source.content == b'Original source bytes.'


def test_packaged_replit_bundle_contains_original_files_and_matching_parents():
    from finwhiz.config import ROOT
    path = ROOT / 'data/recovery/replit-library-v1.json.gz'
    sha = '84e0f0b08f6bbe46d3b0891ed6003aeabe568e98e85dcb0923f7ac1c3b07b7d9'
    data, objects = load_bundle(path, sha)
    assert sum(len(job['documents']) for job in data['jobs']) == 9
    assert sum(sum(index['namespaces'].values()) for index in data['indexes']) == 3332
    for job in data['jobs']:
        for strategy in job['strategies']:
            rows = json.loads(objects[data['keys'][f'chunks/{job["id"]}/{strategy}']])
            for row in rows:
                m = row['metadata']
                parents = json.loads(objects[data['keys'][f'parents/{job["id"]}/{m["doc_id"]}']])
                parent = parents[int(m['parent_id'].rsplit(':', 1)[1])]
                assert parent['section'] == m['section_path'] and parent['kind'] == m['chunk_type']


def test_replit_auto_detects_bundle_without_new_environment_settings(settings, tmp_path, monkeypatch):
    import finwhiz.api as api
    import finwhiz.bootstrap as bootstrap
    path, sha = bundle(settings, tmp_path)
    deployed = tmp_path / 'data/recovery/replit-library-v1.json.gz'
    deployed.parent.mkdir(parents=True)
    deployed.write_bytes(path.read_bytes())
    monkeypatch.setattr(api, 'ROOT', tmp_path)
    monkeypatch.setattr(bootstrap, 'DEFAULT_BUNDLE_SHA256', sha)
    configured = settings.model_copy(update={'replit_deployment': '1'})
    with TestClient(create_app(configured, disable_workers=True, provider_factory=Provider)) as client:
        result = client.get('/api/state', headers={'Authorization': 'Bearer baseline-password'})
        assert result.status_code == 200 and result.json()['recovery']['status'] == 'restored'


def test_replit_can_start_empty_without_provider_calls(settings, monkeypatch):
    from finwhiz.config import Settings
    monkeypatch.setenv('FINWHIZ_BOOTSTRAP_AUTO', 'false')
    monkeypatch.setenv('FINWHIZ_BOOTSTRAP_LIBRARY', '')
    configured = Settings(
        _env_file=None, replit_deployment='1',
        finwhiz_data_dir=settings.finwhiz_data_dir,
        app_password='baseline-password', openai_api_key='',
        nebius_api_key='', pinecone_api_key='', finwhiz_backend_url='',
    )
    assert configured.finwhiz_bootstrap_auto is False
    assert configured.finwhiz_bootstrap_library is None
    def forbidden(*args):
        raise AssertionError('Empty-library startup must not contact providers')
    with TestClient(create_app(configured, disable_workers=True, provider_factory=forbidden)) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/health').json() == {'ok': True}
        assert client.get('/api/state').status_code == 401
        state = client.get('/api/state', headers={'Authorization': 'Bearer baseline-password'})
        assert state.status_code == 200
        assert state.json()['documents'] == []
