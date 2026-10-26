"""Cold deployment restores the exact retained library, without re-embedding."""
from fastapi.testclient import TestClient

from finwhiz.api import create_app
from finwhiz.bootstrap import load_bundle, RETAINED_BUNDLE_SHA256
from finwhiz.config import ROOT


def test_fresh_deployment_restores_seven_retained_filings_read_only(settings):
    path=ROOT/'data/recovery/deepwhiz-retained-library-v1.json.gz'
    bundle,_=load_bundle(path,RETAINED_BUNDLE_SHA256)
    namespaces=bundle['indexes'][0]['namespaces']
    calls=[]
    class ReadOnlyProvider:
        def __init__(self,*args): pass
        def pinecone(self,operation,body):
            calls.append(operation)
            assert operation=='describe_index_stats'
            return {'namespaces':{name:{'vectorCount':count} for name,count in namespaces.items()}}
        def close(self): pass
        def embed(self,*args,**kwargs): raise AssertionError('Recovery must reuse embeddings')
        def upsert(self,*args,**kwargs): raise AssertionError('Recovery must not rewrite vectors')
    configured=settings.model_copy(update={'replit_deployment':'1','finwhiz_bootstrap_library':path,
                                           'finwhiz_bootstrap_sha256':RETAINED_BUNDLE_SHA256})
    with TestClient(create_app(configured,disable_workers=True,provider_factory=ReadOnlyProvider)) as client:
        result=client.get('/api/state',headers={'Authorization':'Bearer baseline-password','X-AI-Provider':'nebius'})
        assert result.status_code==200
        state=result.json()
        assert len(state['documents'])==7
        assert {d['reportDate'] for d in state['documents']}=={'2025-03-31','2025-06-30','2025-09-30','2025-12-31','2026-03-31','2026-06-30','2026-09-30'}
        assert state['recovery']['embeddingsCreated']==0
        assert state['recovery']['vectorsWritten']==0
        assert state['jobs'][0]['namespaces']==bundle['jobs'][0]['namespaces']
        assert state['recovery']['reusedVectors']==2416
    assert calls==['describe_index_stats']
    # Another startup preserves the nonempty restored registry.
    with TestClient(create_app(configured,disable_workers=True,provider_factory=ReadOnlyProvider)) as client:
        assert client.get('/api/state',headers={'Authorization':'Bearer baseline-password','X-AI-Provider':'nebius'}).json()['documents']
    assert calls==['describe_index_stats']
