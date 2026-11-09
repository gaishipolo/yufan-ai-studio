import { useEffect, useState, type FormEvent } from 'react';
import { Button, FormField, Modal, Select, TextInput, type SelectOption } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { Role } from '@/entities/role/model/types';
import type { Group } from '@/entities/group/model/types';
import { useCreateUser, useUpdateUser } from '@/entities/user/api/user.queries';
import { UserAvatar } from '@/entities/user/ui/UserAvatar';
import {
  PASSWORD_MIN_LENGTH,
  USERNAME_PATTERN,
  checkAvatarUrl,
  checkEmail,
  checkPassword,
} from '@/entities/user/model/validation';
import type { User } from '@/entities/user/model/types';

/**
 * 新建 / 编辑用户。
 * 复用一个弹窗是因为字段高度重合；两者差异（密码、角色、分组只在创建时可填）用
 * `isEdit` 分支表达。编辑时改角色属于提权动作，走「分配角色」并单独受
 * `identity:role:manage` 约束；这里不碰。
 *
 * 分组与角色是**单选**的原生下拉，并放在表单顶部：Modal 容器有 `overflow-auto`，
 * 自定义浮层放在底部会被裁切、还会盖住提交按钮；原生 `<select>` 的选项层由浏览器
 * 渲染、不占文档流，浏览器在空间不足时自动向上展开，移到顶部后向下也有足够空间。
 *
 * 接口入参仍是数组（底层允许一人多组 / 多角色，由小组「成员」页签和「分配角色」
 * 弹窗维护），这里只提交 0 或 1 项 —— 建号入口按界面语义收敛为单选，不动后端契约。
 */
