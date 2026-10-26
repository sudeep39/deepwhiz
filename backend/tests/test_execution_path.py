from finwhiz.execution_path import public_path, record_step
from finwhiz.telemetry import ResearchTrace


def test_lookup_receipts_attach_to_actual_retrieval_without_prior_year():
    trace = ResearchTrace('test', 60)
    state = {'scope': {'label': 'Tesla Q1 and Q2 2026', 'requestedPeriods': ['2026-Q1', '2026-Q2'],
                       'filings': [{'ticker': 'TSLA', 'reportDate': day} for day in ['2026-03-31', '2026-06-30']]}}
    record_step(trace, 'policy', state, {}, 1)
    for day in ['2026-03-31', '2026-06-30']:
        trace.record('filing_lookup', 10, ticker='TSLA', reportDate=day, outcome='ok')
    trace.record('provider_internal', 3, private='must not be returned')
    record_step(trace, 'retrieve', state, {'evidence': {'sources': [{}, {}]}}, 20)
    path = public_path(trace, True)
    assert len(path['steps']) == 2
    assert [r['reportDate'] for r in path['steps'][1]['lookups']] == ['2026-03-31', '2026-06-30']
    assert '2025' not in str(path)
    assert 'must not be returned' not in str(path)


def test_failed_tool_and_validation_are_not_reported_as_success():
    trace = ResearchTrace('test', 60)
    record_step(trace, 'agent_tool', {'agent_decision': {'action': 'search', 'query': 'cost of revenue'}},
                {'agent_stop_reason': 'tool_unavailable'}, 10)
    record_step(trace, 'validate', {}, {'issues': [], 'answer': {'status': 'insufficient_evidence'}}, 2)
    path = public_path(trace, True)
    assert [s['status'] for s in path['steps']] == ['unavailable', 'insufficient_evidence']


def test_operation_receipts_are_attached_and_filtered():
    trace = ResearchTrace('test', 60)
    trace.record('evidence_assessment', 90, model='test-model', provider='nebius', schema='AgentDecision', attempt=1,
                 outcome='ok', prompt='private prompt', api_key='secret')
    record_step(trace, 'assess_evidence', {}, {'agent_assessments': 1,
                'agent_decision': {'action': 'search', 'query': 'quarterly revenue', 'missing_evidence': ['Q2 revenue']}}, 100)
    trace.record('vector_db_call', 20, database='Pinecone', index='test-index', namespace='test-corpus',
                 filters={'ticker': {'$eq': 'TSLA'}}, topK=5, matches=3, outcome='completed')
    record_step(trace, 'agent_tool', {'agent_decision': {'action': 'search'}}, {'evidence': {'sources': [{}]}}, 30)
    path = public_path(trace, True)
    assessment, tool = path['steps']
    assert assessment['control'] == 'agent' and tool['control'] == 'agent_tool'
    assert assessment['operations'][0]['model'] == 'test-model'
    assert 'Missing facts: Q2 revenue' in assessment['details']
    assert tool['operations'][0]['matches'] == 3
    assert 'private prompt' not in str(path) and 'secret' not in str(path)


def test_skipped_assessment_is_application_controlled():
    trace = ResearchTrace('test', 60)
    record_step(trace, 'assess_evidence', {}, {'agent_decision': {'action': 'answer'}}, 1)
    assert public_path(trace, False)['steps'][0]['control'] == 'application'


def test_provider_records_real_vector_call_metadata(settings):
    from types import SimpleNamespace
    from finwhiz.providers import Providers
    from finwhiz.telemetry import current_trace
    provider = Providers(settings, settings.provider('openai'))
    provider.index = SimpleNamespace(pinecone=lambda path, body: {'matches': [{'id': 'one'}]})
    trace = ResearchTrace('test', 60)
    token = current_trace.set(trace)
    try:
        provider.pinecone('query', {'namespace': 'corpus', 'topK': 5,
                                   'filter': {'ticker': {'$eq': 'TSLA'}}, 'vector': [0.123]})
    finally:
        current_trace.reset(token)
        provider.close()
    event = trace.metrics()['operations'][0]
    assert event['stage'] == 'vector_db_call' and event['matches'] == 1
    assert event['filters'] == {'ticker': {'$eq': 'TSLA'}}
    assert 'vector' not in event


def test_outputs_snapshot_each_stage_without_leaking_arbitrary_state(caplog):
    trace = ResearchTrace('outputs', 60)
    state = {'api_key': 'secret-key', 'reasoning': 'hidden model reasoning'}
    plan = {'origin': 'agent', 'tasks': [{'id': 'T1', 'ticker': 'MU', 'metric': 'cost', 'objective': 'Find costs'}]}
    record_step(trace, 'decompose', state, {'research_plan': plan}, 2)
    source = {'id': 'S1', 'ticker': 'MU', 'text': 'PRIVATE_EXCERPT costs were 10 million.', 'api_key': 'secret-key'}
    with caplog.at_level('INFO', logger='finwhiz.telemetry'):
        record_step(trace, 'retrieve', state, {'evidence': {'sources': [source]}}, 3)
    record_step(trace, 'generate', state, {'answer': {'status': 'answered', 'answer': 'Draft cost 99 [S1]',
                'citation_ids': ['S1'], 'calculations': [], 'reasoning': 'hidden model reasoning'}}, 2)
    record_step(trace, 'validate', state, {'issues': ['Value 99 is unsupported'], 'answer': {'status': 'insufficient_evidence'}}, 1)
    source['text'] = 'Changed later'
    plan['tasks'][0]['objective'] = 'Changed later'
    path = public_path(trace, True)
    assert path['steps'][0]['outputs'][0]['data']['tasks'][0]['objective'] == 'Find costs'
    assert path['steps'][1]['outputs'][0]['data']['sources'][0]['excerpt'].startswith('PRIVATE_EXCERPT')
    assert path['steps'][2]['outputs'][0]['data']['text'] == 'Draft cost 99 [S1]'
    assert path['steps'][3]['outputs'][0]['data']['issues'] == ['Value 99 is unsupported']
    assert 'secret-key' not in str(path) and 'hidden model reasoning' not in str(path)
    assert 'PRIVATE_EXCERPT' not in caplog.text


def test_evidence_artifacts_are_bounded_and_tool_failures_do_not_claim_new_outputs():
    trace = ResearchTrace('bounded', 60)
    sources = [{'id': f'S{i}', 'text': 'x' * 2000} for i in range(20)]
    record_step(trace, 'retrieve', {}, {'evidence': {'sources': sources}}, 2)
    record_step(trace, 'agent_tool', {'evidence': {'sources': sources}}, {'agent_stop_reason': 'tool_unavailable'}, 1)
    initial, failed = public_path(trace, True)['steps']
    data = initial['outputs'][0]['data']
    assert data['total'] == 20 and len(data['sources']) == 12
    assert len(data['sources'][0]['excerpt']) == 1800 and data['sources'][0]['truncated']
    assert failed['outputs'][0]['data']['status'] == 'tool_unavailable'
