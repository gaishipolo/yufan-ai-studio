import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Check, Pencil } from 'lucide-react';
import { Button, PageHeader } from '@/shared/ui';
import { useTemplatePack } from '@/entities/template-pack/api/templatePack.queries';
import { templatePackApi } from '@/entities/template-pack/api/templatePack.api';
import { TemplatePackRuntimePicker } from '@/features/template-pack/TemplatePackRuntimePicker';

/**
 * 封装模板详情：已发布的模板在这里「用」。
 *
 * **筛选约束在这一页兑现**：用户看到的是服务端算出来的合规素材池，
 * 提交前还有一次服务端复核 —— 前端过滤只是 UI 便利，接口可以被直接调用。
 */
export default function TemplatePackDetailPage() {
  const navigate = useNavigate();
  const {packId} = useParams();
  const packQuery = useTemplatePack(packId);
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [verifying, setVerifying] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState('');

  const pack = packQuery.data;

  const filteredSlots = useMemo(
    () => (pack?.slots ?? []).filter(slot => slot.slotKind === 'filtered'),
    [pack?.slots],
  );

  const missingRequired = useMemo(
    () =>
      filteredSlots.filter(
        slot => (slot.required ?? true) && !picks[slot.token],
      ),
    [filteredSlots, picks],
  );

  if (packQuery.isLoading) {
    return (
      <>
        <PageHeader title="封装模板" />
        <p className="px-4 py-12 text-center text-xs text-ink-mute">加载中…</p>
      </>
    );
  }

  if (!pack) {
    return (
      <>
        <PageHeader title="封装模板" />
        <p className="px-4 py-12 text-center text-xs text-ink-mute">
          找不到这条模板，或者它还没发布。
        </p>
      </>
    );
  }

  const isDraft = pack.status === 'draft';

  const runVerify = async () => {
    setVerifying(true);
    setError('');
    setVerified(false);
    try {
      const result = await templatePackApi.verify(pack.id, {picks});
      setPicks(result.picks ?? {});
      setVerified(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '复核失败');
    } finally {
      setVerifying(false);
    }
  };

  return (
    <>
      <PageHeader
        title={pack.name}
        description={pack.description || '带筛选约束的封装模板'}
      />

      <div className="mx-auto max-w-4xl space-y-5 px-4 pb-16">
        <div className="flex items-center justify-between">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => navigate('/template-packs')}
          >
            <ArrowLeft size={13} />
            返回模板库
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => navigate(`/template-packs/${pack.id}/edit`)}
          >
            <Pencil size={13} />
            编辑
          </Button>
        </div>

        <section className="rounded-xl border border-line bg-white p-4">
          <h2 className="mb-2 text-sm font-medium text-ink">固定提示词</h2>
          <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-relaxed text-ink-soft">
            {pack.prompt}
          </pre>
        </section>

        {isDraft ? (
          <p className="rounded-lg bg-panel-soft p-3 text-[11.5px] text-ink-mute">
            这条模板还在草稿状态，只有你能看到。发布之后才会进模板库，别人才能用。
          </p>
        ) : (
          <section className="space-y-3">
            <div>
              <h2 className="text-sm font-medium text-ink">选择素材</h2>
              <p className="mt-0.5 text-[11px] text-ink-mute">
                下面的素材是服务端按模板声明的筛选条件算出来的，只有这些能选。
              </p>
            </div>

            <TemplatePackRuntimePicker pack={pack} picks={picks} onChange={setPicks} />

            {error && (
              <p role="alert" className="text-[11.5px] text-danger">
                {error}
              </p>
            )}
            {verified && (
              <p className="flex items-center gap-1 text-[11.5px] text-primary">
                <Check size={12} />
                选材已通过服务端复核
              </p>
            )}

            <div className="flex items-center justify-end gap-2">
              {missingRequired.length > 0 && (
                <span className="text-[11px] text-ink-mute">
                  还有 {missingRequired.length} 个必选槽位没选
                </span>
              )}
              <Button
                type="button"
                disabled={verifying || missingRequired.length > 0}
                onClick={() => void runVerify()}
              >
                {verifying ? '复核中…' : '复核选材'}
              </Button>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
