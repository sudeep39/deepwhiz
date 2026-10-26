// Create an isolated source package without modifying Git or publishing anything.
import { readdir, mkdir, readFile, writeFile, copyFile } from 'node:fs/promises';
import { resolve, relative, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
const root=fileURLToPath(new URL('../',import.meta.url));
const destination=join(root,'release',`DeepWhiz-${new Date().toISOString().replace(/[:.]/g,'-')}`);
const roots=['lib','scripts','docs','evaluation','pinecone'];
const files=[];
async function collect(directory){for(const entry of await readdir(join(root,directory),{withFileTypes:true})){
  const path=join(directory,entry.name);
  if(entry.isSymbolicLink())throw new Error(`Symlinks are not exported: ${path}`);
  if(entry.isDirectory()){if(!['__pycache__'].includes(entry.name))await collect(path);}
  else if(/\.(mjs|py|json|md)$/.test(entry.name))files.push(path);
}}
for(const entry of await readdir(root,{withFileTypes:true}))if(entry.isFile()&&(/\.(mjs|html|css|md)$/.test(entry.name)||['package.json','package-lock.json','requirements.txt','experiment.json','quality-policy.json','.gitignore','.env.example','.replit'].includes(entry.name)))files.push(entry.name);
for(const directory of roots)await collect(directory);
for(const name of ['filings.json','experiment-filings.json','experiment-filings.csv'])files.push(join('data',name));
await collect('data/raw');
// Keep checksum provenance, but omit the large downloaded HTML archive.
files.push(join('data','filings','manifest.json'));
const manifest=[];
for(const file of files){
  const bytes=await readFile(join(root,file)),text=bytes.toString('utf8');
  if(/\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}|\bpcsk_[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/.test(text))throw new Error(`Possible credential detected; export stopped: ${file}`);
  const target=resolve(destination,file);if(!relative(destination,target)||relative(destination,target).startsWith('..'))throw new Error('Unsafe export path');
  await mkdir(dirname(target),{recursive:true});await copyFile(join(root,file),target);
  manifest.push({path:file.replaceAll('\\','/'),sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length});
}
await writeFile(join(destination,'SOURCE_MANIFEST.json'),JSON.stringify(manifest,null,2));
console.log(`Prepared ${manifest.length} files at ${destination}`);
console.log('No .git, secrets, runtime uploads, caches, downloaded HTML or source backups were exported. No GitHub operation was performed.');
