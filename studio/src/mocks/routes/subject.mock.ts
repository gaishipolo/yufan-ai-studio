import type { ArchivedAsset, SubjectMemberOut, SubjectOut } from '@/shared/api/schema';
import { db } from '../db';
import { HttpError, type MockRoute } from '../kernel';

/* ------------------------------------------------------------------
 * 主体：两个演示主体 + 三态 + 发布
 *
 * 刻意做成两个：一个 `ready`（可发布）、一个 `incomplete`（缺 download）。
 * 只给"能用的"会让可用性角标永远只有一种颜色，而那个角标正是这一批要看的东西。
 *
 * **可用性在 mock 里重算一遍**，而不是写死字段：写死的话，改了成员的 grants
 * 而卡片不变，前端就会在"数据变了、界面没变"这种假象上做判断。重算的规则
 * 与后端 `subject_capabilities` 保持一致——能力取成员交集、只算参与输出的成员。
 * ------------------------------------------------------------------ */

const MINE = 'u_001';

/**
 * 路径里的第 n 段。
 *
 * **不能用 `ctx.params[0]`**：`MockContext.params` 只装 query 参数（见
 * `kernel.ts`），正则捕获组不在里面。用 `params[0]` 会一直拿到 `undefined`，
 * 于是主体详情 / 发布 / 旧包映射全部变成 404——而那个 404 与真实的不存在
 * 长得一模一样，只能靠人眼在页面上发现"点进主体也打不开"。
 */
const segment = (path: string, index: number) => path.split('/')[index] ?? '';

interface MockSubject {
  id: string;
  name: string;
  subjectType: string;
  status: string;
  visibility: string;
  coverAssetId: string;
  requires: string[];
  requiredRoles: string[];
  attributes: Record<string, string>;
  currentVersion: number;
  members: SubjectMemberOut[];
}

/** 取个人库里现成的素材当成员，避免 mock 自己造假封面。 */
function pickAssets(count: number): ArchivedAsset[] {
  return db.assets
    .filter(asset => asset.ownerId === MINE && !asset.deletedAt)
    .slice(0, count);
}

function seeded(): MockSubject[] {
  const [character, scene, texture] = pickAssets(3);
  if (!character) return [];
  return [
    {
      id: 'sbj_character',
      name: '示例主体 · 人物替换',
      subjectType: 'character',
      status: 'published',
      visibility: 'personal',
      coverAssetId: character.id,
      requires: ['generate', 'download'],
      requiredRoles: ['front', 'scene'],
      attributes: {发型: '齐肩', 肤色: '自然'},
      currentVersion: 1,
      members: [
        {
          assetId: character.id,
          name: character.name,
          coverUrl: character.previewUrl ?? character.url,
          role: 'front',
          sortOrder: 0,
          grants: ['generate', 'download'],
          inOutput: true,
        },
        {
          assetId: scene?.id ?? character.id,
          name: scene?.name ?? '（缺）',
          coverUrl: scene ? (scene.previewUrl ?? scene.url) : '',
          role: 'scene',
          sortOrder: 1,
          grants: ['generate', 'download'],
          inOutput: true,
        },
      ],
    },
    {
      id: 'sbj_product',
      name: '示例主体 · 产品细节待补',
      subjectType: 'product',
      status: 'draft',
      visibility: 'personal',
      coverAssetId: texture?.id ?? character.id,
      requires: ['generate', 'download'],
      requiredRoles: ['front', 'detail'],
      attributes: {材质: '阳极氧化铝'},
      currentVersion: 0,
      // 只有 generate：能力和角色各缺一项，卡片上会显示"待补齐"
      members: [
        {
          assetId: texture?.id ?? character.id,
          name: texture?.name ?? '（缺）',
          coverUrl: texture ? (texture.previewUrl ?? texture.url) : '',
          role: 'front',
          sortOrder: 0,
          grants: ['generate'],
          inOutput: true,
        },
      ],
    },
  ];
}

const store: MockSubject[] = seeded();

/** 能力 = 参与输出且未删除的成员的 grants 交集；角色 = 成员角色的并集。 */
function evaluate(subject: MockSubject) {
  const live = subject.members.filter(member => {
    const asset = db.assets.find(item => item.id === member.assetId);
    return !member.missing && !!asset && !asset.deletedAt;
  });
  const broken = subject.members.filter(member => {
    const asset = db.assets.find(item => item.id === member.assetId);
    return member.missing || !asset || !!asset.deletedAt;
  });
  const output = live.filter(member => member.inOutput !== false);
  const capabilities = output.length
    ? (output[0]?.grants ?? []).filter(grant =>
        output.every(member => (member.grants ?? []).includes(grant)),
      )
    : [];
  const roles = new Set(subject.members.map(member => member.role ?? ''));
  const missingRoles = subject.requiredRoles.filter(role => !roles.has(role));
  const missingCapabilities = subject.requires.filter(cap => !capabilities.includes(cap));
  const memberGaps: Record<string, string[]> = {};
  for (const member of output) {
    const gaps = subject.requires.filter(cap => !(member.grants ?? []).includes(cap));
    if (gaps.length) memberGaps[member.assetId] = gaps;
  }
  return {broken, missingRoles, missingCapabilities, memberGaps};
}

