const {chromium}=await import('playwright-core').catch(()=>import('../../finwhiz/node_modules/playwright-core/index.mjs'));
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'msedge',headless:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const jobs=Array.from({length:24},(_,i)=>({id:String(i),createdAt:`2026-10-${String(i+1).padStart(2,'0')}T10:00:00Z`,status:i===23?'failed':'ready',files:[{name:`report-${i}.html`}],summaries:[{strategy:'semantic',chunks:100}],percent:100,detail:'Processing batch detail '.repeat(20),error:'Failure detail '.repeat(50),aiConfig:{provider:'nebius'}}));
 await page.route('**/api/state',route=>route.fulfill({json:{companies:[],documents:[],jobs,config:{providersConfigured:true,aiProvider:'nebius',aiProviders:[]}}}));
 await page.goto(`${process.env.APP_URL||'http://127.0.0.1:3101'}/#documents`);await page.locator('#jobs-list .job-card').waitFor();
 assert.equal(await page.locator('#sec-periods').inputValue(),'latest');
 assert.equal(await page.locator('#sec-count').inputValue(),'1');
 assert.equal(await page.locator('#sec-count').isDisabled(),true);
 await page.selectOption('#sec-periods','quarters');assert.equal(await page.locator('#sec-count').isEnabled(),true);
 await page.fill('#sec-count','4');await page.selectOption('#sec-periods','latest');
 assert.equal(await page.locator('#sec-count').inputValue(),'1');
 let request;
 await page.route('**/api/sec/import',async route=>{request=route.request().postDataJSON();await route.fulfill({json:{companies:[{ticker:'TSLA',filings:[{form:'10-K'}]}],created:true,warnings:[],duplicates:[]}});});
 await page.fill('#sec-companies','Tesla');await page.selectOption('#sec-filing-type','10-K');await page.click('#sec-submit');
 await page.waitForFunction(()=>document.querySelector('#sec-result').textContent.includes('TSLA: 1 report'));
 assert.deepEqual(request,{companies:'Tesla',filingType:'10-K',periods:'latest'});
 await page.mouse.move(0,0);await page.waitForTimeout(200);
 await page.locator('#sec-result').evaluate(el=>{el.textContent='Report retrieval status '.repeat(100);});
 await page.locator('#file-picker').setInputFiles([{name:'first.html',mimeType:'text/html',buffer:Buffer.from('<html>Ticker: TSLA</html>')},{name:'second.html',mimeType:'text/html',buffer:Buffer.from('<html>Ticker: AAPL</html>')}]);
 for(const [width,height] of [[1440,1100],[1440,900],[1366,768],[1280,720],[1024,768],[390,844]]){
   await page.setViewportSize({width,height});await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   const dimensions=await page.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,cards:[...document.querySelectorAll('.documents-workspace>section')].map(el=>({display:getComputedStyle(el).display,top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight}))}));
   console.log(JSON.stringify(dimensions));
   assert.ok(dimensions.scrollWidth<=width,`Horizontal overflow at ${width}`);assert.ok(dimensions.scrollHeight<=height,`Vertical overflow at ${width}x${height}`);
   if(width>=1100){const buttons=await page.evaluate(()=>['sec-submit','upload-submit'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {top:r.top,height:r.height,width:r.width};}));assert.equal(buttons[0].top,buttons[1].top,'Import buttons must share the same top edge');assert.equal(buttons[0].height,buttons[1].height);assert.ok(Math.abs(buttons[0].width-buttons[1].width)<.02,'Button widths must match within grid subpixel rounding');assert.ok(await page.evaluate(()=>{const fields=[...document.querySelectorAll('.sec-fields,.upload-fields')];return document.getElementById('sec-submit').getBoundingClientRect().top-Math.max(...fields.map(el=>el.getBoundingClientRect().bottom))<=9;}),'Actions must sit directly below the content');assert.ok(await page.evaluate(()=>document.querySelector('.sec-fields').getBoundingClientRect().bottom<=document.getElementById('sec-submit').getBoundingClientRect().top&&document.querySelector('.upload-fields').getBoundingClientRect().bottom<=document.getElementById('upload-submit').getBoundingClientRect().top),'Fields must not overlap actions');assert.ok(dimensions.cards.every(card=>card.top===dimensions.cards[0].top));assert.ok(dimensions.cards.every(card=>card.scrollHeight<=card.clientHeight+1),'Card content overflows');}
 }
 await page.click('#documents-tab-upload');assert.equal(await page.locator('.manual-upload-card').isVisible(),true);
 assert.equal(await page.locator('#selected-files .selected-file').count(),1);await page.click('#files-next');assert.match(await page.locator('#selected-files').textContent(),/second.html/);
 await page.click('#documents-tab-activity');assert.equal(await page.locator('#jobs-list .job-card').count(),1);await page.click('#jobs-next');assert.match(await page.locator('#jobs-page').textContent(),/2 of 24/);
 await page.locator('#jobs-list').getByRole('button',{name:'View batch details'}).click();assert.equal(await page.locator('#document-details-dialog').isVisible(),true);await page.keyboard.press('Escape');
 await page.click('#documents-tab-sec');await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#documents-tab-upload').getAttribute('aria-selected'),'true');
 await page.setViewportSize({width:1366,height:768});await page.screenshot({path:'.cache/documents-desktop.png'});
 assert.deepEqual(errors,[]);console.log('Passed: horizontal desktop layout, no viewport scroll, responsive tabs, file/batch pagination and full details.');
}finally{await browser.close();}
