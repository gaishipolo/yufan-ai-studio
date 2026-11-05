import { useEffect, useState, type FormEvent } from 'react';
import { Button, FormField, Modal, TextInput } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useCreateRole, useUpdateRole } from '@/entities/role/api/role.queries';
import type { Role } from '@/entities/role/model/types';

/** 与后端 `RoleCreateIn.code` 的 pattern 一致 */
const CODE_PATTERN = /^[a-z][a-z0-9_-]{1,31}$/;

/**
 * 新建 / 编辑角色（只改基本信息）。
 * 权限勾选在 `RolePermissionsModal`，因为它是独立的提权动作与独立接口。
 */
export function RoleFormModal({
  open,
  role,
  onClose,
}: {
  open: boolean;
  /** 传入即编辑模式 */
  role?: Role | null;
  onClose: () => void;
}) {
  const isEdit = Boolean(role);
  const createRole = useCreateRole();
  const updateRole = useUpdateRole();

  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setCode(role?.code ?? '');
    setName(role?.name ?? '');
    setDescription(role?.description ?? '');
    setFormError(null);
  }, [open, role]);

  const pending = createRole.isPending || updateRole.isPending;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);

    if (!isEdit && !CODE_PATTERN.test(code.trim())) {
      setFormError('角色标识 2-32 位，小写字母开头，可含数字、- 和 _');
      return;
    }
    if (!name.trim()) {
      setFormError('角色名称不能为空');
      return;
    }

    if (isEdit && role) {
      updateRole.mutate(
        { id: role.id, payload: { name: name.trim(), description: description.trim() } },
        {
          onSuccess: () => {
            toast('角色已更新', 'success');
            onClose();
          },
          onError: (error) => setFormError(error.message),
        },
      );
      return;
    }

    createRole.mutate(
      { code: code.trim(), name: name.trim(), description: description.trim(), permissions: [] },
      {
        onSuccess: () => {
          toast('角色已创建，接着勾选权限', 'success');
          onClose();
        },
        onError: (error) => setFormError(error.message),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title={isEdit ? `编辑角色 · ${role?.name ?? ''}` : '新建角色'} width={460}>
      <form className="space-y-3 px-[18px] py-4" onSubmit={handleSubmit}>
        <FormField
          label="角色标识"
          htmlFor="role-code"
          required={!isEdit}
          hint={isEdit ? '标识是权限判定的依据，创建后不可修改' : '小写字母开头，如 content_editor'}
        >
          <TextInput
            id="role-code"
            value={code}
            disabled={isEdit}
            readOnly={isEdit}
            onChange={(event) => setCode(event.target.value)}
          />
        </FormField>

        <FormField label="角色名称" htmlFor="role-name" required>
          <TextInput
            id="role-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>

        <FormField label="说明" htmlFor="role-description" hint="一句话说明这个角色能做什么，给后来人看">
          <TextInput
            id="role-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </FormField>

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={pending}>
            取消
          </Button>
          <Button variant="primary" type="submit" loading={pending}>
            {isEdit ? '保存' : '创建'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
