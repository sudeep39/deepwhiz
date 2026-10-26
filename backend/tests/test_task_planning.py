from types import SimpleNamespace
from finwhiz.task_planning import create_plan, round_budget, task_progress
from finwhiz.agent_evaluation import score_case, run_agent_evaluation
import json


def test_planner_rejects_added_year_and_retains_coverage(settings):
    scope=dict(tickers=['TSLA'],requestedPeriods=['2026-Q1'],filings=[dict(ticker='TSLA',reportDate='2026-03-31')])
    provider=SimpleNamespace(settings=settings,structured=lambda *args:{'tasks':[dict(ticker='TSLA',report_date='2025-03-31',period='2025-Q1',metric='revenue',objective='Find revenue')]})
    plan=create_plan(provider,'Revenue in Q1 2026',scope)
    assert plan['origin']=='application' and plan['fallbackReason']=='planning_unavailable'
    assert plan['tasks'][0]['report_date']=='2026-03-31'
    assert round_budget({'tasks':[{}]*12},6)==6
    assert round_budget({'tasks':[{}]},6)==2
    assert round_budget({'tasks':[{}]*12},0)==0


def test_progress_does_not_confuse_passage_presence_with_verified_fact(monkeypatch):
    plan={'tasks':[dict(id='T1',ticker='TSLA',report_date='2026-06-30',period='2026-Q2',metric='revenue')]}
    source=dict(id='S1',ticker='TSLA',report_date='2026-06-30',text='A financial discussion')
    assert task_progress(plan,[source])[0]['status']=='evidence_found'
    facts=[dict(source_id='S1',ticker='TSLA',period='2026-Q2',metric='revenue',value=1,unit='millions')]
    monkeypatch.setattr('finwhiz.task_planning.facts_payload',lambda sources:facts)
    assert task_progress(plan,[source])[0]['status']=='fact_located'
    facts.append({**facts[0],'value':2})
    assert task_progress(plan,[source])[0]['status']=='conflicting_values'


def test_eval_dry_run_has_no_provider_calls(settings,tmp_path):
    suite=tmp_path/'suite.json';suite.write_text(json.dumps({'cases':[dict(id='one',question='Revenue?',tickers=['TSLA'],expectedStatus='answered')]}))
    output=tmp_path/'results'
    result=run_agent_evaluation(settings,suite,output)
    assert result['trials']==2 and not output.exists()


def test_eval_counts_wrong_scope_and_failures():
    case=dict(expectedStatus='answered',forbiddenFilingYears=[2025],evidenceLabels=[dict(ticker='TSLA',contains=['revenue'])])
    result=dict(status='insufficient_evidence',sources=[],executionPath={'steps':[{'lookups':[{'reportDate':'2025-03-31'}]}]})
    scores=score_case(case,result)
    assert not scores['statusCorrect'] and scores['scopeViolation'] and scores['evidenceLabelRecall']==0


def test_retrieval_modes_control_actual_embedding_and_vector_calls(settings):
    from test_workflows import setup_job
    from finwhiz.ingestion import ingest
    from finwhiz.retrieval import retrieve
    from finwhiz.telemetry import ResearchTrace,current_trace
    store,jobs,job,provider=setup_job(settings)
    ingest(job,store,jobs,provider)
    calls=[];original_embed=provider.embed;original_pinecone=provider.pinecone
    def embed(*args,**kwargs):
        calls.append('embedding');return original_embed(*args,**kwargs)
    def pinecone(path,body):
        if path=='query':calls.append(('query',body['topK']))
        return original_pinecone(path,body)
    provider.embed=embed;provider.pinecone=pinecone
    retrieve(store,jobs.all(),provider,'revenue',['ACME'],[],'fixed',False,local_first=False,retrieval_mode='lexical')
    assert calls==[]
    trace=ResearchTrace('mode-test',60);token=current_trace.set(trace)
    try:
        retrieve(store,jobs.all(),provider,'revenue',['ACME'],[],'fixed',False,local_first=False,retrieval_mode='dense',top_k=3)
    finally:
        current_trace.reset(token)
    assert 'embedding' in calls and ('query',3) in calls
    assert not any(op['stage']=='bm25' for op in trace.metrics()['operations'])


def test_live_eval_retains_failed_trials_and_writes_review_template(settings,tmp_path,monkeypatch):
    import finwhiz.agent_evaluation as module
    settings=settings.model_copy(update={'openai_api_key':'offline','pinecone_api_key':'offline'})
    monkeypatch.setattr(module,'Storage',lambda path:object())
    monkeypatch.setattr(module,'Jobs',lambda store:SimpleNamespace(all=lambda:[{'status':'ready','namespaces':{'semantic':'test'}}]))
    monkeypatch.setattr(module,'job_matches',lambda *args:True)
    monkeypatch.setattr(module,'catalog_documents',lambda *args:[{'ticker':'TSLA','reportDate':'2026-06-30'}])
    monkeypatch.setattr(module,'resolve_scope',lambda *args:{'status':'resolved'})
    monkeypatch.setattr(module,'Providers',lambda configuration,config:SimpleNamespace(settings=configuration,close=lambda:None))
    def research(store,jobs,provider,*args,**kwargs):
        if provider.settings.agentic_rag_enabled:
            raise RuntimeError('simulated failure')
        return dict(status='answered',answer='A cited answer',sources=[],metrics={'latencyMs':10,'operations':[]})
    monkeypatch.setattr(module,'research',research)
    suite=tmp_path/'suite.json';suite.write_text(json.dumps({'cases':[dict(id='one',question='Revenue?',tickers=['TSLA'],expectedStatus='answered')]}))
    output=tmp_path/'results';result=run_agent_evaluation(settings,suite,output,True)
    assert result['summary']['agentic']['failures']==1
    assert result['summary']['agentic']['statusAccuracy']==0
    assert len((output/'trials.jsonl').read_text().splitlines())==2
    assert len(json.loads((output/'manual-review.json').read_text()))==1
    reviews=json.loads((output/'manual-review.json').read_text())
    reviews[0].update(reviewer='offline-reviewer',claimSupport=1,numericAccuracy=1,periodAndUnitAccuracy=1,answerCompleteness=1)
    (output/'manual-review.json').write_text(json.dumps(reviews))
    scored=module.score_agent_reviews(output)
    assert scored['agentic']['scores']['claimSupport']==0
    assert scored['agenticMinusSinglePass']['claimSupport']==-1
