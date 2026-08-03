import * as React from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '../../utils';

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {}

/**
 * Native `<select>` wearing the same clothes as `Input`.
 *
 * The design system shipped Input, Textarea and Switch but no Select, so every
 * screen that needed one re-typed the border/height classes by hand — at three
 * different heights, none of them matching Input. This is deliberately native
 * rather than a Radix listbox: it keeps mobile behaviour (the OS picker) and
 * needs no portal, and the gap it closes is visual consistency, not features.
 *
 * `appearance-none` plus our own chevron, because the platform arrow ignores the
 * foreground token and stays black on dark backgrounds.
 */
const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div className="relative">
        <select
          className={cn(
            'flex h-9 w-full appearance-none rounded-md border border-input bg-transparent',
            'py-1 pl-3 pr-8 text-sm shadow-sm transition-colors',
            'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
            'disabled:cursor-not-allowed disabled:opacity-50',
            className,
          )}
          ref={ref}
          {...props}
        >
          {children}
        </select>
        <ChevronDown
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
        />
      </div>
    );
  },
);
Select.displayName = 'Select';

export { Select };
