import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { TemplateSpecOut } from '@/shared/api/schema';
import { KIND_LABEL } from '@/entities/library/model/types';
import {
  MAX_PROMPT,
  SLOT_TYPE_LABELS,
  blankSlot,
  blankSpec,
  dropUserSelectableParams,
  fromSpecOut,
  insertRef,
  nextSlotKey,
  normalize,
  rekeySlot,
  referenceChips,
  slotsMissingTag,
  stripSlotRefs,
} from './specDraft';

describe('提示词上限', () => {
  /**
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

  it('发布前预检：只有「让用户填的图片/视频」槽位缺一级标签', () => {
    const slots = [
      {...blankSlot(0, 'image', []), key: 'image_1', tagId: ''},
      {...blankSlot(1, 'video', []), key: 'video_1', tagId: ''},
      // 固定槽位、文本、音频都不要求标签
      {...blankSlot(2, 'image', []), key: 'image_2', binding: 'fixed' as const, tagId: ''},
      {...blankSlot(3, 'text', []), key: 'text_1', tagId: ''},
      {...blankSlot(4, 'image', []), key: 'image_3', tagId: 'tag-1'},
    ];
    expect(slotsMissingTag(slots)).toEqual(['图片 1', '成片 2']);
    // 都填了就是空数组（保存草稿不判，预检与发布判）
    expect(slotsMissingTag([{...slots[0], tagId: 'tag-1'}])).toEqual([]);
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
