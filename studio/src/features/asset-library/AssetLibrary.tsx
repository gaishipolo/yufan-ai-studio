import { useEffect, useRef, useState } from 'react';
import { ImageOff, UploadCloud } from 'lucide-react';
import {
  Button,
  CardSkeletonGrid,
  EmptyState,
  ErrorState,
  Modal,
  Pager,
  SearchInput,
  Tabs,
} from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { useCan } from '@/shared/access/use-access';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { toast } from '@/shared/lib/toast';
import { AssetCard } from '@/entities/asset/ui/AssetCard';
import { useAssets, useDeleteAsset, useUploadAsset } from '@/entities/asset/api/asset.queries';
import type { Asset, AssetListParams } from '@/entities/asset/model/types';
import { AssetPreview } from './AssetPreview';

const PAGE_SIZE = 24;

/** 一次最多同时选的个数：再多就不是「挑几张素材」，而是批量导入，该走归档线。 */
const MAX_UPLOAD_AT_ONCE = 20;

export function AssetLibrary() {
  const canDelete = useCan('asset:delete');

  const [type, setType] = useState<NonNullable<AssetListParams['type']>>('all');
  const [keywordInput, setKeywordInput] = useState('');
  const keyword = useDebouncedValue(keywordInput, 300).trim();
  const [page, setPage] = useState(1);

  // 筛选条件变化必须回到第 1 页：否则「在第 3 页搜一个只有 2 条的关键字」
  // 会得到一个空列表 + 「第 3 / 1 页」，看起来像搜索没生效。
  useEffect(() => setPage(1), [type, keyword]);

  const params: AssetListParams = { type, keyword: keyword || undefined, page, pageSize: PAGE_SIZE };
  const { data, isLoading, isFetching, isError, error, refetch } = useAssets(params);
  const upload = useUploadAsset();
  const removal = useDeleteAsset();

  const [selected, setSelected] = useState<Asset | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Asset | null>(null);
  const [progress, setProgress] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pageSize = data?.pageSize ?? PAGE_SIZE;
  // 没有数据（加载中 / 请求失败）时给 0，Pager 据此判断「还不知道总数」而不回收页码
  const totalPages = data ? Math.max(1, Math.ceil(total / pageSize)) : 0;
  const counts = data?.counts;

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    const picked = Array.from(files).slice(0, MAX_UPLOAD_AT_ONCE);
    if (files.length > MAX_UPLOAD_AT_ONCE) {
      toast(`一次最多上传 ${MAX_UPLOAD_AT_ONCE} 个文件，多余的已忽略`, 'error');
    }
    // 上传前这一页已有的 id：去重命中时后端返回的是**旧记录**，
    // 它的 id 就在这个集合里，可以用来区分「新入库」和「已存在」。
    // 本批内重复选同一个文件，也会落在 seen 里。
    const known = new Set(items.map((item) => item.id));
    const seen = new Set<string>();
    let succeeded = 0;
    let duplicate = 0;
    for (const file of picked) {
      setProgress(0);
      try {
        const asset = await upload.mutateAsync({
          file,
          onProgress: (percent) => setProgress(percent),
        });
        if (known.has(asset.id) || seen.has(asset.id)) duplicate += 1;
        else succeeded += 1;
        seen.add(asset.id);
      } catch (reason) {
        toast(`${file.name}：${reason instanceof Error ? reason.message : '上传失败'}`, 'error');
      }
    }
    setProgress(0);
    if (input.current) input.current.value = '';

    if (succeeded && duplicate) {
      toast(`已上传 ${succeeded} 个，${duplicate} 个已存在（自动复用）`, 'success');
    } else if (succeeded) {
      toast(`已上传 ${succeeded} 个素材`, 'success');
    } else if (duplicate) {
      toast('这些文件已经在素材库里了', 'success');
    }
    // 新素材在最前面：停在旧页看不到刚传的那张
    if (succeeded) setPage(1);
  }

  function confirmDelete() {
    if (!pendingDelete) return;
    const target = pendingDelete;
    removal.mutate(target.id, {
      onSuccess: () => {
        toast('素材已删除', 'success');
        setPendingDelete(null);
        setSelected((current) => (current?.id === target.id ? null : current));
      },
      onError: (reason) => {
        // 被模板 / 任务引用时后端回 409 并说明引用方，原样展示才有用
        toast(reason.message, 'error');
        setPendingDelete(null);
      },
    });
  }

  const typeTabs = [
    { value: 'all' as const, label: '全部', count: counts?.all },
    { value: 'image' as const, label: '图片', count: counts?.image },
    { value: 'video' as const, label: '视频', count: counts?.video },
    { value: 'audio' as const, label: '音频', count: counts?.audio },
  ];

  const filtering = type !== 'all' || keyword.length > 0;

  return (
    <section className="space-y-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          items={typeTabs}
          value={type}
          onChange={(next) => setType(next)}
          className="min-w-0"
        />
        <SearchInput
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          placeholder="按文件名搜索"
          aria-label="按文件名搜索素材"
          wrapperClassName="w-[190px]"
        />
        <span className="ml-auto text-[11.5px] text-ink-mute">
          {isError ? '共 — 个' : `共 ${total} 个`}
        </span>
        <Can permission="asset:create">
          <Button
            icon={<UploadCloud size={14} />}
            loading={upload.isPending}
            onClick={() => input.current?.click()}
          >
            上传素材
          </Button>
        </Can>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          aria-label="上传素材文件"
          accept="image/*,video/*,audio/*"
          onChange={(event) => {
            void uploadFiles(event.target.files);
          }}
        />
      </div>

      {upload.isPending && (
        <div className="flex items-center gap-2 rounded-card border border-line-soft bg-panel px-3 py-2 text-[11.5px] text-ink-soft">
          <span className="shrink-0">正在上传…</span>
          <div className="h-1 flex-1 overflow-hidden rounded-pill bg-line-soft">
            <div
              className="h-full bg-primary transition-all duration-150"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span className="w-9 shrink-0 text-right tabular-nums">{progress}%</span>
        </div>
      )}

      {isLoading ? (
        <CardSkeletonGrid count={8} />
      ) : isError ? (
        <ErrorState error={error} title="素材加载失败" onRetry={() => void refetch()} />
      ) : !items.length ? (
        <EmptyState
          icon={<ImageOff size={28} />}
          title={filtering ? '没有匹配的素材' : '素材库还是空的'}
          description={
            filtering
              ? '换个类型或关键字再试一次。'
              : '上传图片、视频或音频，它们会成为生成任务的参考素材。'
          }
          action={
            filtering ? (
              <Button
                onClick={() => {
                  setType('all');
                  setKeywordInput('');
                }}
              >
                清除筛选
              </Button>
            ) : (
              <Can permission="asset:create">
                <Button
                  variant="primary"
                  icon={<UploadCloud size={14} />}
                  onClick={() => input.current?.click()}
                >
                  上传第一份素材
                </Button>
              </Can>
            )
          }
        />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-3">
          {items.map((asset) => (
            <AssetCard
              key={asset.id}
              asset={asset}
              onOpen={setSelected}
              onDelete={canDelete ? setPendingDelete : undefined}
            />
          ))}
        </div>
      )}

      <Pager page={page} totalPages={totalPages} busy={isFetching} onChange={setPage} />

      <AssetPreview
        asset={selected}
        onClose={() => setSelected(null)}
        onDelete={canDelete ? setPendingDelete : undefined}
        deleting={removal.isPending}
      />

      <Modal
        open={!!pendingDelete}
        onClose={() => {
          if (!removal.isPending) setPendingDelete(null);
        }}
        title="删除素材"
        width={420}
      >
        <div className="space-y-4 p-4">
          <p className="text-xs leading-relaxed text-ink-soft">
            确认删除「{pendingDelete?.name}」？文件会从存储中移除，且不可恢复。
          </p>
          <p className="text-[11px] text-ink-mute">
            被模板或任务引用的素材不会被删除，后端会拦下来并说明引用方。
          </p>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setPendingDelete(null)} disabled={removal.isPending}>
              取消
            </Button>
            <Button variant="danger" loading={removal.isPending} onClick={confirmDelete}>
              确认删除
            </Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}
