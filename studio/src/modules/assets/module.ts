import { FolderOpen } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 现役素材资产库（/assets）—— 并存观察期的**旧版入口**。
 *
 * M5 起主入口换成 `/library`（新库），这里保留：
 *
 * 1. 侧栏与宫格仍列出，但排在「资产库」之后并标注「旧版」。用户不会误点进
 *    旧库，需要对比 / 回退的人又随时找得到。**藏起来不算下线**：那会让回退
 *    路径变成「改一行代码再重启」，观察期恰恰最需要随手切换；
 * 2. 权限仍是 `asset:*`，接口、表、存储前缀都与 v2 完全隔离，
 *    两边同时写不会互相污染；
 * 3. 页面顶部有一条指向新版的位置提示（`pages/assets/AssetsPage.tsx`），
 *    不拦截任何操作——观察期要能照常使用旧库，才能对比「同一件事两边是否一致」。
 *
 * 下线（观察期结束后）分两步，别和别的改动混在一次提交里：
 * 1. 先删本文件里的 `nav` / `card`：入口消失，直连 `/assets` 仍可访问，
 *    给「还在用旧链接的人」留缓冲期；
 * 2. 确认旧链接也没人访问后，再整模块删除（`src/modules/assets`、
 *    `src/pages/assets`，以及 `src/entities/asset` 里模板库仍依赖的部分要留下）。
 */
export default defineModule({
  id: 'assets',
  title: '素材资产库',
  routes: [
    {
      path: 'assets',
      load: () => import('@/pages/assets/AssetsPage'),
      permissions: ['asset:read'],
      nav: {
        label: '素材资产库（旧版）',
        icon: FolderOpen,
        group: '资产与数据',
        groupOrder: 3,
        order: 2,
      },
      card: {
        subtitle: '旧版 · 并存观察期',
        gradient: 'linear-gradient(140deg,#ffd24a,#f5a623)',
        order: 5,
      },
    },
  ],
});
