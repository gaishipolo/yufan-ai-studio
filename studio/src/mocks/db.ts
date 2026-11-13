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
export const db = {
  tasks: buildTasks(),
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
