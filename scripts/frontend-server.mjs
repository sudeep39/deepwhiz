import http from 'node:http';
import https from 'node:https';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/app.mjs', ['app.mjs', 'text/javascript; charset=utf-8']],
  ['/style.css', ['style.css', 'text/css; charset=utf-8']],
  ['/data/experiment-filings.json', ['data/experiment-filings.json', 'application/json']],
  ['/data/experiment-filings.csv', ['data/experiment-filings.csv', 'text/csv']],
]);

export function createFrontendServer(backend = 'http://127.0.0.1:8000', { remoteAssets = false } = {}) {
  const target = new URL(backend);
  if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('Use a backend origin such as https://your-app.replit.app, without credentials or a path.');
  }
  return http.createServer(async (request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (pathname.startsWith('/api/') || (remoteAssets && assets.has(pathname) && ['GET', 'HEAD'].includes(request.method))) {
      const headers = { ...request.headers, host: target.host };
      if (request.headers.origin) {
        let origin;
        try { origin = new URL(request.headers.origin); } catch { /* reject below */ }
        if (!origin || origin.protocol !== 'http:' || origin.host !== request.headers.host) {
          response.writeHead(403, { 'Content-Type': 'application/json' });
          response.end(JSON.stringify({ error: 'Origin not allowed.' }));
          return;
        }
        // Validate the browser's origin locally before translating it upstream.
        headers.origin = target.origin;
      }
      // Rebuild forwarding metadata from this connection, never from caller input.
      delete headers['x-forwarded-host'];
      headers['x-forwarded-for'] = request.socket.remoteAddress;
      headers['x-forwarded-proto'] = target.protocol.slice(0, -1);
      const transport = target.protocol === 'https:' ? https : http;
      const upstream = transport.request({
        hostname: target.hostname, port: target.port || (target.protocol === 'https:' ? 443 : 80),
        path: request.url, method: request.method, headers,
      }, incoming => {
        response.writeHead(incoming.statusCode, incoming.headers);
        incoming.on('error', () => response.destroy());
        incoming.pipe(response);
      });
      upstream.setTimeout(120_000, () => upstream.destroy(new Error('Backend timed out')));
      upstream.on('error', () => {
        if (response.headersSent) return response.destroy();
        response.writeHead(502, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ error: `Cannot reach the Python backend at ${target.origin}. Check that it is running.` }));
      });
      request.on('aborted', () => upstream.destroy());
      response.on('close', () => { if (!response.writableFinished) upstream.destroy(); });
      request.pipe(upstream);
      return;
    }
    const asset = assets.get(pathname);
    if (!asset || !['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(404);
      response.end('Not found');
      return;
    }
    try {
      const contents = await readFile(resolve(root, asset[0]));
      response.writeHead(200, {
        'Content-Type': asset[1], 'Content-Length': contents.length,
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
      });
      response.end(request.method === 'HEAD' ? undefined : contents);
    } catch {
      response.writeHead(500);
      response.end('Unable to read the frontend asset.');
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.loadEnvFile(resolve(root, '.env')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const args = process.argv.slice(2);
  const port = Number(args.find(arg => arg.startsWith('--port='))?.slice(7) || 5173);
  const backend = args.find(arg => arg.startsWith('--backend='))?.slice(10) || process.env.FINWHIZ_BACKEND_URL || 'http://127.0.0.1:8000';
  const remoteAssets = args.includes('--remote-assets') || (!args.includes('--local-assets') && Boolean(process.env.FINWHIZ_BACKEND_URL));
  const server = createFrontendServer(backend, { remoteAssets });
  server.on('error', error => { console.error(`Frontend startup failed: ${error.message}`); process.exitCode = 1; });
  server.listen(port, '127.0.0.1', () => {
    console.log(`Frontend: http://127.0.0.1:${port}`);
    console.log(`API proxy: ${backend}`);
  });
}
