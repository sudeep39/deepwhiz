import { createApplication } from './lib/web-server.mjs';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
if(existsSync(resolve(process.env.FINWHIZ_DATA_DIR||fileURLToPath(new URL('.runtime/',import.meta.url)),'jobs.sqlite3'))){
  throw new Error('This is a Python runtime. Set FINWHIZ_DATA_DIR to a restored legacy runtime before starting Node.');
}
export const server = await createApplication();
server.listen(Number(process.env.PORT || 3100), process.env.HOST || '0.0.0.0', () => {
  console.log(`DeepWhiz: http://localhost:${server.address().port}`);
});
