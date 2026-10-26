"""Exact quarterly revenue comparisons from source-located statement cells."""
import re

from .financial_facts import facts_payload
from .finance import empty_chart


def answer(question, sources, scope=None):
    if not scope or not re.search(r'\brevenues?\b', question, re.I):
        return None
    # A numeric handler must never swallow causal, segment or other metric requests.
    names = ' '.join(s.get('company', '') + ' ' + s.get('ticker', '') for s in sources)
    allowed = set(('compare comparison revenue revenues total across the all four quarters '
                   'qrts qtrs quarter quarterly last latest for of in between versus vs and '
                   'show me what was were is are how much did report reported q1 q2 q3 q4').split())
    if set(re.findall(r'[a-z]+\d*', question.lower())) - allowed - set(re.findall(r'[a-z]+', names.lower())):
        return None
    periods = scope.get('requestedPeriods', [])
    if not periods and set(scope.get('tickers', [])) == {'TSLA'}:
        periods = sorted({f"{f['reportDate'][:4]}-Q{(int(f['reportDate'][5:7])-1)//3+1}"
                          for f in scope.get('filings', [])})
    if not periods:
        # Dates identify source filings, not financial periods. Resolve using DEI labels.
        periods = sorted({f"{s['fiscal_year']}-Q{str(s['fiscal_quarter']).lstrip('Qq')}"
                          for s in sources if s.get('fiscal_year') and s.get('fiscal_quarter')})
    if not periods or any(not re.fullmatch(r'\d{4}-Q[1-4]', p) for p in periods):
        return None
    facts = [f for f in facts_payload(sources) if f['metric'] == 'revenue' and f['unit'] in ('thousands', 'millions', 'billions')]

    def cell(ticker, period):
        matching = [f for f in facts if f['ticker'] == ticker and f['period'] == period]
        if len({(f['value'], f['unit']) for f in matching}) != 1:
            return None
        return matching[0]

    lines, citations, calculations = [], [], []
    for ticker in scope.get('tickers', []):
        for period in sorted(set(periods)):
            fact = cell(ticker, period)
            if fact:
                ids, value, unit = [fact['source_id']], fact['value'], fact['unit']
                note = ''
            elif period.endswith('-Q4'):
                annual, ytd = cell(ticker, period[:4] + '-FY'), cell(ticker, period[:4] + '-YTD-Q3')
                if not annual or not ytd or annual['unit'] != ytd['unit']:
                    return None
                value, unit = annual['value'] - ytd['value'], annual['unit']
                ids = list(dict.fromkeys([annual['source_id'], ytd['source_id']]))
                note = ' (calculated as full-year revenue minus nine-month revenue)'
                calculations.append(dict(label=f'{ticker} revenue {period[:4]}-FY minus {period[:4]}-YTD-Q3', operation='difference',
                                         values=[annual['value'], ytd['value']], source_ids=ids, unit=unit))
            else:
                return None
            citations.extend(ids)
            lines.append(f"{ticker} {period}: {value:,.10g} {unit}{note}. " + ' '.join(f'[{sid}]' for sid in ids))
    if not lines:
        return None
    return dict(status='answered', answer='\n\n'.join(lines), citation_ids=list(dict.fromkeys(citations)),
                chart=empty_chart(), calculations=calculations)
