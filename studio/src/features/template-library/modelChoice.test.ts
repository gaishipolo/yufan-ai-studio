import { describe, expect, it } from 'vitest';
import type { ModelSpec } from '@/entities/model/model/types';
import { modelChoiceOptions, resolveModelChoice } from './modelChoice';

/**
 * 模板库这一侧的「用哪款模型」。
 *
 * 四件事必须钉死：
 * 1. **没有清单 = 固定模型**（老模板的行为，页面连下拉都不出现）；
 * 2. 清单里的每一款都能选，选中哪款就**用哪款的接口与参数默认值**
 *    （用 A 的参数去跑 B 是最隐蔽的一种错）；
 * 3. 固定模型永远在可选项里 —— 它是模板自己的模型；
 * 4. 选了不在清单里的模型（脏数据 / 旧版本）时退回固定模型，而不是停在
 *    一个「选了个不存在的模型」的状态上。
 */

const VIDEO: ModelSpec = {
  id: 'libtv-wan3-replacement',
  label: 'LibTV Wan3',
  capability: 'video',
  provider: 'libtv',
  paramSchema: {
    durationSec: {type: 'int', min: 2, max: 30, step: 1, default: 5},
    aspectRatio: {type: 'enum', options: ['9:16', '16:9'], default: '9:16'},
  },
  maxReferences: 5,
  isDefault: false,
};

const OTHER_VIDEO: ModelSpec = {
  ...VIDEO,
  id: 'bailian-wan3-replacement',
  label: '百炼 Wan3',
  paramSchema: {
    durationSec: {type: 'int', min: 2, max: 30, step: 1, default: 8},
    aspectRatio: {type: 'enum', options: ['9:16', '16:9'], default: '16:9'},
  },
};

const IMAGE: ModelSpec = {
  id: 'agnes-image-2.5-flash',
  label: 'Agnes 图片',
  capability: 'image',
  provider: 'agnes',
  paramSchema: {
    size: {type: 'enum', options: ['1024x1024'], default: '1024x1024'},
    count: {type: 'int', min: 1, max: 1, default: 1},
  },
  maxReferences: 4,
  isDefault: false,
};

const IMAGES = [IMAGE];
const VIDEOS = [VIDEO, OTHER_VIDEO];

describe('resolveModelChoice', () => {
  it('没有清单时就是固定模型（老模板的行为）', () => {
    const choice = resolveModelChoice(VIDEO.id, [], '', IMAGES, VIDEOS);
    expect(choice.id).toBe(VIDEO.id);
    expect(choice.kind).toBe('video');
    expect(choice.defaults.durationSec).toBe(5);
    expect(choice.defaults.aspectRatio).toBe('9:16');
  });

  it('使用者挑了清单里的另一款：接口与参数默认值都跟着换', () => {
    const choice = resolveModelChoice(
      VIDEO.id,
      [OTHER_VIDEO.id],
      OTHER_VIDEO.id,
      IMAGES,
      VIDEOS,
    );
    expect(choice.id).toBe(OTHER_VIDEO.id);
    expect(choice.kind).toBe('video');
    // 默认值取的是**选中那款**的：拿固定模型的时长去跑另一款，出来的片子不一样
    expect(choice.defaults.durationSec).toBe(8);
    expect(choice.defaults.aspectRatio).toBe('16:9');
  });

  it('固定模型永远在可选项里（作者没把它写进清单也一样）', () => {
    const choice = resolveModelChoice(VIDEO.id, [OTHER_VIDEO.id], '', IMAGES, VIDEOS);
    expect(choice.id).toBe(VIDEO.id);
  });

  it('选了不在清单里的模型就退回固定模型，不停在坏状态上', () => {
    const choice = resolveModelChoice(VIDEO.id, [OTHER_VIDEO.id], IMAGE.id, IMAGES, VIDEOS);
    expect(choice.id).toBe(VIDEO.id);
    expect(choice.kind).toBe('video');
  });

  it('挑了生图模型就走生图接口，参数默认值也换一套', () => {
    const choice = resolveModelChoice(VIDEO.id, [IMAGE.id], IMAGE.id, IMAGES, VIDEOS);
    expect(choice.kind).toBe('image');
    expect(choice.defaults.size).toBe('1024x1024');
    expect(choice.defaults.count).toBe(1);
    // 视频的默认值不该串过来
    expect(choice.defaults.durationSec).toBeUndefined();
  });
});

describe('modelChoiceOptions', () => {
  it('下拉里只有清单内的模型，名字取自模型目录', () => {
    const options = modelChoiceOptions([VIDEO.id, OTHER_VIDEO.id], IMAGES, VIDEOS);
    expect(options).toEqual([
      {value: 'libtv-wan3-replacement', label: 'LibTV Wan3'},
      {value: 'bailian-wan3-replacement', label: '百炼 Wan3'},
    ]);
  });

  it('目录里没有的模型退回显示 id，不显示空白', () => {
    const options = modelChoiceOptions(['ghost-model'], IMAGES, VIDEOS);
    expect(options).toEqual([{value: 'ghost-model', label: 'ghost-model'}]);
  });
});
