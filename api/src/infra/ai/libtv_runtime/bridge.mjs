// New workbench bridge. Legacy pinned files are verified and imported without edits.
import {readFile, copyFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {randomUUID, createHash} from 'node:crypto';

export function freeQuote(value) {
  return typeof value === 'number' && Number.isFinite(value) && value === 0;
}

export function canSubmit(meta) {
  return !meta.task_id && ['queued', 'uploading', 'uploaded', 'waiting_free', 'waiting_executor', 'needs_login', 'service_error'].includes(meta.status);
}

let stage = 'verify';

export async function main() {
  stage = 'verify';
  const [command, run, legacy] = process.argv.slice(2);
  if (!['upload', 'submit', 'poll', 'download'].includes(command) || !path.isAbsolute(run) || !path.isAbsolute(legacy)) throw Error('Invalid bridge arguments');
  execFileSync(process.execPath, [path.join(legacy, 'scripts/libtv_video.mjs'), 'check'], {encoding:'utf8', timeout:30000, stdio:'pipe'});
  const moduleAt = value => import(pathToFileURL(path.join(legacy, value)).href);
  const {CanvasApi} = await moduleAt('tools/js-reverse-mcp/canvas-transport.mjs');
  const {load, save, update, withRunLock, poll} = await moduleAt('tools/js-reverse-mcp/canvas-run.mjs');
  const {MediaUploader, accountUploadUuid} = await moduleAt('libtv/adapters/canvas-media.mjs');
  const {submit} = await moduleAt('libtv/workbench/submit.mjs');
  await withRunLock(run, async () => {
    let meta = await load(run);
    stage = 'login';
    const api = await CanvasApi.fromBrowser(process.env.JS_REVERSE_BROWSER_URL);
    if (command === 'download') {
      if (!meta.task_id) throw Error('No task ID');
      stage = 'download';
      await poll(api, run);
      return;
    }
    if (command === 'poll') {
      if (!meta.task_id) throw Error('No task ID');
      stage = 'poll';
      const response = await api.request('/api/task/generation/progress', {taskIds:[meta.task_id]});
      await save(path.join(run,'records/status.json'), response);
      const progress = response.data?.progresses?.find(item => String(item.taskId) === meta.task_id);
      const status = {0:'queued',1:'running',2:'generated',3:'failed',4:'timeout'}[progress?.status];
      if (!status) throw Error('Unrecognized task status');
      await update(run,{status, progress_percent:progress.progressPercent ?? 0,
        credits:progress.power ?? meta.credits, failed_reason:progress.failedReason ?? null});
      return;
    }
    if (command === 'submit') {
      if (meta.task_id) return;
      if (meta.status !== 'uploaded') throw Error('Refusing duplicate or uncertain submission');
      stage = 'submit';
      const result = await submit(api,run,meta.id,async () => true);
      if (result.state === 'waiting_free') await update(run,{status:'waiting_free'});
      return;
    }
    if (!canSubmit(meta)) throw Error('Refusing duplicate or uncertain submission');
    const spec = JSON.parse(await readFile(path.join(run, 'inputs/prompt-spec.json'), 'utf8'));
    const compiled = JSON.parse(await readFile(path.join(run, 'records/prompt-compilation.json'), 'utf8'));
    if (compiled.prompt !== await readFile(path.join(run, 'inputs/prompt.txt'), 'utf8')) throw Error('Prompt snapshot changed');
    if (compiled.bindings.length !== spec.references.length) throw Error('Reference count changed');
    const info = JSON.parse(await readFile(path.join(run, 'records/media-inputs.json'), 'utf8'));
    let uploads = {};
    try {uploads = JSON.parse(await readFile(path.join(run, 'records/uploads.json'), 'utf8'));}
    catch (error) {if (error.code !== 'ENOENT') throw error;}
    stage = 'upload';
    await update(run, {status:'uploading', last_error:null});
    const uploader = new MediaUploader(api, await accountUploadUuid(process.env.JS_REVERSE_BROWSER_URL));
    const mixed = [], media = {image:[], video:[], audio:[]};
    for (const [index, ref] of spec.references.entries()) {
      const filename = path.resolve(run, ref.file);
      if (!filename.startsWith(path.resolve(run, 'inputs') + path.sep)) throw Error('Invalid input snapshot path');
      const digest = createHash('sha256').update(await readFile(filename)).digest('hex');
      if (meta.file_checksums[ref.file] !== digest) throw Error('Input snapshot changed');
      if (uploads[ref.file]?.sha256 !== digest || !uploads[ref.file]?.audit?.passed) {
        uploads[ref.file] = await uploader.upload(filename);
        await save(path.join(run, 'records/uploads.json'), uploads);
      }
      mixed.push({url:uploads[ref.file].url, type:ref.type});
      media[ref.type].push({...info[ref.file], url:uploads[ref.file].url});
      if (compiled.bindings[index].id !== ref.id || compiled.bindings[index].type !== ref.type) throw Error('Reference order changed');
    }
    const request = {params:{prompt:compiled.prompt, model:'wanx3.0', modeType:'mixed2video', count:1,
      ratio:spec.aspect_ratio, resolution:'720P', duration:spec.duration, enableSound:'on', extendPrompt:0,
      infiniteSwitch:0, textList:[], imageList:[], imageLabelList:[], videoList:[], audioList:[],
      imageListV2:media.image, videoListV2:media.video, audioListV2:media.audio, mixedList:mixed},
      metadata:{node_id:'v-6SIVysJx8O', project_id:'52c9de0f1ad24432927236bd9e3413c9'},
      provider:'Wan', model:'wanx3.0', taskType:'video', requestId:randomUUID().replaceAll('-','')};
    await save(path.join(run, 'inputs/request.json'), request);
    const mapping = JSON.parse(await readFile(path.join(run, 'inputs/input-map.json'), 'utf8'));
    await save(path.join(run, 'inputs/input-map.json'), mapping.map((entry,index) => ({...entry,
      field:`params.mixedList[${index}].url`, url:mixed[index].url})));
    for (const filename of ['canvas-transport.mjs','canvas-run.mjs']) {
      await copyFile(path.join(legacy,'tools/js-reverse-mcp',filename), path.join(run,'code',filename));
    }
    await copyFile(path.join(legacy,'libtv/adapters/canvas-media.mjs'), path.join(run,'code/canvas-media.mjs'));
    await copyFile(path.join(legacy,'libtv/workbench/submit.mjs'), path.join(run,'code/submit.mjs'));
    await copyFile(path.join(legacy,'tools/js-reverse-mcp/release.json'), path.join(run,'code/legacy-release.json'));
    await update(run, {status:'uploaded'});
    stage = 'estimate';
    const preview = await api.request('/api/task/generation/power/calculator', request);
    await save(path.join(run, 'records/estimate-preflight.json'), preview);
    if (!freeQuote(preview.data?.power)) {
      const cost = preview.data?.power;
      const valid = typeof cost === 'number' && Number.isFinite(cost) && cost >= 0;
      await update(run, {status:valid ? 'waiting_free' : 'service_error',
        estimated_credits:valid ? cost : null});
      return;
    }
    // Actual submission is dispatched through GenerationRunner after preparation.

  });
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(async error => {
    const codes = {verify:'RELEASE_INVALID',login:'LOGIN_REQUIRED',upload:'UPLOAD_OR_AUDIT_FAILED',
      estimate:'ESTIMATE_UNAVAILABLE',submit:'SUBMISSION_UNCONFIRMED',poll:'POLL_FAILED',download:'DOWNLOAD_FAILED'};
    const code = error?.code === 'EEXIST' ? 'RUN_LOCK_BUSY' : codes[stage] ?? 'ADAPTER_FAILED';
    const {writeFile} = await import('node:fs/promises');
    const run = process.argv[3];
    if (run && path.isAbsolute(run)) await writeFile(path.join(run,'records/executor-error.json'),
      JSON.stringify({code,stage,message:'LibTV stage did not complete; credentials and raw responses omitted.'}));
    console.error(JSON.stringify({code,stage}));
    process.exitCode=1;
  });
}
