'use client';

import { SidebarNavItem, useSidebar, Separator } from '@reloop/ui';
import { useEffect } from 'react';
import { adminNav } from '@/config/nav';

export function Sidebar() {
  const { registerNav, collapsed } = useSidebar();

  useEffect(() => {
    registerNav(adminNav);
  }, [registerNav]);

  return (
    <>
      {adminNav.map((section, i) => (
        <div key={i}>
          {i > 0 && <Separator className="my-2" />}
          {section.title && !collapsed && (
            <p className="mb-1 px-3 text-[10px] font-medium uppercase tracking-widest text-muted-foreground/60">
              {section.title}
            </p>
          )}
          <div className="space-y-0.5">
            {section.items.map((item) => (
              <SidebarNavItem key={item.href} item={item} />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
