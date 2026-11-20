#!/usr/bin/env node
/**
 * LibTV「智能去字幕」—— 独立接口脚本（2026-11-20 逆向自 9412 画布）
 *
 * 逆向来源：画布里对视频节点点「智能去字幕」时，前端发的就是一条**普通生成任务**：
 *
 *   POST /api/task/generation/power/calculator
 *   {
 *     "params": {
 *       "model": "volcano-subtitle-eraser",
 *       "mode": "Subtitle",
 *       "videoList":   ["<源视频 mp4 URL>"],
 *       "videoListV2": [{"url": "<同一个 URL>", "width": 720, "height": 1280, "duration": 4.04}]
 *     },
 *     "metadata": {"node_id": "v-xxxx", "project_id": "<当前画布 uuid>"},
 *     "provider": "volcano",
 *     "model": "volcano-subtitle-eraser",
 *     "taskType": "video",
 *     "requestId": "<16 位随机>"
 *   }
 *
 * 然后照常 `POST /api/task/generation/create`（带 `riskControl.requestToken`，
 * 该 token 由 `POST /api/security/client-verification-token` 现取），
 * 用 `POST /api/task/generation/progress` 轮询——与普通视频任务同一条链路，
 * 产物是 `<源文件名>_<unix秒>_lib_upscaled.mp4`。
 *
 * **计费**：该模型在本账号（9412）实测 `power: 0`，即免费。
 *
 * 用法：
 *   node api/scripts/libtv_desubtitle.mjs --video https://... .mp4 --out out.mp4
 *   node api/scripts/libtv_desubtitle.mjs --video ./local.mp4 --width 720 --height 1280 --duration 4.0
 *   node api/scripts/libtv_desubtitle.mjs --video <url> --dry-run        # 只报价，不建任务
 *   node api/scripts/libtv_desubtitle.mjs --poll 20261120120103218604489 # 只轮询已有任务
 *
 * 输出：一行 JSON（便于后端/工作台直接消费），失败时退出码非 0。
 */
import {randomUUID} from 'node:crypto';
import {readFile, stat, writeFile} from 'node:fs/promises';
import path from 'node:path';

const VENDOR = new URL('../src/modules/model_driver/libtv/runtime/vendor/', import.meta.url).pathname;

function parseArgs(argv) {
  const out = {
    video: '',
    out: '',
    account: process.env.JS_REVERSE_BROWSER_URL || 'http://127.0.0.1:9412',
    projectId: '',
    nodeId: '',
    width: 720,
    height: 1280,
    duration: 0,
    poll: '',
    dryRun: false,
    timeoutSec: 600,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const next = () => argv[++i];
    if (key === '--video') out.video = next();
    else if (key === '--out') out.out = next();
    else if (key === '--account') out.account = next();
    else if (key === '--project-id') out.projectId = next();
    else if (key === '--node-id') out.nodeId = next();
    else if (key === '--width') out.width = Number(next());
    else if (key === '--height') out.height = Number(next());
    else if (key === '--duration') out.duration = Number(next());
    else if (key === '--poll') out.poll = next();
    else if (key === '--timeout') out.timeoutSec = Number(next());
    else if (key === '--dry-run') out.dryRun = true;
  }
  return out;
}

const MODEL = 'volcano-subtitle-eraser';

