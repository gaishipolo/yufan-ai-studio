import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { SpecSlotIn, TemplateSpecIn, TemplateSpecOut } from '@/shared/api/schema';
import { KIND_LABEL } from '@/entities/library/model/types';
import {
  MAX_ALLOWED_MODELS,
  MAX_PROMPT,
  SLOT_TYPE_LABELS,
  authoredFingerprint,
  blankSlot,
  blankSpec,
  dropUserSelectableParams,
  effectiveModelIds,
  fromSpecOut,
  hasSingleSlotRef,
  insertRef,
  isOptionalSlot,
  joinModelSelection,
  nextSlotKey,
  normalize,
  promptRefError,
  rekeySlot,
  referenceChips,
  rewriteSlotRef,
  selfRefChips,
  setSlotOptional,
  slotRefModes,
  textsToClearForRequired,
  slotsMissingTag,
  splitModelSelection,
  stripSlotRefs,
} from './specDraft';

describe('提示词上限', () => {  /**
   * 漂移守卫：前端这个数只用来显示「字数 N / 上限」，但它必须跟后端一致 ——
   * 后端超了会 400，界面上却显示还有余量，是最坏的一种不一致。
   *
   * 真值在契约里（`TemplateSpecIn.prompt.maxLength`），所以直接对着
   * `api/docs/openapi.json` 读，而不是把数字再抄一遍。
   */
  it('跟契约里 TemplateSpecIn.prompt.maxLength 一致', () => {
    // jsdom 环境下 import.meta.url 不是 file://，用 cwd 找仓库根
    const candidates = [
      resolve(process.cwd(), '../api/docs/openapi.json'),
      resolve(process.cwd(), 'api/docs/openapi.json'),
    ];
    const file = candidates.find(path => existsSync(path));
    expect(file, '找不到 api/docs/openapi.json').toBeTruthy();
    const openapi = JSON.parse(readFileSync(file!, 'utf8')) as {
      components: {schemas: {TemplateSpecIn: {properties: {prompt: {maxLength: number}}}}};
    };
    expect(openapi.components.schemas.TemplateSpecIn.properties.prompt.maxLength).toBe(
      MAX_PROMPT,
    );
  });
});

