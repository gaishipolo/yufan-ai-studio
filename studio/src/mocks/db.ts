import type { ArchivedAsset } from '@/shared/api/schema';
import type { ShotPreset } from '@/entities/shot/model/types';
import type { Storyboard } from '@/entities/storyboard/model/types';
import type { AspectRatio, TaskStatus, VideoTask } from '@/entities/task/model/types';
import type { ImageJob } from '@/entities/image/model/types';

/* ------------------------------------------------------------------
 * 内存数据库：模拟后端持久层
 * 未来接入真实后端时，整个 mocks 目录可直接删除，业务代码零改动
 * ------------------------------------------------------------------ */

const NOW = Date.now();

/** 单条视频的预计生成耗时（mock 用 60s 模拟真实排队 + 渲染） */
const GENERATION_MS = 60_000;
/** 单张图的预计生成耗时（真实后端 3–12s，mock 取中段，便于验证轮询） */
const IMAGE_GENERATION_MS = 9_000;
const minutesAgo = (m: number) => new Date(NOW - m * 60_000).toISOString();

const cover = (seed: string, w = 400, h = 500) => `https://picsum.photos/seed/${seed}/${w}/${h}`;

const MODELS = ['Seedance 2.5', 'Seedance 2.0', '可灵 2.1', 'Vidu Q1', '海螺 01'];
const RATIOS: AspectRatio[] = ['9:16', '16:9', '1:1'];

const TASK_SEEDS: Array<{ title: string; status: TaskStatus; minutes: number }> = [
  { title: '按照@图1图片1故事板进行生成_超市场景补拍', status: 'completed', minutes: 12 },
  { title: '美国超市抢购裂变2-胡佛·一、视频开场镜头', status: 'completed', minutes: 47 },
  { title: '20261017期 超市抢购·分镜1（0-5s）', status: 'completed', minutes: 96 },
  { title: '主角推门进入卖场 特写镜头（重生成中）', status: 'running', minutes: 2 },
  { title: '冰川徒步第一视角 12s 长镜头', status: 'completed', minutes: 150 },
  { title: '夜跑城市街头 运镜 A/B 对比', status: 'completed', minutes: 210 },
  { title: '产品开箱特写 4K 精修版', status: 'completed', minutes: 260 },
  { title: '咖啡店晨间氛围 空镜素材', status: 'completed', minutes: 320 },
  { title: '人物转身慢动作 120fps', status: 'completed', minutes: 400 },
  { title: '海边日落延时 情感向', status: 'completed', minutes: 460 },
  { title: '健身房力量训练 分镜 3/5', status: 'running', minutes: 1 },
  { title: '直播间口播开场 30s 成片', status: 'completed', minutes: 520 },
  { title: '复古胶片感 街景空镜', status: 'completed', minutes: 610 },
  { title: '无人机穿越森林 主观视角', status: 'completed', minutes: 700 },
  { title: '服装模特走位 换装转场', status: 'completed', minutes: 800 },
  { title: '厨房烹饪特写 手部细节', status: 'completed', minutes: 900 },
  { title: '办公室汇报场景 竖版适配', status: 'failed', minutes: 980 },
  { title: '车载第一视角 夜路雨景', status: 'completed', minutes: 1100 },
];

function buildTasks(): VideoTask[] {
  return TASK_SEEDS.map((seed, i) => {
    const id = `T20261105${1000 + i}`;
    return {
      id,
      title: seed.title,
      status: seed.status,
      model: MODELS[i % MODELS.length],
      durationSec: 5 + (i % 6) * 2,
      aspectRatio: RATIOS[i % 3],
      progress: seed.status === 'running' ? 30 + ((i * 13) % 40) : seed.status === 'completed' ? 100 : 18,
      coverUrl: cover(`sf-task-${i}`),
      prompt: '超市抢购场景，手持镜头跟拍，人群涌动，暖色顶光，电影感浅景深',
      authorId: seed.status === 'failed' || i % 4 !== 0 ? 'u_001' : 'u_002',
      authorName: seed.status === 'failed' || i % 4 !== 0 ? '老大' : '林设计',
      favorited: i % 5 === 0,
      downloadable: seed.status === 'completed',
      errorMessage: seed.status === 'failed' ? '参考图分辨率过低（640×360），请上传 1080P 以上素材' : undefined,
      createdAt: minutesAgo(seed.minutes),
      updatedAt: minutesAgo(Math.max(seed.minutes - 1, 0)),
      startedAt:
        seed.status === 'running' || seed.status === 'queued'
          ? minutesAgo(0.4 + (i % 3) * 0.15)
          : undefined,
    };
  });
}

