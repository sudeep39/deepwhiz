"""Bounded research graph: policy, retrieval, generation, validation and repair."""

import re
import time
import uuid
from pathlib import Path
from typing import TypedDict
from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from .config import AppError, job_matches, compact
from .finance import empty_chart
from .retrieval import retrieve
from .schemas import Answer, Repair
from .answer_validation import validation_issues, finalize, referenced_sources, coverage_issues, supported_repair, supported_draft
from .telemetry import ResearchTrace, current_trace, span, operation_budget
from .documents import token_count
from .fiscal import catalog_documents
from .scope import resolve_scope
from .calculation_handlers import try_calculation
from .financial_facts import facts_payload
from .domain_types import ResearchProvider
from .storage import Storage
from . import agentic
from .execution_path import record_step, public_path, answer_output
from .task_planning import create_plan, round_budget, task_progress, financial_evidence
from .evidence_gaps import evidence_gaps

PROMPTS = Path(__file__).parent / "prompts"


def plan_question(question, history):
    if re.fullmatch(
        r"(?:what(?:'s| is)|show(?: me)?|tell me about)\s+(?:the\s+)?(?:profit|performance|growth)\s*[?.!]*",
        question,
        re.I,
    ):
        return dict(
            status="needs_clarification",
            answer="Which metric and reporting period should I use? For example, net income, operating income, revenue growth, or gross margin for a particular quarter.",
        )
    if re.search(
        r"\b(?:predict|forecast)\b.*\b(?:stock|share) price\b|\b(?:stock|share) price\b.*\b(?:next|tomorrow|will)\b|\b(?:buy|sell) (?:the )?(?:stock|shares)\b",
        question,
        re.I,
    ):
        return dict(
            status="insufficient_evidence",
            answer="Historical SEC filings cannot establish future stock prices or a trading recommendation. I can compare the disclosed financial performance and risks instead.",
        )
    if re.search(r"\b(?:earnings call|transcript)\b", question, re.I) and not re.search(
        r"\b(?:filing|10-[kq])\b", question, re.I
    ):
        return dict(
            status="insufficient_evidence",
            answer="This corpus contains SEC filings, not earnings-call transcripts. Uploading transcripts is outside the current 10-K/10-Q ingestion scope; ask about disclosures in the selected filings instead.",
        )
    previous = next(
        (
            m
            for m in reversed(history)
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ),
        None,
    )
    followup = re.search(
        r"^(?:and\b|what about\b|how about\b|why\b|compare (?:it|that|those)\b)|\b(?:that metric|those figures|the same metric|that period)\b",
        question,
        re.I,
    )
    return dict(
        retrievalQuestion=(
            f"{previous['content'][:1500]}\nFollow-up: {question}"
            if followup and previous
            else question
        )
    )


def empty_answer(status, answer):
    return dict(
        status=status,
        answer=answer,
        citation_ids=[],
        sources=[],
        chart=empty_chart(),
        calculations=[],
    )


class ResearchState(TypedDict, total=False):
    """Per-invocation LangGraph state for research and agent tool rounds.

    research() initializes it; build_graph node functions return partial
    updates. RESEARCH_GRAPH is shared, but this state and runtime are not."""
    runtime: 'ResearchRuntime'
    question: str
    tickers: list[str]
    periods: list[str]
    strategy: str
    rerank: bool
    history: list[dict]
    retrieval_only: bool
    trace: bool
    plan: dict
    evidence: dict
    answer: dict
    issues: list[str]
    repaired: bool
    validation_terminal: bool
    validation_recovery_attempted: bool
    validation_errors: list[str]
    repair_sentences: list[dict]
    answer_partial: bool
    repair_timed_out: bool
    deterministic: bool
    scope: dict
    deterministic_method: str
    agent_rounds: int
    agent_decision: dict
    agent_observations: list[dict]
    agent_attempts: list[str]
    agent_stop_reason: str
    agent_assessments: int
    evidence_budget: int
    source_catalog: dict[str, str]
    research_plan: dict
    round_limit: int
    tool_selection: dict


@dataclass(frozen=True)
class ResearchRuntime:
    """Request dependencies supplied to the shared RESEARCH_GRAPH.

    research() constructs it from the blob store, a job snapshot and the
    selected provider. Nodes read these dependencies from state.runtime."""
    store: Storage
    jobs: list[dict]
    provider: ResearchProvider


