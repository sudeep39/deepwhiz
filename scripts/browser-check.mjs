// Offline browser integration: mocked answer/provider readiness, real upload API.
// No API calls are made to OpenAI or Pinecone. Requires an installed Edge browser.
const {chromium}=await import('playwright-core').catch(()=>import('../../finwhiz/node_modules/playwright-core/index.mjs'));
import { mkdir, mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
import { once } from 'node:events';
const directory=await mkdtemp(join(tmpdir(),'finwhiz-browser-'));
process.env.OPENAI_API_KEY='offline-test';process.env.NEBIUS_API_KEY='offline-test';process.env.PINECONE_API_KEY='offline-test';delete process.env.APP_PASSWORD;delete process.env.REPLIT_DEPLOYMENT;
const server=process.env.FINWHIZ_BROWSER_BASE?null:await (await import('../lib/web-server.mjs')).createApplication({disableWorkers:true,storage:{directory}});
if(server){server.listen(0,'127.0.0.1');await once(server,'listening');}
let browser;
try{
  browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const base=process.env.FINWHIZ_BROWSER_BASE||`http://127.0.0.1:${server.address().port}`;
  const emptyState=await (await fetch(base+'/api/state')).json();
  const recoveryPage=await browser.newPage();
  await recoveryPage.addInitScript(()=>sessionStorage.setItem('finwhiz-provider','openai'));
  const recoveredDoc={id:'fixture',ticker:'TSLA',company:'Tesla',form:'10-Q',reportDate:'2026-06-30',strategies:['fixed','semantic']};
  await recoveryPage.route('**/api/state',route=>{
    const provider=route.request().headers()['x-ai-provider']||'openai';
    const visible=provider==='nebius';
    return route.fulfill({json:{...emptyState,config:{...emptyState.config,aiProvider:provider},
      documents:visible?[recoveredDoc]:[],companies:visible?[{ticker:'TSLA',name:'Tesla',periods:[{reportDate:'2026-06-30',form:'10-Q',ready:true}]}]:[],
      jobs:[{id:'recovered',status:'ready',createdAt:'2026-10-22',aiConfig:{provider:'nebius'},files:[],summaries:[],documents:[recoveredDoc]}]}});
  });
  await recoveryPage.goto(base);
  await recoveryPage.waitForFunction(()=>document.querySelector('#ai-provider').value==='nebius'&&document.querySelector('#nav-doc-count').textContent==='1');
  await recoveryPage.selectOption('#ai-provider','openai');
  await recoveryPage.waitForFunction(()=>document.querySelector('#nav-doc-count').textContent==='0');
  assert.equal(await recoveryPage.locator('#ai-provider').inputValue(),'openai');
  await recoveryPage.close();
  // An old backend may still return catalog companies without uploaded data.
  // The frontend must hide those as well, not just trust that stale list.
  await page.route('**/api/state',route=>route.fulfill({json:{...emptyState,companies:[{ticker:'NVDA',name:'NVIDIA',periods:[{reportDate:'2026-07-26',form:'10-Q',ready:false}]}]}}));
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#context-company-name').textContent==='No corpus loaded');
  assert.equal(await page.locator('.period-row').count(),0);assert.equal(await page.locator('#company-picker').isDisabled(),true);
assert.equal(await page.locator('#send-button').isDisabled(),true);
  for(const selector of ['#company-picker','.dataset-strip','#knowledge-context','#research-composer'])assert.equal(await page.locator(selector).isVisible(),false);
  assert.equal(await page.locator('#research-empty').isVisible(),true);
  await mkdir(new URL('../artifacts/',import.meta.url),{recursive:true});
  await page.screenshot({path:new URL('../artifacts/research-desktop.png',import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
  await page.unroute('**/api/state');
  for(const provider of ['nebius','openai']){
    const response=page.waitForResponse(r=>r.url().endsWith('/api/state')&&r.request().headers()['x-ai-provider']===provider);
    await page.selectOption('#ai-provider',provider);const selected=await (await response).json();assert.equal(selected.config.aiProvider,provider);
    assert.equal(await page.locator('#research-empty').isVisible(),true);
  }
  await page.click('[data-view="documents"]');
  const doc={reportDate:'2025-03-31',url:'/api/sources/browser-fixture'};
  const html='<html><body><ix:nonNumeric name="dei:TradingSymbol">ACME</ix:nonNumeric><ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric><ix:nonNumeric name="dei:DocumentPeriodEndDate">2025-03-31</ix:nonNumeric><p>Synthetic browser test filing. No real financial values.</p></body></html>';
  await page.setInputFiles('#file-picker',{name:'2025-03-31_10-Q.html',mimeType:'text/html',buffer:Buffer.from(html)});
  await page.waitForSelector('.selected-file');assert.equal(await page.locator('.file-details input[type="date"]').inputValue(),doc.reportDate);
  await page.screenshot({path:new URL('../artifacts/documents-desktop.png',import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
  await page.click('#upload-submit');await page.waitForSelector('#toast:not([hidden])');assert.match(await page.locator('#toast').textContent(),/background/);
  const state=await (await fetch(base+'/api/state')).json();assert.equal(state.jobs.length,1);assert.equal(state.jobs[0].status,'queued');
  assert.equal(state.jobs[0].aiConfig.provider,'openai');
  // Fixture-only readiness and answer to exercise chart/citation controls.
  const ready=structuredClone(state);ready.companies=[{ticker:'ADBE',name:'Adobe',periods:[{reportDate:'2025-03-31',form:'10-Q',ready:true}]},{ticker:'ACME',name:'Example Company',periods:[{reportDate:'2025-06-30',form:'10-Q',ready:true}]}];ready.documents=ready.companies.flatMap(c=>c.periods.map(p=>({ticker:c.ticker,reportDate:p.reportDate,form:p.form,strategies:['fixed','semantic']})));
  await page.route('**/api/state',route=>route.fulfill({json:ready}));
  await page.route('**/api/scope',route=>route.fulfill({json:{status:'resolved',tickers:['ACME'],filingDates:['2025-06-30'],filings:[{ticker:'ACME',reportDate:'2025-06-30'}],requestedPeriods:[],label:'Searching ACME | Filings: 2025-06-30'}}));
  await page.route('**/api/chat',route=>route.fulfill({json:{scope:{status:'resolved',label:'Searching ACME | Filings: 2025-06-30',requestedPeriods:[]},status:'answered',answer:'Synthetic browser-test revenue increased between the two fixture periods [S1].',citation_ids:['S1'],sources:[{id:'S1',company:'NVIDIA',ticker:'NVDA',form:'10-Q',report_date:doc.reportDate,section_path:'Fixture-only evidence',excerpt:'Synthetic test revenue: 100, 125. These are test fixtures, not company financial results.',source_url:doc.url}],chart:{type:'bar',title:'Synthetic browser-test values',unit:'test units',points:[{label:'Fixture A',value:100,source_ids:['S1']},{label:'Fixture B',value:125,source_ids:['S1']}]},calculations:[{label:'Synthetic change',operation:'percent_change',values:[125,100],source_ids:['S1'],result:25,unit:'%'}],metrics:{latencyMs:20,evidence:1,candidates:2,strategy:'semantic',rerank:true}}}));
  await page.click('#refresh-jobs');await page.click('[data-view="research"]');await page.waitForFunction(()=>!document.querySelector('#send-button').disabled);
  assert.equal(await page.locator('#research-empty').isVisible(),false);for(const selector of ['#company-picker','.dataset-strip','#knowledge-context','#research-composer'])assert.equal(await page.locator(selector).isVisible(),true);
  await page.fill('#question','Show the fixture chart');await page.click('#send-button');await page.waitForSelector('.chart-card');
  assert.match(await page.locator('.message-metrics').first().textContent(),/Searching ACME/);assert.equal(await page.locator('.chart-bar').count(),2);assert.match(await page.locator('.calculation').textContent(),/25/);
  await page.locator('.evidence-chip').first().click();assert.equal(await page.locator('#source-dialog').isVisible(),true);await page.click('#close-source');
  await page.screenshot({path:new URL('../artifacts/chat-fixture-desktop.png',import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
  async function choose(ticker){
    await page.click('#company-picker');await page.click('#picker-clear');await page.locator('.picker-row').filter({hasText:ticker}).locator('input').check();await page.click('#picker-apply');
  }
  await choose('ACME');await page.waitForFunction(()=>document.querySelector('#context-company-name').textContent==='Example Company');
  await page.click('#company-picker');await page.click('#picker-all');await page.click('#picker-apply');assert.equal(await page.locator('.period-row').count(),2);assert.match(await page.locator('#context-company-name').textContent(),/2 companies/);
  await page.setViewportSize({width:390,height:844});await choose('ADBE');
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);assert.equal(overflow,false);
  await page.screenshot({path:new URL('../artifacts/research-mobile.png',import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1'),fullPage:true});
  await page.click('[data-view="documents"]');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
  assert.deepEqual(errors,[]);console.log('Browser checks passed: navigation, upload acknowledgement, status, ticker/period selection, chart, calculations, citations, and mobile layout. Paid providers were mocked.');
}finally{
  await browser?.close();if(server)await new Promise(r=>server.close(r));
  if(resolve(directory).startsWith(resolve(tmpdir(),'finwhiz-browser-')))await rm(directory,{recursive:true,force:true});
}
