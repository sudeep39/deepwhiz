"""Bind numerical claims and calculation operands to financial fact identities."""
import re
from .financial_facts import table_facts, number, metric_name


def claim_periods(text):
    return set(ordered_periods(text))


def claim_metric(text):
    patterns = [(r'regulatory credits?|credit revenue', 'regulatory_credits'),
                (r'net income attributable to (?:common stockholders|common shareholders)', 'net_income_common'),
                (r'net income', 'net_income'), (r'automotive sales', 'automotive_sales'),
                (r'cost(?:s| of revenues?)?', 'cost'), (r'revenues?|net sales', 'revenue')]
    matches = [(m.start(), m.end(), metric) for pattern, metric in patterns for m in re.finditer(pattern, text, re.I)]
    if not matches:
        return None
    # "credit revenue" and "cost of revenue" are compound metric names.
    matches = [m for m in matches if not any(other[0] < m[0] and other[1] >= m[1] for other in matches)]
    _, _, metric = max(matches)
    if metric in ('cost', 'revenue') and re.search(r'energy generation and storage', text, re.I):
        return 'energy_' + metric
    return 'automotive_' + metric if metric in ('cost', 'revenue') and re.search(r'automotive', text, re.I) else metric


def numeric_context(text, position):
    prefix = text[:position]
    metric = claim_metric(prefix) or claim_metric(text)
    periods = ordered_periods(prefix)
    if periods:
        without_periods = re.sub(r'\b(?:20\d{2}|Q[1-4]|H[12])\b', '', prefix, flags=re.I)
        return periods[-1] + ' ' + without_periods, metric
    return text, metric


def ordered_periods(text):
    kind_pattern = r'(?:YTD-Q[1-4]|Q[1-4]|H[12]|FY)'
    kinds = set(re.findall(r'\b' + kind_pattern + r'\b', text, re.I))
    if re.search(r'first (?:six months|half)', text, re.I):
        kinds.add('H1')
    years = list(dict.fromkeys(re.findall(r'\b20\d{2}\b', text)))
    if len(kinds) == 1:
        return [f'{year}-{next(iter(kinds)).upper()}' for year in years]
    result = []
    for match in re.finditer(r'\b(' + kind_pattern + r')\s+(?:of\s+|in\s+)?(20\d{2})\b|\b(20\d{2})[- ](' + kind_pattern + r')\b', text, re.I):
        period = f'{match[2] or match[3]}-{(match[1] or match[4]).upper()}'
        if period not in result:
            result.append(period)
    return result


