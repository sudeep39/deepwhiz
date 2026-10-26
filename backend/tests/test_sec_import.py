import pytest
from fastapi.testclient import TestClient

from finwhiz import sec_import
from finwhiz.api import create_app
from finwhiz.config import AppError
from finwhiz.storage import Storage, Jobs


DIRECTORY = [dict(ticker='TSLA', title='Tesla, Inc.', cik_str=1318605),
             dict(ticker='AAPL', title='Apple Inc.', cik_str=320193)]


def test_resolution_and_validation():
    assert [c['ticker'] for c in sec_import.resolve(['tesla', 'TSLA', 'apple'], DIRECTORY)] == ['TSLA', 'AAPL']
    assert sec_import.resolve(['Apple'], DIRECTORY + [dict(ticker='APLE', title='Apple Hospitality REIT, Inc.', cik_str=42)])[0]['ticker'] == 'AAPL'
    with pytest.raises(AppError, match='multiple'):
        sec_import.resolve(['Tes'], DIRECTORY + [dict(ticker='OTHER', title='Tesla Energy', cik_str=42)])
    assert sec_import.inputs(dict(companies='Tesla; AAPL', periods='latest')) == (['Tesla', 'AAPL'], 1)
    assert sec_import.inputs(dict(companies='  TSLA, \r\n AAPL; \n', periods='latest')) == (['TSLA', 'AAPL'], 1)
    assert sec_import.inputs(dict(companies=' ' * 20 + 'T' * 200 + ' ' * 20))[0] == ['T' * 200]
    for companies in (' ,;\r\n ', 'T' * 201, ['TSLA', None]):
        with pytest.raises(AppError):
            sec_import.inputs(dict(companies=companies))
    for periods in (True, 0, 13, 'four'):
        with pytest.raises(AppError):
            sec_import.inputs(dict(companies='TSLA', periods=periods))


@pytest.fixture
def sec(monkeypatch):
    calls = []
    def fetch(url, settings, binary=False):
        calls.append(url)
        if 'company_tickers' in url:
            return {str(i): row for i, row in enumerate(DIRECTORY)}
        if 'submissions' in url:
            return dict(filings=dict(recent=dict(
                form=['8-K', '10-Q', '10-K', '10-Q'],
                reportDate=['2026-05-01', '2026-03-31', '2025-12-31', '2025-09-30'],
                filingDate=['2026-05-02', '2026-04-25', '2026-02-01', '2025-10-25'],
                accessionNumber=['000-0', '000-1', '000-2', '000-3'],
                primaryDocument=['event.htm', 'q1.htm', 'annual.htm', 'q3.htm']), files=[]))
        return ('<html><body>' + url + ' readable report ' * 150 + '</body></html>').encode()
    monkeypatch.setattr(sec_import, 'sec_request', fetch)
    monkeypatch.setattr(sec_import.time, 'sleep', lambda _: None)
    return calls


def test_import_periods_provenance_and_reuse(settings, sec):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    config = settings.provider('openai')
    body = dict(companies='Tesla, AAPL', periods=4)
    result = sec_import.import_reports(body, settings, config, store, jobs)
    assert result['created'] and len(result['job']['files']) == 6
    assert len(result['warnings']) == 2
    first = result['job']['files'][0]
    assert first['known']['form'] == '10-Q'
    assert first['known']['url'].startswith('https://www.sec.gov/Archives/')
    assert store.get(first['key'])
    again = sec_import.import_reports(body, settings, config, store, jobs)
    assert not again['created'] and len(again['duplicates']) == 6
    assert len(jobs.all()) == 1


def test_failed_download_removes_blobs(settings, sec, monkeypatch):
    store = Storage(settings.finwhiz_data_dir)
    jobs = Jobs(store)
    original = sec_import.sec_request
    def fail(url, *args):
        if url.endswith('annual.htm'):
            raise AppError('Download failed')
        return original(url, *args)
    monkeypatch.setattr(sec_import, 'sec_request', fail)
    with pytest.raises(AppError, match='Download failed'):
        sec_import.import_reports(dict(companies='TSLA', periods=2), settings, settings.provider('openai'), store, jobs)
    assert not jobs.all()
    assert len(list(store.directory.glob('*'))) == 1  # job registry only


def test_import_api(settings, sec):
    settings = settings.model_copy(update=dict(openai_api_key='test', pinecone_api_key='test'))
    app = create_app(settings, disable_workers=True)
    with TestClient(app) as client:
        response = client.post('/api/sec/import', json=dict(companies='TSLA', periods='latest'),
                               headers={'Authorization': 'Bearer baseline-password'})
    assert response.status_code == 202, response.text
    assert response.json()['companies'][0]['filings'][0]['reportDate'] == '2026-03-31'
    assert response.json()['job']['status'] == 'queued'


def test_recovery_import_selects_verified_fiscal_period(settings, sec, monkeypatch):
    def metadata(text):
        return dict(fiscal_year=2026 if 'q1.htm' in text else 2025,
                    fiscal_quarter=1 if 'q1.htm' in text else 4)
    monkeypatch.setattr('finwhiz.documents.html_metadata', metadata)
    store=Storage(settings.finwhiz_data_dir);jobs=Jobs(store)
    result=sec_import.import_reports(dict(companies='TSLA',periods=1,requestedPeriods=['2026-Q1']),
                                     settings,settings.provider('openai'),store,jobs)
    assert len(result['job']['files'])==1
    assert result['job']['files'][0]['known']['reportDate']=='2026-03-31'


def test_recovery_does_not_substitute_unrequested_periods(settings, sec, monkeypatch):
    monkeypatch.setattr('finwhiz.documents.html_metadata',lambda text:dict(fiscal_year=2025,fiscal_quarter=4))
    store=Storage(settings.finwhiz_data_dir);jobs=Jobs(store)
    with pytest.raises(AppError,match='No requested periods'):
        sec_import.import_reports(dict(companies='TSLA',periods=1,requestedPeriods=['2035-Q2']),
                                  settings,settings.provider('openai'),store,jobs)
    assert not jobs.all()

@pytest.mark.parametrize('filing_type,expected', [('10-K', ['10-K']), ('10-Q', ['10-Q', '10-Q'])])
def test_import_explicit_filing_type(settings, sec, filing_type, expected):
    store = Storage(settings.finwhiz_data_dir)
    result = sec_import.import_reports(dict(companies='TSLA', periods=2, filingType=filing_type),
                                      settings, settings.provider('openai'), store, Jobs(store))
    assert [f['known']['form'] for f in result['job']['files']] == expected


def test_latest_annual_skips_newer_quarter(settings, sec):
    store = Storage(settings.finwhiz_data_dir)
    result = sec_import.import_reports(dict(companies='TSLA', periods='latest', filingType='10-K'),
                                      settings, settings.provider('openai'), store, Jobs(store))
    assert len(result['job']['files']) == 1
    assert result['job']['files'][0]['known']['reportDate'] == '2025-12-31'
    assert not any(url.endswith('q1.htm') for url in sec)


@pytest.mark.parametrize('filing_type', ['8-K', '', None, ['10-K']])
def test_invalid_filing_type_before_network(settings, sec, filing_type):
    store = Storage(settings.finwhiz_data_dir)
    with pytest.raises(AppError, match='filing types'):
        sec_import.import_reports(dict(companies='TSLA', filingType=filing_type),
                                  settings, settings.provider('openai'), store, Jobs(store))
    assert not sec