/**
 * 素材 mock 直接造归档契约（`ArchivedAsset`），不再是手写的 `Asset`。
 *
 * 两套素材接口合并成一套 `/assets` 之后，前端已经没有第二种素材形状：类型直接
 * 取自生成物，契约一变这里就编译不过——手写类型当年正是靠「看起来差不多」
 * 一直活下来的。
 */
function buildAssets(): ArchivedAsset[] {
  type Kind = ArchivedAsset['type'];
  const seeds: Array<[string, Kind, ArchivedAsset['category'], number, number]> = [
    ['超市抢购-参考图 01', 'image', 'scene', 820 * 1024, 1080],
    ['超市抢购-参考图 02', 'image', 'scene', 1_150_000, 1080],
    ['卖场空镜 4K', 'video', 'source', 16_800_000, 1920],
    ['收银台特写', 'video', 'source', 22_200_000, 1920],
    ['背景音乐-urban pulse', 'audio', 'audio', 4_400_000, 0],
    ['雪山徒步素材', 'video', 'source', 25_000_000, 3840],
    ['品牌 LOGO 白色', 'image', 'product', 2_600_000, 2000],
    ['人物走位参考', 'video', 'source', 35_800_000, 1920],
    ['环境音-雨天街道', 'audio', 'audio', 6_600_000, 0],
    ['产品静物图', 'image', 'product', 3_500_000, 2048],
    ['转场素材 光斑', 'video', 'source', 8_900_000, 1920],
    ['片头动画 AE', 'video', 'source', 42_000_000, 3840],
    ['人物特写合集', 'image', 'character', 4_100_000, 1536],
    ['字幕底条模板', 'image', 'character', 690_000, 1080],
    ['鼓点音效', 'audio', 'audio', 1_800_000, 0],
    ['城市夜景航拍', 'video', 'source', 28_400_000, 3840],
  ];
  return seeds.map(([name, type, category, sizeBytes, width], i) => {
    // 摘要只是 64 位十六进制字符串：mock 不校验内容，但列表、`file_checksums`
    // 与执行器清单都以它为键，形状必须对
    const sha = i.toString(16).padStart(2, '0').repeat(32);
    const thumb = cover(`sf-asset-${i}`, 400, 400);
    const shared = i % 3 === 0;
    // 两条是"生成出来的"：第 1 条与第 10 条各自挂了一条配方，详情浮层的
    // 生成血缘才有东西可看。其余是上传件——**上传件血缘为空是事实，不是错误**。
    const recipeId = i === 0 ? 'R100' : i === 9 ? 'R101' : '';
    return {
      id: `A${1000 + i}`,
      name,
      category,
      type,
      mimeType:
        type === 'image' ? 'image/jpeg' : type === 'video' ? 'video/mp4' : 'audio/mpeg',
      sizeBytes,
      path: `materials/${sha.slice(0, 2)}/${sha}.${type === 'image' ? 'jpg' : type === 'video' ? 'mp4' : 'mp3'}`,
      sha256: sha,
      createdAt: minutesAgo(60 * 5 + i * 37),
      width,
      height: width ? Math.round(width * 0.75) : 0,
      duration: type === 'video' ? 8 + (i % 7) : type === 'audio' ? 30 + (i % 90) : 0,
      hasAudio: type !== 'image',
      // mock 没有对象存储：图片拿占位图当原件，音视频给空串。真实环境这里都是
      // `/assets/{id}/file`，前端一律带令牌取 Blob，所以两条路径都不影响布局
      url: type === 'image' ? thumb : '',
      previewUrl: type === 'image' ? thumb : null,
      ownerId: i % 5 === 0 ? 'u_002' : 'u_001',
      shared,
      sharedAt: shared ? minutesAgo(60 * 5 + i * 37 + 30) : null,
      // 留一条在回收站里，回收站页签不至于永远空着
      deletedAt: i === 15 ? minutesAgo(120) : null,
      stateVersion: 1,
      editable: true,
      downloadAllowed: true,
      // 上传路径必须**显式**写这三列：列级默认值是"生成件的假设"
      // （`compliance_status` 默认 pending），靠默认值会让上传的图不能进主体
      origin: recipeId ? 'generate' : 'upload',
      stage: 'adopted',
      recipeId,
      complianceStatus: 'approved',
      adoptedAt: recipeId ? minutesAgo(60 * 5 + i * 37) : null,
      categoryId: `cat-${['source', 'character', 'product', 'scene', 'audio'].indexOf(category)}`,
    };
  });
}

