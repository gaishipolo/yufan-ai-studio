import { AssetWorkspace } from '@/features/archive-library/AssetWorkspace';
import { PageHeader } from '@/shared/ui';

/**
 * 素材页的外壳交给 `AssetWorkspace`：它按权限分「公共素材 / 我的素材 / 参考素材维护 / 封装与发布」。
 *
 * 两条存储通路都收在「我的素材」里，不要合成一个列表：
 * 上传素材进对象存储（`/assets`），归档原片留在归档盘（`/assets/archive`）。
 */
export default function AssetsPage() {
  return (
    <>
      <PageHeader
        title="素材资产库"
        description="上传素材进对象存储；归档原片与专员封装的公共素材按权限调用。"
      />
      <div className="mt-4">
        <AssetWorkspace />
      </div>
    </>
  );
}
