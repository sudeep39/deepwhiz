"""Public execution receipts: tool actions and outcomes, never model reasoning."""
from copy import deepcopy

TITLES = {
    'decompose': 'Decompose the research question',
    'policy': 'Resolve question and filing scope',
    'retrieve': 'Retrieve scoped evidence',
    'assess_evidence': 'Choose the next action',
    'agent_tool': 'Run additional evidence tool',
    'generate': 'Compose an evidence-based answer',
    'validate': 'Check citations and calculations',
    'repair': 'Repair unsupported claims',
    'validation_recovery': 'Choose evidence recovery from failed checks',
}


def answer_output(answer):
    """Bounded public answer fields, excluding model internals and prompt content."""
    return dict(status=answer.get('status'), text=str(answer.get('answer', ''))[:12000],
                truncated=len(str(answer.get('answer', ''))) > 12000,
                citations=list(answer.get('citation_ids', []))[:64],
                calculations=[{k: c[k] for k in ('label', 'operation', 'values', 'unit', 'source_ids', 'result') if k in c}
                              for c in answer.get('calculations', [])[:16]])


def step_outputs(stage, state, update):
    """Snapshot actual structured outputs; never expose hidden reasoning or arbitrary state."""
    combined = {**state, **update}
    if stage == 'policy':
        scope = combined.get('scope') or {}
        data = {k: scope[k] for k in ('status', 'label', 'tickers', 'requestedPeriods', 'filingDates') if k in scope}
        data['filings'] = [{k: f[k] for k in ('ticker', 'reportDate', 'form') if k in f} for f in scope.get('filings', [])[:32]]
        return [dict(title='Resolved research scope', kind='scope', data=data)]
    if stage == 'decompose':
        plan = combined.get('research_plan', {})
        return [dict(title='Research tasks', kind='tasks', data=dict(origin=plan.get('origin'),
                     tasks=[{k: t[k] for k in ('id', 'ticker', 'report_date', 'period', 'metric', 'objective') if k in t}
                            for t in plan.get('tasks', [])[:32]]))]
    if stage in ('retrieve', 'agent_tool'):
        # A failed tool did not produce fresh evidence; do not mislabel old sources.
        if 'evidence' not in update:
            return [dict(title='Tool outcome', kind='checks', data=dict(status=update.get('agent_stop_reason', 'not_run')))]
        sources = update['evidence'].get('sources', [])
        return [dict(title='Retrieved evidence' if stage == 'retrieve' else 'Evidence after tool merge', kind='evidence',
                     data=dict(total=len(sources), shown=min(len(sources), 12), sources=[
                         {**{k: s[k] for k in ('id', 'ticker', 'report_date', 'chunk_type', 'section_path') if k in s},
                          'excerpt': str(s.get('text', ''))[:1800], 'truncated': len(str(s.get('text', ''))) > 1800}
                         for s in sources[:12]]))]
    if stage in ('assess_evidence', 'validation_recovery'):
        decision = combined.get('agent_decision', {})
        return [dict(title='Selected action', kind='decision', data={
            **{k: decision[k] for k in ('action', 'task_id', 'query', 'source_ids', 'target_tickers', 'filing_dates',
                                       'retrieval_mode', 'top_k', 'missing_evidence') if k in decision},
            'stopCondition': combined.get('agent_stop_reason')})]
    if stage in ('generate', 'repair') and update.get('answer'):
        return [dict(title='Generated draft — not yet validated' if stage == 'generate' else 'Repair / fallback output',
                     kind='draft', data=answer_output(update['answer']))]
    if stage == 'validate':
        return [dict(title='Validation result', kind='checks', data=dict(
            status='partial' if combined.get('answer_partial') else 'failed' if update.get('issues') else 'passed',
            issues=update.get('issues', []), answerStatus=combined.get('answer', {}).get('status')))]
    return []