/* ------------------------------------------------------------------
 * 配方与候选（M3）
 *
 * 三件事必须分开存，和后端同构：
 *   1. `recipes` 是**事实**（一次生成用了什么模型、什么提示词、参考了哪几张图），
 *      一条素材可被复制到三个库，但它们说的是同一次生成事件，配方只有一份；
 *   2. `assets` 是**已采纳**的素材，`recipeId` 指回配方；
 *   3. `candidates` 是**待办**，故意不放进 `assets`——它不进 `GET /assets`、
 *      不计配额、不进主体。mock 里如果图省事塞进 `assets` 再过滤，前端就会
 *      在「候选会出现在列表里」的假前提下开发，上了真后端才发现要改。
 * ------------------------------------------------------------------ */

export interface MockRecipe {
  id: string;
  prompt: string;
  model: string;
  provider: string;
  seed: string;
  params: Record<string, unknown>;
  /** 冻结的参考图列表（与后端 `reference_asset_ids` 同义） */
  referenceIds: string[];
  /** 修正链：这条是「照哪条再生成一次」 */
  parentId: string | null;
  createdAt: string;
}

function buildRecipes(): MockRecipe[] {
  const base = {
    model: 'Agnes Image 2.5 Flash',
    provider: 'agnes',
    seed: '',
    params: {size: '1024x1536', count: 1},
  };
  return [
    {
      ...base,
      id: 'R100',
      prompt: '超市抢购场景，手持镜头跟拍，人群涌动，暖色顶光，电影感浅景深',
      referenceIds: ['A1002'],
      parentId: null,
      createdAt: minutesAgo(60 * 5),
    },
    {
      ...base,
      id: 'R101',
      prompt: '产品静物，深灰渐变背景，侧逆光，金属高光，极简留白',
      referenceIds: [],
      parentId: null,
      createdAt: minutesAgo(60 * 9),
    },
    {
      ...base,
      id: 'R200',
      prompt: '赛博朋克街头，霓虹灯牌，雨夜地面反光，电影级打光，超高细节，9:16 竖构图',
      referenceIds: ['A1000', 'A1001'],
      parentId: null,
      createdAt: minutesAgo(18),
    },
    {
      ...base,
      id: 'R201',
      prompt: '产品静物，深灰渐变背景，侧逆光，金属高光，极简留白，加一层薄雾',
      referenceIds: ['A1009'],
      // 这是「照 R101 再生成一次」，衍生链上会显示父配方
      parentId: 'R101',
      createdAt: minutesAgo(9),
    },
  ];
}

/** 未采纳的生成结果。`expiresAt` 只存在组上：一批里最早那张到期，整批就该消失。 */
export interface MockCandidate {
  asset: ArchivedAsset;
  recipeId: string;
  expiresAt: string;
}

