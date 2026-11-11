import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type { ReplacementCatalog, ReplacementReviewIn } from '@/shared/api/schema';
import { isStateChanged } from '@/shared/api/types';
import { taskKeys } from '@/entities/task/api/task.queries';
import type { ReplacementRun } from '../model/replacement.types';
import { replacementApi } from './replacement.api';
export const replacementKeys = {catalog: (owner: string) => ['replacement-catalog', owner] as const};
const ACTIVE = new Set(['queued', 'uploading', 'uploaded', 'submitting', 'submitted', 'running', 'generated', 'waiting_executor']);
const POLL_INTERVAL = 5000;

export function useReplacementCatalog(options?: {enabled?: boolean}) {
  const owner = useSessionStore(state => state.subject.id);
  return useQuery({queryKey: replacementKeys.catalog(owner), queryFn: replacementApi.catalog,
    enabled: options?.enabled ?? true,
    refetchInterval: query => query.state.data?.runs.some(run => ACTIVE.has(run.status)) ? POLL_INTERVAL : false});
}
function useRunMutation() {
  const client = useQueryClient();
  const owner = useSessionStore(state => state.subject.id);
  const refresh = () => { void client.invalidateQueries({queryKey: replacementKeys.catalog(owner)}); };
  const onSuccess = async (run: ReplacementRun) => {
    client.setQueryData<ReplacementCatalog>(replacementKeys.catalog(owner), previous => previous ?
      {...previous, runs: [run, ...previous.runs.filter(item => item.id !== run.id)]} : previous);
    await client.invalidateQueries({queryKey: replacementKeys.catalog(owner)});
    await client.invalidateQueries({queryKey: taskKeys.feeds()});
  };
  /* 冲突（`409 state_changed`）后只刷新目录，不自动重放请求：写动作里就有
     「提交生成」，静默重试等于替用户再花一次钱。判定见 shared/api/types。 */
  const onError = (error: unknown) => { if (isStateChanged(error)) refresh(); };
  return {onSuccess, onError};
}
export function usePrepareReplacement() {
  const {onSuccess, onError} = useRunMutation();
  return useMutation({mutationFn: replacementApi.prepare, onSuccess, onError});
}
export function useExecuteReplacement() {
  const {onSuccess, onError} = useRunMutation();
  return useMutation({
    mutationFn: ({id, billingMode}: {id: string; billingMode?: 'free' | 'credits'}) =>
      replacementApi.execute(id, billingMode),
    onSuccess,
    onError,
  });
}
export function useManageReplacement() {
  const {onSuccess, onError} = useRunMutation();
  return useMutation({
    mutationFn: ({id, restore}: {id: string; restore: boolean}) => restore ? replacementApi.restore(id) : replacementApi.remove(id),
    onSuccess,
    onError,
  });
}
export function useReviewReplacement() {
  const {onSuccess} = useRunMutation();
  const client = useQueryClient();
  const owner = useSessionStore(state => state.subject.id);
  return useMutation({
    mutationFn: ({id, payload}: {id: string; payload: ReplacementReviewIn}) => replacementApi.review(id, payload),
    onSuccess,
    // 保存失败或冲突时重新读取权威状态，不把未保存的草稿当作服务端结论。
    onError: () => {void client.invalidateQueries({queryKey: replacementKeys.catalog(owner)});},
  });
}
