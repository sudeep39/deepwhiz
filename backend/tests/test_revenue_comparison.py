import pytest
from finwhiz.revenue_comparison import answer


def source(sid, year, month, duration, value):
    return dict(id=sid, ticker='TSLA', company='Tesla', report_date=f'{year}-{month}-30',
        text=f'(in millions)\n{duration} Months Ended {dict(zip(["03","06","09","12"],["March","June","September","December"]))[month]} 30 | |\n | {year} | {year-1}\nTotal revenues | {value} | 100')


def test_four_quarters_with_explicit_q4_derivation():
    sources=[source('S1',2025,'12','Twelve',1000),source('S2',2025,'09','Nine',700),
             source('S3',2026,'03','Three',320),source('S4',2026,'06','Three',330),source('S5',2026,'09','Three',340)]
    scope=dict(tickers=['TSLA'],requestedPeriods=['2025-Q4','2026-Q1','2026-Q2','2026-Q3'])
    result=answer('Compare Tesla revenues across the 4 qrts',sources,scope)
    assert result['status']=='answered'
    from finwhiz.answer_validation import validation_issues, coverage_issues
    assert validation_issues(result,sources,False)==[]
    assert coverage_issues(result,sources,scope)==[]
    assert '300 millions' in result['answer'] and '320 millions' in result['answer']
    assert result['calculations'][0]['values']==[1000,700]
    assert result['calculations'][0]['source_ids']==['S1','S2']
    assert answer('Compare revenues and explain drivers',sources,scope) is None
    assert answer('Compare revenues',sources[:1]+sources[2:],scope) is None


def test_conflicting_or_different_units_never_derive():
    sources=[source('S1',2025,'12','Twelve',1000),source('S2',2025,'09','Nine',700)]
    scope=dict(tickers=['TSLA'],requestedPeriods=['2025-Q4'])
    sources[1]['text']=sources[1]['text'].replace('millions','thousands')
    assert answer('Compare revenues',sources,scope) is None
    sources[1]['text']=sources[1]['text'].replace('thousands','millions')
    sources.append(source('S3',2025,'12','Twelve',1100))
    assert answer('Compare revenues',sources,scope) is None
