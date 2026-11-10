import { ReplacementTemplates } from '@/features/replacement-templates/ReplacementTemplates';
import { VideoTemplateLibrary } from '@/features/video-templates/VideoTemplateLibrary';
import { PageHeader } from '@/shared/ui';

export default function TemplatesPage() {
  /* 用户进来先看的是「全部模板」卡片：封装结果本身就是一张可选择的视频卡片，
     和 0507 高端姐一样。封装管理（固定蓝本 / 复制 / 新建）收在页面底部。 */
  return <>
    <PageHeader title="模板库" description="选择视频模板或已封装的替换素材包，将人物、产品或场景替换成你的内容。" />
    <ReplacementTemplates />
    <details className="mt-6 rounded-xl border border-line bg-panel p-4">
      <summary className="cursor-pointer text-sm font-semibold text-ink">视频模板封装管理</summary>
      <p className="mt-2 text-xs leading-relaxed text-ink-mute">
        封装后的模板会出现在上方的「全部模板」里，选中卡片即自动套用绑定的替换配置。
        这里用于复制固定蓝本、封装后续模板。
      </p>
      <VideoTemplateLibrary embedded />
    </details>
  </>;
}
