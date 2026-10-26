"""Evidence checks and deterministic rendering, independent of graph orchestration."""

import re
from datetime import datetime
from .config import AppError
from .finance import calculate, supports_number, finalize_margins, empty_chart
from .fact_validation import bound_value, calculation_bound, numeric_context, ordered_periods, claim_metric
from .financial_facts import table_facts


def calendar_date_spans(text):
    """Recognize complete, valid dates without exempting nearby financial values."""
    months = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    formats = [(rf'\b{months}\.?\s+\d{{1,2}},?\s+20\d{{2}}\b', ('%B %d %Y', '%b %d %Y')),
               (r'\b20\d{2}-\d{2}-\d{2}\b', ('%Y-%m-%d',)),
               (r'\b\d{1,2}/\d{1,2}/20\d{2}\b', ('%m/%d/%Y',))]
    spans = []
    for pattern, parsers in formats:
        for match in re.finditer(pattern, text, re.I):
            value = re.sub(r'\bSept\b', 'Sep', match[0], flags=re.I).replace(',', '').replace('.', '')
            for parser in parsers:
                try:
                    datetime.strptime(value, parser)
                    spans.append(match.span())
                    break
                except ValueError:
                    continue
    return spans


def referenced_sources(answer, sources):
    """Expose evidence actually referenced by the final rendered response."""
    ids = set(re.findall(r'\[(S\d+)\]', answer['answer']))
    for calculation in answer.get('calculations', []):
        ids.update(calculation.get('source_ids', []))
    if answer.get('chart', {}).get('type') != 'none':
        for point in answer.get('chart', {}).get('points', []):
            ids.update(point.get('source_ids', []))
    return [source for source in sources if source['id'] in ids]


def coverage_issues(answer, sources, scope):
    if not scope or answer['status'] != 'answered':
        return []
    cited = referenced_sources(answer, sources)
    missing_companies = set(scope.get('tickers', [])) - {s.get('ticker') for s in cited}
    issues = [f'The answer omits requested company {ticker}.' for ticker in sorted(missing_companies)]
    for ticker in scope.get('tickers', []):
        company_sources = [s for s in cited if s.get('ticker') == ticker]
        periods = {f.period for s in company_sources for f in table_facts(s)}
        periods.update(p[:4] for p in list(periods))
        for source in company_sources:
            if source.get('report_date'):
                periods.add(source['report_date'])
            year, quarter = source.get('fiscal_year'), source.get('fiscal_quarter')
            if year and quarter:
                periods.add(f'{year}-Q{str(quarter).lstrip("Qq")}')
                periods.add(str(int(year)))
            elif ticker == 'TSLA' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', source.get('report_date', '')):
                day = source['report_date']
                periods.add(f'{day[:4]}-Q{(int(day[5:7])-1)//3+1}')
                periods.add(day[:4])
        for period in scope.get('requestedPeriods', []):
            if period not in periods:
                # Explicit source-column differences may derive Q1/Q4. The
                # operand validator independently checks their period order.
                if not re.fullmatch(r'\d{4}-(?:Q[1-4]|H[12]|FY|YTD-Q[1-4])', period):
                    issues.append(f'The cited evidence does not cover {ticker} {period}.')
                    continue
                year, kind = period.split('-', 1)
                inputs = {'Q1': {f'{year}-H1', f'{year}-Q2'},
                          'Q4': {f'{year}-FY', f'{year}-YTD-Q3'}}.get(kind, set())
                derived = bool(inputs) and inputs <= periods and any(
                    c['operation'] == 'difference' and inputs <= set(ordered_periods(c['label']))
                    for c in answer.get('calculations', []))
                if not derived:
                    issues.append(f'The cited evidence does not cover {ticker} {period}.')
    return issues


def supports_narrative_number(sources, ids, value, unit=None, claim='', metric=None):
    """Allow exact unit conversions, never rounding or new arithmetic claims."""
    scales = {'thousand': 1e3, 'million': 1e6, 'billion': 1e9, 'trillion': 1e12}
    # Validate each citation independently: a table that lacks a disclosed
    # narrative percentage must not veto a supporting MD&A citation.
    for source in sources:
        if source['id'] not in ids:
            continue
        bound = bound_value([source], [source['id']], value, claim, unit, metric)
        if bound is True:
            return True
        if bound is False:
            continue
        text = source['text']
        if not unit:
            if supports_number(text, value):
                return True
            continue
        source_scales = re.findall(r'\bin\s+(thousands|millions|billions|trillions)\b', text, re.I)
        if len(set(s.lower() for s in source_scales)) == 1:
            scale = scales[source_scales[0].lower().rstrip('s')]
            if supports_number(text, value * scales[unit] / scale):
                return True
        # Explicit narrative quantities are also evidence for their own scale.
        for match in re.finditer(r'(?<![\w.])-?\d[\d,]*(?:\.\d+)?\s+(thousand|million|billion|trillion)s?\b', text, re.I):
            if match[1].lower() == unit and supports_number(match[0], value):
                return True
    return False


