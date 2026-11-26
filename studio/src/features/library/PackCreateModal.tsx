import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Layers, Plus, X } from 'lucide-react';
import { Button, CoverImage, Modal } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import type { LibraryAssetOut, PackMemberOut } from '@/shared/api/schema';
import { useCreatePack, useUpdatePack } from '@/entities/library/api/library.queries';
import { LibraryPicker } from '@/entities/library/ui/LibraryPicker';
import { displayName } from '@/entities/library/model/types';

/**
 * 编辑模式下要改的那个包。没有它就是「新建」。
 *
 * 成员用 `PackMemberOut`（详情里给的就是这个形状）—— 这里只用到 id / 名字 /
 * 封面 / 标签 id，不为了复用而把整条 `LibraryAssetOut` 造出来。
 */
export interface EditingPack {
  id: string;
  name: string;
  members: PackMemberOut[];
}

/** 把包成员转成选择器认的形状：只补它真正读的几个字段。 */
function memberToAsset(member: PackMemberOut): LibraryAssetOut {
  return {
    id: member.id,
    name: member.name,
    kind: (member.kind as LibraryAssetOut['kind']) ?? 'image',
    origin: 'upload',
    ownerId: '',
    visibility: 'personal',
    state: 'active',
    sizeBytes: 0,
    createdAt: '',
    coverUrl: member.coverUrl ?? null,
    /* 只用来算「共同标签」，`name` 不参与判断 */
    tags: (member.tagIds ?? []).map((id) => ({id, name: ''})),
  };
}

export interface PackCreateModalProps {
  open: boolean;
  /** 传入 = 编辑已有封装（名字与成员预填，提交走 PATCH） */
  pack?: EditingPack;
  /**
   * 已选好的素材（从批量条进来时就是用户刚勾的那几条）。
   *
   * 传**完整对象**而不是 id：弹窗要立刻画出缩略图，只有 id 就得再查一次，
   * 而用户点开弹窗到看见自己选的东西之间不该有一次加载。
   */
  preset?: LibraryAssetOut[];
  onClose: () => void;
  /** 建完之后回调（外面据此清空批量选择） */
  onCreated?: () => void;
  /** 改完之后回调（外面据此关闭详情或重新拉取） */
  onUpdated?: () => void;
}

/**
 * 新建封装：给一组素材起个名，打成 `kind=pack` 的一条资产。
 *
 * ## 为什么成员不用「同产品」这个概念去校验
 *
 * 系统判断不出「这两张图是不是同一件衣服」—— 颜色只存在文件名里、产品编号
 * 只是个标签。所以这里**不猜**：成员由使用者挑，系统只守两条它真能判的：
 * 至少一张、一张素材只能进一个封装（后一条由后端 409 兜底）。
 *
 * ## 名字留空为什么允许
 *
 * 强行必填的结果是一堆「新建封装 (1)」。留空时后端记成「未命名封装」，
 * 至少是句人话，而且用户可以之后改名（改名走既有 PATCH）。
 */
