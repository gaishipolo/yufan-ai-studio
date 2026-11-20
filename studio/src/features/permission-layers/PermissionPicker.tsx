import { useMemo, useState } from 'react';
import { Loader2, Search } from 'lucide-react';
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

type Item = {
  code: string;
  name: string;
  description: string;
  route?: string;
  requires?: readonly string[];
  /** 覆盖 aria-label（通配这类没有名字的项用它，否则两个通配会撞名） */
  ariaLabel?: string;
};

/**
 * 权限勾选器：一个页面一张卡片，它管着的接口就在卡片里。
 *
 * ## 为什么是「卡片套列表」，而不是两栏并排
 *
 * 页面与接口之间是**从属**：页面没开，它管着的接口会被服务端门控砍掉。
 * 两栏并排表达的是「两件平行的事」，所以只能靠三处小字去补 ——
 * 页头一句「关掉页面，接口一并失效」、每行一句「归 X 管」、再加一句
 * 「已生效 / 未生效」。一个版式需要三处补丁才看得懂，说明补丁在替版式说话。
 *
 * 换成**容器**之后：
 * - 「归 X 管」不需要了 —— 在谁的卡片里就归谁；
 * - 「已生效 / 未生效」不需要了 —— 页面没勾时那一卡整段变淡，
 *   「勾了也用不了」是看得出来的，不用写；
 * - 页头那句话也不需要了 —— 它就是卡片的形状。
 *
 * ## 两个刻意的例外
 *
 * - **共享接口会出现在两张卡里**（`catalog:read` 同时归「生成工作台」和「生图」）。
 *   状态只有一份（都在 `selected` 里），所以不会不同步；`title` 里说明
 *   「任一页开着就有效」—— 关掉其中一页时它仍然可用，这是猜不到的约束。
 * - **勾页面不会自动勾接口**：页面是可见性，接口是能力。自动勾会把
 *   「只想让人看得见这一页、不让他动」这种诉求抹掉。每张卡片右上角的「全选」
 *   是给「这一页的接口全给」准备的，仍然是一次显式动作。
 *
 * 团队层（页面码全部禁授）里卡片仍然在，只是页头没有勾选框：这一层不决定
 * 页面可见性，但「这些接口属于哪一页」仍然是有效信息。
 */