describe('模板规格草稿', () => {
  it('新草稿自带一个图片槽位，位置从 0 开始', () => {
    const spec = blankSpec();
    const slots = spec.slots ?? [];
    expect(slots).toHaveLength(1);
    expect(slots[0].position).toBe(0);
    expect(slots[0].type).toBe('image');
    expect(slots[0].binding).toBe('user');
  });

  it('槽位 key 自动生成且可读：同类型递增、不同类型不撞', () => {
    // 界面上不显示 key，但提示词里的 `{{slot:image_1}}` 要人一眼能读。
    expect(blankSlot(0, 'image', []).key).toBe('image_1');
    const slots = [blankSlot(0, 'image', [])];
    expect(nextSlotKey(slots, 'image')).toBe('image_2');
    expect(nextSlotKey(slots, 'video')).toBe('video_1');
    // 删掉中间那个之后序号可以复用，不要求连续
    slots.push({...blankSlot(1, 'image', slots), key: 'image_3'});
    expect(nextSlotKey(slots, 'image')).toBe('image_2');
  });

  it('插入引用时只在需要的一侧补空格，并把光标放到引用之后', () => {
    // 光标在末尾：只需前导空格，不该凭空多一个尾空格。
    const atEnd = insertRef('替换成', '{{slot:hero}}', 3);
    expect(atEnd.text).toBe('替换成 {{slot:hero}}');
    expect(atEnd.caret).toBe('替换成 {{slot:hero}}'.length);

    // 光标在中间：两侧都要断开，否则 token 会和中文粘成一个词。
    const middle = insertRef('替换成同款', '{{slot:hero}}', 3);
    expect(middle.text).toBe('替换成 {{slot:hero}} 同款');
  });

  it('插入到空文本不产生前后空格', () => {
    expect(insertRef('', '{{slot:hero}}', 0).text).toBe('{{slot:hero}}');
  });

  it('视觉槽位与文本槽位都能引用；多值槽位额外给「全部」', () => {
    const spec = blankSpec();
    spec.slots = [
      {...blankSlot(0), key: 'hero', label: '主图', minCount: 1, maxCount: 1},
      {...blankSlot(1), key: 'detail', label: '细节', minCount: 0, maxCount: 3},
      // 文本槽位是"提示词里唯一可变的地方"：编译成 {{Text N}}，必须能引用。
      {...blankSlot(2, 'text', []), key: 'brief', type: 'text', refMode: 'attachment', label: '口播'},
      {...blankSlot(3), key: 'bgm', type: 'audio', refMode: 'attachment'},
    ];
    const chips = referenceChips(spec);
    expect(chips.map(chip => chip.token)).toEqual([
      '{{slot:hero}}',
      '{{slot:detail}}',
      '{{slot:detail*}}',
      '{{slot:brief}}',
    ]);
    // 音频没有编号空间，给入口就等于让用户写出一条必然 400 的引用。
    expect(chips.some(chip => chip.token.includes('bgm'))).toBe(false);
  });

  it('删槽位时把它在提示词里的引用一并清掉，并回条数', () => {
    // 留着的下场是「点生成才报引用了不存在的槽位」，看不出跟删槽位有关。
    const text = '把 {{slot:source}} 换成 {{slot:hero*}} 与 {{slot:hero}}';
    const one = stripSlotRefs(text, 'hero');
    expect(one.count).toBe(2);
    // 句子残了是必然的（"换成 与" 这种），这里只保证 token 清干净、别的不动
    expect(one.text).not.toContain('slot:hero');
    expect(one.text).toContain('{{slot:source}}');
    expect(one.text).not.toMatch(/ {2,}/);
    // 没引用过就原样返回，不要顺手重排空格
    const untouched = stripSlotRefs('把 {{slot:source}} 换掉', 'hero');
    expect(untouched).toEqual({text: '把 {{slot:source}} 换掉', count: 0});
    // 前缀相同的 key 不能被误伤
    const sibling = stripSlotRefs('{{slot:image_1}} 与 {{slot:image_10}}', 'image_1');
    expect(sibling.count).toBe(1);
    // 只清精确 key：`image_10` 不能被 `image_1` 连坐
    expect(sibling.text.trim()).toBe('与 {{slot:image_10}}');
  });

  it('槽位类型是四种，展示名取自资产库的 kind', () => {
    // 类型是产品定的四种，不跟着资产库多出来：库里加一类，槽位不该自己多一档。
    expect(Object.keys(SLOT_TYPE_LABELS).sort()).toEqual([
      'audio',
      'image',
      'text',
      'video',
    ]);
    // 展示名仍然取自资产库：库里把 video 叫「成片」，这里也叫成片
    // （曾经写死「视频」，同一个东西两个名字）。
    expect(SLOT_TYPE_LABELS.video).toBe(KIND_LABEL.video);
    expect(SLOT_TYPE_LABELS.image).toBe(KIND_LABEL.image);
  });

  it('改类型时改名：没被引用过才改，引用过就留着', () => {
    const slots = [{...blankSlot(0), key: 'image_1', type: 'image' as const}];
    // 没引用 → 跟着类型改成 video_1
    const renamed = rekeySlot(slots, 0, 'video', '把 {{slot:other}} 换掉');
    expect(renamed[0].key).toBe('video_1');
    expect(renamed[0].type).toBe('video');
    // 已被引用 → 原名不动，否则提示词里的引用会静默失效
    const kept = rekeySlot(slots, 0, 'video', '把 {{slot:image_1}} 换掉');
    expect(kept[0].key).toBe('image_1');
  });

  it('带空格的引用也算「被引用」：改类型时不改名', () => {
    // 后端结构校验与编译都容空格；前端曾经只认紧贴写法，于是把带空格引用当成
    // 「没引用」→ 改类型时把 key 改名 → 保存被 400「引用了不存在的槽位」，
    // 而界面完全没提这次改名（审查 3.4）。
    const slots = [{...blankSlot(0), key: 'image_1', type: 'image' as const}];
    for (const prompt of ['用 {{ slot:image_1 }} 合成', '用 {{slot:image_1 }}', '用 {{ slot:image_1}}']) {
      expect(rekeySlot(slots, 0, 'video', prompt)[0].key).toBe('image_1');
    }
    // 删槽位同理：带空格的那几处也要一起清掉
    expect(stripSlotRefs('{{ slot:hero }} 与 {{slot:hero }}', 'hero').count).toBe(2);
  });

  /* 改名时「选中时文案」里的自引用必须跟着改。
     漏了这一步的话：改名后文案里的 token 还指着旧 key，而后端
     `_validate_slot_texts` 规定「选中时文案只能引用自己」—— 引用旧 key
     会被判成「引用了别的槽位」，作者保存时被 400 打回，界面看不出原因。 */
  it('改名时「选中时文案」里的自引用跟着改', () => {
    const slots = [
      {
        ...blankSlot(0),
        key: 'image_1',
        type: 'image' as const,
        filledText: '人物参考 {{slot:image_1}}',
      },
    ];
    const renamed = rekeySlot(slots, 0, 'video', '把 {{slot:other}} 换掉');
    expect(renamed[0].key).toBe('video_1');
    expect(renamed[0].filledText).toBe('人物参考 {{slot:video_1}}');
  });

  it('正文引用过时**不**改名（改了会让正文引用静默失效）', () => {
    const slots = [
      {
        ...blankSlot(0),
        key: 'image_1',
        type: 'image' as const,
        filledText: '人物参考 {{slot:image_1}}',
      },
    ];
    const kept = rekeySlot(slots, 0, 'video', '把 {{slot:image_1}} 换掉');
    expect(kept[0].key).toBe('image_1');
    expect(kept[0].filledText).toBe('人物参考 {{slot:image_1}}');
  });

  it('rewriteSlotRef 只改指定槽位的引用', () => {
    expect(rewriteSlotRef('用 {{slot:image_1}} 换掉', 'image_1', 'video_1')).toBe(
      '用 {{slot:video_1}} 换掉',
    );
    // 别的槽位不动
    expect(rewriteSlotRef('用 {{slot:image_2}} 换掉', 'image_1', 'video_1')).toBe(
      '用 {{slot:image_2}} 换掉',
    );
  });

  /* 修饰符必须**原样保留**：`{{slot:x*}}` 改成 `{{slot:y}}` 是把
     「列出这组」变成了「第一张」—— 语义变了。 */
  it('rewriteSlotRef 保留修饰符（整组 / 第 N 张不能变成单张）', () => {
    expect(rewriteSlotRef('用 {{slot:image_1*}} 换掉', 'image_1', 'video_1')).toBe(
      '用 {{slot:video_1*}} 换掉',
    );
    expect(rewriteSlotRef('用 {{slot:image_1.2}} 换掉', 'image_1', 'video_1')).toBe(
      '用 {{slot:video_1.2}} 换掉',
    );
  });

  /* 三种引用的形态要分得开：单张引用才有「一段文案」可言。 */
  it('slotRefModes 分得清单张 / 整组 / 第 N 张', () => {
    const modes = slotRefModes(
      '用 {{slot:a}} 和 {{slot:b*}} 以及 {{slot:c.2}}，还有 {{slot:a}} 重复',
    );
    expect(modes.a).toEqual(new Set(['']));
    expect(modes.b).toEqual(new Set(['*']));
    expect(modes.c).toEqual(new Set(['.2']));
  });

  it('hasSingleSlotRef 只认单张引用', () => {
    expect(hasSingleSlotRef('用 {{slot:a}}', 'a')).toBe(true);
    expect(hasSingleSlotRef('用 {{slot:a*}}', 'a')).toBe(false);
    expect(hasSingleSlotRef('用 {{slot:a.2}}', 'a')).toBe(false);
    expect(hasSingleSlotRef('用别的', 'a')).toBe(false);
  });

  it('hasSingleSlotRef 容空格（与后端同一口径）', () => {
    expect(hasSingleSlotRef('用 {{ slot:a }}', 'a')).toBe(true);
  });

  it('发布前预检：只有「让用户填的图片/视频」槽位缺标签', () => {
    const slots = [
      {...blankSlot(0, 'image', []), key: 'image_1', tagIds: []},
      {...blankSlot(1, 'video', []), key: 'video_1', tagIds: []},
      // 固定槽位、文本、音频都不要求标签
      {...blankSlot(2, 'image', []), key: 'image_2', binding: 'fixed' as const, tagIds: []},
      {...blankSlot(3, 'text', []), key: 'text_1', tagIds: []},
      {...blankSlot(4, 'image', []), key: 'image_3', tagIds: ['tag-1']},
    ];
    expect(slotsMissingTag(slots)).toEqual(['图片 1', '成片 2']);
    // 都填了就是空数组（保存草稿不判，预检与发布判）
    expect(slotsMissingTag([{...slots[0], tagIds: ['tag-1']}])).toEqual([]);
  });

  it('已发布但没有草稿时，用最近发布版本还原草稿', () => {
    const out: TemplateSpecOut = {
      id: 'm1',
      key: 'demo',
      name: '演示',
      description: '',
      ownerId: 'u1',
      status: 'published',
      revision: 3,
      fingerprint: 'fp',
      currentVersion: 2,
      createdAt: '2026-11-14T00:00:00Z',
      updatedAt: '2026-11-14T00:00:00Z',
      latest: {
        version: 2,
        model: 'libtv-wan3-replacement',
        params: {aspectRatio: '9:16'},
        prompt: '固定\n\n{{slot:hero}}',
        slots: [{...blankSlot(0), key: 'hero'}, {...blankSlot(5), key: 'extra'}],
        fingerprint: 'fp2',
        publishedBy: 'u1',
        publishedAt: '2026-11-14T00:00:00Z',
      },
    };
    const draft = fromSpecOut(out);
    expect(draft.key).toBe('demo');
    expect(draft.revision).toBe(3);
    expect(draft.model).toBe('libtv-wan3-replacement');
    // 位置按数组顺序重排：发布快照里的 position 可能不连续（历史数据）。
    expect((draft.slots ?? []).map(slot => slot.position)).toEqual([0, 1]);
  });

  it('提交前按当前顺序重排 position', () => {
    const spec = blankSpec();
    spec.slots = [{...blankSlot(7), key: 'b'}, {...blankSlot(2), key: 'a'}];
    expect((normalize(spec).slots ?? []).map(slot => [slot.key, slot.position])).toEqual([
      ['b', 0],
      ['a', 1],
    ]);
  });
});

