import { mkdir, writeFile, rename, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { companies } from '../companies.mjs';
import { exportExperiment } from './export-experiment.mjs';

export function selectFilings(recent, cik, asOf, periods = 4) {
  const rows = recent.form.flatMap((form, i) => {
    if (!['10-K', '10-Q'].includes(form) || !recent.reportDate[i] || recent.filingDate[i] > asOf || recent.reportDate[i] > asOf) return [];
    const accession = recent.accessionNumber[i];
    const base = `https://www.sec.gov/Archives/edgar/data/${Number(cik)}/${accession.replaceAll('-', '')}`;
    return [{ form, reportDate: recent.reportDate[i], filingDate: recent.filingDate[i], accession,
      document: recent.primaryDocument[i], url: `${base}/${recent.primaryDocument[i]}`,
      indexUrl: `${base}/${accession}-index.html`, coverage: form === '10-K' ? 'Annual report (includes fiscal Q4)' : 'Quarterly report' }];
  }).sort((a, b) => b.reportDate.localeCompare(a.reportDate) || b.filingDate.localeCompare(a.filingDate));
  return rows.filter((row, i) => rows.findIndex(other => other.reportDate === row.reportDate) === i).slice(0, periods);
}

async function request(url) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await fetch(url, { headers: { 'User-Agent': process.env.SEC_USER_AGENT || 'DeepWhiz local research application', Accept: 'application/json' }, signal: AbortSignal.timeout(30000) });
    if (response.ok) return response.json();
    if (![429, 500, 502, 503, 504].includes(response.status) || attempt === 2) throw new Error(`SEC HTTP ${response.status}: ${url}`);
    await new Promise(resolve => setTimeout(resolve, (attempt + 1) * 2000));
  }
}

async function main() {
  const experiment = JSON.parse(await readFile(new URL('../experiment.json', import.meta.url)));
  const asOf = process.argv.find(arg => arg.startsWith('--as-of='))?.split('=')[1] || experiment.as_of;
  const tickerArg = process.argv.find(arg => arg.startsWith('--tickers='))?.split('=')[1];
  const requested = tickerArg ? tickerArg.split(',').map(t => t.toUpperCase()) : null;
  if (requested?.some(t => !companies.some(c => c.tickers.includes(t)))) throw new Error('Unknown ticker in --tickers');
  const periodArg = process.argv.find(arg => arg.startsWith('--periods='))?.split('=')[1];
  const requestedPeriods = periodArg === undefined ? null : Number(periodArg);
  if (requestedPeriods !== null && (!Number.isInteger(requestedPeriods) || requestedPeriods < 1 || requestedPeriods > 40)) throw new Error('--periods must be an integer from 1 to 40');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf) || Number.isNaN(Date.parse(asOf))) throw new Error('Use --as-of=YYYY-MM-DD');
  const root = new URL('../data/', import.meta.url);
  await mkdir(new URL('raw/', root), { recursive: true });
  const result = [];
  let prior;
  try { prior = JSON.parse(await readFile(new URL('filings.json', root))); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (requested && prior && prior.asOf !== asOf) throw new Error('Subset refresh must use the existing catalog cutoff. Refresh all companies to change the cutoff.');
  for (const company of companies.filter(c => !requested || c.tickers.some(t => requested.includes(t)))) {
    const periods = requestedPeriods ?? (experiment.tickers.includes(company.tickers[0]) ? experiment.periods_per_company : 4);
    const sourceUrl = `https://data.sec.gov/submissions/CIK${company.cik}.json`;
    const submissions = await request(sourceUrl);
    const recent = submissions.filings.recent;
    if (selectFilings(recent, company.cik, asOf, periods).length < periods) {
      for (const file of submissions.filings.files || []) {
        const older = await request(`https://data.sec.gov/submissions/${file.name}`);
        for (const key of Object.keys(recent)) recent[key].push(...(older[key] || []));
        if (selectFilings(recent, company.cik, asOf, periods).length >= periods) break;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
    }
    const filings = selectFilings(recent, company.cik, asOf, periods);
    if (filings.length !== periods) throw new Error(`Expected ${periods} reporting periods for ${company.name}, found ${filings.length}`);
    const gaps = filings.slice(1).map((row, i) => (Date.parse(filings[i].reportDate) - Date.parse(row.reportDate)) / 86400000);
    if (gaps.some(days => days < 60 || days > 120)) throw new Error(`Non-consecutive quarterly periods for ${company.name}`);
    await writeFile(new URL(`raw/${company.cik}.json`, root), JSON.stringify(submissions));
    result.push({ ...company, secName: submissions.name, sourceUrl, filings });
    console.log(`${company.tickers[0]}: ${filings.map(row => `${row.form} ${row.reportDate}`).join(', ')}`);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  const merged = requested && prior ? prior.companies.map(c => result.find(r => r.cik === c.cik) || c) : result;
  for (const company of result) if (!merged.some(c => c.cik === company.cik)) merged.push(company);
  if (prior) {
    await mkdir(new URL('archive/', root), { recursive: true });
    const backup = `archive/filings-${prior.asOf}-${prior.companies.reduce((n,c) => n + c.filings.length, 0)}.json`;
    try { await writeFile(new URL(backup, root), JSON.stringify(prior, null, 2) + '\n', { flag: 'wx' }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  const dataset = { asOf, retrievedAt: new Date().toISOString(), source: 'SEC EDGAR submissions API', definition: 'Latest distinct fiscal reporting periods filed by the as-of date; active experiment uses eight for NVDA and TSLA, archived issuers retain four. 10-K covers the year including Q4, not standalone Q4 results. Amendments excluded.', companies: merged };
  await writeFile(new URL('filings.json.tmp', root), JSON.stringify(dataset, null, 2) + '\n');
  await rename(new URL('filings.json.tmp', root), new URL('filings.json', root));
  const csv = ['company,tickers,cik,form,report_date,filing_date,accession,url', ...merged.flatMap(company => company.filings.map(row => [company.name, company.tickers.join(' / '), company.cik, row.form, row.reportDate, row.filingDate, row.accession, row.url].map(value => `"${value.replaceAll('"', '""')}"`).join(',')))].join('\n');
  await writeFile(new URL('filings.csv', root), csv + '\n');
  if (asOf === experiment.as_of) await exportExperiment();
}
if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
