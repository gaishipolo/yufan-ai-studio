import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  ArrowUpRight,
  Download,
  FileAudio,
  Film,
  Image as ImageIcon,
  Package,
  Pencil,
  Share2,
  Trash2,
  Undo2,
} from 'lucide-react';
import {
  Button,
  Chip,
  CoverImage,
  ErrorState,
  Modal,
  MultiSelect,
  Skeleton,
} from '@/shared/ui';
import { formatBytes, formatDateTime, formatDuration } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import type {
  LibraryAssetDetailOut,
  LibraryAssetOut,
  LibraryTagOut,
  ShareHistoryItem,
} from '@/shared/api/schema';
import {
  KIND_LABEL,
  ORIGIN_LABEL,
  STATE_LABEL,
  VISIBILITY_LABEL,
  deleteOffer,
  isTrashExpired,
  trashDaysLeft,
} from '@/entities/library/model/types';
import {
  useLibraryDetail,
  useLibraryMedia,
  usePatchAsset,
  useSaveAssetFile,
} from '@/entities/library/api/library.queries';
import type { LibraryCardAction } from './LibraryCard';
import { useCreateTagInline } from './use-create-tag-inline';

export interface LibraryDetailProps {
  assetId: string | null;
  tags: LibraryTagOut[];
  viewer: { id: string; canDeleteAny: boolean };
  canUpdate: boolean;
  onClose: () => void;
  onAction: (action: LibraryCardAction, asset: LibraryAssetOut) => void;
  onOpenAsset: (id: string) => void;
}

/**
 * 详情六区（设计 §7.2）：媒体 / 基础信息 / 媒体规格 / 生成参数 / 使用与引用 / 归档信息。
 *
 * 前两区常显，后四区按「有没有内容」出现——生成参数只对 `origin=generated` 显示，
 * 归档信息只给本人和管理员。**未知值显示「未知」**，不写 0×0 / 0 B：
 * 0 是一个合法事实（空文件），未知是「没记下来」，两者混用会让排查走错方向。
 */
export function LibraryDetail(props: LibraryDetailProps) {
  const { assetId } = props;
  const query = useLibraryDetail(assetId);
  const asset = query.data;

  if (!assetId) return null;

  return (
    <Modal open onClose={props.onClose} title={asset?.name ?? '资产详情'} width={880}>
      {query.error ? (
        <div className="p-[18px]">
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </div>
      ) : !asset ? (
        <div className="space-y-3 p-[18px]">
          <Skeleton className="h-52 w-full" />
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-4 w-2/3" />
        </div>
      ) : (
        <DetailBody {...props} asset={asset} />
      )}
    </Modal>
  );
}

