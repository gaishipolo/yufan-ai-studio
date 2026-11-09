import type {
  ArchivedAsset,
  AssetRefOut,
  AssetUsage,
  CandidateGroup,
  LineageNode,
  RecipeOut,
  TagOut,
} from '@/shared/api/schema';
import { db, type MockRecipe } from '../db';
import { HttpError, type MockRoute } from '../kernel';
import { tagCatalog } from '../library-tree';
import { subjectsUsingAsset } from './subject.mock';

/* ------------------------------------------------------------------
 * 候选 / 血缘 / 衍生 / 单条标签（M3）
 *
 * 照抄后端的四条规矩，mock 不比真后端宽松：
 *   1. 候选只从 `/assets/candidates` 出，**不在 `db.assets` 里**（不进列表、不计配额）；
 *   2. 采纳幂等（已采纳再点返回现状），`discarded` / `expired` / 已超期一律 409；
 *   3. 批量采纳**先全部校验再全部写**，半成功是最坏的形态；
 *   4. `derive` 只回填输入、不建任务、不扣费。
 *
 * 注册顺序在这里是**语义的一部分**：`/assets/batch/adopt` 必须排在
 * `/assets/{id}/adopt` 之前，否则 `batch` 会被当成一个素材 id。
 * 同理 `/assets/candidates` 也要排在详情之前——那条已经在 catalog.mock 里
 * 用负向断言排除了，不靠文件名字母序。
 * ------------------------------------------------------------------ */

const MINE = 'u_001';

/** 提示词摘要长度：与后端 `summarize()` 同一个口径（横幅上一行就够）。 */
const PROMPT_SUMMARY = 60;

const summarize = (prompt: string) => {
  const text = prompt.split(/\s+/).filter(Boolean).join(' ');
  if (!text) return '';
  return text.length <= PROMPT_SUMMARY ? text : `${text.slice(0, PROMPT_SUMMARY)}…`;
};

const recipeOf = (id: string | undefined) => db.recipes.find((item) => item.id === id);

/**
 * 从路径里取素材 id。
 *
 * **不能用 `ctx.params[0]`**：`MockContext.params` 装的是 query 参数
 * （见 `kernel.ts`），正则的捕获组不在里面。用 `params[0]` 会永远拿到
 * `undefined`，于是每个请求都变成"素材不存在"——而 mock 的 404 与真实 404
 * 长得一模一样，很难看出是这个原因。
 */
const assetIdOf = (path: string) => path.split('/')[2] ?? '';

/**
 * 一个 id 可能是候选，也可能已经采纳成素材。两处都找，是因为
 * 「已采纳再点」要能返回现状，而采纳之后这一行已经搬到 `db.assets` 里了。
 *
 * **不校验归属**：血缘与标签读对"可读"素材开放（公共原片也能看它是怎么来的，
 * 后端走的是 `service.visible`）。写路径自己再加 `owned()`——两件事分开，
 * 才不会出现"看别人的图被 404"或"改别人的图没被拦"。
 */
function locate(id: string): ArchivedAsset {
  const candidate = db.candidates.find((item) => item.asset.id === id)?.asset;
  const asset =
    candidate ?? db.closed.find((item) => item.id === id) ?? db.assets.find((item) => item.id === id);
  if (!asset) throw new HttpError(404, '素材不存在');
  return asset;
}

/** 写路径的前置：只有本人能采纳/弃用/打标签。别人的 id 与不存在同一个错误。 */
function owned(id: string): ArchivedAsset {
  const asset = locate(id);
  if (asset.ownerId !== MINE) throw new HttpError(404, '素材不存在');
  return asset;
}

/** `_open()` 的等价物：只返回还开着的候选，其余按后端的分支给错。 */
function openCandidate(id: string): ArchivedAsset {
  const asset = owned(id);
  if (asset.origin !== 'generate') throw new HttpError(400, '只有 AI 生成的产物是候选');
  if (asset.stage === 'adopted') return asset;
  if (asset.stage === 'discarded' || asset.stage === 'expired') {
    throw new HttpError(409, '这条候选已关闭，不能再次确认');
  }
  if (asset.complianceStatus === 'rejected') {
    throw new HttpError(409, '这条候选合规未通过，不能采纳');
  }
  return asset;
}

/** 采纳：从候选区搬进素材库，**不搬字节**，只翻状态。 */
function adoptOne(asset: ArchivedAsset, categoryId: string, tagIds: string[]) {
  if (asset.stage === 'adopted') return asset;
  const index = db.candidates.findIndex((item) => item.asset.id === asset.id);
  if (index >= 0) db.candidates.splice(index, 1);
  asset.stage = 'adopted';
  asset.adoptedAt = new Date().toISOString();
  asset.categoryId = categoryId || 'cat-inbox';
  if (tagIds.length) db.assetTags[asset.id] = tagIds;
  db.assets.unshift(asset);
  return asset;
}

