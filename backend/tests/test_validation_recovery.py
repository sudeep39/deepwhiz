"""Failed claims feed bounded recovery; partial results never retain failed units."""
from copy import deepcopy
import pytest
import httpx

from finwhiz.answer_validation import supported_repair, validation_issues
from finwhiz.finance import empty_chart
from finwhiz.config import AppError
from finwhiz.telemetry import ResearchTrace
from finwhiz.providers import Providers
from finwhiz.answer_validation import supported_draft
from finwhiz.schemas import AgentDecision, Answer, Repair
from test_agentic import fixture, decision, evidence, source


def answer(text, ids=None):
    return dict(status='answered', answer=text, citation_ids=ids or ['S1'],
                chart=empty_chart(), calculations=[])


def test_list_markers_and_filing_forms_are_not_financial_claims():
    sources = [source('one', 'Improved product mix supported gross margin. Pricing pressure is a risk.')]
    assert not validation_issues(answer('1. Improved product mix supported gross margin in the 10-Q [S1].'), sources)
    assert validation_issues(answer('Gross margin was 99% [S1].'), sources)


def test_partial_retains_only_exact_checked_excerpts_and_valid_citations():
    sources = [source('one', 'Improved product mix supported gross margin. Pricing pressure is a risk.')]
    sentences = [dict(text='Improved product mix supported gross margin.', source_ids=['S1']),
                 dict(text='Gross margin was 99%.', source_ids=['S1']),
                 dict(text='Pricing pressure is a risk.', source_ids=['S99']),
                 dict(text='The company doubled profitability.', source_ids=['S1'])]
    result = supported_repair(sentences, [], sources)
    assert result['answer'] == 'Improved product mix supported gross margin. [S1]'
    assert not validation_issues(result, sources)
    assert supported_repair(sentences[1:], [], sources) is None


def test_failed_validation_returns_to_agent_for_targeted_search(settings, monkeypatch):
    run, provider, queries, models = fixture(settings, monkeypatch,
        [decision(), decision('search', 'reported revenue'), decision()],
        [evidence(source('cost', 'Costs were 100 million.')),
         evidence(source('rev', 'Revenue was 125 million.'))])
    original = provider.structured
    observations = []
    def structured(system, payload, schema, model=None):
        if schema is AgentDecision:
            observations.extend(deepcopy(payload['observations']))
        return original(system, payload, schema, model)
    provider.structured = structured
    result = run()
    assert result['status'] == 'answered'
    assert result['validation'] == dict(status='passed', issues=[], recoveryAttempted=True, warnings=[])
    assert len(queries) == 2 and queries[1]['question'] == 'reported revenue'
    assert any(o['tool'] == 'validation' and o['issues'] for o in observations)
    assert any(s['node'] == 'validation_recovery' and s['control'] == 'agent' for s in result['executionPath']['steps'])


def test_failed_repair_keeps_verified_findings_and_reports_exact_gaps(settings, monkeypatch):
    run, provider, queries, _ = fixture(settings, monkeypatch, [decision()],
        [evidence(source('margin', 'Improved product mix supported gross margin. Pricing pressure is a risk.'))])
    original = provider.structured
    def structured(system, payload, schema, model=None):
        if schema is Answer:
            return answer('Gross margin was 99% [S1].')
        if schema is Repair:
            return dict(sentences=[dict(text='Improved product mix supported gross margin.', source_ids=['S1']),
                                   dict(text='Gross margin was 99%.', source_ids=['S1'])], calculations=[])
        return original(system, payload, schema, model)
    provider.structured = structured
    result = run()
    assert result['status'] == 'answered' and result['researchCompleteness'] == 'partial'
    assert '99' not in result['answer'] and 'product mix' in result['answer']
    assert result['validation']['status'] == 'partial' and result['validation']['issues']
    final = result['executionPath']['steps'][-1]
    assert final['status'] == 'partial'
    assert any('99%' in detail for detail in final['details'])
    assert '0 validation issues detected' not in final['details']
    assert len(queries) == 1


