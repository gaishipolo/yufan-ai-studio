import { describe, expect, it } from 'vitest';
import { caseKeys } from './case.queries';

describe('caseKeys.list · 键与请求语义对齐', () => {
  it('status 不同时是不同的键', () => {
    expect(caseKeys.list({ status: 'published' })).not.toEqual(caseKeys.list({ status: 'offline' }));
  });

  it('没传 status 时键里是空串（后端默认就是空串）', () => {
    // `status` 已收窄成 `CaseStatus | undefined`（非法值走不到后端），
    // 所以「空串」只可能由缺省产生，不再是调用方能传进来的值
    expect(caseKeys.list({})[5]).toBe('');
    expect(caseKeys.list({ status: 'published' })[5]).toBe('published');
  });

  it('pageSize 缺省时键里的值与后端默认一致（50）', () => {
    expect(caseKeys.list({})).toContain(50);
    expect(caseKeys.list({})).toContain(1); // page 的后端默认
  });

  it('口径不同是不同的键', () => {
    expect(caseKeys.list({ scope: 'library' })).not.toEqual(caseKeys.list({ scope: 'mine' }));
  });
});
