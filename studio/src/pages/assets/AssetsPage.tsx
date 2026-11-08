import { AssetWorkspace } from '@/features/archive-library/AssetWorkspace';
import { PageHeader } from '@/shared/ui';

/**
 * 素材页的外壳交给 `AssetWorkspace`：它按权限分「公共素材 / 我的素材 / 参考素材维护 / 封装与发布」。
 *
 * 只有一条存储通路。这里曾经写着「上传素材进对象存储（`/assets`），归档原片留在
 * 归档盘（`/assets/archive`）」——两套接口合并成一套 `/assets` 之后，归档就是素材
 * 本身，「对象存储」只是它今天用的后端。页面不该再暴露这个区分：用户关心的是
 * 「这张图能不能用」，不是「字节在哪个桶里」。
 */
export default function AssetsPage() {
  return (
    <>
      <PageHeader
        title="素材资产库"
        description="上传原片、维护参考素材与专员封装；按权限在公共素材池里调用。"
      />
      <div className="mt-4">
        <AssetWorkspace />
      </div>
    </>
  );
}
