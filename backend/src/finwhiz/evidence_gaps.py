"""Explain evidence gaps using recorded scope and agent tasks, without another model call."""
import re

REQUIREMENTS = {
    'revenue': 'the revenue line and its reporting period and units',
    'cost': 'the cost-of-revenue line and its reporting period and units',
    'gross_margin': 'reported gross margin, or revenue and cost of revenue for the same period and accounting basis',
    'capex': 'the cash-flow statement line for purchases of property, plant and equipment, with the period, units and sign',
    'net_income': 'the net-income line for the requested period and accounting basis',
}


def evidence_gaps(question, scope=None, tasks=None, missing=None, decision=None,
                  verification_failed=False, timed_out=False):
    scope, tasks, decision = scope or {}, tasks or [], decision or {}
    if timed_out:
        return [dict(part=question, reason='verification_timeout',
                     needed='Retry this question with the existing filings. Verification did not finish, so a missing document has not been established.',
                     action='retry')]
    gaps = []
    for filing in missing or []:
        ticker, day = filing.get('ticker', ''), filing.get('reportDate', '')
        gaps.append(dict(part=f'{ticker}: evidence from the filing ending {day}', reason='missing_evidence',
                         needed=f'Upload or reprocess the {ticker} filing ending {day}, including the relevant statements, notes and management discussion.', action='upload'))
    for task in tasks:
        if task.get('status') not in ('unresolved', 'conflicting_values'):
            continue
        metric = task.get('metric', '').replace(' ', '_').lower()
        requirement = REQUIREMENTS.get(metric, f'disclosures supporting: {task.get("objective") or metric.replace("_", " ")}')
        location = ' · '.join(str(task[k]) for k in ('ticker', 'period') if task.get(k))
        conflicting = task.get('status') == 'conflicting_values'
        gaps.append(dict(part=f'{location}: {task.get("objective") or metric.replace("_", " ")}',
                         reason='conflicting_evidence' if conflicting else 'missing_evidence',
                         needed=f'{"Confirm the correct values and accounting basis for" if conflicting else "Provide"} {requirement}. Include the original statement or note from the relevant filing.',
                         action='review' if conflicting else 'upload'))
    for fact in decision.get('missing_evidence', []):
        gaps.append(dict(part=str(fact)[:240], reason='missing_evidence',
                         needed=f'Provide the relevant statement, note or management discussion disclosing {str(fact)[:240]} for the selected companies and periods.', action='upload'))
    if not gaps and scope.get('status') == 'insufficient_evidence':
        periods = ', '.join(scope.get('requestedPeriods', []))
        companies = ', '.join(scope.get('tickers', []))
        gaps.append(dict(part=scope.get('message') or question, reason='filing_coverage',
                         needed=f'Provide filings for {companies or "the requested company"}{" covering " + periods if periods else " and the requested period"}, with identifiable fiscal period ends.{(' A Q4 derivation requires the annual filing and the matching nine-month statement.' if any(str(p).endswith('-Q4') for p in scope.get('requestedPeriods', [])) else '')}', action='upload'))
    if not gaps:
        # Finding passages does not establish that they support every requested claim.
        objectives = list(dict.fromkeys(' · '.join(str(t.get(k, '')) for k in ('ticker', 'period', 'objective') if t.get(k)) for t in tasks if t.get('objective')))
        requirements = [REQUIREMENTS[t['metric']] for t in tasks if t.get('metric') in REQUIREMENTS]
        if re.search(r'\bcapex\b|capital expenditures?', question, re.I):
            requirements.append(REQUIREMENTS['capex'])
        if re.search(r'gross margin', question, re.I):
            requirements.append(REQUIREMENTS['gross_margin'])
        if re.search(r'\bdrivers?|why\b', question, re.I):
            requirements.append('management discussion or notes explicitly explaining the requested performance drivers')
        if re.search(r'\brisks?\b', question, re.I):
            requirements.append('the relevant risk-factor disclosures or management discussion of those risks')
        needed = '; '.join(dict.fromkeys(requirements))
        gaps.append(dict(part='; '.join(objectives[:4]) or question,
                         reason='verification_failed' if verification_failed else 'missing_evidence',
                         needed=f'Provide {needed or "a clear source excerpt or filing statement supporting this part of the question"}. Identify the company, financial period, units and accounting basis. Retrieved passages could not establish a complete supported answer.', action='review'))
    unique = {gap['part']: gap for gap in gaps}
    return list(unique.values())[:8]