def test_recovery_respects_existing_tool_round_limit(settings, monkeypatch):
    run, provider, queries, _ = fixture(settings, monkeypatch,
        [decision(), decision('search', 'reported revenue'), decision()],
        [evidence(source('cost', 'Costs were 100 million.'))])
    provider.settings = provider.settings.model_copy(update={'agent_max_rounds': 0})
    original = provider.structured
    def structured(system, payload, schema, model=None):
        if schema is Repair:
            return dict(sentences=[dict(text='Revenue was 125 million.', source_ids=['S99'])], calculations=[])
        return original(system, payload, schema, model)
    provider.structured = structured
    result = run()
    assert len(queries) == 1 and not result['validation']['recoveryAttempted']
    assert result['validation']['status'] == 'failed' and result['validation']['issues']
    assert result['status'] == 'insufficient_evidence'
    assert result['answer'].startswith('Insufficient data.')


@pytest.mark.parametrize('date', ['September 3, 2026', 'Sep. 3, 2026', 'Sept 3 2026', '2026-09-03', '9/3/2026'])
def test_complete_dates_are_not_financial_values(date):
    sources = [source('rev', 'Revenue was 125 million.')]
    assert not validation_issues(answer(f'As of {date}, revenue was 125 million [S1].'), sources)
    assert validation_issues(answer(f'As of {date}, revenue was 999 million [S1].'), sources)


def test_invalid_dates_and_nearby_amounts_are_still_checked():
    sources = [source('rev', 'Revenue was 125 million.')]
    assert validation_issues(answer('As of February 30, 2026, revenue was 125 million [S1].'), sources)
    assert validation_issues(answer('Revenue was 3 million on September 3, 2026 [S1].'), sources)


@pytest.mark.parametrize('has_checked_excerpt', [True, False])
def test_repair_timeout_returns_explicit_checked_fallback(settings, monkeypatch, has_checked_excerpt):
    run, provider, _, _ = fixture(settings, monkeypatch, [decision()],
        [evidence(source('cost', 'Costs were 100 million.'))])
    original = provider.structured
    def structured(system, payload, schema, model=None):
        if schema is Answer:
            return answer(('Costs were 100 million. [S1] ' if has_checked_excerpt else '') + 'Revenue was 999 million. [S1]')
        if schema is Repair:
            raise AppError('Answer repair timed out. Retry the request.', 504)
        return original(system, payload, schema, model)
    provider.structured = structured
    result = run()
    assert result['researchCompleteness'] == 'partial'
    assert result['validation']['warnings'] and result['validation']['issues']
    assert result['status'] == ('answered' if has_checked_excerpt else 'insufficient_evidence')
    assert '999' not in result['answer']
    if not has_checked_excerpt:
        assert result['answer'].startswith('Insufficient data') and 'Verification timed out' in result['answer']
    final = result['executionPath']['steps'][-1]
    assert final['node'] == 'repair' and final['status'] != 'completed'
    assert 'Answer repair model timed out' in final['details']


def test_repair_timeout_does_not_hide_request_cancellation(settings, monkeypatch):
    run, provider, _, _ = fixture(settings, monkeypatch, [decision()],
        [evidence(source('cost', 'Costs were 100 million.'))])
    context = ResearchTrace('cancel-repair', 60)
    original = provider.structured
    def structured(system, payload, schema, model=None):
        if schema is Repair:
            context.cancelled.set()
            raise AppError('Answer repair timed out.', 504)
        return original(system, payload, schema, model)
    provider.structured = structured
    with pytest.raises(AppError, match='Research exceeded its time budget'):
        run(context=context)


def test_repair_has_own_transport_timeout_and_stage_message(settings):
    settings = settings.model_copy(update={'openai_api_key': 'offline', 'provider_attempts': 1,
                                           'generation_timeout_seconds': 30, 'answer_repair_timeout_seconds': 12})
    requests = []
    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout('simulated stall', request=request)
    provider = Providers(settings, settings.provider(), httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(AppError, match='Answer repair timed out') as error:
            provider.structured('Repair.', {}, Repair)
        assert error.value.status == 504
        assert len(requests) == 1 and 0 < requests[0].extensions['timeout']['read'] <= 12
    finally:
        provider.close()


def test_checked_calculation_survives_when_no_draft_prose_can_be_kept():
    sources = [source('rev', 'Revenue was 125 million. Costs were 100 million.')]
    draft = answer('Revenue was 999 million [S1].')
    draft['calculations'] = [dict(label='ACME gross profit 2025-Q1', operation='difference',
                                  values=[125, 100], unit='million', source_ids=['S1'])]
    result = supported_draft(draft, sources)
    assert result and result['calculations'][0]['result'] == 25
    assert '999' not in result['answer']