function buildCandidates(): MockCandidate[] {
  const seeds: Array<[string, string, number]> = [
    ['赛博朋克街景 · 候选 1', 'R200', 1],
    ['赛博朋克街景 · 候选 2', 'R200', 2],
    ['赛博朋克街景 · 候选 3', 'R200', 3],
    ['赛博朋克街景 · 候选 4', 'R200', 4],
    ['产品薄雾版 · 候选 1', 'R201', 5],
    ['产品薄雾版 · 候选 2', 'R201', 6],
  ];
  const expires = new Date(NOW + 7 * 24 * 3600_000).toISOString();
  return seeds.map(([name, recipeId, seq]) => {
    const thumb = cover(`sf-cand-${seq}`, 400, 500);
    const sha = `${(200 + seq).toString(16)}`.padStart(64, '0');
    return {
      recipeId,
      expiresAt: expires,
      asset: {
        id: `C${900 + seq}`,
        name,
        category: 'scene',
        type: 'image',
        mimeType: 'image/png',
        sizeBytes: 1_400_000 + seq * 90_000,
        path: `drafts/${sha.slice(0, 2)}/${sha}.png`,
        sha256: sha,
        createdAt: minutesAgo(20 - seq),
        width: 1024,
        height: 1536,
        duration: 0,
        hasAudio: false,
        url: thumb,
        previewUrl: thumb,
        ownerId: 'u_001',
        shared: false,
        sharedAt: null,
        deletedAt: null,
        stateVersion: 0,
        editable: true,
        downloadAllowed: true,
        // 列级默认值的"生成件假设"：候选出身即待审，采纳也不改合规
        origin: 'generate',
        stage: 'candidate',
        recipeId,
        complianceStatus: 'pending',
        adoptedAt: null,
        categoryId: '',
      },
    };
  });
}

const storyboards: Storyboard[] = [
  '开场-环境交代', '主角登场-中景', '冲突爆发-手持', '细节特写-慢镜', '结尾-品牌落版',
].map((name, i) => ({
  id: `SB${200 + i}`,
  name,
  description: '镜头序列已编排，可直接生成为视频任务',
  shotCount: 4 + i,
  durationSec: 12 + i * 3,
  aspectRatio: '9:16' as AspectRatio,
  coverUrl: cover(`sf-sb-${i}`, 480, 280),
  createdAt: minutesAgo(60 * 24 * (i + 1)),
}));

const shots: ShotPreset[] = [
  ['推镜-人物特写', 'slow push in, close-up, shallow depth of field', 3],
  ['环绕-产品', 'orbit camera, product hero shot, soft light', 4],
  ['手持跟拍', 'handheld follow, natural shake, documentary style', 5],
  ['俯拍-桌面', 'top-down shot, flat lay, clean background', 3],
  ['穿越-第一视角', 'dolly through, POV, immersive', 6],
  ['拉镜-环境交代', 'pull out, establishing shot, wide angle', 4],
  ['摇镜-街景', 'pan left, street scene, golden hour', 3],
  ['升降-建筑', 'crane up, architecture, dramatic scale', 5],
].map(([name, promptFragment, durationSec], i) => ({
  id: `SH${300 + i}`,
  name: name as string,
  promptFragment: promptFragment as string,
  durationSec: durationSec as number,
  coverUrl: cover(`sf-shot-${i}`, 480, 280),
  popularity: 30 + i * 9,
}));

/** 出参形状跟随后端 ImageJobOut，mock 额外把异步状态设为必填 */
export type MockImageJob = ImageJob & {
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  progress: number;
};

const imageJobs: MockImageJob[] = [
  '赛博朋克街景 霓虹人像', '产品白底图 精修', '古风女子 半身像',
  '雪山日出 空镜', '卡通 IP 三视图', '服装模特换装',
].map((name, i) => {
  const url = cover(`sf-img-${i}`, 400, 500);
  return {
    id: `IMG${500 + i}`,
    name,
    prompt: 'cinematic lighting, ultra detailed',
    // 尺寸随 paramSchema 走半角 x，不再写全角 ×
    size: ['1024x1536', '1024x1024', '1536x1024'][i % 3],
    count: 1,
    status: 'succeeded' as const,
    progress: 100,
    model: 'Agnes Image 2.5 Flash',
    modelId: 'agnes-image-2.5-flash',
    coverUrl: url,
    outputUrl: url,
    errorMessage: null,
    createdAt: minutesAgo(30 + i * 90),
  };
});

