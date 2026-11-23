/**
 * 模板库「生成」二次确认的内容。
 *
 * 单独抽成纯函数，是因为它要回答一个具体问题：**按下确认之前，用户能不能
 * 看清这次到底会发生什么**。把这件事塞进组件里就会变成一堆散落的 JSX，
 * 改文案时容易漏掉一行，而漏掉的那行恰恰是「会扣积分」这种关键提醒。
 *
 * 事实清单的取舍：
 * - **列什么**：模板、模型、素材数、生效参数、计费方式 —— 都是这次提交
 *   真正会带出去的东西。
 * - **不列什么**：槽位逐个的标签、提示词正文、参考图明细 —— 那些在右栏
 *   已经看得见，重复一遍只是把弹窗撑长，反而让人扫不到关键那行。
 */

export interface ConfirmSlotLine {
  label: string;
  count: number;
  /**
   * 可选槽位**没填**时：用「未选时文案」兜底。
   *
   * 这条必须摆出来：使用者在确认框里看到的素材数量，与提示词里实际用的
   * 内容对不上时，他没法判断这次生成会是什么样子。不说的后果是
   * 「提交了才发现它用的是我以为没生效的那段话」。
   */
  fallback?: string;
  /**
   * 使用者**改写的**提示词（这一格不挑素材，直接给文字）。
   *
   * 它和 `fallback` 摆出来是同一件事：让使用者在按下确认前看到
   * 「这次生成里这一段到底说什么」。区别只在口径 —— 文案格不能说成
   * 「（未填）」，那不是没填，是他自己写的。
   */
  override?: string;
}

export interface ConfirmParamLine {
  label: string;
  text: string;
}

export interface GenerateConfirmInput {
  templateName: string;
  version: number;
  /** 模型目录里的中文名（没有就退回 id） */
  modelLabel: string;
  /** 使用者填的槽位 */
  slots: ConfirmSlotLine[];
  /** 作者设的、这次真正生效的参数（时长、画幅、张数…） */
  params: ConfirmParamLine[];
  /** 计费模式：`free` / `credits`；模型没声明就没有 */
  billingMode?: string;
}

export interface GenerateConfirmRow {
  label: string;
  value: string;
}

export interface GenerateConfirm {
  rows: GenerateConfirmRow[];
  /** 需要特别注意的那一句；没有就不显示 */
  notice?: string;
  noticeTone: 'warn' | 'danger' | 'info';
}

/** 计费模式的显示文案。与 `entities/model/lib/paramForm` 的短标签同源。 */
const BILLING_LABEL: Record<string, string> = {
  free: '免费队列',
  credits: '积分加速',
};

export function buildGenerateConfirm(input: GenerateConfirmInput): GenerateConfirm {
  const rows: GenerateConfirmRow[] = [
    {label: '模板', value: `${input.templateName} v${input.version}`},
    {label: '模型', value: input.modelLabel},
  ];

  if (input.slots.length > 0) {
    const total = input.slots.reduce((sum, slot) => sum + slot.count, 0);
    rows.push({label: '素材', value: `${total} 项`});
  }

  /* 可选槽位没填 → 用「未选时文案」。逐条摆出来，不合并成一行 ——
     合并成「未填则用：A、B」就看不出哪个槽位对哪段话了，而确认框的
     职责正是「按下之前看清这次会发生什么」。

     `count === 0` 是硬条件：光看 `fallback` 有值的话，调用方在填了素材
     却顺手带上 fallback 时就会多出一行「（未填）」—— 而那行下面写着
     兜底文案，使用者会以为这次生成用的就是那段话。 */
  const fallbacks = input.slots.filter(slot => slot.count === 0 && slot.fallback);
  for (const slot of fallbacks) {
    rows.push({
      label: `${slot.label}（未填）`,
      value: slot.fallback as string,
    });
  }

  /* 使用者改写的提示词：这一格没有素材，写的就是这次要用的那段话。
     不写成「（未填）」—— 那句话不是缺省，是他自己填的。 */
  for (const slot of input.slots) {
    if (!slot.override) continue;
    rows.push({label: `${slot.label}（改写的提示词）`, value: slot.override});
  }

  if (input.params.length > 0) {
    rows.push({
      label: '参数',
      value: input.params.map(item => `${item.label} ${item.text}`).join(' · '),
    });
  }

  if (input.billingMode) {
    rows.push({
      label: '计费',
      value: BILLING_LABEL[input.billingMode] ?? input.billingMode,
    });
  }

  /* 只有积分加速才需要提醒。免费队列慢一点但不扣钱，没什么可确认的 ——
     每次都弹一句「不会扣积分」是噪音，会让人习惯性地盲点确认。 */
  const notice =
    input.billingMode === 'credits'
      ? '这次提交走积分加速队列，会消耗账号积分。'
      : undefined;

  return {
    rows,
    ...(notice ? {notice} : {}),
    noticeTone: input.billingMode === 'credits' ? 'warn' : 'info',
  };
}
