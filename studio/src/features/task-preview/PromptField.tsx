import { PromptRefs } from '@/entities/library/ui/PromptRefs';

export interface PromptFieldProps {
  /** 提示词正文（可能几百字） */
  value?: string | null;
  /** 第 N 份文案的资产 id（与 `textLibraryIds` 同序） */
  textIds?: string[];
  /** 第 N 位视觉参考的资产 id（与 `referenceLibraryIds` 同序） */
  referenceIds?: string[];
}

/**
 * 任务详情里的「画面描述」。
 *
 * ## 为什么限高
 *
 * 模板写出来的提示词动辄几百字，平铺在弹窗里会把下面的按钮（下载成片 / 重新生成 /
 * 收藏）顶出视口 —— 用户得先滚过一大段文字才找得到按钮。
 *
 * 所以正文放进一个**限高、自己滚**的盒子：弹窗该多高还是多高，按钮永远在那儿。
 * 盒子的样子沿用资产库详情里「最终提示词」那套（同样的边框与底色），
 * 免得两个地方的提示词长得不一样。
 *
 * 限高同时跟视口走（`min(220px, 30vh)`）：屏幕越矮，盒子越矮，
 * 弹窗本身不会被撑到需要滚动才看得见按钮。
 */
export function PromptField({ value, textIds, referenceIds }: PromptFieldProps) {
  return (
    <div className="scroll-thin max-h-[min(220px,30vh)] overflow-y-auto overscroll-contain rounded-lg border border-line-soft bg-panel-soft px-2.5 py-2">
      {value ? (
        <PromptRefs
          value={value}
          textIds={textIds}
          referenceIds={referenceIds}
          className="block"
        />
      ) : (
        <span className="text-ink-mute">—</span>
      )}
    </div>
  );
}