describe('dropUserSelectableParams · 作者不该写死「使用者选的」参数', () => {
  it('把 userSelectable 的键从 params 里剔掉', () => {
    // 老草稿里可能已经存了计费模式（那时还没这条规矩），换模型后残留也会
    // 跟着走 —— 后端预检/发布会直接拒掉，报「计费模式 由使用模板的人来选」。
    const spec = blankSpec();
    spec.params = {durationSec: 6, billingMode: 'credits'};

    const cleaned = dropUserSelectableParams(spec, ['billingMode']);
    expect(cleaned.params).toEqual({durationSec: 6});
  });

  it('没有这类参数时原样返回，不复制一份', () => {
    const spec = blankSpec();
    spec.params = {durationSec: 6};
    expect(dropUserSelectableParams(spec, [])).toBe(spec);
  });

  it('剔完不剩下任何参数时给空对象，不留 undefined', () => {
    const spec = blankSpec();
    spec.params = {billingMode: 'credits'};
    expect(dropUserSelectableParams(spec, ['billingMode']).params).toEqual({});
  });
});


describe('允许选用的模型', () => {
  it('新草稿的清单是空的 —— 空 = 只有固定模型（老行为）', () => {
    expect(blankSpec().allowedModels).toEqual([]);
  });

  it('可选项 = 固定模型 + 清单：去重、保序，固定模型在前', () => {
    // 固定模型永远在可选项里：它是模板自己的模型，作者没把它写进清单也不该
    // 让使用者无路可走。
    expect(effectiveModelIds('a', ['b', 'c'])).toEqual(['a', 'b', 'c']);
    expect(effectiveModelIds('a', ['a', 'b'])).toEqual(['a', 'b']);
    expect(effectiveModelIds('a', [])).toEqual(['a']);
    expect(effectiveModelIds('', ['b'])).toEqual(['b']);
  });

  it('已发布但没有草稿时，用最近发布版本还原草稿（清单也要带上）', () => {
    const out: TemplateSpecOut = {
      id: 'm2',
      key: 'demo',
      name: '演示',
      description: '',
      ownerId: 'u1',
      status: 'published',
      revision: 4,
      fingerprint: 'fp',
      currentVersion: 3,
      createdAt: '2026-11-17T00:00:00Z',
      updatedAt: '2026-11-17T00:00:00Z',
      latest: {
        version: 3,
        model: 'libtv-wan3-replacement',
        allowedModels: ['bailian-wan3-replacement'],
        params: {},
        prompt: '固定',
        slots: [],
        fingerprint: 'fp3',
        publishedBy: 'u1',
        publishedAt: '2026-11-17T00:00:00Z',
      },
    };
    const draft = fromSpecOut(out);
    expect(draft.allowedModels).toEqual(['bailian-wan3-replacement']);
  });
});

