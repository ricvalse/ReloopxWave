'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '../utils';
import { ScrollArea } from '../components/ui/scroll-area';
import { Sheet, SheetContent } from '../components/ui/sheet';
import { Separator } from '../components/ui/separator';
import { TooltipProvider } from '../components/ui/tooltip';
import { SidebarProvider, useSidebar } from './sidebar-context';
import { CommandPalette } from './command-palette';
import { Topbar } from './topbar';
import type { ShellUser } from './types';

export interface AppShellProps {
  sidebar: ReactNode;
  userMenu?: ReactNode;
  brand?: string;
  user?: ShellUser;
  onSignOut?: () => void;
  children: ReactNode;
}

function AppShellInner({
  sidebar,
  userMenu,
  brand = 'Reloop',
  onSignOut,
  children,
}: AppShellProps) {
  const { collapsed } = useSidebar();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const paletteOpenRef = useRef(paletteOpen);
  paletteOpenRef.current = paletteOpen;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex h-screen overflow-hidden bg-background">
        {/* Desktop sidebar */}
        <aside
          className={cn(
            'hidden md:flex flex-col h-full shrink-0 border-r border-border bg-card',
            'transition-[width] duration-200 ease-in-out overflow-hidden',
            collapsed ? 'w-14' : 'w-64',
          )}
        >
          {/* Brand */}
          <div
            className={cn(
              'flex h-14 shrink-0 items-center border-b border-border',
              collapsed ? 'justify-center px-0' : 'px-4',
            )}
          >
            {!collapsed && (
              <span className="text-sm font-semibold tracking-tight text-foreground">{brand}</span>
            )}
            {collapsed && (
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-[11px] font-bold text-primary-foreground">
                {brand.charAt(0)}
              </span>
            )}
          </div>
          {/* Nav */}
          <ScrollArea className="flex-1 py-2">
            <div className={cn('space-y-0.5', collapsed ? 'px-2' : 'px-3')}>{sidebar}</div>
          </ScrollArea>
          {/* Footer separator */}
          <Separator />
        </aside>

        {/* Mobile sidebar sheet */}
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetContent side="left" className="w-64 p-0">
            <div className="flex h-14 items-center border-b border-border px-4">
              <span className="text-sm font-semibold tracking-tight text-foreground">{brand}</span>
            </div>
            <ScrollArea className="flex-1 py-2">
              <div className="space-y-0.5 px-3" onClick={() => setMobileOpen(false)}>
                {sidebar}
              </div>
            </ScrollArea>
          </SheetContent>
        </Sheet>

        {/* Main area */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Topbar
            brand={brand}
            userMenu={userMenu}
            onCommandPaletteOpen={() => setPaletteOpen(true)}
            onMobileMenuOpen={() => setMobileOpen(true)}
          />
          <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>

      {/* ⌘K palette */}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} onSignOut={onSignOut} />
    </TooltipProvider>
  );
}

export function AppShell(props: AppShellProps) {
  return (
    <SidebarProvider>
      <AppShellInner {...props} />
    </SidebarProvider>
  );
}
