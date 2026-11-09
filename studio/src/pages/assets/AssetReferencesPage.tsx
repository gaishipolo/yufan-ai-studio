import { ArchiveLibrary } from '@/features/archive-library/ArchiveLibrary';
import { PageHeader } from '@/shared/ui';

/**
 * 参考素材维护。
 *
 * 独立路由，**不进五段式管理页**（设计文档 §7.6）：这一页维护的是「模板要用的
 * 运镜样本」，它的读者是素材专员，一周来一次；放进管理页要么占掉一个分类条
 * 位置，要么变成第 6 个 tab，而 tab 一多，用户记不住哪个是浏览、哪个是配置。
 *
 * 权限在 `module.ts` 的 `permissions` 上声明，路由层挡一次；页面里不再二次判断——
 * 两处判断会出现「路由放行、页面白屏」，那比 403 难查得多。
 */
export default function AssetReferencesPage() {
  return (
    <>
      <PageHeader
        title="参考素材维护"
        description="维护替换模板要用的运镜样本与参考原片；发布后全工作台按模板调用。"
      />
      <div className="mt-4">
        <ArchiveLibrary />
      </div>
    </>
  );
}
