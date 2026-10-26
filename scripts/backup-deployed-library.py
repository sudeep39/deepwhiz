"""Read-only deployed-library recovery, including existing Pinecone embeddings.

Run before republishing an instance whose runtime is not yet on durable storage.
The password is read from FINWHIZ_SHARED_BACKEND_PASSWORD, never an argument.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path
from urllib.parse import urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from finwhiz.config import Settings, AIConfig, AppError, digest, compact
from finwhiz.documents import PARSER_VERSION, parse_file, make_parents
from finwhiz.providers import Providers
from finwhiz.recovery import snapshot
from finwhiz.storage import Storage, Jobs, now


def recover(url, destination, settings):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise AppError('Supply an HTTPS application origin without credentials or a path.')
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise AppError('Backup destination must be new or empty.')
    password = settings.finwhiz_shared_backend_password
    if not password:
        raise AppError('Set FINWHIZ_SHARED_BACKEND_PASSWORD locally to the deployed workspace password.')
    destination.mkdir(parents=True, exist_ok=True)
    store = Storage(destination/'runtime')
    jobs = Jobs(store)
    # Providers supplies the platform trust store and configured transport.
    owner = Providers(settings, settings.provider('nebius'))
    headers = {'Authorization': 'Bearer '+password, 'X-AI-Provider': 'nebius'}
    try:
        def app_get(path, binary=False):
            response = owner.client.get(url.rstrip('/')+path, headers=headers, timeout=45)
            if response.status_code != 200:
                raise AppError(f'Deployed backup request failed with HTTP {response.status_code}.')
            return response.content if binary else response.json()
        state = app_get('/api/state')
        if any(j['status'] in ('queued','processing') for j in state['jobs']):
            raise AppError('Wait for ingestion to finish before backing up the deployed library.')
        ready = [j for j in state['jobs'] if j['status']=='ready']
        if not ready:
            raise AppError('No ready library is available to preserve.')
        documents = {d['id']:d for d in state['documents']}
        total_vectors = 0
        for job in ready:
            config = AIConfig.model_validate(job['aiConfig'])
            if config.fingerprint != settings.provider(config.provider).fingerprint:
                raise AppError('Backup configuration does not match the deployed embedding space.')
            provider = Providers(settings,config)
            try:
                files, parents = [], {}
                for item in job['files']:
                    doc = documents.get(item['id']) or next((d for d in job.get('documents',[]) if d['id']==item['id']),None)
                    if not doc:
                        raise AppError('Missing deployed document provenance.')
                    raw = app_get('/api/sources/'+item['id'],True)
                    if digest(raw) != doc['checksum']:
                        raise AppError('Downloaded source checksum differs from the registry.')
                    key = 'upload/'+item['id']
                    store.put(key,raw)
                    file = dict(**item,key=key,checksum=digest(raw),known={**doc,'sha256':digest(raw),'url':doc.get('sourceUrl')})
                    files.append(file)
                    if job.get('ingestionVersion') != PARSER_VERSION:
                        raise AppError('Parent reconstruction requires the deployed Python parser version.')
                    parsed_doc = parse_file(file,store)
                    parents[item['id']] = make_parents(parsed_doc['blocks'])
                    store.put_json(f"parents/{job['id']}/{item['id']}",parents[item['id']])
                for strategy,namespace in job['namespaces'].items():
                    host = provider.pinecone_host()
                    pc_headers = {'Api-Key':settings.pinecone_api_key,'X-Pinecone-Api-Version':'2025-10'}
                    ids, pagination = [], None
                    while True:
                        params = {'namespace':namespace,'limit':100}
                        if pagination: params['paginationToken']=pagination
                        page = provider.request('https://'+host+'/vectors/list?'+urlencode(params),headers=pc_headers,method='GET')
                        ids.extend(v['id'] for v in page.get('vectors',[]))
                        pagination = page.get('pagination',{}).get('next')
                        if not pagination: break
                    expected = next(s['chunks'] for s in job['summaries'] if s['strategy']==strategy)
                    if len(set(ids)) != expected:
                        raise AppError('Namespace count differs from the deployed registry; backup stopped.')
                    rows = []
                    for start in range(0,len(ids),50):
                        query = urlencode({'namespace':namespace,'ids':ids[start:start+50]},doseq=True)
                        fetched = provider.request('https://'+host+'/vectors/fetch?'+query,headers=pc_headers,method='GET').get('vectors',{})
                        store.put(f"retained-vectors/{job['id']}/{strategy}/{start}", gzip.compress(compact(fetched).encode()))
                        for identifier in ids[start:start+50]:
                            vector = fetched[identifier]
                            metadata = vector['metadata']
                            if metadata['corpus_id'] != job['id'] or metadata['strategy'] != strategy:
                                raise AppError('Vector identity does not match the deployed library.')
                            parent = parents[metadata['doc_id']][int(metadata['parent_id'].rsplit(':',1)[1])]
                            if parent['section'] != metadata['section_path'] or parent['kind'] != metadata['chunk_type']:
                                raise AppError('Recovered parent identity differs from the indexed chunk.')
                            rows.append(dict(id=identifier,metadata=metadata))
                            cache_key = 'embedding/'+digest([config.fingerprint,config.embeddingModel,config.dimension,metadata['text']])
                            store.put_json(cache_key,provider.vector(vector['values']))
                    store.put_json(f"chunks/{job['id']}/{strategy}",rows)
                    total_vectors += len(rows)
                    print(f'Preserved {len(rows)} existing {strategy} vectors; no embedding or vector writes.',flush=True)
                restored = {**job,'files':files}
                jobs.add(restored)
            finally:
                provider.close()
        after = app_get('/api/state')
        if [(j['id'],j['status'],j.get('namespaces')) for j in state['jobs']] != [(j['id'],j['status'],j.get('namespaces')) for j in after['jobs']]:
            raise AppError('The deployed registry changed during backup; repeat to a new directory.')
        snapshot(store.directory,destination/'snapshot')
        report = dict(createdAt=now(),origin=url,jobs=len(ready),documents=sum(len(j['files']) for j in ready),
                      retainedVectors=total_vectors,embeddingsCreated=0,vectorsWritten=0)
        (destination/'REPORT.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report),flush=True)
        return report
    finally:
        owner.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',required=True)
    parser.add_argument('--to',required=True)
    args=parser.parse_args()
    recover(args.url,args.to,Settings(_env_file=Path(__file__).resolve().parents[1]/'.env'))
