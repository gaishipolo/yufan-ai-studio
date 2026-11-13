import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile, rename, mkdir, readdir, copyFile, open } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { CanvasApi } from './canvas-transport.mjs';

export const ROOT = fileURLToPath(new URL('../../',import.meta.url));
export const sha = data => createHash('sha256').update(data).digest('hex');
const now = () => new Date().toISOString();
export async function save(filename, value) {
  const temp = filename+'.'+randomUUID()+'.tmp';
  await writeFile(temp, JSON.stringify(value,null,2)+'\n');
  await rename(temp,filename);
}
export async function load(run) {return JSON.parse(await readFile(path.join(run,'run.json'),'utf8'));}
export async function withRunLock(run,action) {
  const lockfile=path.join(run,'records/worker.lock');
  const lock=await open(lockfile,'wx');
  try {return await action();}
  finally {await lock.close();const {unlink}=await import('node:fs/promises');await unlink(lockfile);}
}
export async function update(run, changes) {
  const meta = {...await load(run),...changes,updated_at:now()};
  const checksums = {};
  for (const folder of ['inputs','outputs','records','review','code']) {
    async function visit(relative) {
      for (const entry of await readdir(path.join(run,relative),{withFileTypes:true})) {
        const name=relative+'/'+entry.name;
        if (entry.isDirectory()) await visit(name);
        else if (entry.isFile() && !/\.(lock|download|tmp)$/.test(name)) checksums[name]=sha(await readFile(path.join(run,name)));
      }
    }
    await visit(folder);
  }
  meta.file_checksums=checksums;
  await save(path.join(run,'run.json'),meta);
  await writeFile(path.join(run,'00_本次调用说明.md'),[
    '# LibTV 本地调用', '',
    `- 模型：${meta.model}；${meta.resolution}；${meta.duration} 秒`,
    `- 状态：${meta.status}；task_id：${meta.task_id ?? '尚未取得'}`,
    `- 估价：${meta.estimated_credits ?? '未查询'} 积分；实际费用：${meta.credits ?? '待回传/核对账单'}`,
    `- 验收：${meta.review_status ?? 'pending'}。生成完成不代表画面、身份、商品或动作验收通过。`,
    '', '[实际请求（凭据字段脱敏）](inputs/request.json) · [提示词](inputs/prompt.txt) · [元数据](run.json)',
    '', '[原始结果](outputs/generated.mp4)（仅下载成功后存在）。',
    '', 'inputs 为本次输入；records 为估价、task_id 和状态响应；code 为执行代码快照。',
    '实际素材及字段对应关系见 inputs/input-map.json。登录态仅保留在专用浏览器配置目录，鉴权头和临时校验凭据不写入本目录。',
    'request.json 中 riskControl 临时凭据被标记脱敏，记录字段名但不能用于重新认证。',
    '未知提交状态不得重复提交；已有 task_id 仅查询。', '',
  ].join('\n'));
  return meta;
}

export function validateRequest(request) {
  if (request.taskType!=='video' || request.model!=='wanx3.0' || request.provider!=='Wan') throw new Error('This verified adapter currently supports Wan 3.0 video only');
  const p=request.params;
  if (!p || p.model!==request.model || p.modeType!=='text2video' || p.count!==1 || typeof p.prompt!=='string' || !p.prompt.trim()) throw new Error('Expected one text2video output with a nonempty prompt');
  if (!Number.isInteger(p.duration) || p.duration<2 || p.duration>30 || !['480P','720P','1080P'].includes(p.resolution)) throw new Error('Invalid duration/resolution');
  if (!['16:9','9:16','1:1','3:4','4:3','adaptive'].includes(p.ratio) || !['on','off'].includes(p.enableSound)) throw new Error('Invalid ratio/audio setting');
  if (!request.metadata?.project_id || !request.metadata?.node_id) throw new Error('Use captured project/node metadata');
  // Do not silently accept uncopied external media or billing overrides.
  const allowed=new Set(['model','modeType','count','prompt','ratio','duration','resolution','enableSound','extendPrompt','textList','imageList','imageLabelList','videoList','audioList','infiniteSwitch']);
  if (Object.keys(p).some(k=>!allowed.has(k))) throw new Error('Unsupported parameter; review it before submission');
  for (const key of ['textList','imageList','imageLabelList','videoList','audioList']) if (!Array.isArray(p[key]) || p[key].length) throw new Error('Only reference-free text2video is currently verified');
  if ('riskControl' in request || 'token' in request || 'headers' in request || 'billingMode' in request) throw new Error('Do not put authentication/billing overrides in the job manifest');
}

