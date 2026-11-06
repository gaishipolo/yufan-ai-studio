import { AssetWorkspace } from '@/features/archive-library/AssetWorkspace';
import { PageHeader } from '@/shared/ui';

export default function AssetsPage() {
  return <>
    <PageHeader title="素材资产库" description="管理我的原片、分享公共原片，调用专员封装的产品、人物与场景。" />
    <div className="mt-5"><AssetWorkspace /></div>
  </>;
}
