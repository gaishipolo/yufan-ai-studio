import { HttpError, type MockRoute } from '../kernel';
import { categoryIdOf, categoryTree, INBOX_ID, tagCatalog, TEAMS, visibleInLibrary } from '../library-tree';

/* ------------------------------------------------------------------
 * 库边界：团队 / 分类树 / 标签词表 / 保存视图
 *
 * 这四条与 `/assets` 是同一个域（素材库）的四个侧面，所以放一个文件。
 * 单独拆开会让「切库时哪几个请求该一起发」变得难读——它们是同一次交互的一部分。
 * ------------------------------------------------------------------ */

/** 保存视图：mock 里只存内存，刷新即失。真后端的 `asset_view` 表按 owner 隔离。 */
const views: Array<{id: string; name: string; query: Record<string, unknown>}> = [];

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/teams$/,
    handler: () => ({items: TEAMS, canManage: true}),
  },
  {
    method: 'GET',
    pattern: /^\/asset-categories$/,
    handler: (ctx) => {
      const library = String(ctx.params.library ?? 'personal');
      // 团队库的可见性照抄后端：不是成员就 403，不是空列表。
      // mock 里假装"全都能看"会让前端的 403 分支从来没被走过。
      if (library.startsWith('team:')) {
        const teamId = library.slice('team:'.length);
        const team = TEAMS.find(item => item.id === teamId);
        if (!team) throw new HttpError(404, '团队不存在');
        if (!team.role) throw new HttpError(403, '无权访问该团队库');
      }
      // 计数与 `/assets` 用同一个 `categoryIdOf`，所以两边一定一致
      const counts = new Map<string, number>();
      for (const asset of visibleInLibrary(library)) {
        const id = categoryIdOf(asset);
        counts.set(id, (counts.get(id) ?? 0) + 1);
      }
      const items = categoryTree(library).map(item => ({
        ...item,
        count: item.id === INBOX_ID ? 0 : (counts.get(item.id) ?? 0),
      }));
      return {
        library,
        items,
        inboxCount: items.find(item => item.isInbox)?.count ?? 0,
      };
    },
  },
  {
    method: 'GET',
    pattern: /^\/asset-tags$/,
    handler: () => tagCatalog(),
  },
  {
    method: 'POST',
    pattern: /^\/asset-tags$/,
    status: 201,
    handler: (ctx) => {
      const body = ctx.body as {dimension?: string; name?: string};
      const name = String(body?.name ?? '').trim();
      if (!name) throw new HttpError(422, '标签名不能为空');
      // 词表外的新词：真后端会校验同维度重名，这里也校验——mock 不该比真后端宽松
      const exists = tagCatalog().groups?.some(group =>
        (group.items ?? []).some(tag => tag.name === name),
      );
      if (exists) throw new HttpError(409, '这个词已经存在');
      return {
        id: `new-${Date.now()}`,
        dimension: String(body?.dimension ?? 'style'),
        name,
        status: 'active',
        source: 'manual',
        useCount: 0,
      };
    },
  },
  {
    method: 'GET',
    pattern: /^\/asset-views$/,
    handler: () => ({items: views}),
  },
  {
    method: 'POST',
    pattern: /^\/asset-views$/,
    status: 201,
    handler: (ctx) => {
      const body = ctx.body as {name?: string; query?: Record<string, unknown>};
      const name = String(body?.name ?? '').trim();
      if (!name) throw new HttpError(422, '视图名不能为空');
      // 同名覆盖而不是报错：用户第二次点「保存视图」的意图通常是"更新它"
      const existing = views.find(view => view.name === name);
      if (existing) {
        existing.query = body?.query ?? {};
        return existing;
      }
      const view = {id: `v-${Date.now()}`, name, query: body?.query ?? {}};
      views.unshift(view);
      return view;
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/asset-views\/([^/]+)$/,
    handler: (ctx) => {
      // 捕获组不在 `ctx.params` 里（那里只有 query），从路径第 3 段取
      const id = ctx.path.split('/')[2] ?? '';
      const index = views.findIndex(view => view.id === id);
      if (index < 0) throw new HttpError(404, '视图不存在');
      views.splice(index, 1);
      return null;
    },
  },
];
