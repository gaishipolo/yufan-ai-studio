import { useState } from 'react';
import { FolderOpen, Plus, UploadCloud } from 'lucide-react';
import { Button, CardSkeletonGrid, EmptyState, PageHeader, Tabs, type TabItem } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { formatBytes } from '@/shared/lib/format';
import { AssetCard } from '@/entities/asset/ui/AssetCard';
import { useAssets } from '@/entities/asset/api/asset.api';
import type { AssetType } from '@/entities/asset/model/types';

type TypeFilter = AssetType | 'all';

const TABS: Array<{ value: TypeFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'image', label: '图片' },
  { value: 'video', label: '视频' },
  { value: 'audio', label: '音频' },
];

export default function AssetsPage() {
  const [type, setType] = useState<TypeFilter>('all');
  const { data, isLoading } = useAssets({ type });

  const items: TabItem<TypeFilter>[] = TABS.map((t) => ({
    ...t,
    count: data?.counts?.[t.value] ?? 0,
  }));

  const usedBytes = (data?.items ?? []).reduce((sum, a) => sum + a.sizeBytes, 0);

  return (
    <>
      <PageHeader
        title="素材资产库"
        description="统一管理图片 / 视频 / 音频素材，支持被生成任务直接引用。"
        actions={
          <>
            <Button size="sm" icon={<UploadCloud size={13} />} onClick={() => toast('上传面板待接入对象存储')}>
              上传素材
            </Button>
            <Button size="sm" variant="primary" icon={<Plus size={13} />} onClick={() => toast('新建文件夹')}>
              新建文件夹
            </Button>
          </>
        }
      />

      <div className="mt-4 flex flex-wrap items-center gap-2 border-b border-line-soft pb-3">
        <Tabs items={items} value={type} onChange={setType} />
        <span className="ml-auto text-[11.5px] text-ink-mute">
          当前分类占用 {formatBytes(usedBytes)}
        </span>
      </div>

      <div className="mt-3.5">
        {isLoading ? (
          <CardSkeletonGrid count={8} />
        ) : (data?.items.length ?? 0) === 0 ? (
          <EmptyState icon={<FolderOpen size={40} strokeWidth={1.4} />} title="这个分类还没有素材" />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-2.5 md:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] md:gap-3.5">
            {(data?.items ?? []).map((asset) => (
              <AssetCard key={asset.id} asset={asset} />
            ))}
          </div>
        )}
      </div>
    </>
  );
}
