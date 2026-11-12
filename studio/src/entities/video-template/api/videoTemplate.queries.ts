import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { videoTemplateApi } from './videoTemplate.api';
import type { VideoTemplateListOut, VideoTemplatePackReferenceIn } from '@/shared/api/schema';

export const videoTemplateKeys = {
  all: ['video-templates'] as const,
};

export function useVideoTemplates() {
  return useQuery({queryKey: videoTemplateKeys.all, queryFn: videoTemplateApi.list});
}

export function useUpdateVideoTemplatePrompt() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({id, prompt, references}: {id: string; prompt: string; references?: VideoTemplatePackReferenceIn[]}) =>
      videoTemplateApi.updatePrompt(id, {prompt, ...(references !== undefined ? {references} : {})}),
    onSuccess: updated => {
      client.setQueryData<VideoTemplateListOut>(videoTemplateKeys.all, previous => previous && ({
        ...previous, items: previous.items?.map(item => item.id === updated.id ? updated : item),
      }));
      void client.invalidateQueries({queryKey: videoTemplateKeys.all});
    },
  });
}

export function useVideoTemplateMutations() {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({queryKey: videoTemplateKeys.all});
  return {
    create: useMutation({mutationFn: videoTemplateApi.create, onSettled: refresh}),
    pin: useMutation({mutationFn: videoTemplateApi.pin, onSettled: refresh}),
  };
}
