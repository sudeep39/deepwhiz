"""SEC collection, downloads and independent source-label candidates."""

import csv
import io
import json
import re
import time

import httpx

from .config import ROOT, AppError, digest
from .documents import extract_html
from .storage import now
from .telemetry import remaining


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def select_filings(recent, cik, as_of, periods=4, forms=("10-K", "10-Q")):
    rows = []
    for i, form in enumerate(recent["form"]):
        if (
            form not in forms
            or not recent["reportDate"][i]
            or recent["filingDate"][i] > as_of
            or recent["reportDate"][i] > as_of
        ):
            continue
        accession = recent["accessionNumber"][i]
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
        rows.append(
            dict(
                form=form,
                reportDate=recent["reportDate"][i],
                filingDate=recent["filingDate"][i],
                accession=accession,
                document=recent["primaryDocument"][i],
                url=base + "/" + recent["primaryDocument"][i],
                indexUrl=f"{base}/{accession}-index.html",
                coverage=(
                    "Annual report (includes fiscal Q4)" if form == "10-K" else "Quarterly report"
                ),
            )
        )
    rows.sort(key=lambda r: (r["reportDate"], r["filingDate"]), reverse=True)
    unique = {}
    for row in rows:
        unique.setdefault(row["reportDate"], row)
    return list(unique.values())[:periods]


def sec_request(url, settings, binary=False):
    if not settings.sec_user_agent or "@" not in settings.sec_user_agent:
        raise AppError("Configure SEC_USER_AGENT with your app name and contact email.")
    limit = (20 if binary else 8) * 1024 * 1024
    try:
        with httpx.Client(follow_redirects=False) as client:
            for attempt in range(3):
                with client.stream('GET', url, timeout=remaining(20), headers={
                    'User-Agent': settings.sec_user_agent,
                    'Accept': 'text/html' if binary else 'application/json',
                }) as response:
                    if response.is_success:
                        data = bytearray()
                        for chunk in response.iter_bytes(chunk_size=65536):
                            remaining(20)
                            if len(data) + len(chunk) > limit:
                                raise AppError('SEC response exceeds the download size limit. Request a smaller filing.', 413)
                            data.extend(chunk)
                        if binary:
                            return bytes(data)
                        try:
                            return json.loads(data)
                        except (ValueError, UnicodeDecodeError):
                            raise AppError('SEC returned an invalid data response. Retry later.') from None
                    if response.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise AppError(f'SEC HTTP {response.status_code}; review not marked complete.')
                time.sleep(min(2 * (attempt + 1), remaining(20)))
    except httpx.TimeoutException:
        raise AppError('SEC download timed out. Request fewer reports and retry.', 504) from None
    except httpx.TransportError:
        raise AppError('SEC could not be reached. Retry later.') from None


def csv_export(companies, experiment=False):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(
        [
            "company",
            "ticker" if experiment else "tickers",
            "cik",
            "form",
            "report_date",
            "filing_date",
            "accession",
            "url",
        ]
    )
    for company in companies:
        for filing in company["filings"]:
            writer.writerow(
                [
                    company["name"],
                    company["tickers"][0] if experiment else " / ".join(company["tickers"]),
                    company["cik"],
                    *[filing[k] for k in ("form", "reportDate", "filingDate", "accession", "url")],
                ]
            )
    return stream.getvalue()


def export_experiment():
    experiment, catalog = load("experiment.json"), load("data/filings.json")
    companies = [c for c in catalog["companies"] if c["tickers"][0] in experiment["tickers"]]
    if (
        len(companies) != len(experiment["tickers"])
        or any(len(c["filings"]) != experiment["periods_per_company"] for c in companies)
        or catalog["asOf"] != experiment["as_of"]
    ):
        raise AppError("Experiment catalog does not match experiment.json")
    write(
        ROOT / "data/experiment-filings.json",
        {**catalog, "experiment": experiment, "companies": companies},
    )
    (ROOT / "data/experiment-filings.csv").write_text(csv_export(companies, True), encoding="utf-8")
    return dict(companies=len(companies), filings=sum(len(c["filings"]) for c in companies))


