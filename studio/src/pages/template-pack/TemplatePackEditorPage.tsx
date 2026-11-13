import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Save, Send } from 'lucide-react';
import { Button, FormField, PageHeader, TextInput } from '@/shared/ui';
import { createId } from '@/shared/lib/id';
import { useLibraryTags } from '@/entities/library/api/library.queries';
import { LibraryPicker } from '@/entities/library/ui/LibraryPicker';
import type { LibraryAssetOut } from '@/shared/api/schema';
import {
  useTemplatePack,
  useTemplatePackMutations,
} from '@/entities/template-pack/api/templatePack.queries';
import {
  initialSlots,
  toSlotIn,
  validateSlots,
  type TemplatePackSlotDraft,
} from '@/entities/template-pack/model/templatePackSlots';
import { TemplatePackSlotEditor } from '@/features/template-pack/TemplatePackSlotEditor';

/**
 * 封装模板编辑页。
 *
 * 流程与用户描述的一致：上传参考视频 → 编辑提示词 → 配槽位 → 发布到模板库。
 *
 * **筛选条件是这个功能的全部价值**：作者在这里声明「这一槽只能选
 * 资产库 v2 的图片-一级标签=产品」，发布后用户在模板库用这条模板时，
 * 就只能从符合筛选的素材里选 —— 约束由服务端在候选池与复核两处兑现。
 */