def validation_issues(answer, sources, validate_numeric=True):
    allowed = {s["id"] for s in sources}

    def valid(ids):
        return bool(ids) and all(i in allowed for i in ids)

    def cited(ids):
        return "\n".join(s["text"] for s in sources if s["id"] in ids)

    answer["answer"] = re.sub(
        r"\[(S\d+(?:\s*[,;]\s*S\d+)+)\]",
        lambda m: " ".join(f"[{i}]" for i in re.split(r"\s*[,;]\s*", m[1])),
        answer["answer"],
    )
    inline = re.findall(r"\[(S\d+)\]", answer["answer"])
    issues = []
    # Models sometimes label a comparison with only the current quarter. Add
    # the other operand's period only when both existing literal inputs have
    # unique source-bound metric/company/period/unit identities. Ambiguity
    # still fails normal validation; no value or operation is changed.
    for calculation in answer.get('calculations', []):
        label=calculation['label']
        periods=ordered_periods(label)
        if calculation['operation'] not in ('percent_change','difference') or len(periods)!=1 or len(calculation['values'])!=2:
            continue
        metric=claim_metric(label)
        facts=[f for s in sources if s['id'] in calculation['source_ids'] for f in table_facts(s)]
        matches=[{(f.ticker,f.period,f.unit) for f in facts if f.metric==metric and f.value==value}
                 for value in calculation['values']]
        if all(len(m)==1 for m in matches):
            first,second=[next(iter(m)) for m in matches]
            if first[0]==second[0] and first[2]==second[2] and first[1]==periods[0] and first[1]!=second[1]:
                calculation['label']=label+' versus '+second[1]
    if answer["status"] == "answered" and validate_numeric:
        financial_text = re.sub(r'(?m)^\s*(?:[-*]\s+)?\d+[.)]\s+', '', answer['answer'])
        # Keep trailing citations with the preceding claim while checking it.
        # The rendered answer remains unchanged for exact-excerpt recovery.
        financial_text = re.sub(r'([.!?])\s+((?:\[S\d+\]\s*)+)',
                                lambda m: ' '+m[2].strip()+m[1]+' ', financial_text)
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", financial_text):
            ids = re.findall(r"\[(S\d+)\]", sentence)
            if not ids:
                ids = answer["citation_ids"]
            text = re.sub(r"\[S\d+\]", "", sentence)
            dates = calendar_date_spans(text)
            for match in re.finditer(r"(?<![A-Za-z\d])\(?-?\d[\d,]*(?:\.\d+)?%?\)?", text):
                raw = match[0].rstrip(',')
                if any(start <= match.start() and match.start() + len(raw) <= end for start, end in dates):
                    continue
                # Filing form names and numbered list markers are not financial values.
                if re.match(r'10-[KQ]\b', text[match.start():], re.I):
                    continue
                if re.fullmatch(r'\d+', raw) and re.fullmatch(r'\s*(?:[-*]\s+)?', text[:match.start()]) and re.match(r'[.)]\s', text[match.end():]):
                    continue
                # Period labels identify the claim; they are not financial values.
                if re.fullmatch(r'20\d{2}', raw) or (match.start() and text[match.start()-1:match.start()].upper() == 'Q'):
                    continue
                value = float(re.sub(r"[(),%]", "", raw)) * (-1 if raw.startswith("(") else 1)
                suffix = re.match(r'\s+(thousand|million|billion|trillion)s?\b', text[match.end():], re.I)
                unit = suffix[1].lower() if suffix and '%' not in raw else None
                context, metric = numeric_context(text, match.start())
                if not supports_narrative_number(sources, ids, value, unit, context, metric):
                    issues.append(
                        f"Narrative value {raw}{' ' + unit if unit else ''} is not supported by citations {ids}. Use an exact cited value with its original units, or move derived results to the calculation panel."
                    )
    if any(i not in allowed for i in answer["citation_ids"] + inline) or (
        answer["status"] == "answered"
        and (
            not inline
            or not valid(answer["citation_ids"])
            or any(i not in answer["citation_ids"] for i in inline)
        )
    ):
        issues.append("The answer must contain valid evidence citations.")
    for calculation in answer["calculations"]:
        if not valid(calculation["source_ids"]) or not calculation_bound(calculation, sources) or not all(
            supports_number(cited(calculation["source_ids"]), value)
            for value in calculation["values"]
        ):
            issues.append(
                f"{calculation['label']}: inputs must be literal values in the cited evidence."
            )
        try:
            calculate(calculation["operation"], calculation["values"])
        except AppError:
            issues.append(f"{calculation['label']}: invalid operation, input count or denominator.")
        if calculation['operation'] == 'ratio' and re.search(r'%|percent', calculation['unit'], re.I):
            issues.append(f"{calculation['label']}: use the percentage operation for percent units.")
    for point in answer['chart']['points']:
        scale = re.search(r'\b(thousand|million|billion)s?\b', answer['chart'].get('unit', ''), re.I)
        if not valid(point['source_ids']) or bound_value(sources, point['source_ids'], point['value'], point['label'], scale[1].lower() if scale else None) is False or not supports_number(cited(point['source_ids']), point['value']):
            issues.append('Chart values must be literal numbers in their cited evidence; omit an unsupported chart.')
    return list(dict.fromkeys(issues))


