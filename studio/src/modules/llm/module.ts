import { Bot } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * LLM 配置页：管理 OpenAI 兼容供应商与「平台 id → 上游模型名」映射。
 *
 * 顶级菜单 `page:llm` 与接口码 `llm_config:*` 分开：路由守卫管进不进页面，
 * 接口码由后端逐个校验。关掉页面时接口也跟着关。
 */
export default defineModule({
  id: 'llm',
  title: 'LLM 配置',
  routes: [
    {
      path: 'llm',
      load: () => import('@/pages/llm/LlmConfigPage'),
      permissions: ['page:llm'],
      nav: {
        label: 'LLM 配置',
        icon: Bot,
        group: '运维',
        groupOrder: 10,
        order: 2,
      },
    },
  ],
});
