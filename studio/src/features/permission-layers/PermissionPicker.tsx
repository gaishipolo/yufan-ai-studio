import { useMemo, useState } from 'react';
import { Layers3, Loader2, Plug, Search } from 'lucide-react';
import { Chip } from '@/shared/ui';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import { cn } from '@/shared/lib/cn';
import { PermissionSummaryBar } from './PermissionSummaryBar';

export interface PermissionPickerProps {
  selected: string[];
  onChange: (codes: string[]) => void;
  /**
   * 别的层已经授予的权限：`权限码 → 来源标签`（如 `{'task:read': ['角色']}`）。
   *
   * 为什么要有它：这一层是「个人授权」，多数人的权限其实来自角色/团队 ——
   * 只显示本层，打开弹窗就是一片空白，管理员会以为「这个人什么权限都没有」。
   *
   * 呈现方式：**勾上但灰色不可改**。勾选框的状态要回答「这个人现在能不能用
   * 这项能力」——答案是能（别的层给了），所以得勾上；而它不属于这一层，
   * 所以不能点。这里的灰色不是「禁用」，是「已生效但不由这里决定」。
   */
  inherited?: Record<string, string[]>;
  /**
   * 这一层不允许授予的码。团队基础权限禁 `identity:*` 与 `page:*`：
   * 后端同样会拒绝，前端提前禁用是为了不让人白点一次「保存」才知道。
   */
  forbidden?: (code: string) => boolean;
  /**
   * 被禁用的原因。
   *
   * **不再单独摆一行警告框**：每个禁用项的 `title` 里已经写着同一句，
   * 一条警告 + N 个禁用项 = 同一句话说 N+1 次。现在只留 `title` 那一处。
   */
  forbiddenHint?: string;
}

/**
 * 权限勾选器：**页面在上、接口在下**，两层关系画在同一屏里。
 *
 * 为什么不是一列平铺：权限点分成两层之后（页面决定「看得见哪一页」，
 * 接口决定「能做什么」），平铺的清单会让人看不出
 * 「关掉任务中心这一页，任务相关的接口就一起失效」这层因果 ——
 * 而这正是这个功能最容易配错的地方。
 *
 * 界面承担三件事：
 * 1. 页面卡片直接列出它关联的接口（哪些接口会被这一页关掉）；
 * 2. 每个接口标出它被哪些页面管，以及这些页面**当前是否开着** ——
 *    「勾了也没用」必须在勾之前就看得见；
 * 3. 摘要条分开报「页面 N 个 / 接口 N 个」，免得把两种东西当成一种。
 *
 * 勾页面**不会**自动勾接口：页面是可见性，接口是能力，
 * 自动勾会把「只想让人看得见这一页、不让他动」这种诉求抹掉。
 */