export function PermissionPicker({
  selected,
  onChange,
  inherited,
  forbidden,
  forbiddenHint,
}: PermissionPickerProps) {
  const catalogQuery = usePermissionCatalog();
  const [keyword, setKeyword] = useState('');

  /** 目录 → 「页面卡片 + 后台能力 + 目录外」三段视图模型。 */
  const model = useMemo(() => {
    const groups = catalogQuery.data?.groups ?? [];
    const pages: Item[] = [];
    const apis: Item[] = [];
    for (const group of groups) {
      for (const item of group.items) {
        if (item.code.startsWith('page:')) pages.push(item);
        else apis.push(item);
      }
    }

    // 接口归哪几页：由页面项下发的 `requires` 反查（后端按接口侧的 `pages=` 生成）
    const owners = new Map<string, string[]>();
    for (const page of pages) {
      for (const code of page.requires ?? []) {
        owners.set(code, [...(owners.get(code) ?? []), page.code]);
      }
    }

    const byPage = new Map<string, Item[]>(pages.map((page) => [page.code, []]));
    const free: Item[] = [];
    for (const api of apis) {
      const own = owners.get(api.code) ?? [];
      if (own.length === 0) {
        free.push(api);
        continue;
      }
      // 共享接口给每一页都放一份：管理员在「生图」卡片里找得到它，不必去别处翻
      for (const code of own) byPage.get(code)?.push(api);
    }

    const catalogCodes = new Set<string>([...pages, ...apis].map((item) => item.code));
    return { pages, byPage, free, owners, catalogCodes };
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
    onChange(
      selected.includes(code)
        ? selected.filter((item) => item !== code)
        : [...selected, code],
    );
  };

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

  const lockedCodes = Object.keys(inherited ?? {}).filter(
    (code) => !selected.includes(code),
  );

  /* 已勾选、但目录里没有的码：`task:*` / `image:*` 这类通配，或已下线模块留下的
     （v1 的 `asset:read`）。它们照样在 `selected` 里、照样被汇总条算进「接口 N」，
     却原来一行都渲染不出来 —— 数字对不上，而看不见的那几条恰恰是最危险的：
     通配能一次性覆盖十几个接口，撤的时候却找不到它在哪。 */
  const offCatalog = [...new Set([...selected, ...lockedCodes])].filter(
    (code) => !model.catalogCodes.has(code),
  );

  const selectedPages = selected.filter((code) => code.startsWith('page:')).length;
  const selectedApis = selected.length - selectedPages;

  const kw = keyword.trim().toLowerCase();
  const hit = (item: Item) =>
    !kw || item.name.toLowerCase().includes(kw) || item.code.toLowerCase().includes(kw);

  // 页面名命中时整卡保留（父节点是上下文）；否则只留命中的接口
  const cards = model.pages
    .map((page) => {
      const all = model.byPage.get(page.code) ?? [];
      const shown = kw && !hit(page) ? all.filter(hit) : all;
      return { page, all, shown };
    })
    .filter((card) => !kw || hit(card.page) || card.shown.length > 0);

  const freeShown = model.free.filter(hit);
  const nothing =
    kw && cards.length === 0 && freeShown.length === 0 && offCatalog.length === 0;

  /** 一个可勾选的权限项（接口 / 通配 / 后台能力共用）。 */
  const row = (item: Item, owners?: string[]) => {
    const locked = isLocked(item.code);
    const checked = selected.includes(item.code) || locked;
    const blocked = Boolean(forbidden?.(item.code));
    // 同一个接口归多页时，关掉其中一页不会让它失效 —— 这一点猜不到，放 title
    const shared = (owners?.length ?? 0) > 1;
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
                : shared
                  ? '这一页与别的页面共用它：任一页开着就有效，关掉本页不会让它失效'
                  : undefined
          }
          className={cn(
            'flex min-w-0 flex-1 items-start gap-2',
            blocked || locked ? 'cursor-not-allowed' : 'cursor-pointer',
          )}
        >
          <input
            type="checkbox"
            className="mt-[3px] h-3 w-3 shrink-0 accent-primary disabled:opacity-40"
            checked={checked}
            disabled={blocked || locked}
            onChange={() => toggle(item.code)}
            /* 名称可能重名（同一个动作在不同模块都有），只靠可见文字取元素会撞车 */
            aria-label={item.ariaLabel ?? `${item.name} 接口权限`}
          />
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-1.5">
              {item.name ? (
                <span className={cn('text-xs', locked ? 'text-ink-mute' : 'text-ink-soft')}>
                  {item.name}
                </span>
              ) : null}
              {locked ? (
                <span
                  className="text-meta text-ink-mute"
                  title="别的层已经授予它；本层取消勾选不会收回它"
                >
                  来自 {inherited?.[item.code]?.join('、')}
                </span>
              ) : null}
              <span className="font-mono text-micro text-ink-mute">{item.code}</span>
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
          </span>
        </label>
      </li>
    );
  };

  if (catalogQuery.isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-xs text-ink-mute">
        <Loader2 className="animate-spin" size={14} /> 正在加载权限目录…
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

      <div className="flex items-center gap-1.5">
        <Search size={12} className="shrink-0 text-ink-mute" />
        <input
          type="search"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜页面、接口名或权限码"
          aria-label="搜索权限"
          className="w-full rounded border border-line-soft bg-panel px-1.5 py-0.5 text-meta text-ink outline-none placeholder:text-ink-mute focus:border-primary-soft"
        />
      </div>

      <div className="max-h-[58vh] overflow-auto pr-1">
        {/* 多列而不是 grid：卡片高度差很大（一页 1 个接口、另一页 14 个），
            grid 会把同一行拉齐，短的那张变成一大块空白边框。 */}
        <div className="columns-1 gap-2 md:columns-2">
          {cards.map(({ page, all, shown }) => {
            const blocked = Boolean(forbidden?.(page.code));
            const locked = isLocked(page.code);
            const checked = selected.includes(page.code) || locked;
            /* 页面没开 → 卡片正文变淡，这就是「勾了也不生效」的表达：
               不用另写一句「未生效」，它所属的容器已经淡了。
               团队层（页面禁授）不淡：这一层根本不决定页面可见性。 */
            const dimmed = !blocked && !checked;
            const codes = all.map((item) => item.code);
            const selectable = codes.filter((code) => !forbidden?.(code));
            const allChecked =
              selectable.length > 0 &&
              selectable.every((code) => selected.includes(code) || isLocked(code));
            return (
              <section
                key={page.code}
                className="mb-2 flex min-w-0 break-inside-avoid flex-col rounded-lg border border-line-soft"
              >
                <header className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line-strong bg-line px-2.5 py-2">
                  {blocked ? null : (
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 shrink-0 accent-primary disabled:opacity-40"
                      checked={checked}
                      disabled={locked}
                      onChange={() => toggle(page.code)}
                      /* 页面与接口可能重名，取元素时要能分开 */
                      aria-label={`${page.name} 页面权限`}
                    />
                  )}
                  {/* 页面名比接口名**大一档、颜色更深**：一屏九张卡，先认出「有哪几页」，
                      再看页里有什么。字号不是用来表达意思的（那靠位置与勾选），
                      这里只用它来分两级。 */}
                  <span
                    className={cn(
                      'min-w-0 text-body font-semibold',
                      locked ? 'text-ink-mute' : 'text-ink',
                    )}
                    title={locked ? '别的层已经授予这一页' : undefined}
                  >
                    {page.name}
                  </span>
                  {page.route ? (
                    <span className="text-micro text-ink-mute">{page.route}</span>
                  ) : null}
                  {locked ? (
                    <span
                      className="text-meta text-ink-mute"
                      title="别的层已经授予这一页；本层取消勾选不会收回它"
                    >
                      来自 {inherited?.[page.code]?.join('、')}
                    </span>
                  ) : null}
                  <span className="ml-auto text-meta tabular-nums text-ink-mute">
                    {all.length}
                  </span>
                  <button
                    type="button"
                    className="text-meta text-primary hover:underline disabled:opacity-50"
                    disabled={selectable.length === 0}
                    onClick={() => toggleMany(selectable, !allChecked)}
                  >
                    {allChecked ? '取消' : '全选'}
                  </button>
                </header>
                {all.length > 0 ? (
                  <ul
                    className={cn(
                      'ml-2.5 grid gap-0.5 border-l border-line-soft py-1 pl-2 pr-1.5 transition-opacity',
                      dimmed && 'opacity-55',
                    )}
                  >
                    {shown.map((item) => row(item, model.owners.get(item.code)))}
                  </ul>
                ) : (
                  <p className="px-2.5 py-2 text-meta text-ink-mute">这一页没有接口</p>
                )}
              </section>
            );
          })}

          {freeShown.length > 0 ? (
            <section className="mb-2 flex min-w-0 break-inside-avoid flex-col rounded-lg border border-line-soft">
              <header className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line-strong bg-line px-2.5 py-2">
                <span className="text-xs font-semibold text-ink-soft">后台能力</span>
                <span className="ml-auto text-meta tabular-nums text-ink-mute">
                  {model.free.length}
                </span>
              </header>
              <ul className="grid gap-0.5 p-1.5">
                {freeShown.map((item) => row(item))}
              </ul>
            </section>
          ) : null}

          {offCatalog.length > 0 ? (
            <section className="mb-2 flex min-w-0 break-inside-avoid flex-col rounded-lg border border-line-soft">
              <header className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line-strong bg-line px-2.5 py-2">
                <span className="text-xs font-semibold text-ink-soft">目录外</span>
                <span className="ml-auto text-meta tabular-nums text-ink-mute">
                  {offCatalog.length}
                </span>
              </header>
              <ul className="grid gap-0.5 p-1.5">
                {offCatalog.map((code) =>
                  row({
                    code,
                    // 没有名字（目录里没有它），可见标识就是权限码本身
                    name: '',
                    ariaLabel: `${code} 接口权限`,
                    description: code.endsWith(':*')
                      ? '整类授权：覆盖该资源下现有的全部接口'
                      : '目录里已没有这个码',
                  }),
                )}
              </ul>
            </section>
          ) : null}
        </div>

        {nothing ? (
          <p className="py-6 text-center text-xs text-ink-mute">没有匹配的权限点</p>
        ) : null}
      </div>
    </div>
  );
}
