import { readFile } from 'node:fs/promises';
import { activeAI, aiIndexConfig } from './ai-config.mjs';
export const indexConfig = JSON.parse(await readFile(new URL('../pinecone/index.json', import.meta.url)));
export async function requestJson(url, body, headers, { attempts = 4, method = 'POST' } = {}) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    let response;
    try { response = await fetch(url, { method, headers: { 'Content-Type': 'application/json', ...headers }, ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(120000), redirect: 'error' }); }
    catch { if (attempt === attempts - 1) throw new Error('Provider connection failed. Retry the operation.'); }
    if (response?.ok) return response.json();
    if (response?.status === 429) {
      const failure = await response.clone().json().catch(() => ({}));
      const code = failure.error?.code, type = failure.error?.type;
      if (type === 'insufficient_quota' || ['insufficient_quota','credit_balance_exhausted','billing_hard_limit_reached'].includes(code)) {
        throw new Error('Provider API quota is exhausted. Restore API credits or configure a key with available quota, then retry to resume cached work.');
      }
    }
    if (response && ![429, 500, 502, 503, 504].includes(response.status)) throw new Error(`Provider returned HTTP ${response.status}. Check server credentials, permissions, and quota.`);
    if (attempt === attempts - 1) throw new Error(`Provider temporarily unavailable${response ? ` (HTTP ${response.status})` : ''}.`);
    await new Promise(r => setTimeout(r, 1000 * 2 ** attempt));
  }
}
export const openai = (path, body) => {
  if (!process.env.OPENAI_API_KEY) throw new Error('Add OPENAI_API_KEY to the server environment to enable processing and research.');
  return requestJson(`https://api.openai.com/v1/${path}`, body, { Authorization: `Bearer ${process.env.OPENAI_API_KEY}` });
};
export function aiRequest(path,body){
  const config=activeAI(),name=config.provider==='nebius'?'NEBIUS_API_KEY':'OPENAI_API_KEY';
  if(!process.env[name])throw new Error(`Configure ${name} in server secrets to use ${config.label}.`);
  const base=config.provider==='nebius'?'https://api.tokenfactory.nebius.com/v1':'https://api.openai.com/v1';
  return requestJson(`${base}/${path}`,body,{Authorization:`Bearer ${process.env[name]}`});
}
export function validateVector(vector) {
  if (!Array.isArray(vector) || vector.length !== activeAI().dimension || !vector.every(v => Number.isFinite(v)) || !vector.some(v => v !== 0)) throw new Error('Embedding dimensions or values are invalid.');
  return vector;
}
export async function embed(texts, {purpose='document'}={}) {
  if (!texts.length) return [];
  const config=activeAI();
  const input=config.provider==='nebius'&&purpose==='query'?texts.map(t=>`Instruct: Retrieve SEC filing evidence for the financial research question.\nQuery: ${t}`):texts;
  const result = await aiRequest('embeddings', { model: config.embeddingModel, dimensions: config.dimension, encoding_format: 'float', input });
  if (result.model !== config.embeddingModel || result.data?.length !== texts.length) throw new Error('Unexpected embedding model or response size.');
  const rows = [...result.data].sort((a,b) => a.index - b.index);
  if (rows.some((r,i) => r.index !== i)) throw new Error('Unexpected embedding response order.');
  return rows.map(r => validateVector(r.embedding));
}
const knownHosts=new Map();
export async function pineconeHost() {
  if (!process.env.PINECONE_API_KEY) throw new Error('Add PINECONE_API_KEY to the server environment.');
  const config=aiIndexConfig(),name=config.name,cacheKey=JSON.stringify([name,config.dimension,config.tags['embedding-model'],config.tags['embedding-provider']]);
  if (knownHosts.has(cacheKey)) return knownHosts.get(cacheKey);
  const result = await requestJson(`https://api.pinecone.io/indexes/${encodeURIComponent(name)}`, undefined, { 'Api-Key': process.env.PINECONE_API_KEY, 'X-Pinecone-Api-Version': '2025-10' }, { method: 'GET' });
  if (result.dimension !== config.dimension || result.metric !== 'cosine' || result.vector_type !== 'dense') throw new Error(`Pinecone index ${name} must be dense, cosine, and ${config.dimension} dimensions.`);
  if (result.tags?.['embedding-model'] !== config.tags['embedding-model'] || (result.tags?.['embedding-provider']&&result.tags['embedding-provider']!==activeAI().provider)) throw new Error('Pinecone index embedding provenance differs from the selected AI provider. Use its separate index.');
  if (!result.status?.ready) throw new Error('Pinecone index is still initializing.');
  if (!/^[a-zA-Z0-9.-]+\.pinecone\.io$/.test(result.host)) throw new Error('Unexpected Pinecone data-plane host.');
  knownHosts.set(cacheKey,result.host);return result.host;
}
export async function pinecone(path, body) {
  const host = await pineconeHost();
  return requestJson(`https://${host}/${path}`, body, { 'Api-Key': process.env.PINECONE_API_KEY, 'X-Pinecone-Api-Version': '2025-10' });
}
export async function upsertVectors(namespace,vectors){
  let batch=[],bytes=0,total=0;
  const flush=async()=>{if(!batch.length)return;const result=await pinecone('vectors/upsert',{namespace,vectors:batch});if(result.upsertedCount!==batch.length)throw new Error('Pinecone did not acknowledge the full batch.');total+=batch.length;batch=[];bytes=0;};
  for(const vector of vectors){
    const size=Buffer.byteLength(JSON.stringify(vector))+1;
    if(size>1_700_000)throw new Error('A vector record exceeds the safe Pinecone request size.');
    if(bytes+size>1_700_000)await flush();batch.push(vector);bytes+=size;
  }
  await flush();return {upsertedCount:total};
}
export async function structured(system, user, name, schema, options={}) {
  const result = await aiRequest('chat/completions', { model: options.model || activeAI().chatModel, temperature: 0,
    messages: [{ role: 'system', content: system+(activeAI().provider==='nebius'?`\nReturn JSON matching this schema: ${JSON.stringify(schema)}`:'') }, { role: 'user', content: user }],
    response_format: { type: 'json_schema', json_schema: { name, strict: true, schema } } });
  const message = result.choices?.[0]?.message;
  if (message?.refusal || result.choices?.[0]?.finish_reason !== 'stop') throw new Error('The model could not complete this response. Try a narrower question.');
  const value=JSON.parse(message.content);
  validateSchema(value,schema);return value;
}

// Enforce the subset of JSON Schema used by every DeepWhiz structured response,
// even when a provider/model returns JSON without enforcing its schema fully.
export function validateSchema(value,schema){
  if(schema.type==='object'){
    if(!value||typeof value!=='object'||Array.isArray(value)||schema.required.some(k=>!Object.hasOwn(value,k))||Object.keys(value).some(k=>!Object.hasOwn(schema.properties,k)))throw new Error('AI response failed schema validation.');
    for(const [k,s] of Object.entries(schema.properties))validateSchema(value[k],s);
  }else if(schema.type==='array'){
    if(!Array.isArray(value))throw new Error('AI response failed schema validation.');for(const item of value)validateSchema(item,schema.items);
  }else if(typeof value!==schema.type||schema.type==='number'&&!Number.isFinite(value))throw new Error('AI response failed schema validation.');
  if(schema.enum&&!schema.enum.includes(value))throw new Error('AI response failed schema validation.');
}