describe('模型选择：勾选顺序即默认序', () => {
  it('第一个勾选的模型是默认模型（契约里的 model），其余进 allowedModels', () => {
    const {model, allowedModels} = splitModelSelection(['a', 'b', 'c']);
    expect(model).toBe('a');
    expect(allowedModels).toEqual(['b', 'c']);
  });

  it('只勾一款时：model 是它，allowedModels 为空', () => {
    const {model, allowedModels} = splitModelSelection(['a']);
    expect(model).toBe('a');
    expect(allowedModels).toEqual([]);
  });

  it('一个都没勾时：model 为空串（后端会拒「模型必填」）', () => {
    const {model, allowedModels} = splitModelSelection([]);
    expect(model).toBe('');
    expect(allowedModels).toEqual([]);
  });

  it('重复的只留第一次出现的位置', () => {
    const {model, allowedModels} = splitModelSelection(['b', 'a', 'b']);
    expect(model).toBe('b');
    expect(allowedModels).toEqual(['a']);
  });

  it('空串不算一项', () => {
    const {model, allowedModels} = splitModelSelection(['', 'a']);
    expect(model).toBe('a');
    expect(allowedModels).toEqual([]);
  });

  it('还原：契约两段 → 勾选清单，固定模型排第一', () => {
    // 老草稿是「单选 + 多选」两段式的，还原时固定模型（=默认）排第一。
    expect(joinModelSelection('a', ['b', 'c'])).toEqual(['a', 'b', 'c']);
    expect(joinModelSelection('a', [])).toEqual(['a']);
    expect(joinModelSelection('', ['b'])).toEqual(['b']);
  });

  it('split 与 join 互为逆运算', () => {
    const selection = ['a', 'b', 'c'];
    const {model, allowedModels} = splitModelSelection(selection);
    expect(joinModelSelection(model, allowedModels)).toEqual(selection);
  });

  it('还原出来的清单喂给 effectiveModelIds，与 split 的结果一致', () => {
    // 模板库一侧用 effectiveModelIds 算「这份模板能跑哪些模型」。
    // 作者侧用 split 算 —— 两处口径必须一致，否则会「界面里能选、提交时被拒」。
    const selection = ['a', 'b'];
    const {model, allowedModels} = splitModelSelection(selection);
    expect(effectiveModelIds(model, allowedModels)).toEqual(selection);
  });
});

