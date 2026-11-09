import { PackManager } from '@/features/archive-library/PackManager';
import { PageHeader } from '@/shared/ui';

/**
 * 封装与发布。
 *
 * 和参考素材维护同理，独立路由（设计文档 §7.6）。这一页的动作是「把若干素材
 * 打成一个可替换的模板包并发布」，它是配置行为，不是浏览行为——混进管理页会
 * 让「点错一个 tab 就开始建模板」变成可能。
 */
export default function AssetPacksPage() {
  return (
    <>
      <PageHeader
        title="封装与发布"
        description="把素材封装成可替换的模板包并发布；发布后调用方只看到模板，不看到原片。"
      />
      <div className="mt-4">
        <PackManager />
      </div>
    </>
  );
}
