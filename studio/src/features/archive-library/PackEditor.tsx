import { useState } from 'react';
import { ArrowDown, ArrowUp, Check, Circle, Trash2 } from 'lucide-react';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibrarySelection } from '@/entities/library/api/library.queries';
import { usePackMutations } from '@/entities/template/api/pack.queries';
import { PACK_SCOPES, baseRole, rolesFor } from '@/entities/template/model/packRoles';
import type { PackBinding, PackDraftIn, PackManaged } from '@/shared/api/schema';
import { createId } from '@/shared/lib/id';
import { Button, Modal } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';

type Kind = 'product' | 'character' | 'scene';

/**
 * 参考图 id 属于哪套素材库。**只按字段名判，不按「查得到哪张表」猜**：
 * 两套 id 都是 32 位 hex，形状完全一样，猜错的方向是「把别人的图当自己的」
 * 或者「编辑一次封装就把同一批 id 重新解释成另一侧」。
 */
type Space = 'v1' | 'v2';

interface Form {
  requestId: string;
  expectedRevision: number;
  kind: Kind;
  name: string;
  space: Space;
  referenceIds: string[];
  coverReferenceId: string | null;
  wardrobeMode: 'preserve' | 'reference';
  features: string;
  instructions: string;
  bindings: PackBinding[];
  requiredRoles: string[];
  supportedScopes: string[];
}

/** 候选图统一形状：两套素材库字段名不同，列表只认这三样。 */
interface Candidate {
  id: string;
  name: string;
  previewUrl: string | null;
}

/** 已选图的展示信息缓存：翻页后当前页里找不到它，名称和缩略图仍要画得出来。 */
type PickedInfo = Record<string, { name: string; previewUrl: string | null }>;

/** 一页只放 24 张，翻页不影响已选：选择状态只存在表单里。 */
const PAGE_SIZE = 24;

function initialForm(pack: PackManaged | null): Form {
  const kind = (pack?.kind ?? 'product') as Kind;
  const referenceIds = pack?.referenceIds ?? [];
  const bindings = pack?.bindings ?? [];
  return {
    // 编辑既有包必须沿用它自己的 id，否则会另存出一份新草稿。
    requestId: pack && /^[a-f0-9]{32}$/.test(pack.id) ? pack.id : createId(),
    expectedRevision: pack?.revision ?? 0,
    kind,
    name: pack?.name ?? '',
    // 新建一律写资产库 v2；编辑旧包沿用它的空间，否则一次保存就会把 v1 id 当 v2 重解释。
    space: pack ? (pack.referenceIdSpace === 'v2' ? 'v2' : 'v1') : 'v2',
    referenceIds,
    coverReferenceId: pack?.coverReferenceId ?? referenceIds[0] ?? null,
    wardrobeMode: pack?.wardrobeMode === 'reference' ? 'reference' : 'preserve',
    features: pack?.features ?? '',
    instructions: pack?.instructions ?? '',
    bindings: referenceIds.map((_, index) => ({
      role: bindings[index]?.role || baseRole(kind),
      description: bindings[index]?.description ?? '',
    })),
    requiredRoles: pack?.requiredRoles?.length ? pack.requiredRoles : [baseRole(kind)],
    supportedScopes: pack?.supportedScopes ?? [],
  };
}

/** 编辑既有包时把它的缩略图与名称先灌进缓存：首屏不该出现「参考图 1 / 参考图 2」。 */
function initialPicked(pack: PackManaged | null): PickedInfo {
  const seeded: PickedInfo = {};
  (pack?.referenceIds ?? []).forEach((id, index) => {
    seeded[id] = {
      name: pack?.referenceNames?.[index] ?? '',
      previewUrl: pack?.previewUrls?.[index] ?? null,
    };
  });
  return seeded;
}

/** 新加一张图时优先补没被占用的用途，避免整包全是主图。 */
function suggestRole(form: Form): string {
  const used = new Set(form.bindings.map(binding => binding.role));
  return Object.keys(rolesFor(form.kind)).find(role => !used.has(role)) ?? baseRole(form.kind);
}

function gapsOf(form: Form): string[] {
  const gaps: string[] = [];
  if (!form.name.trim()) gaps.push('对象名称');
  if (form.referenceIds.length < 1 || form.referenceIds.length > 10) gaps.push('1–10 张参考图');
  const labels = rolesFor(form.kind);
  const present = new Set(form.bindings.map(binding => binding.role));
  const missing = Object.keys(labels).filter(role => form.requiredRoles.includes(role) && !present.has(role));
  if (missing.length) gaps.push(`缺少${missing.map(role => labels[role]).join('、')}`);
  if (form.kind === 'product' && !form.supportedScopes.length) gaps.push('产品替换范围');
  return gaps;
}