export async function prepare(request, label='test') {
  validateRequest(request);
  const actual=structuredClone(request);
  actual.requestId=randomUUID().replaceAll('-','');
  actual.params.infiniteSwitch=0; // Normal website unlimited queue; never switch to paid acceleration.
  delete actual.clientContext;
  const stamp=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()).replace(/[-: ]/g,'');
  const base=path.join(ROOT,'libtv/runs');
  await mkdir(base,{recursive:true});
  const run=path.join(base,`${stamp}-libtv-wan3-${label.replace(/[^\w-]/g,'_').slice(0,40)}-${randomUUID().slice(0,6)}`);
  await mkdir(run,{recursive:false});
  for (const folder of ['inputs','outputs','records','review','code']) await mkdir(path.join(run,folder));
  const release=JSON.parse(await readFile(fileURLToPath(new URL('release.json',import.meta.url)),'utf8'));
  await save(path.join(run,'run.json'),{schema_version:2,provider:'libtv-canvas',adapter_version:release.version,model:actual.model,resolution:actual.params.resolution,duration:actual.params.duration,status:'prepared',task_id:null,credits:null,review_status:'pending',created_at:now()});
  await save(path.join(run,'inputs/request.json'),actual);
  await writeFile(path.join(run,'inputs/prompt.txt'),actual.params.prompt+'\n');
  await save(path.join(run,'inputs/input-map.json'),{mode:'text2video',references:[],prompt_field:'params.prompt',authentication_saved:false});
  for (const name of ['canvas-run.mjs','canvas-transport.mjs']) await copyFile(fileURLToPath(new URL(name,import.meta.url)),path.join(run,'code',name));
  await save(path.join(run,'code/release.json'),release);
  await save(path.join(run,'review/review.json'),{status:'pending',note:'未做画面与动作验收'});
  await update(run,{});
  return run;
}

export async function estimate(api, run) {
  const request=JSON.parse(await readFile(path.join(run,'inputs/request.json'),'utf8'));
  const result=await api.request('/api/task/generation/power/calculator',request);
  await save(path.join(run,'records/estimate.json'),result);
  const value=result.data?.power;
  if (typeof value!=='number' || !Number.isFinite(value) || value<0) throw new Error('Estimate is not a valid credit amount');
  await update(run,{estimated_credits:value});
  return value;
}

export async function submit(api, run, maxCredits=0) {
  if (!Number.isFinite(maxCredits) || maxCredits<0) throw new Error('Invalid credit limit');
  const meta=await load(run);
  if (meta.status!=='prepared') throw new Error(`Refusing resubmission from ${meta.status}`);
  const request=JSON.parse(await readFile(path.join(run,'inputs/request.json'),'utf8'));
  validateRequest(request);
  request.params.infiniteSwitch=0;
  const cost=await estimate(api,run);
  if (cost>maxCredits) {await update(run,{status:'estimate_over_limit'}); throw new Error('Estimate exceeds limit; not submitted');}
  // Standard first-party verification endpoint. Token remains only in memory.
  const verification=await api.request('/api/security/client-verification-token',{});
  const data=verification.data;
  const token=typeof data==='string'?data:(data?.requestToken??data?.token);
  if (typeof token!=='string' || !token) throw new Error('Normal client verification did not return a token; no submission');
  request.riskControl={requestToken:token};
  await save(path.join(run,'inputs/request.json'),{...request,riskControl:{requestToken:'[REDACTED_EPHEMERAL_VERIFICATION]'}});
  await update(run,{status:'submitting',max_credits:maxCredits,request_redactions:['riskControl.requestToken']});
  let result;
  try {
    result=await api.request('/api/task/generation/create',request);
    await save(path.join(run,'records/submission.json'),result);
  } catch(error) {
    await update(run,{status:'submission_unknown',last_error:error.message});
    throw new Error('Submission not confirmed; reconcile platform records before any retry');
  }
  const taskId=result.data?.taskId??result.data?.task_id;
  if (!taskId) {await update(run,{status:'submission_unknown'});throw new Error('Submission returned no task ID; do not retry');}
  return await update(run,{task_id:String(taskId),status:'submitted'});
}

