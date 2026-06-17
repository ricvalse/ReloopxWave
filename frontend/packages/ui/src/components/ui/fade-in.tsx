import type { HTMLAttributes } from 'react';
import { cn } from '../../utils';

/**
 * Lightweight content reveal. CSS-only (the `fade-in` keyframe is defined in
 * `tailwind.preset.ts`) — deliberately NOT framer-motion: a 150ms fade doesn't
 * justify a client-component boundary or the bundle weight. Wrap the resolved
 * branch of a query so content eases in once it replaces a skeleton.
 */
export function FadeIn({ className, children, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn('animate-fade-in', className)} {...props}>
      {children}
    </div>
  );
}
