import { SpecWorkspace } from '@/features/template-specs/SpecWorkspace';
import { PageHeader } from '@/shared/ui';

/**
 * 模板封装：定表，不执行。
 *
 * 一份模板描述「要什么素材 + 提示词 + 用哪个模型」，发布后进模板库填表。
 * 页面上不做规则校验，也不解释每一步该干什么——缺口由服务端预检逐条报出来，
 * 分区本身（槽位 → 提示词 → 模型）就是流程。
 */
export default function SpecsPage() {
  return (
    <>
      <PageHeader title="模板封装" />
      <SpecWorkspace />
    </>
  );
}