export async function poll(api,run) {
  const meta=await load(run);
  if (!meta.task_id) throw new Error('No task ID; cannot poll or retry');
  const result=await api.request('/api/task/generation/progress',{taskIds:[meta.task_id]});
  await save(path.join(run,'records/status.json'),result);
  await save(path.join(run,'records',`status-${Date.now()}.json`),result);
  const progress=result.data?.progresses?.find(p=>String(p.taskId)===meta.task_id);
  if (!progress) throw new Error('Task missing from response; do not resubmit');
  const status={0:'queued',1:'running',2:'success',3:'failed',4:'timeout'}[progress.status];
  if (!status) throw new Error(`Unrecognized task status ${progress.status}; do not resubmit`);
  await update(run,{last_polled_at:now(),status:status==='success'?'generated':status,
    progress_percent:progress.progressPercent,credits:progress.power??meta.credits,
    benefit_tag:progress.benefitTag??null,failed_reason:progress.failedReason??null});
  if (status==='success') await downloadResult(run,progress);
  return result;
}

export function resultVideo(progress) {
  const result=typeof progress.taskResult==='string'?JSON.parse(progress.taskResult):progress.taskResult;
  const video=result?.videos?.[0];
  const url=typeof video==='string'?video:(video?.originalPath??video?.videoUrl??video?.previewPath??video?.url);
  if (!url || typeof url!=='string') throw new Error('Successful task has no recognized video URL; inspect records/status.json');
  const parsed=new URL(url);
  if (parsed.protocol!=='https:' || parsed.username || parsed.password) throw new Error('Unexpected result URL');
  return url;
}

async function downloadResult(run,progress) {
  const filename=path.join(run,'outputs/generated.mp4');
  try {await readFile(filename);await update(run,{status:'success'});return;} catch(error) {if (error.code!=='ENOENT') throw error;}
  const url=resultVideo(progress);
  const response=await fetch(url,{headers:{'User-Agent':'Mozilla/5.0',Referer:'https://www.liblib.tv/'},signal:AbortSignal.timeout(180000)});
  if (!response.ok || !response.body) throw new Error(`Video download HTTP ${response.status}`);
  // LibTV's CDN also serves real MP4s as text/plain. Validate bytes below.
  const temporary=filename+'.download';
  await pipeline(Readable.fromWeb(response.body),createWriteStream(temporary));
  const bytes=await readFile(temporary);
  if (bytes.length<1024 || bytes.subarray(4,8).toString()!=='ftyp') throw new Error('Downloaded result is not an MP4; original output not overwritten');
  await rename(temporary,filename);
  await update(run,{status:'success',downloaded_at:now()});
}

async function main() {
  const [command, filename, limit]=process.argv.slice(2);
  if (!['prepare','estimate','submit','poll'].includes(command)) throw new Error('Usage: canvas-run.mjs prepare request.json | estimate RUN | submit RUN MAX_CREDITS | poll RUN');
  if (command==='prepare') {console.log(await prepare(JSON.parse(await readFile(filename,'utf8'))));return;}
  const run=path.resolve(filename);
  if (![path.join(ROOT,'libtv/runs'),path.join(ROOT,'api_input')].some(base=>run.startsWith(base+path.sep))) throw new Error('Run must be inside libtv/runs or legacy api_input');
  // Exclusive lock stops two local processes from submitting the same run.
  await withRunLock(run,async()=>{
    const api=await CanvasApi.fromBrowser(process.env.JS_REVERSE_BROWSER_URL);
    if (command==='submit') {
      if (limit===undefined) throw new Error('An explicit credit limit is required');
      console.log(JSON.stringify(await submit(api,run,Number(limit)),null,2));
    } else console.log(JSON.stringify(await (command==='estimate'?estimate(api,run):poll(api,run)),null,2));
  });
}

if (process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  main().catch(error=>{console.error(error.message);process.exitCode=1;});
}
