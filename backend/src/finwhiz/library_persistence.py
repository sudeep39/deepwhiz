"""Verified durable library checkpoints; never embeds or writes Pinecone vectors."""
import asyncio
import io
import json
import re
import threading
import zipfile

from .config import AppError, compact, digest
from .storage import now

MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024


def pack_library(store, jobs):
    records = jobs.all()
    if not records or any(j['status'] in ('queued', 'processing') for j in records):
        raise AppError('Wait for all ingestion jobs to finish before protecting the library.', 409)
    for job in records:
        if job['status'] != 'ready':
            continue
        for file in job.get('files', []):
            if file.get('samplePath'):
                raise AppError('Convert sample-path sources to stored uploads before checkpointing.', 409)
            if file.get('key') and store.get(file['key']) is None:
                raise AppError('An original source file is missing. Restore it before checkpointing.', 409)
            if file.get('key') and file.get('checksum') and digest(store.get(file['key']))!=file['checksum']:
                raise AppError('An original source checksum changed. Restore it before checkpointing.',409)
        for strategy in job.get('namespaces', {}):
            if store.get(f"chunks/{job['id']}/{strategy}") is None:
                raise AppError('A retrieval artifact is missing. Restore it before checkpointing.', 409)
    stream, files, total = io.BytesIO(), [], 0
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(store.directory.iterdir()):
            if not path.is_file() or not re.fullmatch(r'[a-f0-9]{64}', path.name):
                continue
            data = path.read_bytes()
            total += len(data)
            if total > MAX_ARCHIVE_BYTES:
                raise AppError('Library exceeds the checkpoint size limit.')
            files.append(dict(name=path.name, sha256=digest(data), bytes=len(data)))
            archive.writestr('blobs/'+path.name, data)
        if compact(records) != compact(jobs.all()):
            raise AppError('The library changed during checkpointing. Retry after ingestion finishes.', 409)
        manifest = dict(version=1, createdAt=now(), jobs=records, files=files)
        archive.writestr('LIBRARY.json', compact(manifest))
    return stream.getvalue(), manifest


def inspect_archive(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries)>20000 or sum(e.file_size for e in entries)>MAX_ARCHIVE_BYTES:
                raise AppError('Library archive exceeds the restore limit.')
            manifest = json.loads(archive.read('LIBRARY.json'))
            if manifest.get('version')!=1 or not manifest.get('jobs') or not manifest.get('files'):
                raise AppError('Invalid library archive.')
            objects = {}
            expected = {'LIBRARY.json'}
            for file in manifest['files']:
                name = file['name']
                if not re.fullmatch(r'[a-f0-9]{64}', name) or name in objects:
                    raise AppError('Invalid or duplicate library object name.')
                expected.add('blobs/'+name)
                value = archive.read('blobs/'+name)
                if len(value)!=file['bytes'] or digest(value)!=file['sha256']:
                    raise AppError('Library archive checksum verification failed.')
                objects[name] = value
            if set(archive.namelist())!=expected or len(entries)!=len(expected):
                raise AppError('Unexpected entries in library archive.')
            identifiers=[j['id'] for j in manifest['jobs']]
            if len(set(identifiers))!=len(identifiers):
                raise AppError('Duplicate job identities in library archive.')
            for job in manifest['jobs']:
                if job['status'] != 'ready':
                    continue
                for file in job.get('files', []):
                    if file.get('samplePath') or (file.get('key') and digest(file['key']) not in objects):
                        raise AppError('Library archive lacks an original source.')
                for strategy in job.get('namespaces', {}):
                    if digest(f"chunks/{job['id']}/{strategy}") not in objects:
                        raise AppError('Library archive lacks retrieval artifacts.')
            return manifest, objects
    except (ValueError, KeyError, TypeError, zipfile.BadZipFile):
        raise AppError('Invalid library checkpoint. No registry changes were made.') from None


