import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Filter, Plus } from 'lucide-react';
import { Button, EmptyState, PageHeader } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { useTemplatePacks } from '@/entities/template-pack/api/templatePack.queries';

/**
 * 封装模板库。
 *
 * 两类内容：
 * - **已发布**的 everybody 都能用 —— 这是模板库的主体。
 * - **我的草稿**只有我能看 —— 草稿是私有工作区，里面有提示词原文与筛选条件。
 */
export default function TemplatePacksPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<'published' | 'mine'>('published');
  const packsQuery = useTemplatePacks();

  const visible = useMemo(() => {
    const items = packsQuery.data?.items ?? [];
    /* 「我的草稿」这一栏要带上 status=draft，但草稿只有作者能看，
       后端已经把非本人的草稿挡在外面了，这里只是不再显示别人的已发布。 */
    if (tab === 'published') return items.filter(item => item.status === 'published');
    return items.filter(item => item.status !== 'published');
  }, [packsQuery.data?.items, tab]);

  return (
    <>
      <PageHeader
        title="封装模板"
        description="带筛选约束的模板：作者声明素材准入条件，发布后用户只能从符合条件的素材里选。"
      />

      <div className="mx-auto max-w-6xl space-y-4 px-4 pb-16">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 rounded-lg bg-panel-soft p-0.5">
            <button
              type="button"
              onClick={() => setTab('published')}
              className={cn(
                'h-7 rounded-md px-3 text-xs transition-colors',
                tab === 'published'
                  ? 'bg-white text-ink shadow-sm'
                  : 'text-ink-mute hover:text-ink-soft',
              )}
            >
              模板库
            </button>
            <button
              type="button"
              onClick={() => setTab('mine')}
              className={cn(
                'h-7 rounded-md px-3 text-xs transition-colors',
                tab === 'mine'
                  ? 'bg-white text-ink shadow-sm'
                  : 'text-ink-mute hover:text-ink-soft',
              )}
            >
              我的草稿
            </button>
          </div>

          <Button
            type="button"
            size="sm"
            onClick={() => navigate('/template-packs/new')}
          >
            <Plus size={13} />
            新增模板
          </Button>
        </div>

        {packsQuery.isLoading ? (
          <p className="py-12 text-center text-xs text-ink-mute">加载中…</p>
        ) : visible.length === 0 ? (
          <EmptyState
            title={tab === 'published' ? '模板库里还没有内容' : '你还没有草稿'}
            description={
              tab === 'published'
                ? '新建一条封装模板：参考视频 + 固定提示词 + 一组槽位，发布后就能在这里被取用。'
                : '草稿只有你自己能看到，发布之后才会进模板库。'
            }
          />
        ) : (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {visible.map(pack => {
              const filtered = (pack.slots ?? []).filter(
                slot => slot.slotKind === 'filtered',
              ).length;
              return (
                <li key={pack.id}>
                  <Link
                    to={`/template-packs/${pack.id}`}
                    className="block rounded-xl border border-line bg-white p-3.5 transition-colors hover:border-line-strong"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="min-w-0 flex-1 truncate text-sm font-medium text-ink">
                        {pack.name}
                      </h3>
                      <span className="shrink-0 rounded bg-panel-soft px-1.5 py-0.5 text-[10px] text-ink-mute">
                        {pack.status === 'published' ? '已发布' : pack.status === 'draft' ? '草稿' : '已停用'}
                      </span>
                    </div>
                    {pack.description && (
                      <p className="mt-1 line-clamp-2 text-[11.5px] text-ink-mute">
                        {pack.description}
                      </p>
                    )}
                    <div className="mt-2.5 flex items-center gap-3 text-[11px] text-ink-mute">
                      <span>{(pack.slots ?? []).length} 个槽位</span>
                      {filtered > 0 && (
                        <span className="flex items-center gap-1">
                          <Filter size={10} />
                          {filtered} 个筛选槽位
                        </span>
                      )}
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </>
  );
}
