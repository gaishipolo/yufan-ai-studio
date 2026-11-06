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
  /* 计费适配器只在加速路径上懒加载：免费队列的每次上传都不该因为
     一个用不到的模块而依赖 legacy 目录里存在 billing.mjs。 */
  // 积分提交不能复用 legacy 的 submit.mjs：它硬校验 infiniteSwitch===0（免费门禁），
  // 加速按定义就是 infiniteSwitch=1。这里只做等价校验，门禁条件换成积分那条队列。
  const submitCredits = async (api, run, meta) => {
    const request = JSON.parse(await readFile(path.join(run, 'inputs/request.json'), 'utf8'));
    const compiled = JSON.parse(await readFile(path.join(run, 'records/prompt-compilation.json'), 'utf8'));
    const {verifyMixedList} = await moduleAt('libtv/prompt-compiler.mjs');
    verifyMixedList(compiled, request.params.mixedList);
    const p = request.params;
    if (compiled.prompt !== p.prompt || p.resolution !== '720P' || p.model !== 'wanx3.0'
      || p.modeType !== 'mixed2video' || p.count !== 1 || p.infiniteSwitch !== 1)
      throw Error('Unexpected credit request');
    if (!Array.isArray(request.disableBenefitTags) || !request.disableBenefitTags.length)
      throw Error('Credit request is missing the account zero-benefit tags');
    const estimate = await api.request('/api/task/generation/power/calculator', request);
    await save(path.join(run, 'records/estimate.json'), estimate);
    const cost = estimate.data?.power;
    if (typeof cost !== 'number' || !Number.isFinite(cost) || cost <= 0)
      throw Error('Credit queue still quotes zero; not submitted');
    await update(run, {estimated_credits: cost});
    const verification = await api.request('/api/security/client-verification-token', {});
    const d = verification.data;
    const token = typeof d === 'string' ? d : d?.requestToken ?? d?.token;
    if (!token) throw Error('Normal client verification failed; not submitted');
    await update(run, {status: 'submitting'});
    let result;
    try {
      result = await api.request('/api/task/generation/create', {...request, riskControl: {requestToken: token}});
      await save(path.join(run, 'records/submission.json'), result);
    } catch {
      await update(run, {status: 'submission_unknown', last_error: 'CreateTask result unconfirmed; reconcile before retry'});
      throw Error('Submission unknown; never automatically resubmit');
    }
    const id = result.data?.taskId ?? result.data?.task_id;
    if (!id) {
      await update(run, {status: 'submission_unknown'});
      throw Error('Submission returned no task_id; do not retry');
    }
    const charged = result.data?.power;
    await update(run, {status: 'submitted', task_id: String(id),
      credits: typeof charged === 'number' ? charged : cost});
  };
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
      /* 上游 progress.power 在排队/生成阶段恒为 0，结算后才回填。无条件覆盖会把
         提交时记下的真实扣费改写成 0，档案从此「看起来没花钱」。只在拿到正数时更新；
         若已被改写成 0，回落到提交回执 data.power（那是真正结算的那笔）。 */
      let known = meta.credits;
      if (!(typeof known === 'number' && known > 0)) {
        try {
          const receipt = JSON.parse(await readFile(path.join(run, 'records/submission.json'), 'utf8'));
          const billed = receipt?.data?.power;
          if (typeof billed === 'number' && billed > 0) known = billed;
        } catch {}
      }
      const settled = typeof progress.power === 'number' && progress.power > 0 ? progress.power : known;
      await update(run,{status, progress_percent:progress.progressPercent ?? 0,
        credits:settled, failed_reason:progress.failedReason ?? null});
      return;
    }
    if (command === 'submit') {
      if (meta.task_id) return;
      if (meta.status !== 'uploaded') throw Error('Refusing duplicate or uncertain submission');
      stage = 'submit';
      if (meta.billing_mode === 'credits') {await submitCredits(api, run, meta); return;}
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
    let request = {params:{prompt:compiled.prompt, model:'wanx3.0', modeType:'mixed2video', count:1,
      ratio:spec.aspect_ratio, resolution:'720P', duration:spec.duration, enableSound:'on', extendPrompt:0,
      infiniteSwitch:0, textList:[], imageList:[], imageLabelList:[], videoList:[], audioList:[],
      imageListV2:media.image, videoListV2:media.video, audioListV2:media.audio, mixedList:mixed},
      metadata:{node_id:'v-6SIVysJx8O', project_id:'52c9de0f1ad24432927236bd9e3413c9'},
      provider:'Wan', model:'wanx3.0', taskType:'video', requestId:randomUUID().replaceAll('-','')};
    const credits = meta.billing_mode === 'credits';
    if (credits) {
      // 加速：关闭该账号当前的 0 积分权益标签，走正常积分队列。
      // 只作用于这一条任务的请求体，账号级开关（billing-default）一律不动。
      const {applyBilling, accountCreditTags} = await moduleAt('libtv/adapters/billing.mjs');
      request = applyBilling(request, 'credits', await accountCreditTags(api));
    }
    await save(path.join(run, 'inputs/request.json'), request);
    const mapping = JSON.parse(await readFile(path.join(run, 'inputs/input-map.json'), 'utf8'));
    await save(path.join(run, 'inputs/input-map.json'), mapping.map((entry,index) => ({...entry,
      field:`params.mixedList[${index}].url`, url:mixed[index].url})));
    for (const filename of ['canvas-transport.mjs','canvas-run.mjs']) {
      await copyFile(path.join(legacy,'tools/js-reverse-mcp',filename), path.join(run,'code',filename));
    }
    await copyFile(path.join(legacy,'libtv/adapters/canvas-media.mjs'), path.join(run,'code/canvas-media.mjs'));
    await copyFile(path.join(legacy,'libtv/workbench/submit.mjs'), path.join(run,'code/submit.mjs'));
    if (credits) await copyFile(path.join(legacy,'libtv/adapters/billing.mjs'), path.join(run,'code/billing.mjs'));
    await copyFile(path.join(legacy,'tools/js-reverse-mcp/release.json'), path.join(run,'code/legacy-release.json'));
    await update(run, {status:'uploaded'});
    stage = 'estimate';
    const preview = await api.request('/api/task/generation/power/calculator', request);
    await save(path.join(run, 'records/estimate-preflight.json'), preview);
    if (credits) {
      // 积分队列必须给正数报价：报 0 说明关标签没生效，此时提交等于「以为花钱插队，
      // 实际又排回免费队列」，所以停下来让人核对，不提交。
      const cost = preview.data?.power;
      const quoted = typeof cost === 'number' && Number.isFinite(cost) && cost > 0;
      await update(run, {status: quoted ? 'uploaded' : 'service_error',
        estimated_credits: quoted ? cost : null,
        last_error: quoted ? null : '积分队列报价仍为 0，请核对账号权益后重试'});
      return;
    }
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
    // 只脱敏长凭证串（token/uuid/签名），保留可诊断的信息如 HTTP 状态、审计结论与出问题的文件。
    const where = error?.path ? ` @ ...${String(error.path).slice(-80)}` : '';
    const detail = `${String(error?.message ?? error)}${where}`.replace(/[A-Za-z0-9_-]{24,}/g, '<redacted>').slice(0, 500);
    if (run && path.isAbsolute(run)) await writeFile(path.join(run,'records/executor-error.json'),
      JSON.stringify({code,stage,detail,message:'LibTV stage did not complete; credentials and raw responses omitted.'}));
    console.error(JSON.stringify({code,stage,detail}));
    process.exitCode=1;
  });
}
