/**
 * 智能去字幕的**单次调用**入口（由 Python 执行器按需拉起，不常驻）。
 *
 * 用法：node desubtitle.mjs '{"account":"http://127.0.0.1:9412","source":"<本地路径或URL>",
 *                             "width":720,"height":1280,"duration":4.0}'
 * 输出：一行 JSON —— 成功 `{"taskId":"...","power":0,"sourceUrl":"..."}`，
 *      失败 `{"error":"..."}` 并且退出码非 0。
 *
 * 为什么单独一个文件而不是塞进 `bridge.mjs`：桥接层是**运行档案**的执行器
 * （上传素材 → 编译提示词 → 提交 → 轮询 → 下载），去字幕没有档案、没有素材要传，
 * 只在提交这一步需要「把本地成片传到上游 + 建任务」。把两种生命周期塞进同一个
 * 入口，会让 bridge 的每一条纪律（幂等、回执、账号绑定）都要多一套分支。
 */
import {readFile, stat} from 'node:fs/promises';
import path from 'node:path';
import {randomUUID} from 'node:crypto';

const VENDOR = new URL('./vendor/', import.meta.url).pathname;
const {CanvasApi} = await import(new URL('tools/js-reverse-mcp/canvas-transport.mjs', `file://${VENDOR}`).href);
const {MediaUploader, accountUploadUuid} = await import(new URL('libtv/adapters/canvas-media.mjs', `file://${VENDOR}`).href);

function out(payload, code = 0) {
  console.log(JSON.stringify(payload));
  process.exit(code);
}

const raw = process.argv[2];
if (!raw) out({error: '缺少 JSON 参数'}, 1);

let args;
try {
  args = JSON.parse(raw);
} catch (error) {
  out({error: `参数不是合法 JSON：${error.message}`}, 1);
}

const account = String(args.account || 'http://127.0.0.1:9412');
const source = String(args.source || '');
if (!source) out({error: '缺少 source（本地文件或 http 地址）'}, 1);

const api = await CanvasApi.fromBrowser(account);
const post = (route, body) => api.request(route, body);

/* 本地文件先传到上游素材库：`create` 只接受上游能取到的 URL。
   MediaUploader 顺带做视频审核，未过审的素材不会被受理。 */
let sourceUrl = source;
if (!/^https?:\/\//i.test(source)) {
  const absolute = path.resolve(source);
  await stat(absolute);
  const uploader = new MediaUploader(api, await accountUploadUuid(account));
  const uploaded = await uploader.upload(absolute);
  sourceUrl = uploaded.url;
}

/* 画布 uuid 必须与浏览器里当前打开的画布一致，否则上游回 30001。 */
async function currentProjectId() {
  try {
    const tabs = await (await fetch(new URL('/json/list', account), {signal: AbortSignal.timeout(4000)})).json();
    for (const tab of tabs) {
      const match = /liblib\.tv\/canvas\?[^#]*projectId=([0-9a-f]{32})/i.exec(tab.url ?? '');
      if (match) return match[1];
    }
  } catch { /* 交给下面的报错 */ }
  return '';
}

const projectId = args.projectId || await currentProjectId();
if (!projectId) out({error: '读不到当前画布：请在该账号浏览器里打开一个画布页后重试'}, 1);

const body = {
  params: {
    model: 'volcano-subtitle-eraser',
    mode: 'Subtitle',
    videoList: [sourceUrl],
    videoListV2: [
      {
        url: sourceUrl,
        width: Number(args.width || 720),
        height: Number(args.height || 1280),
        duration: Number(args.duration || 4),
      },
    ],
  },
  metadata: {node_id: `v-desub${randomUUID().replaceAll('-', '').slice(0, 8)}`, project_id: projectId},
  provider: 'volcano',
  model: 'volcano-subtitle-eraser',
  taskType: 'video',
  requestId: randomUUID().replaceAll('-', '').slice(0, 16),
};

try {
  const quote = await post('/api/task/generation/power/calculator', body);
  const verification = await post('/api/security/client-verification-token', {});
  const data = verification.data;
  const token = typeof data === 'string' ? data : (data?.requestToken ?? data?.token);
  if (!token) out({error: '拿不到 client-verification-token，未提交'}, 1);
  let created;
  try {
    created = await post('/api/task/generation/create', {...body, riskControl: {requestToken: token}});
  } catch (error) {
    out({
      code: 'SUBMISSION_UNKNOWN',
      error: `去字幕创建请求结果未知（可能已受理或扣费）：${String(error?.message ?? error)}`,
    }, 1);
  }
  const taskId = created.data?.taskId ?? created.data?.task_id;
  if (!taskId) out({code: 'SUBMISSION_UNKNOWN', error: '上游没有返回 task_id，提交结果未知'}, 1);
  out({taskId: String(taskId), power: created.data?.power ?? quote.data?.power ?? 0, sourceUrl});
} catch (error) {
  out({error: String(error?.message ?? error)}, 1);
}
