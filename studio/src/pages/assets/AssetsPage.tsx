import { ArchiveLibrary } from '@/features/archive-library/ArchiveLibrary';
import { PageHeader } from '@/shared/ui';

export default function AssetsPage() {
  return <>
    <PageHeader title="素材资产库" description="按原片、人物、产品、场景和音频归档素材，输入与生成结果统一保存在 Z 盘。" />
    <div className="mt-5"><ArchiveLibrary /></div>
  </>;
}
