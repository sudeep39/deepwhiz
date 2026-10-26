"""Scripted offline agents exercise actual graph routing and read-only tools."""
from copy import deepcopy
import time
import uuid

import pytest

from finwhiz.agentic import merge_evidence, read_tables
from finwhiz.config import AppError
from finwhiz.finance import empty_chart
from finwhiz.ingestion import ingest
from finwhiz.research import research
from finwhiz.schemas import AgentDecision, AgentPlan, Answer
from finwhiz.storage import Storage
from finwhiz.telemetry import ResearchTrace
from test_workflows import setup_job


def decision(action='answer', query='', ids=None):
    return dict(action=action, query=query, source_ids=ids or [], missing_evidence=['revenue'] if action == 'search' else [])


def source(chunk, text, id='S1'):
    return dict(id=id, chunkId=chunk, ticker='ACME', company='Acme', report_date='2025-03-31',
                text=text, chunk_type='text')


def evidence(*sources):
    return dict(sources=list(sources), ranking=[], candidate_count=len(sources), missingCoverage=[],
                rerankFallback=False, rerankFallbackReason=None)


def fixture(settings, monkeypatch, decisions, batches, answer='Revenue was 125 million [S2].'):
    settings = settings.model_copy(update={'finwhiz_data_dir': settings.finwhiz_data_dir / str(uuid.uuid4())})
    store, jobs, job, provider = setup_job(settings)
    ingest(job, store, jobs, provider)
    provider.settings = settings.model_copy(update={'agentic_rag_enabled': True, 'agent_max_rounds': 2})
    queries, models = [], []
    def retrieve(*args, **kwargs):
        queries.append(dict(question=args[3], tickers=args[4], periods=args[5], **kwargs))
        return deepcopy(batches[min(len(queries)-1, len(batches)-1)])
    monkeypatch.setattr('finwhiz.research.retrieve', retrieve)
    def structured(system, payload, schema, model=None):
        if schema is AgentPlan:
            raise AppError('Scripted fallback plan')
        models.append(schema.__name__)
        if schema is AgentDecision:
            result = decisions[min(models.count('AgentDecision')-1, len(decisions)-1)]
            if isinstance(result, Exception):
                raise result
            return result
        assert schema is Answer
        return dict(status='answered', answer=answer, citation_ids=['S2'] if '[S2]' in answer else ['S1'],
                    chart=empty_chart(), calculations=[])
    provider.structured = structured
    scope = dict(status='resolved', tickers=['ACME'], filingDates=['2025-03-31'],
                 filings=[dict(ticker='ACME', reportDate='2025-03-31')], requestedPeriods=[])
    def run(**kwargs):
        return research(store, jobs.all(), provider, 'What revenue was reported?', ['ACME'],
                        scope_result=scope, **kwargs)
    return run, provider, queries, models


def test_missing_input_triggers_scoped_search_then_validated_answer(settings, monkeypatch):
    run, provider, queries, models = fixture(settings, monkeypatch,
        [decision('search', 'reported revenue'), decision()],
        [evidence(source('cost', 'Costs were 100 million.')), evidence(source('rev', 'Revenue was 125 million.'))])
    result = run()
    assert result['status'] == 'answered' and result['citation_ids'] == ['S2']
    assert [q['question'] for q in queries] == ['What revenue was reported?', 'reported revenue']
    assert all(q['filing_scope'] == {('ACME', '2025-03-31')} and q['tickers'] == ['ACME'] for q in queries)
    assert models == ['AgentDecision', 'AgentDecision', 'Answer']
    assert result['metrics']['agentRounds'] == 1 and result['metrics']['agentStopReason'] == 'evidence_sufficient'
    assert result['metrics']['agentTools'][0]['newEvidence'] == 1
    path = result['executionPath']
    assert path['agentic']
    assert [step['node'] for step in path['steps']] == [
        'policy', 'decompose', 'retrieve', 'assess_evidence', 'agent_tool', 'assess_evidence', 'generate', 'validate']
    tool = next(step for step in path['steps'] if step['node'] == 'agent_tool')
    assert 'Search: reported revenue' in tool['details']
    assert tool['filings'] == [{'ticker': 'ACME', 'reportDate': '2025-03-31'}]
    assert len(path['edges']) == len(path['steps']) - 1