export default function TemplatePackEditorPage() {
  const navigate = useNavigate();
  const {packId} = useParams();
  const editing = Boolean(packId);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [prompt, setPrompt] = useState('');
  const [sourceVideoAssetId, setSourceVideoAssetId] = useState('');
  const [templateKey, setTemplateKey] = useState('');
  const [slots, setSlots] = useState<TemplatePackSlotDraft[]>(initialSlots);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pickerFor, setPickerFor] = useState<TemplatePackSlotDraft | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState('');

  const existing = useTemplatePack(packId);
  const {save, publish} = useTemplatePackMutations();
  const tagsQuery = useLibraryTags({state: 'active', library: 'personal'});

  /* 打开已有模板时铺初始值。`existing.data` 是稳定的引用（React Query 会缓存），
     所以这里依赖它而不是每次渲染都重置 —— 否则作者改一个字就会被拉回去。 */
  useEffect(() => {
    if (!existing.data) return;
    const pack = existing.data;
    setName(pack.name ?? '');
    setDescription(pack.description ?? '');
    setPrompt(pack.prompt ?? '');
    setSourceVideoAssetId(pack.sourceVideoAssetId ?? '');
    setTemplateKey(pack.templateKey ?? '');
    setSlots(
      (pack.slots ?? []).map(slot => ({
        token: slot.token,
        slotKind: slot.slotKind,
        label: slot.label ?? '',
        assetId: slot.assetId ?? '',
        mediaType: slot.mediaType ?? 'image',
        filterKind: slot.filterKind ?? '',
        filterTagIds: slot.filterTagIds ?? [],
        required: slot.required ?? true,
      })),
    );
  }, [existing.data]);

  const localErrors = useMemo(() => validateSlots(slots, prompt), [slots, prompt]);

  /* 新建时本地生成一个稳定标识：保存之后要一直带着它，
     否则第二次保存会被后端当成新模板（key 变了），草稿列表里就多了一条。
     标识由前端生成不代表它是契约 —— 后端会按「同键同配置返原行、
     同键异配置冲突」处理它。 */
  useEffect(() => {
    if (editing || templateKey) return;
    setTemplateKey(`tp-${createId().replace(/-/g, '').slice(0, 12)}`);
  }, [editing, templateKey]);

  const pickAsset = (slot: TemplatePackSlotDraft) => setPickerFor(slot);

  const confirmPick = (assets: LibraryAssetOut[]) => {
    if (!pickerFor) return;
    const picked = assets[0];
    if (!picked) return;
    if (pickerFor.slotKind === 'fixed') {
      setSlots(previous =>
        previous.map(slot =>
          slot.token === pickerFor.token
            ? {...slot, assetId: picked.id, mediaType: picked.kind}
            : slot,
        ),
      );
      /* 参考视频（第 1 槽）选了之后，模板主表的参考视频字段也要跟上：
         它和第 1 槽是同一件事的两种记法。 */
      if (pickerFor.token === '{{Mixed 1}}') {
        setSourceVideoAssetId(picked.id);
      }
    }
    setPickerFor(null);
  };

  const submit = async (action: 'save' | 'publish') => {
    const nextErrors = validateSlots(slots, prompt);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      setNotice('还有地方没填好');
      return;
    }
    if (!name.trim()) {
      setErrors({_name: '给模板起个名字'});
      return;
    }

    setSubmitting(true);
    setNotice('');
    try {
      /* 编辑已有模板时必须带上 `templateKey`：
         不带的话后端会新建一条，而不是更新这一条 —— 作者看到的是
         「保存成功」，草稿列表里却多了一条。 */
      const saved = await save.mutateAsync({
        name: name.trim(),
        description: description.trim(),
        prompt,
        sourceVideoAssetId,
        slots: slots.map(toSlotIn),
        ...(templateKey ? {templateKey} : {}),
      });
      if (action === 'publish') {
        await publish.mutateAsync(saved.id);
        setNotice('已发布到模板库');
        navigate(`/template-packs/${saved.id}`);
      } else {
        setNotice('已保存');
        if (!editing) navigate(`/template-packs/${saved.id}`);
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : '保存失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <PageHeader
        title={editing ? '编辑封装模板' : '新增封装模板'}
        description="参考视频 + 固定提示词 + 一组槽位。筛选槽位发布后，用户只能从符合条件的素材里选。"
      />

      <div className="mx-auto max-w-4xl space-y-5 px-4 pb-16">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => navigate('/template-packs')}
        >
          <ArrowLeft size={13} />
          返回模板库
        </Button>

        <section className="rounded-xl border border-line bg-white p-4">
          <h2 className="mb-3 text-sm font-medium text-ink">基本信息</h2>
          <div className="space-y-3">
            <FormField label="模板名称" required error={errors._name}>
              <TextInput
                value={name}
                onChange={event => setName(event.target.value)}
                placeholder="如「产品主图模板」"
                maxLength={160}
              />
            </FormField>
            <FormField label="模板说明">
              <TextInput
                value={description}
                onChange={event => setDescription(event.target.value)}
                placeholder="这个模板用来做什么"
                maxLength={2000}
              />
            </FormField>
          </div>
        </section>

        <section className="rounded-xl border border-line bg-white p-4">
          <h2 className="mb-1 text-sm font-medium text-ink">参考视频</h2>
          <p className="mb-3 text-[11px] text-ink-mute">
            模板的原片。它对应第 1 个槽位，用户在模板库里不能替换它。
          </p>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => pickAsset(slots[0])}
            >
              {sourceVideoAssetId ? '更换视频' : '选择视频'}
            </Button>
            <span className="min-w-0 flex-1 truncate text-[11px] text-ink-mute">
              {sourceVideoAssetId || '还没选视频'}
            </span>
          </div>
        </section>

        <section className="rounded-xl border border-line bg-white p-4">
          <h2 className="mb-1 text-sm font-medium text-ink">固定提示词</h2>
          <p className="mb-3 text-[11px] text-ink-mute">
            用 {'{{Mixed N}}'} 引用槽位。编号与下面的槽位一一对应。
          </p>
          <textarea
            value={prompt}
            onChange={event => setPrompt(event.target.value)}
            rows={6}
            placeholder="按 {{Mixed 1}} 的节奏，用 {{Mixed 2}} 做主图"
            className="w-full resize-y rounded-lg border border-line bg-white p-2.5 text-xs leading-relaxed text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10"
          />
          {errors._prompt && (
            <p className="mt-1.5 text-[11px] text-danger">{errors._prompt}</p>
          )}
        </section>

        <section className="rounded-xl border border-line bg-white p-4">
          <h2 className="mb-1 text-sm font-medium text-ink">槽位</h2>
          <p className="mb-3 text-[11px] text-ink-mute">
            每个槽位二选一：钉死一条素材，或声明一条筛选规则让用户在规则内选。
          </p>
          <TemplatePackSlotEditor
            slots={slots}
            errors={localErrors}
            tags={tagsQuery.data ?? []}
            onChange={setSlots}
            onPickAsset={pickAsset}
          />
          {errors._form && (
            <p className="mt-2 text-[11px] text-danger">{errors._form}</p>
          )}
        </section>

        {notice && (
          <p className="text-center text-[11px] text-ink-mute">{notice}</p>
        )}

        <div className="flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            disabled={submitting}
            onClick={() => void submit('save')}
          >
            <Save size={13} />
            保存草稿
          </Button>
          <Button
            type="button"
            disabled={submitting}
            onClick={() => void submit('publish')}
          >
            <Send size={13} />
            发布到模板库
          </Button>
        </div>
      </div>

      <LibraryPicker
        open={Boolean(pickerFor)}
        multiple={false}
        kinds={pickerFor?.slotKind === 'fixed' ? ['video', 'image', 'audio'] : undefined}
        title="从资产库选择素材"
        onClose={() => setPickerFor(null)}
        onConfirm={confirmPick}
      />
    </>
  );
}
