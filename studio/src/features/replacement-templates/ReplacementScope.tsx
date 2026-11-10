import type { ReplacementWorkbench } from './useReplacementWorkbench';

interface ReplacementScopeProps { model: ReplacementWorkbench; id: string }
export function ReplacementScope({ model, id }: ReplacementScopeProps) {
  const {kind, wardrobeMode, scopes, setScopes, toggle, availableScopes} = model;
  if (!model.showScope) return null;
  const selectable = !!model.boundTemplate || kind === 'product';
  return <>
        <div className="border-t border-line pt-4"><h2 className="mb-3 font-semibold text-ink">替换范围</h2>
          {!model.boundTemplate && kind === 'character' && <p className="rounded-lg bg-panel-soft p-3 text-xs leading-relaxed text-ink-soft">{wardrobeMode === 'reference' ? '按素材包设定，同时替换人物与参考服装。' : '仅替换人物身份，保留原穿搭和商品。'}</p>}
          {selectable && (availableScopes.length
            ? <fieldset aria-describedby={!scopes.length ? `${id}-scope-hint` : undefined} className="grid grid-cols-2 gap-3"><legend className="mb-2 text-xs text-ink-soft">原片中允许替换的位置</legend>{availableScopes.map((scope, index) => <label htmlFor={`${id}-scope-${index}`} key={scope} className="flex gap-2 text-xs text-ink"><input id={`${id}-scope-${index}`} aria-describedby={!scopes.length ? `${id}-scope-hint` : undefined} type="checkbox" checked={scopes.includes(scope)} onChange={() => setScopes(toggle(scopes, scope))} />{scope}</label>)}</fieldset>
            : <p className="rounded-lg bg-panel-soft p-3 text-xs leading-relaxed text-ink-soft">该素材包还没有声明可替换范围，暂时无法用于产品替换。请联系素材专员在封装里补齐并重新发布。</p>)}
          {selectable && availableScopes.length > 0 && <p className="mt-2 text-[11px] text-ink-mute">仅勾选需要替换的位置，未勾选的位置保持原样。切换产品不会自动扩大已选范围。</p>}
          {!model.boundTemplate && kind === 'scene' && <p className="rounded-lg bg-panel-soft p-3 text-xs leading-6 text-ink-soft">仅替换背景环境、建筑、材质与陈设；保留原片人物、商品、手持物及前景交互关系。</p>}
        </div>
        {selectable && availableScopes.length > 0 && !scopes.length && <p id={`${id}-scope-hint`} className="text-xs text-ink-mute">请选择至少一个产品替换位置</p>}
  </>;
}
