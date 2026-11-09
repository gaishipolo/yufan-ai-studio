import { useState } from 'react';
import type { SubjectMemberOut } from '@/shared/api/schema';
import { toast } from '@/shared/lib/toast';
import { Button, Chip, CoverImage, Modal } from '@/shared/ui';
import { usePublishSubject, useSubject } from '@/entities/asset/api/subject.api';
import { availabilityOf, grantLabel, memberSummary } from '@/entities/asset/model/subject';

/**
 * 主体详情：**成员条带** + 发布闸门。
 *
 * 条带里每条都要能回答"这张图给了什么、缺什么"：只显示封面的话，用户看到
 * "缺少 download"还是得自己点十张图去猜是哪一张。所以每条带 `grants` 标签，
 * 并在 `memberGaps` 里点名的位置上直接写"缺 xx"。
 *
 * 发布按钮**不禁用** `broken` 以外的情况：能力不齐时点下去由服务端给出确切原因
 * （400 + "缺少 download"），比前端灰掉按钮、用户不知道该改哪里要好。缺图时
 * 按钮禁用并写明"先去恢复"，因为那一步不在这个浮层里能做。
 */
export function SubjectDetailPanel({
  subjectId,
  onClose,
  canPublish,
}: {
  subjectId: string;
  onClose: () => void;
  canPublish: boolean;
}) {
  const {data, isLoading, error} = useSubject(subjectId);
  const publish = usePublishSubject();
  const [busy, setBusy] = useState(false);

  const state = availabilityOf(data?.availability);
  const broken = data?.availability === 'broken';

  const doPublish = async () => {
    if (!data) return;
    setBusy(true);
    try {
      const result = await publish.mutateAsync(data.id);
      toast(`已发布 v${result.version}：模板侧会按新版本取图`);
    } catch (caught) {
      // 服务端会明确说"缺哪个能力 / 哪张图被删"，原样透出——自己编一句
      // "发布失败"会让用户只能反复试
      toast(caught instanceof Error ? caught.message : '发布失败', 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} title={data?.name || '主体详情'} width={860}>
      {isLoading && <div className="py-10 text-center text-sm text-ink-mute">正在读取主体…</div>}
      {error && (
        <div className="py-10 text-center text-sm text-danger-deep">
          {error instanceof Error ? error.message : '主体读取失败'}
        </div>
      )}

      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone={state.tone}>{state.label}</Chip>
            <span className="text-xs text-ink-mute">
              {memberSummary(data.memberCount, data.currentVersion)}
            </span>
            <span className="text-xs text-ink-mute">
              {data.status === 'published' ? `已发布 v${data.currentVersion}` : '尚未发布'}
            </span>
            <span className="ml-auto flex items-center gap-2">
              <Button
                size="sm"
                variant="primary"
                onClick={() => void doPublish()}
                disabled={busy || broken || !canPublish}
                title={broken ? '有成员图已被删除，先去回收站恢复' : undefined}
              >
                {busy ? '发布中…' : '发布'}
              </Button>
            </span>
          </div>

          {!!state.hint && (
            <p className="text-xs text-ink-soft">
              {state.hint}
              {!!data.availabilityNote && `：${data.availabilityNote}`}
            </p>
          )}

          {/* 缺项清单：主体级结论。逐成员的缺口在下面条带里点出来 */}
          {(!!data.missingCapabilities?.length || !!data.missingRoles?.length) && (
            <div className="flex flex-wrap gap-1.5 rounded-md border border-line-soft bg-panel-soft px-2.5 py-2">
              {(data.missingRoles ?? []).map(role => (
                <Chip key={`role-${role}`} tone="warn">
                  缺角色 · {role}
                </Chip>
              ))}
              {(data.missingCapabilities ?? []).map(cap => (
                <Chip key={`cap-${cap}`} tone="warn">
                  缺能力 · {grantLabel(cap)}
                </Chip>
              ))}
            </div>
          )}

          <div>
            <div className="mb-1.5 text-xs font-medium text-ink-soft">
              成员条带（顺序即提示词编号顺序）
            </div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {(data.members ?? []).map(member => (
                <MemberTile
                  key={member.assetId}
                  member={member}
                  gaps={data.memberGaps?.[member.assetId] ?? []}
                />
              ))}
              {!data.members?.length && (
                <div className="col-span-full rounded-md border border-dashed border-line-soft px-3 py-6 text-center text-xs text-ink-mute">
                  还没有成员。组装台在 M3.5 上线前，主体只能由系统按旧素材包生成。
                </div>
              )}
            </div>
          </div>

          {!!Object.keys(data.attributes ?? {}).length && (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(data.attributes ?? {}).map(([key, value]) => (
                <Chip key={key}>
                  {key}：{String(value)}
                </Chip>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function MemberTile({ member, gaps }: { member: SubjectMemberOut; gaps: string[] }) {
  return (
    <div className="rounded-md border border-line-soft p-1.5">
      <div className="relative">
        <CoverImage
          src={member.coverUrl}
          seed={member.assetId}
          alt={member.name}
          className="h-20 w-full rounded"
        />
        {/* 缺图/不参与输出两个标记必须在**缩略图上**，不能只在文字里：
            用户扫视条带时先看图，看不到图才会去读那行小字 */}
        {member.missing && (
          <span className="absolute inset-0 flex items-center justify-center rounded bg-black/55 text-[11px] font-medium text-white">
            已删除
          </span>
        )}
        {!member.inOutput && (
          <span className="absolute left-1 top-1 rounded bg-black/55 px-1 text-[10px] text-white">
            仅参考
          </span>
        )}
      </div>
      <div className="mt-1 truncate text-[11px] text-ink" title={member.name || member.assetId}>
        {member.name || member.assetId}
      </div>
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-[10px] text-ink-mute">{member.role || '未设角色'}</span>
        <span className="text-[10px] text-ink-mute">#{member.sortOrder ?? 0}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {(member.grants ?? []).map(grant => (
          <Chip key={grant} tone={gaps.includes(grant) ? 'warn' : 'default'}>
            {grantLabel(grant)}
          </Chip>
        ))}
        {gaps.map(grant => (
          <Chip key={`gap-${grant}`} tone="danger">
            缺 · {grantLabel(grant)}
          </Chip>
        ))}
      </div>
    </div>
  );
}
