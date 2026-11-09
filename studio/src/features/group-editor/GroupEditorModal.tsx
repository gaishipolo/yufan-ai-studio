import { useEffect, useState } from 'react';
import { Button, FormField, Modal, Tabs, TextInput, type TabItem } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { toast } from '@/shared/lib/toast';
import {
  useCreateGroup,
  useMemberCandidates,
  useSetGroupMembers,
  useSetGroupPermissions,
  useUpdateGroup,
} from '@/entities/group/api/group.queries';
import type { Group } from '@/entities/group/model/types';
import { displayNameOf } from '@/entities/user/model/types';
import { PermissionPicker } from '@/features/permission-layers/PermissionPicker';

type Tab = 'basic' | 'members' | 'permissions';

const TABS: TabItem<Tab>[] = [
  { value: 'basic', label: '基本信息' },
  { value: 'members', label: '成员' },
  { value: 'permissions', label: '权限' },
];

const EMPTY_CODES: string[] = [];

/**
 * 小组编辑（新建 / 基本信息 / 成员 / 权限三块）。
 *
 * 成员与权限都是「整组替换」，各自独立保存：改成员不该顺带把权限也提交一遍，
 * 反过来也一样。因此三块共用一个弹窗，但各有保存按钮与错误提示。
 *
 * 成员候选取第一页 100 条（后端 `MAX_PAGE_SIZE` 上限）。管理页的账号规模远小于此；
 * 真要超过时，这里的正确做法是给候选列表加搜索而不是无限翻页。
 */
export function GroupEditorModal({
  open,
  group,
  onClose,
}: {
  open: boolean;
  /** 传 null = 新建模式，此时只有「基本信息」可用 */
  group: Group | null;
  onClose: () => void;
}) {
  const isEdit = Boolean(group);
  const [tab, setTab] = useState<Tab>('basic');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const createGroup = useCreateGroup();
  const updateGroup = useUpdateGroup();
  const setMembers = useSetGroupMembers();
  const setGroupPermissions = useSetGroupPermissions();

  useEffect(() => {
    if (!open) return;
    setTab('basic');
    setName(group?.name ?? '');
    setDescription(group?.description ?? '');
    setMemberIds(group?.memberIds ?? EMPTY_CODES);
    setPermissions(group?.permissions ?? EMPTY_CODES);
    setFormError(null);
  }, [open, group]);

  const candidatesQuery = useMemberCandidates(open && isEdit);

  const run = async (action: () => Promise<unknown>, message: string, close = false) => {
    setFormError(null);
    setSaving(true);
    try {
      await action();
      toast(message, 'success');
      if (close) onClose();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const saveBasic = () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError('小组名称不能为空');
      return;
    }
    const payload = { name: trimmed, description: description.trim() || undefined };
    void run(
      () =>
        isEdit && group
          ? updateGroup.mutateAsync({ id: group.id, payload })
          : createGroup.mutateAsync(payload),
      isEdit ? '小组资料已更新' : '小组已创建，可在左侧列表继续编辑成员与权限',
      true,
    );
  };

  const saveMembers = () => {
    if (!group) return;
    void run(() => setMembers.mutateAsync({ id: group.id, userIds: memberIds }), '小组成员已更新');
  };

  const savePermissions = () => {
    if (!group) return;
    void run(
      () => setGroupPermissions.mutateAsync({ id: group.id, permissions }),
      '小组权限已更新',
    );
  };

  const toggleMember = (id: string) => {
    setMemberIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isEdit ? `编辑小组 · ${group?.name ?? ''}` : '新增分组'}
      width={640}
    >
      <div className="space-y-3 px-[18px] py-4">
        {isEdit && <Tabs items={TABS} value={tab} onChange={setTab} />}

        {tab === 'basic' && (
          <div className="space-y-3">
            <FormField label="小组名称" htmlFor="group-name" required hint="名称唯一，重名会被后端拒绝">
              <TextInput
                id="group-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </FormField>
            <FormField label="小组说明" htmlFor="group-description">
              <TextInput
                id="group-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </FormField>
            {isEdit && (
              <p className="text-[11px] leading-[1.6] text-ink-mute">
                一人可属多组；成员与权限分别在另外两个页签里保存。
              </p>
            )}
          </div>
        )}

        {tab === 'members' && isEdit && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-[11.5px] text-ink-mute">
              <span>
                已选 <span className="font-semibold tabular-nums text-ink">{memberIds.length}</span> 名成员
              </span>
              <span className="text-line-strong">·</span>
              <span>保存后整组替换</span>
            </div>
            {candidatesQuery.isLoading ? (
              <p className="py-8 text-center text-xs text-ink-mute">正在加载账号…</p>
            ) : candidatesQuery.isError ? (
              <p className="py-8 text-center text-xs text-danger">
                账号加载失败：{candidatesQuery.error.message}
              </p>
            ) : (
              <ul className="max-h-[42vh] space-y-1 overflow-auto rounded-lg border border-line-soft p-1.5">
                {(candidatesQuery.data?.items ?? []).map((user) => (
                  <li key={user.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded-[7px] px-2 py-1.5 hover:bg-panel-soft">
                      <input
                        type="checkbox"
                        className="h-3 w-3 accent-primary"
                        checked={memberIds.includes(user.id)}
                        onChange={() => toggleMember(user.id)}
                      />
                      <span className="text-xs text-ink">{displayNameOf(user)}</span>
                      <span className="text-[11px] text-ink-mute">@{user.username}</span>
                      {!user.isActive && <span className="text-[11px] text-ink-mute">（已停用）</span>}
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {tab === 'permissions' && isEdit && (
          <PermissionPicker selected={permissions} onChange={setPermissions} />
        )}

        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}

        <div className="flex items-center justify-end gap-1.5 border-t border-line-soft pt-3">
          <Button onClick={onClose} disabled={saving}>
            取消
          </Button>
          {tab === 'basic' && (
            <Button variant="primary" onClick={saveBasic} loading={saving}>
              {isEdit ? '保存' : '创建'}
            </Button>
          )}
          {/* 成员与权限是两个独立权限点：能编辑小组资料不代表能改成员或授权。
              藏掉入口而不是让人点一下吃 403，页面门禁与后端 require() 必须同口径。 */}
          {tab === 'members' && isEdit && (
            <Can
              permission="identity:group:member:manage"
              fallback={<span className="text-[11px] text-ink-mute">没有分配成员的权限</span>}
            >
              <Button variant="primary" onClick={saveMembers} loading={saving}>
                保存成员
              </Button>
            </Can>
          )}
          {tab === 'permissions' && isEdit && (
            <Can
              permission="identity:group:permission:manage"
              fallback={<span className="text-[11px] text-ink-mute">没有设置小组权限的权限</span>}
            >
              <Button variant="primary" onClick={savePermissions} loading={saving}>
                保存权限
              </Button>
            </Can>
          )}
        </div>
      </div>
    </Modal>
  );
}
