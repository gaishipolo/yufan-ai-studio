// Workbench-only submission gate; pinned adapters are imported without edits.
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {CanvasApi} from '../../tools/js-reverse-mcp/canvas-transport.mjs';
import {load,save,update,withRunLock,ROOT} from '../../tools/js-reverse-mcp/canvas-run.mjs';
import {verifyMixedList} from '../prompt-compiler.mjs';

export function isAllowed(control,batchId,now=new Date()){
  if(!control.enabled||!Number.isInteger(control.start_hour)||!Number.isInteger(control.end_hour)||control.paused?.includes(batchId))return false;
  const hour=(now.getUTCHours()+8)%24;
  return control.start_hour>control.end_hour?hour>=control.start_hour||hour<control.end_hour:hour>=control.start_hour&&hour<control.end_hour;
}
export async function submit(api,run,batchId,guard){
  const m=await load(run);
  if(m.task_id||m.status!=='uploaded')throw Error('Refusing duplicate or uncertain submission');
  const request=JSON.parse(await readFile(path.join(run,'inputs/request.json'),'utf8'));
  delete request.riskControl;
  const compiled=JSON.parse(await readFile(path.join(run,'records/prompt-compilation.json'),'utf8'));
  verifyMixedList(compiled,request.params.mixedList);
  const p=request.params;
  if(compiled.prompt!==p.prompt||p.resolution!=='720P'||p.model!=='wanx3.0'||p.modeType!=='mixed2video'||p.count!==1||p.infiniteSwitch!==0)throw Error('Unexpected workbench request');
  if(!await guard())return{state:'deferred_window'};
  const estimate=await api.request('/api/task/generation/power/calculator',request);
  await save(path.join(run,'records/estimate.json'),estimate);
  const cost=estimate.data?.power;
  if(typeof cost!=='number'||!Number.isFinite(cost)||cost<0)throw Error('No valid credit estimate; not submitted');
  await update(run,{estimated_credits:cost});
  if(cost!==0)return{state:'waiting_free',estimated_credits:cost};
  if(!await guard())return{state:'deferred_window'};
  const verification=await api.request('/api/security/client-verification-token',{});
  const d=verification.data;const token=typeof d==='string'?d:d?.requestToken??d?.token;
  if(!token)throw Error('Normal client verification failed; not submitted');
  if(!await guard())return{state:'deferred_window'};
  request.riskControl={requestToken:token};
  await save(path.join(run,'inputs/request.json'),{...request,riskControl:{requestToken:'[REDACTED_EPHEMERAL_VERIFICATION]'}});
  await update(run,{status:'submitting',request_redactions:['riskControl.requestToken']});
  // Check the local control once more after disk writes, immediately before create.
  if(!await guard()){await update(run,{status:'uploaded'});return{state:'deferred_window'};}
  let result;
  try{result=await api.request('/api/task/generation/create',request);await save(path.join(run,'records/submission.json'),result);}
  catch(e){await update(run,{status:'submission_unknown',last_error:'CreateTask result unconfirmed; reconcile before retry'});throw Error('Submission unknown; never automatically resubmit');}
  const id=result.data?.taskId??result.data?.task_id;
  if(!id){await update(run,{status:'submission_unknown'});throw Error('Submission returned no task_id; do not retry');}
  await update(run,{status:'submitted',task_id:String(id)});
  return{state:'submitted',task_id:String(id)};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const run=path.resolve(process.argv[2]??'');const batchId=process.argv[3];
  (async()=>{
    if(!run.startsWith(path.join(ROOT,'libtv/runs')+path.sep)||!batchId)throw Error('Invalid workbench run');
    const guard=async()=>isAllowed(JSON.parse(await readFile(path.join(ROOT,'libtv/state/workbench-control.json'),'utf8')),batchId);
    await withRunLock(run,async()=>{const api=await CanvasApi.fromBrowser(process.env.JS_REVERSE_BROWSER_URL);console.log(JSON.stringify(await submit(api,run,batchId,guard)));});
  })().catch(e=>{console.error(e.message);process.exitCode=1;});
}
