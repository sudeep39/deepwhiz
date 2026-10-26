// Workspace credentials remain in memory; clear the former browser-storage entry.
sessionStorage.removeItem('finwhiz-password');
const $=id=>document.getElementById(id);
const make=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
let state=null,selectedFiles=[],fileDetails=[],busy=false,uploading=false,periodSelection=new Set(),conversation=[],password='';
let toastTimer,pollTimer;
let initialLibraryProviderResolved=false;
let documentsJobPage=0,documentsFilePage=0;
function showDocumentDetails(text){$('document-details-content').textContent=text;$('document-details-dialog').showModal();}
$('close-document-details').onclick=()=>$('document-details-dialog').close();
$('documents-help').onclick=()=>showDocumentDetails('Fetch SEC reports using company names or tickers separated by commas. Choose the latest report or a number of quarters. A 10-K covers the annual year-end period.\n\nUpload HTML, PDF or TXT filings, up to 20 MB each and 32 files per batch. Review each file using Previous and Next before uploading.\n\nProcessing continues on the server after you leave this page. Review batches in Activity, and retry any failed jobs.');
function selectDocumentPanel(panel){
  $('documents-view').dataset.documentPanel=panel;
  document.querySelectorAll('[data-document-tab]').forEach(button=>{button.setAttribute('aria-selected',String(button.dataset.documentTab===panel));button.tabIndex=button.dataset.documentTab===panel?0:-1;});
}
document.querySelectorAll('[data-document-tab]').forEach((button,index,buttons)=>{
  button.onclick=()=>selectDocumentPanel(button.dataset.documentTab);
  button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();selectDocumentPanel(buttons[next].dataset.documentTab);}};
});
$('sec-result-details').onclick=()=>showDocumentDetails($('sec-result').textContent);
new MutationObserver(()=>{$('sec-result-details').hidden=!$('sec-result').textContent.trim();}).observe($('sec-result'),{childList:true,characterData:true,subtree:true});
$('jobs-prev').onclick=()=>{documentsJobPage--;renderJobs();};
$('jobs-next').onclick=()=>{documentsJobPage++;renderJobs();};
$('files-prev').onclick=()=>{documentsFilePage--;renderFiles();};
$('files-next').onclick=()=>{documentsFilePage++;renderFiles();};
let pendingRecovery=null;
let coverageTicker='',coveragePage=0,companyPage=0;
let selectedProvider=sessionStorage.getItem('finwhiz-provider')||'';
$('ai-provider').value=selectedProvider||'openai';
$('ai-provider').onchange=()=>{if(busy||uploading){$('ai-provider').value=selectedProvider||'openai';return;}selectedProvider=$('ai-provider').value;sessionStorage.setItem('finwhiz-provider',selectedProvider);periodSelection.clear();clearChat();refresh().catch(error=>toast(error.message,true));};
const freshnessNote=make('p','upload-explanation');freshnessNote.hidden=true;document.querySelector('.workflow-card').append(freshnessNote);
const date=value=>new Date(`${value}T12:00:00Z`).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'});
const number=value=>new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(value);
function toast(message,error=false){clearTimeout(toastTimer);$('toast').textContent=message;$('toast').classList.toggle('error',error);$('toast').hidden=false;toastTimer=setTimeout(()=>$('toast').hidden=true,error?9000:6500);}
async function api(path,options={}){
  const headers={...(selectedProvider?{'X-AI-Provider':selectedProvider}:{}),...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(password?{Authorization:`Bearer ${password}`}:{})};
  const response=await fetch(path,{...options,headers:{...headers,...options.headers}});
  if(response.status===401){$('password-dialog').showModal();throw new Error('Enter your workspace password to continue.');}
  const result=await response.json();if(!response.ok)throw new Error(result.error||'The request failed.');return result;
}
function switchView(next){location.hash=next;$('research-view').hidden=next!=='research';$('documents-view').hidden=next!=='documents';$('page-title').textContent=next==='research'?'Research':'Documents';document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===next));}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>switchView(button.dataset.view));
$('open-documents').onclick=$('manage-corpus').onclick=()=>switchView('documents');
$('empty-upload').onclick=()=>switchView('documents');
$('sec-form').onsubmit=async event=>{
  event.preventDefault();if(uploading)return;
  uploading=true;$('ai-provider').disabled=true;$('sec-submit').disabled=true;renderFiles();
  $('sec-result').textContent='Looking up companies and downloading SEC reports. Keep this page open until the request finishes…';
  try{
    const value=$('sec-periods').value;
    const result=await api('/api/sec/import',{method:'POST',body:JSON.stringify({companies:$('sec-companies').value,filingType:$('sec-filing-type').value,periods:value==='latest'?'latest':Number($('sec-count').value)})});
    const summary=result.companies.map(c=>`${c.ticker}: ${c.filings.length} report${c.filings.length===1?'':'s'}`).join(' · ');
    $('sec-result').textContent=`${summary}. ${result.created?'Downloaded and queued for background processing.':'Existing reports reused; no new indexing scheduled.'} ${result.warnings.join(' ')}${result.duplicates.some(d=>d.status==='failed')?' Some existing reports failed processing; retry their jobs below.':''}`;
    await refresh();
  }catch(error){$('sec-result').textContent=error.message;}
  finally{uploading=false;$('ai-provider').disabled=busy;$('sec-submit').disabled=false;renderFiles();}
};
function updateSecCount(){
  const latest=$('sec-periods').value==='latest';
  $('sec-count').disabled=latest;
  if(latest)$('sec-count').value='1';
}
$('sec-periods').onchange=updateSecCount;
updateSecCount();
window.addEventListener('hashchange',()=>switchView(location.hash==='#documents'?'documents':'research'));
$('connection-toggle').onclick=()=>$('connection-panel').hidden=!$('connection-panel').hidden;
$('change-password').onclick=()=>$('password-dialog').showModal();
$('password-form').onsubmit=async event=>{event.preventDefault();password=$('workspace-password').value;try{await refresh();$('password-dialog').close();$('password-error').textContent='';}catch(error){$('password-error').textContent=error.message;}};
let researchTickers=new Set(),draftTickers=new Set(),pickerPage=0;
const selectedTickers=()=>state?.companies.filter(c=>researchTickers.has(c.ticker)).map(c=>c.ticker)||[];
$('company-picker').onclick=()=>{draftTickers=new Set(selectedTickers());pickerPage=0;$('picker-search').value='';renderCompanyPicker();$('company-picker-dialog').showModal();};
for(const id of ['close-company-picker','picker-cancel'])$(id).onclick=()=>$('company-picker-dialog').close();
$('picker-search').oninput=()=>{pickerPage=0;renderCompanyPicker();};
$('picker-prev').onclick=()=>{pickerPage--;renderCompanyPicker();};
$('picker-next').onclick=()=>{pickerPage++;renderCompanyPicker();};
$('picker-all').onclick=()=>{draftTickers=new Set(state.companies.map(c=>c.ticker));renderCompanyPicker();};
$('picker-clear').onclick=()=>{draftTickers.clear();renderCompanyPicker();};
$('picker-apply').onclick=()=>{
  if(!draftTickers.size||draftTickers.size>8||busy)return;
  researchTickers=new Set([...draftTickers].filter(t=>state.companies.some(c=>c.ticker===t)));
  if(!researchTickers.size)return;
  coverageTicker='';coveragePage=0;periodSelection.clear();clearChat();renderScope();$('company-picker-dialog').close();
};
function renderCompanyPicker(){
  const query=$('picker-search').value.trim().toLowerCase();
  const companies=state.companies.filter(c=>`${c.ticker} ${c.name}`.toLowerCase().includes(query));
  const pages=Math.max(1,Math.ceil(companies.length/5));pickerPage=Math.max(0,Math.min(pickerPage,pages-1));
  $('picker-count').textContent=`${draftTickers.size} selected`;
  $('picker-all').disabled=state.companies.length>8;
  $('picker-all').title=state.companies.length>8?'Choose up to 8 companies per research group':'Select every loaded company';
  $('picker-apply').disabled=!draftTickers.size||draftTickers.size>8||busy;
  $('picker-chips').replaceChildren(...[...draftTickers].map(t=>{
    const chip=make('button','symbol-chip',`${t} ×`);chip.setAttribute('aria-label',`Remove ${t}`);
    chip.onclick=()=>{draftTickers.delete(t);renderCompanyPicker();};return chip;
  }));
  $('picker-list').replaceChildren(...companies.slice(pickerPage*5,pickerPage*5+5).map(c=>{
    const row=make('label','picker-row'),check=make('input');check.type='checkbox';check.checked=draftTickers.has(c.ticker);
    check.disabled=!check.checked&&draftTickers.size>=8;
    check.onchange=()=>{if(check.checked)draftTickers.add(c.ticker);else draftTickers.delete(c.ticker);renderCompanyPicker();};
    const info=make('span','picker-company');info.append(make('strong','',c.ticker),make('small','',c.name));
    row.append(check,info,make('small','',`${c.periods.length} periods`));return row;
  }));
  if(!companies.length)$('picker-list').append(make('p','','No matching companies.'));
  $('picker-page').textContent=`${pickerPage+1} / ${pages}`;
  $('picker-prev').disabled=pickerPage===0;$('picker-next').disabled=pickerPage===pages-1;
}
const scopeDocuments=()=>state?.documents.filter(d=>selectedTickers().includes(d.ticker))||[];
function selectedPeriods(){return periodSelection.size?[...periodSelection]:[];}
function readyToChat(){return state?.config.providersConfigured&&selectedTickers().length>0&&selectedTickers().every(t=>state.documents.some(d=>d.ticker===t&&d.strategies.includes($('strategy-select').value)));}
function clearChat(){conversation=[];$('messages').replaceChildren();$('welcome').hidden=false;sessionStorage.removeItem('finwhiz-conversation');}

