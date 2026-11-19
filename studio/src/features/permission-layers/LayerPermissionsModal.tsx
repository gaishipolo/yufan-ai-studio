import { useEffect, useMemo, useState } from 'react';
import { Button, Modal, Notice } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { usePermissionCatalog } from '@/entities/role/api/role.queries';
import {
  useEffectivePermissions,
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
  /** 别的层已经授予的权限：`权限码 → 来源标签`；本层之外的东西也要看得见 */
  inherited?: Record<string, string[]>;
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
  inherited,
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
    <Modal open onClose={onClose} title={title} width={1000}>
      <div className="space-y-3 px-[18px] py-4">
        <p className="rounded-lg bg-panel-soft px-2.5 py-2 text-meta leading-[1.6] text-ink-soft">
          {description}
        </p>
        {loading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取当前授权…</p>
        ) : (
          <PermissionPicker
            selected={selected}
            onChange={setSelected}
            inherited={inherited}
            forbidden={forbidden}
            forbiddenHint={forbiddenHint}
          />
        )}
        {formError && (
          <Notice tone="error">{formError}</Notice>
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

/**
 * 团队层禁三类：
 * - `*`：等于把全站变成超管；
 * - `identity:` 前缀：等于给所有人发「管理组织」的票；
 * - `page:` 前缀：团队层没有 deny，页面放进来就再也拿不掉，
 *   而「关掉某个人进某一页」正是页面权限存在的意义（详见 docs/PERMISSION_PAGES.md）。
 */
const teamForbidden = (code: string) =>
  code === '*' || code.startsWith('identity:') || code.startsWith('page:');
/**
 * 禁用项 `title` 里的**短**理由。
 *
 * 完整版（哪一层能授什么）写在弹窗的 `description` 里说一次；
 * 每个禁用项的 `title` 只说「这里不能给」，不重复整段规则 ——
 * 同一句话出现 N+1 次就是噪音。
 */
const TEAM_ITEM_HINT = '团队层不能授予该权限';

function TeamLayer({ onClose }: { onClose: () => void }) {
  const query = useTeamPermissions();
  const setPermissions = useSetTeamPermissions();
  const codes = useMemo(() => query.data ?? EMPTY_CODES, [query.data]);

  return (
    <LayerForm
      title="团队基础权限"
      /* 两句都是**约束**，不是解释：
         1. 团队层覆盖所有启用账号（范围）；
         2. 组织管理类权限（identity:*）与通配 * 不能在这一层授（去哪授）。 */
      description="团队 = 所有启用账号。这里授予的权限人人都有；取消勾选只移除团队这一层的授予，不会削掉小组、个人或角色已给的权限。组织管理类权限（identity:*）、页面权限（page:*）与通配 * 不能在团队层授予，请用小组或个人层。"
      currentCodes={codes}
      forbidden={teamForbidden}
      forbiddenHint={TEAM_ITEM_HINT}
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
  const effectiveQuery = useEffectivePermissions(id);
  const codes = useMemo(() => query.data ?? EMPTY_CODES, [query.data]);

  /**
   * 别的层（团队 / 小组 / 角色）已经授予的权限。
   *
   * 为什么必须有它：这一层是「个人授权」，而多数人的权限其实来自角色 ——
   * 只显示本层的话，打开弹窗就是一片空白，管理员会读成「这个人什么权限都没有」。
   * 显示出来并标明来源，既回答了「他已有的权限是什么」，也说清了
   * 「本层取消勾选不会收回它」（个人层只是并集里的一层）。
   */
  const inherited = useMemo(() => {
    const data = effectiveQuery.data;
    if (!data) return undefined;
    const personal = new Set(codes);
    const map: Record<string, string[]> = {};
    const add = (code: string, source: string) => {
      if (code === '*' || personal.has(code)) return;
      const list = map[code] ?? [];
      if (!list.includes(source)) list.push(source);
      map[code] = list;
    };
    for (const code of data.team ?? []) add(code, '团队');
    for (const group of data.groups ?? []) {
      for (const code of group.permissions ?? []) add(code, `小组·${group.name}`);
    }
    for (const code of data.roles ?? []) add(code, '角色');
    return map;
  }, [effectiveQuery.data, codes]);

  return (
    <LayerForm
      title={`个人权限 · ${name}`}
      /* 只留「取消勾选只清本层」这半句 —— 并集模型在「有效权限」弹窗里
         已经摊开说过了，这里再说一遍就是同一件事说两遍。 */
      description="在这里取消勾选只移除个人这一层的授予，不会削掉团队、小组或角色已给的权限。标着「已有」的那些来自别的层，同样不受这里的操作影响。"
      currentCodes={codes}
      inherited={inherited}
      loading={query.isLoading || effectiveQuery.isLoading}
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
