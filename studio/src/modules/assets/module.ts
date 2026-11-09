import { FolderOpen } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'assets',
  title: '素材资产库',
  routes: [
    {
      path: 'assets',
      load: () => import('@/pages/assets/AssetsPage'),
      permissions: ['asset:read'],
      nav: {
        label: '素材资产库',
        icon: FolderOpen,
        group: '资产与数据',
        groupOrder: 3,
        order: 1,
      },
      card: {
        subtitle: '图 / 视频 / 音频',
        gradient: 'linear-gradient(140deg,#ffd24a,#f5a623)',
        order: 3,
      },
    },
    /**
     * 两个维护页是**独立路由**，不进五段式管理页（设计文档 §7.6）。
     *
     * 它们没有 `nav` / `card`：入口在管理页第①段的「维护」入口里，侧栏不该
     * 多出两个常驻项——素材专员之外的账号点进去是 403，常驻的禁用项只是噪音。
     * 但**有 permissions**：直接粘地址的人要在路由层被挡住，而不是进去看白屏。
     */
    {
      path: 'assets/references',
      load: () => import('@/pages/assets/AssetReferencesPage'),
      permissions: ['asset:reference:manage'],
    },
    {
      path: 'assets/packs',
      load: () => import('@/pages/assets/AssetPacksPage'),
      permissions: ['template:pack:manage'],
    },
    /**
     * 主体：装配单位，不是素材的筛选视角，所以独立成路由（设计文档 §7.2 注）。
     *
     * 与上面两条不同，它**有入口**（管理页第①段的「主体」链接）但同样没有 `nav`：
     * 侧栏的常驻项已经够多，而主体是"从素材库进去看的东西"，不是并列的一级产品。
     */
    {
      path: 'assets/subjects',
      load: () => import('@/pages/assets/AssetSubjectsPage'),
      permissions: ['asset:read'],
    },
  ],
});
