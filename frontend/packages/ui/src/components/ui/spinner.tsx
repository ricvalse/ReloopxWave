import { Loader2 } from 'lucide-react';
import { cn } from '../../utils';

export type SpinnerProps = {
  /** Pixel size of the spinner (width = height). Default 16. */
  size?: number;
  className?: string;
};

/** A spinning loader. Single source of truth so pending states look consistent. */
export function Spinner({ size = 16, className }: SpinnerProps) {
  return (
    <Loader2
      width={size}
      height={size}
      className={cn('animate-spin text-current', className)}
      aria-hidden
    />
  );
}

/** Convenience spinner sized to sit inside a Button left of the label. */
export function ButtonSpinner({ className }: { className?: string }) {
  return <Spinner size={16} className={cn('-ml-0.5', className)} />;
}