describe('模型数量上限', () => {
  /**
   * 漂移守卫：前端用这个数拦住超限勾选，后端用它 422 拒。
   * 两处不一致 = 用户能勾到超限、提交时才被拒（界面上看不出问题）。
   *
   * 真值在契约里（`TemplateSpecIn.allowedModels.maxItems`），直接对着
   * `api/docs/openapi.json` 读，而不是把数字再抄一遍。
   */
  it('跟契约里 TemplateSpecIn.allowedModels.maxItems 一致', () => {
    const candidates = [
      resolve(process.cwd(), '../api/docs/openapi.json'),
      resolve(process.cwd(), 'api/docs/openapi.json'),
    ];
    const file = candidates.find(path => existsSync(path));
    expect(file, '找不到 api/docs/openapi.json').toBeTruthy();
    const openapi = JSON.parse(readFileSync(file!, 'utf8')) as {
      components: {
        schemas: {
          TemplateSpecIn: {
            properties: {allowedModels: {maxItems?: number}};
          };
        };
      };
    };
    expect(openapi.components.schemas.TemplateSpecIn.properties.allowedModels.maxItems).toBe(
      MAX_ALLOWED_MODELS,
    );
  });
});

describe('必填 / 可选', () => {
  /**
   * 判据只有 `minCount === 0` —— **不另存一个 `required` 字段**。
   *
   * 两个事实源不同步时，界面显示「必填」而编译器放行（或反过来），
   * 用户看到的与实际生效的不一致，而这类错只在生成结果里显形。
   */
  it('可选 = 最少数 0', () => {
    expect(isOptionalSlot({minCount: 0} as SpecSlotIn)).toBe(true);
    expect(isOptionalSlot({minCount: 1} as SpecSlotIn)).toBe(false);
    // 老草稿没写 minCount → 按必填（1 张）
    expect(isOptionalSlot({} as SpecSlotIn)).toBe(false);
  });

  it('切成可选时最少数变 0、最多数至少留 1', () => {
    const patch = setSlotOptional({minCount: 1, maxCount: 1} as SpecSlotIn, true);
    expect(patch).toEqual({minCount: 0, maxCount: 1});
  });

  it('切回必填时最少数至少是 1', () => {
    const patch = setSlotOptional({minCount: 0, maxCount: 3} as SpecSlotIn, false);
    expect(patch).toEqual({minCount: 1, maxCount: 3});
  });

  it('最多数是 0 的怪状态也会被拉回 1（不然没法选任何东西）', () => {
    const patch = setSlotOptional({minCount: 0, maxCount: 0} as SpecSlotIn, false);
    expect(patch).toEqual({minCount: 1, maxCount: 1});
  });

  it('固定槽位不能切成可选：素材早就定死了，不存在「选不选」', () => {
    expect(setSlotOptional({binding: 'fixed', minCount: 1, maxCount: 1} as SpecSlotIn, true)).toEqual({});
  });
});

