import { useEffect, useMemo, useState } from 'react';
import { Button, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import {
  useSetTeamPermissions,
  useSetUserPermissions,
  useTeamPermissions,
  useUserPermissions,
} from '@/entities/group/api/group.queries';
import { PermissionPicker } from './PermissionPicker';

/**
 * 团队 / 个人两层的授权弹窗（小组层在 `GroupEditorModal` 的「权限」页签里编辑）。
 *
 * 两层写接口的入参形状完全一致（`{ permissions }` 整组替换），差异只有
 * 「取当前值」「调哪个 mutation」「哪些码不能放」，因此这里按 target.kind
 * 分发到两个薄壳，共用一份 `LayerForm`。
 *
 * 有效权限是四层并集、没有 deny：这里的「清空」只清掉本层的授予，
 * 不会削掉角色或别的层给的权限 —— 文案必须说清楚，否则管理员会误以为
 * 取消勾选就等于禁用。
 */
export type LayerTarget = { kind: 'team' } | { kind: 'user'; id: string; name: string };

interface LayerFormProps {
  title: string;
  description: string;
  /** 引用必须稳定（来自 query.data 或 useMemo），否则 effect 会每次渲染重置勾选 */
  currentCodes: string[];
  forbidden?: (code: string) => boolean;
  forbiddenHint?: string;
  loading?: boolean;
  save: (codes: string[]) => Promise<void>;
  onClose: () => void;
}

const EMPTY_CODES: string[] = [];

function LayerForm({
  title,
  description,
  currentCodes,
  forbidden,
  forbiddenHint,
  loading,
  save,
  onClose,
}: LayerFormProps) {
  const catalogQuery = usePermissionCatalog();
  const [selected, setSelected] = useState<string[]>(currentCodes);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const knownCodes = useMemo(() => {
    const codes = new Set<string>();
    for (const group of catalogQuery.data?.groups ?? []) {
      for (const item of group.items) codes.add(item.code);
    }
    return codes;
  }, [catalogQuery.data]);

  // 目录就绪后过滤已下线的码：三层都是整组替换，留着失效码会让保存被后端拒绝，
  // 管理员只能先手动找出那条看不见的码。角色弹窗的 orphanedPermissions 同理。
  useEffect(() => {
    if (!knownCodes.size) return;
    setSelected(currentCodes.filter((code) => code === '*' || knownCodes.has(code)));
    setFormError(null);
  }, [currentCodes, knownCodes]);

  const handleSave = async () => {
    setFormError(null);
    setSaving(true);
    try {
      await save(selected);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open onClose={onClose} title={title} width={640}>
      <div className="space-y-3 px-[18px] py-4">
        <p className="rounded-lg bg-panel-soft px-2.5 py-2 text-[11.5px] leading-[1.6] text-ink-soft">
          {description}
        </p>
        {loading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取当前授权…</p>
        ) : (
          <PermissionPicker
            selected={selected}
            onChange={setSelected}
            forbidden={forbidden}
            forbiddenHint={forbiddenHint}
          />
        )}
        {formError && (
          <p className="rounded-lg border border-[#fad7d4] bg-[#feeceb] px-2.5 py-2 text-[11.5px] text-[#d94036]" role="alert">
            {formError}
          </p>
        )}
        <div className="flex items-center justify-end gap-1.5 pt-1">
          <Button onClick={onClose} disabled={saving}>
            取消
          </Button>
          <Button variant="primary" onClick={() => void handleSave()} loading={saving} disabled={loading}>
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/** 团队层禁 `*`（全员超管）与 `identity:` 前缀（给全站发组织管理权） */
const teamForbidden = (code: string) => code === '*' || code.startsWith('identity:');
const TEAM_HINT = '团队层面向所有启用账号，禁止授予组织管理类权限（identity:*）与通配 *；这些权限请用小组或个人层授予。';

function TeamLayer({ onClose }: { onClose: () => void }) {
  const query = useTeamPermissions();
  const setPermissions = useSetTeamPermissions();
  const codes = useMemo(() => query.data ?? EMPTY_CODES, [query.data]);

  return (
    <LayerForm
      title="团队基础权限"
      description="团队 = 所有启用账号。这里授予的权限人人都有；取消勾选只移除团队这一层的授予，不会削掉小组、个人或角色已给的权限。"
      currentCodes={codes}
      forbidden={teamForbidden}
      forbiddenHint={TEAM_HINT}
      loading={query.isLoading}
      onClose={onClose}
      save={async (permissions) => {
        await setPermissions.mutateAsync(permissions);
        toast('团队基础权限已更新', 'success');
        onClose();
      }}
    />
  );
}

function UserLayer({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const query = useUserPermissions(id);
  const setPermissions = useSetUserPermissions();
  const codes = useMemo(() => query.data ?? EMPTY_CODES, [query.data]);

  return (
    <LayerForm
      title={`个人权限 · ${name}`}
      description="单独授予的权限与团队、小组、角色取并集，只增不减：在这里取消勾选不会移除其它层已授予的权限。"
      currentCodes={codes}
      loading={query.isLoading}
      onClose={onClose}
      save={async (permissions) => {
        await setPermissions.mutateAsync({ id, permissions });
        toast('个人权限已更新', 'success');
        onClose();
      }}
    />
  );
}

export function LayerPermissionsModal({
  open,
  target,
  onClose,
}: {
  open: boolean;
  target: LayerTarget | null;
  onClose: () => void;
}) {
  // 小组权限走 `GroupEditorModal` 的「权限」页签（那里同时能改成员，不必叠两层弹窗）。
  // 关闭时整体卸载：下次打开拿到的是全新的 initial state，不会残留上一次的勾选。
  // hooks 规则没问题 —— 本组件自己不调 hook，两个薄壳各自调自己的。
  if (!open || !target) return null;
  if (target.kind === 'team') return <TeamLayer onClose={onClose} />;
  return <UserLayer id={target.id} name={target.name} onClose={onClose} />;
}