def build_graph():
    """Compile research/agent nodes and bounded repair routing once at import.

    RESEARCH_GRAPH is reused by research(); each invocation supplies its own
    ResearchState and ResearchRuntime. Nested functions below are LangGraph
    callbacks, not independent HTTP endpoints.
    """
    def policy(state):
        """Graph entry: enforce question policy, resolve scope and check ready jobs."""
        runtime = state['runtime']
        store, jobs, provider = runtime.store, runtime.jobs, runtime.provider
        plan = plan_question(state["question"], state["history"])
        scope = state.get('scope')
        if not state['retrieval_only'] and not plan.get('status'):
            if scope is None:
                documents = catalog_documents([job for job in jobs if job_matches(job, provider.config) and job.get('namespaces', {}).get(state['strategy'])], store)
                if documents:
                    scope = resolve_scope(state['question'], documents, state['tickers'], state['periods'], state['history'])
            if scope:
                if scope['status'] != 'resolved':
                    return {'plan': plan, 'scope': scope, 'answer': empty_answer(scope['status'], scope['message'])}
        if (
            not plan.get("status")
            and not state["retrieval_only"]
            and not any(
                j["status"] == "ready"
                and j.get("namespaces", {}).get(state["strategy"])
                and job_matches(j, provider.config)
                for j in jobs
            )
        ):
            return {
                "plan": plan,
                "answer": empty_answer(
                    "insufficient_evidence",
                    "No indexed filings are available for this chunking strategy.",
                ),
            }
        return {
            "plan": plan,
            **({'scope': scope, 'tickers': scope['tickers'], 'periods': scope['filingDates']} if scope else {}),
            **(
                {"answer": empty_answer(plan["status"], plan["answer"])}
                if plan.get("status") and not state["retrieval_only"]
                else {}
            ),
        }

    def decompose(state):
        provider = state['runtime'].provider
        plan = create_plan(provider, state['question'], state.get('scope'),
                           agentic.enabled(provider) and not state['retrieval_only'])
        maximum = getattr(getattr(provider, 'settings', None), 'agent_max_rounds', 2)
        return {'research_plan': plan, 'round_limit': round_budget(plan, maximum)}

    def retrieval(state):
        """Graph/tool callback: run scoped hybrid retrieval under the answer budget."""
        runtime = state['runtime']
        store, jobs, provider = runtime.store, runtime.jobs, runtime.provider
        settings = getattr(provider, 'settings', None)
        budget = settings.answer_max_tokens if settings else 20000
        overhead = token_count((PROMPTS / 'answer.txt').read_text(encoding='utf-8')) + token_count(compact({
            'question': state['question'], 'tickers': state['tickers'], 'requestedPeriods': state['periods'],
            'resolvedScope': state.get('scope'), 'history': state['history'][-4:]})) + 1000
        selection = state.get('tool_selection', {})
        selected_tickers = selection.get('target_tickers') or state['tickers']
        selected_dates = selection.get('filing_dates') or state['periods']
        pairs = {(f['ticker'], f['reportDate']) for f in state['scope']['filings']
                 if f['ticker'] in selected_tickers and (not selected_dates or f['reportDate'] in selected_dates)} if state.get('scope') else None
        query = state['plan'].get('retrievalQuestion', state['question'])
        if not state.get('agent_rounds') and state.get('research_plan', {}).get('origin') == 'agent':
            query = '\n'.join(t['objective'] for t in state['research_plan']['tasks'])
        result = retrieve(
            store,
            jobs,
            provider,
            query,
            selected_tickers,
            selected_dates,
            state["strategy"],
            state["rerank"] if not state.get('agent_rounds') else False,
            filing_scope=pairs,
            resolved_scope=state.get('scope'),
            evidence_budget=max(0, budget - overhead),
            local_first=not state['retrieval_only'] and not state.get('agent_rounds') and (not state.get('scope') or pairs == {(f['ticker'],f['reportDate']) for f in state['scope']['filings']}),
            retrieval_mode=selection.get('retrieval_mode', 'hybrid'), top_k=selection.get('top_k', 5),
            calculation_question=state['question'],
        )
        if not state.get('agent_rounds') and state.get('scope'):
            represented = {(s['ticker'], s['report_date']) for s in result.get('sources', [])}
            missing = [f for f in state['scope']['filings'] if (f['ticker'], f['reportDate']) not in represented]
            if missing:
                result = {**result, 'missingCoverage': missing,
                          'coverageReason': result.get('coverageReason') or 'no_supporting_evidence'}
        return {'evidence': result, 'evidence_budget': max(0, budget - overhead),
                'source_catalog': {s['chunkId']: s['id'] for s in result.get('sources', []) if s.get('chunkId')}}

    def assess_evidence(state):
        """Graph callback: ask the bounded agent for its next action, or skip it."""
        provider = state['runtime'].provider
        evidence = state['evidence']
        if not agentic.enabled(provider) or evidence.get('localAnswer') or state.get('agent_stop_reason'):
            return {'agent_decision': {'action': 'answer'}}
        if evidence.get('coverageReason') == 'evidence_budget':
            return {'agent_stop_reason': 'evidence_budget', 'agent_decision': {'action': 'answer'}}
        evidence = {**evidence, 'researchTasks': task_progress(state.get('research_plan', {}), evidence.get('sources', []))}
        decision, reason = agentic.assess(provider, state['plan'].get('retrievalQuestion', state['question']), state.get('scope'),
                                          evidence, state.get('agent_observations', []))
        assessments = state.get('agent_assessments', 0) + 1
        if reason:
            return {'agent_stop_reason': reason, 'agent_decision': {'action': 'answer'},
                    'agent_assessments': assessments}
        if decision['action'] in ('search', 'read_table'):
            if state.get('agent_rounds', 0) >= state.get('round_limit', provider.settings.agent_max_rounds):
                reason = 'round_limit'
            elif agentic.action_key(decision) in state.get('agent_attempts', []):
                reason = 'repeated_action'
        else:
            reason = 'evidence_sufficient' if decision['action'] == 'answer' else 'insufficient_evidence'
        return {'agent_decision': decision, 'agent_assessments': assessments,
                **({'agent_stop_reason': reason} if reason else {})}

    def agent_tool(state):
        """Graph callback: execute an authorized search/table read and merge evidence."""
        runtime, decision = state['runtime'], state['agent_decision']
        trace = current_trace.get()
        rounds = state.get('agent_rounds', 0) + 1
        attempts = state.get('agent_attempts', []) + [agentic.action_key(decision)]
        seconds = trace.remaining() - runtime.provider.settings.agent_answer_reserve_seconds if trace else 15
        if seconds <= 1:
            return {'agent_stop_reason': 'answer_time_reserved', 'agent_decision': {'action': 'answer'}}
        previous = state['evidence']
        try:
            with operation_budget(seconds):
                if decision['action'] == 'search':
                    incoming = retrieval({**state, 'agent_rounds': rounds,
                                          'tool_selection': decision,
                                          'plan': {**state['plan'], 'retrievalQuestion': decision['query']}})['evidence']
                else:
                    incoming = agentic.read_tables(runtime.store, previous.get('sources', []), decision['source_ids'])
            merged, catalog = agentic.merge_evidence(runtime.store, previous, incoming,
                                                     state.get('scope'), state['evidence_budget'], state['source_catalog'])
        except AppError:
            if trace:
                trace.remaining()
            return {'agent_rounds': rounds, 'agent_attempts': attempts, 'agent_stop_reason': 'tool_unavailable'}
        old = {s['chunkId']: s['text'] for s in previous.get('sources', [])}
        gained = sum(old.get(s['chunkId']) != s['text'] for s in merged.get('sources', []))
        observation = dict(tool=decision['action'], taskId=decision.get('task_id', ''), newEvidence=gained,
                           evidenceCount=len(merged.get('sources', [])), missingCoverage=merged.get('missingCoverage', []))
        if trace:
            trace.record('agent_tool_result', 0, round=rounds, **observation)
        return {'evidence': merged, 'source_catalog': catalog, 'agent_rounds': rounds, 'agent_attempts': attempts,
                'agent_observations': state.get('agent_observations', []) + [observation],
                **({'agent_stop_reason': 'no_new_evidence'} if not gained and rounds >= state.get('round_limit', 2) else {})}

    def generate(state):
        """Graph callback: use a verified local calculation or request an Answer."""
        provider = state['runtime'].provider
        if state.get('agent_stop_reason') == 'insufficient_evidence':
            return {'answer': empty_answer('insufficient_evidence',
                'The selected filings do not provide sufficient evidence for every part of this question. Try a narrower question or add the missing filings.')}
        if state['evidence'].get('missingCoverage'):
            if state['evidence'].get('coverageReason') == 'no_supporting_evidence':
                return {'answer': empty_answer('insufficient_evidence',
                    'I could not find supporting evidence for every requested company and period in the selected filings.')}
            return {'answer': empty_answer('needs_clarification',
                'I cannot fit supporting evidence for every requested company and period. Select fewer periods or ask a narrower question.')}
        if state['evidence'].get('localAnswer'):
            return {'answer': state['evidence']['localAnswer'], 'deterministic': True,
                    'deterministic_method': state['evidence']['localMethod']}
        sources = state["evidence"]["sources"]
        if not sources:
            return {
                "answer": empty_answer(
                    "insufficient_evidence",
                    "I could not find supporting evidence in the selected filings. Try a different period or a more specific financial metric.",
                )
            }
        deterministic, method = try_calculation(state['question'], sources, state.get('scope'))
        if deterministic:
            return {"answer": deterministic, "deterministic": True, 'deterministic_method': method}
        settings = getattr(provider, "settings", None)
        budget = settings.answer_max_tokens if settings else 20000
        system = (PROMPTS / "answer.txt").read_text(encoding="utf-8")
        payload = dict(
            question=state["question"],
            tickers=state["tickers"],
            requestedPeriods=state["periods"],
            resolvedScope=state.get('scope'),
            history=state["history"][-4:],
            retrievedCoverage=list(
                dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources)
            ),
            evidence=sources,
            financialFacts=facts_payload(sources),
            researchTasks=task_progress(state.get('research_plan', {}), sources),
            validationFeedback=state.get('validation_errors', []),
            evidenceAssessment=dict(action=state.get('agent_decision', {}).get('action'),
                                    missingEvidence=state.get('agent_decision', {}).get('missing_evidence', []),
                                    stopReason=state.get('agent_stop_reason')),
        )
        input_tokens = token_count(system) + token_count(compact(payload))
        measurement = current_trace.get()
        if measurement:
            measurement.record(
                "answer_input", 0, inputTokens=input_tokens, evidenceCount=len(sources)
            )
        if input_tokens > budget:
            return {'answer': empty_answer('needs_clarification',
                'The complete evidence exceeds the answer budget. Select fewer periods or ask a narrower question.')}
        answer = provider.structured(system, payload, Answer)
        return {"answer": answer}

    def validate(state):
        """Graph callback: check facts/citations/coverage, finalize, or route to repair."""
        answer = state["answer"]
        issues = validation_issues(
            answer, state["evidence"]["sources"], not state.get("deterministic", False)
        )
        issues += coverage_issues(answer, state['evidence']['sources'], state.get('scope'))
        if state.get('repaired') and answer['status'] != 'answered':
            issues = list(dict.fromkeys(issues + state.get('issues', [])))
        measurement = current_trace.get()
        if measurement:
            measurement.record('evidence_validation', 0, issueCount=len(issues), issues=issues,
                               repaired=bool(state.get('repaired')), outcome='failed' if issues else 'passed')
        if issues and state.get("repaired"):
            partial = supported_repair(state.get('repair_sentences', []), answer.get('calculations', []),
                                       state['evidence']['sources'])
            return {'issues': issues, 'validation_errors': issues, 'validation_terminal': True,
                    'answer_partial': bool(partial), 'answer': partial or empty_answer(
                'insufficient_evidence',
                'The retrieved evidence did not pass the answer checks, even after repair. No unverified claims are shown. See Validation details for the specific citation, value, or coverage gaps.'
            )}
        return {
            "issues": issues,
            'validation_errors': issues,
            "answer": answer if issues else finalize(answer, state["evidence"]["sources"]),
        }

    def validation_recovery(state):
        """Let the agent choose a bounded evidence action from actual failed checks."""
        observation = dict(tool='validation', issues=state['issues'],
                           instruction='Find missing supporting evidence or choose answer to repair using existing evidence.')
        observations = state.get('agent_observations', []) + [observation]
        update = assess_evidence({**state, 'agent_stop_reason': None, 'agent_observations': observations})
        return {**update, 'validation_recovery_attempted': True, 'agent_observations': observations,
                'agent_stop_reason': update.get('agent_stop_reason')}

    def repair(state):
        """Graph callback: request one Repair; the next validation is still mandatory."""
        provider = state['runtime'].provider
        system = (PROMPTS / 'repair.txt').read_text(encoding='utf-8')
        payload = dict(
                question=state["question"],
                tickers=state["tickers"],
                requestedPeriods=state["periods"],
                resolvedScope=state.get('scope'),
                evidence=state["evidence"]["sources"],
                financialFacts=facts_payload(state['evidence']['sources']),
                validationErrors=state["issues"],
                previousAnswer=state['answer'],
            )
        settings = getattr(provider, 'settings', None)
        budget = settings.answer_max_tokens if settings else 20000
        if token_count(system) + token_count(compact(payload)) > budget:
            return {'repaired': True, 'deterministic': False, 'answer': empty_answer('needs_clarification',
                'The evidence and validation details exceed the answer budget. Select fewer periods or ask a narrower question.')}
        try:
            result = provider.structured(system, payload, Repair)
        except AppError as error:
            if error.status != 504:
                raise
            trace = current_trace.get()
            if trace:
                trace.remaining()  # Never hide request cancellation or the overall deadline.
            partial = supported_draft(state['answer'], state['evidence']['sources'])
            return dict(repaired=True, deterministic=False, repair_timed_out=True,
                        validation_terminal=True, answer_partial=bool(partial),
                        validation_errors=state['issues'],
                        answer=partial or empty_answer('insufficient_evidence',
                            'The answer repair model timed out. No findings passed the fallback checks, so no unverified claims are shown. See Validation details for the outstanding gaps.'))
        return {
            "repaired": True,
            'repair_sentences': result['sentences'],
            "deterministic": False,
            "answer": {
                **state["answer"],
                "answer": " ".join(
                    s["text"] + " " + " ".join(f"[{i}]" for i in s["source_ids"])
                    for s in result["sentences"]
                ),
                "citation_ids": list(
                    dict.fromkeys(i for s in result["sentences"] for i in s["source_ids"])
                ),
                "calculations": result["calculations"],
                # Repair does not regenerate charts. Never carry an invalid chart forward.
                "chart": empty_chart(),
            },
        }

    graph = StateGraph(ResearchState)
    for name, fn in [
        ("policy", policy),
        ('decompose', decompose),
        ("retrieve", retrieval),
        ('assess_evidence', assess_evidence),
        ('agent_tool', agent_tool),
        ("generate", generate),
        ("validate", validate),
        ('validation_recovery', validation_recovery),
        ("repair", repair),
    ]:

        def timed_node(function, stage):
            def run(state):
                started = time.monotonic()
                with span(stage):
                    update = function(state)
                record_step(current_trace.get(), stage, state, update, (time.monotonic() - started) * 1000)
                return update

            return run

        graph.add_node(name, timed_node(fn, name))
    graph.add_edge(START, "policy")
    graph.add_conditional_edges("policy", lambda s: END if s.get("answer") else "decompose")
    graph.add_edge('decompose', 'retrieve')
    graph.add_conditional_edges("retrieve", lambda s: END if s["retrieval_only"] else 'assess_evidence')
    graph.add_conditional_edges('assess_evidence', lambda s: 'agent_tool' if (
        not s.get('agent_stop_reason') and s.get('agent_decision', {}).get('action') in ('search', 'read_table')) else 'generate')
    graph.add_edge('agent_tool', 'assess_evidence')
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges('validate', lambda s: END if not s['issues'] or s.get('validation_terminal') else (
        'validation_recovery' if agentic.enabled(s['runtime'].provider) and not s.get('repaired')
        and not s.get('validation_recovery_attempted') and s.get('agent_rounds', 0) < s.get('round_limit', 0)
        else 'repair'))
    graph.add_conditional_edges('validation_recovery', lambda s: 'agent_tool' if (
        not s.get('agent_stop_reason') and s.get('agent_decision', {}).get('action') in ('search', 'read_table')) else 'repair')
    graph.add_conditional_edges('repair', lambda s: END if s.get('validation_terminal') else 'validate')
    return graph.compile()