export function PackEditor({pack, onClose}: {pack: PackManaged | null; onClose: () => void}) {
  const [form, setForm] = useState<Form>(() => initialForm(pack));
  const [page, setPage] = useState(1);
  const [error, setError] = useState('');
  const [picked, setPicked] = useState<PickedInfo>(() => initialPicked(pack));
  const legacy = useArchiveAssets(form.kind, {
    scope: 'mine',
    page,
    pageSize: PAGE_SIZE,
    enabled: form.space === 'v1',
  });
  const library = useLibrarySelection({
    kind: 'image',
    page,
    pageSize: PAGE_SIZE,
    enabled: form.space === 'v2',
  });
  /* 「从资产库封装导入」：同一产品的三视图/细节图在库里已经是一个封装，
     逐张勾选既慢又容易漏，直接把成员按用途带进来。只在 v2 空间可用——
     v1 没有封装这个概念。 */
  const libraryPacks = useLibrarySelection({
    kind: 'pack',
    page: 1,
    pageSize: 50,
    includePacks: true,
    enabled: form.space === 'v2',
  });
  const [importing, setImporting] = useState(false);
  const [importNote, setImportNote] = useState('');
  const {save, action} = usePackMutations();
  const busy = save.isPending || action.isPending;
  const labels = rolesFor(form.kind);
  const gaps = gapsOf(form);
  /* 两套数据源只有字段名不同；这里收敛成同一形状，下面的渲染一个字都不用改。 */
  const candidates: Candidate[] = form.space === 'v2'
    ? (library.data?.items ?? []).map(item => ({
        id: item.id,
        name: item.name,
        // 缩略图端点是带鉴权的取图接口，与本页用的 `ArchivedThumbnail` 走同一条路；
        // `coverUrl` 是对象存储的公开地址（`/static/...`），拼不进 API 相对路径。
        previewUrl: item.previewUrl ?? null,
      }))
    : (legacy.data?.items ?? []).map(item => ({
        id: item.id,
        name: item.name,
        previewUrl: item.previewUrl ?? null,
      }));
  const total = (form.space === 'v2' ? library.data?.total : legacy.data?.total) ?? 0;
  const listError = form.space === 'v2' ? library.error : legacy.error;
  const listPending = form.space === 'v2' ? library.isPending : legacy.isPending;
  const refetch = () => {void (form.space === 'v2' ? library.refetch() : legacy.refetch());};

  const patch = (next: Partial<Form>) => setForm(value => ({...value, ...next}));

  function add(candidate: Candidate) {
    setPicked(value => ({...value, [candidate.id]: {name: candidate.name, previewUrl: candidate.previewUrl}}));
    setForm(value => {
      if (value.referenceIds.includes(candidate.id) || value.referenceIds.length >= 10) return value;
      return {
        ...value,
        referenceIds: [...value.referenceIds, candidate.id],
        coverReferenceId: value.coverReferenceId ?? candidate.id,
        bindings: [...value.bindings, {role: suggestRole(value), description: ''}],
      };
    });
  }

  function removeAt(index: number) {
    setForm(value => {
      const referenceIds = value.referenceIds.filter((_, position) => position !== index);
      return {
        ...value,
        referenceIds,
        bindings: value.bindings.filter((_, position) => position !== index),
        coverReferenceId: value.coverReferenceId === value.referenceIds[index] ? referenceIds[0] ?? null : value.coverReferenceId,
      };
    });
  }

  function move(index: number, delta: number) {
    setForm(value => {
      const target = index + delta;
      if (target < 0 || target >= value.referenceIds.length) return value;
      const referenceIds = [...value.referenceIds];
      const bindings = [...value.bindings];
      [referenceIds[index], referenceIds[target]] = [referenceIds[target], referenceIds[index]];
      [bindings[index], bindings[target]] = [bindings[target], bindings[index]];
      return {...value, referenceIds, bindings};
    });
  }

  function setBinding(index: number, next: Partial<PackBinding>) {
    setForm(value => ({...value, bindings: value.bindings.map((item, position) => position === index ? {...item, ...next} : item)}));
  }

  function toggleRequired(role: string) {
    if (role === baseRole(form.kind)) return;
    setForm(value => ({
      ...value,
      requiredRoles: value.requiredRoles.includes(role)
        ? value.requiredRoles.filter(item => item !== role)
        : [...value.requiredRoles, role],
    }));
  }

  /**
   * 把一个资产库封装整包带进来。
   *
   * 用途**照成员自己的 `role` 走**，认不出来就退回基准用途并在界面上点出来——
   * 不按成员位置猜「第一张是主图」：`{{Mixed N}}` 与用途绑死，猜错会生成
   * 「拿细节图当主图」的成片且不报错。成员超过 10 张只取前 10 张并说明。
   */
  async function importLibraryPack(packId: string) {
    if (!packId || busy || importing) return;
    setError('');
    setImportNote('');
    setImporting(true);
    try {
      const detail = await libraryApi.packDetail(packId);
      const images = (detail.packItems ?? []).filter(
        item => item.kind === 'image' && item.state === 'active',
      );
      if (!images.length) {
        setError('这个封装里没有可用的图片，请先到资产库补齐成员');
        return;
      }
      const kept = images.slice(0, 10);
      const labels = rolesFor(form.kind);
      const unmapped: string[] = [];
      const bindings = kept.map((item, index) => {
        const mapped = item.role && item.role in labels ? item.role : '';
        if (!mapped) unmapped.push(item.name || `第 ${index + 1} 张`);
        return {role: mapped || baseRole(form.kind), description: item.description ?? ''};
      });
      setPicked(value => {
        const next = {...value};
        kept.forEach((item, index) => {
          next[item.assetId] = {
            name: item.name || `参考图 ${index + 1}`,
            // 缩略图端点要鉴权，走 `ArchivedThumbnail` 同一条取图路径
            previewUrl: `/library/assets/${item.assetId}/thumbnail`,
          };
        });
        return next;
      });
      setForm(value => ({
        ...value,
        referenceIds: kept.map(item => item.assetId),
        coverReferenceId: kept[0].assetId,
        bindings,
      }));
      const notes: string[] = [];
      if (unmapped.length) notes.push(`这些图的用途需要你确认：${unmapped.join('、')}`);
      if (images.length > kept.length) notes.push(`成员 ${images.length} 张，只带入前 10 张`);
      setImportNote(notes.join('；'));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '封装读取失败，请刷新后重试');
    } finally {
      setImporting(false);
    }
  }

  async function submit(publish: boolean) {
    setError('');
    /* 字段名由空间决定：`referenceLibraryIds` / `coverLibraryId` 进资产库 v2，
       `referenceIds` / `coverReferenceId` 仍走旧素材库。两组互斥，后端也这么判。 */
    const refs = form.space === 'v2'
      ? {referenceLibraryIds: form.referenceIds, coverLibraryId: form.coverReferenceId}
      : {referenceIds: form.referenceIds, coverReferenceId: form.coverReferenceId};
    const payload: PackDraftIn = {
      requestId: form.requestId,
      expectedRevision: form.expectedRevision,
      kind: form.kind,
      name: form.name,
      wardrobeMode: form.wardrobeMode,
      features: form.features,
      instructions: form.instructions,
      requiredRoles: form.requiredRoles,
      supportedScopes: form.supportedScopes,
      bindings: form.bindings.slice(0, form.referenceIds.length),
      ...refs,
    };
    try {
      const saved = await save.mutateAsync(payload);
      if (!publish) { onClose(); return; }
      await action.mutateAsync({id: saved.id, action: 'publish', expectedRevision: saved.revision ?? 0});
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '保存未完成，请刷新后核对');
    }
  }

  return <Modal open onClose={onClose} title={pack ? '编辑封装草稿／新版本' : '创建封装草稿'} width={1080}>
    <form className="space-y-5 p-4" onSubmit={event => {event.preventDefault(); void submit(false);}}>
      <p className="text-xs leading-relaxed text-ink-mute">每张图选一个用途：主图定外观，多视图补结构，细节图补局部。保存草稿不会发布，发布后普通用户才能在模板页选到它。</p>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block text-xs">对象类型<select className="mt-1 w-full rounded-lg border border-line bg-panel p-2" value={form.kind} disabled={!!pack || busy} onChange={event => {
          const kind = event.target.value as Kind;
          setForm({...initialForm(null), kind, name: form.name, requiredRoles: [baseRole(kind)]});
          setPicked({});
          setPage(1);
        }}><option value="product">产品</option><option value="character">人物</option><option value="scene">场景</option></select></label>
        <label className="block text-xs sm:col-span-2">对象名称<input maxLength={160} required className="mt-1 w-full rounded-lg border border-line bg-panel p-2" placeholder="例如：3030 粉色" value={form.name} onChange={event => patch({name: event.target.value})} /></label>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.05fr_1fr]">
        <section aria-label="从素材库选择参考图" className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">1. 选择同类参考图<span className="ml-2 text-xs font-normal text-ink-mute">已选 {form.referenceIds.length}/10</span></h3>
          {form.space === 'v2' && <label className="block text-xs">从资产库封装导入
            <select className="mt-1 w-full rounded-lg border border-line bg-panel p-2" value="" disabled={busy || importing}
              onChange={event => {void importLibraryPack(event.target.value);}}>
              <option value="">{importing ? '正在读取封装…' : '已在资产库里封好的三视图／细节图'}</option>
              {(libraryPacks.data?.items ?? []).map(pack => <option key={pack.id} value={pack.id}>{pack.name}</option>)}
            </select>
            {importNote && <span className="mt-1 block text-[11px] text-ink-mute">{importNote}</span>}
          </label>}
          {listError && <ErrorState error={listError} title="参考素材读取失败" onRetry={refetch} />}
          {listPending && <p role="status" className="text-xs">正在读取参考图…</p>}
          {!listError && !listPending && !candidates.length && <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">还没有可用的同类素材，请先到「我的素材」上传后回来。</p>}
          <div className="grid max-h-[320px] grid-cols-3 gap-2 overflow-y-auto pr-1">{candidates.map(candidate => {
            const at = form.referenceIds.indexOf(candidate.id);
            return <button type="button" key={candidate.id} aria-pressed={at >= 0} disabled={busy || (at < 0 && form.referenceIds.length >= 10)}
              onClick={() => at >= 0 ? removeAt(at) : add(candidate)}
              className={`relative rounded-lg border p-1.5 text-left text-[11px] transition-colors ${at >= 0 ? 'border-primary ring-1 ring-primary' : 'border-line hover:border-primary-line'} disabled:opacity-50`}>
              <span className="mb-1 block h-16"><ArchivedThumbnail url={candidate.previewUrl} type="image" /></span>
              <span className="block truncate text-ink-soft">{candidate.name}</span>
              {at >= 0 && <span className="absolute right-1 top-1 rounded-full bg-primary px-1 text-[10px] text-white">{at + 1}</span>}
            </button>;
          })}</div>
          <div className="flex items-center justify-between text-xs"><Button size="sm" disabled={page <= 1 || busy} onClick={() => setPage(page - 1)}>上一页</Button><span className="text-ink-mute">第 {page} 页</span><Button size="sm" disabled={busy || page * PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>下一页</Button></div>
        </section>

        <section aria-label="已选参考图与用途" className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">2. 指定每张图的用途</h3>
          {!form.referenceIds.length && <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">从左侧点选参考图；顺序就是提示词里的编号顺序，可用箭头调整。</p>}
          <ol className="max-h-[320px] space-y-2 overflow-y-auto pr-1">{form.referenceIds.map((id, index) => <li key={id} className="rounded-lg border border-line bg-panel p-2">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 w-5 shrink-0 text-center text-xs font-semibold text-ink-mute">{index + 1}</span>
              <span className="h-12 w-12 shrink-0 overflow-hidden rounded"><ArchivedThumbnail url={picked[id]?.previewUrl ?? null} type="image" /></span>
              <div className="min-w-0 flex-1 space-y-1">
                <p className="truncate text-xs text-ink">{picked[id]?.name || `参考图 ${index + 1}`}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  <select aria-label={`第 ${index + 1} 张的用途`} className="rounded border border-line bg-panel px-1.5 py-1 text-[11px]" value={form.bindings[index]?.role ?? baseRole(form.kind)} onChange={event => setBinding(index, {role: event.target.value})} disabled={busy}>
                    {Object.entries(labels).map(([role, label]) => <option key={role} value={role}>{label}</option>)}
                  </select>
                  <label className="flex items-center gap-1 text-[11px] text-ink-soft"><input type="radio" name="cover" checked={form.coverReferenceId === id} onChange={() => patch({coverReferenceId: id})} disabled={busy} />主图</label>
                </div>
                <input aria-label={`第 ${index + 1} 张的补充说明`} maxLength={200} placeholder="补充说明（可选）：这张图特有的事实" className="w-full rounded border border-line bg-panel-soft px-1.5 py-1 text-[11px]" value={form.bindings[index]?.description ?? ''} onChange={event => setBinding(index, {description: event.target.value})} disabled={busy} />
              </div>
              <div className="flex shrink-0 flex-col gap-0.5">
                <Button size="sm" variant="ghost" aria-label="上移" disabled={busy || index === 0} onClick={() => move(index, -1)}><ArrowUp size={12} /></Button>
                <Button size="sm" variant="ghost" aria-label="下移" disabled={busy || index === form.referenceIds.length - 1} onClick={() => move(index, 1)}><ArrowDown size={12} /></Button>
                <Button size="sm" variant="ghost" aria-label="移除" disabled={busy} onClick={() => removeAt(index)}><Trash2 size={12} /></Button>
              </div>
            </div>
          </li>)}</ol>
        </section>
      </div>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">3. 必备用途<span className="ml-2 text-xs font-normal text-ink-mute">缺了就不能发布</span></h3>
          <div className="flex flex-wrap gap-2">{Object.entries(labels).map(([role, label]) => {
            const fixed = role === baseRole(form.kind);
            const checked = form.requiredRoles.includes(role);
            return <label key={role} className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs ${checked ? 'border-primary bg-primary/5 text-ink' : 'border-line text-ink-soft'} ${fixed ? 'opacity-70' : 'cursor-pointer'}`}>
              <input type="checkbox" checked={checked} disabled={fixed || busy} onChange={() => toggleRequired(role)} />{label}{fixed && <span className="text-[10px] text-ink-mute">固定</span>}
            </label>;
          })}</div>
        </div>
        {form.kind === 'product' && <div className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">4. 已审核的替换范围<span className="ml-2 text-xs font-normal text-ink-mute">用户只能在此范围内选择</span></h3>
          <div className="grid grid-cols-2 gap-2">{Object.entries(PACK_SCOPES).map(([key, label]) => {
            const checked = form.supportedScopes.includes(key);
            return <label key={key} className="flex items-center gap-1.5 text-xs text-ink-soft"><input type="checkbox" checked={checked} disabled={busy} onChange={() => patch({supportedScopes: checked ? form.supportedScopes.filter(item => item !== key) : [...form.supportedScopes, key]})} />{label}</label>;
          })}</div>
        </div>}
      </section>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs">特征描述<textarea className="mt-1 w-full rounded-lg border border-line bg-panel p-2" maxLength={2000} placeholder="这款对象稳定的外观事实，换哪个原片都成立" value={form.features} onChange={event => patch({features: event.target.value})} /></label>
        <label className="block text-xs">固定使用说明<textarea className="mt-1 w-full rounded-lg border border-line bg-panel p-2" maxLength={2000} placeholder="每次生成都要遵守的约束" value={form.instructions} onChange={event => patch({instructions: event.target.value})} /></label>
        {form.kind === 'character' && <label className="block text-xs">服装模式<select className="mt-1 w-full rounded-lg border border-line bg-panel p-2" value={form.wardrobeMode} onChange={event => patch({wardrobeMode: event.target.value === 'reference' ? 'reference' : 'preserve'})}><option value="preserve">保留原服装</option><option value="reference">使用参考服装</option></select></label>}
      </div>

      <section aria-label="发布前检查" className="rounded-lg border border-line bg-panel-soft p-3">
        <p className="mb-1 text-xs font-semibold text-ink">发布前检查</p>
        {gaps.length ? <ul className="space-y-0.5 text-xs text-ink-soft">{gaps.map(gap => <li key={gap} className="flex items-center gap-1.5"><Circle size={10} aria-hidden />{gap}</li>)}</ul>
          : <p className="flex items-center gap-1.5 text-xs text-primary"><Check size={12} aria-hidden />已齐全，可以发布</p>}
      </section>

      {error && <p role="alert" className="rounded-lg bg-danger/5 p-2 text-xs text-danger">{error}</p>}
      {save.error && <p role="alert" className="text-xs text-danger">{save.error.message}</p>}
      {action.error && <p role="alert" className="text-xs text-danger">{action.error.message}。状态未确认，请刷新核对后继续。</p>}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" loading={save.isPending && !action.isPending} disabled={busy}>保存草稿</Button>
        <Button variant="primary" loading={action.isPending} disabled={busy || !!gaps.length} onClick={() => {void submit(true);}}>保存并发布</Button>
        <span className="text-xs text-ink-mute">{gaps.length ? `还需补齐 ${gaps.length} 项才能发布` : '保存后立即发布，用户可马上选到'}</span>
        <Button variant="ghost" className="ml-auto" disabled={busy} onClick={onClose}>取消</Button>
      </div>
    </form>
  </Modal>;
}
