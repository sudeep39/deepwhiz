import {chromium} from 'playwright-core';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 let imported=false,scopeCalls=0,chatQuestion='';
 const question='Compare Tesla revenue in Q1 and Q2 2026';
 const job={id:'recovered',status:'ready',createdAt:'2026-10-25',files:[],summaries:[],detail:'Ready',aiConfig:{provider:'nebius'}};
 const doc={id:'one',ticker:'TSLA',company:'Tesla',reportDate:'2026-06-30',form:'10-Q',strategies:['fixed','semantic']};
 await page.route('**/api/state',r=>r.fulfill({json:{documents:[doc],companies:[{ticker:'TSLA',name:'Tesla',periods:[{...doc,ready:true}]}],jobs:imported?[job]:[],config:{providersConfigured:true,aiProvider:'nebius',aiProviders:[]}}}));
 await page.route('**/api/scope',r=>{scopeCalls++;return r.fulfill({json:scopeCalls===1?{status:'insufficient_evidence',message:'Q1 filing is missing',recovery:{companies:['TSLA'],periods:1,requestedPeriods:['2026-Q1']}}:{status:'resolved',label:'TSLA comparison'}});});
 await page.route('**/api/sec/import',r=>{const body=r.request().postDataJSON();assert.deepEqual(body.requestedPeriods,['2026-Q1']);imported=true;return r.fulfill({json:{job,duplicates:[],warnings:[]}});});
 await page.route('**/api/chat',r=>{chatQuestion=r.request().postDataJSON().question;return r.fulfill({json:{status:'answered',answer:'Recovered comparison',sources:[],chart:{type:'none'}}});});
 await page.goto((process.env.FINWHIZ_BROWSER_BASE||'http://127.0.0.1:3101')+'/');await page.fill('#question',question);await page.click('#send-button');
 await page.getByRole('button',{name:'Fetch missing reports & continue'}).click();
 await page.getByText('Recovered comparison',{exact:true}).waitFor();assert.equal(chatQuestion,question);assert.equal(scopeCalls,2);assert.deepEqual(errors,[]);
 console.log('Passed: explicit recovery, scoped import, job readiness, original question retried.');
}finally{await browser.close();}
