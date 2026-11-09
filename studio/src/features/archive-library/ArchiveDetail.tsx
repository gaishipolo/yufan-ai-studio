import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import { ARCHIVE_LABELS, archivePreviewUrl, archiveStreamUrl, type ArchivedAsset } from '@/entities/asset/model/archive.types';
import { replacementFitness } from '@/entities/asset/model/archive-fit';
import { complianceTone, COMPLIANCE_HINTS, COMPLIANCE_LABELS, ORIGIN_LABELS } from '@/entities/asset/model/lifecycle';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';
import { useAssetTags, useSetAssetTags, useTagCatalog } from '@/entities/asset/api/tag.queries';
import { useDeriveFromAsset } from '@/entities/asset/api/lineage.queries';
import { AssetLineage } from './AssetLineage';

interface ArchiveDetailProps {
  asset: ArchivedAsset | null;
  onClose: () => void;
}

/**
 * 素材详情：**右侧滑出浮层，不是侧栏**。
 *
 * 侧栏会永久占掉网格宽度，而这个页面的主任务是浏览——为了看一眼详情
 * 就少一列卡片，代价是每次浏览都在付。浮层只在需要时出现，关掉就回到满宽。
 *
 * 内容顺序按"用户打开它想问什么"排：
 * ① 这是哪张（大图 + 规格）→ ② 它怎么来的（生成血缘）→ ③ 它被谁用了
 * → ④ 我能对它做什么（标签 / 合规 / 衍生 / 下载）。
 * 前三段是只读事实，最后一段才是动作。
 */
export function ArchiveDetail({asset, onClose}: ArchiveDetailProps) {
  return <Modal open={!!asset} onClose={onClose} placement="right" width={520} title={asset?.name}>
    {asset && <DetailBody asset={asset} />}
  </Modal>;
}

