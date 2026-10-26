"""Paired agent-on/off evaluation; paid calls require explicit --apply."""
import json
import statistics
import math
from pathlib import Path
from .config import AppError, digest, job_matches
from .storage import Storage, Jobs, now
from .providers import Providers
from .research import research, PROMPTS
from .fiscal import catalog_documents
from .scope import resolve_scope


def score_case(case, result):
    """Mechanical checks only; semantic claim support requires human review."""
    sources = result.get('sources', [])
    labels = case.get('evidenceLabels', [])
    hits = [any(s['ticker']==label['ticker'] and
                (not label.get('reportDate') or s['report_date']==label['reportDate']) and
                all(phrase.lower() in s.get('excerpt', '').lower() for phrase in label['contains'])
                for s in sources) for label in labels]
    metrics = result.get('metrics', {})
    gold_facts = case.get('expectedFacts', [])
    fact_hits = [any(all(fact.get(k)==gold.get(k) for k in ('ticker','period','metric','unit')) and
                     math.isclose(fact['value'],gold['value'],rel_tol=0,abs_tol=gold.get('tolerance',0.001))
                     for fact in result.get('financialEvidence', [])) for gold in gold_facts]
    operations = metrics.get('operations', [])
    lookups = [op for step in result.get('executionPath', {}).get('steps', []) for op in step.get('lookups', [])]
    forbidden = case.get('forbiddenFilingYears', [])
    return dict(statusCorrect=result.get('status')==case['expectedStatus'],
                evidenceLabelRecall=sum(hits)/len(hits) if hits else None,
                extractedFactRecall=sum(fact_hits)/len(fact_hits) if fact_hits else None,
                scopeViolation=any(int(op['reportDate'][:4]) in forbidden for op in lookups),
                latencyMs=metrics.get('latencyMs'), agentRounds=metrics.get('agentRounds', 0),
                modelCalls=sum(op['stage'] in ('research_planning','evidence_assessment','answer_generation','answer_repair','reranking') for op in operations),
                vectorQueries=sum(op['stage']=='vector_db_call' for op in operations),
                tokens=sum(op.get('total_tokens', 0) for op in operations if op['stage']=='model_usage'),
                unresolvedTasks=sum(t['status'] in ('unresolved','conflicting_values') for t in result.get('researchPlan', {}).get('tasks', [])))


