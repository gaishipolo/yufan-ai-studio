import { ArrowRight, Info } from 'lucide-react';
import { Link } from 'react-router-dom';
import { AssetWorkspace } from '@/features/archive-library/AssetWorkspace';
import { PageHeader } from '@/shared/ui';

/**
 * 素材页的外壳交给 `AssetWorkspace`：它按权限分「公共素材 / 我的素材 / 参考素材维护 / 封装与发布」。
 *
 * 只有一条存储通路。这里曾经写着「上传素材进对象存储（`/assets`），归档原片留在
 * 归档盘（`/assets/archive`）」——两套接口合并成一套 `/assets` 之后，归档就是素材
 * 本身，「对象存储」只是它今天用的后端。页面不该再暴露这个区分：用户关心的是
 * 「这张图能不能用」，不是「字节在哪个桶里」。
 *
 * M5 起这是**旧版入口**：新库在 `/library`（草稿箱 / 个人 / 小组 / 团队 / 回收站）。
 * 顶部那条提示只负责「告诉人新库在哪」，不做任何拦截 —— 并存观察期要能照常用旧库，
 * 才对比得出同一件事两边的表现是否一致。
 */
export default function AssetsPage() {
  return (
    <>
      <PageHeader
        title="素材资产库（旧版）"
        description="上传原片、维护参考素材与专员封装；按权限在公共素材池里调用。"
      />
      <div className="mt-3 flex items-start gap-2 rounded-card border border-line bg-panel-soft px-3 py-2.5 text-[11.5px] text-ink-soft">
        <Info size={14} className="mt-0.5 shrink-0 text-ink-mute" aria-hidden />
        <p className="leading-5">
          新版资产库已上线（三层可见性、共享审批、草稿箱与回收站）。这里保留为并存观察期的
          旧版入口，功能照常可用；新上传的素材请优先走新版，避免两份数据各写一半。
          <Link
            to="/library"
            className="ml-1 inline-flex items-center gap-0.5 font-semibold text-primary hover:underline"
          >
            去新版资产库
            <ArrowRight size={11} aria-hidden />
          </Link>
        </p>
      </div>
      <div className="mt-4">
        <AssetWorkspace />
      </div>
    </>
  );
}
