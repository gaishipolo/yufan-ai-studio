import { describe, expect, it } from 'vitest';
import {
  MAX_BYTES,
  TEXT_MAX_BYTES,
  UPLOAD_ACCEPT,
  textByteLength,
  uploadKindOf,
} from './uploadLimits';

/**
 * 上传白名单是**后端闸门的镜像**：后端 `api/src/infra/media_probe.py` 的
 * `EXTENSIONS` 一改，这里就必须跟着改，否则前端又会把服务端拒的文件放进来。
 */
describe('uploadLimits · 后端白名单同源', () => {
  it('只认后端白名单里的扩展名，.gif/.svg 一律不放行', () => {
    expect(uploadKindOf('a.png')).toBe('image');
    expect(uploadKindOf('a.JPG')).toBe('image');
    expect(uploadKindOf('a.jpeg')).toBe('image');
    expect(uploadKindOf('a.webp')).toBe('image');
    expect(uploadKindOf('a.mp4')).toBe('video');
    expect(uploadKindOf('a.MP4')).toBe('video');
    expect(uploadKindOf('a.mp3')).toBe('audio');
    expect(uploadKindOf('a.wav')).toBe('audio');
    expect(uploadKindOf('a.txt')).toBe('text');
    expect(uploadKindOf('a.md')).toBe('text');
    // 后端不收的：gif / svg（会被当标记语言执行）、mov（容器不在白名单）
    expect(uploadKindOf('a.gif')).toBeNull();
    expect(uploadKindOf('a.svg')).toBeNull();
    expect(uploadKindOf('a.mov')).toBeNull();
    expect(uploadKindOf('没有扩展名')).toBeNull();
  });

  it('accept 与白名单同源，不再用 image/* 这类会放行 .gif 的宽泛写法', () => {
    expect(UPLOAD_ACCEPT).toBe('.png,.jpg,.jpeg,.webp,.mp4,.mp3,.wav,.txt,.md');
  });

  it('体积上限与后端一致：文本 1 MB、其它 300 MB', () => {
    expect(TEXT_MAX_BYTES).toBe(1024 * 1024);
    expect(MAX_BYTES).toBe(300 * 1024 * 1024);
  });

  it('文本字节数按 UTF-8 算，中文一个字 3 字节', () => {
    expect(textByteLength('abc')).toBe(3);
    expect(textByteLength('中')).toBe(3);
  });
});
