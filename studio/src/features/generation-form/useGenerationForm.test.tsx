import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ModelSpec } from '@/entities/model/model/types';
import {
  useGenerationForm,
  type GenerationPayload,
} from './useGenerationForm';

vi.mock('@/entities/library/api/library.queries', () => ({
  useUploadAsset: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

const IMAGE_MODEL: ModelSpec = {
  id: 'img-default',
  label: '默认生图',
  capability: 'image',
  provider: 'fake',
  paramSchema: {},
  maxReferences: 2,
  referenceKinds: ['image'],
  isDefault: true,
};

const OTHER_IMAGE_MODEL: ModelSpec = {
  ...IMAGE_MODEL,
  id: 'img-other',
  label: '另一生图',
  isDefault: false,
};

describe('useGenerationForm', () => {
  it('空提示词走页面校验，不提交', async () => {
    const submit = vi.fn(async () => ({ id: 'ok' }));
    const onValidationError = vi.fn();
    const { result } = renderHook(() =>
      useGenerationForm({
        kind: 'image',
        models: [IMAGE_MODEL],
        submit,
        onSuccess: vi.fn(),
        onValidationError,
      }),
    );

    await act(async () => {
      await result.current.submit();
    });

    expect(submit).not.toHaveBeenCalled();
    expect(onValidationError).toHaveBeenCalledWith('请先填写提示词');
  });

  it('直接生图页不默认模型、不发空参考数组，成功后按需清空提示词', async () => {
    const submit = vi.fn(async (_payload: GenerationPayload) => ({ id: 'ok' }));
    const { result } = renderHook(() =>
      useGenerationForm({
        kind: 'image',
        models: [IMAGE_MODEL],
        omitDefaultModel: true,
        clearPromptOnSuccess: true,
        titleFromPrompt: () => '模板名',
        submit,
        onSuccess: vi.fn(),
      }),
    );

    act(() => result.current.setPrompt('  一只猫  '));
    await act(async () => {
      await result.current.submit();
    });

    const payload = submit.mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(payload.prompt).toBe('一只猫');
    expect(payload.name).toBe('模板名');
    expect(payload).not.toHaveProperty('model');
    expect(payload).not.toHaveProperty('referenceLibraryIds');
    expect(payload).not.toHaveProperty('textLibraryIds');
    expect(result.current.prompt).toBe('');
  });

  it('已挑参考素材时换模型要先确认，取消则模型不变', () => {
    const { result } = renderHook(() =>
      useGenerationForm({
        kind: 'image',
        models: [IMAGE_MODEL, OTHER_IMAGE_MODEL],
        submit: async () => ({ id: 'ok' }),
        onSuccess: vi.fn(),
      }),
    );

    act(() =>
      result.current.addLibraryAsset({
        id: 'asset-1',
        name: '参考图',
        url: '/asset-1.png',
        kind: 'image',
      }),
    );
    act(() => result.current.changeModel('img-other'));

    expect(result.current.pendingModelId).toBe('img-other');
    expect(result.current.currentModel?.id).toBe('img-default');

    act(() => result.current.cancelModelChange());
    expect(result.current.pendingModelId).toBeNull();
    expect(result.current.currentModel?.id).toBe('img-default');
  });
});