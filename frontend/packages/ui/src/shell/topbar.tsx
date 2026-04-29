'use client';

import { Menu, Search } from 'lucide-react';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { Button } from '../primitives/button';
import { Separator } from '../components/ui/separator';
import { useSidebar } from './sidebar-context';
import { ThemeToggle } from './theme-toggle';
import { cn } from '../utils';

function formatSegment(segment: string) {
  return segment.charAt(0).toUpperCase() + segment.slice(1).replace(/-/g, ' ');
}

interface TopbarProps {
  brand: string;
  userMenu?: ReactNode;
  onCommandPaletteOpen: () => void;
  onMobileMenuOpen: () => void;
}

export function Topbar({ brand, userMenu, onCommandPaletteOpen, onMobileMenuOpen }: TopbarProps) {
  const { collapsed, toggle } = useSidebar();
  const pathname = usePathname();

  const segments = pathname
    .split('/')
    .filter(Boolean)
    .map((s: string) => formatSegment(s));

  const breadcrumb = segments.length > 0 ? segments[segments.length - 1] : brand;

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-background/80 px-4 backdrop-blur-sm">
      {/* Mobile hamburger */}
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 md:hidden"
        onClick={onMobileMenuOpen}
        aria-label="Open menu"
      >
        <Menu className="h-4 w-4" />
      </Button>

      {/* Desktop collapse toggle */}
      <Button
        variant="ghost"
        size="icon"
        className="hidden h-8 w-8 md:flex text-muted-foreground hover:text-foreground"
        onClick={toggle}
        aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
      >
        <Menu className="h-4 w-4" />
      </Button>

      <Separator orientation="vertical" className="hidden h-5 md:flex" />

      {/* Breadcrumb */}
      <span className="text-sm font-medium text-foreground">{breadcrumb}</span>

      <div className="ml-auto flex items-center gap-1">
        {/* ⌘K trigger */}
        <button
          onClick={onCommandPaletteOpen}
          className={cn(
            'hidden h-8 items-center gap-2 rounded-md border border-border bg-muted/40 px-3 text-xs text-muted-foreground',
            'hover:bg-muted hover:text-foreground transition-colors',
            'sm:flex',
          )}
        >
          <Search className="h-3 w-3" />
          Cerca…
          <kbd className="ml-2 inline-flex h-5 select-none items-center gap-0.5 rounded border border-border bg-muted px-1.5 font-mono text-[10px]">
            <span className="text-[10px]">⌘</span>K
          </kbd>
        </button>

        <ThemeToggle />

        {userMenu && <Separator orientation="vertical" className="mx-1 h-5" />}
        {userMenu}
      </div>
    </header>
  );
}
