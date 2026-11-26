import { useState } from 'react';
import type { TemplateCaseOut } from '@/shared/api/schema';
import type { CaseScope } from '@/entities/template-case/api/case.api';
import { useCaseMutations, useTemplateCases } from '@/entities/template-case/api/case.queries';
import { useCan } from '@/shared/access/use-access';
import { ConfirmDialog, EmptyState, Notice, SearchInput, Segmented } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { CaseCard } from './CaseCard';
import { CaseDetailDialog } from './CaseDetailDialog';
import { CaseEditorDialog } from './CaseEditorDialog';

export interface CaseLibraryProps {
  /** 复用：拿到案例，在编辑器里打开成一份新草稿。 */
  onReuse: (item: TemplateCaseOut) => void;
}

/**
 * 案例库面板。
 *
 * ## 三档口径
 *
 * - **公共案例库**：所有人发布的、已上架的，别人能看见也能复用；
 * - **我发布的**：自己发的全部（含已下架），用来管理；
 * - **已删除**：回收站，可恢复。
 *
 * 口径是**后端给的**（`scope` 查询参数），不是前端过滤 —— 前端过滤会把别人
 * 下架的案例也算进来，那是他不该看见的东西。
 */
export function CaseLibrary({ onReuse }: CaseLibraryProps) {
  const [scope, setScope] = useState<CaseScope>('library');
  const [keyword, setKeyword] = useState('');
  const [category, setCategory] = useState('');
  const [editing, setEditing] = useState<TemplateCaseOut | null>(null);
  const [detail, setDetail] = useState<TemplateCaseOut | null>(null);
  /* 删除要二次确认：页面内的 ConfirmDialog，不是浏览器的原生弹窗。 */
  const [removing, setRemoving] = useState<TemplateCaseOut | null>(null);

  const cases = useTemplateCases({ scope, keyword, category, pageSize: 100 });
  const { update, offline, online, remove, restore } = useCaseMutations();

  /* 管理动作（编辑 / 下架 / 上架 / 删除 / 恢复）要 `template_pack:case:write`。
     没有就不摆这些按钮 —— 后端一定 403，露出来只是让人点一次错。 */
  const canManage = useCan('template_pack:case:write');

  const items = cases.data?.items ?? [];
  const categories = cases.data?.categories ?? [];
  const busy =
    update.isPending ||
    offline.isPending ||
    online.isPending ||
    remove.isPending ||
    restore.isPending;

  const fail = (reason: unknown) =>
    toast(reason instanceof Error ? reason.message : String(reason), 'error');

  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented
          options={[
            { value: 'library', label: '公共案例库' },
            { value: 'mine', label: '我发布的' },
            { value: 'trash', label: '已删除' },
          ]}
          value={scope}
          onChange={value => {
            setScope(value);
            /* 分类是按口径给的：换口径后原来的分类可能不存在了，
               留着会得到一个空列表（看起来像「这个分类下没有案例」）。 */
            setCategory('');
          }}
          aria-label="案例口径"
        />
        <SearchInput
          value={keyword}
          placeholder="搜索案例名称、描述或模板 key"
          wrapperClassName="w-full sm:w-72"
          onChange={event => setKeyword(event.target.value)}
        />
      </div>

      {categories.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={() => setCategory('')}
            className={
              'rounded-xs border px-2 py-[2px] text-micro transition-colors ' +
              (category === ''
                ? 'border-primary-line bg-primary-soft text-primary-deep'
                : 'border-line-soft bg-panel text-ink-mute hover:text-ink-soft')
            }
          >
            全部分类
          </button>
          {categories.map(item => (
            <button
              key={item}
              type="button"
              onClick={() => setCategory(item)}
              className={
                'rounded-xs border px-2 py-[2px] text-micro transition-colors ' +
                (category === item
                  ? 'border-primary-line bg-primary-soft text-primary-deep'
                  : 'border-line-soft bg-panel text-ink-mute hover:text-ink-soft')
              }
            >
              {item}
            </button>
          ))}
        </div>
      )}

      {cases.isError && (
        <Notice tone="error">
          {cases.error instanceof Error ? cases.error.message : '案例加载失败'}
        </Notice>
      )}

      {cases.isLoading ? (
        <p className="py-10 text-center text-xs text-ink-mute">正在读取…</p>
      ) : items.length === 0 ? (
        <EmptyState
          title={
            scope === 'library'
              ? '案例库还是空的'
              : scope === 'mine'
                ? '你还没有发布过案例'
                : '回收站是空的'
          }
        />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {items.map(item => (
            <CaseCard
              key={item.id}
              item={item}
              scope={scope}
              busy={busy}
              canManage={canManage}
              onDetail={() => setDetail(item)}
              onReuse={() => onReuse(item)}
              onEdit={() => setEditing(item)}
              onOffline={() =>
                offline
                  .mutateAsync(item.id)
                  .then(() => toast(`已下架「${item.name}」，别人再也复用不了`, 'success'))
                  .catch(fail)
              }
              onOnline={() =>
                online
                  .mutateAsync(item.id)
                  .then(() => toast(`已上架「${item.name}」`, 'success'))
                  .catch(fail)
              }
              onRemove={() => setRemoving(item)}
              onRestore={() =>
                restore
                  .mutateAsync(item.id)
                  .then(() => toast(`已恢复「${item.name}」`, 'success'))
                  .catch(fail)
              }
            />
          ))}
        </div>
      )}

      <CaseEditorDialog
        item={editing}
        onClose={() => setEditing(null)}
        onSave={payload => {
          if (!editing) return;
          update
            .mutateAsync({id: editing.id, payload})
            .then(() => {
              toast(`已保存案例「${payload.name ?? editing.name}」`, 'success');
              setEditing(null);
            })
            .catch(fail);
        }}
      />

      <CaseDetailDialog
        item={detail}
        onClose={() => setDetail(null)}
        onReuse={item => {
          setDetail(null);
          onReuse(item);
        }}
      />

      {/* 移进回收站的二次确认：把「这次会发生什么」摊开写清楚，
          不用原生 `window.confirm`（样式不受控，还会卡住整页）。 */}
      <ConfirmDialog
        open={removing !== null}
        title="把案例移进回收站？"
        description={`「${removing?.name ?? ''}」会移进回收站，随时可以恢复。`}
        details={[
          {label: '案例', value: removing?.name ?? '—'},
          {label: '影响', value: '别人再也复用不了这个案例'},
        ]}
        notice="回收站里的案例仍然保留全部配置，恢复后与原来一致。"
        noticeTone="info"
        confirmTone="danger"
        confirmLabel="移进回收站"
        onCancel={() => setRemoving(null)}
        onConfirm={() => {
          const item = removing;
          if (!item) return;
          setRemoving(null);
          remove
            .mutateAsync(item.id)
            .then(() => toast(`已移进回收站：${item.name}`, 'success'))
            .catch(fail);
        }}
      />
    </section>
  );
}
