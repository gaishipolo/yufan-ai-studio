import { AssetLibrary } from '@/features/asset-library/AssetLibrary';
import { PageHeader } from '@/shared/ui';

export default function AssetsPage() {
  return (
    <>
      <PageHeader
        title="素材资产库"
        description="图片、视频与音频素材统一存放，上传后可被生成任务与替换模板引用。"
      />
      <div className="mt-4">
        <AssetLibrary />
      </div>
    </>
  );
}
