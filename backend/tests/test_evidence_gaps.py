from finwhiz.evidence_gaps import evidence_gaps


def test_unresolved_task_identifies_company_period_metric_and_required_statement():
    gaps = evidence_gaps('Compare capex', tasks=[dict(ticker='MU', period='2026-Q2', metric='capex',
                          objective='Find quarterly capex', status='unresolved')])
    assert 'MU' in gaps[0]['part'] and '2026-Q2' in gaps[0]['part']
    assert 'property, plant and equipment' in gaps[0]['needed']
    assert gaps[0]['action'] == 'upload'


def test_verification_timeout_does_not_assert_that_documents_are_missing():
    gaps = evidence_gaps('What is capex?', timed_out=True)
    assert gaps[0]['action'] == 'retry'
    assert 'existing filings' in gaps[0]['needed']


def test_passages_without_verified_facts_get_specific_review_guidance():
    gaps = evidence_gaps('Explain gross margin drivers and risks.', verification_failed=True,
                         tasks=[dict(ticker='MU', period='2026-Q2', objective='Explain margin drivers', status='evidence_found')])
    assert gaps[0]['reason'] == 'verification_failed'
    assert 'revenue and cost of revenue' in gaps[0]['needed']
    assert 'management discussion' in gaps[0]['needed'] and 'risk-factor' in gaps[0]['needed']
    assert 'MU' in gaps[0]['part']


def test_missing_scope_retains_original_specific_period_reason():
    gaps = evidence_gaps('Compare Q1 and Q2', scope=dict(status='insufficient_evidence', tickers=['TSLA'],
        requestedPeriods=['2026-Q2'], message='No verified filing mapping for Tesla Q2 2026.'))
    assert gaps[0]['part'] == 'No verified filing mapping for Tesla Q2 2026.'
    assert '2026-Q2' in gaps[0]['needed'] and 'Q4' not in gaps[0]['needed']


def test_conflicts_request_reconciliation_rather_than_asserting_missing_files():
    gaps = evidence_gaps('Revenue?', tasks=[dict(ticker='TSLA', period='2026-Q2', metric='revenue',
                         objective='Reported revenue', status='conflicting_values')])
    assert gaps[0]['reason'] == 'conflicting_evidence' and gaps[0]['action'] == 'review'
    assert 'Confirm' in gaps[0]['needed']


def test_verified_quarters_are_not_listed_as_missing_and_task_prompts_are_hidden():
    tasks=[dict(ticker='TSLA', period='2026-Q1', metric='revenue', status='fact_located', objective='Retrieve internal task'),
           dict(ticker='TSLA', period='2025-Q4', metric='revenue', status='evidence_found', objective='Retrieve internal task')]
    gaps=evidence_gaps('Compare revenues across four quarters', tasks=tasks, verification_failed=True)
    assert len(gaps)==1 and '2025-Q4' in gaps[0]['part']
    assert 'Retrieve internal' not in str(gaps)
    assert gaps[0]['action']=='review' and 'nine-month' in gaps[0]['needed']