def record_step(trace, stage, state, update, duration):
    if trace is None:
        return
    combined = {**state, **update}
    scope = combined.get('scope') or {}
    details = []
    outcome = 'completed'
    if stage == 'decompose':
        plan = combined.get('research_plan', {})
        details = [f"{len(plan.get('tasks', []))} evidence tasks", f"Up to {combined.get('round_limit', 0)} additional tool rounds"]
        if plan.get('fallbackReason'):
            details.append('Plan fallback: ' + plan['fallbackReason'])
    elif stage == 'policy':
        details = [scope.get('label') or ', '.join(combined.get('tickers', []))]
        requested = scope.get('requestedPeriods', [])
        if requested:
            details.append('Requested periods: ' + ', '.join(requested))
        if update.get('answer'):
            outcome = update['answer'].get('status', 'stopped')
    elif stage in ('retrieve', 'agent_tool'):
        evidence = combined.get('evidence', {})
        if stage == 'agent_tool':
            decision = state.get('agent_decision', {})
            details.append('Tool: ' + decision.get('action', 'unknown'))
            if decision.get('query'):
                details.append('Search: ' + decision['query'][:500])
            if decision.get('task_id'):
                details.append('Research task: ' + decision['task_id'])
            if decision.get('action') == 'search':
                details.append(f"Retrieval: {decision.get('retrieval_mode', 'hybrid')} · top K {decision.get('top_k', 5)}")
            if decision.get('source_ids'):
                details.append('Table sources: ' + ', '.join(decision['source_ids']))
            if update.get('agent_stop_reason') == 'tool_unavailable':
                outcome = 'unavailable'
        details.append(f"{len(evidence.get('sources', []))} evidence sources available")
        if evidence.get('missingCoverage'):
            details.append(f"{len(evidence['missingCoverage'])} filing periods missing evidence")
        if evidence.get('rerankFallback') or evidence.get('retrievalDegraded'):
            details.append('Retrieval used a fallback')
        observations = update.get('agent_observations', [])
        if observations:
            details.append(f"{observations[-1].get('newEvidence', 0)} new or expanded sources")
    elif stage in ('assess_evidence', 'validation_recovery'):
        decision = combined.get('agent_decision', {})
        if stage == 'validation_recovery':
            details.extend(state.get('issues', []))
        details.append('Selected action: ' + decision.get('action', 'answer').replace('_', ' '))
        if decision.get('missing_evidence'):
            details.append('Missing facts: ' + ', '.join(decision['missing_evidence']))
        if decision.get('query'):
            details.append('Proposed search: ' + decision['query'])
        reason = combined.get('agent_stop_reason')
        if reason:
            details.append('Stop condition: ' + reason.replace('_', ' '))
        elif not combined.get('agent_assessments'):
            details.append('Agent assessment skipped; using the available evidence')
    elif stage == 'validate':
        issues = update.get('issues', [])
        details.append(f'{len(issues)} validation issues detected')
        details.extend(issues)
        outcome = 'needs_repair' if issues else 'completed'
        if combined.get('answer_partial'):
            outcome = 'partial'
            details.append('Only independently checked excerpts and calculations were retained; remaining claims were omitted')
        if combined.get('answer', {}).get('status') == 'insufficient_evidence':
            outcome = 'insufficient_evidence'
            details.append('A complete supported answer could not be verified')
    elif stage == 'generate':
        details.append('Answer status: ' + combined.get('answer', {}).get('status', 'unknown'))
        if combined.get('evidence', {}).get('localAnswer'):
            details.append('Used a verified local calculation')
    elif stage == 'repair':
        if update.get('repair_timed_out'):
            outcome = 'partial' if update.get('answer_partial') else 'insufficient_evidence'
            details.append('Answer repair model timed out')
            details.append('Retained independently checked findings only' if update.get('answer_partial') else 'No findings passed the fallback checks')
            details.extend(update.get('validation_errors', []))
        else:
            details.append('Rechecked claims against the retrieved evidence')
    control = 'application'
    if stage == 'decompose' and combined.get('research_plan', {}).get('origin') == 'agent':
        control = 'agent'
    if stage in ('assess_evidence', 'validation_recovery') and update.get('agent_assessments', 0) > state.get('agent_assessments', 0) and combined.get('agent_stop_reason') not in ('assessment_unavailable', 'invalid_tool_arguments', 'assessment_context_budget', 'answer_time_reserved'):
        control = 'agent'
    if stage == 'agent_tool':
        control = 'agent_tool'
    trace.record('execution_step', duration, node=stage, title=TITLES[stage], control=control,
                 outputs=deepcopy(step_outputs(stage, state, update)),
                 status=outcome, details=[str(d)[:600] for d in details if d],
                 filings=[{'ticker': f['ticker'], 'reportDate': f['reportDate']}
                          for f in scope.get('filings', [])] if stage in ('policy', 'retrieve', 'agent_tool') else [])


def public_path(context, agentic):
    events = context.metrics()['operations']
    steps, lookups, operations = [], [], []
    allowed = {'research_planning', 'evidence_assessment', 'answer_generation', 'answer_repair', 'reranking',
               'embedding_model_call', 'query_embedding_cache_hit', 'vector_db_call', 'bm25', 'model_usage'}
    for event in events:
        if event['stage'] == 'filing_lookup':
            lookups.append({key: event[key] for key in ('ticker', 'reportDate', 'durationMs', 'outcome')})
        elif event['stage'] in allowed:
            fields = {'stage', 'durationMs', 'outcome', 'attempt', 'model', 'provider', 'schema',
                      'dimensions', 'inputs', 'purpose', 'database', 'index', 'namespace',
                      'topK', 'filters', 'matches', 'operation', 'input_tokens', 'output_tokens', 'total_tokens'}
            operations.append({k: v for k, v in event.items() if k in fields})
        elif event['stage'] == 'execution_step':
            step = {k: value for k, value in event.items() if k != 'stage'}
            step['operations'], operations = operations, []
            if event['node'] in ('retrieve', 'agent_tool'):
                step['lookups'], lookups = lookups, []
            steps.append(step)
    for index, step in enumerate(steps):
        step['id'] = f'step-{index + 1}'
    return dict(version=1, agentic=agentic, architecture='Agent-selected evidence actions within an application-controlled workflow', steps=steps,
                edges=[{'from': a['id'], 'to': b['id']} for a, b in zip(steps, steps[1:])])
