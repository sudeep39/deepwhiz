// No-call preview by default. --apply explicitly enables paid model/vector trials.
import {spawn} from 'node:child_process';
import {existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {join, dirname} from 'node:path';
const root=fileURLToPath(new URL('../',import.meta.url));
const executable=process.platform==='win32'?'Scripts/python.exe':'bin/python';
const local=join(root,'.venv',executable),shared=join(dirname(root),'finwhiz','.venv',executable);
const python=process.env.FINWHIZ_PYTHON||(existsSync(local)?local:existsSync(shared)?shared:'python');
const args=process.argv.slice(2),review=args.indexOf('--review-run');
if(review>=0&&!args[review+1])throw new Error('Supply an evaluation directory after --review-run.');
const command=review>=0?['agent-score-reviews','--run',args[review+1]]:
  ['agent-evaluate','--queries','evaluation/agentic-suite.json','--provider','nebius',...args];
const child=spawn(python,['-B','run.py',...command],{cwd:root,stdio:'inherit',windowsHide:true});
child.on('error',error=>{console.error(error.message);process.exitCode=1;});
child.on('exit',code=>{process.exitCode=code??1;});
