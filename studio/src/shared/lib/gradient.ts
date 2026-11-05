/** 无图/加载失败时的封面兜底渐变（按索引轮换） */
const GRADIENTS = [
  'linear-gradient(150deg,#1b3a5c 0%,#3f6f8f 45%,#8fc4d6 100%)',
  'linear-gradient(150deg,#3a2418 0%,#8a5a33 50%,#d8a86a 100%)',
  'linear-gradient(150deg,#231a3d 0%,#4c3a7a 50%,#9a7fd1 100%)',
  'linear-gradient(150deg,#0f2b2b 0%,#2a6b63 50%,#79c2ad 100%)',
  'linear-gradient(150deg,#2b1a1a 0%,#7a3a34 50%,#cf8a7a 100%)',
  'linear-gradient(150deg,#1a1f2b 0%,#3d4a63 50%,#8fa0bb 100%)',
];

export function coverGradient(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash * 31 + seed.charCodeAt(i)) % 9973;
  }
  return GRADIENTS[hash % GRADIENTS.length];
}