export function UserFormModal({
  open,
  user,
  roles,
  groups,
  onClose,
}: {
  open: boolean;
  /** 传入即编辑模式 */
  user?: User | null;
  roles: Role[];
  groups: Group[];
  onClose: () => void;
}) {
  const isEdit = Boolean(user);
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [position, setPosition] = useState('');
  const [avatarUrl, setAvatarUrl] = useState('');
  const [roleId, setRoleId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  // 每次打开都从传入值重置，避免上一次的残留（尤其是密码）
  useEffect(() => {
    if (!open) return;
    setUsername(user?.username ?? '');
    setPassword('');
    setDisplayName(user?.displayName ?? '');
    setEmail(user?.email ?? '');
    setPhone(user?.phone ?? '');
    setPosition(user?.position ?? '');
    setAvatarUrl(user?.avatarUrl ?? '');
    setRoleId(user?.roles[0]?.id ?? '');
    setGroupId('');
    setFormError(null);
  }, [open, user]);

  const pending = createUser.isPending || updateUser.isPending;

  // 首项是「不选」占位：单选下拉用空串表示未选，提交时映射回空数组。
  const roleOptions: SelectOption[] = [
    { value: '', label: '不分配角色' },
    ...roles.map((role) => ({
      value: role.id,
      label: role.isSuperuser
        ? `${role.name}（超管）`
        : role.isBuiltin
          ? `${role.name}（内置）`
          : role.name,
    })),
  ];
  const groupOptions: SelectOption[] = [
    { value: '', label: groups.length ? '不加入分组' : '暂无可选分组' },
    ...groups.map((group) => ({ value: group.id, label: group.name })),
  ];

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);

    if (!isEdit) {
      if (!USERNAME_PATTERN.test(username.trim())) {
        setFormError('用户名 3-64 位，只能是字母、数字或 . _ -');
        return;
      }
      const passwordError = checkPassword(password, '初始密码');
      if (passwordError) {
        setFormError(passwordError);
        return;
      }
    }
    const emailError = checkEmail(email);
    if (emailError) {
      setFormError(emailError);
      return;
    }
    const avatarError = checkAvatarUrl(avatarUrl);
    if (avatarError) {
      setFormError(avatarError);
      return;
    }

    if (isEdit && user) {
      updateUser.mutate(
        {
          id: user.id,
          payload: {
            displayName: displayName.trim(),
            email: email.trim() || null,
            phone: phone.trim() || null,
            position: position.trim() || null,
            avatarUrl: avatarUrl.trim() || null,
          },
        },
        {
          onSuccess: () => {
            toast('用户资料已更新', 'success');
            onClose();
          },
          onError: (error) => setFormError(error.message),
        },
      );
      return;
    }

    createUser.mutate(
      {
        username: username.trim().toLowerCase(),
        password,
        displayName: displayName.trim() || undefined,
        email: email.trim() || null,
        phone: phone.trim() || null,
        position: position.trim() || null,
        roleIds: roleId ? [roleId] : [],
        groupIds: groupId ? [groupId] : [],
      },
      {
        onSuccess: () => {
          toast('用户已创建', 'success');
          onClose();
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? `编辑用户 · ${user?.username ?? ''}` : '新建用户'}
      width={520}
    >
      <form className="space-y-3 px-[18px] py-4" onSubmit={handleSubmit}>
        {/* 身份归属放在最上面：下拉展开时下方有足够空间，不会像放在底部那样
            被 Modal 的滚动容器裁切、也盖不到「取消 / 创建」按钮。 */}
        {!isEdit && (
          <div className="grid gap-3 sm:grid-cols-2">
            <FormField
              label="分组"
              htmlFor="user-group"
              hint={groups.length ? '创建后可在小组里调整' : '暂无分组，可先在左侧新增'}
            >
              <Select
                id="user-group"
                block
                options={groupOptions}
                value={groupId}
                disabled={pending}
                onChange={(event) => setGroupId(event.target.value)}
              />
            </FormField>
            <FormField label="角色" htmlFor="user-role" hint="决定基础权限点">
              <Select
                id="user-role"
                block
                options={roleOptions}
                value={roleId}
                disabled={pending}
                onChange={(event) => setRoleId(event.target.value)}
              />
            </FormField>
          </div>
        )}

        {isEdit ? (
          <FormField label="登录名" htmlFor="user-username">
            {/* 登录名是历史归属的一部分，创建后不可改 */}
            <TextInput id="user-username" value={user?.username ?? ''} disabled readOnly />
          </FormField>
        ) : (
          <FormField
            label="登录名"
            htmlFor="user-username"
            required
            hint="3-64 位字母、数字或 . _ -，保存后会自动转小写"
          >
            <TextInput
              id="user-username"
              value={username}
              autoComplete="off"
              onChange={(event) => setUsername(event.target.value)}
            />
          </FormField>
        )}

        {!isEdit && (
          <FormField
            label="初始密码"
            htmlFor="user-password"
            required
            hint={`至少 ${PASSWORD_MIN_LENGTH} 位，不能是纯数字`}
          >
            <TextInput
              id="user-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </FormField>
        )}

        <FormField label="昵称" htmlFor="user-display-name" hint="顶栏与任务作者展示用；留空则显示登录名">
          <TextInput
            id="user-display-name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </FormField>

        <FormField label="邮箱" htmlFor="user-email">
          <TextInput
            id="user-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </FormField>

        {/* 手机号 / 职务对齐截图的用户表格列。Q1 未定：目前只作可选展示字段，
            不做唯一性、必填或格式校验，留空提交 null 由后端清空。 */}
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField label="手机号" htmlFor="user-phone" hint="仅用于展示与检索">
            <TextInput
              id="user-phone"
              value={phone}
              inputMode="tel"
              onChange={(event) => setPhone(event.target.value)}
            />
          </FormField>
          <FormField label="职务" htmlFor="user-position" hint="仅用于展示">
            <TextInput
              id="user-position"
              value={position}
              onChange={(event) => setPosition(event.target.value)}
            />
          </FormField>
        </div>

        {/* 建号接口（CreateUserIn）不接收 avatarUrl，只在编辑时可设 */}
        {isEdit && (
          <FormField
            label="头像地址"
            htmlFor="user-avatar"
            hint="留空则显示昵称首字；图片加载失败会自动退回首字"
          >
            <div className="flex items-center gap-2">
              <UserAvatar user={{ displayName, username, avatarUrl }} size={32} shape="square" />
              <TextInput
                id="user-avatar"
                className="flex-1"
                value={avatarUrl}
                maxLength={512}
                placeholder="https://…"
                onChange={(event) => setAvatarUrl(event.target.value)}
              />
            </div>
          </FormField>
        )}

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button size="md" onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button size="md" variant="primary" type="submit" loading={pending}>
            {isEdit ? '保存' : '创建'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
