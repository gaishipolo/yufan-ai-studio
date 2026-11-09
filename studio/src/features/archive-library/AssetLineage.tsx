import { Copy, GitBranch } from 'lucide-react';
import { Chip } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { copyText } from '@/shared/lib/clipboard';
import { formatDateTime } from '@/shared/lib/format';
import { useAssetLineage } from '@/entities/asset/api/lineage.queries';
import type { LineageNode, RecipeOut } from '@/shared/api/schema';

/**
 * 生成血缘：怎么来的、拿什么生成的、还生出了什么、谁在用它。
 *
 * 四段分开显示，因为它们是四个不同的问题，而"合成一棵树"会暗示有唯一根——
 * 实际上同一张图可以被 20 次生成同时引用，那 20 个结果之间没有任何关系。
 *
 * 上传件到这里显示一句话而不是空区块：**血缘为空是事实，不是加载失败**。
 * 反过来，如果加载失败却显示"没有生成记录"，用户会把一次网络错误当成
 * "这张图来路不明"——所以错误态与空态必须分开。
 */
export function AssetLineage({assetId, hasRecipe}: {assetId: string; hasRecipe: boolean}) {
  const query = useAssetLineage(assetId);

  if (!hasRecipe) {
    return (
      <p className="text-[11.5px] text-ink-mute">
        这条素材是上传的，没有生成配方。上传件不会被记录"怎么来的"，因此也没有可复现的参数。
      </p>
    );
  }
  if (query.isPending) return <p role="status" className="text-[11.5px] text-ink-mute">正在读取生成记录…</p>;
  if (query.error) {
    return (
      <p role="alert" className="text-[11.5px] text-danger">
        生成记录读取失败：{query.error.message}
      </p>
    );
  }
  const data = query.data;
  if (!data?.recipe) {
    return <p className="text-[11.5px] text-ink-mute">配方已不存在（可能被清理），这张图的内容与参数无法再复现。</p>;
  }

  return (
    <div className="space-y-2.5">
      <RecipeBlock recipe={data.recipe} />
      {(data.references?.length ?? 0) > 0 && (
        <div>
          <p className="mb-1 text-[11px] text-ink-mute">参考图（按输入顺序）</p>
          <ul className="space-y-1">
            {(data.references ?? []).map((ref) => (
              <li key={`${ref.assetId}-${ref.sortOrder ?? 0}`} className="flex items-center gap-1.5 text-[11.5px] text-ink-soft">
                <Chip>{ref.role ?? 'reference'}</Chip>
                <span className="truncate" title={ref.name ?? ref.assetId}>{ref.name || ref.assetId}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {(data.parents?.length ?? 0) > 0 && (
        <NodeList title="上游：照它再生成的" nodes={data.parents ?? []} />
      )}
      {(data.children?.length ?? 0) > 0 && (
        <NodeList title="下游：由它衍生出的" nodes={data.children ?? []} />
      )}
      <UsageList usages={data.usedBy ?? []} />
    </div>
  );
}

function RecipeBlock({recipe}: {recipe: RecipeOut}) {
  const copy = async () => {
    const ok = await copyText(recipe.prompt ?? '');
    toast(ok ? '提示词已复制' : '复制失败：浏览器拒绝了剪贴板访问', ok ? 'success' : 'error');
  };
  const params = Object.entries(recipe.params ?? {}).filter(([, value]) => value != null && value !== '');
  return (
    <div className="space-y-1.5 rounded-lg border border-line-soft bg-panel-soft p-2">
      <p className="flex flex-wrap items-center gap-1.5 text-[11.5px] text-ink-soft">
        <span className="font-semibold text-ink">{recipe.model || '未知模型'}</span>
        {recipe.provider && <Chip>{recipe.provider}</Chip>}
        {recipe.modelVersion && <Chip>{recipe.modelVersion}</Chip>}
        {recipe.createdAt && <span className="text-ink-mute">{formatDateTime(recipe.createdAt)}</span>}
      </p>
      <div>
        <div className="mb-1 flex items-center gap-1.5">
          <span className="text-[11px] text-ink-mute">提示词</span>
          <button type="button" onClick={() => void copy()}
            className="inline-flex items-center gap-0.5 text-[11px] text-primary underline">
            <Copy size={11} />复制全文
          </button>
        </div>
        {/* 提示词**不截断**：它是这张图的说明书，省略号会让"复现"变成猜 */}
        <p className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-line-soft bg-panel p-1.5 text-[11.5px] leading-relaxed text-ink-soft">
          {recipe.prompt || '（空提示词）'}
        </p>
      </div>
      {recipe.negativePrompt && (
        <p className="text-[11.5px] text-ink-soft"><span className="text-ink-mute">负向：</span>{recipe.negativePrompt}</p>
      )}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-mute">
        <span>seed：{recipe.seed || '随机'}</span>
        {recipe.costUnits ? <span>消耗：{recipe.costUnits}</span> : null}
        {params.map(([key, value]) => (
          <span key={key}>{key}：{String(value)}</span>
        ))}
      </p>
    </div>
  );
}

function NodeList({title, nodes}: {title: string; nodes: LineageNode[]}) {
  return (
    <div>
      <p className="mb-1 flex items-center gap-1 text-[11px] text-ink-mute"><GitBranch size={11} />{title}</p>
      <ul className="space-y-1">
        {nodes.map((node) => (
          <li key={node.recipe.id} className="text-[11.5px] text-ink-soft">
            <span className="text-ink">{node.name || '（产物已删除）'}</span>
            <span className="ml-1.5 text-ink-mute">{node.recipe.model}</span>
            {node.recipe.prompt && (
              <p className="truncate text-[11px] text-ink-mute" title={node.recipe.prompt}>
                {node.recipe.prompt}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 谁在用它。两类消费者分开列：`asset` 是别的配方引用了它（生成血缘），
 * `subject` 是某个主体把它当成员（业务装配）。合并成一种会让
 * "这张图能不能删"的答案少一半——两者各自都会阻止删除。
 */
function UsageList({usages}: {usages: Array<{kind: string; id: string; name?: string}>}) {
  const assets = usages.filter((item) => item.kind === 'asset');
  const subjects = usages.filter((item) => item.kind === 'subject');
  if (!usages.length) {
    return <p className="text-[11.5px] text-ink-mute">还没有别的地方在用它。</p>;
  }
  return (
    <div>
      <p className="mb-1 text-[11px] text-ink-mute">谁在用它</p>
      <ul className="space-y-1 text-[11.5px] text-ink-soft">
        {assets.map((item) => (
          <li key={`a-${item.id}`} className="truncate">
            <Chip tone="primary">生成</Chip> <span title={item.name}>{item.name || item.id}</span>
          </li>
        ))}
        {subjects.map((item) => (
          <li key={`s-${item.id}`} className="truncate">
            <Chip tone="ok">主体</Chip> <span title={item.name}>{item.name || item.id}</span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-[11px] text-ink-mute">被引用中的素材不能删除：删了会让成品与主体的血缘中途断掉。</p>
    </div>
  );
}
