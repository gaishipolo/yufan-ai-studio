import { useState } from 'react';
import type { TemplateCaseOut, TemplateCaseUpdateIn } from '@/shared/api/schema';
import { Button, FormField, Modal, Notice, TextInput } from '@/shared/ui';

export interface CaseEditorDialogProps {
  item: TemplateCaseOut | null;
  onClose: () => void;
  onSave: (payload: TemplateCaseUpdateIn) => void;
}

/**
 * 编辑案例：**只改案例自己的字段**（名称 / 描述 / 分类），不碰模板。
 *
 * 这一点在标题下写出来：用户很容易以为「改案例」= 「改它关联的模板」，那需要
 * 回模板封装里去改 —— 案例是模板的一份**快照**，改了模板不会回写案例。
 */
export function CaseEditorDialog(props: CaseEditorDialogProps) {
  const { item } = props;
  /* 用 key 让「换一份案例」与「重新打开」都等于「换一份表单」。
     不用 effect 去同步 props —— 父组件每渲染一次就同步一次，用户在输入框里
     打的字会被当场冲掉，根本没法填。 */
  return <CaseEditorForm key={`${item?.id ?? 'none'}-${item ? 'open' : 'closed'}`} {...props} />;
}

function CaseEditorForm({ item, onClose, onSave }: CaseEditorDialogProps) {
  const [name, setName] = useState(item?.name ?? '');
  const [description, setDescription] = useState(item?.description ?? '');
  const [category, setCategory] = useState(item?.category ?? '');

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onSave({name: trimmed, description: description.trim(), category: category.trim()});
  };

  return (
    <Modal open={item !== null} onClose={onClose} title="编辑案例" width={520}>
      <div className="flex flex-col gap-3 px-[18px] py-4">
        <Notice tone="info">
          案例是关联模板的一份快照。这里改的只是案例自己的名称、描述与分类，
          不会改动模板；要改模板内容请回「模板封装」里改。
        </Notice>

        <FormField label="案例名称" htmlFor="case-name" required hint="同一作者下不能重名">
          <TextInput
            id="case-name"
            value={name}
            maxLength={80}
            onChange={event => setName(event.target.value)}
          />
        </FormField>

        <FormField label="描述" htmlFor="case-desc" hint="这段话会展示给复用者">
          <textarea
            id="case-desc"
            value={description}
            rows={3}
            maxLength={500}
            placeholder="这份案例适合什么场景？"
            onChange={event => setDescription(event.target.value)}
            className="w-full resize-y rounded-lg border border-line bg-white px-2.5 py-1.5 text-body text-ink outline-none transition-colors placeholder:text-[#a9b8bf] focus:border-primary-line focus:ring-2 focus:ring-primary/10"
          />
        </FormField>

        <FormField label="分类" htmlFor="case-category" hint="留空表示不分类">
          <TextInput
            id="case-category"
            value={category}
            maxLength={64}
            placeholder="例如：人物替换 / 商品展示 / 剧情演绎"
            onChange={event => setCategory(event.target.value)}
          />
        </FormField>

        <div className="flex items-center gap-1.5 border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" className="ml-auto" onClick={onClose}>
            取消
          </Button>
          <Button size="sm" variant="primary" disabled={!name.trim()} onClick={submit}>
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}
