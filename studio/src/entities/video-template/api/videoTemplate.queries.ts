import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { videoTemplateApi } from './videoTemplate.api';

export const videoTemplateKeys = {
  all: ['video-templates'] as const,
};

export function useVideoTemplates() {
  return useQuery({queryKey: videoTemplateKeys.all, queryFn: videoTemplateApi.list});
}

export function useVideoTemplateMutations() {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({queryKey: videoTemplateKeys.all});
  return {
    create: useMutation({mutationFn: videoTemplateApi.create, onSettled: refresh}),
    pin: useMutation({mutationFn: videoTemplateApi.pin, onSettled: refresh}),
  };
}