describe('「选中时文案」的自身候选', () => {
  /**
   * 候选**只列自己**：后端规定「选中时文案只能引用自己」（跨槽位引用会让
   * 编号依赖别的槽位的填充量，同一段文案在不同填充下编译出不同结果）。
   *
   * 候选是**算出来**的 —— 用户不该手敲 `{{slot:key}}`（拼错要到编译期才
   * 报错，槽位改名后手敲的引用会静默失效）。
   */
  it('单值槽位只有「单张」一种写法', () => {
    const chips = selfRefChips({key: 'image_1', type: 'image', maxCount: 1} as SpecSlotIn);
    expect(chips.map(chip => chip.token)).toEqual(['{{slot:image_1}}']);
  });

  it('多值槽位有「单张 / 全部 / 第 N 张」三种写法', () => {
    const chips = selfRefChips({key: 'image_1', type: 'image', maxCount: 3} as SpecSlotIn);
    expect(chips.map(chip => chip.token)).toEqual([
      '{{slot:image_1}}',
      '{{slot:image_1*}}',
      '{{slot:image_1.2}}',
      '{{slot:image_1.3}}',
    ]);
  });

  it('「第 1 张」不单独列：它就是「单张」，重复列会让人以为有两个意思', () => {
    const chips = selfRefChips({key: 'image_1', type: 'image', maxCount: 3} as SpecSlotIn);
    expect(chips.map(chip => chip.token)).not.toContain('{{slot:image_1.1}}');
  });

  it('候选里只有这个槽位自己的 key（不列别的槽位）', () => {
    const chips = selfRefChips({key: 'hero', type: 'image', maxCount: 2} as SpecSlotIn);
    expect(chips.every(chip => chip.token.includes('hero'))).toBe(true);
  });

  it('用展示名，不用内部 key（作者看不懂 key）', () => {
    const chips = selfRefChips({
      key: 'image_1',
      type: 'image',
      maxCount: 1,
      label: '主图',
    } as SpecSlotIn);
    expect(chips[0].label).toContain('主图');
    expect(chips[0].label).not.toContain('image_1');
  });

  it('没起名时回退到「类型 + 第几个」（位置可读名）', () => {
    const chips = selfRefChips({
      key: 'image_1',
      type: 'image',
      maxCount: 1,
      position: 0,
    } as SpecSlotIn);
    expect(chips[0].label).toContain(KIND_LABEL.image);
  });
});