RESEARCH_GRAPH = build_graph()


def research(
    store,
    jobs,
    provider,
    question,
    tickers,
    periods=None,
    strategy="fixed",
    rerank=True,
    history=None,
    retrieval_only=False,
    trace=False,
    context=None,
    scope_result=None,
):
    """Execute one isolated research request on the shared compiled graph.

    Called by ResearchEndpoints, CLI evaluation and offline tests. jobs is a
    registry snapshot; tickers/periods bound retrieval; scope_result may supply
    an already-resolved scope. Returns the validated answer, cited excerpts
    and metrics, or ranking/metrics only when retrieval_only is true. context
    carries deadline/cancellation; otherwise a ResearchTrace is created here.
    """
    started = time.monotonic()
    context = context or ResearchTrace(
        str(uuid.uuid4()), getattr(getattr(provider, "settings", None), "chat_timeout_seconds", 60)
    )
    token = current_trace.set(context)
    try:
        state = RESEARCH_GRAPH.invoke(
            dict(
                runtime=ResearchRuntime(store, jobs, provider),
                question=question,
                tickers=tickers,
                periods=periods or [],
                strategy=strategy,
                rerank=rerank,
                history=history or [],
                retrieval_only=retrieval_only,
                trace=trace,
                **({'scope': scope_result} if scope_result else {}),
            ), config={'recursion_limit': 50}
        )
    finally:
        current_trace.reset(token)
    evidence = state.get("evidence", {})
    sources = evidence.get("sources", [])
    metrics = dict(
        **context.metrics(),
        aiProvider=provider.config.provider,
        latencyMs=round((time.monotonic() - started) * 1000),
        strategy=strategy,
        rerank=rerank,
        rerankFallback=evidence.get("rerankFallback", False),
        rerankFallbackReason=evidence.get("rerankFallbackReason"),
        retrievalDegraded=evidence.get('retrievalDegraded', False),
        degradationReasons=evidence.get('degradationReasons', []),
        missingCoverage=evidence.get('missingCoverage', []),
        agentic=agentic.enabled(provider) and not retrieval_only,
        agentRounds=state.get('agent_rounds', 0),
        agentRoundBudget=state.get('round_limit', 0),
        agentAssessments=state.get('agent_assessments', 0),
        agentStopReason=state.get('agent_stop_reason', 'local_calculation' if evidence.get('localAnswer') else 'disabled'),
        agentTools=state.get('agent_observations', []),
        candidates=evidence.get("candidate_count", 0),
        evidence=len(sources),
        coverage=list(dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources)),
    )
    if retrieval_only:
        return dict(ranking=evidence.get("ranking", []), metrics=metrics)
    answer = state["answer"]
    if answer['status'] == 'insufficient_evidence':
        answer = {**answer, 'answer': (
            'Insufficient data to provide a verified answer. Verification timed out; please retry.'
            if state.get('repair_timed_out') else
            'Insufficient data. The selected filings do not provide enough verified information to answer this question. Add the relevant filings or refine your question.'
        )}
    sources = referenced_sources(answer, sources)
    answer = {**answer, 'citation_ids': [source['id'] for source in sources]}
    metrics['retrievedEvidence'] = metrics['evidence']
    metrics['evidence'] = len(sources)
    metrics['retrievedCoverage'] = metrics['coverage']
    metrics['coverage'] = list(dict.fromkeys(f"{s['ticker']}:{s['report_date']}" for s in sources))
    if state.get("deterministic"):
        metrics["calculationMethod"] = state.get('deterministic_method', 'cash-flow-table')
    result = {
        **answer,
        **({'scope': state['scope']} if state.get('scope') else {}),
        "sources": [
            {**{k: v for k, v in s.items() if k != "text"}, "excerpt": s["text"]} for s in sources
        ],
        "metrics": metrics,
        "executionPath": public_path(context, metrics['agentic']),
        "researchPlan": {**state.get('research_plan', {}), 'tasks': task_progress(state.get('research_plan', {}), evidence.get('sources', []))},
        "financialEvidence": financial_evidence(sources),
        'validation': dict(status='partial' if state.get('answer_partial') else 'failed' if state.get('validation_errors') else 'passed',
                           issues=state.get('validation_errors', []), recoveryAttempted=bool(state.get('validation_recovery_attempted')),
                           warnings=['Answer repair timed out. Only independently checked excerpts or calculations could be retained.'] if state.get('repair_timed_out') else []),
        "researchCompleteness": 'partial' if state.get('answer_partial') or answer.get('status') != 'answered' or any(t['status'] in ('unresolved', 'conflicting_values') for t in task_progress(state.get('research_plan', {}), evidence.get('sources', []))) or state.get('agent_stop_reason') in ('round_limit', 'answer_time_reserved', 'tool_unavailable', 'assessment_unavailable', 'assessment_context_budget', 'evidence_budget', 'no_new_evidence', 'repeated_action') else 'answered_with_validation',
    }
    if trace:
        result["ranking"] = evidence.get("ranking", [])
    result['executionPath']['finalOutput'] = answer_output(answer)
    if result['researchCompleteness'] == 'partial' and answer.get('status') != 'needs_clarification':
        result['evidenceGaps'] = evidence_gaps(question, state.get('scope'), result['researchPlan']['tasks'],
            evidence.get('missingCoverage'), state.get('agent_decision'),
            verification_failed=bool(state.get('validation_errors')), timed_out=bool(state.get('repair_timed_out')))
    return result
