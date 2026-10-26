"""On-demand SEC company resolution and imports into the existing job pipeline."""

import re
import threading
import time
import uuid
from datetime import datetime, timezone

from .catalog import sec_request, select_filings
from .config import AppError, digest
from .library import accept_upload
from .storage import now
from .telemetry import operation_budget, remaining

_lock = threading.Lock()


def inputs(body):
    companies = body.get('companies')
    if isinstance(companies, str):
        companies = [part.strip() for part in re.split(r'[,;\r\n]+', companies) if part.strip()]
    if not isinstance(companies, list) or not companies or len(companies) > 8:
        raise AppError('Enter 1–8 company names or tickers, separated by commas.', 400)
    if any(not isinstance(c, str) or not c.strip() or len(c.strip()) > 200 for c in companies):
        raise AppError('Each company must be a name or ticker of up to 200 characters.', 400)
    periods = body.get('periods', 'latest')
    if periods == 'latest':
        periods = 1
    if type(periods) is not int or not 1 <= periods <= 12 or periods * len(companies) > 32:
        raise AppError('Choose 1–12 reporting periods, up to 32 reports per request.', 400)
    return [c.strip() for c in companies], periods


def normalized(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.lower()))


def company_name(value):
    return re.sub(r'(?:\s+(?:inc|incorporated|corp|corporation|co|company|ltd|limited))+$', '', normalized(value))


def resolve(queries, directory):
    results, seen = [], set()
    for query in queries:
        key = normalized(query)
        matches = [r for r in directory if r['ticker'].upper() == query.upper().lstrip('$')]
        if not matches:
            matches = [r for r in directory if normalized(r['title']) == key]
        if not matches:
            matches = [r for r in directory if company_name(r['title']) == company_name(query)]
        if not matches:
            matches = [r for r in directory if normalized(r['title']).startswith(key + ' ')]
        if not matches:
            matches = [r for r in directory if key in normalized(r['title'])]
        ciks = {str(r['cik_str']) for r in matches}
        if not matches:
            raise AppError(f'No SEC-listed company found for “{query}”. Try its ticker.', 400)
        if len(ciks) > 1:
            choices = ', '.join(f"{r['ticker']} ({r['title']})" for r in matches[:8])
            raise AppError(f'“{query}” matches multiple companies. Enter a ticker: {choices}', 400)
        row = matches[0]
        cik = str(row['cik_str']).zfill(10)
        if cik not in seen:
            results.append(dict(ticker=row['ticker'], company=row['title'], cik=cik))
            seen.add(cik)
    return results


def import_reports(body, settings, config, store, jobs):
    try:
        with operation_budget(settings.sec_import_timeout_seconds):
            return _import_reports(body, settings, config, store, jobs)
    except AppError as error:
        if error.status == 504:
            raise AppError('SEC import timed out. Request fewer reports and retry.', 504) from None
        raise