function DetailBody({asset}: {asset: ArchivedAsset}) {
  const navigate = useNavigate();
  const derive = useDeriveFromAsset();
  const fitness = replacementFitness(asset);
  const playback = asset.type === 'video' ? archivePreviewUrl(archiveStreamUrl(asset.url)) : undefined;
  const specs: [string, string][] = [
    ['分类', ARCHIVE_LABELS[asset.category]],
    ['格式', asset.mimeType || '未知'],
    ['体积', formatBytes(asset.sizeBytes)],
    ['尺寸', asset.width && asset.height ? `${asset.width}×${asset.height}` : '未知'],
    ['时长', asset.duration ? `${asset.duration.toFixed(1)} 秒` : '未知'],
    ['音轨', asset.hasAudio ? '有' : '无'],
    ['来源', ORIGIN_LABELS[asset.origin ?? 'upload'] ?? asset.origin ?? '上传'],
    ['创建时间', formatDateTime(asset.createdAt)],
  ];

  /**
   * 提取提示词：**真的可用**。
   *
   * 它只把提示词带到生图页，不绑参考图——因为生图接口目前只收
   * prompt / size / count / model，没有参考图入参。绑不进去的东西就不假装绑，
   * 否则用户会以为"参考图已经在生效"，而结果里根本没有它。
   */
  const extract = async () => {
    try {
      const intent = await derive.mutateAsync({assetId: asset.id, mode: 'prompt_extract'});
      if (!intent.prompt) {
        toast('这条配方的提示词是空的', 'error');
        return;
      }
      navigate(`/image?prompt=${encodeURIComponent(intent.prompt)}`);
      toast('提示词已带到生图页，可以改完再生成', 'success');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  };

  return <div className="space-y-4 p-4">
    <ArchivedMedia url={asset.url} previewUrl={playback} type={asset.type} name={asset.name} downloadAllowed={asset.downloadAllowed} />

    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
      {specs.map(([label, value]) => <div key={label}>
        <dt className="text-ink-mute">{label}</dt>
        <dd className="mt-0.5 break-all text-ink-soft">{value}</dd>
      </div>)}
    </dl>

    {asset.category === 'source' && <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-ink-mute">替换适用性</span>
      {fitness.ok ? <Chip tone="ok">可用于替换</Chip> : <Chip tone="warn">{fitness.reason}</Chip>}
    </div>}

    <section className="space-y-2 border-t border-line-soft pt-3">
      <h3 className="text-[11.5px] font-semibold text-ink">生成血缘</h3>
      <AssetLineage assetId={asset.id} hasRecipe={!!asset.recipeId} />
    </section>

    <section className="space-y-2 border-t border-line-soft pt-3">
      <h3 className="text-[11.5px] font-semibold text-ink">标签</h3>
      <AssetTags assetId={asset.id} />
    </section>

    <section className="space-y-1.5 border-t border-line-soft pt-3">
      <h3 className="text-[11.5px] font-semibold text-ink">合规</h3>
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={complianceTone(asset.complianceStatus)}>
          {COMPLIANCE_LABELS[asset.complianceStatus ?? 'pending'] ?? asset.complianceStatus}
        </Chip>
        <span className="text-[11px] text-ink-mute">
          {COMPLIANCE_HINTS[asset.complianceStatus ?? 'pending'] ?? ''}
        </span>
      </div>
    </section>

    <section className="space-y-2 border-t border-line-soft pt-3">
      <h3 className="flex items-center gap-1 text-[11.5px] font-semibold text-ink">
        <Sparkles size={12} />用这张素材
      </h3>
      <div className="flex flex-wrap gap-1.5">
        {/* 三条入口分开：它们回填的输入位不同，合成一个"衍生"按钮会让
            用户点完才发现"我要的是首帧，它给了图生图参考" */}
        <Button size="sm" disabled title="生图接口目前只收提示词/尺寸/张数/模型，还没有参考图入参">
          用作图生图参考
        </Button>
        <Button size="sm" disabled title="生成侧还没有首帧入参；视频请先截一帧再上传">
          用作视频首帧
        </Button>
        <Button size="sm" loading={derive.isPending} disabled={!asset.recipeId}
          onClick={() => void extract()}>
          提取提示词微调
        </Button>
      </div>
      <p className="text-[11px] text-ink-mute">
        {asset.recipeId
          ? '提取提示词只把文字带到生图页，不会替你提交，也不扣费。'
          : '这条素材没有生成配方（上传件），提取不了提示词。'}
      </p>
      <p className="text-[11px] text-ink-mute">
        前两条入口等生成页接上参考图入参后开放；现在点了也不会生效，所以先禁用而不是假装能跳。
      </p>
    </section>

    {asset.path && <details className="rounded-lg border border-line p-3 text-xs">
      <summary className="cursor-pointer text-ink-soft">归档信息</summary>
      <p className="mt-2 break-all text-ink-mute">存储键：{asset.path}</p>
      <p className="mt-1 break-all text-ink-mute">SHA-256：{asset.sha256}</p>
      <p className="mt-1 text-ink-soft">字节按摘要存放，同一份文件在归档里只占一份空间。</p>
    </details>}
  </div>;
}

/**
 * 标签：读一条、整体替换一条。
 *
 * 只在这里做编辑，列表里不做——列表的卡片密度已经到顶，再放一个多选词表
 * 会把"浏览"变成"打标"。而打标本身是低频动作，放进浮层刚好。
 *
 * 保存是**整体替换**（空数组 = 清空）：逐条增删的接口会让两个页面各发一半
 * 请求后互相覆盖，这类问题只会在数据不一致时才被发现。
 */
function AssetTags({assetId}: {assetId: string}) {
  const tags = useAssetTags(assetId);
  const catalog = useTagCatalog();
  const save = useSetAssetTags();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);

  const current = (tags.data?.items ?? []).map((tag) => tag.id);
  if (tags.isPending) return <p role="status" className="text-[11.5px] text-ink-mute">正在读取标签…</p>;
  if (tags.error) return <p role="alert" className="text-[11.5px] text-danger">标签读取失败：{tags.error.message}</p>;

  if (!editing) {
    return <div className="flex flex-wrap items-center gap-1.5">
      {(tags.data?.items ?? []).map((tag) => <Chip key={tag.id} tone="primary">{tag.name}</Chip>)}
      {!current.length && <span className="text-[11.5px] text-ink-mute">还没有标签。标签会进词表，之后可以在筛选条里直接选。</span>}
      <Button size="sm" variant="ghost" onClick={() => { setDraft(current); setEditing(true); }}>
        编辑
      </Button>
    </div>;
  }

  const groups = catalog.data?.groups ?? [];
  return <div className="space-y-2">
    {groups.map((group) => <div key={group.dimension}>
      <p className="mb-1 text-[11px] text-ink-mute">{group.label}</p>
      <div className="flex flex-wrap gap-1">
        {(group.items ?? []).map((tag) => {
          const on = draft.includes(tag.id);
          return <button key={tag.id} type="button"
            aria-pressed={on}
            onClick={() => setDraft((prev) => on ? prev.filter((id) => id !== tag.id) : [...prev, tag.id])}
            className={on
              ? 'rounded-[5px] border border-primary-line bg-primary-soft px-1.5 py-px text-[10px] text-primary-deep'
              : 'rounded-[5px] border border-line-soft px-1.5 py-px text-[10px] text-ink-soft hover:border-line-strong'}
          >
            {tag.name}
          </button>;
        })}
      </div>
    </div>)}
    {!groups.length && <p className="text-[11.5px] text-ink-mute">词表还没加载出来，稍后再试。</p>}
    <div className="flex gap-1.5">
      <Button size="sm" variant="primary" loading={save.isPending} disabled={!groups.length}
        onClick={() => save.mutate({assetId, tagIds: draft}, {
          onSuccess: () => { setEditing(false); toast('标签已保存', 'success'); },
          onError: (error) => toast(error.message, 'error'),
        })}>
        保存
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>取消</Button>
      <span className="ml-auto self-center text-[11px] text-ink-mute">已选 {draft.length} 个</span>
    </div>
  </div>;
}
