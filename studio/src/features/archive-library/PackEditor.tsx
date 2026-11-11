import { useState } from 'react';
import { ArrowDown, ArrowUp, Check, Circle, Trash2 } from 'lucide-react';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import { usePackMutations } from '@/entities/template/api/pack.queries';
import { PACK_SCOPES, baseRole, rolesFor } from '@/entities/template/model/packRoles';
import type { PackBinding, PackDraftIn, PackManaged } from '@/shared/api/schema';
import { createId } from '@/shared/lib/id';
import { Button, Modal } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';

type Kind = 'product' | 'character' | 'scene';

interface Form {
  requestId: string;
  expectedRevision: number;
  kind: Kind;
  name: string;
  referenceIds: string[];
  coverReferenceId: string | null;
  wardrobeMode: 'preserve' | 'reference';
  features: string;
  instructions: string;
  bindings: PackBinding[];
  requiredRoles: string[];
  supportedScopes: string[];
}

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
  const [names, setNames] = useState<Record<string, string>>({});
  const assets = useArchiveAssets(form.kind, {scope: 'mine', page, pageSize: PAGE_SIZE});
  const {save, action} = usePackMutations();
  const busy = save.isPending || action.isPending;
  const labels = rolesFor(form.kind);
  const gaps = gapsOf(form);

  const patch = (next: Partial<Form>) => setForm(value => ({...value, ...next}));

  function add(id: string) {
    setNames(value => ({...value, [id]: assets.data?.items.find(asset => asset.id === id)?.name ?? value[id] ?? ''}));
    setForm(value => {
      if (value.referenceIds.includes(id) || value.referenceIds.length >= 10) return value;
      return {
        ...value,
        referenceIds: [...value.referenceIds, id],
        coverReferenceId: value.coverReferenceId ?? id,
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

  async function submit(publish: boolean) {
    setError('');
    const payload: PackDraftIn = {...form, bindings: form.bindings.slice(0, form.referenceIds.length)};
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
          setNames({});
          setPage(1);
        }}><option value="product">产品</option><option value="character">人物</option><option value="scene">场景</option></select></label>
        <label className="block text-xs sm:col-span-2">对象名称<input maxLength={160} required className="mt-1 w-full rounded-lg border border-line bg-panel p-2" placeholder="例如：3030 粉色" value={form.name} onChange={event => patch({name: event.target.value})} /></label>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.05fr_1fr]">
        <section aria-label="从素材库选择参考图" className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">1. 选择同类参考图<span className="ml-2 text-xs font-normal text-ink-mute">已选 {form.referenceIds.length}/10</span></h3>
          {assets.error && <ErrorState error={assets.error} title="参考素材读取失败" onRetry={() => {void assets.refetch();}} />}
          {assets.isPending && <p role="status" className="text-xs">正在读取参考图…</p>}
          {!assets.error && !assets.isPending && !assets.data?.items.length && <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">还没有可用的同类素材，请先到「我的素材」上传后回来。</p>}
          <div className="grid max-h-[320px] grid-cols-3 gap-2 overflow-y-auto pr-1">{assets.data?.items.map(asset => {
            const picked = form.referenceIds.indexOf(asset.id);
            return <button type="button" key={asset.id} aria-pressed={picked >= 0} disabled={busy || (picked < 0 && form.referenceIds.length >= 10)}
              onClick={() => picked >= 0 ? removeAt(picked) : add(asset.id)}
              className={`relative rounded-lg border p-1.5 text-left text-[11px] transition-colors ${picked >= 0 ? 'border-primary ring-1 ring-primary' : 'border-line hover:border-primary-line'} disabled:opacity-50`}>
              <span className="mb-1 block h-16"><ArchivedThumbnail url={asset.previewUrl ?? null} type="image" /></span>
              <span className="block truncate text-ink-soft">{asset.name}</span>
              {picked >= 0 && <span className="absolute right-1 top-1 rounded-full bg-primary px-1 text-[10px] text-white">{picked + 1}</span>}
            </button>;
          })}</div>
          <div className="flex items-center justify-between text-xs"><Button size="sm" disabled={page <= 1 || busy} onClick={() => setPage(page - 1)}>上一页</Button><span className="text-ink-mute">第 {page} 页</span><Button size="sm" disabled={busy || !assets.data || page * PAGE_SIZE >= assets.data.total} onClick={() => setPage(page + 1)}>下一页</Button></div>
        </section>

        <section aria-label="已选参考图与用途" className="space-y-2">
          <h3 className="text-sm font-semibold text-ink">2. 指定每张图的用途</h3>
          {!form.referenceIds.length && <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">从左侧点选参考图；顺序就是提示词里的编号顺序，可用箭头调整。</p>}
          <ol className="max-h-[320px] space-y-2 overflow-y-auto pr-1">{form.referenceIds.map((id, index) => <li key={id} className="rounded-lg border border-line bg-panel p-2">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 w-5 shrink-0 text-center text-xs font-semibold text-ink-mute">{index + 1}</span>
              <span className="h-12 w-12 shrink-0 overflow-hidden rounded"><ArchivedThumbnail url={assets.data?.items.find(asset => asset.id === id)?.previewUrl ?? null} type="image" /></span>
              <div className="min-w-0 flex-1 space-y-1">
                <p className="truncate text-xs text-ink">{assets.data?.items.find(asset => asset.id === id)?.name ?? names[id] ?? `参考图 ${index + 1}`}</p>
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