/* ------------------------------------------------------------------
 * 可变的库：任务进度推进 / 创建 / 删除
 * ------------------------------------------------------------------ */

/**
 * 素材 → 标签 id。只在这一处存，`GET` 与 `PUT /assets/{id}/tags` 读写同一份，
 * 否则「保存成功但浮层里还是旧标签」这类问题在 mock 里先复现不出来。
 */
const assetTags: Record<string, string[]> = {
  A1000: ['style-1', 'scene-3', 'composition-0'],
  A1001: ['style-1', 'shot-1'],
  A1002: ['shot-0', 'placement-2'],
  A1009: ['style-7', 'scene-8', 'placement-0'],
};

export const db = {
  tasks: buildTasks(),
  assets: buildAssets(),
  recipes: buildRecipes(),
  candidates: buildCandidates(),
  /**
   * 已弃用 / 已过期的候选。**行还在，只是不进任何列表**——与后端一致：
   * 弃用只翻 `stage`，字节等宽限期回收，配方永久保留（衍生链可能从它经过）。
   * 单独一个数组是为了让 `GET /assets/candidates` 与 `GET /assets` 都干净，
   * 而"这条已经关了"仍然答得出来。
   */
  closed: [] as ArchivedAsset[],
  assetTags,
  storyboards,
  shots,
  imageJobs,

  /**
   * 模拟后台 worker：进度按「已耗时 / 预计耗时」推算，
   * 与请求次数无关，多个组件并发拉取也不会出现进度跳变
   */
  tick(): void {
    const now = Date.now();

    db.tasks.forEach((t) => {
      if (t.status === 'queued') {
        const started = new Date(t.startedAt ?? t.createdAt).getTime();
        if (now - started > 3_000) t.status = 'running';
      }

      if (t.status === 'running') {
        const started = new Date(t.startedAt ?? t.createdAt).getTime();
        const pct = Math.floor(((now - started) / GENERATION_MS) * 100);
        if (pct >= 100) {
          t.status = 'completed';
          t.progress = 100;
          t.downloadable = true;
        } else {
          t.progress = Math.min(99, Math.max(1, pct));
        }
      }
    });

    // 生图同样是异步的：queued → running → succeeded，完成后才回填成图地址。
    // 这样 mock 模式也能演示「提交后不刷新，卡片自己变成图」的轮询闭环。
    db.imageJobs.forEach((job) => {
      if (job.status === 'succeeded' || job.status === 'failed') return;
      const elapsed = now - new Date(job.createdAt).getTime();
      if (elapsed >= IMAGE_GENERATION_MS) {
        const url = cover(`sf-img-${job.id}`, 400, 500);
        job.status = 'succeeded';
        job.progress = 100;
        job.coverUrl = url;
        job.outputUrl = url;
      } else if (elapsed >= 3_000) {
        job.status = 'running';
        job.progress = Math.min(99, Math.max(1, Math.floor((elapsed / IMAGE_GENERATION_MS) * 100)));
      } else {
        job.status = 'queued';
        job.progress = 0;
      }
    });
  },

  addTask(input: {
    title: string;
    prompt: string;
    model: string;
    durationSec: number;
    aspectRatio: AspectRatio;
  }): VideoTask {
    const seq = db.tasks.length + 1;
    const task: VideoTask = {
      id: `T${Date.now()}`,
      title: input.title,
      status: 'queued',
      model: input.model,
      durationSec: input.durationSec,
      aspectRatio: input.aspectRatio,
      progress: 0,
      coverUrl: cover(`sf-new-${seq}`),
      prompt: input.prompt,
      authorId: 'u_001',
      authorName: '老大',
      favorited: false,
      downloadable: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      startedAt: new Date().toISOString(),
    };
    db.tasks.unshift(task);
    return task;
  },
};
