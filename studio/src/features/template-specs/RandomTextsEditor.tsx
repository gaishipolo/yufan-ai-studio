import { Plus, X } from 'lucide-react';
import { IconButton } from '@/shared/ui';

/**
 * 「随机文案」编辑器：多条独立输入框，可增删，至少留一条。
 *
 * ## 为什么至少一条
 *
 * 随机档下可选槽位不传素材，全靠这批文案产出提示词。一条都没有的话，
 * 随机档什么也注入不了 —— 界面上会缺一整段，而用户看不出为什么。
 * 所以删到只剩一条时禁用删除（而不是删光后再提示），控件本身就不让人
 * 进入无效状态。
 *
 * ## 为什么是独立组件
 *
 * SlotCard 已经很长；把「多条增删」这块独立出来，既让 Fast Refresh
 * 不被打断，也能单独测增删行为。文案内容只经 onChange 上报，父级是唯一真相。
 */
export function RandomTextsEditor({
  texts,
  onChange,
}: {
  texts: string[];
  onChange: (texts: string[]) => void;
}) {
  const items = texts.length ? texts : [''];
  const setAt = (index: number, value: string) =>
    onChange(items.map((text, i) => (i === index ? value : text)));
  const removeAt = (index: number) => onChange(items.filter((_, i) => i !== index));

  return (
    <div className="flex flex-col gap-1.5">
      {items.map((text, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <input
            aria-label={`随机文案 ${index + 1}`}
            value={text}
            onChange={event => setAt(index, event.target.value)}
            placeholder="这段场景怎么说"
            className="min-w-0 flex-1 rounded-lg border border-line bg-white px-2.5 py-2 text-xs leading-[1.7] text-ink outline-none transition-colors placeholder:text-[#a9b8bf] hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10"
          />
          <IconButton
            icon={<X size={13} />}
            label={items.length > 1 ? `删除随机文案 ${index + 1}` : '至少保留一段'}
            disabled={items.length <= 1}
            onClick={() => removeAt(index)}
          />
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...items, ''])}
        className="inline-flex items-center gap-1 self-start rounded-md px-1.5 py-1 text-micro text-primary transition-colors hover:bg-primary-soft"
      >
        <Plus size={12} /> 加一段
      </button>
    </div>
  );
}