def supported_repair(sentences, calculations, sources):
    """Retain independently checked repair units; never reuse an invalid whole answer."""
    kept = []
    for sentence in sentences:
        ids = sentence['source_ids']
        candidate = dict(status='answered', answer=sentence['text'] + ' ' + ' '.join(f'[{i}]' for i in ids),
                         citation_ids=ids, calculations=[], chart=empty_chart())
        # A citation supplied by the model is not proof on its own. Require an
        # exact disclosed sentence for qualitative claims retained after failure.
        text = re.sub(r'\s+', ' ', sentence['text']).strip().rstrip('.')
        excerpts = [re.sub(r'\s+', ' ', s['text']) for s in sources if s['id'] in ids]
        if text and any(text.casefold() in excerpt.casefold() for excerpt in excerpts) and not validation_issues(candidate, sources):
            kept.append(candidate)
    result = dict(status='answered', answer=' '.join(c['answer'] for c in kept),
                  citation_ids=list(dict.fromkeys(i for c in kept for i in c['citation_ids'])),
                  calculations=[], chart=empty_chart())
    for calculation in calculations:
        ids = calculation['source_ids']
        candidate = dict(status='answered', answer='Disclosed inputs are shown in the calculation panel. ' + ' '.join(f'[{i}]' for i in ids),
                         citation_ids=ids, calculations=[calculation], chart=empty_chart())
        if not validation_issues(candidate, sources):
            result['calculations'].append(calculation)
    if not kept:
        if not result['calculations']:
            return None
        result['citation_ids'] = list(dict.fromkeys(i for c in result['calculations'] for i in c['source_ids']))
        result['answer'] = 'Disclosed inputs are shown in the calculation panel. ' + ' '.join(f'[{i}]' for i in result['citation_ids'])
    return finalize(result, sources)


def supported_draft(answer, sources):
    """Salvage only exact checked excerpts and valid calculations after a model timeout."""
    units = []
    for fragment in re.split(r'(?<=\])\s+|\n+', answer['answer']):
        ids = re.findall(r'\[(S\d+)\]', fragment)
        text = re.sub(r'\[S\d+\]', '', fragment).strip()
        if ids and text:
            units.append(dict(text=text, source_ids=ids))
    return supported_repair(units, answer.get('calculations', []), sources)


def finalize(answer, sources):
    allowed = {s["id"] for s in sources}

    def valid(ids, value):
        return (
            bool(ids)
            and all(i in allowed for i in ids)
            and supports_number("\n".join(s["text"] for s in sources if s["id"] in ids), value)
        )

    for point in answer["chart"]["points"]:
        if not valid(point["source_ids"], point["value"]):
            raise AppError(
                "Chart values could not be verified against the cited evidence. Ask for a specific metric and period."
            )
    for calculation in answer["calculations"]:
        if calculation["operation"] == "ratio" and re.search(
            r"%|percent", calculation["unit"], re.I
        ):
            raise AppError("Use the percentage operation for percent units.")
        calculation["result"] = calculate(calculation["operation"], calculation["values"])
    if answer["status"] != "answered":
        answer["chart"], answer["calculations"] = empty_chart(), []
    return finalize_margins(answer)
