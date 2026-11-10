import { describe, expect, it } from 'vitest';
import type { VideoTemplateOut } from '@/shared/api/schema';
import { videoTemplatesForSource } from './videoTemplateFunctions';

describe('封装原片绑定', () => {
  it('同名未绑定的封装不能冒充已绑定原片的功能', () => {
    const unbound = {id: 'legacy', sourceVideoName: '原片.mp4'} as VideoTemplateOut;
    expect(videoTemplatesForSource([unbound], {id: 'source', name: '原片.mp4'})).toEqual([]);
  });
});