def bound_value(sources, ids, value, text, unit=None, metric=None):
    """None means no parsed table; False means a parsed fact contradicts/omits it."""
    cited = [s for s in sources if s['id'] in ids]
    facts = [fact for source in cited for fact in table_facts(source)]
    if not facts:
        # Explicit period-marked narrative must not validate another year's number.
        periods = claim_periods(text)
        if periods:
            fragments = []
            for source in cited:
                chunks = re.split(r'(?=\bQ[1-4]\s+20\d{2}\b)', source.get('text', ''))
                fragments.extend(chunk for chunk in chunks if claim_periods(chunk) & periods)
            if fragments:
                from .finance import supports_number
                return any(supports_number(fragment, value) for fragment in fragments)
        numeric_rows = [line for s in cited for line in s.get('text', '').splitlines()
                        if '|' in line and any(number(c.strip()) is not None for c in line.split('|')[1:])]
        if numeric_rows:
            # A compact, undated quoted row can support its own metric. A
            # multi-year table without interpretable columns cannot.
            if periods or any(re.search(r'\b20\d{2}\b', line) for line in numeric_rows):
                return False
            metric = metric or claim_metric(text)
            matching = []
            for source in cited:
                scales = {'thousand': 1e3, 'million': 1e6, 'billion': 1e9}
                units = {u.lower().rstrip('s') for u in re.findall(r'\bin\s+(thousands|millions|billions)\b', source.get('text', ''), re.I)}
                for line in source.get('text', '').splitlines():
                    if '|' not in line:
                        continue
                    label, *cells = [c.strip() for c in line.split('|')]
                    name = metric_name(label)
                    if not ((metric and (name == metric or name.endswith('_' + metric)))
                            or (not metric and re.search(r'\b' + re.escape(label) + r'\b', text, re.I))):
                        continue
                    adjusted = value
                    if unit:
                        if len(units) != 1:
                            continue
                        adjusted *= scales[unit.rstrip('s')] / scales[next(iter(units))]
                    matching.append(any(number(cell) == adjusted for cell in cells))
            return any(matching)
        return None
    periods = claim_periods(text)
    years = set(re.findall(r'\b20\d{2}\b', text))
    if not periods and len({f.period for f in facts if not years or f.period[:4] in years}) != 1:
        return False
    metric = metric or claim_metric(text)
    if metric is None:
        named_metrics = {f.metric for f in facts if re.search(r'\b' + re.escape(f.metric.replace('_', ' ')) + r'\b', text, re.I)}
        if len(named_metrics) != 1:
            return False
        metric = next(iter(named_metrics))
    tickers = {s.get('ticker', '') for s in cited}
    named = {ticker for ticker in tickers if ticker and re.search(r'\b' + re.escape(ticker) + r'\b', text, re.I)}
    if len(tickers) > 1 and not named:
        return False
    scales = {'thousand': 1e3, 'million': 1e6, 'billion': 1e9}
    for fact in facts:
        if periods and fact.period not in periods or not periods and years and fact.period[:4] not in years:
            continue
        if named and fact.ticker not in named or metric and fact.metric != metric:
            continue
        adjusted = value
        if unit:
            if fact.unit.rstrip('s') not in scales:
                continue
            adjusted *= scales[unit.rstrip('s')] / scales[fact.unit.rstrip('s')]
        if abs(fact.value - adjusted) <= max(1e-9, abs(adjusted) * 1e-9):
            return True
    return False


def calculation_bound(calculation, sources):
    operation, values, label = calculation['operation'], calculation['values'], calculation['label']
    facts = [f for s in sources if s['id'] in calculation['source_ids'] for f in table_facts(s)]
    if not facts:
        return all(bound_value(sources, calculation['source_ids'], v, label) is not False for v in values)
    gross_profit = operation == 'difference' and bool(re.search(r'gross profit', label, re.I))
    if operation in ('gross_margin', 'gross_margin_excluding_revenue') or gross_profit:
        automotive = bool(re.search(r'automotive', label, re.I))
        energy = bool(re.search(r'energy generation and storage', label, re.I))
        metrics = ['automotive_revenue' if automotive else 'energy_revenue' if energy else 'revenue',
                   'automotive_cost' if automotive else 'energy_cost' if energy else 'cost']
        if operation == 'gross_margin_excluding_revenue':
            metrics.append('regulatory_credits' if automotive else 'excluded_revenue')
    else:
        metrics = [claim_metric(label)] * len(values)
    periods = claim_periods(label)
    if not periods:
        return False
    candidates = [f for f in facts if not periods or f.period in periods]
    named_tickers = {s.get('ticker') for s in sources if s.get('ticker') and re.search(r'\b' + re.escape(s['ticker']) + r'\b', label, re.I)}
    if named_tickers:
        candidates = [f for f in candidates if f.ticker in named_tickers]
    elif len({f.ticker for f in candidates}) > 1:
        return False
    groups = {(f.ticker, f.period, f.unit) for f in candidates}
    # Single-period operations cannot mix quarterly/YTD or different companies.
    if operation in ('gross_margin', 'gross_margin_excluding_revenue', 'sum', 'ratio', 'percentage') or gross_profit:
        if len(periods) != 1:
            return False
        return any(all(any((f.ticker, f.period, f.unit) == group and (not metric or f.metric == metric)
                               and f.value == value for f in candidates)
                           for value, metric in zip(values, metrics)) for group in groups)
    # Cross-period differences/growth require a unique requested metric and an
    # unambiguous period per operand; the model must name the periods in its label.
    ordered = ordered_periods(label)
    return len(ordered) == len(values) and bool(claim_metric(label)) and any(all(
        any(f.ticker == ticker and f.period == period and f.unit == unit
            and f.metric == metric and f.value == value for f in facts)
        for value, metric, period in zip(values, metrics, ordered))
        for ticker, _, unit in groups)
