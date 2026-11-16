import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  STICKY_FIT_MARGIN,
  fitHeightToViewport,
  useStickyFitHeight,
} from './useStickyFitHeight';

function rectAt(top: number): DOMRect {
  return {
    top,
    bottom: top + 760,
    height: 760,
    left: 0,
    right: 0,
    width: 0,
    x: 0,
    y: top,
    toJSON: () => ({}),
  } as DOMRect;
}

/** 把视口高度钉成 860 —— 模板库右栏出问题时的真实视口高度 */
function stubViewport(height = 860) {
  Object.defineProperty(window, 'innerHeight', {
    value: height,
    configurable: true,
    writable: true,
  });
}

describe('fitHeightToViewport', () => {
  it('按自然位置算上限，而不是按吸顶位置', () => {
    /* 模板库右栏的真实数字：视口 860，自然位置 110，吸顶后 71。
       只按吸顶位置算（860 - 71 - 12 = 777），自然位置上底边就落在 887，
       又是被视口切掉 —— 这正是这个函数存在的理由。 */
    expect(fitHeightToViewport(860, 110)).toBe(738);
    expect(fitHeightToViewport(860, 71)).toBe(777);
  });

  it('自然位置 + 上限 + 余量 = 视口高度，一像素都不许溢出', () => {
    const viewport = 860;
    const naturalTop = 110;
    const cap = fitHeightToViewport(viewport, naturalTop);
    expect(naturalTop + cap + STICKY_FIT_MARGIN).toBe(viewport);
  });

  it('视口变矮时上限跟着变矮', () => {
    expect(fitHeightToViewport(600, 110)).toBe(478);
  });

  it('面板整体在视口之外时上限归零，不给负数', () => {
    expect(fitHeightToViewport(860, 900)).toBe(0);
  });
});

describe('useStickyFitHeight', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('面板挂上来就量出自然位置，给出高度上限', () => {
    stubViewport(860);
    const element = document.createElement('div');
    document.body.appendChild(element);
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue(rectAt(110));
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      position: 'sticky',
    } as CSSStyleDeclaration);

    const {result} = renderHook(() => useStickyFitHeight());
    expect(result.current.maxHeight).toBeUndefined();

    act(() => result.current.ref(element));
    expect(result.current.maxHeight).toBe(738);
  });

  it('普通流里的面板不给上限：页面自己会滚', () => {
    stubViewport(860);
    const element = document.createElement('div');
    document.body.appendChild(element);
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue(rectAt(110));
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      position: 'static',
    } as CSSStyleDeclaration);

    const {result} = renderHook(() => useStickyFitHeight());
    act(() => result.current.ref(element));
    expect(result.current.maxHeight).toBeUndefined();
  });

  it('量完把 sticky 装回去：不能把面板改成普通流', () => {
    stubViewport(860);
    const element = document.createElement('div');
    element.style.position = 'sticky';
    document.body.appendChild(element);
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue(rectAt(110));
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      position: 'sticky',
    } as CSSStyleDeclaration);

    const {result} = renderHook(() => useStickyFitHeight());
    act(() => result.current.ref(element));
    expect(element.style.position).toBe('sticky');
  });
});