def _import_reports(body, settings, config, store, jobs):
    queries, periods = inputs(body)
    filing_type = body.get('filingType', 'all')
    if not isinstance(filing_type, str) or filing_type not in ('all', '10-Q', '10-K'):
        raise AppError('Choose quarterly (10-Q), annual (10-K), or both filing types.', 400)
    forms = ('10-Q', '10-K') if filing_type == 'all' else (filing_type,)
    requested = body.get('requestedPeriods', [])
    if not isinstance(requested, list) or len(requested)>8 or any(not isinstance(p, str) or not re.fullmatch(r'\d{4}-(?:Q[1-4]|FY)|\d{4}-\d{2}-\d{2}', p) for p in requested):
        raise AppError('Recovery requires explicit fiscal quarters, years or filing dates.', 400)
    if requested:
        periods = 12  # Bound discovery; do not assume calendar quarters for fiscal issuers.
    # Serialize imports to keep this process below SEC fair-access limits.
    if not _lock.acquire(blocking=False):
        raise AppError('Another SEC import is running. Try again shortly.', 409)
    files = []
    try:
        def fetch(url, binary=False):
            remaining(settings.sec_import_timeout_seconds)
            time.sleep(0.2)
            remaining(settings.sec_import_timeout_seconds)
            return sec_request(url, settings, binary)

        directory = fetch('https://www.sec.gov/files/company_tickers.json')
        companies = resolve(queries, list(directory.values()))
        cutoff = datetime.now(timezone.utc).date().isoformat()
        warnings = []
        for company in companies:
            submission = fetch(f"https://data.sec.gov/submissions/CIK{company['cik']}.json")
            recent = {k: list(v) for k, v in submission['filings']['recent'].items()}
            filings = select_filings(recent, company['cik'], cutoff, periods, forms=forms)
            for archive in submission['filings'].get('files', []):
                if len(filings) >= periods:
                    break
                name = archive['name']
                if not re.fullmatch(r'CIK\d+-submissions-\d+\.json', name):
                    raise AppError('SEC returned an unexpected submissions archive name.')
                older = fetch('https://data.sec.gov/submissions/' + name)
                for key in recent:
                    recent[key].extend(older.get(key, []))
                filings = select_filings(recent, company['cik'], cutoff, periods, forms=forms)
            if not filings:
                raise AppError(f"{company['ticker']} has no supported {' or '.join(forms)} reports.", 400)
            if len(filings) < periods:
                warnings.append(f"{company['ticker']}: found {len(filings)} of {periods} requested reporting periods.")
            company['filings'] = filings
            selected_filings = []
            found_periods = set()
            for filing in filings:
                # Only SEC-generated archive paths are allowed, never arbitrary user URLs.
                if not re.fullmatch(r'https://www\.sec\.gov/Archives/edgar/data/\d+/\d+/[A-Za-z0-9_.-]+', filing['url']):
                    raise AppError('SEC returned an unexpected document path.')
                data = fetch(filing['url'], True)
                text = data.decode('utf-8', errors='replace')
                if len(data) > 20 * 1024 * 1024 or len(data) < 1000 or not re.search(r'<(?:html|ix:header)\b', text, re.I) or re.search(r'Undeclared Automated Tool|Request Rate Threshold Exceeded', text, re.I):
                    raise AppError(f"{company['ticker']}: SEC did not return a supported filing HTML document.")
                if requested:
                    from .documents import html_metadata
                    metadata = html_metadata(text)
                    year, quarter = metadata.get('fiscal_year'), metadata.get('fiscal_quarter')
                    labels = {filing['reportDate']}
                    if year and quarter:
                        labels.add(f'{year}-Q{str(quarter).lstrip("Qq")}')
                    if year and filing['form']=='10-K':
                        labels.add(f'{year}-FY')
                    if not labels.intersection(requested):
                        continue
                    found_periods.update(labels.intersection(requested))
                selected_filings.append(filing)
                if len(files)>=32:
                    raise AppError('Recovery exceeds 32 reports. Select fewer companies.', 400)
                if sum(f['size'] for f in files) + len(data) > 100 * 1024 * 1024:
                    raise AppError('Reports exceed the 100 MB batch limit. Request fewer periods.', 400)
                file_id = str(uuid.uuid4())
                key = f'upload/{file_id}'
                record = dict(id=file_id, key=key, name=f"{company['ticker']}_{filing['reportDate']}_{filing['form']}.html",
                              index=len(files), size=len(data), checksum=digest(data),
                              known={**company, **filing, 'sha256': digest(data)})
                files.append(record)
                store.put(key, data)
            company['filings'] = selected_filings
            if requested and set(requested)-found_periods:
                warnings.append(f"{company['ticker']}: periods not found in the last 12 reporting periods: {', '.join(sorted(set(requested)-found_periods))}")
        if not files:
            raise AppError('No requested periods were found in the last 12 SEC reporting periods. Upload the required historical filings manually.', 400)
        job = dict(id=str(uuid.uuid4()), aiConfig=config.model_dump(), createdAt=now(),
                   status='queued', phase='queued', percent=0, detail='SEC reports downloaded; waiting to process',
                   strategies=['fixed', 'semantic'], files=files)
        job, duplicates, created = accept_upload(jobs, job)
        retained = {f.get('key') for f in job['files']}
        for file in files:
            if file['key'] not in retained:
                store.remove(file['key'])
        return dict(job=job, duplicates=duplicates, created=created, companies=companies, warnings=warnings)
    except BaseException:
        for file in files:
            store.remove(file['key'])
        raise
    finally:
        _lock.release()