def test_no_more_than_two_additional_retrievals(settings, monkeypatch):
    run, provider, queries, models = fixture(settings, monkeypatch,
        [decision('search', 'revenue'), decision('search', 'revenue details'), decision('search', 'more details')],
        [evidence(source('cost', 'Costs were 100 million.')), evidence(source('rev', 'Revenue was 125 million.')),
         evidence(source('extra', 'Revenue context was disclosed.'))])
    result = run()
    assert result['status'] == 'answered' and len(queries) == 3
    assert result['metrics']['agentRounds'] == 2 and result['metrics']['agentStopReason'] == 'round_limit'


def test_repeated_query_and_no_progress_stop_the_loop(settings, monkeypatch):
    run, _, queries, _ = fixture(settings, monkeypatch,
        [decision('search', 'revenue'), decision('search', ' REVENUE ')],
        [evidence(source('cost', 'Costs were 100 million.')), evidence(source('rev', 'Revenue was 125 million.'))])
    assert run()['metrics']['agentStopReason'] == 'repeated_action'
    assert len(queries) == 2
    run, _, queries, _ = fixture(settings, monkeypatch, [decision('search', 'cost details'), decision('search', 'cost table')],
        [evidence(source('cost', 'Costs were 100 million.'))], answer='Costs were 100 million [S1].')
    assert run()['metrics']['agentStopReason'] == 'no_new_evidence'
    assert len(queries) == 3


def test_agent_plan_precedes_retrieval_and_targeted_tools_are_applied(settings, monkeypatch):
    targeted = {**decision('search','reported revenue'), 'task_id':'T1', 'target_tickers':['ACME'],
                'filing_dates':['2025-03-31'], 'retrieval_mode':'lexical', 'top_k':3}
    run, provider, queries, _ = fixture(settings, monkeypatch, [targeted, decision()],
        [evidence(source('cost','Costs were 100 million.')), evidence(source('rev','Revenue was 125 million.'))])
    original=provider.structured
    def structured(system,payload,schema,model=None):
        if schema is AgentPlan:
            return {'tasks':[dict(ticker='ACME',report_date='2025-03-31',period='2025-Q1',metric='revenue',objective='Find quarterly revenue')]}
        return original(system,payload,schema,model)
    provider.structured=structured
    result=run()
    assert result['researchPlan']['origin']=='agent'
    assert queries[0]['question']=='Find quarterly revenue'
    assert queries[1]['retrieval_mode']=='lexical' and queries[1]['top_k']==3
    assert queries[1]['filing_scope']=={('ACME','2025-03-31')}
    assert result['researchPlan']['tasks'][0]['status']=='evidence_found'


def test_agent_cannot_expand_authorized_targets(settings, monkeypatch):
    bad={**decision('search','revenue'),'target_tickers':['TSLA'],'filing_dates':['2035-03-31']}
    run, _, queries, _=fixture(settings,monkeypatch,[bad],
        [evidence(source('rev','Revenue was 125 million.'))],answer='Revenue was 125 million [S1].')
    assert run()['metrics']['agentStopReason']=='invalid_tool_arguments'
    assert len(queries)==1


@pytest.mark.parametrize('bad', [decision('read_table', ids=['S999']), {'action': 'execute_code'}, AppError('Unavailable', 504)])
def test_bad_decision_or_unavailable_assessor_uses_validated_fallback(settings, monkeypatch, bad):
    run, _, queries, models = fixture(settings, monkeypatch, [bad],
        [evidence(source('rev', 'Revenue was 125 million.'))], answer='Revenue was 125 million [S1].')
    result = run()
    assert result['status'] == 'answered' and len(queries) == 1
    assert result['metrics']['agentStopReason'] in ('invalid_tool_arguments', 'assessment_unavailable')


def test_insufficient_evidence_never_generates_an_answer(settings, monkeypatch):
    run, _, queries, models = fixture(settings, monkeypatch, [decision('insufficient_evidence')],
        [evidence(source('cost', 'Costs were 100 million.'))])
    result = run()
    assert result['status'] == 'insufficient_evidence' and not result['sources']
    assert models == ['AgentDecision'] and len(queries) == 1


def test_local_calculation_and_rollback_skip_agent_models(settings, monkeypatch):
    local = dict(status='answered', answer='Revenue was 125 million [S1].', citation_ids=['S1'],
                 calculations=[], chart=empty_chart())
    run, _, queries, models = fixture(settings, monkeypatch, [],
        [{**evidence(source('rev', 'Revenue was 125 million.')), 'localAnswer': local, 'localMethod': 'fixture'}])
    result = run()
    assert result['status'] == 'answered' and models == []
    assert result['metrics']['agentStopReason'] == 'local_calculation'
    run, provider, _, models = fixture(settings, monkeypatch, [],
        [evidence(source('rev', 'Revenue was 125 million.'))], answer='Revenue was 125 million [S1].')
    provider.settings = provider.settings.model_copy(update={'agentic_rag_enabled': False})
    assert run()['status'] == 'answered' and models == ['Answer']


