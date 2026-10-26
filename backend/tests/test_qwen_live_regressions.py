"""Deterministic regressions derived from actual Qwen/deployed failures."""
from finwhiz.answer_validation import coverage_issues, validation_issues
from finwhiz.finance import empty_chart


def answer(text):
    return dict(status='answered',answer=text,citation_ids=['S1','S2'],chart=empty_chart(),calculations=[])


def test_year_only_and_date_scope_never_crash_coverage_validation():
    source=dict(id='S1',ticker='TSLA',report_date='2026-06-30',text='Source')
    for period in ('2026','2026-06-30'):
        assert coverage_issues(answer('Disclosed amount [S1].'),[source],dict(tickers=['TSLA'],requestedPeriods=[period]))==[]
    assert coverage_issues(answer('Disclosed amount [S1].'),[source],dict(tickers=['TSLA'],requestedPeriods=['unsupported']))


def test_trailing_citations_remain_with_their_own_sentence():
    sources=[dict(id='S1',text='Revenue increased $350 million, or 13%.'),
             dict(id='S2',text='Cost increased $556 million, or 29%.')]
    result=answer('Revenue increased $350 million, or 13%. [S1] Cost increased $556 million, or 29%. [S2]')
    assert validation_issues(result,sources)==[]
    swapped=answer('Revenue increased $350 million, or 13%. [S2] Cost increased $556 million, or 29%. [S1]')
    assert validation_issues(swapped,sources)


def test_unrelated_table_does_not_veto_exact_narrative_citation():
    sources=[dict(id='S1',ticker='TSLA',report_date='2026-06-30',chunk_type='table',text='(in millions)\nThree Months Ended June 30 | |\n | 2026 | 2025\nTotal revenues | 3139 | 2789'),
             dict(id='S2',text='Energy revenue increased $350 million, or 13%.')]
    assert validation_issues(answer('Energy revenue increased $350 million, or 13% [S1] [S2].'),sources)==[]


def test_comparison_period_is_completed_only_for_unique_source_cells():
    source=dict(id='S1',ticker='TSLA',report_date='2026-06-30',text='(in millions)\nThree Months Ended June 30 | |\n | 2026 | 2025\nEnergy generation and storage | 3139 | 2789')
    result=answer('See the source-bound calculation [S1].')
    result['citation_ids']=['S1']
    result['calculations']=[dict(label='energy_revenue_2026_Q2',operation='difference',values=[3139,2789],unit='millions',source_ids=['S1'])]
    assert validation_issues(result,[source],False)==[]
    assert result['calculations'][0]['label'].endswith('versus 2025-Q2')
    result['calculations'][0]['values'][1]=999
    assert validation_issues(result,[source],False)
