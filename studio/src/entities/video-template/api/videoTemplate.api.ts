import { api } from '@/shared/api/http';
import type { VideoTemplateDraftIn, VideoTemplateListOut, VideoTemplateOut, VideoTemplatePromptIn } from '@/shared/api/schema';

// 路径写字面量：契约守卫会把它们与后端 EndpointData 逐条比对。
export const videoTemplateApi = {
  list: () => api.get<VideoTemplateListOut>('/templates/video-templates'),
  get: (id: string) => api.get<VideoTemplateOut>(`/templates/video-templates/${id}`),
  create: (payload: VideoTemplateDraftIn) =>
    api.post<VideoTemplateOut>('/templates/video-templates', payload),
  updatePrompt: (id: string, payload: VideoTemplatePromptIn) =>
    api.patch<VideoTemplateOut>(`/templates/video-templates/${id}/prompt`, payload),
  pin: (id: string) => api.post<VideoTemplateOut>(`/templates/video-templates/${id}/pin`, {}),
};