def test_answer_time_is_reserved_and_retrieval_only_is_unchanged(settings, monkeypatch):
    run, _, queries, models = fixture(settings, monkeypatch, [],
        [evidence(source('rev', 'Revenue was 125 million.'))], answer='Revenue was 125 million [S1].')
    context = ResearchTrace('limited', 60, started=time.monotonic()-45)
    result = run(context=context)
    assert result['metrics']['agentStopReason'] == 'answer_time_reserved' and models == ['Answer']
    models.clear()
    result = run(retrieval_only=True)
    assert 'answer' not in result and models == [] and not result['metrics']['agentic']


def test_table_tool_reads_only_selected_parent_and_preserves_source_identity(tmp_path):
    store = Storage(tmp_path)
    store.put_json('parents/job/doc', [dict(kind='table', text='Revenue | 125\nCosts | 100')])
    selected = {**source('table', 'Revenue | 125', 'S4'), 'chunk_type': 'table',
                'corpus_id': 'job', 'doc_id': 'doc', 'parent_id': 'doc:0', 'form': '10-Q', 'section_path': 'Operations'}
    result = read_tables(store, [selected], ['S4'])
    assert result['sources'][0]['id'] == 'S4' and 'Costs | 100' in result['sources'][0]['text']
    assert not read_tables(store, [selected], ['S999'])['sources']


def test_table_read_runs_through_graph_without_additional_search(settings, monkeypatch):
    table = {**source('table', 'A table excerpt.'), 'chunk_type': 'table', 'corpus_id': 'job',
             'doc_id': 'doc', 'parent_id': 'doc:0', 'form': '10-Q', 'section_path': 'Operations'}
    run, provider, queries, models = fixture(settings, monkeypatch,
        [decision('read_table', ids=['S1']), decision()], [evidence(table)],
        answer='Revenue was 125 million [S1].')
    store = Storage(provider.settings.finwhiz_data_dir)
    store.put_json('parents/job/doc', [dict(kind='text', text='(Dollars in millions)'),
                                      dict(kind='table', text='Revenue | 125\nCosts | 100')])
    # Match the selected source to the trusted parent index.
    table['parent_id'] = 'doc:1'
    result = run()
    assert result['status'] == 'answered' and len(queries) == 1
    assert result['metrics']['agentTools'][0]['tool'] == 'read_table'
    assert result['sources'][0]['id'] == 'S1' and 'Costs | 100' in result['sources'][0]['excerpt']


def test_tool_failure_preserves_existing_evidence_and_answer_budget(settings, monkeypatch):
    run, _, queries, _ = fixture(settings, monkeypatch, [decision('search', 'revenue details')],
        [evidence(source('rev', 'Revenue was 125 million.'))], answer='Revenue was 125 million [S1].')
    initial = __import__('finwhiz.research', fromlist=['retrieve']).retrieve
    def failed(*args, **kwargs):
        if args[3] == 'revenue details':
            raise AppError('Tool timeout', 504)
        return initial(*args, **kwargs)
    monkeypatch.setattr('finwhiz.research.retrieve', failed)
    result = run()
    assert result['status'] == 'answered' and result['metrics']['agentStopReason'] == 'tool_unavailable'


def test_nested_operation_budget_does_not_leak_into_answer_generation():
    from finwhiz.telemetry import operation_budget, remaining
    with operation_budget(0.1):
        assert 0 < remaining(30) <= 0.1
        with operation_budget(1):
            assert remaining(30) <= 0.1
    assert remaining(30) == 30


def test_new_evidence_gets_budget_priority_without_reusing_source_ids(tmp_path):
    store = Storage(tmp_path)
    previous = evidence(*[source(f'old{i}', 'Old context.', f'S{i+1}') for i in range(5)])
    catalog = {s['chunkId']: s['id'] for s in previous['sources']}
    incoming = evidence(source('new', 'Revenue was 125 million.'))
    merged, catalog = merge_evidence(store, previous, incoming, None, 10000, catalog)
    assert merged['sources'][0]['id'] == 'S6' and merged['sources'][0]['chunkId'] == 'new'
    assert len(merged['sources']) == 5 and len(catalog) == 6