function availabilityOf(subject: MockSubject) {
  if (!subject.members.length) return {state: 'incomplete', note: '还没有成员'};
  const {broken, missingRoles, missingCapabilities} = evaluate(subject);
  if (broken.length) return {state: 'broken', note: `${broken.length} 张成员图已被删除`};
  if (missingCapabilities.length) {
    return {state: 'incomplete', note: `成员缺少能力：${missingCapabilities.join('、')}`};
  }
  if (missingRoles.length) {
    return {state: 'incomplete', note: `缺少角色：${missingRoles.join('、')}`};
  }
  if (subject.status !== 'published') return {state: 'incomplete', note: '尚未发布'};
  return {state: 'ready', note: ''};
}

function toOut(subject: MockSubject): SubjectOut {
  const {state, note} = availabilityOf(subject);
  const cover = db.assets.find(asset => asset.id === subject.coverAssetId);
  return {
    id: subject.id,
    name: subject.name,
    subjectType: subject.subjectType,
    status: subject.status,
    visibility: subject.visibility,
    teamId: '',
    categoryId: '',
    coverAssetId: subject.coverAssetId,
    coverUrl: cover ? (cover.previewUrl ?? cover.url) : '',
    memberCount: subject.members.length,
    currentVersion: subject.currentVersion,
    availability: state,
    availabilityNote: note,
  };
}

function findSubject(id: string) {
  const subject = store.find(item => item.id === id);
  if (!subject) throw new HttpError(404, '主体不存在或已下架');
  return subject;
}

/**
 * 某个素材被哪些主体当成员。
 *
 * 导出来是给血缘用的（"谁在用它"里 `subject` 那一类）：主体数据只存在这个
 * 文件里，让 candidate.mock 自己再造一份会立刻漂移——改了成员而浮层仍显示
 * 旧主体，正是这条导出要避免的。
 */
export function subjectsUsingAsset(assetId: string): Array<{id: string; name: string}> {
  return store
    .filter(subject => subject.members.some(member => member.assetId === assetId))
    .map(subject => ({id: subject.id, name: subject.name}));
}

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/subjects$/,
    handler: ctx => {
      const page = Math.max(1, Number(ctx.params.page ?? 1) || 1);
      const pageSize = Math.max(1, Number(ctx.params.pageSize ?? 24) || 24);
      const type = String(ctx.params.subjectType ?? '');
      const list = store.filter(item => !type || item.subjectType === type);
      return {
        items: list.slice((page - 1) * pageSize, page * pageSize).map(toOut),
        total: list.length,
        page,
        pageSize,
      };
    },
  },
  {
    method: 'GET',
    // 必须排在 `/{id}` 之前：否则 `by-legacy-pack` 会被当成一个主体 id
    pattern: /^\/subjects\/by-legacy-pack\/([^/]+)$/,
    handler: ctx => {
      const packId = segment(ctx.path, 3);
      // mock 里只假装一个已知的旧 id，其余 404——这正是真实环境里的样子：
      // 只有被搬运过的旧包才有映射
      if (packId !== 'pkg_demo_character') throw new HttpError(404, '这个旧素材包没有对应的主体');
      const subject = findSubject('sbj_character');
      return detailOf(subject);
    },
  },
  {
    method: 'GET',
    pattern: /^\/subjects\/([^/]+)$/,
    handler: ctx => detailOf(findSubject(segment(ctx.path, 2))),
  },
  {
    method: 'POST',
    pattern: /^\/subjects\/([^/]+)\/publish$/,
    handler: ctx => {
      const subject = findSubject(segment(ctx.path, 2));
      const {state} = availabilityOf(subject);
      if (!subject.members.length) throw new HttpError(400, '至少要有一张成员图才能发布');
      if (state === 'broken') throw new HttpError(409, '有成员图已被删除，请先补齐');
      if (state === 'incomplete') {
        const {missingCapabilities, missingRoles} = evaluate(subject);
        if (missingCapabilities.length) {
          throw new HttpError(400, `成员缺少能力：${missingCapabilities.join('、')}`);
        }
        throw new HttpError(400, `缺少角色：${missingRoles.join('、')}`);
      }
      subject.currentVersion += 1;
      subject.status = 'published';
      return {
        subjectId: subject.id,
        version: subject.currentVersion,
        publishedAt: new Date().toISOString(),
      };
    },
  },
];

function detailOf(subject: MockSubject) {
  const {missingRoles, missingCapabilities, memberGaps} = evaluate(subject);
  return {
    ...toOut(subject),
    members: subject.members,
    attributes: subject.attributes,
    requires: subject.requires,
    requiredRoles: subject.requiredRoles,
    missingCapabilities,
    missingRoles,
    memberGaps,
  };
}
