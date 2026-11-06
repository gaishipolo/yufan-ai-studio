import { useEffect, useState } from 'react';
import { cn } from '@/shared/lib/cn';
import { coverGradient } from '@/shared/lib/gradient';

export interface CoverImageProps {
  src?: string;
  /** 兜底渐变的随机种子，一般传 id */
  seed: string;
  alt?: string;
  className?: string;
  imgClassName?: string;
}

/** 统一封面：加载失败自动回落为渐变占位，避免破图影响布局 */
export function CoverImage({ src, seed, alt = '', className, imgClassName }: CoverImageProps) {
  const [failed, setFailed] = useState(false);

  /* 换图必须重置失败态：素材卡的封面先是远端地址、随后换成鉴权取回的 Blob，
     如果不重置，第一次失败就永久停在渐变占位上，明明拿到了图也不显示。 */
  useEffect(() => setFailed(false), [src]);

  return (
    <div
      className={cn('relative overflow-hidden bg-line-soft', className)}
      style={{ backgroundImage: coverGradient(seed) }}
    >
      {src && !failed && (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          onError={() => setFailed(true)}
          className={cn('absolute inset-0 h-full w-full object-cover', imgClassName)}
        />
      )}
    </div>
  );
}