$('coverage-company').onchange=()=>{coverageTicker=$('coverage-company').value;coveragePage=0;renderScope();};
$('coverage-prev').onclick=()=>{coveragePage--;renderScope();};
$('coverage-next').onclick=()=>{coveragePage++;renderScope();};
$('browse-context').onclick=()=>{companyPage=0;$('company-search').value='';renderCompanyBrowser();$('companies-dialog').showModal();};
$('close-companies').onclick=()=>$('companies-dialog').close();
$('company-search').oninput=()=>{companyPage=0;renderCompanyBrowser();};
$('company-page-prev').onclick=()=>{companyPage--;renderCompanyBrowser();};
$('company-page-next').onclick=()=>{companyPage++;renderCompanyBrowser();};
function renderCompanyBrowser(){
  const query=$('company-search').value.trim().toLowerCase();
  const companies=state.companies.filter(c=>selectedTickers().includes(c.ticker)&&`${c.name} ${c.ticker}`.toLowerCase().includes(query));
  const pages=Math.max(1,Math.ceil(companies.length/5));companyPage=Math.max(0,Math.min(companyPage,pages-1));
  $('context-company-list').replaceChildren(...companies.slice(companyPage*5,companyPage*5+5).map(c=>{
    const button=make('button','context-company-option');
    button.append(make('strong','',c.ticker),make('span','',c.name),make('small','',`${c.periods.length} periods available`));
    button.onclick=()=>{coverageTicker=c.ticker;coveragePage=0;renderScope();$('companies-dialog').close();};return button;
  }));
  if(!companies.length)$('context-company-list').append(make('p','','No matching companies.'));
  $('company-page-label').textContent=`${companyPage+1} / ${pages}`;
  $('company-page-prev').disabled=companyPage===0;$('company-page-next').disabled=companyPage===pages-1;
}
$('new-chat').onclick=clearChat;
$('strategy-select').onchange=()=>{clearChat();renderScope();};
$('all-periods').onclick=()=>{periodSelection.clear();renderScope();};
function renderScope(){
  if(!state)return;
  $('strategy-select').disabled=busy;$('rerank-toggle').disabled=busy;
  $('company-picker').disabled=!state.companies.length;
  const tickers=selectedTickers(),companies=state.companies.filter(c=>tickers.includes(c.ticker)),compare=tickers.length>1;
  const company=companies[0];
  $('company-picker-label').textContent=tickers.length===1?tickers[0]:`${tickers.slice(0,2).join(' + ')}${tickers.length>2?` +${tickers.length-2}`:''}`||'Choose companies';
  $('company-picker').setAttribute('aria-label',`Choose research companies. Selected: ${tickers.join(', ')||'none'}`);
  const hasCorpus=Boolean(company);
  $('research-view').classList.toggle('empty-corpus',!hasCorpus);
  $('research-empty').hidden=hasCorpus;
  for(const id of ['research-scope','chat-scroll','research-composer','knowledge-context'])$(id).hidden=!hasCorpus;
  if(!company){
    for(const id of ['selected-monogram','context-monogram']){$(id).textContent='—';$(id).className='company-monogram';}
    $('scope-periods').textContent='No filing periods';$('context-company-name').textContent='No corpus loaded';$('context-ticker').textContent='Upload documents to begin';
    $('dataset-title').textContent='Your research starts with your documents';$('dataset-description').textContent='Upload financial filings in Documents';$('dataset-status').textContent='No corpus';$('dataset-status').className='pill amber';
    $('composer-scope').textContent='No company selected';$('ready-count').textContent='0';$('period-list').replaceChildren();$('all-periods').disabled=true;$('send-button').disabled=true;
    $('research-notice').hidden=false;$('research-notice').textContent='Upload your filings in Documents. Companies and reporting periods will appear here after processing completes.';return;
  }
  $('all-periods').disabled=false;
  const display=compare?`${companies.length} companies`:company.name;
  for(const id of ['selected-monogram','context-monogram']){$(id).textContent=compare?'↔':company.ticker[0];$(id).className=`company-monogram ${compare?'compare':''}`;}
  $('context-company-name').textContent=display;$('context-ticker').textContent=compare?'All included in research':'SEC filings';
  $('context-symbols').replaceChildren(...tickers.slice(0,3).map(t=>make('span','symbol-chip',t)));
  if(tickers.length>3){const more=make('button','symbol-chip',`+${tickers.length-3}`);more.onclick=()=>$('browse-context').click();$('context-symbols').append(more);}
  $('browse-context').textContent=`Browse ${companies.length===1?'company':`${companies.length} companies`} →`;
  if(coverageTicker&&!tickers.includes(coverageTicker)){coverageTicker='';coveragePage=0;}
  const allCompanies=make('option','',compare?'All companies':company.ticker);allCompanies.value='';
  $('coverage-company').replaceChildren(allCompanies,...(compare?companies:[]).map(c=>{const option=make('option','',c.ticker);option.value=c.ticker;return option;}));
  $('coverage-company').value=coverageTicker;
  const periods=companies.flatMap(c=>c.periods.map(p=>({...p,ticker:c.ticker}))).sort((a,b)=>b.reportDate.localeCompare(a.reportDate));
  $('scope-periods').textContent=`${periods.length} fiscal periods`;
  $('dataset-title').textContent=`${display} · Company filings`;$('dataset-description').textContent=`${periods.length} fiscal periods · 10-K & 10-Q`;
  const ready=periods.filter(p=>p.ready).length;
  $('dataset-status').textContent=ready===periods.length&&ready?'Ready for research':ready?`${ready} of ${periods.length} ready`:'Not indexed';
  $('dataset-status').className=`pill ${ready===periods.length&&ready?'':'amber'}`;
  $('composer-scope').textContent=`${display} filings`;$('ready-count').textContent=ready;
  const visiblePeriods=coverageTicker?periods.filter(p=>p.ticker===coverageTicker):periods;
  const grouped=new Map();
  for(const period of visiblePeriods){
    if(!grouped.has(period.reportDate))grouped.set(period.reportDate,{...period,tickers:[],forms:new Set(),ready:false});
    const group=grouped.get(period.reportDate);if(!group.tickers.includes(period.ticker))group.tickers.push(period.ticker);group.forms.add(period.form);group.ready ||= period.ready;
  }
  const focusedPeriods=[...grouped.values()],pages=Math.max(1,Math.ceil(focusedPeriods.length/4));
  $('coverage-summary').textContent=coverageTicker?`${coverageTicker} coverage · Research still includes all ${companies.length} companies`:`${companies.length} ${compare?'companies':'company'} · ${periods.length} filings across ${focusedPeriods.length} period ends`;
  coveragePage=Math.max(0,Math.min(coveragePage,pages-1));
  $('coverage-page').textContent=`${coveragePage+1} / ${pages}`;
  $('coverage-prev').disabled=coveragePage===0;$('coverage-next').disabled=coveragePage===pages-1;
  $('period-list').replaceChildren(...focusedPeriods.slice(coveragePage*4,coveragePage*4+4).map(period=>{
    const row=make('label',`period-row ${period.form==='10-K'?'annual':''}`),check=make('input');check.type='checkbox';check.disabled=!period.ready;
    check.checked=!periodSelection.size||periodSelection.has(period.reportDate);row.classList.toggle('selected',check.checked&&period.ready);
    check.onchange=()=>{
      if(!periodSelection.size)periods.filter(p=>p.ready).forEach(p=>periodSelection.add(p.reportDate));
      if(check.checked)periodSelection.add(period.reportDate);else periodSelection.delete(period.reportDate);
      // Empty selection means all periods; display that convention explicitly.
      renderScope();
    };
    const description=make('span','period-date');description.append(make('span','',date(period.reportDate)));
    const symbols=period.tickers.slice(0,2).join(' · ')+(period.tickers.length>2?` +${period.tickers.length-2}`:'');
    description.append(make('small','coverage-row-symbols',symbols));description.title=period.tickers.join(', ');
    row.append(check,description,make('span','form-badge',[...period.forms].join('/')),make('span',`status-dot ${period.ready?'':'amber'}`));
    row.title=period.ready?'Indexed and available for research':'Downloaded locally; not yet indexed';return row;
  }));
  $('send-button').disabled=busy||!readyToChat();
  $('research-notice').hidden=readyToChat();
  $('research-notice').textContent=!state.config.providersConfigured?'Configure the selected AI provider and Pinecone to start researching.':`No ready documents are available for ${display} with this strategy.`;
}
function renderJobs(){
  $('jobs-list').replaceChildren();
  const ordered=[...state.jobs].sort((a,b)=>(b.createdAt||'').localeCompare(a.createdAt||''));
  documentsJobPage=Math.max(0,Math.min(documentsJobPage,ordered.length-1));
  $('jobs-pager').hidden=ordered.length<2;
  $('jobs-page').textContent=`Batch ${documentsJobPage+1} of ${ordered.length}`;
  $('jobs-prev').disabled=documentsJobPage===0;$('jobs-next').disabled=documentsJobPage>=ordered.length-1;
  if(!state.jobs.length){$('jobs-list').append(make('div','empty-activity','No processing batches yet. Upload your financial documents to begin.'));return;}
  for(const job of ordered.slice(documentsJobPage,documentsJobPage+1)){
    const card=make('article','job-card'),heading=make('div','job-heading');
    heading.append(make('strong','',`${job.files.length} documents · ${new Date(job.createdAt).toLocaleString()}`),make('span',`pill ${job.status==='ready'?'':job.status==='failed'?'failed':'amber'}`,job.status==='ready'?'Ready for research':job.status==='failed'?'Needs attention':job.status==='queued'?'Queued':'Processing'));
    card.append(heading,make('p','',`${job.aiConfig?.provider==='nebius'?'Nebius Token Factory':'OpenAI'} · ${job.detail||job.phase}`));
    if(job.status==='failed'){card.append(make('p','job-error',job.error));const retry=make('button','text-button','Retry processing →');retry.onclick=async()=>{try{await api(`/api/jobs/${job.id}/retry`,{method:'POST',body:'{}'});await refresh();}catch(error){toast(error.message,true);}};card.append(retry);}
    const track=make('div','progress-track'),bar=make('span');bar.style.width=`${job.percent||0}%`;track.append(bar);card.append(track);
    const note=job.status==='ready'?job.summaries.map(s=>`${s.strategy}: ${number(s.chunks)} chunks`).join(' · '):'You can close this page. Processing continues on the server.';
    const detailsButton=make('button','text-button','View batch details');detailsButton.onclick=()=>showDocumentDetails(`${job.detail||job.phase}\n${job.error||''}\n${job.files.map(f=>f.name).join('\n')}\n${note}`);
    card.append(make('small','',note),detailsButton);$('jobs-list').append(card);
  }
}
async function refresh(){
  const requestedProvider=selectedProvider;
  const updated=await api('/api/state');if(selectedProvider!==requestedProvider)return;
  if(!initialLibraryProviderResolved){
    initialLibraryProviderResolved=true;
    if(!updated.documents?.length){
      const available=(updated.jobs||[]).find(job=>job.status==='ready'&&job.documents?.length&&['openai','nebius'].includes(job.aiConfig?.provider));
      if(available&&available.aiConfig.provider!==(updated.config.aiProvider||selectedProvider)){
        selectedProvider=available.aiConfig.provider;sessionStorage.setItem('finwhiz-provider',selectedProvider);await refresh();return;
      }
    }
  }
  state=updated;
  selectedProvider=state.config.aiProvider||selectedProvider||'openai';$('ai-provider').value=selectedProvider;
  // Do not display legacy catalog-only company entries from an older server.
  // A company and period must have a corresponding completed-upload document.
  state.companies=(state.companies||[]).filter(c=>(state.documents||[]).some(d=>d.ticker===c.ticker)).map(c=>({...c,periods:c.periods.filter(p=>(state.documents||[]).some(d=>d.ticker===c.ticker&&d.reportDate===p.reportDate&&d.form===p.form))}));
  const validTickers=new Set(state.companies.map(c=>c.ticker));
  const priorSelection=[...researchTickers];
  researchTickers=new Set(priorSelection.filter(t=>validTickers.has(t)));
  if(!researchTickers.size&&state.companies.length)researchTickers.add(state.companies[0].ticker);
  if(priorSelection.length&&priorSelection.join(',')!==[...researchTickers].join(',')){periodSelection.clear();clearChat();}
  $('company-picker').disabled=!state.companies.length;
  $('nav-doc-count').textContent=state.documents.length;
  $('connection-text').textContent=state.config.providersConfigured?'Providers configured':'Setup required';$('connection-dot').classList.toggle('amber',!state.config.providersConfigured);
  $('connection-details').textContent=`${state.config.providersConfigured?'Provider credentials are configured.':'The selected AI provider and/or Pinecone credentials are missing.'} Pinecone stores vectors. Uploads and processing state use server files; hosted persistence must be configured separately.`;
  renderScope();renderJobs();
  if(pendingRecovery){
    const recovery=pendingRecovery;
    const tracked=recovery.ids.map(id=>state.jobs.find(j=>j.id===id));
    if(selectedProvider!==recovery.provider||selectedTickers().join(',')!==recovery.tickers.join(','))pendingRecovery=null;
    else if(tracked.some(j=>j?.status==='failed')){pendingRecovery=null;$('question').value=recovery.question;toast('A recovery job failed. Retry processing in Documents before resubmitting.',true);}
    else if(tracked.every(j=>j?.status==='ready')&&!busy){pendingRecovery=null;$('question').value=recovery.question;$('chat-form').requestSubmit();}
  }
  if(state.documents.length){
    const operations=await api('/api/operations').catch(()=>null);
    if(operations){freshnessNote.hidden=false;freshnessNote.textContent=operations.freshness.updates.length?'New or amended SEC filings are available. Review your corpus.':operations.freshness.reviewDue?'Weekly source review is due. Existing answers use the uploaded filing dates.':'Source review is current. Answers use the filing periods you select.';if(operations.sla.breaches.length)freshnessNote.textContent+=' A processing batch has exceeded its service target.';}
  }else freshnessNote.hidden=true;
  clearTimeout(pollTimer);pollTimer=setTimeout(()=>refresh().catch(error=>{toast(error.message,true);}),state.jobs.some(j=>['queued','processing'].includes(j.status))?4000:15000);
}
$('refresh-jobs').onclick=()=>refresh().catch(error=>toast(error.message,true));
function inferDetails(file,text=''){
  const value=name=>{const match=text.match(new RegExp(`name=["']dei:${name}["'][^>]*>([\\s\\S]*?)<\\/ix:nonnumeric>`,'i'));return match?match[1].replace(/<[^>]*>/g,'').trim():'';};
  let rawDate=value('DocumentPeriodEndDate'),reportDate=file.name.match(/\d{4}-\d{2}-\d{2}/)?.[0]||'';
  if(!reportDate&&rawDate){const d=new Date(rawDate);if(Number.isFinite(d.getTime()))reportDate=d.toISOString().slice(0,10);}
  const ticker=value('TradingSymbol');
  return {ticker,reportDate,form:value('DocumentType')||file.name.match(/10-[KQ]/i)?.[0]?.toUpperCase()||'10-Q'};
}
async function addFiles(files){
  const picked=[...files];
  for(const file of picked){
    if(!/\.(html?|pdf|txt)$/i.test(file.name)){toast(`${file.name}: choose HTML, PDF, or TXT.`,true);continue;}
    if(file.size>20*1024*1024){toast(`${file.name}: file exceeds 20 MB.`,true);continue;}
    if(selectedFiles.length>=32){toast('A batch can contain up to 32 files.',true);break;}
    if(selectedFiles.some(f=>f.name===file.name&&f.size===file.size)){continue;}
    const text=/\.html?$/i.test(file.name)?await file.slice(0,250000).text():'';
    selectedFiles.push(file);fileDetails.push(inferDetails(file,text));
  }
  renderFiles();
}
function renderFiles(){
  documentsFilePage=Math.max(0,Math.min(documentsFilePage,selectedFiles.length-1));
  $('files-pager').hidden=selectedFiles.length<2;
  $('files-page').textContent=`File ${documentsFilePage+1} of ${selectedFiles.length}`;
  $('files-prev').disabled=documentsFilePage===0;$('files-next').disabled=documentsFilePage>=selectedFiles.length-1;
  $('selected-files').replaceChildren(...selectedFiles.slice(documentsFilePage,documentsFilePage+1).map((file,offset)=>{
    const i=documentsFilePage+offset;
    const row=make('div','selected-file'),head=make('div','file-heading'),remove=make('button','remove-file','×');remove.title=`Remove ${file.name}`;remove.onclick=()=>{selectedFiles.splice(i,1);fileDetails.splice(i,1);renderFiles();};
    head.append(make('span','','▧'),make('strong','',file.name),make('small','',`${(file.size/1024/1024).toFixed(1)} MB`),remove);
    const details=make('div','file-details');
    for(const [key,label] of [['ticker','Ticker'],['form','Report'],['reportDate','Fiscal period end']]){
      const field=make('label','',label),input=make(key==='form'?'select':'input');
      if(key==='form')input.append(...['10-Q','10-K'].map(v=>{const o=make('option','',v);o.value=v;return o;}));else input.type=key==='reportDate'?'date':'text';
      input.value=fileDetails[i][key];input.setAttribute('aria-label',`${file.name}: ${label}`);if(key==='ticker'){input.maxLength=10;input.placeholder='Auto-detect';}
      input.onchange=()=>{fileDetails[i][key]=key==='ticker'?input.value.trim().toUpperCase():input.value;input.value=fileDetails[i][key];};field.append(input);details.append(field);
    }
    row.append(head,details);return row;
  }));
  const total=selectedFiles.reduce((n,f)=>n+f.size,0);
  $('upload-selection-label').textContent=selectedFiles.length?`${selectedFiles.length} files · ${(total/1024/1024).toFixed(1)} MB`:'No files selected';
  $('upload-submit').disabled=uploading||!selectedFiles.length;
}
$('file-picker').onchange=event=>{addFiles(event.target.files).catch(error=>toast(error.message,true));event.target.value='';};
for(const event of ['dragover','dragenter'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});
for(const event of ['dragleave','drop'])$('drop-zone').addEventListener(event,e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');});
$('drop-zone').addEventListener('drop',e=>addFiles(e.dataTransfer.files).catch(error=>toast(error.message,true)));
$('upload-submit').onclick=async()=>{
  if(!selectedFiles.length||uploading)return;
  const total=selectedFiles.reduce((n,f)=>n+f.size,0);if(total>100*1024*1024){toast('A batch must be smaller than 100 MB.',true);return;}
  if(fileDetails.some((d,i)=>!/\.html?$/i.test(selectedFiles[i].name)&&(!d.ticker||!d.reportDate))){toast('Confirm the ticker and fiscal period end for PDF and text files.',true);return;}
  const data=new FormData();data.append('details',JSON.stringify(fileDetails));selectedFiles.forEach((file,i)=>data.append(`file_${i}`,file));
  uploading=true;$('ai-provider').disabled=true;$('upload-submit').textContent='Uploading files…';renderFiles();
  try{const result=await api('/api/uploads',{method:'POST',body:data});selectedFiles=[];fileDetails=[];toast(result.created===false?'Existing files reused; no duplicate indexing.':'Upload accepted. New files will process in the background.');await refresh();}
  catch(error){toast(error.message,true);}finally{uploading=false;$('ai-provider').disabled=busy;$('upload-submit').textContent='Upload & process ↗';renderFiles();}
};

const scrollBottom=()=>{$('chat-scroll').scrollTop=$('chat-scroll').scrollHeight;};
function addMessage(role,text){
  const message=make('article',`message ${role==='user'?'user-message':'assistant-message'}`),head=make('div','message-header');
  head.append(make('span','message-avatar',role==='user'?'A':'D'),make('span','',role==='user'?'You':'DeepWhiz'));message.append(head);
  const content=make('div','message-content',text);message.append(content);$('messages').append(message);$('welcome').hidden=true;return {message,content};
}
function sourceButton(id,sources,label=id){const button=make('button','citation-button',label);button.onclick=()=>showSource(sources.find(s=>s.id===id));return button;}
function citationText(node,text,sources){const re=/\[(S\d+)\]/g;let cursor=0;for(const match of text.matchAll(re)){node.append(document.createTextNode(text.slice(cursor,match.index)),sourceButton(match[1],sources));cursor=match.index+match[0].length;}node.append(document.createTextNode(text.slice(cursor)));}
async function showSource(source){
  if(!source)return;
  const content=$('source-content');content.replaceChildren(make('div','eyebrow',`${source.id} · SOURCE EVIDENCE`),make('h2','source-title',`${source.company} · ${source.form}`),make('p','source-details',`Period ended ${date(source.report_date)}\n${source.section_path}`),make('div','source-excerpt',source.excerpt));
  const link=make('a','source-link','Open original source ↗');
  if(source.source_url.startsWith('/api/sources/')){
    link.href='#';link.onclick=async event=>{event.preventDefault();try{const response=await fetch(source.source_url,{headers:password?{Authorization:`Bearer ${password}`}:{}});if(!response.ok)throw new Error('Cannot download this source.');const url=URL.createObjectURL(await response.blob());const a=make('a');a.href=url;a.download=response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1]||`${source.ticker}-${source.report_date}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){toast(error.message,true);}};
  }else if(/^https:\/\/www\.sec\.gov\//.test(source.source_url)){link.href=source.source_url+(source.source_anchor?`#${encodeURIComponent(source.source_anchor)}`:'');link.target='_blank';link.rel='noopener noreferrer';}else return;
  content.append(link);$('source-dialog').showModal();
}
$('close-source').onclick=()=>$('source-dialog').close();
const svgNode=(name,attrs={})=>{const node=document.createElementNS('http://www.w3.org/2000/svg',name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);return node;};
function chartCard(chart,sources){
  const card=make('section','chart-card');card.append(make('h3','',chart.title),make('p','',chart.unit));
  const svg=svgNode('svg',{viewBox:'0 0 600 240',role:'img','aria-label':chart.title});const title=svgNode('title');title.textContent=chart.title;svg.append(title);
  const points=chart.points.slice(0,20),values=points.map(p=>p.value),min=Math.min(0,...values),max=Math.max(0,...values),range=max-min||1;
  const y=v=>190-(v-min)/range*160,x=i=>65+i*490/Math.max(1,points.length-1);
  for(let i=0;i<4;i++){const value=min+range*i/3,line=svgNode('line',{x1:50,x2:575,y1:y(value),y2:y(value),class:'chart-grid'}),label=svgNode('text',{x:43,y:y(value)+4,'text-anchor':'end',class:'chart-label'});label.textContent=number(value);svg.append(line,label);}
  if(chart.type==='line')svg.append(svgNode('path',{d:points.map((p,i)=>`${i?'L':'M'}${x(i)},${y(p.value)}`).join(' '),class:'chart-line'}));
  points.forEach((point,i)=>{
    const px=chart.type==='bar'?60+i*510/points.length:x(i),width=Math.min(45,410/points.length);
    const shape=chart.type==='bar'?svgNode('rect',{x:px,y:Math.min(y(point.value),y(0)),width,height:Math.max(1,Math.abs(y(point.value)-y(0))),rx:3,class:'chart-bar',tabindex:0}):svgNode('circle',{cx:px,cy:y(point.value),r:4,class:'chart-point',tabindex:0});
    const tooltip=svgNode('title');tooltip.textContent=`${point.label}: ${number(point.value)} ${chart.unit}`;shape.append(tooltip);svg.append(shape);
    const label=svgNode('text',{x:chart.type==='bar'?px+width/2:px,y:215,'text-anchor':'middle',class:'chart-label'});label.textContent=point.label.length>13?point.label.slice(0,12)+'…':point.label;svg.append(label);
  });card.append(svg);
  const details=make('details'),summary=make('summary','','View values and sources'),table=make('table','chart-values'),head=make('tr');for(const label of ['Period / metric','Value','Evidence'])head.append(make('th','',label));const thead=make('thead');thead.append(head);table.append(thead);const tbody=make('tbody');
  for(const p of points){const row=make('tr'),refs=make('td');p.source_ids.forEach(id=>refs.append(sourceButton(id,sources)));row.append(make('td','',p.label),make('td','',`${number(p.value)} ${chart.unit}`),refs);tbody.append(row);}table.append(tbody);details.append(summary,table);card.append(details);return card;
}
let executionPreview=null;
function hideExecutionPreview(){executionPreview?.remove();executionPreview=null;}
function executionOutput(output){
  const panel=make('details','execution-output'),data=output.data||{};
  panel.append(make('summary','',output.title));
  const content=make('div','execution-output-content');
  if(output.kind==='evidence'){
    content.append(make('p','',`${data.shown} of ${data.total} sources shown. Excerpts reflect the evidence available at this step.`));
    for(const source of data.sources||[]){
      const card=make('article','execution-source');card.append(make('strong','',`${source.id||'Source'} · ${source.ticker||''} · ${source.report_date||''}`));
      if(source.section_path)card.append(make('small','',String(source.section_path)));
      card.append(make('p','execution-excerpt',source.excerpt||'No excerpt recorded.'));
      if(source.truncated)card.append(make('small','','Excerpt shortened for this view.'));
      content.append(card);
    }
  }else if(output.kind==='draft'||output.kind==='final'){
    if(output.kind==='draft')content.append(make('p','execution-draft-note','Intermediate output: claims here may be rejected or changed by subsequent checks.'));
    content.append(make('span','execution-output-status',data.status||'recorded'),make('p','execution-excerpt',data.text||'No answer text produced.'));
    if(data.truncated)content.append(make('small','','Answer text shortened for this view.'));
    if(data.citations?.length)content.append(make('p','',`Citations: ${data.citations.join(', ')}`));
    if(data.calculations?.length)content.append(make('pre','',JSON.stringify(data.calculations,null,2)));
  }else if(output.kind==='tasks'){
    content.append(make('p','',`${data.tasks?.length||0} tasks · ${data.origin||'application'} plan`));
    for(const task of data.tasks||[]){const card=make('article','execution-source');card.append(make('strong','',`${task.id} · ${task.ticker} · ${task.period||task.report_date}`),make('small','',task.metric||''),make('p','',task.objective||''));content.append(card);}
  }else{
    const list=make('dl','execution-output-fields');
    for(const [key,value] of Object.entries(data)){if(value===undefined||value===null)continue;list.append(make('dt','',key.replaceAll('_',' ')),make('dd','',typeof value==='object'?JSON.stringify(value,null,2):String(value)));}
    content.append(list);
  }
  panel.append(content);return panel;
}
function executionFlow(path,preview=false){
  const flow=make('ol',`execution-flow ${preview?'execution-flow-preview':''}`);
  if(!preview&&path.tasks?.length){
    const plan=make('li','research-task-summary');plan.append(make('strong','','Research task checklist'));
    for(const task of path.tasks){const row=make('p','',`${task.id} · ${task.ticker} · ${task.period} · ${task.metric}: ${task.status.replaceAll('_',' ')}`);row.title=task.objective;plan.append(row);}
    plan.append(make('small','','Evidence found means relevant passages were retrieved; fact located means a matching table value was extracted. Neither is an independent accuracy certification.'));
    flow.append(plan);
  }
  const steps=preview?path.steps.slice(0,4):path.steps;
  for(const [index,step] of steps.entries()){
    const node=make('li',`execution-node execution-${step.status||'completed'}`);
    node.append(make('span','execution-number',String(index+1)));
    const body=make('div','execution-node-body');body.append(make('strong','',step.title));
    if(step.control)body.append(make('span',`execution-control ${step.control}`,step.control==='agent'?'Agent decision':step.control==='agent_tool'?'Agent-selected tool':'Application step'));
    if(!preview){
      for(const output of step.outputs||[])body.append(executionOutput(output));
      for(const detail of step.details||[])body.append(make('p','',detail));
      if(step.filings?.length){
        const filings=make('div','execution-filings');
        for(const filing of step.filings)filings.append(make('span','',`${filing.ticker} · ${filing.reportDate}`));
        body.append(filings);
      }
      if(step.lookups?.length){
        body.append(make('p','execution-lookup-label','Filing lookups (can run concurrently)'));
        const branches=make('ul','execution-branches');
        for(const lookup of step.lookups)branches.append(make('li','',`${lookup.ticker} · ${lookup.reportDate} · ${lookup.outcome==='ok'?'completed':'unavailable'} · ${(lookup.durationMs/1000).toFixed(2)}s`));
        body.append(branches);
      }
      if(step.operations?.length){
        const calls=make('details','execution-calls'),summary=make('summary','',`Inspect ${step.operations.length} recorded operations`);calls.append(summary);
        for(const operation of step.operations){
          const card=make('div','execution-call');
          const label=operation.stage==='vector_db_call'?'Vector database query':operation.stage==='query_embedding_cache_hit'?'Embedding reused from cache':operation.stage.replaceAll('_',' ');
          card.append(make('strong','',label));
          const rows=[];
          if(operation.model)rows.push(['Model',`${operation.provider||''} / ${operation.model}`]);
          if(operation.schema)rows.push(['Response type',operation.schema]);
          if(operation.attempt)rows.push(['Attempt',operation.attempt]);
          if(operation.database)rows.push(['Database',`${operation.database} / ${operation.index}`],['Namespace',operation.namespace],['Top K',operation.topK],['Returned matches',operation.matches],['Filters',JSON.stringify(operation.filters)]);
          if(operation.dimensions)rows.push(['Embedding dimensions',operation.dimensions]);
          if(operation.inputs)rows.push(['Inputs',operation.inputs]);
          if(operation.total_tokens!==undefined)rows.push(['Tokens',`${operation.input_tokens??0} input / ${operation.output_tokens??0} output / ${operation.total_tokens} total`]);
          rows.push(['Outcome',operation.outcome||'recorded'],['Duration',`${(operation.durationMs/1000).toFixed(2)}s`]);
          const list=make('dl');for(const [key,value] of rows){list.append(make('dt','',key),make('dd','',String(value??'Not recorded')));}card.append(list);calls.append(card);
        }
        body.append(calls);
      }
      body.append(make('small','',`${step.status.replaceAll('_',' ')} · ${(step.durationMs/1000).toFixed(2)}s`));
    }
    node.append(body);flow.append(node);
  }
  if(!preview&&path.finalOutput){
    const node=make('li','execution-node execution-delivered'),body=make('div','execution-node-body');
    node.append(make('span','execution-number','✓'));body.append(make('strong','','Delivered response'));
    const output=executionOutput({title:'Final response shown to the user',kind:'final',data:path.finalOutput});output.open=true;body.append(output);node.append(body);flow.append(node);
  }
  return flow;
}
function executionButton(path){
  const button=make('button','execution-trigger','Execution path');button.type='button';
  const icon=svgNode('svg',{viewBox:'0 0 20 20',width:16,height:16,'aria-hidden':'true'});icon.append(svgNode('path',{d:'M5 3v14M5 7h7V3M5 13h10',fill:'none',stroke:'currentColor','stroke-width':1.5}));button.prepend(icon);
  button.setAttribute('aria-haspopup','dialog');button.setAttribute('aria-controls','execution-dialog');
  const previewId=`execution-preview-${crypto.randomUUID()}`;
  const showPreview=()=>{
    hideExecutionPreview();executionPreview=make('div','execution-preview');executionPreview.id=previewId;executionPreview.setAttribute('role','tooltip');
    button.setAttribute('aria-describedby',previewId);
    executionPreview.append(make('strong','',`${path.steps.length} recorded steps`),executionFlow(path,true),make('small','',`${path.steps.length>4?`+${path.steps.length-4} more steps · `:''}Click or press Enter to explore`));
    document.body.append(executionPreview);
    const bounds=button.getBoundingClientRect(),height=executionPreview.offsetHeight;
    executionPreview.style.left=`${Math.max(12,Math.min(bounds.left,innerWidth-executionPreview.offsetWidth-12))}px`;
    executionPreview.style.top=`${bounds.top>height+16?bounds.top-height-10:Math.min(bounds.bottom+10,innerHeight-height-12)}px`;
  };
  button.onmouseenter=showPreview;button.onfocus=showPreview;button.onmouseleave=hideExecutionPreview;button.onblur=hideExecutionPreview;
  button.onclick=()=>{hideExecutionPreview();$('execution-flow').replaceChildren(executionFlow(path));$('execution-dialog').showModal();};
  return button;
}
$('close-execution').onclick=()=>$('execution-dialog').close();
$('expand-execution-outputs').onclick=()=>document.querySelectorAll('#execution-flow .execution-output').forEach(panel=>panel.open=true);
$('collapse-execution-outputs').onclick=()=>document.querySelectorAll('#execution-flow .execution-output').forEach(panel=>panel.open=false);
window.addEventListener('resize',hideExecutionPreview);
window.addEventListener('scroll',hideExecutionPreview,true);
document.addEventListener('keydown',event=>{if(event.key==='Escape')hideExecutionPreview();});
function renderAnswer(result){
  const insufficient=result.status==='insufficient_evidence';
  const displayText=insufficient?(result.answer?.startsWith('Insufficient data')?result.answer:'Insufficient data. The selected filings do not provide enough verified information to answer this question. Add the relevant filings or refine your question.'):result.answer;
  const {message,content}=addMessage('assistant','');citationText(content,displayText,result.sources);
  if(!insufficient&&result.researchCompleteness==='partial')message.append(make('p','research-partial','Insufficient data for a complete answer. The available findings are shown above.'));
  const gaps=result.evidenceGaps||result.scope?.evidenceGaps||[];
  if(gaps.length){
    const panel=make('section','evidence-gaps');panel.setAttribute('aria-label','Evidence needed to answer your question');panel.append(make('h4','',"What's needed"));
    for(const gap of gaps){const row=make('article','evidence-gap');row.append(make('strong','',gap.part),make('p','',gap.needed));panel.append(row);}
    const retryOnly=gaps.every(gap=>gap.action==='retry');
    const action=make('button','button small secondary',retryOnly?'Retry verification':'Review or upload supporting documents');
    action.onclick=()=>{if(retryOnly){$('question').value=result.researchQuestion||'';if(!busy&&$('question').value)$('chat-form').requestSubmit();}else switchView('documents');};
    panel.append(action);message.append(panel);
  }
  if(result.scope?.recovery){
    const recovery=make('button','button secondary','Fetch missing reports & continue');
    recovery.onclick=async()=>{
      if(pendingRecovery){toast('A recovery is already in progress.',true);return;}
      recovery.disabled=true;
      try{
        const imported=await api('/api/sec/import',{method:'POST',body:JSON.stringify(result.scope.recovery)});
        if(imported.warnings?.some(w=>w.includes('not found'))){message.append(make('p','research-partial',imported.warnings.join(' ')));return;}
        pendingRecovery={ids:[...new Set([imported.job.id,...imported.duplicates.map(d=>d.jobId)])],provider:selectedProvider,tickers:selectedTickers(),question:result.researchQuestion||''};
        message.append(make('p','','Reports queued. This question will retry when processing completes.'));await refresh();
      }catch(error){toast(error.message,true);}finally{recovery.disabled=false;}
    };
    message.append(recovery,make('p','message-metrics','Searches up to 12 recent reporting periods per company. Historical gaps may require a manual upload.'));
  }
  if(result.status==='needs_clarification')message.prepend(make('div','eyebrow','A LITTLE MORE CONTEXT'));
  if(result.chart?.type!=='none'&&result.chart?.points?.length)message.append(chartCard(result.chart,result.sources));
  if(result.calculations?.length){const grid=make('div','calculations');for(const c of result.calculations){const card=make('div','calculation');card.append(make('h4','',c.label),make('strong','',`${number(c.result)} ${c.unit}`),make('small','',`${c.operation.replaceAll('_',' ')} · Inputs: ${c.values.map(number).join(', ')}`));c.source_ids.forEach(id=>card.append(sourceButton(id,result.sources)));grid.append(card);}message.append(grid);}
  const links=make('div','evidence-links');for(const source of result.sources){const chip=make('button','evidence-chip',`${source.id} · ${source.ticker} · ${source.form} · ${source.report_date}`);chip.onclick=()=>showSource(source);links.append(chip);}message.append(links);
  if(!insufficient&&result.scope)message.append(make('div','message-metrics',result.scope.label+(result.scope.requestedPeriods?.length?' | Requested: '+result.scope.requestedPeriods.join(', '):'')));
  if(!insufficient&&result.metrics)message.append(make('div','message-metrics',`${result.metrics.strategy||$('strategy-select').value} · Hybrid${result.metrics.rerank?(result.metrics.rerankFallback?' ? Reranking limited':' + rerank'):''} · ${result.metrics.evidence||0} sources · ${(result.metrics.latencyMs/1000).toFixed(1)}s`));
  const toolbar=make('div','message-toolbar'),copy=make('button','','Copy answer');copy.onclick=()=>navigator.clipboard.writeText(displayText).then(()=>toast('Answer copied.'),()=>toast('Copy is unavailable in this browser.',true));toolbar.append(copy);
  if(result.financialEvidence?.length){
    const details=make('details','financial-evidence'),summary=make('summary','','Inspect financial evidence and accounting basis');details.append(summary);
    const wrap=make('div','financial-evidence-wrap'),table=make('table','chart-values');
    const header=make('tr');for(const title of ['Company / metric','Period / basis','Value / units','Source'])header.append(make('th','',title));const head=make('thead');head.append(header);table.append(head);const body=make('tbody');
    for(const fact of result.financialEvidence){const row=make('tr'),ref=make('td');ref.append(sourceButton(fact.source_id,result.sources));row.append(make('td','',`${fact.ticker} · ${fact.metric.replaceAll('_',' ')}`),make('td','',`${fact.period} · ${fact.basis} · ${fact.segment}`),make('td','',`${number(fact.value)} ${fact.unit}`),ref);body.append(row);}table.append(body);wrap.append(table);details.append(wrap,make('p','message-metrics','Extracted table values as disclosed. Unknown units and differing accounting bases require review. Derived values appear separately in calculation cards.'));message.append(details);
  }
  if(result.executionPath?.steps?.length)toolbar.append(executionButton({...result.executionPath,tasks:result.researchPlan?.tasks}));
  message.append(toolbar);scrollBottom();
}
document.querySelectorAll('[data-question]').forEach(button=>button.onclick=()=>{$('question').value=button.dataset.question;$('question').focus();});
$('question').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('chat-form').requestSubmit();}};
$('chat-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;if(!readyToChat()){toast('Process your selected company filings in Documents first.',true);return;}
  const question=$('question').value.trim();if(!question)return;
  const scope=selectedTickers(),strategy=$('strategy-select').value,periods=selectedPeriods();
  addMessage('user',question);$('question').value='';busy=true;$('ai-provider').disabled=true;renderScope();
  const thinking=make('div','thinking');thinking.append(make('span','thinking-dot'),make('span','','Searching filings and checking the evidence…'));$('messages').append(thinking);scrollBottom();
  try{
    const payload={question,tickers:scope,periods,strategy,rerank:$('rerank-toggle').checked,history:conversation.slice(-4)};
    const resolved=await api('/api/scope',{method:'POST',body:JSON.stringify(payload)});
    let result;
    if(resolved.status!=='resolved'){
      result={status:resolved.status,answer:resolved.message,scope:resolved,evidenceGaps:resolved.evidenceGaps||[],sources:[],calculations:[],chart:{type:'none',points:[]}};
    }else{
      thinking.lastChild.textContent=resolved.label+' | Checking the evidence\u2026';
      result=await api('/api/chat',{method:'POST',body:JSON.stringify(payload)});
    }
    result.researchQuestion=question;thinking.remove();renderAnswer(result);
    conversation.push({role:'user',content:question,scope:result.scope||resolved},{role:'assistant',content:result.answer});
  }
  catch(error){thinking.remove();addMessage('assistant',error.message);toast(error.message,true);}
  finally{busy=false;$('ai-provider').disabled=uploading;renderScope();scrollBottom();}
};
switchView(location.hash==='#documents'?'documents':'research');
refresh().catch(error=>{if(!$('password-dialog').open)toast(error.message,true);$('connection-text').textContent='Connection needed';});

// Keep advanced retrieval controls available without occupying the research canvas.
function setContextOpen(open){
  $('research-view').classList.toggle('context-open',open);
  $('toggle-context').setAttribute('aria-expanded',String(open));
  if(open)$('close-context').focus();else $('toggle-context').focus();
}
$('toggle-context').onclick=()=>setContextOpen(!$('research-view').classList.contains('context-open'));
$('close-context').onclick=()=>setContextOpen(false);
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&$('research-view').classList.contains('context-open'))setContextOpen(false);});
$('research-settings').onclick=()=>$('research-settings-dialog').showModal();
$('close-research-settings').onclick=()=>$('research-settings-dialog').close();
function updateMethodSummary(){$('method-summary').textContent=`${$('strategy-select').selectedOptions[0].textContent}${$('rerank-toggle').checked?' / reranked':''}`;}
$('strategy-select').addEventListener('change',updateMethodSummary);
$('rerank-toggle').addEventListener('change',updateMethodSummary);

// Keep keyboard navigation inside the scope drawer until it is dismissed.
document.addEventListener('keydown',event=>{
  if(event.key!=='Tab'||!$('research-view').classList.contains('context-open')||!matchMedia('(max-width:1100px)').matches||document.querySelector('dialog[open]'))return;
  const controls=[...$('knowledge-context').querySelectorAll('button:not(:disabled),select,input:not(:disabled),[tabindex="0"]')].filter(el=>el.getClientRects().length);
  const first=controls[0],last=controls.at(-1);
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
});
