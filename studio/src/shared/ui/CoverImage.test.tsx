import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CoverImage } from './CoverImage';

describe('CoverImage', () => {
  it('封面立即加载，不用 lazy', () => {
    /* Edge 会把视口外的 lazy 图片换成占位符并延后 load 事件；模板库一屏十几张
       封面时，用户看到的就是「模板没显示演示案例」。这条守卫钉住这个回归。 */
    render(<CoverImage src="http://example.test/cover.jpg" seed="s1" alt="封面" />);
    expect(screen.getByAltText('封面').getAttribute('loading')).not.toBe('lazy');
  });
});
