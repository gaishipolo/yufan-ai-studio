/** 生图任务记录（后端 /images 资源） */
export interface ImageJob {
  id: string;
  name: string;
  prompt: string;
  size: string;
  coverUrl: string;
  createdAt: string;
}

export interface GenerateImagePayload {
  prompt: string;
  size?: string;
  count?: number;
}