describe('必填槽位清掉两段文案', () => {
  /**
   * **必填 = 素材一定在 = 直接插编号**。两段文案（「填了怎么说 / 没填怎么说」）
   * 对必填槽位没有意义 —— 没有「没填」这个状态。
   *
   * 清掉而不是留着：留着的话后端会 400（必填槽位带文案直接拒），而界面
   * 看不出是哪一条规则 —— 作者明明写了文案，点保存却被拒。
   */
  it('切成必填时两段文案被清空', () => {
    const patch = setSlotOptional(
      {
        minCount: 0,
        maxCount: 1,
        filledText: '人物参考 {{slot:hero}}',
        emptyText: '主角是穿红裙的年轻女性',
      } as SpecSlotIn,
      false,
    );
    expect(patch).toEqual({
      minCount: 1,
      maxCount: 1,
      filledText: '',
      emptyText: '',
    });
  });

  it('本来就没文案时不动它（不塞空串进去）', () => {
    const patch = setSlotOptional({minCount: 0, maxCount: 1} as SpecSlotIn, false);
    expect(patch).toEqual({minCount: 1, maxCount: 1});
    expect(patch).not.toHaveProperty('filledText');
  });

  it('切成可选不清文案（那是作者写的，回切回来还要在）', () => {
    const patch = setSlotOptional(
      {minCount: 1, maxCount: 1, filledText: '人物参考'} as SpecSlotIn,
      true,
    );
    expect(patch).toEqual({minCount: 0, maxCount: 1});
  });

  it('判断函数：只管「有没有文案要清」，目标状态由调用方判断', () => {
    expect(
      textsToClearForRequired({minCount: 1, filledText: 'x'} as SpecSlotIn),
    ).toEqual({filledText: '', emptyText: ''});
    // 槽位现在还是可选（minCount=0）也照样返回「要清什么」——
    // 「切完之后是不是必填」是调用方才知道的事。
    expect(
      textsToClearForRequired({minCount: 0, filledText: 'x'} as SpecSlotIn),
    ).toEqual({filledText: '', emptyText: ''});
    expect(
      textsToClearForRequired({minCount: 1} as SpecSlotIn),
    ).toBeNull();
  });

  it('只有空白的文案不算「有文案」（不必清）', () => {
    expect(
      textsToClearForRequired({minCount: 1, filledText: '   '} as SpecSlotIn),
    ).toBeNull();
  });
});

/**
 * 「有没有未保存改动」的判据。
 *
 * 比的是**内容**，不是 JSON 形状 —— 形状差异会让「只点了几下、什么都没改」
 * 的模板反复弹「有未保存的修改」，而用户改回原样后又被判成有改动。
 */
