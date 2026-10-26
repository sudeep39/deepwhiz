"""Source-bound quarterly statement lookups and explicit period differences."""
import re

from .financial_facts import facts_payload
from .finance import empty_chart


def answer(question, sources, scope=None):
    if not scope or re.search(r'why|drivers?|explain|risks?|cash flow|margin|excluding', question, re.I):
        return None
    names = ' '.join(s.get('company', '') + ' ' + s.get('ticker', '') for s in sources)
    allowed = set(('using filing filings tesla s what was were are is the a an total revenue revenues '
                   'and net income attributable to common stockholders shareholders how did q1 q2 q3 q4 '
                   'compare compared with versus vs in of for between quarter quarterly first second '
                   'third fourth energy generation storage increased gross profit also improve '
                   'automotive regulatory credit credits its reported report').split())
    if set(re.findall(r'[a-z]+\d*', question.lower())) - allowed - set(re.findall(r'[a-z]+', names.lower())):
        return None
    metrics = []
    energy = bool(re.search(r'energy generation and storage', question, re.I))
    if re.search(r'regulatory credits?', question, re.I):
        metrics.append('regulatory_credits')
    elif re.search(r'revenues?', question, re.I):
        metrics.append('energy_revenue' if energy else 'revenue')
    if re.search(r'net income attributable to common (?:stockholders|shareholders)', question, re.I):
        metrics.append('net_income_common')
    gross_profit = energy and bool(re.search(r'gross profit', question, re.I))
    periods = scope.get('requestedPeriods', [])
    if not periods or not metrics or any(not re.fullmatch(r'\d{4}-Q[1-4]', p) for p in periods):
        return None
    # A year-over-year comparison is required when evaluating an asserted increase.
    if gross_profit and len(periods) == 1:
        year, quarter = periods[0].split('-')
        periods = [f'{int(year)-1}-{quarter}', periods[0]]
    facts = facts_payload(sources)

    def cell(ticker, period, metric):
        rows = [f for f in facts if f['ticker'] == ticker and f['period'] == period and f['metric'] == metric]
        if len({(f['value'], f['unit']) for f in rows}) != 1 or rows[0]['unit'] not in ('thousands', 'millions', 'billions'):
            return None
        return rows[0]

    labels = dict(revenue='total revenue', energy_revenue='energy generation and storage revenue',
                  net_income_common='net income attributable to common stockholders',
                  regulatory_credits='automotive regulatory credit revenue')
    lines, ids, calculations = [], [], []
    profits, compared = [], {}
    for ticker in scope.get('tickers', []):
        for period in sorted(set(periods)):
            for metric in metrics:
                fact = cell(ticker, period, metric)
                if fact:
                    lines.append(f"{ticker} {period} {labels[metric]}: {fact['value']:,.10g} {fact['unit']} [{fact['source_id']}].")
                    ids.append(fact['source_id'])
                    compared[ticker,period,metric] = fact['value']
                elif period.endswith('-Q1'):
                    year = period[:4]
                    h1, q2 = cell(ticker, year+'-H1', metric), cell(ticker, year+'-Q2', metric)
                    if not h1 or not q2 or h1['unit'] != q2['unit']:
                        return None
                    cited = list(dict.fromkeys([h1['source_id'], q2['source_id']]))
                    ids.extend(cited)
                    calculations.append(dict(label=f'{ticker} {labels[metric]} {year}-H1 minus {year}-Q2',
                                             operation='difference', values=[h1['value'],q2['value']],
                                             unit=h1['unit'], source_ids=cited))
                    compared[ticker,period,metric] = h1['value'] - q2['value']
                    lines.append(f"{ticker} {period} {labels[metric]}: {h1['value']-q2['value']:,.10g} {h1['unit']}, calculated as the six-month value minus the Q2 value."
                                 + ' ' + ' '.join(f'[{sid}]' for sid in cited))
                else:
                    return None
            if gross_profit:
                revenue, cost = cell(ticker,period,'energy_revenue'), cell(ticker,period,'energy_cost')
                if not revenue or not cost or revenue['unit'] != cost['unit']:
                    return None
                cited = list(dict.fromkeys([revenue['source_id'],cost['source_id']]))
                ids.extend(cited)
                calculations.append(dict(label=f'{ticker} energy generation and storage gross profit {period}',
                                         operation='difference', values=[revenue['value'],cost['value']],
                                         unit=revenue['unit'], source_ids=cited))
                profits.append((ticker,period,revenue['value']-cost['value'],revenue['unit']))
    if not gross_profit:
        for ticker in scope.get('tickers', []):
            for metric in metrics:
                ordered = [(p,compared[ticker,p,metric]) for p in sorted(set(periods)) if (ticker,p,metric) in compared]
                if len(ordered)==2:
                    direction = 'increased' if ordered[1][1]>ordered[0][1] else 'declined' if ordered[1][1]<ordered[0][1] else 'was unchanged'
                    lines.append(f'{ticker} {labels[metric]} {direction} from {ordered[0][0]} to {ordered[1][0]}.')
    if gross_profit:
        for ticker in scope.get('tickers', []):
            values = [p for p in profits if p[0] == ticker]
            if len(values) != 2 or values[0][3] != values[1][3]:
                return None
            direction = 'increased' if values[1][2]>values[0][2] else 'declined' if values[1][2]<values[0][2] else 'was unchanged'
            lines.append(f'{ticker} energy generation and storage gross profit {direction}; see the source-bound calculations below.')
    if not lines:
        return None
    return dict(status='answered', answer='\n\n'.join(lines), citation_ids=list(dict.fromkeys(ids)),
                chart=empty_chart(), calculations=calculations)
