/** 模板（后端 /templates 资源）：可复用的分镜结构 */
export interface VideoTemplate {
  id: string;
  name: string;
  description: string;
  industry: string;
  shotCount: number;
  usageCount: number;
  coverUrl: string;
  tags: string[];
  createdAt: string;
}
