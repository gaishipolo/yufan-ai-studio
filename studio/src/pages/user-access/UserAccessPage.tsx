import { useState } from 'react';
import { PageHeader, Tabs, type TabItem } from '@/shared/ui';
import { UsersSection } from './UsersSection';
import { RolesSection } from './RolesSection';

type Tab = 'users' | 'roles';

const TABS: TabItem<Tab>[] = [
  { value: 'users', label: '用户' },
  { value: 'roles', label: '角色与权限' },
];

/**
 * 用户与权限。
 *
 * 页面只做三件事：读 query、调 mutation、渲染。请求在 `entities/user|role|group`，
 * 弹窗在 `features/*`，这里负责把它们组装起来。
 * 路由守卫与导航显隐由 `modules/user-access/module.ts` 的 `permissions` 决定。
 *
 * 没有为「团队 / 小组 / 个人」新开路由：它们是同一批账号的三层授权来源，
 * 拆成三个页面只会让人以为存在三套用户数据（D7）。
 */
export default function UserAccessPage() {
  const [tab, setTab] = useState<Tab>('users');

  return (
    <>
      <PageHeader
        title="用户与权限"
        description="按团队、小组、个人与角色四层授予权限，取并集生效；权限以服务端为准，前端只做「少看到不该看的入口」。"
      />

      <div className="mt-4 border-b border-line-soft pb-2.5">
        <Tabs items={TABS} value={tab} onChange={setTab} />
      </div>

      <div className="mt-3.5">{tab === 'users' ? <UsersSection /> : <RolesSection />}</div>
    </>
  );
}
