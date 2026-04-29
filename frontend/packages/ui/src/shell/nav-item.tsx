'use client';

import { cn } from '../utils';
import { Badge } from '../components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '../components/ui/tooltip';
import { useSidebar } from './sidebar-context';
import type { Route } from 'next';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { NavItem } from './types';

interface SidebarNavItemProps {
  item: NavItem;
}

export function SidebarNavItem({ item }: SidebarNavItemProps) {
  const { collapsed } = useSidebar();
  const pathname = usePathname();
  const isActive = item.exact ? pathname === item.href : pathname.startsWith(item.href);

  const inner = (
    <Link
      href={item.href as Route}
      className={cn(
        'group flex h-9 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors',
        'text-muted-foreground hover:bg-accent hover:text-foreground',
        isActive && 'bg-accent text-foreground',
        collapsed && 'w-9 justify-center px-0',
      )}
    >
      <item.icon
        className={cn(
          'h-4 w-4 shrink-0',
          isActive ? 'text-foreground' : 'text-muted-foreground group-hover:text-foreground',
        )}
      />
      {!collapsed && <span className="truncate">{item.label}</span>}
      {!collapsed && item.badge != null && item.badge > 0 && (
        <Badge variant="default" className="ml-auto h-5 min-w-5 px-1 text-[10px]">
          {item.badge > 99 ? '99+' : item.badge}
        </Badge>
      )}
    </Link>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>{inner}</TooltipTrigger>
        <TooltipContent side="right" className="flex items-center gap-2">
          {item.label}
          {item.badge != null && item.badge > 0 && (
            <Badge variant="default" className="h-5 min-w-5 px-1 text-[10px]">
              {item.badge}
            </Badge>
          )}
        </TooltipContent>
      </Tooltip>
    );
  }

  return inner;
}
