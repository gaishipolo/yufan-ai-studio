/** 镜头词条（后端 /shots 资源）：运镜指令片段 */
export interface ShotPreset {
  id: string;
  name: string;
  promptFragment: string;
  durationSec: number;
  coverUrl: string;
  popularity: number;
}
