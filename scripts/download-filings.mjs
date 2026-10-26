import { readFile, writeFile, mkdir, rename } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const root = new URL('../data/', import.meta.url);
const catalog = JSON.parse(await readFile(new URL('filings.json', root)));
const total = catalog.companies.reduce((n, company) => n + company.filings.length, 0);
const entries = [];
const failures = [];
await mkdir(new URL('filings/', root), { recursive: true });
for (const company of catalog.companies) {
  await mkdir(new URL(`filings/${company.tickers[0]}/`, root), { recursive: true });
  for (const filing of company.filings) {
    const relativePath = `filings/${company.tickers[0]}/${filing.reportDate}_${filing.form}_${filing.accession}.html`;
    const target = new URL(relativePath, root);
    try {
      let bytes;
      try { bytes = await readFile(target); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      if (!bytes) {
        for (let attempt = 0; attempt < 3; attempt++) {
          const response = await fetch(filing.url, { headers: { 'User-Agent': process.env.SEC_USER_AGENT || 'DeepWhiz local research application', Accept: 'text/html' }, signal: AbortSignal.timeout(60000) });
          if (response.ok) { bytes = Buffer.from(await response.arrayBuffer()); break; }
          if (![429, 500, 502, 503, 504].includes(response.status) || attempt === 2) throw new Error(`HTTP ${response.status}`);
          await new Promise(resolve => setTimeout(resolve, 2000 * (attempt + 1)));
        }
      }
      const html = bytes.toString('utf8');
      if (bytes.length < 10000 || !/<(?:html|ix:header)\b/i.test(html) || /Your Request Originates from an Undeclared Automated Tool|Request Rate Threshold Exceeded/i.test(html)) throw new Error('Response is not a complete filing HTML document');
      await writeFile(new URL(`${relativePath}.tmp`, root), bytes);
      await rename(new URL(`${relativePath}.tmp`, root), target);
      entries.push({ company: company.name, ticker: company.tickers[0], cik: company.cik, ...filing, localPath: `data/${relativePath}`, bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') });
      console.log(`${entries.length}/${total} ${company.tickers[0]} ${filing.form} ${filing.reportDate}: ${bytes.length} bytes`);
    } catch (error) {
      failures.push({ ticker: company.tickers[0], url: filing.url, error: error.message });
      console.error(`${company.tickers[0]} ${filing.reportDate}: ${error.message}`);
    }
    await new Promise(resolve => setTimeout(resolve, 350));
  }
}
await writeFile(new URL('filings/manifest.json', root), JSON.stringify({ asOf: catalog.asOf, downloadedAt: new Date().toISOString(), description: 'Primary filing HTML documents. Referenced images, exhibits, and external assets are not downloaded.', entries, failures }, null, 2) + '\n');
console.log(`Downloaded ${entries.length} filings; ${failures.length} failures.`);
if (failures.length) process.exitCode = 1;
