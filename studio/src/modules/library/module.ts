import { Library } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 资产库（/library）—— 全站唯一的素材入口。
 *
 * v1 素材库（`/assets`、`assets` 表、`modules/asset`、权限前缀 `asset:*`）已于
 * 2026-11-13 彻底下线：两套 id 空间形状相同、不可区分，并存期每个下游都要判断
 * 「这个 id 属于谁」。删掉之后只剩 `library_assets` 一个 id 空间。
 *
 * 权限前缀 `library:*` 因此也是唯一的一套。
 */
export default defineModule({
  id: 'library',
  title: '资产库',
  routes: [
    {
      path: 'library',
      load: () => import('@/pages/library/LibraryPage'),
      permissions: ['library:read'],
      nav: {
        label: '资产库',
        icon: Library,
        group: '资产与数据',
        groupOrder: 3,
        order: 1,
      },
      card: {
        subtitle: '草稿 / 小组 / 团队',
        gradient: 'linear-gradient(140deg,#3ddc97,#12b3a0)',
        order: 3,
      },
    },
  ],
});