const recipeOut = (recipe: MockRecipe): RecipeOut => ({
  id: recipe.id,
  model: recipe.model,
  modelVersion: '',
  provider: recipe.provider,
  prompt: recipe.prompt,
  negativePrompt: '',
  promptLanguage: 'zh',
  params: recipe.params,
  seed: recipe.seed,
  seedRequested: '',
  taskId: '',
  templateId: '',
  templateVersion: 0,
  subjectVersion: 0,
  contentLabel: {},
  parentRecipeId: recipe.parentId,
  referenceAssetIds: recipe.referenceIds,
  costUnits: 0,
  createdBy: MINE,
  createdAt: recipe.createdAt,
});

/** 一条配方产出的素材（候选也算：它是"产出"，只是还没转正）。 */
function producedBy(recipeId: string): ArchivedAsset | undefined {
  return (
    db.assets.find((item) => item.recipeId === recipeId) ??
    db.candidates.find((item) => item.recipeId === recipeId)?.asset
  );
}

const lineageNode = (recipe: MockRecipe): LineageNode => {
  const asset = producedBy(recipe.id);
  return {
    recipe: recipeOut(recipe),
    assetId: asset?.id ?? '',
    name: asset?.name ?? '',
    previewUrl: asset?.previewUrl ?? null,
  };
};

