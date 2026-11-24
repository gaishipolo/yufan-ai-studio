import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { RotateCw } from 'lucide-react';
import { driverApi } from '@/entities/model/api/driver.api';
import { Button, Notice, PageHeader } from '@/shared/ui';
import type { RefreshOut } from '@/shared/api/schema';
import { ApiError } from '@/shared/api/types';

/**
 * 运维页（P4）：目前只承载「刷新驱动目录」。
 *
 * 为什么单独一页：刷新是运维级动作（能改进程里装了什么），权限点
 * `model_driver:refresh` 不挂在 `read` 上。页面码 `page:ops` 同样独立 ——
 * 关掉页面时接口也跟着关，不会有人从别处误点。
 *
 * 不在这里做「刷新历史」：后端只有日志、没有可查询的审计记录。摆一个查不出
 * 东西的入口比不摆更糟；等审计接口落地再加。
 */
export default function OpsPage() {
  const [result, setResult] = useState<RefreshOut | null>(null);
  const mutation = useMutation({
    mutationFn: driverApi.refresh,
    onSuccess: (data) => setResult(data),
    onError: () => setResult(null),
  });

  return (
    <>
      <PageHeader
        title="运维"
        meta="模型驱动目录"
        actions={
          <Button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            aria-label="刷新驱动目录"
          >
            <RotateCw size={14} className={mutation.isPending ? 'animate-spin' : ''} aria-hidden />
            {mutation.isPending ? '刷新中…' : '刷新驱动目录'}
          </Button>
        }
      />

      <div className="mt-4 flex flex-col gap-3.5">
        <Notice tone="info" title="刷新是进程级的">
          只影响当前实例；多实例部署时其它实例靠各自的 5 分钟周期刷新收敛。
          新增驱动文件会热接上，但<strong>改已加载的文件不热替换</strong>，要重启才生效。
        </Notice>

        {mutation.isError && (
          <Notice tone="error" title="刷新失败">
            {mutation.error instanceof ApiError
              ? mutation.error.message
              : '刷新请求失败，请查看后端日志'}
          </Notice>
        )}

        {result && (
          <section className="rounded-card border border-line-soft bg-panel p-4" aria-label="刷新结果">
            <h2 className="text-body font-medium text-ink">本次刷新结果</h2>
            <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-meta sm:grid-cols-2">
              <div>
                <dt className="text-ink-soft">新增驱动</dt>
                <dd className="mt-0.5 text-ink">{result.added?.join('、') || '无'}</dd>
              </div>
              <div>
                <dt className="text-ink-soft">保留驱动</dt>
                <dd className="mt-0.5 text-ink">{result.kept?.join('、') || '无'}</dd>
              </div>
              <div>
                <dt className="text-ink-soft">变更待重启</dt>
                <dd className="mt-0.5 text-ink">{result.changedFiles?.join('、') || '无'}</dd>
              </div>
              <div>
                <dt className="text-ink-soft">缺失处理器</dt>
                <dd className="mt-0.5 text-ink">{result.missingHandlers?.join('、') || '无'}</dd>
              </div>
            </dl>
            {result.errors?.length ? (
              <Notice tone="warning" title="刷新时发现配置问题" className="mt-3">
                {result.errors.join('；')}
              </Notice>
            ) : null}
            {result.refreshedAt && (
              <p className="mt-3 text-micro text-ink-mute">刷新时间：{result.refreshedAt}</p>
            )}
          </section>
        )}
      </div>
    </>
  );
}
