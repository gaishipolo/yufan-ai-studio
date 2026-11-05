import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type { ReplacementCatalog } from '@/shared/api/schema';
import type { ReplacementRun } from '../model/replacement.types';
import { replacementApi } from './replacement.api';
export const replacementKeys = {catalog: (owner: string) => ['replacement-catalog', owner] as const};
const ACTIVE = new Set(['queued', 'uploading', 'uploaded', 'submitting', 'submitted', 'running', 'generated', 'waiting_executor']);
const POLL_INTERVAL = 5000;
export function useReplacementCatalog() {
  const owner = useSessionStore(state => state.subject.id);
  return useQuery({queryKey: replacementKeys.catalog(owner), queryFn: replacementApi.catalog,
    refetchInterval: query => query.state.data?.runs.some(run => ACTIVE.has(run.status)) ? POLL_INTERVAL : false});
}
function useRunMutation() {
  const client = useQueryClient();
  const owner = useSessionStore(state => state.subject.id);
  const onSuccess = async (run: ReplacementRun) => {
    client.setQueryData<ReplacementCatalog>(replacementKeys.catalog(owner), previous => previous ?
      {...previous, runs: [run, ...previous.runs.filter(item => item.id !== run.id)]} : previous);
    await client.invalidateQueries({queryKey: replacementKeys.catalog(owner)});
  };
  return {onSuccess};
}
export function usePrepareReplacement() {
  const {onSuccess} = useRunMutation();
  return useMutation({mutationFn: replacementApi.prepare, onSuccess});
}
export function useExecuteReplacement() {
  const {onSuccess} = useRunMutation();
  return useMutation({mutationFn: replacementApi.execute, onSuccess});
}