export function PackCreateModal({
  open,
  pack,
  preset = [],
  onClose,
  onCreated,
  onUpdated,
}: PackCreateModalProps) {
  const create = useCreatePack();
  const update = useUpdatePack();
  const editing = !!pack;
  const [name, setName] = useState('');
  const [assets, setAssets] = useState<LibraryAssetOut[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState('');

  /* 每次打开都从 `preset` 重新铺：上一次建完留在里面的东西不该带到这一次，
     而 `preset` 本身是「这次要打包的那几条」。只在 open 变化时读，避免
     调用方每次渲染新建一个数组把用户刚删掉的项又塞回来。 */
  useEffect(() => {
    if (!open) return;
    setName(pack?.name ?? '');
    setAssets(pack ? pack.members.map(memberToAsset) : preset);
    setError('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /**
   * 成员共有的标签。
   *
   * **一个都没有就打不成封装**：封装的意思是「同一件东西的几个视角」，而这件事
   * 在系统里唯一能判的信号就是标签（`6036` 这类产品编号）。后端也会 400，
   * 但让人选完、填完名字、点了按钮才被拒，比一开始就说清差得多。
   */
  const commonTags = useMemo(() => {
    const [first, ...rest] = assets;
    if (!first) return [];
    const sets = rest.map((item) => new Set((item.tags ?? []).map((tag) => tag.id)));
    return (first.tags ?? []).filter((tag) => sets.every((set) => set.has(tag.id)));
  }, [assets]);

  const noCommonTag = assets.length > 0 && commonTags.length === 0;

  const remove = (id: string) => {
    setAssets((current) => current.filter((item) => item.id !== id));
    setError('');
  };

  const submit = async () => {
    if (!assets.length) return;
    setError('');
    try {
      if (pack) {
        const saved = await update.mutateAsync({
          id: pack.id,
          name: name.trim(),
          assetIds: assets.map((item) => item.id),
        });
        toast(`已更新封装「${saved.name}」，${saved.memberCount} 张素材`);
        onUpdated?.();
      } else {
        const created = await create.mutateAsync({
          name: name.trim(),
          assetIds: assets.map((item) => item.id),
        });
        toast(`已创建封装「${created.name}」，${created.memberCount} 张素材收进去了`);
        onCreated?.();
      }
      onClose();
    } catch (failure) {
      /* 就地显示而不是 toast：这条错误要让人对着弹窗里的素材逐条核对
         （比如「这张已经在别的封装里」），飘一下就走的信息读不完。 */
      setError(failure instanceof Error ? failure.message : '创建失败，请重试');
    }
  };

  return (
    <>
      <Modal open={open} onClose={onClose} title={editing ? '编辑封装' : '新建封装'} width={560}>
        <div className="space-y-3 p-[18px]">
          <label className="block space-y-1">
            <span className="text-meta text-ink-soft">封装名称</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="如「6036 黑」；留空则叫「未命名封装」"
              aria-label="封装名称"
              className="h-8 w-full rounded-lg border border-line bg-panel px-2.5 text-body text-ink outline-none transition-colors placeholder:text-ink-mute hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10"
            />
          </label>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-meta text-ink-soft">
                <Layers size={13} /> 已选 {assets.length} 张
              </span>
              <Button
                size="sm"
                variant="secondary"
                icon={<Plus size={12} />}
                onClick={() => setPickerOpen(true)}
              >
                选择素材
              </Button>
            </div>

            {assets.length ? (
              <ul className="grid max-h-[220px] grid-cols-4 gap-2 overflow-auto pr-1">
                {assets.map((asset) => (
                  <li
                    key={asset.id}
                    className="group relative overflow-hidden rounded-lg border border-line-soft bg-panel"
                  >
                    <CoverImage
                      src={asset.coverUrl ?? undefined}
                      seed={asset.id}
                      className="aspect-[4/3]"
                    />
                    <div className="truncate px-1.5 py-1 text-micro text-ink-soft" title={asset.name}>
                      {displayName(asset.name)}
                    </div>
                    <button
                      type="button"
                      onClick={() => remove(asset.id)}
                      aria-label={`移除 ${displayName(asset.name)}`}
                      title="移出这一批"
                      className={cn(
                        'absolute right-1 top-1 grid size-[18px] place-items-center rounded-full',
                        'bg-[rgba(12,24,30,.62)] text-white transition-colors hover:bg-danger-deep',
                        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                      )}
                    >
                      <X size={11} strokeWidth={2.5} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              /* 空态是死胡同里的门：说清下一步要做什么，而不是只说「还没有素材」 */
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="flex w-full flex-col items-center gap-1.5 rounded-lg border border-dashed border-line-strong bg-panel-soft px-3 py-8 text-ink-mute transition-colors hover:border-primary-line hover:text-primary-deep"
              >
                <Layers size={20} />
                <span className="text-meta">点这里选几张同产品的素材</span>
              </button>
            )}
          </div>

          {noCommonTag && (
            <p className="flex items-start gap-1.5 rounded-lg bg-warn-soft px-2.5 py-2 text-meta text-warn-deep">
              <AlertCircle size={13} className="mt-0.5 shrink-0" />
              这些素材没有共同的标签，打不成一个封装。先给它们打上同一个标签（比如产品编号）再来。
            </p>
          )}

          {error && (
            <p className="flex items-start gap-1.5 rounded-lg bg-danger-soft px-2.5 py-2 text-meta text-danger-deep">
              <AlertCircle size={13} className="mt-0.5 shrink-0" />
              {error}
            </p>
          )}

          <div className="flex items-center justify-end gap-1.5 border-t border-line-soft pt-3">
            <Button size="sm" variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={
                !assets.length || noCommonTag || create.isPending || update.isPending
              }
              onClick={() => void submit()}
            >
              {editing
                ? update.isPending
                  ? '保存中…'
                  : '保存'
                : create.isPending
                  ? '创建中…'
                  : '创建封装'}
            </Button>
          </div>
        </div>
      </Modal>

      {/* 选择器里把「已经属于别的封装」的素材标灰并写出原因：
          让人选到最后才被 409 打回，比一开始就说明难解释得多。 */}
      <LibraryPicker
        open={pickerOpen}
        title="选择封装成员"
        multiple
        kinds={['image', 'video']}
        value={assets.map((item) => item.id)}
        valueItems={assets}
        unavailableReason={(asset) => {
          /* 编辑时，本来就在**这个包**里的成员当然可以再选回来 ——
             按 `packId` 判而不是名字（重名的包是存在的）。 */
          if (pack && asset.packId === pack.id) return null;
          if (!asset.packName || assets.some((item) => item.id === asset.id)) return null;
          return `已在封装「${asset.packName}」里`;
        }}
        onClose={() => setPickerOpen(false)}
        onConfirm={(picked) => {
          setAssets(picked);
          setPickerOpen(false);
          setError('');
        }}
      />
    </>
  );
}
