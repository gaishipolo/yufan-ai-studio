import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { driverApi } from '@/entities/model/api/driver.api';
import { Button, FormField, Notice, TextInput } from '@/shared/ui';
import type { ConfigItemOut } from '@/shared/api/schema';
import { ApiError } from '@/shared/api/types';

/**
 * 运行时配置：改 .env 里的白名单与供应商接入项，下一次请求即生效。
 *
 * 输入框自己会说清楚状态，不靠旁边的说明文字：
 * - 非密钥项 value 就是当前生效值；
 * - 密钥项 value 永远是空的，掩码放在 placeholder —— 看得到是哪把 key，
 *   一输入新值占位符自动让位，不输入就是不改；
 * - 只读项置灰 + 锁图标，原因放在 title 里（悬停可见，不占版面）。
 */
function ConfigRow({ item, value, onChange }: {
  item: ConfigItemOut;
  value: string;
  onChange: (key: string, value: string) => void;
}) {
  const id = `config-${item.key.toLowerCase().replace(/_/g, '-')}`;
  const editable = item.editable !== false;
  const fromEnv = item.source === 'environment';

  return (
    <FormField label={item.label} htmlFor={id} required={item.required}>
      <TextInput
        id={id}
        value={value}
        onChange={(event) => onChange(item.key, event.target.value)}
        disabled={!editable}
        autoComplete="off"
        placeholder={item.secret ? (item.masked || '未配置') : undefined}
        title={
          fromEnv
            ? '由进程环境变量提供，页面改动不会影响生效值'
            : item.hint || undefined
        }
        aria-label={item.label}
      />
    </FormField>
  );
}

export default function RuntimeConfigSection() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['ops', 'runtime-config'],
    queryFn: driverApi.config,
  });
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<string[] | null>(null);

  useEffect(() => {
    // 非密钥项回填当前值；密钥项留空，掩码在 placeholder 里。
    const fresh: Record<string, string> = {};
    for (const item of data?.items ?? []) {
      fresh[item.key] = item.secret ? '' : (item.value ?? '');
    }
    setDrafts(fresh);
    setSaved(null);
  }, [data]);

  const mutation = useMutation({
    mutationFn: driverApi.updateConfig,
    onSuccess: (result) => {
      setSaved(result.updated ?? []);
      void queryClient.invalidateQueries({ queryKey: ['ops', 'runtime-config'] });
    },
  });

  const items = data?.items ?? [];
  const groups = data?.groups ?? ['白名单', '供应商接入'];
  const editableItems = items.filter((item) => item.editable !== false);
  // 密钥的「原值」视为空：掩码在 placeholder 里，不是输入框的值，输入才算改
  const baseline = (item: ConfigItemOut) => item.secret ? '' : (item.value ?? '');
  const hasDraft = editableItems.some(
    (item) => (drafts[item.key] ?? '') !== baseline(item),
  );

  function change(key: string, value: string) {
    setDrafts((prev) => ({ ...prev, [key]: value }));
    setSaved(null);
  }

  function save() {
    setSaved(null);
    const values: Record<string, string> = {};
    for (const item of editableItems) {
      const draft = drafts[item.key] ?? '';
      if (draft !== baseline(item)) values[item.key] = draft;
    }
    mutation.mutate({ values });
  }

  return (
    <section className="rounded-card border border-line-soft bg-panel p-4" aria-label="运行时配置">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h2 className="text-body font-medium text-ink">运行时配置</h2>
          <p className="mt-0.5 text-micro text-ink-mute">
            {data?.envFile || '.env'} · 保存即生效，不用重启
          </p>
        </div>
        <Button onClick={save} disabled={mutation.isPending || !hasDraft} aria-label="保存配置">
          {mutation.isPending ? '保存中…' : '保存配置'}
        </Button>
      </div>

      {isLoading && <p className="mt-4 text-meta text-ink-mute">加载配置中…</p>}
      {error && (
        <Notice tone="error" title="配置读取失败" className="mt-4">
          {error instanceof ApiError ? error.message : '请查看后端日志'}
        </Notice>
      )}
      {mutation.isError && (
        <Notice tone="error" title="保存失败" className="mt-4">
          {mutation.error instanceof ApiError ? mutation.error.message : '请查看后端日志'}
        </Notice>
      )}
      {saved && (
        <Notice tone="success" title="已生效" className="mt-4">
          {saved.length ? `${saved.length} 项已更新` : '没有需要保存的改动'}
        </Notice>
      )}

      {!isLoading && !error && (
        <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-5 lg:grid-cols-2">
          {groups.map((group) => (
            <div key={group} className="flex flex-col gap-3.5">
              <h3 className="text-meta font-medium text-ink-soft">{group}</h3>
              {items.filter((item) => item.group === group).map((item) => (
                <ConfigRow
                  key={item.key}
                  item={item}
                  value={drafts[item.key] ?? ''}
                  onChange={change}
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
