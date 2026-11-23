import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Copy,
  Download,
  FileAudio,
  FileText,
  Film,
  History,
  Image as ImageIcon,
  Layers,
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
  Notice,
  Skeleton,
} from '@/shared/ui';
import { useCan } from '@/shared/access/use-access';
import { cn } from '@/shared/lib/cn';
import { formatBytes, formatDateTime, formatDuration } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import type {
  LibraryAssetDetailOut,
  LibraryAssetOut,
  LibraryTagStatOut,
  ShareHistoryItem,
} from '@/shared/api/schema';
import {
  KIND_LABEL,
  ORIGIN_LABEL,
  STATE_LABEL,
  VISIBILITY_LABEL,
  deleteOffer,
  displayName,
  isTrashExpired,
  trashDaysLeft,
} from '@/entities/library/model/types';
import { tagLabel, tagOptions } from '@/entities/library/model/tagTree';
import { PromptRefs } from '@/entities/library/ui/PromptRefs';
import {
  useLibraryDetail,
  useLibraryLargePreview,
  useLibraryMedia,
  usePatchAsset,
  useReadText,
  useSaveAssetFile,
} from '@/entities/library/api/library.queries';
import type { LibraryCardAction } from './LibraryCard';
import { PackCreateModal, type EditingPack } from './PackCreateModal';
import { TextAssetModal } from './TextAssetModal';
import { TextVersionHistory } from './TextVersionHistory';

export interface LibraryDetailProps {
  assetId: string | null;
  tags: LibraryTagStatOut[];
  viewer: { id: string; canDeleteAny: boolean };
  canUpdate: boolean;
  onClose: () => void;
  onAction: (action: LibraryCardAction, asset: LibraryAssetOut) => void;
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
  /* 打标失败后要把本地标签对齐到服务端真值：重新拉一次，而不是回填旧快照 */
  const refetchAsset = async () => (await query.refetch())?.data;

  if (!assetId) return null;

