import { useMemo } from 'react';
import type {
  EffectivePermissionsOut,
  PermissionCatalogOut,
  PermissionOriginOut,
} from '@/shared/api/schema';

/** 一个权限码的来源层（`kind` 与后端 scope 常量同词：team / group / user / role）。 */
export type Origin = PermissionOriginOut;

export type RowStatus = 'effective' | 'denied' | 'gated';

export interface PermissionRow {
  code: string;
  name: string;
  description: string;
  status: RowStatus;
  origins: Origin[];
  /** 被多个页面声明（关掉其中一页它仍然有效）—— 动它之前要知道这一点 */
  shared: boolean;
}

export interface PermissionGroup {
  code: string;
  name: string;
  route: string;
  /** 这一页现在开着没有。关着的整组降权，组内的行不可操作 */
  open: boolean;
  /** 页面本身的来源（哪一层给的页面码） */
  origins: Origin[];
  rows: PermissionRow[];
}

export interface PermissionOverview {
  groups: PermissionGroup[];
  /** 没有页面管的码（`export:*` 这类后台能力）：不受页面开关影响 */
  background: PermissionRow[];
  /** 已对他关闭的码。**含当前不生效的**：角色撤了那条例外还在，必须可撤销 */
  exceptions: PermissionRow[];
  /** 目录里已经没有的码（模块下线后残留） */
  orphans: PermissionRow[];
  counts: { effective: number; denied: number; gated: number };
  allCodes: string[];
}

const PAGE_PREFIX = 'page:';

function isPageCode(code: string): boolean {
  return code.startsWith(PAGE_PREFIX);
}

/**
 * 把「有效权限」的出参摊成界面要的行。
 *
 * 三件事只在这里做一次，别在组件里再算：
 * 1. 每个码归到**一个**页面（共享码只出现一行，否则计数会翻倍）；
 * 2. 行的状态取 `denied > deniedOff > gatedOff > effective` 的优先级 ——
 *    后端刻意把「被本人关掉」和「页面没开」分成两个列表，这里不合并它们，
 *    只是映射成同一种展示态；
 * 3. 例外段用 `denied` 全量（含当前无人授予的），否则那条例外就永远看不见。
 */
export function buildOverview(
  data: EffectivePermissionsOut | undefined,
  catalog: PermissionCatalogOut | undefined,
): PermissionOverview {
  const effective = new Set(data?.effective ?? []);
  const denied = new Set(data?.denied ?? []);
  const deniedOff = new Set(data?.deniedOff ?? []);
  const gatedOff = new Set(data?.gatedOff ?? []);
  const pagesOn = new Set(data?.pages ?? []);

  const nameOf = new Map<string, { name: string; description: string }>();
  const pageItems: { code: string; name: string; route: string; requires: string[] }[] = [];
  for (const group of catalog?.groups ?? []) {
    for (const item of group.items ?? []) {
      nameOf.set(item.code, { name: item.name, description: item.description ?? '' });
      if (isPageCode(item.code)) {
        pageItems.push({
          code: item.code,
          name: item.name,
          route: item.route ?? '',
          requires: item.requires ?? [],
        });
      }
    }
  }

  const originsOf = new Map<string, Origin[]>();
  for (const source of data?.sources ?? []) {
    originsOf.set(source.code, source.layers ?? []);
  }

  /** 接口码 → 声明它的页面码。用于归组与「共享」判定。 */
  const owners = new Map<string, string[]>();
  for (const page of pageItems) {
    for (const code of page.requires) {
      owners.set(code, [...(owners.get(code) ?? []), page.code]);
    }
  }

  const statusOf = (code: string): RowStatus => {
    if (denied.has(code) || deniedOff.has(code)) return 'denied';
    if (gatedOff.has(code)) return 'gated';
    return 'effective';
  };

  const rowOf = (code: string): PermissionRow => {
    const meta = nameOf.get(code);
    return {
      code,
      name: meta?.name ?? code,
      description: meta?.description ?? '',
      status: statusOf(code),
      origins: originsOf.get(code) ?? [],
      shared: (owners.get(code)?.length ?? 0) > 1,
    };
  };

  /** 所有「授予过」的接口码（页面码不算行，它是分组本身）。 */
  const granted = new Set<string>();
  for (const code of [...effective, ...deniedOff, ...gatedOff]) {
    if (!isPageCode(code)) granted.add(code);
  }

  // 每个码只归到一个页面：按目录顺序取第一个声明它的页面。
  const assignment = new Map<string, string>();
  for (const page of pageItems) {
    for (const code of page.requires) {
      if (granted.has(code) && !assignment.has(code)) assignment.set(code, page.code);
    }
  }

  const groups: PermissionGroup[] = pageItems.map((page) => ({
    code: page.code,
    name: page.name,
    route: page.route,
    open: pagesOn.has(page.code),
    origins: originsOf.get(page.code) ?? [],
    rows: [...assignment.entries()]
      .filter(([, pageCode]) => pageCode === page.code)
      .map(([code]) => rowOf(code)),
  }));

  const background: PermissionRow[] = [];
  const orphans: PermissionRow[] = [];
  for (const code of [...granted].sort()) {
    if (assignment.has(code)) continue;
    if (nameOf.has(code)) background.push(rowOf(code));
    else orphans.push(rowOf(code));
  }

  const exceptions = [...denied].sort().map(rowOf);

  return {
    // 空组不占版面（一页 0 个接口时它只是一个标题）；
    // **开着的页排在前面**：「他能做什么」是这一页要回答的第一个问题，
    // 而「页面没开所以不生效」是补充说明。按码排序会让红条占据首屏。
    groups: groups
      .filter((group) => group.rows.length > 0)
      .sort((a, b) => Number(b.open) - Number(a.open)),
    background,
    exceptions,
    orphans,
    counts: {
      effective: [...effective].filter((code) => !isPageCode(code)).length,
      denied: denied.size,
      gated: gatedOff.size,
    },
    allCodes: [...granted].sort(),
  };
}

/** 把 hook 与 `buildOverview` 拼起来；组件只拿结果。 */
export function useOverview(
  data: EffectivePermissionsOut | undefined,
  catalog: PermissionCatalogOut | undefined,
): PermissionOverview {
  return useMemo(() => buildOverview(data, catalog), [data, catalog]);
}
