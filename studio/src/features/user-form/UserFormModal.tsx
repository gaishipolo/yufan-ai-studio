import { useEffect, useState, type FormEvent } from 'react';
import {
  Button,
  FormField,
  Modal,
  MultiSelect,
  TextInput,
  type MultiSelectOption,
} from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import type { Role } from '@/entities/role/model/types';
import type { Group } from '@/entities/group/model/types';
import { useCreateUser, useUpdateUser } from '@/entities/user/api/user.queries';
import { UserAvatar } from '@/entities/user/ui/UserAvatar';
import {
  PASSWORD_HINT,
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
 * 身份归属放在表单**第一行**：分组与角色都是可多选的 `MultiSelect`。
 * 浮层是 absolute 向下展开的，放在顶部才有空间——放在底部会被
 * `Modal` 的 `overflow-auto` 裁切、还盖住「取消 / 创建」按钮（见 M3.2）。
 *
 * 接口入参是数组（一人可属多组 / 多角色），与控件的多选语义一一对应。
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
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [groupIds, setGroupIds] = useState<string[]>([]);
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
    setRoleIds(user?.roles.map((role) => role.id) ?? []);
    setGroupIds([]);
    setFormError(null);
  }, [open, user]);

  const pending = createUser.isPending || updateUser.isPending;

  const roleOptions: MultiSelectOption[] = roles.map((role) => ({
    value: role.id,
    label: role.name,
    description: role.description || role.code,
    badge: role.isSuperuser ? '超管' : role.isBuiltin ? '内置' : undefined,
  }));
  /* 分组用**多选**：接口入参 `groupIds` 本来就是数组，而且「一人可属多组」
     是这个系统的既定规则（小组编辑页就写着）。原来这里用单选下拉，
     等于用控件限制住了后端允许的事 —— 旁边那句「一人可属多组」的小字
     就是在替这个控件打补丁。控件改了，补丁就不用留了。 */
  const groupOptions: MultiSelectOption[] = groups.map((group) => ({
    value: group.id,
    label: group.name,
    description: group.description,
  }));

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
        roleIds,
        groupIds,
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
        {/* 身份归属放在最上面：角色的多选浮层向下展开时下方有足够空间，
            不会像放在底部那样被 Modal 的滚动容器裁切、也盖不到「取消 / 创建」按钮。 */}
        {!isEdit && (
          <div className="grid gap-3 sm:grid-cols-2">
            <FormField label="分组" htmlFor="user-group">
              <MultiSelect
                id="user-group"
                ariaLabel="分组"
                options={groupOptions}
                value={groupIds}
                onChange={setGroupIds}
                placeholder="不加入分组"
                emptyText="还没有可用分组"
                disabled={pending}
              />
            </FormField>
            <FormField label="角色" htmlFor="user-role">
              <MultiSelect
                id="user-role"
                ariaLabel="角色"
                options={roleOptions}
                value={roleIds}
                onChange={setRoleIds}
                placeholder="不分配角色"
                emptyText="还没有可用角色"
                disabled={pending}
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
            hint={PASSWORD_HINT}
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

        {/* 「留空则显示登录名」由 placeholder 表达：输入框里看得见
            留空之后会显示成什么，比一行小字更准。 */}
        <FormField label="昵称" htmlFor="user-display-name">
          <TextInput
            id="user-display-name"
            value={displayName}
            placeholder={username.trim() || '留空则显示登录名'}
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
          <FormField label="手机号" htmlFor="user-phone">
            <TextInput
              id="user-phone"
              value={phone}
              inputMode="tel"
              onChange={(event) => setPhone(event.target.value)}
            />
          </FormField>
          <FormField label="职务" htmlFor="user-position">
            <TextInput
              id="user-position"
              value={position}
              onChange={(event) => setPosition(event.target.value)}
            />
          </FormField>
        </div>

        {/* 建号接口（CreateUserIn）不接收 avatarUrl，只在编辑时可设。
            头像旁边就有实时预览：留空时预览画的就是昵称首字。 */}
        {isEdit && (
          <FormField label="头像地址" htmlFor="user-avatar">
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
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-meta text-[#d94036]" role="alert">
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
