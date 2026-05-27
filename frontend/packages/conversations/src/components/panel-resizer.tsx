'use client';

import { cn } from '@reloop/ui';

interface PanelResizerProps {
  onMouseDown: (e: React.MouseEvent) => void;
  active: boolean;
  'aria-label': string;
}

/**
 * A 1px draggable divider between two inbox panels. Desktop-only (the caller
 * hides it below `md`). The hit area is wider than the visible rule so it's
 * easy to grab; the rule brightens on hover and while dragging.
 */
export function PanelResizer({ onMouseDown, active, 'aria-label': ariaLabel }: PanelResizerProps) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      onMouseDown={onMouseDown}
      className={cn(
        'group relative hidden w-px shrink-0 cursor-col-resize md:block',
        'bg-border',
      )}
    >
      {/* Wide invisible hit area centred on the rule. */}
      <span className="absolute inset-y-0 -left-1.5 -right-1.5 z-10" />
      {/* Visible accent on hover / during drag. */}
      <span
        className={cn(
          'absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 transition-colors',
          active ? 'bg-primary/40' : 'bg-transparent group-hover:bg-primary/25',
        )}
      />
    </div>
  );
}
