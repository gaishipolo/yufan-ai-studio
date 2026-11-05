import { useEffect, useMemo, useState } from 'react';
import { Sparkles } from 'lucide-react';
import { Button, Chip, Modal, Select } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useCreateTask } from '@/entities/task/api/task.queries';
import { aspectOptionsFor, durationOptionsFor, useModels } from '@/entities/model/api/model.queries';
import type { AspectRatio, VideoTask } from '@/entities/task/model/types';

export interface CreateTaskModalProps {
  open: boolean;
  onClose: () => void;
  onCreated?: (task: VideoTask) => void;
}

export function CreateTaskModal({ open, onClose, onCreated }: CreateTaskModalProps) {
  const create = useCreateTask();
  // 模型与时长选项全部来自后端 /models：新增模型不需要改这里
  const models = useModels('video');
  const [prompt, setPrompt] = useState('');
  const [modelId, setModelId] = useState('');
  const [duration, setDuration] = useState('');
  const [ratio, setRatio] = useState<AspectRatio>('9:16');
  const [error, setError] = useState('');

  const currentModel = useMemo(
    () => models.find((item) => item.id === modelId) ?? models.find((m) => m.isDefault) ?? models[0],
    [models, modelId],
  );

  const modelOptions = useMemo(
    () => models.map((item) => ({ value: item.id, label: item.label })),
    [models],
  );

  const durationValues = useMemo(() => durationOptionsFor(currentModel), [currentModel]);
  const durationOptions = useMemo(
    () => durationValues.map((value) => ({ value: String(value), label: `${value} 秒` })),
    [durationValues],
  );
  const ratioOptions = useMemo(() => aspectOptionsFor(currentModel), [currentModel]);

  // 模型变化时把时长收敛到该模型支持的范围内
  useEffect(() => {
    if (!durationValues.length) return;
    setDuration((prev) => {
      const current = Number(prev);
      if (prev && durationValues.includes(current)) return prev;
      return String(durationValues[durationValues.length - 1]);
    });
  }, [durationValues]);

  // 比例同理：模型不支持的比例不能留在表单里
  useEffect(() => {
    if (!ratioOptions.length) return;
    setRatio((prev) =>
      ratioOptions.some((item) => item.value === prev) ? prev : ratioOptions[0].value,
    );
  }, [ratioOptions]);

  useEffect(() => {
    if (!open) {
      setPrompt('');
      setError('');
    }
  }, [open]);

  const submit = () => {
    if (!prompt.trim()) {
      setError('请先填写画面描述');
      return;
    }
    setError('');
    create.mutate(
      {
        prompt: prompt.trim(),
        model: currentModel?.id ?? '',
        durationSec: Number(duration),
        aspectRatio: ratio,
      },
      {
        onSuccess: (task) => {
          toast('任务已创建，正在排队生成', 'success');
          onCreated?.(task);
          onClose();
        },
        onError: (e) => toast(e.message, 'error'),
      },
    );
  };

  return (
    <Modal open={open} onClose={onClose} title="创建视频任务" width={560}>
      <div className="space-y-3 p-[18px]">
        <div>
          <div className="mb-1.5 flex items-center gap-1 text-[11.5px] text-ink-soft">
            画面描述 <span className="text-danger">*</span>
          </div>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
            placeholder="例：超市抢购场景，手持镜头跟拍，人群涌动，暖色顶光，电影感浅景深"
            className={cn(
              'w-full resize-y rounded-lg border bg-panel-soft px-2.5 py-2 text-[12.5px] leading-relaxed text-ink outline-none transition',
              'placeholder:text-[#a9b8bf] focus:border-primary-line focus:bg-white focus:shadow-ring',
              error ? 'border-danger' : 'border-line',
            )}
          />
          {error && <p className="mt-1 text-[11px] text-danger">{error}</p>}
        </div>

        <div className="grid grid-cols-2 gap-2.5">
          <div>
            <div className="mb-1.5 text-[11.5px] text-ink-soft">生成模型</div>
            <Select
              block
              options={modelOptions}
              value={currentModel?.id ?? ''}
              onChange={(e) => setModelId(e.target.value)}
            />
          </div>
          <div>
            <div className="mb-1.5 text-[11.5px] text-ink-soft">时长</div>
            <Select
              block
              options={durationOptions}
              value={duration}
              onChange={(e) => setDuration(e.target.value)}
            />
          </div>
        </div>

        {currentModel?.description && (
          <p className="text-[11px] leading-relaxed text-ink-mute">{currentModel.description}</p>
        )}

        <div>
          <div className="mb-1.5 text-[11.5px] text-ink-soft">画面比例</div>
          <div className="flex flex-wrap gap-1.5">
            {ratioOptions.map((r) => (
              <button
                key={r.value}
                type="button"
                onClick={() => setRatio(r.value)}
                className={cn(
                  'rounded-pill border px-2.5 py-1 text-[11.5px] transition-colors',
                  ratio === r.value
                    ? 'border-[#bcd6ff] bg-primary-soft font-semibold text-[#2b6bd4]'
                    : 'border-line bg-panel text-ink-soft hover:border-[#b9d3ff] hover:text-primary',
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 pt-1">
          <Chip tone="primary">异步排队 · 生成后自动入库</Chip>
          <div className="flex gap-2">
            <Button size="md" onClick={onClose}>
              取消
            </Button>
            <Button
              size="md"
              variant="primary"
              icon={<Sparkles size={13} />}
              loading={create.isPending}
              onClick={submit}
            >
              提交生成
            </Button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
