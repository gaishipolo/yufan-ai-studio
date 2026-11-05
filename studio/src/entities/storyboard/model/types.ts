import type { AspectRatio } from '@/entities/task/model/types';

/** 分镜模板（后端 /storyboards 资源） */
export interface Storyboard {
  id: string;
  name: string;
  description: string;
  shotCount: number;
  durationSec: number;
  aspectRatio: AspectRatio;
  coverUrl: string;
  createdAt: string;
}
