import {useEffect, useMemo, useRef, useState} from 'react';
import {useMutation, useQuery} from '@tanstack/react-query';
import {Play, RotateCcw, Save} from 'lucide-react';
import {
  llmConfigApi,
  type RandomRewriteSettingsIn,
  type RandomRewriteSettingsOut,
  type RewriteDebugIn,
  type RewriteDebugOut,
} from '@/entities/llm/api/llm-config.api';
import {ApiError} from '@/shared/api/types';
import {Button, FormField, Notice, Select, TextInput} from '@/shared/ui';
import {cn} from '@/shared/lib/cn';

const CODE_BOX =
  'scroll-thin max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-line-soft bg-panel-soft p-3 font-mono text-micro leading-relaxed text-ink';

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return error instanceof Error ? error.message : String(error);
}

interface ModelOption {
  id: string;
  label: string;
}

/** LLM 调试台：选模板 / 模型，改 system prompt / 参数，看原文与最终提示词。 */
export function RewriteDebugPanel({models}: {models: ModelOption[]}) {
  const options = useQuery({
    queryKey: ['llm-debug-templates'],
    queryFn: llmConfigApi.debugTemplates,
  });
  const templates = useMemo(() => options.data?.templates ?? [], [options.data]);

  const [templateKey, setTemplateKey] = useState('');
  const [model, setModel] = useState('llm/default');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [temperature, setTemperature] = useState('1');
  const [maxTokens, setMaxTokens] = useState('8000');
  const [extraParams, setExtraParams] = useState('');
  const [localError, setLocalError] = useState('');
  const [saved, setSaved] = useState('');
  const [savedConfig, setSavedConfig] = useState<RandomRewriteSettingsOut | null>(null);
  const loadedRef = useRef(false);

  const modelOptions = useMemo(() => {
    const seen = new Set<string>();
    const result: ModelOption[] = [{id: 'llm/default', label: '平台默认（降级链）'}];
    seen.add('llm/default');
    for (const item of models) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      result.push(item);
    }
    return result;
  }, [models]);

  const applySaved = () => {
    const current = savedConfig ?? options.data?.currentSettings;
    if (!current) return;
    setModel(current.model);
    setSystemPrompt(current.systemPrompt);
    setTemperature(String(current.temperature));
    setMaxTokens(String(current.maxTokens ?? 8000));
    setExtraParams(
      current.extraParams && Object.keys(current.extraParams).length
        ? JSON.stringify(current.extraParams, null, 2)
        : '',
    );
    setLocalError('');
    setSaved('已恢复当前保存的生产配置');
  };

  useEffect(() => {
    if (!options.data) return;
    const current = options.data.currentSettings;
    setSavedConfig(current);
    // 首次拿到保存配置时**无条件覆盖**初始的代码默认值；之后 refetch 不再覆盖用户编辑。
    if (loadedRef.current) return;
    loadedRef.current = true;
    setModel(current.model);
    setSystemPrompt(current.systemPrompt);
    setTemperature(String(current.temperature));
    setMaxTokens(String(current.maxTokens ?? 8000));
    setExtraParams(
      current.extraParams && Object.keys(current.extraParams).length
        ? JSON.stringify(current.extraParams, null, 2)
        : '',
    );
    setTemplateKey(templates[0]?.key || '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.data]);

  const selected = useMemo(
    () => templates.find(item => item.key === templateKey),
    [templates, templateKey],
  );

  const run = useMutation({
    mutationFn: (payload: RewriteDebugIn) => llmConfigApi.debugRewrite(payload),
  });
  const save = useMutation({
    mutationFn: (payload: RandomRewriteSettingsIn) =>
      llmConfigApi.saveRandomSettings(payload),
    onSuccess: data => {
      setSavedConfig(data);
      setModel(data.model);
      setSystemPrompt(data.systemPrompt);
      setTemperature(String(data.temperature));
      setMaxTokens(String(data.maxTokens ?? 8000));
      setExtraParams(
        data.extraParams && Object.keys(data.extraParams).length
          ? JSON.stringify(data.extraParams, null, 2)
          : '',
      );
      setSaved('已覆盖生产配置；下一次随机档编译立即生效');
      void options.refetch();
    },
  });

  const buildSettings = (): RandomRewriteSettingsIn | null => {
    if (!systemPrompt.trim()) {
      setLocalError('System prompt 不能为空');
      return null;
    }
    const temp = Number(temperature);
    if (!Number.isFinite(temp) || temp < 0 || temp > 2) {
      setLocalError('temperature 必须在 0–2 之间');
      return null;
    }
    let extra: Record<string, unknown> = {};
    if (extraParams.trim()) {
      try {
        const parsed: unknown = JSON.parse(extraParams);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          throw new Error('必须是 JSON 对象');
        }
        extra = parsed as Record<string, unknown>;
      } catch (error) {
        setLocalError(`额外参数不是合法 JSON：${errorText(error)}`);
        return null;
      }
    }
    const tokens = Number(maxTokens);
    if (!Number.isInteger(tokens) || tokens < 1) {
      setLocalError('max tokens 必须是正整数');
      return null;
    }
    return {
      model,
      systemPrompt,
      temperature: temp,
      maxTokens: tokens,
      extraParams: extra,
    };
  };

  const submit = () => {
    setLocalError('');
    setSaved('');
    run.reset();
    save.reset();
    if (!selected) {
      setLocalError('先选一个模板');
      return;
    }
    const settings = buildSettings();
    if (!settings) return;
    run.mutate({...settings, templateKey: selected.key, version: selected.version});
  };

  const saveSettings = () => {
    setLocalError('');
    setSaved('');
    save.reset();
    const settings = buildSettings();
    if (settings) save.mutate(settings);
  };

  const result = run.data;
  const requestError = run.error ? errorText(run.error) : '';
  const saveError = save.error ? errorText(save.error) : '';
  const shownError = localError || requestError || saveError;

  return (
    <section
      className="rounded-card border border-line-soft bg-panel p-4"
      aria-label="随机改写调试"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-body font-medium text-ink">随机改写调试</h2>
          {options.data && (
            <p className="mt-1 font-mono text-micro text-ink-mute">
              {options.data.customized ? '已自定义' : '默认'} ·{' '}
              {options.data.currentSettings.model} · temperature{' '}
              {options.data.currentSettings.temperature} · max tokens{' '}
              {options.data.currentSettings.maxTokens ?? 8000}
            </p>
          )}
        </div>
        <Button size="sm" variant="ghost" onClick={applySaved}>
          <RotateCcw size={13} /> 恢复已保存
        </Button>
      </div>

      {options.isError && (
        <Notice tone="error" title="模板列表加载失败">
          {errorText(options.error)}
        </Notice>
      )}
      {!options.isLoading && templates.length === 0 && (
        <Notice tone="info" title="没有可调试的模板">
          先发布一个模板，再回来调试随机改写。
        </Notice>
      )}

      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <FormField label="模型" htmlFor="llm-debug-model">
          <Select
            id="llm-debug-model"
            block
            value={model}
            onChange={event => setModel(event.target.value)}
            options={modelOptions.map(item => ({value: item.id, label: item.label}))}
          />
        </FormField>
        <FormField label="模板" htmlFor="llm-debug-template">
          <Select
            id="llm-debug-template"
            block
            value={templateKey}
            onChange={event => setTemplateKey(event.target.value)}
            options={templates.map(item => ({
              value: item.key,
              label: `${item.name}（${item.key} · v${item.version}）`,
            }))}
          />
        </FormField>
        <FormField label="temperature" htmlFor="llm-debug-temperature">
          <TextInput
            id="llm-debug-temperature"
            type="number"
            min={0}
            max={2}
            step={0.1}
            value={temperature}
            onChange={event => setTemperature(event.target.value)}
          />
        </FormField>
        <FormField label="max tokens" htmlFor="llm-debug-max-tokens">
          <TextInput
            id="llm-debug-max-tokens"
            type="number"
            min={1}
            step={1}
            placeholder="8000"
            value={maxTokens}
            onChange={event => setMaxTokens(event.target.value)}
          />
        </FormField>
      </div>

      <FormField label="System prompt" htmlFor="llm-debug-system">
        <textarea
          id="llm-debug-system"
          className={cn('min-h-[220px] w-full rounded-lg border border-line bg-panel p-3 font-mono text-micro leading-relaxed text-ink', 'outline-none focus:border-primary-line focus:ring-2 focus:ring-primary/10')}
          value={systemPrompt}
          onChange={event => setSystemPrompt(event.target.value)}
        />
      </FormField>

      <details className="mt-3 rounded-lg border border-line-soft bg-panel-soft px-3 py-2">
        <summary className="cursor-pointer select-none text-meta text-ink-soft">
          额外参数（JSON）
        </summary>
        <textarea
          id="llm-debug-extra"
          aria-label="额外参数（JSON）"
          className={cn('mt-2 h-24 w-full rounded-lg border border-line bg-panel p-2.5 font-mono text-micro text-ink', 'outline-none focus:border-primary-line focus:ring-2 focus:ring-primary/10')}
          placeholder='{"top_p": 0.9}'
          value={extraParams}
          onChange={event => setExtraParams(event.target.value)}
        />
      </details>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={submit} disabled={run.isPending || save.isPending || !selected}>
          <Play size={14} /> {run.isPending ? '改写中…' : '运行改写（不生成）'}
        </Button>
        <Button variant="ghost" onClick={saveSettings} disabled={save.isPending || run.isPending}>
          <Save size={14} /> {save.isPending ? '保存中…' : '覆盖生产配置'}
        </Button>
      </div>

      {saved && (
        <Notice tone="info" title="配置已保存">
          {saved}
        </Notice>
      )}

      {shownError && (
        <Notice tone="error" title="操作失败">
          {shownError}
        </Notice>
      )}
      {result && <DebugResult result={result} />}
    </section>
  );
}

function DebugResult({result}: {result: RewriteDebugOut}) {
  const usage = result.usage;
  const overLimit =
    (result.promptLength ?? 0) > (result.promptLimit ?? Number.MAX_SAFE_INTEGER);
  return (
    <div className="mt-4 flex flex-col gap-3 border-t border-line-soft pt-3">
      {!result.ok && (
        <Notice tone="error" title="模型稿未通过校验">
          {result.error || '未知错误'}
        </Notice>
      )}
      {!result.ok && result.requestedModel !== 'llm/default' && (
        <p className="text-meta text-ink-soft">
          当前是显式模型，不参与降级链；想测降级请把模型切到「平台默认（降级链）」。
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-ink-soft">
        <span>请求模型：{result.requestedModel || '—'}</span>
        <span>平台模型：{result.llmPlatformModel || result.llmModel || '—'}</span>
        <span>上游模型：{result.llmModel || '—'}</span>
        <span>finish：{result.finishReason || '—'}</span>
        <span>耗时：{result.latencyMs ?? 0} ms</span>
        <span>
          tokens：{usage?.promptTokens ?? 0} / {usage?.completionTokens ?? 0}
        </span>
        {result.promptLimit != null && (
          <span className={cn(overLimit && 'text-danger-deep')}>
            最终提示词：{result.promptLength ?? 0} / {result.promptLimit} 字
            {overLimit ? '（超过执行上限）' : ''}
          </span>
        )}
      </div>

      {result.syntheticSlots && result.syntheticSlots.length > 0 && (
        <p className="text-micro text-ink-mute">
          必填槽位用调试占位素材展开：{result.syntheticSlots.join('、')}。
          占位只影响编号与段落，不会创建任务。
        </p>
      )}

      <div>
        <h3 className="mb-1 text-meta font-medium text-ink">模型原始输出</h3>
        <pre className={CODE_BOX}>{result.rawOutput || '（无）'}</pre>
      </div>

      <div>
        <h3 className="mb-1 text-meta font-medium text-ink">
          最终提示词（传给生成模型之前）
        </h3>
        <pre className={CODE_BOX}>{result.finalPrompt || '（模型稿未通过校验，没有最终提示词）'}</pre>
      </div>

      <details className="text-meta text-ink-soft">
        <summary className="cursor-pointer select-none">改写后的槽位文案</summary>
        <pre className={cn(CODE_BOX, 'mt-1')}>
          {JSON.stringify(result.rewrittenTexts ?? {}, null, 2)}
        </pre>
      </details>

      <details className="text-meta text-ink-soft">
        <summary className="cursor-pointer select-none">实际发给 LLM 的 system / user / 参数</summary>
        <div className="mt-1">
          <p className="mb-1 text-micro text-ink-mute">system</p>
          <pre className={CODE_BOX}>{result.systemPrompt || '（无）'}</pre>
          <p className="mb-1 mt-2 text-micro text-ink-mute">request params</p>
          <pre className={CODE_BOX}>{JSON.stringify(result.requestParams ?? {}, null, 2)}</pre>
          <p className="mb-1 mt-2 text-micro text-ink-mute">user payload</p>
          <pre className={CODE_BOX}>{result.userPayload || '（无）'}</pre>
        </div>
      </details>

      <details className="text-meta text-ink-soft">
        <summary className="cursor-pointer select-none">编译产物（素材 id 与参数）</summary>
        <pre className={cn(CODE_BOX, 'mt-1')}>
          {JSON.stringify(
            {
              referenceLibraryIds: result.referenceLibraryIds ?? [],
              textLibraryIds: result.textLibraryIds ?? [],
              params: result.compiledParams ?? {},
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}