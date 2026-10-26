"""Real-filing regression cases: no provider calls or fabricated evidence."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_automotive_margins import library
from finwhiz.calculation_handlers import local_tables, try_calculation
from finwhiz.fiscal import catalog_documents
from finwhiz.scope import resolve_scope
from finwhiz.answer_validation import validation_issues, coverage_issues
from finwhiz.finance import calculate
from finwhiz.research import research
from finwhiz.config import AIConfig
from finwhiz.lexical import LexicalIndex
from finwhiz.schemas import Answer

SUITE = json.loads((Path(__file__).resolve().parents[2]/'evaluation/tesla-q2-2026.json').read_text())['cases']


@pytest.mark.parametrize('case', SUITE, ids=[c['id'] for c in SUITE])
def test_real_filing_question(case, library):
    jobs, store = library
    documents = [d for d in catalog_documents(jobs, store) if d['reportDate']=='2026-06-30']
    scope = resolve_scope(case['question'], documents, ['TSLA'], ['2026-06-30'])
    assert scope['status']=='resolved', scope
    rows = [r for j in jobs for r in store.json(f"chunks/{j['id']}/semantic", [])
            if r['metadata']['report_date']=='2026-06-30']
    tables = local_tables(store, rows, case['question'])
    sources = [dict(id=f'S{i+1}', **r['metadata']) for i,r in enumerate(tables)]
    if case['id']=='energy-margin-drivers':
        # Causality must come from MD&A, not be invented by a numeric handler.
        assert try_calculation(case['question'],sources,scope)[0] is None
        prose = '\n'.join(r['metadata']['text'] for r in rows if r['metadata'].get('chunk_type')!='table')
        assert all(term in prose.lower() for term in case['expected']['disclosedDrivers'])
        assert sources
        return
    answer, method = try_calculation(case['question'],sources,scope)
    assert answer, {'id':case['id'], 'scope':scope, 'tableCount':len(tables)}
    assert validation_issues(answer,sources,False)==[]
    assert coverage_issues(answer,sources,scope)==[]
    values = [calculate(c['operation'],c['values']) for c in answer['calculations']]
    if case['id']=='q1-derived-from-q2':
        assert 22387 in values and 477 in values
        assert '28,236' in answer['answer'] and '1,114' in answer['answer']
    elif case['id']=='free-cash-flow':
        assert 352 in values and 810 in values
    elif case['id']=='energy-gross-profit':
        assert 640 in values and 846 in values and 'declined' in answer['answer']
    elif case['id']=='automotive-credit-margins':
        assert len(values)==4
    elif case['id']=='regulatory-credit-revenue':
        assert '146' in answer['answer'] and '439' in answer['answer']
    elif case['id']=='spacex-reconciliation':
        assert '1,005' in answer['answer'] and ('negative' in answer['answer'].lower() or '\u2212' in answer['answer'] or '-1,005' in answer['answer'])


@pytest.mark.parametrize('case', SUITE, ids=[c['id'] for c in SUITE])
def test_research_graph_on_real_filing(case, library, monkeypatch):
    jobs, store = library
    config = AIConfig.model_validate(jobs[0]['aiConfig'])
    documents = [d for d in catalog_documents(jobs,store) if d['reportDate']=='2026-06-30']
    scope = resolve_scope(case['question'],documents,['TSLA'],['2026-06-30'])
    def load(store,key):
        rows=store.json(key)
        return rows,LexicalIndex(rows).counts
    monkeypatch.setattr('finwhiz.retrieval.CORPORA',SimpleNamespace(load=load))
    class Provider:
        def embed(self,*args,**kwargs):
            assert case['id']=='energy-margin-drivers', 'Numeric answers should use local statement evidence'
            return [[0.0]]
        def pinecone(self,*args):
            return {'matches':[]}
        def structured(self,system,payload,schema,model=None):
            assert case['id']=='energy-margin-drivers' and schema is Answer
            # Scripted synthesis checks that actual retrieved MD&A reaches the
            # model. It does not claim to measure live-provider answer quality.
            source=next((s for s in payload['evidence'] if 'warranty' in s['text'].lower()
                         and 'cost per mwh' in s['text'].lower()),None)
            assert source, 'The model must receive the disclosed energy-margin drivers'
            text=' '.join(sentence for sentence in re.split(r'(?<=[.!?])\s+',source['text'])
                          if ('energy' in sentence.lower() or 'mwh' in sentence.lower())
                          and ('warranty' in sentence.lower() or 'gross margin' in sentence.lower()))
            assert text
            return dict(status='answered',answer=text+f" [{source['id']}]",citation_ids=[source['id']],
                        chart=dict(type='none',title='',unit='',points=[]),calculations=[])
    provider=Provider()
    provider.config=config
    result=research(store,jobs,provider,case['question'],['TSLA'],scope_result=scope,rerank=False)
    assert result['status']=='answered',result.get('evidenceGaps')
    assert result['sources'] and result['citation_ids']