def restore_library(data, store, jobs):
    manifest, objects = inspect_archive(data)
    with jobs.connection() as db:
        if db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]:
            raise AppError('Restore requires an empty registry; existing documents are never overwritten.', 409)
        for name, value in objects.items():
            path=store.directory/name
            if path.exists() and path.read_bytes()!=value:
                raise AppError('Existing artifacts conflict with this checkpoint.', 409)
        for name,value in objects.items():
            path=store.directory/name
            if not path.exists():
                temporary=path.with_suffix('.restore.tmp')
                temporary.write_bytes(value)
                temporary.replace(path)
        for ordinal,job in enumerate(reversed(manifest['jobs']),1):
            db.execute('INSERT INTO jobs VALUES (?,?,?)',(job['id'],compact(job),ordinal))
    return dict(status='restored', jobs=len(manifest['jobs']), embeddingsCreated=0, vectorsWritten=0)


class CloudLibrary:
    def __init__(self, settings, client=None):
        self.settings=settings
        if client is None:
            try:
                from replit.object_storage import Client
                client=Client(bucket_id=settings.finwhiz_storage_bucket_id or None)
            except Exception:
                raise AppError('Configure a Replit App Storage bucket for library protection before starting with FINWHIZ_CLOUD_LIBRARY=true.') from None
        self.client=client
        self.prefix=settings.finwhiz_library_prefix.rstrip('/')
        self.signature=None
        self.lock=threading.Lock()
        self.status=dict(enabled=True,status='pending',message='Library checkpoint is pending.')

    def pointer(self):
        key=self.prefix+'/latest.json'
        return json.loads(self.client.download_as_bytes(key)) if self.client.exists(key) else None

    def restore(self,store,jobs):
        pointer=self.pointer()
        if not pointer or jobs.all():
            return
        if not re.fullmatch(r'[a-f0-9]{64}',pointer.get('sha256','')):
            raise AppError('Invalid durable library checkpoint reference.')
        data=self.client.download_as_bytes(self.prefix+'/snapshots/'+pointer['sha256']+'.zip')
        if digest(data)!=pointer['sha256']:
            raise AppError('Durable library checkpoint checksum failed.')
        restore_library(data,store,jobs)
        self.signature=digest(jobs.all())
        self.status=dict(enabled=True,status='current',lastBackupAt=pointer['createdAt'],jobs=len(jobs.all()),message='Library restored without re-embedding.')

    def save_archive(self,data):
        manifest,_=inspect_archive(data)
        previous=self.pointer()
        identifiers={j['id'] for j in manifest['jobs']}
        if previous and not set(previous['jobIds']).issubset(identifiers):
            raise AppError('This runtime omits previously protected jobs. Restore the complete library before replacing its checkpoint.',409)
        checksum=digest(data)
        key=self.prefix+'/snapshots/'+checksum+'.zip'
        self.client.upload_from_bytes(key,data)
        if digest(self.client.download_as_bytes(key))!=checksum:
            raise AppError('Cloud checkpoint verification failed; the previous checkpoint was retained.')
        pointer=dict(version=1,sha256=checksum,createdAt=manifest['createdAt'],jobIds=sorted(identifiers))
        self.client.upload_from_bytes(self.prefix+'/latest.json',compact(pointer).encode())
        self.status=dict(enabled=True,status='current',lastBackupAt=manifest['createdAt'],jobs=len(identifiers),message='Verified cloud checkpoint; existing vector namespaces are preserved.')
        return self.status

    def checkpoint(self,store,jobs,force=False):
        if not self.lock.acquire(blocking=False):
            raise AppError('A library checkpoint is already running. Retry shortly.',409)
        try:
            records=jobs.all()
            signature=digest(records)
            if not force and signature==self.signature:
                return self.status
            data,_=pack_library(store,jobs)
            self.save_archive(data)
            self.signature=signature
            return self.status
        finally:
            self.lock.release()


async def protect_loop(cloud,store,jobs):
    while True:
        try:
            records=await asyncio.to_thread(jobs.all)
            if records and not any(j['status'] in ('queued','processing') for j in records):
                await asyncio.to_thread(cloud.checkpoint,store,jobs)
        except asyncio.CancelledError:
            raise
        except Exception:
            cloud.status=dict(enabled=True,status='error',message='Library checkpoint failed. Preserve the current instance and retry protection before republishing.')
        await asyncio.sleep(5)
