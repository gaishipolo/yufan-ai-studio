import { useState } from 'react';
import { AlertCircle, Plus, RefreshCw, ShieldCheck, UserRound } from 'lucide-react';
import { Button, Chip, EmptyState, SearchInput, Select, Skeleton } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { useSubject } from '@/shared/access/use-access';
import { cn } from '@/shared/lib/cn';
import { formatDateTime, formatRelative } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { useRoleList } from '@/entities/role/api/role.queries';
import { useUpdateUser, useUserList } from '@/entities/user/api/user.queries';
import { displayNameOf, type User } from '@/entities/user/model/types';
import { UserAvatar } from '@/entities/user/ui/UserAvatar';
import type { Group } from '@/entities/group/model/types';
import { UserFormModal } from '@/features/user-form/UserFormModal';
import { AssignRolesModal } from '@/features/user-roles/AssignRolesModal';
import { ResetPasswordModal } from '@/features/user-security/ResetPasswordModal';
import { DeactivateUserModal } from '@/features/user-security/DeactivateUserModal';
import { GroupEditorModal } from '@/features/group-editor/GroupEditorModal';
import { EffectivePermissionsModal } from '@/features/permission-layers/EffectivePermissionsModal';
import {
  LayerPermissionsModal,
  type LayerTarget,
} from '@/features/permission-layers/LayerPermissionsModal';
import { GroupPanel } from './GroupPanel';
import { UserTableFooter } from './UserTableFooter';

type Dialog =
  | 'create'
  | 'edit'
  | 'roles'
  | 'password'
  | 'deactivate'
  | 'effective'
  | 'personal'
  | 'group'
  | null;

const STATUS_OPTIONS = [
  { value: 'all', label: '全部状态' },
  { value: 'active', label: '已启用' },
  { value: 'inactive', label: '已停用' },
];

/** 8 列表格（截图顺序）。类名写成完整字面量，Tailwind 才能在源码里扫到。 */
const GRID =
  'md:grid-cols-[minmax(0,1fr)_minmax(0,.9fr)_minmax(0,.9fr)_minmax(0,1.1fr)_minmax(0,.8fr)_minmax(0,1.25fr)_minmax(0,.9fr)_minmax(0,1.7fr)]';

/**
 * 用户页：左侧分组 + 顶部三路筛选 + 用户表格 + 分页（截图布局）。
 *
 * 数据仍然全部来自服务端：小组筛选传 `groupId`，姓名/账号/手机号各自走后端
 * 的独立 query 参数。前端不做「把用户拉回来再按组过滤」——分页会立刻失真。
 *
 * 权限判定只认权限码：分层的来源明细仅用于「有效权限」弹窗展示，
 * 不进 `Subject`、不参与 `can()`。合并规则只在后端有一份。
 */