/** 标签 id → 词条。`tagCatalog()` 是词表的唯一来源，这里只建一次索引。 */
const tagIndex = (): Map<string, TagOut> => {
  const map = new Map<string, TagOut>();
  for (const group of tagCatalog().groups ?? []) {
    for (const tag of group.items ?? []) map.set(tag.id, tag);
  }
  return map;
};

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/assets\/candidates$/,
    handler: () => {
      // 分组用插入序：`buildCandidates()` 里最新的批次排在前面
      const grouped = new Map<string, ArchivedAsset[]>();
      for (const item of db.candidates) {
        grouped.set(item.recipeId, [...(grouped.get(item.recipeId) ?? []), item.asset]);
      }
      const groups: CandidateGroup[] = [];
      for (const [recipeId, items] of grouped) {
        const recipe = recipeOf(recipeId);
        const sample = items[0];
        const size =
          sample.width && sample.height ? `${sample.width}×${sample.height}` : sample.type;
        groups.push({
          recipeId,
          summary: `${recipe?.model || sample.type} · ${items.length} 张 · ${size}`,
          promptSummary: recipe ? summarize(recipe.prompt) : '',
          createdAt: sample.createdAt,
          // 组内最早到期：一张到期整组就该消失
          expiresAt: db.candidates
            .filter((item) => item.recipeId === recipeId)
            .map((item) => item.expiresAt)
            .sort()[0],
          items,
        });
      }
      return {groups, total: db.candidates.length};
    },
  },
  {
    method: 'POST',
    // 必须排在 `/{id}/adopt` 之前，否则 `batch` 会被当成素材 id
    pattern: /^\/assets\/batch\/adopt$/,
    handler: (ctx) => {
      const body = ctx.body as {assetIds?: string[]; categoryId?: string; tagIds?: string[]};
      const ids = [...new Set(body?.assetIds ?? [])];
      if (!ids.length) throw new HttpError(422, '至少选一张');
      // 先全部校验：任一条不可采纳就整批拒绝，一条都不写
      const rows = ids.map((id) => openCandidate(id));
      const alreadyRows = rows.filter((row) => row.stage === 'adopted');
      const fresh = rows.filter((row) => row.stage !== 'adopted');
      const adopted = fresh.map((row) =>
        adoptOne(row, String(body?.categoryId ?? ''), body?.tagIds ?? []),
      );
      // `adoptOne` 会原地把 stage 改成 adopted，所以 alreadyRows 必须在写之前取
      return {items: [...adopted, ...alreadyRows], already: alreadyRows.length};
    },
  },
  {
    method: 'POST',
    pattern: /^\/assets\/([^/]+)\/adopt$/,
    handler: (ctx) => {
      const body = ctx.body as {categoryId?: string; tagIds?: string[]};
      const row = openCandidate(assetIdOf(ctx.path));
      return adoptOne(row, String(body?.categoryId ?? ''), body?.tagIds ?? []);
    },
  },
  {
    method: 'POST',
    pattern: /^\/assets\/([^/]+)\/discard$/,
    handler: (ctx) => {
      const row = openCandidate(assetIdOf(ctx.path));
      // 弃用是幂等的：已经弃用再点一次仍然是"弃用"，不是"已关闭"
      if (row.stage === 'discarded') return row;
      const index = db.candidates.findIndex((item) => item.asset.id === row.id);
      if (index >= 0) db.candidates.splice(index, 1);
      row.stage = 'discarded';
      // 字节不删、配方不删，行也不删：衍生链可能从它经过
      if (!db.closed.includes(row)) db.closed.push(row);
      return row;
    },
  },
  {
    method: 'GET',
    pattern: /^\/assets\/([^/]+)\/lineage$/,
    handler: (ctx) => {
      const asset = locate(assetIdOf(ctx.path));
      const recipe = recipeOf(asset.recipeId);
      if (!recipe) {
        // 上传件：血缘为空是**事实**，不是错误。前端据此隐藏这一段
        return {
          assetId: asset.id,
          recipe: null,
          references: [],
          parents: [],
          children: [],
          usedBy: usageOf(asset),
        };
      }
      const references: AssetRefOut[] = recipe.referenceIds.map((id, index) => {
        const ref = db.assets.find((item) => item.id === id);
        return {
          assetId: id,
          role: 'reference',
          sortOrder: index,
          name: ref?.name ?? '（已删除）',
          previewUrl: ref?.previewUrl ?? null,
        };
      });
      const parents: LineageNode[] = [];
      let cursor = recipe.parentId;
      // 上限 5 层：真实使用里很少超过三层，而坏数据造成的环会让"一直向上"死循环
      while (cursor && parents.length < 5) {
        const parent = recipeOf(cursor);
        if (!parent || parent.id === recipe.id) break;
        parents.push(lineageNode(parent));
        cursor = parent.parentId;
      }
      return {
        assetId: asset.id,
        recipe: recipeOut(recipe),
        references,
        parents,
        children: db.recipes
          .filter((item) => item.parentId === recipe.id)
          .map((item) => lineageNode(item)),
        usedBy: usageOf(asset),
      };
    },
  },
  {
    method: 'POST',
    pattern: /^\/assets\/([^/]+)\/derive$/,
    handler: (ctx) => {
      const asset = locate(assetIdOf(ctx.path));
      const mode = String(ctx.body.mode ?? '');
      if (mode === 'prompt_extract') {
        const recipe = recipeOf(asset.recipeId);
        if (!recipe) throw new HttpError(400, '这条素材没有生成配方，提取不了提示词');
        return {mode, assetId: asset.id, prompt: recipe.prompt, sourceRecipeId: recipe.id};
      }
      if (mode !== 'image_ref' && mode !== 'video_first_frame') {
        throw new HttpError(422, '不认识的衍生方式');
      }
      if (asset.type !== 'image') {
        // 视频走"先截一帧"的路径：隐式抽帧会产生新素材，那是另一条写路径
        throw new HttpError(400, '首帧与图生图参考需要一个图片位的素材；视频请先截一帧再上传');
      }
      return {
        mode,
        assetId: asset.id,
        referenceAssetIds: [asset.id],
        referenceRole: mode === 'video_first_frame' ? 'first_frame' : 'reference',
        sourceRecipeId: asset.recipeId || null,
      };
    },
  },
  {
    method: 'GET',
    pattern: /^\/assets\/([^/]+)\/tags$/,
    handler: (ctx) => {
      const asset = locate(assetIdOf(ctx.path));
      const index = tagIndex();
      return {
        assetId: asset.id,
        items: (db.assetTags[asset.id] ?? [])
          .map((id) => index.get(id))
          .filter((tag): tag is TagOut => !!tag),
      };
    },
  },
  {
    method: 'PUT',
    pattern: /^\/assets\/([^/]+)\/tags$/,
    handler: (ctx) => {
      // 写入要求本人：与 GET 不同，读对"可读"素材开放，改只对自己的开放
      const asset = owned(assetIdOf(ctx.path));
      const index = tagIndex();
      const requested = (ctx.body.tagIds as string[] | undefined) ?? [];
      const unknown = requested.filter((id) => !index.has(id));
      // 词表外的 id 一律拒绝：静默丢弃会让"保存成功但少了一个标签"
      if (unknown.length) throw new HttpError(422, `未知标签：${unknown.join(', ')}`);
      // 空数组是「清空」，不是「不改」
      db.assetTags[asset.id] = [...new Set(requested)];
      return {
        assetId: asset.id,
        items: db.assetTags[asset.id].map((id) => index.get(id)).filter((tag): tag is TagOut => !!tag),
      };
    },
  },
];

/**
 * 谁在用它。两类消费者分开标：`asset` 是别的配方引用了它（生成血缘），
 * `subject` 是某个主体把它当成员（业务装配）。合并成一种会让
 * "这张图能不能删"的答案少一半——两者各自都会阻止删除。
 */
function usageOf(asset: ArchivedAsset): AssetUsage[] {
  const usages: AssetUsage[] = [];
  const referencing = db.recipes.filter((recipe) => recipe.referenceIds.includes(asset.id));
  for (const recipe of referencing) {
    const child = producedBy(recipe.id);
    if (child && child.id !== asset.id) {
      usages.push({kind: 'asset', id: child.id, name: child.name});
    }
  }
  usages.push(
    ...subjectsUsingAsset(asset.id).map((subject) => ({
      kind: 'subject' as const,
      id: subject.id,
      name: subject.name,
    })),
  );
  return usages;
}
