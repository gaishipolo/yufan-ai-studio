import { SubjectLibrary } from '@/features/archive-library/SubjectLibrary';
import { PageHeader } from '@/shared/ui';

/**
 * 主体。
 *
 * 独立路由而不是管理页的一个 tab：主体是**装配单位**，不是素材的一个筛选视角。
 * 混进五段式管理页会让「切库 / 筛分类 / 看主体」三件事共用一条 URL，
 * 而主体的归属跟着库走，跟分类条没有关系。
 *
 * 这一页只读：组装（拖图、改角色、调顺序）在 M3.5 的组装台，发布按钮在详情浮层里。
 */
export default function AssetSubjectsPage() {
  return (
    <>
      <PageHeader
        title="主体"
        description="把同一角色的几张素材装成一组，模板按角色调用它。可用性由服务端判定：可发布 / 缺图 / 待补齐。"
      />
      <div className="mt-4">
        <SubjectLibrary />
      </div>
    </>
  );
}
