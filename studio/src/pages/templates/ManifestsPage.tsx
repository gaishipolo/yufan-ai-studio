import { ManifestWorkspace } from '@/features/template-manifests/ManifestWorkspace';
import { PageHeader } from '@/shared/ui';

/**
 * 模板封装：一份配置描述一支原片与它的若干功能，发布后成为模板库卡片。
 *
 * 页面上不写操作指引——分区本身就是流程（基本信息 → 素材包 → 功能 → 发布），
 * 缺什么由服务端预检逐条报出来。
 */
export default function ManifestsPage() {
  return (
    <>
      <PageHeader title="模板封装" description="把一支原片封装成可复用的模板：选素材包、写提示词，发布后出现在模板库。" />
      <ManifestWorkspace />
    </>
  );
}
