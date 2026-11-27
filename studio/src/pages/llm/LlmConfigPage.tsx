import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, Plus, Trash2, Zap, ChevronUp, ChevronDown } from 'lucide-react';
import { llmConfigApi, type LlmModelOut, type LlmProviderOut } from '@/entities/llm/api/llm-config.api';
import {
  Button,
  ConfirmDialog,
  FormField,
  IconButton,
  Modal,
  Notice,
  PageHeader,
  TextInput,
} from '@/shared/ui';
import { ApiError } from '@/shared/api/types';
import { useCan } from '@/shared/access/use-access';

/**
 * LLM 供应商配置：增删供应商、维护「平台 id → 上游模型名」映射、
 * 从网关拉真实模型 id、发一条最小请求验文本往返。
 *
 * 密钥输入框永远空着，掩码在 placeholder —— 看得到是哪把 key，
 * 输入新值才覆盖，留空表示不改（与运维页配置同一口径）。
 */
interface Draft {
  name: string;
  baseUrl: string;
  apiKey: string;
  /** 编辑已有供应商时带入的掩码，仅作输入框占位符，不回显明文。 */
  apiKeyMasked: string;
  models: LlmModelOut[];
}

const emptyDraft = (): Draft => ({
  name: '',
  baseUrl: '',
  apiKey: '',
  apiKeyMasked: '',
  models: [],
});

interface ChainModel {
  id: string;
  provider: string;
  upstream: string;
}