def collect(settings, as_of=None, tickers=None, periods=None):
    experiment, prior = load("experiment.json"), load("data/filings.json")
    as_of = as_of or experiment["as_of"]
    if tickers and prior["asOf"] != as_of:
        raise AppError("Subset refresh must use the existing catalog cutoff.")
    companies = prior["companies"]
    if tickers and any(not any(t in c["tickers"] for c in companies) for t in tickers):
        raise AppError("Unknown ticker in --tickers")
    result = []
    for company in companies:
        if tickers and not any(t in tickers for t in company["tickers"]):
            result.append(company)
            continue
        count = periods or (
            experiment["periods_per_company"]
            if company["tickers"][0] in experiment["tickers"]
            else 4
        )
        url = f"https://data.sec.gov/submissions/CIK{company['cik']}.json"
        submissions = sec_request(url, settings)
        recent = submissions["filings"]["recent"]
        for file in submissions["filings"].get("files", []):
            if len(select_filings(recent, company["cik"], as_of, count)) >= count:
                break
            time.sleep(0.3)
            older = sec_request("https://data.sec.gov/submissions/" + file["name"], settings)
            for key in recent:
                recent[key].extend(older.get(key, []))
        filings = select_filings(recent, company["cik"], as_of, count)
        if len(filings) != count:
            raise AppError(f"Expected {count} reporting periods for {company['name']}")
        from datetime import date

        if any(
            not 60
            <= (date.fromisoformat(a["reportDate"]) - date.fromisoformat(b["reportDate"])).days
            <= 120
            for a, b in zip(filings, filings[1:])
        ):
            raise AppError(f"Non-consecutive quarterly periods for {company['name']}")
        write(ROOT / f"data/raw/{company['cik']}.json", submissions)
        result.append(
            {**company, "secName": submissions["name"], "sourceUrl": url, "filings": filings}
        )
        time.sleep(0.3)
    backup = (
        ROOT
        / f"data/archive/filings-{prior['asOf']}-{sum(len(c['filings']) for c in prior['companies'])}.json"
    )
    if not backup.exists():
        write(backup, prior)
    dataset = {**prior, "asOf": as_of, "retrievedAt": now(), "companies": result}
    write(ROOT / "data/filings.json", dataset)
    (ROOT / "data/filings.csv").write_text(csv_export(result), encoding="utf-8")
    if as_of == experiment["as_of"]:
        export_experiment()
    return dict(companies=len(result))


def download(settings):
    catalog, entries, failures = load("data/filings.json"), [], []
    for company in catalog["companies"]:
        for filing in company["filings"]:
            relative = f"data/filings/{company['tickers'][0]}/{filing['reportDate']}_{filing['form']}_{filing['accession']}.html"
            path = ROOT / relative
            try:
                data = (
                    path.read_bytes()
                    if path.exists()
                    else sec_request(filing["url"], settings, True)
                )
                text = data.decode("utf-8", errors="replace")
                if (
                    len(data) < 10000
                    or not re.search(r"<(?:html|ix:header)\b", text, re.I)
                    or re.search(
                        "Your Request Originates from an Undeclared Automated Tool|Request Rate Threshold Exceeded",
                        text,
                        re.I,
                    )
                ):
                    raise AppError("Response is not a complete filing HTML document")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                entries.append(
                    dict(
                        company=company["name"],
                        ticker=company["tickers"][0],
                        cik=company["cik"],
                        **filing,
                        localPath=relative,
                        bytes=len(data),
                        sha256=digest(data),
                    )
                )
            except Exception as error:
                failures.append(
                    dict(ticker=company["tickers"][0], url=filing["url"], error=str(error))
                )
            time.sleep(0.35)
    write(
        ROOT / "data/filings/manifest.json",
        dict(asOf=catalog["asOf"], downloadedAt=now(), entries=entries, failures=failures),
    )
    if failures:
        raise AppError(
            f"Downloaded {len(entries)} filings; {len(failures)} failures. See manifest."
        )
    return dict(downloaded=len(entries))


def evaluation_entries():
    experiment = load("experiment.json")
    entries = [
        e
        for e in load("data/filings/manifest.json")["entries"]
        if e["ticker"] in experiment["tickers"]
    ]
    if len(entries) != len(experiment["tickers"]) * experiment["periods_per_company"]:
        raise AppError("Wrong evaluation corpus size.")
    for entry in entries:
        path = (ROOT / entry["localPath"]).resolve()
        if not path.is_relative_to(ROOT / "data") or digest(path.read_bytes()) != entry["sha256"]:
            raise AppError("Changed or invalid source: " + entry["localPath"])
    return entries


def benchmark_candidates():
    docs = [
        {**e, "blocks": extract_html((ROOT / e["localPath"]).read_text(encoding="utf-8"))}
        for e in sorted(
            evaluation_entries(), key=lambda e: (e["ticker"], e["reportDate"]), reverse=True
        )
    ]
    # Rebuild evidence directly from verified source tables/passages. Questions remain frozen.
    suite = load("evaluation/benchmark.json")
    for query in suite:
        query.update(reviewed=False, reviewer="")
        for label in query["labels"]:
            doc = next(
                d
                for d in docs
                if d["ticker"] == label["ticker"] and d["reportDate"] == label["reportDate"]
            )
            candidates = [
                b
                for b in doc["blocks"]
                if all(phrase.lower() in b["text"].lower() for phrase in label["contains"])
            ]
            if not candidates:
                raise AppError(
                    f"No source evidence found: {query['id']}; review source and label definition."
                )
            block = candidates[0]
            if block["kind"] == "table":
                matched = next(
                    (
                        line
                        for line in block["text"].splitlines()
                        if label["contains"][0].lower() in line.lower()
                    ),
                    "",
                )
                quote = "\n".join(block["text"].splitlines()[:3] + [matched])
            else:
                start = max(0, block["text"].lower().index(label["contains"][0].lower()) - 80)
                quote = block["text"][start : start + 450]
            label.update(
                sourceQuote=quote,
                accession=doc["accession"],
                sourceSha256=doc["sha256"],
                sourceUrl=doc["url"],
            )
    return suite