function DetailBody({
  asset,
  tags,
  viewer,
  canUpdate,
  onClose,
  onAction,
  onOpenAsset,
}: LibraryDetailProps & { asset: LibraryAssetDetailOut }) {
  const patch = usePatchAsset();
  const saveFile = useSaveAssetFile();
  const createTag = useCreateTagInline();
  const offer = deleteOffer(asset, viewer);
  const days = trashDaysLeft(asset.trashExpiresAt);
  const hasMedia = asset.kind === 'video' || asset.kind === 'audio';
  const media = useLibraryMedia(asset.id, hasMedia);
  const [renaming, setRenaming] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(asset.name);
  const [tagIds, setTagIds] = useState<string[]>(asset.tags?.map((tag) => tag.id) ?? []);

  /* 换一条资产时把本地编辑态重置，否则会把上一条的名字带到下一条 */
  useEffect(() => {
    setName(asset.name);
    setTagIds(asset.tags?.map((tag) => tag.id) ?? []);
    setRenaming(false);
  }, [asset.id, asset.name, asset.tags]);

  /* 点铅笔后再聚焦：autoFocus 会在弹窗打开时抢焦点，把滚动条也拽下去 */
  useEffect(() => {
    if (renaming) nameInput.current?.focus();
  }, [renaming]);

  /**
   * `expectedVersion` 默认带当前版本，但**标签改动不带**。
   *
   * 标签选择器是每勾一次就提交一次的整体替换，而 `asset` 要等 invalidate 后
   * 重新拉取才更新：勾第二个标签时带的还是旧版本，服务端第一次已经把它 +1 了，
   * 于是必然 409「资产已被修改」——用户看到的是「打两个标签，第二个没生效」。
   * 改名仍带版本：那才是「两个人同时改同一行」该拦的场景。
   */
  const save = (
    body: { name?: string; tagIds?: string[] },
    options?: { versioned?: boolean },
  ) => {
    patch.mutate(
      {
        id: asset.id,
        body: {
          ...body,
          expectedVersion: options?.versioned === false ? 0 : (asset.stateVersion ?? 0),
        },
      },
      {
        onSuccess: () => {
          setRenaming(false);
          toast('已保存');
        },
        onError: (error) => toast(error.message, 'error'),
      },
    );
  };

  const download = () => {
    saveFile.mutate(
      { id: asset.id, filename: asset.name },
      {
        onSuccess: () => toast('已开始下载'),
        onError: (error) => toast(error.message, 'error'),
      },
    );
  };

  return (
    <div className="max-h-[76vh] space-y-3 overflow-auto p-[18px]">
      {/* 1. 媒体区 */}
      <section className="overflow-hidden rounded-card border border-line-soft bg-panel-soft">
        {asset.kind === 'pack' ? (
          <PackMembers asset={asset} onOpenAsset={onOpenAsset} />
        ) : asset.kind === 'image' ? (
          <MediaImage asset={asset} />
        ) : asset.kind === 'video' ? (
          media.url ? (
            <video
              src={media.url}
              controls
              poster={asset.coverUrl ?? undefined}
              aria-label={asset.name}
              className="max-h-[360px] w-full bg-black"
            >
              <track kind="captions" />
            </video>
          ) : (
            <MediaFallback icon={<Film size={22} />} loading={media.isPending} />
          )
        ) : media.url ? (
          <div className="flex items-center gap-3 p-4">
            <FileAudio size={22} className="text-ink-mute" />
            <audio src={media.url} controls aria-label={asset.name} className="w-full">
              <track kind="captions" />
            </audio>
          </div>
        ) : (
          <MediaFallback icon={<FileAudio size={22} />} loading={media.isPending} />
        )}
      </section>

      {/* 2. 基础信息 */}
      <section className="space-y-2">
        <SectionTitle>基础信息</SectionTitle>
        <div className="flex flex-wrap items-center gap-1.5">
          <Chip tone="primary">{KIND_LABEL[asset.kind]}</Chip>
          <Chip>{ORIGIN_LABEL[asset.origin] ?? asset.origin}</Chip>
          <Chip tone={asset.state === 'active' ? 'ok' : asset.state === 'draft' ? 'warn' : 'danger'}>
            {STATE_LABEL[asset.state]}
            {asset.state === 'trash' && days != null ? ` · ${isTrashExpired(asset.trashExpiresAt) ? '已过期' : `剩余 ${days} 天`}` : ''}
          </Chip>
          {asset.state === 'trash' && asset.trashedFromState && (
            <Chip>来自{STATE_LABEL[asset.trashedFromState as 'draft' | 'active'] ?? asset.trashedFromState}</Chip>
          )}
          <Chip>{VISIBILITY_LABEL[asset.visibility]}</Chip>
          {asset.groups?.map((group) => (
            <Chip key={group.id}>{group.name || group.id}</Chip>
          ))}
        </div>

        {asset.rejectReason && (
          <p className="rounded-lg border border-danger-line bg-danger-soft px-2.5 py-1.5 text-[11.5px] text-danger-deep">
            驳回原因：{asset.rejectReason}
          </p>
        )}

        <div className="grid gap-1.5 sm:grid-cols-2">
          <Field label="所有者" value={asset.ownerName || asset.ownerId} />
          <Field label="创建时间" value={formatDateTime(asset.createdAt)} />
          <Field
            label="名称"
            value={
              renaming ? (
                <span className="flex items-center gap-1">
                  <input
                    ref={nameInput}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    className="h-7 w-full rounded-lg border border-primary-line bg-white px-2 text-xs text-ink outline-none"
                    aria-label="资产名称"
                  />
                  <Button size="sm" variant="primary" onClick={() => name.trim() && save({ name: name.trim() })}>
                    保存
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setRenaming(false)}>
                    取消
                  </Button>
                </span>
              ) : (
                <span className="flex items-center gap-1">
                  <span className="truncate">{asset.name}</span>
                  {canUpdate && asset.state !== 'trash' && (
                    <button
                      type="button"
                      className="text-ink-mute hover:text-primary-deep"
                      aria-label="重命名"
                      onClick={() => setRenaming(true)}
                    >
                      <Pencil size={12} />
                    </button>
                  )}
                </span>
              )
            }
          />
          <Field
            label="标签"
            value={
              canUpdate && asset.state !== 'trash' ? (
                <MultiSelect
                  options={tags.map((tag) => ({ value: tag.id, label: tag.name }))}
                  value={tagIds}
                  onChange={(values) => {
                    setTagIds(values);
                    save({ tagIds: values }, { versioned: false });
                  }}
                  placeholder="未打标签"
                  emptyText="还没有标签"
                  ariaLabel="编辑标签"
                  onCreate={createTag}
                />
              ) : (
                asset.tags?.map((tag) => tag.name).join('、') || '未打标签'
              )
            }
          />
        </div>
      </section>

      {/* 3. 媒体规格 */}
      {asset.kind !== 'pack' && (
        <section className="space-y-2">
          <SectionTitle>媒体规格</SectionTitle>
          <div className="grid gap-1.5 sm:grid-cols-2">
            <Field label="格式" value={asset.mimeType || '未知'} />
            <Field label="体积" value={asset.sizeBytes != null ? formatBytes(asset.sizeBytes) : '未知'} />
            <Field label="时长" value={asset.durationSec ? formatDuration(asset.durationSec) : '未知'} />
            <Field label="音轨" value={asset.hasAudio == null ? '未知' : asset.hasAudio ? '有' : '无'} />
          </div>
        </section>
      )}

      {/* 4. 生成参数 */}
      {asset.generation && (
        <section className="space-y-2">
          <SectionTitle>生成参数</SectionTitle>
          <div className="grid gap-1.5 sm:grid-cols-2">
            <Field label="来源模块" value={asset.generation.sourceModule} />
            <Field label="上游任务" value={asset.generation.sourceTaskId} />
            <Field label="模型" value={asset.generation.model || asset.generation.modelId || '未记录'} />
            <Field label="尺寸 / 比例" value={[asset.generation.size, asset.generation.aspectRatio].filter(Boolean).join(' · ') || '未记录'} />
            <Field label="时长 / 张数" value={[asset.generation.durationSec ? `${asset.generation.durationSec}s` : '', asset.generation.count ? `${asset.generation.count} 张` : ''].filter(Boolean).join(' · ') || '未记录'} />
            <Field label="风格" value={asset.generation.stylePreset || '未记录'} />
            <Field label="种子" value={asset.generation.seed || '未记录'} />
            <Field label="积分" value={asset.generation.credits != null ? String(asset.generation.credits) : '未记录'} />
          </div>
          {asset.generation.prompt && (
            <div className="rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
              <div className="mb-1 text-[11px] text-ink-mute">最终提示词</div>
              <p className="whitespace-pre-wrap text-[12px] leading-relaxed text-ink-soft">
                {asset.generation.prompt}
              </p>
            </div>
          )}
          {Object.keys(asset.generation.promptSpec ?? {}).length > 0 && (
            <details className="rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
              <summary className="cursor-pointer text-[11.5px] text-ink-soft">查看完整参数</summary>
              <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap text-[11px] text-ink-mute">
                {JSON.stringify(
                  { promptSpec: asset.generation.promptSpec, configuration: asset.generation.configuration },
                  null,
                  2,
                )}
              </pre>
            </details>
          )}
        </section>
      )}

      {/* 5. 使用与引用 */}
      <section className="space-y-2">
        <SectionTitle>使用与引用</SectionTitle>
        {asset.references === null ? (
          <p className="text-[11.5px] text-ink-mute">
            引用检查当前不可用（注入口未接上），暂时无法判断能否彻底删除。
          </p>
        ) : asset.references?.length ? (
          <div className="flex flex-wrap gap-1">
            {asset.references.map((ref) => (
              <Chip key={ref} tone="warn">
                {ref}
              </Chip>
            ))}
          </div>
        ) : (
          <p className="text-[11.5px] text-ink-mute">没有模板 / 任务 / 封装在用它。</p>
        )}

        {!!asset.shareHistory?.length && (
          <ul className="space-y-1.5 border-t border-line-soft pt-2">
            {asset.shareHistory.map((item) => (
              <ShareHistoryRow key={item.id} item={item} />
            ))}
          </ul>
        )}
      </section>

      {/* 6. 归档信息 */}
      {asset.archive && (
        <section className="space-y-2">
          <SectionTitle>归档信息（仅本人与管理员可见）</SectionTitle>
          <div className="grid gap-1.5 sm:grid-cols-2">
            <Field label="存储键" value={<code className="break-all font-mono text-[11px]">{asset.archive.storageKey}</code>} />
            <Field label="SHA-256" value={<code className="break-all font-mono text-[11px]">{asset.archive.sha256}</code>} />
            <Field label="幂等键" value={asset.archive.sourceKey || '—'} />
            <Field label="引用计数" value={asset.archive.referenceCount != null ? String(asset.archive.referenceCount) : '未知'} />
          </div>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-1.5 border-t border-line-soft pt-3">
        {asset.kind !== 'pack' && asset.state !== 'draft' && (
          <Button size="sm" variant="secondary" icon={<Download size={13} />} onClick={download}>
            下载原件
          </Button>
        )}
        {asset.state === 'draft' && (
          <>
            <Button size="sm" variant="primary" onClick={() => onAction('approve', asset)}>
              入库
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onAction('reject', asset)}>
              驳回
            </Button>
          </>
        )}
        {asset.state === 'trash' ? (
          <>
            <Button size="sm" variant="secondary" onClick={() => onAction('restore', asset)}>
              恢复
            </Button>
            <Button size="sm" variant="danger" icon={<Trash2 size={13} />} onClick={() => onAction('purge', asset)}>
              彻底删除
            </Button>
          </>
        ) : (
          <>
            {offer === 'revoke' ? (
              <Button size="sm" variant="secondary" icon={<Undo2 size={13} />} onClick={() => onAction('revoke', asset)}>
                取消共享
              </Button>
            ) : asset.state === 'active' ? (
              <Button size="sm" variant="secondary" icon={<Share2 size={13} />} onClick={() => onAction('share', asset)}>
                共享到…
              </Button>
            ) : null}
            {offer === 'trash' && (
              <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => onAction('delete', asset)}>
                移入回收站
              </Button>
            )}
            {offer === 'force' && (
              <Button size="sm" variant="danger" icon={<Trash2 size={13} />} onClick={() => onAction('delete', asset)}>
                强制移入回收站
              </Button>
            )}
          </>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
          关闭
        </Button>
      </div>
    </div>
  );
}

function MediaImage({ asset }: { asset: LibraryAssetDetailOut }) {
  if (asset.coverUrl) {
    return <img src={asset.coverUrl} alt={asset.name} className="max-h-[420px] w-full bg-black object-contain" />;
  }
  return <MediaFallback icon={<ImageIcon size={22} />} loading={false} />;
}

function MediaFallback({ icon, loading }: { icon: ReactNode; loading: boolean }) {
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-1.5 text-ink-mute">
      {icon}
      <span className="text-[11.5px]">{loading ? '正在读取文件…' : '暂时取不到可预览的文件'}</span>
    </div>
  );
}