function ModelChainPanel({
  models,
  chain,
  canWrite,
  saving,
  onMove,
  onRemove,
  onAdd,
  onSave,
}: {
  models: ChainModel[];
  chain: string[];
  canWrite: boolean;
  saving: boolean;
  onMove: (index: number, delta: number) => void;
  onRemove: (id: string) => void;
  onAdd: (id: string) => void;
  onSave: () => void;
}) {
  const byId = new Map(models.map((model) => [model.id, model]));
  const available = models.filter((model) => !chain.includes(model.id));

  return (
    <section
      className="rounded-card border border-line-soft bg-panel p-4"
      aria-label="默认模型与降级顺序"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-body font-medium text-ink">默认模型与降级顺序</h2>
          <p className="mt-1 text-meta text-ink-soft">
            第一项是默认模型；调用失败时按顺序尝试后面的模型。
          </p>
          <p className="mt-0.5 text-meta text-ink-mute">
            只有 429 / 401 / 403 / 连接失败 / 5xx 会降级；400/422 与超时不降级
            —— 超时可能已被上游受理，换模型会重复计费。
          </p>
        </div>
        {canWrite && (
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? '保存中…' : '保存降级顺序'}
          </Button>
        )}
      </div>

      {chain.length === 0 ? (
        <p className="mt-3 text-meta text-ink-mute">
          未配置时 <code>llm/default</code> 使用 default 供应商的 LLM_MODEL
          （或它的第一个模型映射），不做降级。
        </p>
      ) : (
        <ol className="mt-3 flex flex-col gap-1.5">
          {chain.map((id, index) => {
            const model = byId.get(id);
            return (
              <li
                key={id}
                className="flex items-center gap-2 rounded-md border border-line-soft bg-panel-soft px-2 py-1.5 text-meta"
              >
                <span className="w-14 shrink-0 text-ink-mute">
                  {index === 0 ? '默认' : `降级 ${index}`}
                </span>
                <code className="text-ink">{id}</code>
                {model ? (
                  <span className="text-ink-soft">{model.upstream}</span>
                ) : (
                  <span className="text-danger-deep">模型已不存在</span>
                )}
                {canWrite && (
                  <span className="ml-auto flex items-center gap-1">
                    <IconButton
                      icon={<ChevronUp size={14} />}
                      label={`上移 ${id}`}
                      disabled={index === 0}
                      onClick={() => onMove(index, -1)}
                    />
                    <IconButton
                      icon={<ChevronDown size={14} />}
                      label={`下移 ${id}`}
                      disabled={index === chain.length - 1}
                      onClick={() => onMove(index, 1)}
                    />
                    <IconButton
                      icon={<Trash2 size={14} />}
                      label={`从降级链移除 ${id}`}
                      onClick={() => onRemove(id)}
                    />
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {canWrite && available.length > 0 && (
        <div className="mt-3">
          <p className="text-meta text-ink-soft">加进链里：</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {available.map((model) => (
              <li key={model.id}>
                <button
                  type="button"
                  onClick={() => onAdd(model.id)}
                  title={`把 ${model.id} 加进降级顺序`}
                  className="rounded-md border border-line-soft bg-panel px-2 py-1 text-meta text-ink-soft transition-colors hover:border-primary-line hover:text-primary"
                >
                  + {model.id}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function ProviderEditor({
  open,
  initial,
  onClose,
  onSave,
  saving,
  candidates,
  fetching,
  onFetch,
  fetchError,
}: {
  open: boolean;
  initial: Draft;
  onClose: () => void;
  onSave: (draft: Draft) => void;
  saving: boolean;
  /** 从网关拉到的上游模型 id；点一下就加进下面的映射。 */
  candidates: string[];
  fetching: boolean;
  onFetch: () => void;
  fetchError: string | null;
}) {
  const [draft, setDraft] = useState<Draft>(initial);
  useEffect(() => {
    setDraft(initial);
  }, [initial]);
  const setModel = (index: number, patch: Partial<LlmModelOut>) =>
    setDraft((prev) => ({
      ...prev,
      models: prev.models.map((m, i) => (i === index ? { ...m, ...patch } : m)),
    }));
  const addCandidate = (upstream: string) => {
    // 平台 id 带供应商前缀：default 用 llm/*，具名供应商用 <name>/*。
    const prefix = initial.name && initial.name !== 'default' ? initial.name : 'llm';
    return setDraft((prev) =>
      prev.models.some((m) => m.upstream === upstream)
        ? prev
        : {
            ...prev,
            models: [
              ...prev.models,
              { id: `${prefix}/${upstream.toLowerCase()}`, upstream },
            ],
          },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title={initial.name ? '编辑供应商' : '新增供应商'} width={640}>
      <form
        className="flex flex-col gap-4 px-[18px] py-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(draft);
        }}
      >
        <FormField label="供应商名" htmlFor="llm-name" required>
          <TextInput
            id="llm-name"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            disabled={Boolean(initial.name)}
            placeholder="minimax"
            title={initial.name ? '供应商名保存后不能改' : '小写字母、数字、下划线，字母开头'}
            aria-label="供应商名"
            required
          />
        </FormField>
        <FormField label="网关地址" htmlFor="llm-base-url" required>
          <TextInput
            id="llm-base-url"
            value={draft.baseUrl}
            onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
            placeholder="https://api.minimaxi.com/v1"
            aria-label="网关地址"
            required
          />
        </FormField>
        <FormField label="密钥" htmlFor="llm-api-key">
          <TextInput
            id="llm-api-key"
            value={draft.apiKey}
            onChange={(e) => setDraft({ ...draft, apiKey: e.target.value })}
            placeholder={initial.apiKeyMasked || '留空表示不改'}
            autoComplete="off"
            aria-label="密钥"
          />
        </FormField>

        <fieldset className="flex flex-col gap-2">
          <legend className="text-meta text-ink-soft">模型映射</legend>
          <p className="text-meta text-ink-mute">
            平台 id 要带供应商前缀：default 用 <code>llm/名字</code>，具名供应商用{' '}
            <code>{initial.name && initial.name !== 'default' ? initial.name : '<供应商名>'}/名字</code>
            。运行时按前缀选网关。
          </p>
          {/* 新供应商还没保存，服务端没有网关可拉；拉取只对已有供应商开放。 */}
          {Boolean(initial.name) && (
            <div className="flex flex-col gap-1.5 rounded-md border border-line-soft bg-panel-soft p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-meta text-ink-soft">网关上的模型</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={onFetch}
                  disabled={fetching}
                >
                  <Download size={13} /> {fetching ? '拉取中…' : '从网关拉取'}
                </Button>
              </div>
              {fetchError && <p className="text-meta text-danger-deep">{fetchError}</p>}
              {candidates.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5">
                  {candidates.map((upstream) => {
                    const added = draft.models.some((m) => m.upstream === upstream);
                    return (
                      <li key={upstream}>
                        <button
                          type="button"
                          disabled={added}
                          onClick={() => addCandidate(upstream)}
                          title={added ? '已在下面的映射里' : `添加 ${upstream} 到映射`}
                          className="rounded-md border border-line-soft bg-panel px-2 py-1 text-meta text-ink-soft transition-colors hover:border-primary-line hover:text-primary disabled:cursor-default disabled:opacity-50"
                        >
                          {added ? '✓ ' : '+ '}
                          {upstream}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                !fetching && (
                  <p className="text-meta text-ink-mute">
                    点「从网关拉取」看这个端点实际有哪些模型，再点模型名加进映射。
                  </p>
                )
              )}
            </div>
          )}
          {draft.models.map((model, index) => (
            <div key={index} className="flex items-center gap-2">
              <TextInput
                value={model.id}
                onChange={(e) => setModel(index, { id: e.target.value })}
                placeholder={
                  initial.name && initial.name !== 'default'
                    ? `${initial.name}/flash`
                    : 'llm/flash'
                }
                aria-label={`平台模型 id ${index + 1}`}
                className="flex-1"
              />
              <span className="text-ink-mute">→</span>
              <TextInput
                value={model.upstream}
                onChange={(e) => setModel(index, { upstream: e.target.value })}
                placeholder="MiniMax-M3.1-Flash"
                aria-label={`上游模型名 ${index + 1}`}
                className="flex-1"
              />
              <IconButton
                icon={<Trash2 size={14} />}
                label={`删除模型映射 ${index + 1}`}
                onClick={() =>
                  setDraft((prev) => ({
                    ...prev,
                    models: prev.models.filter((_, i) => i !== index),
                  }))
                }
              />
            </div>
          ))}
          <Button
            type="button"
            variant="ghost"
            onClick={() =>
              setDraft((prev) => ({
                ...prev,
                models: [...prev.models, { id: '', upstream: '' }],
              }))
            }
          >
            <Plus size={14} /> 加一个模型映射
          </Button>
        </fieldset>

        <div className="flex justify-end gap-2 border-t border-line-soft pt-3">
          <Button type="button" variant="ghost" onClick={onClose}>取消</Button>
          <Button type="submit" disabled={saving}>{saving ? '保存中…' : '保存'}</Button>
        </div>
      </form>
    </Modal>
  );
}

function ProviderCard({
  provider,
  editable,
  onEdit,
  onDelete,
  onTest,
  busy,
}: {
  provider: LlmProviderOut;
  editable: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onTest: (modelId?: string) => void;
  busy: boolean;
}) {
  return (
    <section className="rounded-card border border-line-soft bg-panel p-4" aria-label={`供应商 ${provider.name}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-body font-medium text-ink">{provider.name}</h2>
          <p className="mt-0.5 truncate text-meta text-ink-soft" title={provider.baseUrl}>
            {provider.baseUrl}
          </p>
        </div>
        {editable && (
          <div className="flex shrink-0 items-center gap-1">
            <Button variant="ghost" size="sm" onClick={onEdit}>编辑</Button>
            <IconButton
              icon={<Trash2 size={14} />}
              label={`删除供应商 ${provider.name}`}
              onClick={onDelete}
            />
          </div>
        )}
      </div>

      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-meta">
        <div className="flex gap-1.5">
          <dt className="text-ink-mute">密钥</dt>
          <dd className="text-ink">
            {provider.apiKeyConfigured ? provider.apiKeyMasked : '未配置'}
          </dd>
        </div>
        <div className="flex gap-1.5">
          <dt className="text-ink-mute">来源</dt>
          <dd className="text-ink">{provider.source === 'environment' ? '环境变量' : '配置文件'}</dd>
        </div>
      </dl>

      {provider.models?.length ? (
        <ul className="mt-3 flex flex-col gap-1.5">
          {provider.models.map((model) => (
            <li key={model.id} className="flex items-center gap-2 text-meta">
              <code className="text-ink">{model.id}</code>
              <span className="text-ink-mute">→</span>
              <code className="text-ink-soft">{model.upstream}</code>
              {editable && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onTest(model.id)}
                  disabled={busy}
                  title="发一条最小请求验证文本往返"
                >
                  <Zap size={12} /> 测试
                </Button>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-meta text-ink-mute">还没有模型映射，点「编辑」添加或从网关拉取。</p>
      )}
    </section>
  );
}

export default function LlmConfigPage() {
  const queryClient = useQueryClient();
  const canWrite = useCan('llm_config:write');
  const [editor, setEditor] = useState<{ initial: Draft; name: string } | null>(null);
  const [deleting, setDeleting] = useState<LlmProviderOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<string[]>([]);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [chainDraft, setChainDraft] = useState<string[]>([]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['llm', 'providers'],
    queryFn: llmConfigApi.list,
  });
  const providers = data?.providers ?? [];
  const chainModels: ChainModel[] = providers.flatMap((provider) =>
    (provider.models ?? []).map((model) => ({
      id: model.id,
      provider: provider.name,
      upstream: model.upstream,
    })),
  );

  useEffect(() => {
    setChainDraft(data?.modelChain ?? []);
  }, [data?.modelChain]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['llm', 'providers'] });
  const reportError = (err: unknown) =>
    setNotice(err instanceof ApiError ? err.message : '操作失败，请查看后端日志');

  const saveMutation = useMutation({
    mutationFn: (draft: Draft) =>
      llmConfigApi.save({
        name: draft.name,
        baseUrl: draft.baseUrl,
        apiKey: draft.apiKey,
        models: draft.models,
      }),
    onSuccess: () => {
      setEditor(null);
      setNotice(null);
      invalidate();
    },
    onError: reportError,
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => llmConfigApi.remove(name),
    onSuccess: () => {
      setDeleting(null);
      invalidate();
    },
    onError: reportError,
  });

  const fetchMutation = useMutation({
    mutationFn: (name: string) => llmConfigApi.fetchModels(name),
    onSuccess: (result) => {
      // 结果直接进编辑弹窗的候选列表，不塞进一句会消失的提示。
      setCandidates(result.models?.map((m) => m.id).filter(Boolean) ?? []);
      setFetchError(null);
    },
    onError: (err: unknown) => {
      setCandidates([]);
      setFetchError(err instanceof ApiError ? err.message : '拉取失败，请查看后端日志');
    },
  });

  const testMutation = useMutation({
    mutationFn: ({ name, modelId }: { name: string; modelId?: string }) =>
      llmConfigApi.test(name, modelId),
    onSuccess: (result) => {
      const text = String(result.text ?? '');
      setNotice(`连通正常：${String(result.model ?? '')} · 回复「${text}」`);
    },
    onError: reportError,
  });

  const saveChainMutation = useMutation({
    mutationFn: (models: string[]) => llmConfigApi.saveModelChain(models),
    onSuccess: () => {
      setNotice('默认模型与降级顺序已保存');
      invalidate();
    },
    onError: reportError,
  });

  const moveChain = (index: number, delta: number) => {
    setChainDraft((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      const [item] = next.splice(index, 1);
      if (item === undefined) return prev;
      next.splice(target, 0, item);
      return next;
    });
  };

  const openEditor = (provider?: LlmProviderOut) => {
    // 每次打开弹窗重置候选：上一次拉取的结果不该出现在别的供应商里。
    setCandidates([]);
    setFetchError(null);
    setEditor({
      name: provider?.name ?? '',
      initial: provider
        ? {
            name: provider.name,
            baseUrl: provider.baseUrl,
            // 密钥不回显：编辑时留空 = 不改
            apiKey: '',
            apiKeyMasked: provider.apiKeyMasked ?? '',
            models: provider.models ?? [],
          }
        : emptyDraft(),
    });
  };

  return (
    <>
      <PageHeader
        title="LLM 配置"
        meta="OpenAI 兼容网关 · 平台 id 映射"
        actions={
          canWrite && (
            <Button onClick={() => openEditor()}>
              <Plus size={14} /> 新增供应商
            </Button>
          )
        }
      />

      {notice && (
        <Notice tone="info" title="操作结果" onClose={() => setNotice(null)}>
          {notice}
        </Notice>
      )}
      {error instanceof Error && (
        <Notice tone="error" title="加载失败">{error.message}</Notice>
      )}
      {isLoading && <p className="text-meta text-ink-mute">加载中…</p>}

      <div className="flex flex-col gap-3.5">
        <ModelChainPanel
          models={chainModels}
          chain={chainDraft}
          canWrite={canWrite}
          saving={saveChainMutation.isPending}
          onMove={moveChain}
          onRemove={(id) => setChainDraft((prev) => prev.filter((item) => item !== id))}
          onAdd={(id) => setChainDraft((prev) => (prev.includes(id) ? prev : [...prev, id]))}
          onSave={() => saveChainMutation.mutate(chainDraft)}
        />
        {providers.map((provider) => (
          <ProviderCard
            key={provider.name}
            provider={provider}
            editable={canWrite}
            onEdit={() => openEditor(provider)}
            onDelete={() => setDeleting(provider)}
            onTest={(modelId) => testMutation.mutate({ name: provider.name, modelId })}
            busy={testMutation.isPending}
          />
        ))}
        {!isLoading && providers.length === 0 && (
          <Notice tone="info" title="还没有供应商">
            新增一个 OpenAI 兼容网关，填上地址和密钥，再配置模型映射即可调用。
          </Notice>
        )}
      </div>

      {editor && (
        <ProviderEditor
          open
          initial={editor.initial}
          onClose={() => setEditor(null)}
          onSave={(draft) => saveMutation.mutate(draft)}
          saving={saveMutation.isPending}
          candidates={candidates}
          fetching={fetchMutation.isPending}
          onFetch={() => fetchMutation.mutate(editor.name)}
          fetchError={fetchError}
        />
      )}

      {deleting && (
        <ConfirmDialog
          open
          title="删除供应商"
          description="这会删掉该供应商的网关、密钥和模型映射配置。"
          details={[{ label: '供应商', value: deleting.name }]}
          confirmLabel="删除"
          confirmTone="danger"
          onCancel={() => setDeleting(null)}
          onConfirm={() => deleteMutation.mutate(deleting.name)}
        />
      )}
    </>
  );
}
