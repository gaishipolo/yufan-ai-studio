import { useState } from 'react';
import { Button, FormField, Modal, Notice, TextInput } from '@/shared/ui';

export interface PublishCaseSpec {
  /** 源模板的 key（发布案例时按它找模板）。 */
  key: string;
  name: string;
  /** 模板当前版本（还没发布过是 0）。 */
  version: number;
  slotCount: number;
  model: string;
}

export interface PublishCasePayload {
  name: string;
  description: string;
  category: string;
}

export interface PublishCaseDialogProps {
  open: boolean;
  /** 正在发布的模板；`null` 时弹窗关闭。 */
  spec: PublishCaseSpec | null;
  /** 案例库里已有的分类，给几个现成的点选。 */
  categories: string[];
  onClose: () => void;
  /** 失败时抛错，弹窗内显示原因。 */
  onSubmit: (payload: PublishCasePayload) => Promise<void>;
}

/**
 * 「发布为案例」弹窗。
 *
 * 发布出去的案例 = **这份模板当前草稿的一份快照**，别人拿到后可以复制成自己的
 * 模板。所以这里要把两件事摊开：
 *
 * 1. 案例自己的三件套：名称、描述、分类；
 * 2. 关联的模板信息（名称 / key / 版本 / 槽位数）—— 让作者确认自己发出去的是
 *    哪一份配置。
 *
 * 快照的是**服务端已保存的草稿**：提交前会先把当前模板存一遍，免得把还没存
 * 的改动漏在案例外面。
 */
export function PublishCaseDialog(props: PublishCaseDialogProps) {
  const { open, spec } = props;
  /* 用 key 让「换一份模板」与「重新打开」都等于「换一份表单」。
     不用 effect 去同步 props —— 父组件每渲染一次就同步一次，用户在输入框里
     打的字会被当场冲掉，根本没法填。 */
  return <PublishCaseForm key={`${spec?.key ?? 'none'}-${open}`} {...props} />;
}

function PublishCaseForm({
  open,
  spec,
  categories,
  onClose,
  onSubmit,
}: PublishCaseDialogProps) {
  const [name, setName] = useState(spec ? `${spec.name || spec.key} 案例` : '');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setError('');
    try {
      await onSubmit({
        name: trimmed,
        description: description.trim(),
        category: category.trim(),
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const quickCategories = categories.filter(item => item && item !== category).slice(0, 6);

  return (
    <Modal open={open} onClose={onClose} title="发布为案例" width={560}>
      <div className="flex flex-col gap-3 px-[18px] py-4">
        <Notice tone="info">
          案例是这份模板当前内容的一份快照，发布后其他人可以在案例库里复用它。
          模板本身不受影响，之后改模板不会回写案例。
        </Notice>

        {/* 关联模板信息：让作者确认发出去的是哪一份配置。 */}
        <dl className="overflow-hidden rounded-lg border border-line-soft">
          {[
            {label: '模板名称', value: spec?.name || '—'},
            {label: '模板 key', value: spec?.key || '—'},
            {label: '模板版本', value: `v${spec?.version ?? 0}`},
            {label: '槽位数量', value: `${spec?.slotCount ?? 0} 个`},
          ].map((row, index) => (
            <div
              key={row.label}
              className={
                index === 0
                  ? 'flex items-start gap-3 px-2.5 py-1.5'
                  : 'flex items-start gap-3 border-t border-line-soft px-2.5 py-1.5'
              }
            >
              <dt className="w-16 shrink-0 pt-px text-meta text-ink-mute">{row.label}</dt>
              <dd className="min-w-0 flex-1 break-words text-body text-ink">{row.value}</dd>
            </div>
          ))}
        </dl>

        <FormField label="案例名称" htmlFor="publish-case-name" required hint="同一作者下不能重名">
          <TextInput
            id="publish-case-name"
            value={name}
            maxLength={160}
            onChange={event => setName(event.target.value)}
          />
        </FormField>

        <FormField label="描述" htmlFor="publish-case-desc" hint="这段话会展示给复用者">
          <textarea
            id="publish-case-desc"
            value={description}
            rows={3}
            maxLength={2000}
            placeholder="这份案例适合什么场景？"
            onChange={event => setDescription(event.target.value)}
            className="w-full resize-y rounded-lg border border-line bg-white px-2.5 py-1.5 text-body text-ink outline-none transition-colors placeholder:text-[#a9b8bf] focus:border-primary-line focus:ring-2 focus:ring-primary/10"
          />
        </FormField>

        <FormField label="分类" htmlFor="publish-case-category" hint="留空表示不分类">
          <TextInput
            id="publish-case-category"
            value={category}
            maxLength={64}
            placeholder="例如：人物替换 / 商品展示 / 剧情演绎"
            onChange={event => setCategory(event.target.value)}
          />
        </FormField>

        {quickCategories.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-micro text-ink-mute">沿用已有分类</span>
            {quickCategories.map(item => (
              <button
                key={item}
                type="button"
                onClick={() => setCategory(item)}
                className="rounded-full border border-line-soft bg-panel px-2 py-[2px] text-micro text-ink-soft transition-colors hover:border-primary-line hover:bg-primary-soft hover:text-primary-deep"
              >
                {item}
              </button>
            ))}
          </div>
        )}

        {error && <Notice tone="error">{error}</Notice>}

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button size="sm" variant="primary" loading={busy} disabled={!name.trim()} onClick={submit}>
            发布为案例
          </Button>
        </div>
      </div>
    </Modal>
  );
}