function PackMembers({
  asset,
  onOpenAsset,
}: {
  asset: LibraryAssetDetailOut;
  onOpenAsset: (id: string) => void;
}) {
  if (!asset.packItems?.length) {
    return (
      <div className="flex h-32 items-center justify-center gap-2 text-ink-mute">
        <Package size={20} />
        <span className="text-[11.5px]">这个封装还没有成员</span>
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2 p-2.5">
      {asset.packItems.map((item) => (
        <button
          key={`${item.assetId}-${item.position}`}
          type="button"
          onClick={() => onOpenAsset(item.assetId)}
          className="group rounded-lg border border-line-soft bg-panel p-1.5 text-left transition hover:border-primary-line"
        >
          <CoverImage src={item.coverUrl ?? undefined} seed={item.assetId} className="aspect-[4/3] rounded-md" />
          <div className="mt-1 flex items-center gap-1">
            <span className="truncate text-[11.5px] text-ink">{item.name || item.assetId}</span>
            <ArrowUpRight size={11} className="ml-auto shrink-0 text-ink-mute" />
          </div>
          <div className="truncate text-[10.5px] text-ink-mute">
            {KIND_LABEL[item.kind]}
            {item.role ? ` · ${item.role}` : ''}
          </div>
        </button>
      ))}
    </div>
  );
}

function ShareHistoryRow({ item }: { item: ShareHistoryItem }) {
  const action = item.action === 'share' ? '共享' : '撤销共享';
  const target = item.target === 'team' ? '团队' : item.groupName || '小组';
  const status =
    item.status === 'pending' ? '待审' : item.status === 'approved' ? '已通过' : item.status === 'rejected' ? '已驳回' : item.status;
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-ink-soft">
      <Chip tone={item.action === 'share' ? 'primary' : 'warn'}>{action}</Chip>
      <span>{target}</span>
      <span className="text-ink-mute">·</span>
      <span>{item.applicantName || item.applicantId}</span>
      <span className="text-ink-mute">→ {status}</span>
      {item.reviewerName && <span className="text-ink-mute">（{item.reviewerName}）</span>}
      <span className="ml-auto text-ink-mute">{formatDateTime(item.decidedAt || item.createdAt)}</span>
      {item.reason && <span className="w-full text-ink-mute">理由：{item.reason}</span>}
    </li>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-[12.5px] font-semibold text-ink">{children}</h3>;
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-lg bg-panel-soft px-2.5 py-1.5">
      <span className="w-[70px] shrink-0 text-[11px] text-ink-mute">{label}</span>
      <span className="min-w-0 flex-1 text-[11.5px] text-ink-soft">{value}</span>
    </div>
  );
}
