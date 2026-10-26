import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createFrontendServer } from './frontend-server.mjs';

test('remote HTTPS origins are supported and malformed targets rejected', () => {
  const server = createFrontendServer('https://example.replit.app');
  server.close();
  for (const target of ['ftp://example.com', 'https://example.com/api', 'https://user:secret@example.com']) {
    assert.throws(() => createFrontendServer(target));
  }
});

test('shared mode serves matching hosted UI without exposing project files', async () => {
  const backend = http.createServer((req, res) => res.end(req.url === '/api/origin' ? req.headers.origin : 'hosted ' + req.url));
  backend.listen(0, '127.0.0.1');
  await once(backend, 'listening');
  const frontend = createFrontendServer(`http://127.0.0.1:${backend.address().port}`, { remoteAssets: true });
  frontend.listen(0, '127.0.0.1');
  await once(frontend, 'listening');
  const base = `http://127.0.0.1:${frontend.address().port}`;
  try {
    assert.equal(await (await fetch(base)).text(), 'hosted /');
    assert.equal(await (await fetch(base + '/app.mjs')).text(), 'hosted /app.mjs');
    assert.equal((await fetch(base + '/.env')).status, 404);
    assert.equal(await (await fetch(base + '/api/origin', { method: 'POST', headers: { Origin: base } })).text(), `http://127.0.0.1:${backend.address().port}`);
    assert.equal((await fetch(base + '/api/origin', { method: 'POST', headers: { Origin: 'https://unrelated.example' } })).status, 403);
  } finally {
    frontend.closeAllConnections(); backend.closeAllConnections();
    await Promise.all([new Promise(resolve => frontend.close(resolve)), new Promise(resolve => backend.close(resolve))]);
  }
});

test('frontend serves allowlisted assets and proxies API bodies and origin headers', async () => {
  const backend = http.createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    res.writeHead(201, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ body, path: req.url, forwarded: req.headers['x-forwarded-host'], origin: req.headers.origin }));
  });
  backend.listen(0, '127.0.0.1');
  await once(backend, 'listening');
  const frontend = createFrontendServer(`http://127.0.0.1:${backend.address().port}`);
  frontend.listen(0, '127.0.0.1');
  await once(frontend, 'listening');
  const base = `http://127.0.0.1:${frontend.address().port}`;
  try {
    assert.match(await (await fetch(base)).text(), /<html/i);
    assert.equal((await fetch(base + '/.env')).status, 404);
    const response = await fetch(base + '/api/chat?test=1', { method: 'POST', headers: { Origin: base }, body: 'example' });
    assert.equal(response.status, 201);
    assert.deepEqual(await response.json(), { body: 'example', path: '/api/chat?test=1', origin: `http://127.0.0.1:${backend.address().port}` });
    await new Promise(resolve => backend.close(resolve));
    assert.equal((await fetch(base + '/api/health')).status, 502);
  } finally {
    frontend.closeAllConnections();
    backend.closeAllConnections();
    await new Promise(resolve => frontend.close(resolve));
    if (backend.listening) await new Promise(resolve => backend.close(resolve));
  }
});

test('proxy validates origins and rebuilds forwarding headers',async t=>{
  let calls=0;
  const backend=http.createServer((req,res)=>{calls++;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(req.headers));});
  backend.listen(0,'127.0.0.1');await once(backend,'listening');
  const backendBase=`http://127.0.0.1:${backend.address().port}`;
  const frontend=createFrontendServer(backendBase);frontend.listen(0,'127.0.0.1');await once(frontend,'listening');
  t.after(async()=>{for(const server of [frontend,backend]){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}});
  const base=`http://127.0.0.1:${frontend.address().port}`;
  const denied=await fetch(base+'/api/scope',{method:'POST',headers:{Origin:'https://attacker.example','X-Forwarded-Host':'attacker.example'}});
  assert.equal(denied.status,403);assert.equal(calls,0);
  const allowed=await fetch(base+'/api/scope',{method:'POST',headers:{Origin:base,'X-Forwarded-For':'attacker','X-Forwarded-Proto':'https','X-Forwarded-Host':'attacker.example'}});
  const forwarded=await allowed.json();assert.equal(allowed.status,200);assert.equal(forwarded.origin,backendBase);
  assert.equal(forwarded['x-forwarded-for'],'127.0.0.1');assert.equal(forwarded['x-forwarded-proto'],'http');assert.equal(forwarded['x-forwarded-host'],undefined);
});
