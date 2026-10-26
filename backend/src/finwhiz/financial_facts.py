"""Conservative, source-located financial facts from flattened filing tables.

Unknown layouts remain unparsed: a number's presence alone is not a table fact.
"""
import re
import unicodedata
import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FinancialFact:
    """Immutable financial value with company, period, metric and cell location.

    financial_facts.table_facts constructs these records. Calculation handlers
    and fact_validation consume them; facts_payload serializes selected facts
    for model prompts. row and column are zero-based in the supplied text."""
    source_id: str
    ticker: str
    period: str
    metric: str
    value: float
    unit: str
    row: int
    column: int
    filing_date: str


METRICS = {
    'automotive_revenue': r'total automotive revenues?',
    'automotive_cost': r'total automotive cost of revenues?',
    'regulatory_credits': r'automotive regulatory credits(?: revenues?)?',
    'energy_revenue': r'energy generation and storage(?: segment revenue)?',
    'revenue': r'(?:total )?revenues?|net sales',
    'cost': r'(?:total )?cost of (?:revenues?|sales)',
    'net_income_common': r'net income attributable to (?:common stockholders|common shareholders)',
    'net_income': r'net income',
}
MONTHS = {name: i for i, name in enumerate(
    'January February March April May June July August September October November December'.split(), 1)}


def metric_name(label):
    label = re.sub(r'\s+', ' ', label.strip()).rstrip(':')
    return next((name for name, pattern in METRICS.items() if re.fullmatch(pattern, label, re.I)),
                re.sub(r'\W+', '_', label.lower()).strip('_'))


def number(cell):
    cell = ''.join(str(unicodedata.digit(c)) if c.isdecimal() else c for c in cell)
    cell = cell.replace('\u2212', '-').replace('\u066c', ',').replace('\u066b', '.')
    cell = cell.strip().replace(',', '').replace('$', '').strip()
    if cell in ('—', '–', '-'):
        return 0.0
    if not re.fullmatch(r'\(?[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?%?\)?', cell):
        return None
    value = float(cell.strip('()%')) * (-1 if cell.startswith('(') else 1)
    return value if math.isfinite(value) else None


def _period(header, year, source):
    duration = re.search(r'(Three|Six|Nine|Twelve) Months Ended|Year[s]? Ended', header, re.I)
    date = re.search(r'(' + '|'.join(MONTHS) + r')\s+(\d{1,2})', header, re.I)
    if not duration or not date:
        return None
    month = next(value for key, value in MONTHS.items() if key.lower() == date[1].lower())
    if source.get('ticker') == 'TSLA':
        quarter = (month - 1) // 3 + 1
    elif source.get('fiscal_quarter') and source.get('fiscal_year'):
        # Fiscal labels are usable only for this filing's end date. Do not infer
        # another issuer's calendar from Tesla's fiscal calendar.
        if source.get('report_date', '')[5:7] != f'{month:02}':
            return None
        quarter_text = str(source['fiscal_quarter']).lstrip('Qq')
        if quarter_text not in ('1', '2', '3', '4'):
            return None
        quarter = int(quarter_text)
        year = str(int(year) + int(source['fiscal_year']) - int(source['report_date'][:4]))
    else:
        return None
    kind = (duration[1] or 'Twelve').lower()
    if kind == 'six' and quarter != 2 or kind == 'nine' and quarter != 3 or kind == 'twelve' and quarter != 4:
        return None
    suffix = {'three': f'Q{quarter}', 'six': 'H1', 'nine': 'YTD-Q3', 'twelve': 'FY'}[kind]
    return f'{year}-{suffix}'


def table_facts(source):
    lines = source.get('text', source.get('excerpt', '')).splitlines()
    facts, headers, years = [], [], []
    cost_section = False
    unit_matches = set(re.findall(r'\bin\s+(thousands|millions|billions)\b', '\n'.join(lines), re.I))
    unit = next(iter(unit_matches)).lower() if len(unit_matches) == 1 else 'unknown'
    preceding = ''
    for row, line in enumerate(lines):
        if '|' not in line:
            if re.search(r'Months Ended|Years? Ended', line, re.I):
                preceding = line
            continue
        cells = [cell.strip() for cell in line.split('|')]
        if any(re.search(r'Months Ended|Years? Ended', c, re.I) for c in cells):
            headers, current = [], preceding
            years = []
            for cell in cells:
                if re.search(r'Months Ended|Years? Ended', cell, re.I):
                    current = cell
                headers.append(current)
            continue
        if sum(bool(re.fullmatch(r'20\d{2}', c)) for c in cells) >= 2:
            years = cells
            if not headers:
                headers = [preceding] * len(cells)
            continue
        if not years or not cells[0] or number(cells[0]) is not None:
            continue
        metric = metric_name(cells[0])
        if re.fullmatch(r'(?:total )?cost of (?:revenues?|sales)', cells[0], re.I) and all(number(c) is None for c in cells[1:]):
            cost_section = True
        elif re.search(r'gross profit|operating expenses|income from operations', cells[0], re.I):
            cost_section = False
        if cost_section and metric not in ('cost', 'automotive_cost'):
            metric = 'energy_cost' if metric == 'energy_revenue' else metric + '_cost'
        for column, cell in enumerate(cells[1:], 1):
            if column >= len(years) or column >= len(headers) or not re.fullmatch(r'20\d{2}', years[column]):
                continue
            value = number(cell)
            period = _period(headers[column], years[column], source)
            if value is None or period is None:
                continue
            fact_unit = '%' if '%' in cell else 'per_share' if re.search(r'per share', cells[0], re.I) else unit
            facts.append(FinancialFact(source['id'], source.get('ticker', ''), period, metric,
                                       value, fact_unit, row, column, source.get('report_date', '')))
    return list(dict.fromkeys(facts))


def facts_payload(sources):
    unique = {}
    for source in sources:
        for fact in table_facts(source):
            if fact.metric not in METRICS and fact.metric != 'energy_cost':
                continue
            key = fact.source_id, fact.ticker, fact.period, fact.metric, fact.value, fact.unit
            unique.setdefault(key, asdict(fact))
    return list(unique.values())


def complete_tables(store, rows, predicate):
    """Load a parent once and deduplicate slices of the same complete table."""
    parents, seen, result = {}, set(), []
    for row in rows:
        m = row['metadata']
        if m.get('chunk_type') != 'table':
            continue
        key = f'parents/{m["corpus_id"]}/{m["doc_id"]}'
        identity = key, m.get('parent_id')
        if identity in seen:
            continue
        seen.add(identity)
        if key not in parents:
            parents[key] = store.json(key, [])
        try:
            index = int(m['parent_id'].rsplit(':', 1)[1])
        except (KeyError, ValueError, IndexError):
            continue
        if not 0 <= index < len(parents[key]):
            continue
        parent = parents[key][index]
        if parent.get('kind') != 'table' or not predicate(parent['text']):
            continue
        prefix = f"{m['company']} | {m['ticker']} | {m['form']} | Period ended {m['report_date']}\n{m['section_path']}\n"
        if index > 0 and parents[key][index-1].get('kind') != 'table':
            caption = parents[key][index-1]['text']
            if re.search(r'\bin\s+(?:thousands|millions|billions)\b', caption, re.I):
                prefix += caption[-2000:] + '\n'
        result.append({**row, 'metadata': {**m, 'text': prefix + parent['text'], 'complete_table_attached': True}})
    return result