  return (
    <Modal
      open
      onClose={props.onClose}
      title={asset ? displayName(asset.name) : '资产详情'}
      width={880}
    >
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
        <DetailBody {...props} asset={asset} refetchAsset={refetchAsset} />
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
  refetchAsset,
}: LibraryDetailProps & {
  asset: LibraryAssetDetailOut;
  /** 重新拉一次服务端资产：打标失败后要把本地态对齐到**服务端真值** */
  refetchAsset: () => Promise<LibraryAssetDetailOut | undefined>;
}) {
  const patch = usePatchAsset();
  const saveFile = useSaveAssetFile();
  const canCreate = useCan('library:create');
  const offer = deleteOffer(asset, viewer);
  /* 改名与打标签在服务端只认 owner（或有 delete:any）。别人共享给我的素材，
     我能读到但改不了——按钮挂在这里就要跟着同一个判定，否则点下去是一句
     「资产不存在」，看起来像这条记录丢了。 */
  const canEdit = canUpdate && (asset.ownerId === viewer.id || viewer.canDeleteAny);
  /* 封装的编辑走 `PATCH /library/packs`，它要的是 `library:create`（建包那条链），
     不是 `library:update`。以前这里借用了 `canEdit`，于是「有 update、没有 create」
     的角色会看到按钮然后吃 403 —— 判据跟着接口走。 */
  const canEditPack =
    canCreate && (asset.ownerId === viewer.id || viewer.canDeleteAny);
  const days = trashDaysLeft(asset.trashExpiresAt);
  const hasMedia = asset.kind === 'video' || asset.kind === 'audio';
  const media = useLibraryMedia(asset.id, hasMedia);
  const [renaming, setRenaming] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  /* 编辑封装的弹窗叠在详情之上：两者都有关闭按钮，Modal 有栈管理 Escape 顺序。 */
  const [editingPack, setEditingPack] = useState<EditingPack | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  /* 文本资产没有可预览的媒体字节：正文走 /text 单独取，纯文本展示 */
  const isText = asset.kind === 'text';
  const text = useReadText(asset.id, isText);
  /* 输入框里只有主体名：后缀是文件的属性，改不了也不该看起来能改。
     保存时后端 `keep_extension` 会把后缀拼回去，所以这里传裸名是对的。 */
  const [name, setName] = useState(displayName(asset.name));
  const [tagIds, setTagIds] = useState<string[]>(asset.tags?.map((tag) => tag.id) ?? []);

  /* 换一条资产时把本地编辑态重置，否则会把上一条的名字带到下一条。
     **只在 asset.id 变化时重置**：详情会被窗口重新聚焦、写操作后的失效重新
     拉取，每次响应都是新的 tags 数组引用——按 name/tags 做依赖，用户刚输入
     的名字会被一次无关的刷新冲掉，编辑态还整个关掉（用户以为还在编辑，
     其实输入框已经没了）。 */
  const assetIdRef = useRef(asset.id);
  useEffect(() => {
    if (assetIdRef.current === asset.id) return;
    assetIdRef.current = asset.id;
    setName(displayName(asset.name));
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
   *
   * 「不带」= 字段不出现（序列化时被丢掉），后端按 `None` 处理＝明确不校验；
   * 0 不是合法版本，不能再拿它当「不校验」的替身。
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
          expectedVersion: options?.versioned === false ? undefined : asset.stateVersion,
        },
      },
      {
        onSuccess: () => {
          setRenaming(false);
          toast('已保存');
        },
        onError: (error, variables) => {
          toast(error.message, 'error');
          /* 打标被拒（标签被删 / 变成有子级的分组）时把本地态拉回服务端：
             否则界面显示「已打上」实际没打上，下一次保存还会把这枚坏标签
             一起提交。

             这里要**重新拉一次服务端**，而不是回填本地的 `asset` —— 那只是
             上一次 invalidate 时的快照：坏标签若在这次保存之前就已经被删，
             快照里照样有它，回填等于没修（REVIEW_2026-11-23 一般#10）。 */
          if (variables.body.tagIds) {
            void refetchAsset().then((fresh) => {
              // 拉不到就回落到**上一次确认过的服务端标签**（`asset.tags`）：
              // 宁可停在旧值，也不要把用户刚勾上的坏标签留在界面上假装已生效
              // （那正是这次要修的 bug），更不要清空成「没有标签」。
              const confirmed = fresh?.tags ?? asset.tags;
              setTagIds(confirmed?.map((tag) => tag.id) ?? []);
            });
          }
        },
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

  /** 正文还没读出来时不能复制：复制到的是空串，提示「已复制全文」是骗人 */
  const textReady = !text.isPending && !text.isError && !!text.data?.content;

  /** 复制全文：剪贴板可能被浏览器策略禁用（非 HTTPS / 无权限），失败要给人一条可执行的兜底 */
  const copyText = async () => {
    if (!textReady) {
      toast('正文还没读出来，稍后再试', 'error');
      return;
    }
    try {
      await navigator.clipboard.writeText(text.data?.content ?? '');
      toast('已复制全文');
    } catch {
      toast('复制失败，请在正文中手动全选复制', 'error');
    }
  };

  return (
    <div className="max-h-[76vh] space-y-3 overflow-auto p-[18px]">
      {/* 1. 媒体区 */}
      <section className="overflow-hidden rounded-card border border-line-soft bg-panel-soft">
        {asset.kind === 'text' ? (
          <div className="space-y-2 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-body text-ink-soft">
                <FileText size={14} /> 正文（纯文本）
              </span>
              <Button
                size="sm"
                variant="secondary"
                icon={<Copy size={12} />}
                disabled={!textReady}
                onClick={() => void copyText()}
              >
                复制全文
              </Button>
            </div>
            {/* 纯文本，不做 Markdown → HTML 渲染，避免把用户正文当页面执行 */}
            <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap rounded-lg border border-line-soft bg-white px-2.5 py-2 text-body leading-relaxed text-ink-soft">
              {text.isPending
                ? '正在读取正文…'
                : text.isError
                  ? '正文读取失败'
                  : text.data?.content || '（空文本）'}
            </pre>
          </div>
        ) : asset.kind === 'pack' ? (
          <PackMedia asset={asset} />
        ) : asset.kind === 'image' ? (
          <MediaImage asset={asset} />
        ) : asset.kind === 'video' ? (
          media.url ? (
            <video
              src={media.url}
              controls
              poster={asset.coverUrl ?? undefined}
              aria-label={displayName(asset.name)}
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
            <audio src={media.url} controls aria-label={displayName(asset.name)} className="w-full">
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
          <Notice tone="error">驳回原因：{asset.rejectReason}</Notice>
        )}

        <div className="grid gap-1.5 sm:grid-cols-2">
          <Field label="所有者" value={asset.ownerName || asset.ownerId} />
          <Field label="创建时间" value={formatDateTime(asset.createdAt)} />
          <Field
            label="名称"
            value={
              renaming ? (
                <span className="flex flex-col gap-1">
                  <span className="flex items-center gap-1">
                    <input
                      ref={nameInput}
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      className="h-7 w-full rounded-lg border border-primary-line bg-white px-2 text-body text-ink outline-none"
                      aria-label="资产名称"
                      /* 这条约束（改完后缀还在）只在**编辑态**说，平时看不到后缀。
                         原来是一行常驻小字，占着编辑区一行 ——
                         改成输入框的 `title`（悬浮才看）与 `placeholder`。 */
                      title="只改名称主体，格式后缀由文件本身决定"
                      placeholder="只改主体，后缀保留"
                    />
                    <Button size="sm" variant="primary" onClick={() => name.trim() && save({ name: name.trim() })}>
                      保存
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setRenaming(false)}>
                      取消
                    </Button>
                  </span>
                </span>
              ) : (
                <span className="flex items-center gap-1">
                  <span className="truncate" title={asset.name}>
                    {displayName(asset.name)}
                  </span>
                  {canEdit && asset.state !== 'trash' && (
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
              canEdit && asset.state !== 'trash' ? (
                <MultiSelect
                  options={tagOptions(tags)}
                  value={tagIds}
                  onChange={(values) => {
                    setTagIds(values);
                    save({ tagIds: values }, { versioned: false });
                  }}
                  placeholder="未打标签"
                  emptyText="还没有可用标签"
                  ariaLabel="编辑标签"
                />
              ) : (
                asset.tags?.map(tagLabel).join('、') || '未打标签'
              )
            }
          />
        </div>
      </section>

      {/* 3. 媒体规格
          封装整块跳过：它没有自己的字节，也没有格式、时长、音轨 ——
          把这些显示成「未知 / 0 B / 无」，读起来像是这条记录坏了一半，
          而实际信息是「它装了几张图」，那句在媒体区已经说了。 */}
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
              <div className="mb-1 text-body text-ink-mute">最终提示词</div>
              {/* 快照里存的是注入正文之后的提示词，`{{Mixed N}}` 还在：按
                  `referenceLibraryIds` 就地换成缩略图，别让用户读到编号。 */}
              <PromptRefs
                value={asset.generation.prompt}
                referenceIds={asset.generation.referenceLibraryIds}
                className="block text-body leading-relaxed text-ink-soft"
              />
            </div>
          )}
          {Object.keys(asset.generation.promptSpec ?? {}).length > 0 && (
            <details className="rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
              <summary className="cursor-pointer text-body text-ink-soft">查看完整参数</summary>
              <pre className="mt-1.5 max-h-56 overflow-auto whitespace-pre-wrap text-body text-ink-mute">
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
          <p className="text-body text-ink-mute">
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
          <p className="text-body text-ink-mute">没有模板 / 任务在用它。</p>
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
          <SectionTitle>归档信息</SectionTitle>
          <div className="grid gap-1.5 sm:grid-cols-2">
            <Field label="存储键" value={<code className="break-all font-mono text-body">{asset.archive.storageKey}</code>} />
            <Field label="SHA-256" value={<code className="break-all font-mono text-body">{asset.archive.sha256}</code>} />
            <Field label="幂等键" value={asset.archive.sourceKey || '—'} />
            <Field label="引用计数" value={asset.archive.referenceCount != null ? String(asset.archive.referenceCount) : '未知'} />
          </div>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-1.5 border-t border-line-soft pt-3">
        {/* 只在 active 出现：回收站里后端一律拒绝取件，按钮点了也是失败。
            封装没有原文件，但它能进打包任务（后端展开成员再压 zip）——
            同一个按钮两条链路，用户看到的都是「下了」。 */}
        {asset.state === 'active' && (
          <Button
            size="sm"
            variant="secondary"
            icon={<Download size={13} />}
            onClick={() => (asset.kind === 'pack' ? onAction('download', asset) : download())}
          >
            {asset.kind === 'pack' ? '打包下载' : '下载原件'}
          </Button>
        )}
        {/* 封装的编辑入口。成员与名字都可改 —— 建完只能删了重建太笨。 */}
        {asset.kind === 'pack' && asset.state === 'active' && canEditPack && (
          <Button
            size="sm"
            variant="secondary"
            icon={<Layers size={13} />}
            onClick={() =>
              setEditingPack({
                id: asset.id,
                name: asset.name,
                members: asset.packItems ?? [],
              })
            }
          >
            编辑封装
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
        {isText && asset.state === 'active' && (
          <>
            {canEdit && (
              <Button
                size="sm"
                variant="secondary"
                icon={<Pencil size={13} />}
                onClick={() => setEditorOpen(true)}
              >
                编辑
              </Button>
            )}
            <Button
              size="sm"
              variant="secondary"
              icon={<History size={13} />}
              onClick={() => setHistoryOpen(true)}
            >
              版本历史
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
          关闭
        </Button>
      </div>

      {/* 详情里就地打开编辑与版本历史：两个弹窗都用 Portal，不受上面滚动容器影响 */}
      {editorOpen && (
        <TextAssetModal
          open
          mode="edit"
          assetId={asset.id}
          assetName={displayName(asset.name)}
          onClose={() => setEditorOpen(false)}
        />
      )}
      {editingPack && (
        <PackCreateModal
          open
          pack={editingPack}
          onClose={() => setEditingPack(null)}
        />
      )}
      {historyOpen && (
        <TextVersionHistory
          open
          assetId={asset.id}
          assetName={displayName(asset.name)}
          onClose={() => setHistoryOpen(false)}
        />
      )}
    </div>
  );
}

/**
 * 封装的「媒体区」= 成员网格。
 *
 * 包本身没有字节（`storage_key` 为空，`url` 指向一个不存在的文件），所以它
 * **不能**走播放器/大图那条分支 —— 否则详情页会出现一个 src 指向 404 的
 * `<audio>`，看起来像资产坏了。
 *
 * 成员不再单列（进了包的素材从个人库消失），这里就是使用者**唯一**能看清
 * 「包里是哪几张」的地方，所以每张都要有缩略图和名字，而不是只报个数。
 */
function PackMedia({ asset }: { asset: LibraryAssetDetailOut }) {
  const members = asset.packItems ?? [];

  if (members.length === 0) {
    /* 空包在建包接口里就被拦掉了，走到这里说明成员被逐个彻底删除过。
       说清是「空」而不是「加载失败」：两者在界面上长得一样，处理方式完全不同。 */
    return (
      <div className="flex flex-col items-center gap-2 p-8 text-center text-body text-ink-mute">
        <Layers size={22} />
        这个封装里已经没有素材了
      </div>
    );
  }

  return (
    <div className="space-y-2 p-3">
      <span className="flex items-center gap-1.5 text-body text-ink-soft">
        <Layers size={14} /> 包含 {members.length} 张素材
      </span>
      <ul className="grid max-h-[420px] grid-cols-2 gap-2 overflow-auto sm:grid-cols-3">
        {members.map((member) => (
          <li
            key={member.id}
            className={cn(
              'overflow-hidden rounded-lg border border-line-soft bg-panel',
              /* 回收站里的成员仍然列出来、只是变灰：静默少一张会让人以为
                 「这个包本来就这样」，而真去用的时候取图会当场报缺。 */
              member.deleted && 'opacity-55',
            )}
          >
            <CoverImage
              src={member.coverUrl ?? undefined}
              seed={member.id}
              className="aspect-[4/3]"
            />
            <div className="flex items-center gap-1 px-1.5 py-1">
              <span className="min-w-0 flex-1 truncate text-xs text-ink-soft" title={member.name}>
                {displayName(member.name)}
              </span>
              {member.deleted && (
                <span className="shrink-0 rounded bg-danger-soft px-1 text-micro leading-4 text-danger-deep">
                  已删除
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MediaImage({ asset }: { asset: LibraryAssetDetailOut }) {
  /* 详情页给的是**大图**（长边 1280），不是封面。
     封面只有 360×240 —— 放在 880px 宽的详情弹窗里是几百像素的一方糊块，
     产品图的走线、缝线、logo 全看不清。用户点进来就是为了看细节，结果
     还是得下载原件才看得见，那这个详情弹窗就没意义了。
     大图走 `/large`（惰性派生 + 按摘要缓存，几百 KB），是这里最合适的一档：
     清晰度与模板库悬浮那张一致，而详情弹窗比那个浮层宽得多（880 vs 320），
     同一张 1280px 的图在这里看得更细。

     **降级链：大图 → 封面 → 原图**。三套字节一条记录：封面对象可能丢了
     （`previews/*.jpg` 被清、换桶、MinIO 重建），大图是派生的（依赖原图能读），
     原图才是最后的真相。任何一档失败就退下一档，全挂了才说「暂时取不到」。
     换素材时必须复位，否则上一张的失败态会带到下一张。

     **enabled 恒为 true**：本组件只在 `kind === 'image'` 的分支里渲染，
     走到这里就一定是图片。这个 true 不是在防什么 —— 文本资产走的是上面
     「正文」那条分支，连这个组件都进不去。 */
  const large = useLibraryLargePreview(asset.id, true);
  const [coverBroken, setCoverBroken] = useState(false);
  useEffect(() => setCoverBroken(false), [asset.id, asset.coverUrl]);

  if (large.url) {
    return (
      <img
        src={large.url}
        alt={displayName(asset.name)}
        className="max-h-[560px] w-full bg-black object-contain"
      />
    );
  }

  /* 大图还在路上（或是派生失败）：先给封面，页面不空着。 */
  if (asset.coverUrl && !coverBroken) {
    return (
      <img
        src={asset.coverUrl}
        alt={displayName(asset.name)}
        onError={() => setCoverBroken(true)}
        className="max-h-[560px] w-full bg-black object-contain"
      />
    );
  }

  return <OriginalImage asset={asset} />;
}

function OriginalImage({ asset }: { asset: LibraryAssetDetailOut }) {
  const media = useLibraryMedia(asset.id, true);
  if (media.url) {
    return (
      <img
        src={media.url}
        alt={displayName(asset.name)}
        className="max-h-[560px] w-full bg-black object-contain"
      />
    );
  }
  return <MediaFallback icon={<ImageIcon size={22} />} loading={media.isPending} />;
}

function MediaFallback({ icon, loading }: { icon: ReactNode; loading: boolean }) {
  return (
    <div className="flex h-40 flex-col items-center justify-center gap-1.5 text-ink-mute">
      {icon}
      <span className="text-body">{loading ? '正在读取文件…' : '暂时取不到可预览的文件'}</span>
    </div>
  );
}

function ShareHistoryRow({ item }: { item: ShareHistoryItem }) {
  const action = item.action === 'share' ? '共享' : '撤销共享';
  const target = item.target === 'team' ? '团队' : item.groupName || '小组';
  const status =
    item.status === 'pending' ? '待审' : item.status === 'approved' ? '已通过' : item.status === 'rejected' ? '已驳回' : item.status;
  return (
    <li className="flex flex-wrap items-center gap-1.5 text-body text-ink-soft">
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
  return <h3 className="text-lead font-semibold text-ink">{children}</h3>;
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-lg bg-panel-soft px-2.5 py-1.5">
      <span className="w-[70px] shrink-0 text-body text-ink-mute">{label}</span>
      <span className="min-w-0 flex-1 text-body text-ink-soft">{value}</span>
    </div>
  );
}
