import { useState } from 'react';
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
