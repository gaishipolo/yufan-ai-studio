import { ReplacementTemplates } from '@/features/replacement-templates/ReplacementTemplates';
import { PageHeader } from '@/shared/ui';

export default function TemplatesPage() {
  return <>
    <PageHeader title="模板库" description="发现好创意，选择原片，将人物、产品或场景替换成你的内容。" />
    <ReplacementTemplates />
  </>;
}
