"""Restore a verified library snapshot into an empty registry, reusing vectors."""
import base64
import gzip
import json
import re

from .config import AIConfig, AppError, compact, digest
from .storage import now

DEFAULT_BUNDLE_SHA256 = '84e0f0b08f6bbe46d3b0891ed6003aeabe568e98e85dcb0923f7ac1c3b07b7d9'
RETAINED_BUNDLE_SHA256 = 'ac6fa0d5f88e564977863565a91f1fdea76160184e62cbbee32866873db38555'


def load_bundle(path, expected_sha256):
    raw = path.read_bytes()
    if not expected_sha256 or digest(raw) != expected_sha256:
        raise AppError('Library recovery bundle checksum mismatch. No registry changes were made.')
    # Bound expansion before JSON/base64 parsing.
    with gzip.open(path, 'rb') as stream:
        expanded = stream.read(256 * 1024 * 1024 + 1)
    if len(expanded) > 256 * 1024 * 1024:
        raise AppError('Library recovery bundle exceeds its size limit.')
    bundle = json.loads(expanded)
    if bundle.get('version') != 1 or not bundle.get('jobs') or not bundle.get('keys'):
        raise AppError('Invalid library recovery bundle.')
    jobs = bundle['jobs']
    if len({job['id'] for job in jobs}) != len(jobs) or any(job['status'] != 'ready' for job in jobs):
        raise AppError('Invalid recovered job identities/statuses.')
    objects = {}
    for checksum, encoded in bundle['objects'].items():
        data = base64.b64decode(encoded, validate=True)
        if not re.fullmatch(r'[a-f0-9]{64}', checksum) or digest(data) != checksum:
            raise AppError('Recovery object checksum mismatch.')
        objects[checksum] = data
    if any(checksum not in objects for checksum in bundle['keys'].values()):
        raise AppError('Recovery bundle references missing objects.')
    for job in jobs:
        AIConfig.model_validate(job['aiConfig'])
        for file in job['files']:
            data = objects.get(bundle['keys'].get(file['key']))
            if data is None or digest(data) != file.get('checksum'):
                raise AppError('Recovery source file checksum mismatch.')
        for strategy in job['strategies']:
            key = f'chunks/{job["id"]}/{strategy}'
            rows = json.loads(objects[bundle['keys'][key]])
            if not rows or len({row['id'] for row in rows}) != len(rows):
                raise AppError('Recovery chunk identities are missing or duplicated.')
            if any(row['metadata'].get('corpus_id') != job['id'] or row['metadata'].get('strategy') != strategy for row in rows):
                raise AppError('Recovery chunk provenance mismatch.')
    return bundle, objects


def recover_empty_library(store, jobs, settings, bundle_path, expected_sha256, provider_factory, apply=False):
    if jobs.all():
        return dict(status='skipped', reason='Registry already contains jobs; existing data was not changed.')
    bundle, objects = load_bundle(bundle_path, expected_sha256)
    report = dict(status='preview', bundleSha256=expected_sha256, snapshotCreatedAt=bundle['createdAt'],
                  jobs=len(bundle['jobs']), documents=sum(len(job['documents']) for job in bundle['jobs']),
                  reusedVectors=sum(sum(index['namespaces'].values()) for index in bundle['indexes']),
                  embeddingsCreated=0, vectorsWritten=0)
    if not apply:
        return report
    actual = {}
    for index in bundle['indexes']:
        config = settings.provider(index['provider'])
        if config.indexName != index['indexName'] or config.fingerprint != index['fingerprint']:
            raise AppError('Recovery embedding/index configuration differs from the retained library.')
        provider = provider_factory(settings, config)
        try:
            actual.update(provider.pinecone('describe_index_stats', {}).get('namespaces', {}))
        finally:
            provider.close()
    registered = json.loads(compact(bundle['jobs']))
    healthy, missing = [], []
    for job in registered:
        expected = next(index['namespaces'] for index in bundle['indexes'] if index['indexName'] == job['aiConfig']['indexName'])
        counts = [actual.get(namespace, {}).get('vectorCount', 0) for namespace in job['namespaces'].values()]
        wanted = [expected[namespace] for namespace in job['namespaces'].values()]
        if counts == wanted:
            healthy.append(job)
        elif all(count == 0 for count in counts):
            missing.append(job)
        else:
            raise AppError('Recovered namespace counts changed or deletion is incomplete. Inspect Pinecone before startup.')
    # A completed duplicate cleanup must not be undone by an older bootstrap snapshot.
    for job in missing:
        alternatives = [candidate for candidate in healthy if
                        AIConfig.model_validate(candidate['aiConfig']).fingerprint == AIConfig.model_validate(job['aiConfig']).fingerprint and
                        all(any(doc['checksum'] == kept['checksum'] for kept in candidate['documents']) for doc in job['documents'])]
        if not alternatives:
            raise AppError('A retained namespace is absent and its documents have no verified canonical copy.')
        kept = alternatives[0]
        files = {file['id']: file for file in kept['files']}
        aliases = []
        for file in job['files']:
            doc = next(doc for doc in job['documents'] if doc['id'] == file['id'])
            canonical = next(doc2 for doc2 in kept['documents'] if doc2['checksum'] == doc['checksum'])
            source = files[canonical['id']]
            aliases.append({**file, 'key': source['key']})
        job.update(documents=[], namespaces={}, strategies=[], summaries=[], files=aliases,
                   detail='Previously removed duplicate recovered as source-link alias only.', sourceAliasFor=kept['id'])
    with jobs.connection() as db:
        if db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]:
            raise AppError('Registry changed during recovery. Existing jobs were not overwritten.')
        for key, checksum in bundle['keys'].items():
            existing = store.get(key)
            if existing is not None and digest(existing) != checksum:
                raise AppError('An existing runtime artifact conflicts with the recovery bundle.')
        for key, checksum in bundle['keys'].items():
            if store.get(key) is None:
                store.put(key, objects[checksum])
            if digest(store.get(key)) != checksum:
                raise AppError('Recovery artifact readback failed.')
        for ordinal, job in enumerate(registered, 1):
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'], compact(job), ordinal))
        report.update(status='restored', documents=sum(len(job['documents']) for job in registered),
                      restoredAt=now(), sourceAliases=len(missing),
                      reusedVectors=sum(actual.get(namespace, {}).get('vectorCount', 0) for job in healthy for namespace in job['namespaces'].values()))
        db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('library-bootstrap', compact(report)))
    store.put_json('library-bootstrap', report)
    return report
