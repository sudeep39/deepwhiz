"""Deterministic company/period resolution, independent of embeddings and LLMs."""
import re

ALIASES = {'TSLA': ['Tesla', 'Tesla Inc'], 'NVDA': ['Nvidia', 'NVIDIA Corporation']}
MONTHS = {name.lower(): i for i, name in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], 1)}


def resolve_scope(question, documents, tickers, periods=None, history=None):
    """Resolve question mentions against the catalog and UI-selected boundaries.

    Called by ResearchEndpoints for preflight/chat and by the research policy
    node for direct callers. documents are catalog dictionaries; periods are
    selected filing end dates. Returns a resolved/clarification/insufficiency
    dictionary with requested financial periods and authorized source filings.
    History can supply follow-up context. Performs no model or vector calls.
    """
    question = re.sub(r'\b((?:19|20|21)\d{2})\s*[-/]?\s*Q([1-4])\b', r'Q\2 \1', question, flags=re.I)
    question = re.sub(r'\bQ([1-4])\s*FY\s*((?:19|20|21)\d{2})\b', r'Q\1 \2', question, flags=re.I)
    periods = periods or []
    catalog = [doc for doc in documents if doc.get('ticker') and doc.get('reportDate')]
    companies = {}
    for doc in catalog:
        companies.setdefault(doc['ticker'], set()).update([doc.get('company', ''), *ALIASES.get(doc['ticker'], [])])
    for ticker, aliases in ALIASES.items():
        companies.setdefault(ticker, set()).update(aliases)

    def response(status, message, selected=None, filings=None, requested=None):
        selected = selected if selected is not None else list(tickers)
        filings = filings or []
        return dict(status=status, message=message, tickers=selected,
                    filingDates=sorted({f['reportDate'] for f in filings}), filings=filings,
                    requestedPeriods=requested or [],
                    label=('Searching ' if status == 'resolved' else 'Requested scope: ') + ', '.join(selected) + (' | Filings: ' + ', '.join(sorted({f['reportDate'] for f in filings})) if filings else ''))

    explicit = []
    for ticker, aliases in companies.items():
        # Lowercase short words such as "a" must not be interpreted as tickers.
        symbol = re.search(r'(?<![\w.-])\$?' + re.escape(ticker) + r'(?![\w.-])', question, re.I if ticker in ALIASES else 0)
        if symbol or any(alias and re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', question, re.I) for alias in aliases if alias != ticker):
            explicit.append(ticker)
    if explicit and not set(explicit).issubset(tickers):
        return response('needs_clarification', 'Your question names ' + ', '.join(explicit) + ' but the selected companies are ' + ', '.join(tickers) + '. Change the company selection or clarify which company to use.')
    selected = explicit or list(tickers)
    available = [doc for doc in catalog if doc['ticker'] in selected]
    if not available:
        return response('insufficient_evidence', 'No indexed filings are available for the requested companies.', selected)

    # Follow-up scope is derived again from the prior question and this catalog.
    followup = re.search(r'\b(?:that|same|those)\s+(?:quarter|period|year|company)\b|^(?:and\b|what about\b|how about\b|why\b)', question, re.I)
    previous = next((item for item in reversed(history or []) if item.get('role') == 'user'), None)
    context = ''
    if followup and previous:
        previous_scope = previous.get('scope')
        if isinstance(previous_scope, dict) and previous_scope.get('status') == 'resolved':
            saved_tickers = previous_scope.get('tickers', [])
            saved_dates = previous_scope.get('filingDates', [])
            if isinstance(saved_tickers, list) and saved_tickers and all(isinstance(t, str) for t in saved_tickers):
                if not set(saved_tickers).issubset(selected):
                    return response('needs_clarification', 'The previous company differs from your current selection. Which company should I use?', selected)
                if not explicit:
                    selected = saved_tickers
                    available = [doc for doc in available if doc['ticker'] in selected]
            if isinstance(saved_dates, list) and all(isinstance(d, str) for d in saved_dates):
                context = previous.get('content', '')
                labels = previous_scope.get('requestedPeriods', [])
                if isinstance(labels, list):
                    context += ' ' + ' '.join(label for label in labels[:32] if isinstance(label, str) and re.fullmatch(r'\d{4}-(?:Q[1-4]|H[12])', label))
                inherited_dates = saved_dates
            else:
                inherited_dates = []
        else:
            context = previous.get('content', '')
            inherited_dates = []
    else:
        inherited_dates = []

    def parse(text):
        years = sorted(set(int(y) for y in re.findall(r'\b(?:FY\s*)?((?:19|20|21)\d{2})\b', text, re.I)))
        quarters = list(dict.fromkeys(int(q) for q in re.findall(r'\bQ([1-4])\b', text, re.I)))
        words = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, '1st': 1, '2nd': 2, '3rd': 3, '4th': 4}
        for word in re.findall(r'\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:fiscal\s+)?quarter\b', text, re.I):
            if words[word.lower()] not in quarters:
                quarters.append(words[word.lower()])
        half = re.search(r'\b(first|second)\s+(?:half|six months)\b|\bH([12])\b', text, re.I)
        half_num = (int(half[2]) if half[2] else (1 if half[1].lower() == 'first' else 2)) if half else None
        dates = re.findall(r'\b\d{4}-\d{2}-\d{2}\b', text)
        for month, day, year in re.findall(r'\b(' + '|'.join(MONTHS) + r')\s+(\d{1,2}),?\s+(\d{4})\b', text, re.I):
            dates.append(f'{year}-{MONTHS[month.lower()]:02d}-{int(day):02d}')
        return years, quarters, half_num, list(dict.fromkeys(dates))

    years, quarters, half, dates = parse(question)
    if re.search(r'\bQ(?:0|[5-9]|\d{2,})\b', question, re.I):
        return response('needs_clarification', 'A fiscal quarter must be Q1, Q2, Q3 or Q4. Which period should I use?', selected)
    if re.search(r'\b(?:latest|last|previous|next|current)\s+(?:quarter|year)\b|\b(?:year.over.year|quarter.over.quarter|YoY|QoQ)\b', question, re.I):
        return response('needs_clarification', 'Please specify the fiscal years and quarters to compare, or select their filing dates.', selected)
    if context:
        old_years, old_quarters, old_half, old_dates = parse(context)
        if not years:
            years = old_years
        if not quarters and not half and not dates:
            quarters, half, dates = old_quarters, old_half, old_dates
    requested = dates or [f'{year if year else "unspecified"}-Q{quarter}' for year in (years or [None]) for quarter in quarters]
    if half:
        requested += [f'{year if year else "unspecified"}-H{half}' for year in (years or [None])]

    chosen = []
    if dates:
        chosen = [doc for doc in available if doc['reportDate'] in dates]
        if set(dates) - {doc['reportDate'] for doc in chosen}:
            return response('insufficient_evidence', 'The requested filing date is not available in the indexed library.', selected, requested=requested)
    elif quarters or half:
        known = [doc for doc in available if doc.get('fiscal_year') and doc.get('fiscal_quarter')]
        if not years:
            candidates = {doc['fiscal_year'] for doc in known if (not periods or doc['reportDate'] in periods) and doc['fiscal_quarter'] in (quarters or [2 if half == 1 else 4])}
            if len(candidates) != 1:
                return response('needs_clarification', 'Which fiscal year should I use? Please specify a year, such as Q2 2026.', selected)
            years = sorted(candidates)
        # Preserve textual pairings; do not turn "Q2 2026 vs Q1 2025" into four periods.
        pairs = [(int(year), int(q)) for q, year in re.findall(r'\bQ([1-4])\s+(?:FY\s*)?((?:19|20|21)\d{2})\b', question, re.I)]
        if len(quarters) == 1 and len(years) > len({year for year, q in pairs}):
            pairs = [(year, quarters[0]) for year in years]
        if len(pairs) < len(quarters):
            if len(years) > 1 and len(quarters) > 1:
                return response('needs_clarification', 'Please pair each quarter with its fiscal year explicitly, for example Q2 2026 versus Q1 2025.', selected)
            pairs = [(year, q) for year in years for q in quarters]
        if half:
            pairs += [(year, 2 if half == 1 else 4) for year in years]
        requested = ([f'{year}-Q{q}' for year, q in pairs] if quarters else []) + ([f'{year}-H{half}' for year in years] if half else [])
        source_filing = re.search(r'\b(?:using|from|based on|according to)\b[^?.]{0,100}?\bQ([1-4])\s+((?:19|20|21)\d{2})\s+filing\b', question, re.I)
        for ticker in selected:
            for year, q in pairs:
                candidates = [doc for doc in known if doc['ticker'] == ticker and doc['fiscal_year'] == year and doc['fiscal_quarter'] == q]
                if q == 1 and ((source_filing and source_filing[1] == '2' and int(source_filing[2]) == year) or
                               (periods and not any(doc['reportDate'] in periods for doc in candidates))):
                    q2 = [doc for doc in known if doc['ticker'] == ticker and doc['fiscal_year'] == year and doc['fiscal_quarter'] == 2 and doc.get('form') == '10-Q' and (not periods or doc['reportDate'] in periods)]
                    if q2:
                        candidates = q2
                if not candidates and q == 1:
                    # Q2 YTD minus Q2 can support Q1 without a separate Q1 filing.
                    candidates = [doc for doc in known if doc['ticker'] == ticker and doc['fiscal_year'] == year and doc['fiscal_quarter'] == 2 and doc.get('form') == '10-Q']
                if not candidates or (periods and not any(d['reportDate'] in periods for d in candidates)):
                    # A later same-quarter 10-Q can contain the requested prior-year
                    # comparative columns. Retrieval/validation must verify those cells.
                    comparative = [doc for doc in known if doc['ticker'] == ticker and doc['fiscal_year'] == year + 1
                                   and doc['fiscal_quarter'] == q and doc.get('form') == '10-Q'
                                   and (not periods or doc['reportDate'] in periods)]
                    if comparative:
                        candidates = comparative
                if not candidates:
                    status = 'needs_clarification' if any(not d.get('fiscal_year') for d in available if d['ticker'] == ticker) else 'insufficient_evidence'
                    return response(status, f'No verified filing mapping is available for {ticker} fiscal Q{q} {year}. Select an available filing date or upload the required filing.', selected, requested=requested)
                chosen.extend(candidates)
                if q == 4:
                    prior_q = 2 if half == 2 else 3
                    earlier = [doc for doc in known if doc['ticker'] == ticker and doc['fiscal_year'] == year and doc['fiscal_quarter'] == prior_q]
                    if not earlier:
                        return response('insufficient_evidence', f'Deriving {"the second half" if half == 2 else "Q4"} requires the annual filing and the Q{prior_q} year-to-date filing for {ticker} fiscal {year}.', selected, requested=requested)
                    chosen.extend(earlier)
    elif years:
        annual = bool(re.search(r'\b(?:annual|full.year|FY\s*\d{4})\b', question, re.I))
        chosen = [doc for doc in available if doc.get('fiscal_year') in years and (not annual or doc.get('period_type') == 'annual')]
        if any(not any(doc['ticker'] == ticker and doc.get('fiscal_year') == year for doc in chosen) for ticker in selected for year in years):
            return response('insufficient_evidence', 'No verified fiscal-year mapping is available for the requested year.', selected, requested=[str(y) for y in years])
        requested = [str(y) for y in years]
    else:
        scope_dates = periods or inherited_dates
        chosen = [doc for doc in available if not scope_dates or doc['reportDate'] in scope_dates]

    if periods and any(doc['reportDate'] not in periods for doc in chosen):
        return response('needs_clarification', 'The question requires filings outside the selected dates. Select all periods or the required filing dates, then retry.', selected, requested=requested)
    if any(not any(doc['ticker'] == ticker for doc in chosen) for ticker in selected):
        return response('insufficient_evidence', 'The selected filings do not cover every requested company.', selected, requested=requested)
    filings = [dict(ticker=ticker, reportDate=day) for ticker, day in sorted({(d['ticker'], d['reportDate']) for d in chosen})]
    return response('resolved', '', selected, filings, requested)