export function UsersSection() {
  const subject = useSubject();
  const [name, setName] = useState('');
  const [username, setUsername] = useState('');
  const [phone, setPhone] = useState('');
  const [status, setStatus] = useState('all');
  const [roleId, setRoleId] = useState('');
  const [groupId, setGroupId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const debouncedName = useDebouncedValue(name);
  const debouncedUsername = useDebouncedValue(username);
  const debouncedPhone = useDebouncedValue(phone);

  const rolesQuery = useRoleList();
  const usersQuery = useUserList({
    page,
    pageSize,
    name: debouncedName.trim() || undefined,
    username: debouncedUsername.trim() || undefined,
    phone: debouncedPhone.trim() || undefined,
    isActive: status === 'all' ? undefined : status === 'active',
    roleId: roleId || undefined,
    groupId: groupId || undefined,
  });

  const enableUser = useUpdateUser();

  const [dialog, setDialog] = useState<Dialog>(null);
  const [active, setActive] = useState<User | null>(null);
  const [activeGroup, setActiveGroup] = useState<Group | null>(null);

  const open = (next: Dialog, user: User | null = null) => {
    setActive(user);
    setDialog(next);
  };
  const close = () => setDialog(null);

  /** 筛选条件变了就回到第一页，否则会出现「第 3 页但没有结果」 */
  const resetToFirstPage = () => setPage(1);

  const data = usersQuery.data;
  const total = data?.total ?? 0;
  const totalPages = data ? Math.max(1, Math.ceil(total / pageSize)) : 0;
  const items = data?.items ?? [];

  const layerTarget: LayerTarget | null =
    dialog === 'personal' && active
      ? { kind: 'user', id: active.id, name: displayNameOf(active) }
      : null;

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-start">
        <Can permission="identity:group:read">
          <GroupPanel
            selectedId={groupId}
            onSelect={(id) => {
              setGroupId(id);
              resetToFirstPage();
            }}
            onCreate={() => {
              setActiveGroup(null);
              setDialog('group');
            }}
            onEdit={(group) => {
              setActiveGroup(group);
              setDialog('group');
            }}
          />
        </Can>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <SearchInput
              value={name}
              wrapperClassName="w-full md:w-[150px]"
              placeholder="请输入名字"
              aria-label="按名字搜索"
              onChange={(event) => {
                setName(event.target.value);
                resetToFirstPage();
              }}
            />
            <SearchInput
              value={username}
              wrapperClassName="w-full md:w-[150px]"
              placeholder="搜索账号"
              aria-label="按账号搜索"
              onChange={(event) => {
                setUsername(event.target.value);
                resetToFirstPage();
              }}
            />
            <SearchInput
              value={phone}
              wrapperClassName="w-full md:w-[150px]"
              placeholder="搜索手机号"
              aria-label="按手机号搜索"
              onChange={(event) => {
                setPhone(event.target.value);
                resetToFirstPage();
              }}
            />
            <Select
              options={[
                { value: '', label: '请选择角色' },
                ...(rolesQuery.data ?? []).map((role) => ({ value: role.id, label: role.name })),
              ]}
              value={roleId}
              aria-label="按角色筛选"
              onChange={(event) => {
                setRoleId(event.target.value);
                resetToFirstPage();
              }}
            />
            <Select
              options={STATUS_OPTIONS}
              value={status}
              aria-label="按状态筛选"
              onChange={(event) => {
                setStatus(event.target.value);
                resetToFirstPage();
              }}
            />
            <Can permission="identity:create">
              <Button
                size="sm"
                variant="primary"
                icon={<Plus size={13} />}
                title="新建一个账号；随后可在左侧小组的「成员」页签把他加进当前分组"
                onClick={() => open('create')}
              >
                添加成员
              </Button>
            </Can>
          </div>

          <div className="mt-3 overflow-hidden rounded-card border border-line-soft bg-panel">
            <div className="overflow-x-auto">
              <div className="min-w-0 md:min-w-[1120px]">
                <div
                  className={cn(
                    'hidden gap-3 border-b border-line-soft bg-panel-soft px-3.5 py-2 text-[11px] font-semibold text-ink-mute md:grid',
                    GRID,
                  )}
                >
                  <span>姓名</span>
                  <span>账号</span>
                  <span>手机号</span>
                  <span>角色</span>
                  <span>职务</span>
                  <span>创建时间</span>
                  <span>最后登录</span>
                  <span className="text-right">操作</span>
                </div>

                {usersQuery.isLoading ? (
                  <div className="space-y-2 p-3.5">
                    {Array.from({ length: 3 }).map((_, index) => (
                      <Skeleton key={index} className="h-10 w-full" />
                    ))}
                  </div>
                ) : usersQuery.isError ? (
                  <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
                    <AlertCircle size={22} className="text-danger" />
                    <p className="text-xs text-ink-soft">用户列表加载失败：{usersQuery.error.message}</p>
                    <Button size="sm" icon={<RefreshCw size={12} />} onClick={() => void usersQuery.refetch()}>
                      重试
                    </Button>
                  </div>
                ) : items.length === 0 ? (
                  <EmptyState
                    icon={<UserRound size={36} strokeWidth={1.4} />}
                    title="没有匹配的账号"
                    description="换个关键词，或清空筛选条件试试"
                  />
                ) : (
                  <ul className="divide-y divide-line-soft">
                    {items.map((user) => (
                      <li
                        key={user.id}
                        className={cn(
                          'grid gap-2 px-3.5 py-2.5 transition-colors hover:bg-panel-soft md:items-center md:gap-3',
                          GRID,
                        )}
                      >
                        <div className="flex min-w-0 items-center gap-2.5">
                          <UserAvatar user={user} size={28} tone={user.isActive ? 'soft' : 'muted'} />
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="truncate text-xs font-medium text-ink">
                                {displayNameOf(user)}
                              </span>
                              {user.id === subject.id && <Chip tone="primary">我</Chip>}
                              {user.roles.some((role) => role.isSuperuser) && <Chip tone="warn">超管</Chip>}
                            </div>
                            <span
                              className={cn(
                                'inline-flex items-center gap-1 text-[11px]',
                                user.isActive ? 'text-[#0f9c63]' : 'text-ink-mute',
                              )}
                            >
                              <span
                                className={cn(
                                  'h-1.5 w-1.5 rounded-full',
                                  user.isActive ? 'bg-ok' : 'bg-line-strong',
                                )}
                                aria-hidden
                              />
                              {user.isActive ? '已启用' : '已停用'}
                            </span>
                          </div>
                        </div>

                        <div className="truncate text-[11.5px] text-ink-soft" title={user.username}>
                          @{user.username}
                        </div>

                        <div className="truncate text-[11.5px] text-ink-soft" title={user.phone ?? undefined}>
                          {user.phone || '—'}
                        </div>

                        <div className="flex flex-wrap gap-1">
                          {user.roles.length === 0 ? (
                            <span className="text-[11px] text-ink-mute">未分配</span>
                          ) : (
                            user.roles.map((role) => <Chip key={role.id}>{role.name}</Chip>)
                          )}
                        </div>

                        <div className="truncate text-[11.5px] text-ink-soft" title={user.position ?? undefined}>
                          {user.position || '—'}
                        </div>

                        <div className="text-[11px] text-ink-mute">{formatDateTime(user.createdAt)}</div>

                        <div
                          className="text-[11px] text-ink-mute"
                          title={user.lastLoginAt ? `最后登录 ${formatDateTime(user.lastLoginAt)}` : '从未登录'}
                        >
                          {user.lastLoginAt ? formatRelative(user.lastLoginAt) : '从未登录'}
                        </div>

                        <div className="flex flex-wrap items-center justify-start gap-0.5 md:justify-end">
                          <Button size="sm" variant="ghost" onClick={() => open('effective', user)}>
                            权限
                          </Button>
                          <Can permission="identity:update">
                            <Button size="sm" variant="ghost" onClick={() => open('edit', user)}>
                              编辑
                            </Button>
                          </Can>
                          <Can permission="identity:role:manage">
                            <Button
                              size="sm"
                              variant="ghost"
                              icon={<ShieldCheck size={12} />}
                              onClick={() => open('roles', user)}
                            >
                              角色
                            </Button>
                          </Can>
                          <Can permission="identity:update">
                            <Button size="sm" variant="ghost" onClick={() => open('password', user)}>
                              重置密码
                            </Button>
                          </Can>
                          {user.isActive ? (
                            <Can permission="identity:delete">
                              {/* 停用自己会被后端 409 拒绝，但让用户「点一下才知道」太粗暴：
                                  禁用 + title 说明，比一次白跑的请求诚实 */}
                              <span title={user.id === subject.id ? '不能停用自己的账号' : undefined}>
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  disabled={user.id === subject.id}
                                  className="text-danger hover:bg-[#feeceb] hover:text-danger"
                                  onClick={() => open('deactivate', user)}
                                >
                                  停用
                                </Button>
                              </span>
                            </Can>
                          ) : (
                            <Can permission="identity:update">
                              <Button
                                size="sm"
                                variant="ghost"
                                loading={
                                  // 一个 mutation 的 isPending 是整体状态：不按行判，启用 A 时
                                  // 所有已停用行会一起转圈。variables 是本次调用的入参，
                                  // 拿它认出「正在转圈的是哪一行」。
                                  enableUser.isPending && enableUser.variables?.id === user.id
                                }
                                onClick={() =>
                                  enableUser.mutate(
                                    { id: user.id, payload: { isActive: true } },
                                    {
                                      onSuccess: () => toast('用户已启用', 'success'),
                                      onError: (error) => toast(error.message, 'error'),
                                    },
                                  )
                                }
                              >
                                启用
                              </Button>
                            </Can>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            <UserTableFooter
              page={page}
              totalPages={totalPages}
              total={total}
              pageSize={pageSize}
              busy={usersQuery.isFetching}
              onPage={setPage}
              onPageSize={setPageSize}
            />
          </div>
        </div>
      </div>

      <UserFormModal
        open={dialog === 'create' || dialog === 'edit'}
        user={dialog === 'edit' ? active : null}
        roles={rolesQuery.data ?? []}
        onClose={close}
      />
      <AssignRolesModal
        open={dialog === 'roles'}
        user={active}
        roles={rolesQuery.data ?? []}
        onClose={close}
      />
      <ResetPasswordModal open={dialog === 'password'} user={active} onClose={close} />
      <DeactivateUserModal open={dialog === 'deactivate'} user={active} onClose={close} />
      <GroupEditorModal open={dialog === 'group'} group={activeGroup} onClose={close} />
      <EffectivePermissionsModal
        open={dialog === 'effective'}
        user={active}
        onClose={close}
        onEditPersonal={() => setDialog('personal')}
      />
      <LayerPermissionsModal open={Boolean(layerTarget)} target={layerTarget} onClose={close} />
    </>
  );
}
