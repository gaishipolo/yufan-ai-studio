import { SpecWorkspace } from '@/features/template-specs/SpecWorkspace';
import { PageHeader } from '@/shared/ui';

/**
 * 模板规格：定表，不执行。
 *
 * 页面上不做规则校验，也不解释每一步该干什么——缺口由服务端预检逐条报出来，
 * 分区本身（槽位 → 提示词 → 模型）就是流程。
 */
export default function SpecsPage() {
  return (
    <>
      <PageHeader
        title="模板规格"
        description="描述一份模板要什么素材、写什么提示词、用哪个模型；发布后由模板库填表使用。"
      />
      <SpecWorkspace />
    </>
  );
}