def run_agent_evaluation(settings, suite_path, destination, apply=False, provider_name=None, repetitions=1):
    suite = json.loads(Path(suite_path).read_text(encoding='utf-8'))
    cases = suite['cases']
    if not cases or len({c['id'] for c in cases})!=len(cases) or not 1<=repetitions<=5:
        raise AppError('Use unique cases and 1–5 repetitions.')
    for case in cases:
        if not case.get('question') or not case.get('tickers') or case.get('expectedStatus') not in ('answered','insufficient_evidence','needs_clarification'):
            raise AppError('Invalid evaluation case.')
    plan = dict(cases=len(cases), trials=len(cases)*2*repetitions, conditions=['single_pass','agentic'],
                message='Calls the configured model and Pinecone; does not download or index filings. Run with --apply to execute.')
    if not apply:
        return plan
    destination = Path(destination)
    if destination.exists():
        raise AppError('Choose a new evaluation output directory; existing results are preserved.')
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store).all()
    config = settings.provider(provider_name)
    if not settings.configured(config):
        raise AppError('Configure the selected model provider and Pinecone before running live evaluations.')
    ready = [job for job in jobs if job['status']=='ready' and job_matches(job, config) and job.get('namespaces',{}).get('semantic')]
    docs = catalog_documents(ready, store)
    if not docs:
        raise AppError('No ready semantic corpus for this provider. Import and process the evaluation filings first.')
    destination.mkdir(parents=True)
    manifest = dict(startedAt=now(), suiteHash=digest(suite), settings=config.model_dump(),
                    agentRoundMaximum=settings.agent_max_rounds, timeoutSeconds=settings.chat_timeout_seconds,
                    corpus=[{k: doc.get(k) for k in ('ticker','reportDate','checksum','accession')} for doc in docs],
                    promptHashes={p.name:digest(p.read_bytes()) for p in PROMPTS.glob('*.txt')},
                    caveats='No automatic semantic accuracy certification. Review evidence, units, periods and claims manually. Token totals omit usage the provider did not report.')
    from .task_planning import PLAN_SYSTEM
    from .agentic import SYSTEM
    manifest['plannerHash'],manifest['assessorHash'] = digest(PLAN_SYSTEM),digest(SYSTEM)
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    rows, reviews = [], []
    for repetition in range(repetitions):
        for index,case in enumerate(cases):
            order = [False,True] if (index+repetition)%2==0 else [True,False]
            for enabled in order:
                condition='agentic' if enabled else 'single_pass'
                trial=dict(caseId=case['id'], repetition=repetition+1, condition=condition)
                trial_settings=settings.model_copy(update={'agentic_rag_enabled':enabled})
                provider=None
                try:
                    provider=Providers(trial_settings,config)
                    scope=resolve_scope(case['question'],docs,case['tickers'],case.get('periods',[]))
                    if scope['status']!='resolved':
                        result=dict(status=scope['status'],answer=scope['message'],scope=scope,sources=[])
                        trial['preflightOnly']=True
                    else:
                        result=research(store,ready,provider,case['question'],case['tickers'],case.get('periods',[]),'semantic',True,scope_result=scope)
                    trial.update(result=result,scores=score_case(case,result),success=True)
                    reviews.append(dict(caseId=case['id'],condition=condition,repetition=repetition+1,
                                        reviewer='',claimSupport=None,numericAccuracy=None,periodAndUnitAccuracy=None,
                                        answerCompleteness=None,notes=''))
                except Exception as error:
                    # Preserve failed trials in denominators; never invent a score or answer.
                    trial.update(success=False,error=type(error).__name__,scores={'statusCorrect':False,'scopeViolation':False})
                finally:
                    if provider:
                        provider.close()
                rows.append(trial)
                with (destination/'trials.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(trial)+'\n')
    summary={}
    for condition in ('single_pass','agentic'):
        subset=[r for r in rows if r['condition']==condition]
        by_id={case['id']:case for case in cases}
        label_scores=[r['scores'].get('evidenceLabelRecall') or 0 for r in subset if by_id[r['caseId']].get('evidenceLabels')]
        fact_scores=[r['scores'].get('extractedFactRecall') or 0 for r in subset if by_id[r['caseId']].get('expectedFacts')]
        latencies=[r['scores']['latencyMs'] for r in subset if r['scores'].get('latencyMs') is not None]
        summary[condition]=dict(trials=len(subset),failures=sum(not r['success'] for r in subset),
                                statusAccuracy=sum(r['scores']['statusCorrect'] for r in subset)/len(subset),
                                scopeViolations=sum(r['scores']['scopeViolation'] for r in subset),
                                medianLatencyMs=statistics.median(latencies) if latencies else None,
                                modelCalls=sum(r['scores'].get('modelCalls',0) for r in subset),
                                vectorQueries=sum(r['scores'].get('vectorQueries',0) for r in subset),
                                reportedTokens=sum(r['scores'].get('tokens',0) for r in subset))
        summary[condition].update(preflightOnly=sum(bool(r.get('preflightOnly')) for r in subset),
                                  evidenceLabelRecall=statistics.mean(label_scores) if label_scores else None,
                                  extractedFactRecall=statistics.mean(fact_scores) if fact_scores else None)
    (destination/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (destination/'manual-review.json').write_text(json.dumps(reviews,indent=2),encoding='utf-8')
    return dict(**plan,output=str(destination),summary=summary,manualReviewRequired=True)


def score_agent_reviews(directory):
    directory=Path(directory)
    trials=[json.loads(line) for line in (directory/'trials.jsonl').read_text(encoding='utf-8').splitlines()]
    reviews=json.loads((directory/'manual-review.json').read_text(encoding='utf-8'))
    dimensions=['claimSupport','numericAccuracy','periodAndUnitAccuracy','answerCompleteness']
    mapping={}
    for review in reviews:
        key=(review['caseId'],review['condition'],review['repetition'])
        if key in mapping:
            raise AppError('Duplicate manual review entry.')
        mapping[key]=review
        for dimension in dimensions:
            value=review.get(dimension)
            if value is not None and (type(value) not in (int,float) or not 0<=value<=1):
                raise AppError('Manual review dimensions must be numbers between 0 and 1 or null.')
    report={}
    for condition in ('single_pass','agentic'):
        rows=[row for row in trials if row['condition']==condition]
        pending=0;scores={dimension:[] for dimension in dimensions}
        for row in rows:
            review=mapping.get((row['caseId'],condition,row['repetition']))
            if not row['success']:
                for dimension in dimensions:scores[dimension].append(0)
            elif not review or not review.get('reviewer') or any(review.get(d) is None for d in dimensions):
                pending+=1
            else:
                for dimension in dimensions:scores[dimension].append(review[dimension])
        report[condition]=dict(trials=len(rows),pendingReviews=pending,
                               scores={d:statistics.mean(values) if values and not pending else None for d,values in scores.items()})
    report['agenticMinusSinglePass']={d:report['agentic']['scores'][d]-report['single_pass']['scores'][d]
        if report['agentic']['scores'][d] is not None and report['single_pass']['scores'][d] is not None else None for d in dimensions}
    (directory/'review-summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
