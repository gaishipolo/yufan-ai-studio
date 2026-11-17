import {describe, expect, it} from 'vitest';
import {buildGenerateConfirm} from './generateConfirm';

const BASE = {
  templateName: '商品替换',
  version: 3,
  modelLabel: 'LibTV · 720P 视频替换',
  slots: [{label: '商品图', count: 2}],
  params: [{label: '时长', text: '5 秒'}],
};

describe('生成二次确认的内容', () => {
  it('把模板与版本、模型列成事实行', () => {
    const confirm = buildGenerateConfirm(BASE);
    expect(confirm.rows.map(row => row.label)).toEqual(['模板', '模型', '素材', '参数']);
    expect(confirm.rows[0].value).toBe('商品替换 v3');
    expect(confirm.rows[1].value).toBe('LibTV · 720P 视频替换');
  });

  it('素材数是各槽位之和，不是槽位数', () => {
    const confirm = buildGenerateConfirm({
      ...BASE,
      slots: [
        {label: '商品图', count: 2},
        {label: '原片', count: 1},
      ],
    });
    expect(confirm.rows.find(row => row.label === '素材')?.value).toBe('3 项');
  });

  it('没有槽位时不列素材那一行', () => {
    const confirm = buildGenerateConfirm({...BASE, slots: []});
    expect(confirm.rows.map(row => row.label)).not.toContain('素材');
  });

  it('参数行把所有生效参数连在一起', () => {
    const confirm = buildGenerateConfirm({
      ...BASE,
      params: [
        {label: '时长', text: '5 秒'},
        {label: '画幅', text: '9:16 竖版'},
      ],
    });
    expect(confirm.rows.find(row => row.label === '参数')?.value).toBe('时长 5 秒 · 画幅 9:16 竖版');
  });

  it('计费模式单独一行，不混在参数里', () => {
    const confirm = buildGenerateConfirm({...BASE, billingMode: 'free'});
    const row = confirm.rows.find(item => item.label === '计费');
    expect(row?.value).toBe('免费队列');
  });

  /* 免费队列不扣钱，没必要每次都提醒一句 —— 每次都提醒只会让人养成
     盲点「确认」的习惯，真要花钱的那次反而被忽略。 */
  it('免费队列不给提醒', () => {
    const confirm = buildGenerateConfirm({...BASE, billingMode: 'free'});
    expect(confirm.notice).toBeUndefined();
  });

  it('积分加速给出「会消耗积分」的提醒', () => {
    const confirm = buildGenerateConfirm({...BASE, billingMode: 'credits'});
    expect(confirm.notice).toBe('这次提交走积分加速队列，会消耗账号积分。');
    expect(confirm.noticeTone).toBe('warn');
  });

  it('模型没声明计费模式时不列计费行、也没有提醒', () => {
    const confirm = buildGenerateConfirm(BASE);
    expect(confirm.rows.map(row => row.label)).not.toContain('计费');
    expect(confirm.notice).toBeUndefined();
  });
});
