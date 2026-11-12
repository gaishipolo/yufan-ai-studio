import { ReplacementTemplates } from '@/features/replacement-templates/ReplacementTemplates';
import { PageHeader } from '@/shared/ui';

export default function TemplatesPage() {
  /* 用户进来先看的是「全部模板」卡片：封装结果本身就是一张可选择的视频卡片，
     和 0507 高端姐一样。
     模板的封装与维护目前走接口（新建封装 / 改提示词 / 换原片 / 加功能），前端不再
     提供入口——固定蓝本（pinned）在这条维护路径上用不到，页面因此不再显示它。
     `features/video-templates/VideoTemplateLibrary` 组件与它的一套测试原样保留，
     需要恢复面板时把 import 与 <VideoTemplateLibrary embedded /> 挂回来即可。 */
  return <>
    <PageHeader title="模板库" description="选择视频模板或已封装的替换素材包，将人物、产品或场景替换成你的内容。" />
    <ReplacementTemplates />
  </>;
}