describe('authoredFingerprint：判有没有改动', () => {
  /** 一份内容固定的规格，只用来对比「同内容不同写法」。 */
  const base = (): TemplateSpecIn => ({
    key: 'spec-1',
    name: '商品图',
    description: '',
    model: 'doubao-seedream',
    allowedModels: ['doubao-seedream'],
    params: {size: '1024x1024'},
    prompt: '一张 {{slot:hero}}',
    slots: [
      {
        key: 'hero',
        type: 'image',
        binding: 'user',
        refMode: 'mixed',
        minCount: 1,
        maxCount: 1,
        label: '正面图',
        filledText: '',
        emptyText: '',
        position: 0,
      },
    ],
    attributes: {},
  });

  it('内容一样 → 指纹一样', () => {
    expect(authoredFingerprint(base())).toBe(authoredFingerprint(base()));
  });

  /* 这条是这次改动的起因：`SlotCard` 切回「用户填」时写 `assetId: null`，
     而 `blankSlot()` 根本不带这个键 —— 切一圈内容没变，JSON 却不等。 */
  it('`assetId: null` 与压根没有 `assetId` 是同一件事', () => {
    const withNull = base();
    withNull.slots = [{...withNull.slots![0], assetId: null}];

    expect(authoredFingerprint(withNull)).toBe(authoredFingerprint(base()));
  });

  it('候选标签是集合：空数组、缺失、顺序不同都不算改动', () => {
    const missing = base();
    const empty = base();
    empty.slots = [{...empty.slots![0], tagIds: []}];
    const shuffled = base();
    const reordered = base();
    shuffled.slots = [{...shuffled.slots![0], tagIds: ['t2', 't1']}];
    reordered.slots = [{...reordered.slots![0], tagIds: ['t1', 't2']}];

    const canonical = authoredFingerprint(missing);
    expect(authoredFingerprint(empty)).toBe(canonical);
    expect(authoredFingerprint(shuffled)).toBe(authoredFingerprint(reordered));
  });

  it('缺省字段与显式默认值是同一件事', () => {
    const sparse = base();
    sparse.slots = [{key: 'hero', type: 'image', position: 0} as SpecSlotIn];

    const explicit = base();
    explicit.slots = [
      {
        key: 'hero',
        type: 'image',
        binding: 'user',
        refMode: 'mixed',
        minCount: 1,
        maxCount: 1,
        position: 0,
      } as SpecSlotIn,
    ];

    // label 在 sparse 里缺失、explicit 里也没有 → 两边都归一成 ''
    expect(authoredFingerprint(sparse)).toBe(authoredFingerprint(explicit));
  });

  it('对象键的顺序不影响结果', () => {
    const a = base();
    const b = base();
    a.params = {size: '1024x1024', seed: 1};
    b.params = {seed: 1, size: '1024x1024'};

    expect(authoredFingerprint(a)).toBe(authoredFingerprint(b));
  });

  /* `revision` 是服务端给下一次提交的并发令牌，不是作者写的东西。 */
  it('`revision` 变了不算改动', () => {
    const bumped = {...base(), revision: 7};
    expect(authoredFingerprint(bumped)).toBe(authoredFingerprint(base()));
  });

  it('真改了内容就算改动', () => {
    const canonical = authoredFingerprint(base());
    const cases: TemplateSpecIn[] = [
      {...base(), name: '商品图 2'},
      {...base(), prompt: '两张改 {{slot:hero}}'},
      {...base(), model: 'kling-2.1'},
      {...base(), description: '一段说明'},
    ];
    for (const spec of cases) {
      expect(authoredFingerprint(spec)).not.toBe(canonical);
    }

    // 槽位本身：标签、绑定、必填档位、素材
    const slotPatches: Partial<SpecSlotIn>[] = [
      {label: '背面图'},
      {binding: 'fixed'},
      {minCount: 0},
      {assetId: 'asset-9'},
      {filledText: '用这张'},
      {tagIds: ['t1']},
    ];
    for (const patch of slotPatches) {
      const next = base();
      next.slots = [{...next.slots![0], ...patch}];
      expect(authoredFingerprint(next)).not.toBe(canonical);
    }
  });

  it('槽位的顺序是内容：拖拽重排算改动', () => {
    const two = base();
    two.slots = [
      {...two.slots![0]},
      {...two.slots![0], key: 'back', label: '背面图', position: 1},
    ];
    const swapped = {...two, slots: [two.slots![1], two.slots![0]]};

    expect(authoredFingerprint(swapped)).not.toBe(authoredFingerprint(two));
  });
});

/**
 * 引用校验：**只拦错的，不拦花括号**。
 *
 * 旧判据是「花括号变多就拒」。它拦不住真正的问题（拼错的 key 也只是两个
 * 花括号），却把正确写法一起拦了 —— 粘一段 `{{slot:image_1}}` 进来，
 * 明明是对的，界面却报「不要手写大括号」。
 */
describe('promptRefError：提示词里的引用', () => {
  const KEYS = ['image_1', 'image_2', 'group'];

  it('纯文字没问题', () => {
    expect(promptRefError('一张图，暖色调', KEYS)).toBeNull();
  });

  it('合法引用一律放行（含空格、`*`、`.N` 三种修饰符）', () => {
    expect(promptRefError('{{slot:image_1}}', KEYS)).toBeNull();
    expect(promptRefError('{{ slot:image_2 }}', KEYS)).toBeNull();
    expect(promptRefError('整组 {{slot:group*}}', KEYS)).toBeNull();
    expect(promptRefError('第二张 {{slot:group.2}}', KEYS)).toBeNull();
  });

  it('key 不存在 → 指名道姓地说出来', () => {
    expect(promptRefError('{{slot:image_9}}', KEYS)).toContain('image_9');
  });

  /* 手写编号会与本次填充量错位：少填一张，后面全部错位。 */
  it('手写 Mixed / Text 编号 → 拒', () => {
    expect(promptRefError('参考 {{Mixed 2}}', KEYS)).toContain('不要手写');
    expect(promptRefError('{{Text 1}}', KEYS)).toContain('不要手写');
  });

  it('花括号没闭合 → 拒', () => {
    expect(promptRefError('{{slot:image_1', KEYS)).toContain('没闭合');
    expect(promptRefError('{{ image_1 }}', KEYS)).toContain('认不出来');
  });

  it('删字删到一半（合法引用少一个括号）也要拒', () => {
    expect(promptRefError('{{slot:image_1} }}', KEYS)).not.toBeNull();
  });
});