export function PermissionPicker({
  selected,
  onChange,
  inherited,
  forbidden,
  forbiddenHint,
}: PermissionPickerProps) {
  const catalogQuery = usePermissionCatalog();

  const { pages, apiGroups, nameOf, pagesByApi } = useMemo(() => {
    const groups = catalogQuery.data?.groups ?? [];
    const pageItems: typeof groups[number]['items'] = [];
    const rest: typeof groups = [];
    const names = new Map<string, string>();
    for (const group of groups) {
      const isPageGroup = group.items.some((item) => item.code.startsWith('page:'));
      for (const item of group.items) {
        names.set(item.code, item.name);
        if (item.code.startsWith('page:')) pageItems.push(item);
      }
      if (!isPageGroup) rest.push(group);
    }
    // 反向索引：接口码 → 声明它的页面码。页面项的 `requires` 是后端给的
    // （`GET /identity/permissions` 与生成物同源），不在前端猜。
    const owners = new Map<string, string[]>();
    for (const page of pageItems) {
      for (const code of page.requires ?? []) {
        owners.set(code, [...(owners.get(code) ?? []), page.code]);
      }
    }
    return {
      pages: pageItems,
      apiGroups: rest,
      nameOf: names,
      pagesByApi: owners,
    };
  }, [catalogQuery.data]);

  /**
   * 「已勾上但不由这一层决定」的码。
   *
   * 判定用「不在本层 but 在别层」：如果一个码**同时**在本层和别层，
   * 它就是本层可以撤的（撤掉本层那份，别的层那份还在），
   * 那时它不该被锁死 —— 锁死反而让人以为「这个没法撤」。
   */
  const isLocked = (code: string) =>
    Boolean(inherited?.[code]) && !selected.includes(code);

  const toggle = (code: string) => {
    if (forbidden?.(code)) return;
    // 锁定项：勾选框显示为「已勾上」，但点它不该有任何反应。
    if (isLocked(code)) return;
    onChange(selected.includes(code) ? selected.filter((item) => item !== code) : [...selected, code]);
  };

  // 右栏的搜索词。权限点有几十个，靠滚列表找某一个太费劲。
  const [keyword, setKeyword] = useState('');

  const toggleMany = (codes: string[], on: boolean) => {
    const next = new Set(selected);
    for (const code of codes) {
      if (forbidden?.(code)) continue;
      // 只跳过「锁定」的码：来自别的层、且本层没有的。
      // 如果它同时在本层（可撤），批量操作仍要能撤掉它本层那份。
      if (isLocked(code)) continue;
      if (on) next.add(code);
      else next.delete(code);
    }
    onChange([...next]);
  };

  // 一页都不可授（团队层）时，整栏不显示：全是禁用项的那一列没有信息量。
  const showPages =
    pages.length > 0 && !pages.every((page) => forbidden?.(page.code));

  const selectedPages = selected.filter((code) => code.startsWith('page:')).length;
  const selectedApis = selected.length - selectedPages;
  // 来自别的层、勾上但不可改的码。它们**不在**本层的保存集合里，
  // 但界面上是勾着的 —— 摘要必须把这两者分开，否则计数对不上眼睛看到的。
  const lockedCodes = Object.keys(inherited ?? {}).filter((code) => !selected.includes(code));

  if (catalogQuery.isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-xs text-ink-mute">
        <Loader2 size={14} className="animate-spin" /> 正在加载权限点…
      </div>
    );
  }

  if (catalogQuery.isError) {
    return (
      <p className="py-8 text-center text-xs text-danger">
        权限点加载失败：{catalogQuery.error.message}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <PermissionSummaryBar
        count={selected.length}
        unit="个权限点"
        breakdown={[
          { label: '页面', count: selectedPages },
          { label: '接口', count: selectedApis },
          ...(lockedCodes.length > 0
            ? [{ label: '其他层已授', count: lockedCodes.length }]
            : []),
        ]}
        onClear={() => onChange([])}
      />

      {/* 页面在左、接口在右：两层的因果关系本来就是「左右」的 ——
          左边选页面，右边立刻看得见它管着哪些接口。上下排的话，
          视线要来回跑，层级关系反而被拉断了。

          团队层不显示左栏：那里的页面码全部禁用（团队层没有 deny，
          页面放进来就永远关不掉），一整列灰卡片只会让人以为能配。
          页面一律不可授时，右栏独占整行。 */}
      <div
        className={cn(
          'grid gap-3',
          showPages && 'md:grid-cols-[minmax(260px,0.85fr)_minmax(0,1.65fr)]',
        )}
      >
      {/* ---------------- 左栏：页面 ---------------- */}
      {showPages ? (
        <section className="flex min-w-0 flex-col rounded-lg border border-line-soft">
          <header className="flex items-center gap-2 border-b border-line-soft bg-panel-soft px-2.5 py-1.5">
            <Layers3 size={13} className="text-primary" />
            <h3 className="text-xs font-semibold text-ink-soft">页面权限 · 决定能看见哪一页</h3>
            <span className="text-meta tabular-nums text-ink-mute">{pages.length}</span>
            <button
              type="button"
              className="ml-auto text-meta text-primary hover:underline disabled:opacity-50"
              disabled={pages.every((page) => forbidden?.(page.code))}
              onClick={() =>
                toggleMany(
                  pages.map((page) => page.code),
                  // 「已全部勾上」的判定要把「别的层已授」算进去 ——
                  // 它们在界面上就是勾着的，不算进去的话按钮永远显示「全选页面」。
                  !pages.every(
                    (page) => selected.includes(page.code) || isLocked(page.code),
                  ),
                )
              }
            >
              {pages.every(
                (page) => selected.includes(page.code) || isLocked(page.code),
              )
                ? '取消全部页面'
                : '全选页面'}
            </button>
          </header>
          <p className="px-2.5 pt-1.5 text-meta text-ink-mute">
            关掉一个页面，与它关联的接口就一并失效。
          </p>
          <ul className="grid max-h-[56vh] gap-1 overflow-auto p-2.5 md:grid-cols-1">
            {pages.map((page) => {
              const blocked = Boolean(forbidden?.(page.code));
              // 来自别的层：勾上、灰掉、点不动。
              const locked = isLocked(page.code);
              const checked = selected.includes(page.code) || locked;
              const requires = page.requires ?? [];
              // 只列前 3 个接口名，其余折成「等 N 个」：
              // 资产库挂着 14 个接口，全列出来就是一堵墙，卡片高低也不齐。
              // 全部名字放在 title 里，鼠标悬停可见。
              const shown = requires.slice(0, 3).map((code) => nameOf.get(code) ?? code);
              const hidden = requires.length - shown.length;
              return (
                <li
                  key={page.code}
                  className={cn(
                    'rounded-md border px-2 py-1.5',
                    checked && !locked && 'border-primary-soft bg-primary-soft/40',
                    locked && 'border-line-soft bg-panel-soft',
                    !checked && !locked && 'border-line-soft',
                    blocked && 'opacity-45',
                  )}
                >
                  <label
                    title={
                      blocked
                        ? forbiddenHint
                        : locked
                          ? `来自${inherited?.[page.code]?.join('、')}，不在这一层授予`
                          : undefined
                    }
                    className={cn(
                      'flex items-start gap-2',
                      blocked || locked ? 'cursor-not-allowed' : 'cursor-pointer',
                    )}
                  >
                    <input
                      type="checkbox"
                      /* disabled 的勾选框在 accent-color 下仍然是蓝的，
                         不加这层灰度就看不出「不可改」 */
                      className="mt-[3px] h-3 w-3 shrink-0 accent-primary disabled:opacity-40"
                      checked={checked}
                      disabled={blocked || locked}
                      onChange={() => toggle(page.code)}
                      /* aria-label：页面名与接口名会重名（「任务中心」既是页面名
                         又出现在接口的归属提示里），只靠可见文字取元素会撞车 */
                      aria-label={`${page.name} 页面权限`}
                    />
                    {/* 名称独占一行。曾经把「名称 + 已有来源 + 权限码 + 路由」挤在
                        一行里，结果「模板封装」「用户与权限」被 truncate 截成
                        「模…」「用…」——权限码和路由本来就是次要信息，
                        让位给名称才是对的。 */}
                    <span
                      className={cn(
                        'min-w-0 flex-1 text-xs font-medium',
                        locked ? 'text-ink-mute' : 'text-ink',
                      )}
                    >
                      {page.name}
                      {locked ? (
                        <span
                          className="ml-1.5 text-meta font-normal text-ink-mute"
                          title="别的层已经授予这一页；本层取消勾选不会收回它"
                        >
                          来自 {inherited?.[page.code]?.join('、')}
                        </span>
                      ) : null}
                    </span>
                  </label>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 pl-5">
                    <Chip>{page.code}</Chip>
                    {page.route ? (
                      <span className="text-meta text-ink-mute">{page.route}</span>
                    ) : null}
                  </div>
                  {requires.length > 0 ? (
                    <p
                      className="mt-0.5 pl-5 text-meta text-ink-mute"
                      title={requires.map((code) => nameOf.get(code) ?? code).join('、')}
                    >
                      关联 {requires.length} 个接口：{shown.join('、')}
                      {hidden > 0 ? ` 等 ${requires.length} 个` : ''}
                    </p>
                  ) : (
                    <p className="mt-0.5 pl-5 text-meta text-ink-mute">无关联接口</p>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {/* ---------------- 右栏：接口 ---------------- */}
      <section className="flex min-w-0 flex-col rounded-lg border border-line-soft">
        <header className="flex flex-wrap items-center gap-2 border-b border-line-soft bg-panel-soft px-2.5 py-1.5">
          <Plug size={13} className="text-primary" />
          <h3 className="text-xs font-semibold text-ink-soft">接口权限 · 决定能做什么</h3>
          <div className="ml-auto flex items-center gap-1.5">
            <Search size={12} className="text-ink-mute" />
            <input
              type="search"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              placeholder="搜接口名或权限码"
              aria-label="搜索接口权限"
              className="w-40 rounded border border-line-soft bg-panel px-1.5 py-0.5 text-meta text-ink outline-none placeholder:text-ink-mute focus:border-primary-soft"
            />
          </div>
        </header>
        <div className="max-h-[56vh] space-y-3 overflow-auto p-2.5">
          {apiGroups.map((group) => {
            const items = group.items.filter((item) => {
              if (!keyword.trim()) return true;
              const kw = keyword.trim().toLowerCase();
              return (
                item.name.toLowerCase().includes(kw) || item.code.toLowerCase().includes(kw)
              );
            });
            if (items.length === 0) return null;
            const codes = items.map((item) => item.code);
            const selectable = codes.filter((code) => !forbidden?.(code));
            // 「本组是否已全勾」要把锁定项算进去 —— 它们在界面上就是勾着的，
            // 不算进去的话按钮永远显示「全选本组」。
            const allChecked =
              selectable.length > 0 &&
              selectable.every(
                (code) => selected.includes(code) || isLocked(code),
              );
            return (
              <div key={group.group}>
                <div className="flex items-center gap-2 pb-1">
                  <h4 className="text-xs font-semibold text-ink-soft">{group.group}</h4>
                  <span className="text-meta tabular-nums text-ink-mute">{codes.length}</span>
                  <button
                    type="button"
                    className="ml-auto text-meta text-primary hover:underline disabled:opacity-50"
                    disabled={selectable.length === 0}
                    onClick={() => toggleMany(selectable, !allChecked)}
                  >
                    {allChecked ? '取消本组' : '全选本组'}
                  </button>
                </div>
                <ul className="grid gap-1 md:grid-cols-2">
                  {items.map((item) => {
                    const locked = isLocked(item.code);
                    const checked = selected.includes(item.code) || locked;
                    const blocked = Boolean(forbidden?.(item.code));
                    const owners = pagesByApi.get(item.code) ?? [];
                    const openOwners = owners.filter((code) => selected.includes(code));
                    // 有页面管着、但这些页面当前一个都没开 → 勾了也不生效。
                    // 必须在勾之前就看见，否则会得到「权限明明勾了却 403」。
                    const gated = owners.length > 0 && openOwners.length === 0;
                    return (
                      <li
                        key={item.code}
                        className={cn(
                          'flex items-start gap-2 rounded-md px-2 py-1.5',
                          checked && !locked && !blocked && 'bg-primary-soft/40',
                          locked && 'bg-panel-soft',
                          blocked && 'opacity-45',
                        )}
                      >
                        <label
                          title={
                            blocked
                              ? forbiddenHint
                              : locked
                                ? `来自${inherited?.[item.code]?.join('、')}，不在这一层授予`
                                : undefined
                          }
                          className={cn(
                            'flex min-w-0 flex-1 items-start gap-2',
                            blocked || locked ? 'cursor-not-allowed' : 'cursor-pointer',
                          )}
                        >
                          <input
                            type="checkbox"
                            /* 同上：锁定项的勾选框要真的灰掉 */
                            className="mt-[3px] h-3 w-3 shrink-0 accent-primary disabled:opacity-40"
                            checked={checked}
                            disabled={blocked || locked}
                            onChange={() => toggle(item.code)}
                            aria-label={`${item.name} 接口权限`}
                          />
                          <span className="min-w-0">
                            <span className="flex flex-wrap items-center gap-1.5">
                              <span
                                className={cn(
                                  'text-xs',
                                  locked ? 'text-ink-mute' : 'text-ink',
                                )}
                              >
                                {item.name}
                              </span>
                              {locked ? (
                                <span
                                  className="text-meta text-ink-mute"
                                  title="别的层已经授予它；本层取消勾选不会收回它"
                                >
                                  来自 {inherited?.[item.code]?.join('、')}
                                </span>
                              ) : null}
                              <Chip>{item.code}</Chip>
                            </span>
                            {item.description ? (
                              /* 两行封顶：有的说明写了三行，整列行高就参差不齐，
                                 扫读时找不到下一项在哪。完整说明留在 title 里。 */
                              <span
                                className="mt-0.5 block text-meta leading-[1.5] text-ink-mute line-clamp-2"
                                title={item.description}
                              >
                                {item.description}
                              </span>
                            ) : null}
                            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-meta">
                              {locked ? (
                                <span className="text-ink-mute">
                                  由 {inherited?.[item.code]?.join('、')} 授予，这一层改不了
                                </span>
                              ) : owners.length === 0 ? (
                                <span className="text-ink-mute">不受页面开关影响</span>
                              ) : (
                                <>
                                  <span className="text-ink-mute">
                                    归{' '}
                                    {owners
                                      .map((code) => nameOf.get(code) ?? code)
                                      .join('、')}{' '}
                                    管
                                  </span>
                                  {/* 「已生效」是常态，不该用主色刷屏 ——
                                      真正需要看见的是「当前未生效」那个例外。
                                      所以常态走中性色，例外才用危险色。 */}
                                  <span className={gated ? 'text-danger' : 'text-ink-mute'}>
                                    {gated ? '· 当前未生效' : '· 已生效'}
                                  </span>
                                </>
                              )}
                            </span>
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
          {keyword.trim() &&
          apiGroups.every(
            (group) =>
              group.items.filter(
                (item) =>
                  item.name.toLowerCase().includes(keyword.trim().toLowerCase()) ||
                  item.code.toLowerCase().includes(keyword.trim().toLowerCase()),
              ).length === 0,
          ) ? (
            <p className="py-6 text-center text-xs text-ink-mute">
              没有匹配的接口权限
            </p>
          ) : null}
        </div>
      </section>
      </div>
    </div>
  );
}
