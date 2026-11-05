import { useCallback, useEffect, useState } from 'react';

/**
 * 侧边栏分组的折叠状态。
 *
 * 设计取舍：
 * - 折叠是按「分组标题」记录的字符串数组，因此模块作者无需声明 id，
 *   重命名分组 = 该项自动恢复展开（默认值即展开，不会出现「进不去」）。
 * - 状态存 localStorage，跨会话记住；存储不可用（隐私模式 / 配额满）时静默降级为内存态。
 * - 默认展开：读不到值就是 `[]`，不写死任何分组名。
 */

const STORAGE_KEY = 'sf_sidebar.collapsedGroups';

function readCollapsedGroups(): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is string => typeof value === 'string');
  } catch {
    return [];
  }
}

export interface CollapsedGroupsController {
  isCollapsed: (title: string) => boolean;
  toggle: (title: string) => void;
  /** 幂等展开：已展开时不触发重渲染 */
  expand: (title: string) => void;
}

export function useCollapsedGroups(): CollapsedGroupsController {
  const [collapsed, setCollapsed] = useState<string[]>(readCollapsedGroups);

  useEffect(() => {
    try {
      if (collapsed.length === 0) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, JSON.stringify(collapsed));
    } catch {
      // 存不下就算了，折叠只是视觉偏好，不影响功能
    }
  }, [collapsed]);

  const isCollapsed = useCallback((title: string) => collapsed.includes(title), [collapsed]);

  const toggle = useCallback((title: string) => {
    setCollapsed((prev) =>
      prev.includes(title) ? prev.filter((item) => item !== title) : [...prev, title],
    );
  }, []);

  const expand = useCallback((title: string) => {
    setCollapsed((prev) => (prev.includes(title) ? prev.filter((item) => item !== title) : prev));
  }, []);

  return { isCollapsed, toggle, expand };
}
