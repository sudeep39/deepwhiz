"""Durable library restore preserves namespace and cache identities without provider calls."""
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from finwhiz.config import AppError, digest
from finwhiz.storage import Storage, Jobs
from finwhiz.library_persistence import CloudLibrary, pack_library, restore_library, inspect_archive


class Bucket:
    def __init__(self): self.objects={}
    def exists(self,key): return key in self.objects
    def download_as_bytes(self,key): return self.objects[key]
    def upload_from_bytes(self,key,data): self.objects[key]=data


def library(tmp_path, identifier='original'):
    store=Storage(tmp_path);jobs=Jobs(store)
    store.put('upload/source',b'original filing')
    store.put_json(f'chunks/{identifier}/semantic',[{'id':'existing-vector-id','metadata':{'text':'Revenue | 100'}}])
    store.put_json('embedding/original',[.2,.4])
    jobs.add(dict(id=identifier,status='ready',files=[dict(id='source',key='upload/source')],
                  namespaces={'semantic':'existing-py-v1-namespace'},strategies=['semantic'],documents=[]))
    return store,jobs


def cloud(settings,bucket):
    return CloudLibrary(settings.model_copy(update={'finwhiz_cloud_library':True}),client=bucket)


def test_republish_restores_existing_ids_sources_and_embeddings(settings,tmp_path):
    original,jobs=library(tmp_path/'old')
    bucket=Bucket();writer=cloud(settings,bucket)
    writer.checkpoint(original,jobs)
    restored=Storage(tmp_path/'new');registry=Jobs(restored)
    reader=cloud(settings,bucket);reader.restore(restored,registry)
    assert registry.all()==jobs.all()
    assert registry.all()[0]['namespaces']['semantic']=='existing-py-v1-namespace'
    assert restored.json('embedding/original')==[.2,.4]
    assert restored.get('upload/source')==b'original filing'
    assert reader.status['status']=='current'


def test_failed_verification_keeps_previous_checkpoint(settings,tmp_path):
    store,jobs=library(tmp_path)
    bucket=Bucket();client=cloud(settings,bucket);client.checkpoint(store,jobs)
    pointer=bucket.objects[client.prefix+'/latest.json']
    def corrupt(key):
        return b'corrupt' if key.endswith('.zip') else bucket.objects[key]
    bucket.download_as_bytes=corrupt
    with pytest.raises(AppError,match='verification failed'):
        client.checkpoint(store,jobs,True)
    assert bucket.objects[client.prefix+'/latest.json']==pointer


def test_partial_runtime_cannot_replace_complete_cloud_registry(settings,tmp_path):
    store,jobs=library(tmp_path/'complete')
    bucket=Bucket();client=cloud(settings,bucket);client.checkpoint(store,jobs)
    other,other_jobs=library(tmp_path/'partial','different-job')
    with pytest.raises(AppError,match='omits previously protected'):
        cloud(settings,bucket).checkpoint(other,other_jobs)


def test_tampered_archive_and_nonempty_restore_are_rejected(tmp_path):
    store,jobs=library(tmp_path/'source');data,manifest=pack_library(store,jobs)
    with pytest.raises(AppError,match='empty registry'):
        restore_library(data,store,jobs)
    buf=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original,zipfile.ZipFile(buf,'w') as broken:
        for name in original.namelist():
            broken.writestr(name,b'wrong' if name.startswith('blobs/') else original.read(name))
    target=Storage(tmp_path/'target');registry=Jobs(target)
    with pytest.raises(AppError,match='checksum'):
        restore_library(buf.getvalue(),target,registry)
    assert registry.all()==[]


def test_active_or_missing_source_library_is_not_protected(tmp_path):
    store,jobs=library(tmp_path)
    jobs.update('original',status='processing')
    with pytest.raises(AppError,match='ingestion'):
        pack_library(store,jobs)
    jobs.update('original',status='ready');store.remove('upload/source')
    with pytest.raises(AppError,match='original source'):
        pack_library(store,jobs)


def test_restoring_two_jobs_preserves_registry_order(tmp_path):
    store,jobs=library(tmp_path/'source')
    store.put_json('chunks/second/semantic',[])
    jobs.add(dict(id='second',status='ready',files=[],documents=[],strategies=['semantic'],namespaces={'semantic':'second-existing-namespace'}))
    data,_=pack_library(store,jobs)
    target=Storage(tmp_path/'target');registry=Jobs(target)
    restore_library(data,target,registry)
    assert registry.all()==jobs.all()


def test_authenticated_api_restores_and_exports_cloud_checkpoint(settings,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from finwhiz.api import create_app
    source,jobs=library(tmp_path/'source')
    jobs.update('original',aiConfig=settings.provider().model_dump(),files=[dict(id='source',key='upload/source',name='source.txt',size=15)])
    bucket=Bucket();cloud(settings,bucket).checkpoint(source,jobs)
    configured=settings.model_copy(update={'finwhiz_cloud_library':True,'finwhiz_data_dir':tmp_path/'restored'})
    monkeypatch.setattr('finwhiz.library_persistence.CloudLibrary',lambda configuration: CloudLibrary(configuration,client=bucket))
    with TestClient(create_app(configured,disable_workers=True)) as client:
        assert client.get('/api/library/export').status_code==401
        headers={'Authorization':'Bearer baseline-password'}
        state=client.get('/api/state',headers=headers)
        assert state.status_code==200
        assert state.json()['config']['libraryProtection']['status']=='current'
        exported=client.get('/api/library/export',headers=headers)
        assert exported.status_code==200
        manifest,_=inspect_archive(exported.content)
        assert manifest['jobs'][0]['namespaces']==jobs.all()[0]['namespaces']
        assert client.post('/api/library/checkpoint',headers=headers).status_code==200