/** 画布 uuid 必须与浏览器当前打开的画布一致，否则上游回 30001「账号状态已切换」。 */
async function currentProjectId(account) {
  try {
    const list = await (await fetch(new URL('/json/list', account), {signal: AbortSignal.timeout(4000)})).json();
    for (const tab of list) {
      const match = /liblib\.tv\/canvas\?[^#]*projectId=([0-9a-f]{32})/i.exec(tab.url ?? '');
      if (match) return match[1];
    }
  } catch { /* 浏览器没连上就交给下面的报错 */ }
  return '';
}

function fail(message, extra = {}) {
  console.log(JSON.stringify({ok: false, error: message, ...extra}));
  process.exit(1);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  /* 直接用 vendor 的 CanvasApi：它带 token 头、校验路径，`MediaUploader` 也认它
     （上传适配器要读 `api.headers.token`，自己拼一个 {request} 的壳会炸）。 */
  const {CanvasApi} = await import(new URL('tools/js-reverse-mcp/canvas-transport.mjs', `file://${VENDOR}`).href);
  const api = await CanvasApi.fromBrowser(args.account);
  const post = (route, body) => api.request(route, body);

  /* 只轮询模式：给「提交成功但没等到结果」的调用方补一刀 */
  if (args.poll) {
    const result = await waitForTask(post, args.poll, args.timeoutSec);
    return result;
  }

  if (!args.video) fail('缺少 --video（URL 或本地文件）');

  let videoUrl = args.video;
  if (!/^https?:\/\//i.test(videoUrl)) {
    const {MediaUploader, accountUploadUuid} = await import(
      new URL('libtv/adapters/canvas-media.mjs', `file://${VENDOR}`).href
    );
    const absolute = path.resolve(videoUrl);
    await stat(absolute);
    const uploader = new MediaUploader(api, await accountUploadUuid(args.account));
    const uploaded = await uploader.upload(absolute);
    videoUrl = uploaded.url;
  }

  if (!args.duration) {
    // 时长只影响上游排队/切片估算，取不到就给一个保守值；宁可多等，不要报错。
    args.duration = 4;
  }

  const projectId = args.projectId || await currentProjectId(args.account);
  if (!projectId) fail('读不到当前画布 projectId：请在该账号浏览器里打开一个画布页后重试');

  const request = {
    params: {
      model: MODEL,
      mode: 'Subtitle',
      videoList: [videoUrl],
      videoListV2: [{url: videoUrl, width: args.width, height: args.height, duration: args.duration}],
    },
    metadata: {
      node_id: args.nodeId || `v-desub${randomUUID().replaceAll('-', '').slice(0, 8)}`,
      project_id: projectId,
    },
    provider: 'volcano',
    model: MODEL,
    taskType: 'video',
    requestId: randomUUID().replaceAll('-', '').slice(0, 16),
  };

  const quote = await post('/api/task/generation/power/calculator', request);
  const power = quote.data?.power ?? null;
  if (args.dryRun) {
    return {ok: true, dryRun: true, power, video: videoUrl};
  }

  const verification = await post('/api/security/client-verification-token', {});
  const data = verification.data;
  const token = typeof data === 'string' ? data : (data?.requestToken ?? data?.token);
  if (!token) fail('拿不到 client-verification-token，未提交');

  const created = await post('/api/task/generation/create', {...request, riskControl: {requestToken: token}});
  const taskId = created.data?.taskId ?? created.data?.task_id;
  if (!taskId) fail('create 没有返回 taskId', {power});

  const finished = await waitForTask(post, taskId, args.timeoutSec);
  return {...finished, taskId, power: created.data?.power ?? power, video: videoUrl};
}

/** 轮询到终态；成功则下载到 --out（不给就只返回 URL）。 */
async function waitForTask(post, taskId, timeoutSec) {
  const deadline = Date.now() + timeoutSec * 1000;
  for (;;) {
    const progress = await post('/api/task/generation/progress', {taskIds: [taskId]});
    const item = (progress.data?.progresses ?? []).find(p => String(p.taskId) === String(taskId));
    if (!item) throw new Error(`进度里没有任务 ${taskId}`);
    if (item.status === 2) {
      const url = JSON.parse(item.taskResult || '{}')?.videos?.[0]?.previewPath ?? '';
      if (!url) throw new Error('任务成功但没有产物地址');
      return {ok: true, taskId, url, power: item.power ?? 0};
    }
    if (item.status === 3) throw new Error(`上游任务失败：${item.failedReason ?? '未知原因'}`);
    if (Date.now() > deadline) throw new Error(`等待超过 ${timeoutSec} 秒仍未完成（task ${taskId}）`);
    await new Promise(resolve => setTimeout(resolve, 6000));
  }
}

const result = await main().catch(error => fail(String(error?.message ?? error)));
if (!result) process.exit(0);

/* 有 --out 就顺手下载并校验 MP4 头，省得调用方再写一遍 */
const args = parseArgs(process.argv.slice(2));
if (args.out && result.url) {
  try {
    const response = await fetch(result.url, {headers: {'User-Agent': 'Mozilla/5.0', Referer: 'https://www.liblib.tv/'}});
    if (!response.ok) throw new Error(`下载 HTTP ${response.status}`);
    const bytes = Buffer.from(await response.arrayBuffer());
    if (bytes.length < 1024 || bytes.subarray(4, 8).toString() !== 'ftyp') {
      throw new Error('下载结果不是 MP4，已放弃写入');
    }
    await writeFile(path.resolve(args.out), bytes);
    result.file = path.resolve(args.out);
    result.bytes = bytes.length;
  } catch (error) {
    result.downloadError = String(error?.message ?? error);
  }
}
console.log(JSON.stringify(result));
process.exit(result.ok ? 0 : 1);
