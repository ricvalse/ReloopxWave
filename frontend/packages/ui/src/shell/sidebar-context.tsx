'use client';

import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { NavSection } from './types';

interface SidebarContextValue {
  collapsed: boolean;
  toggle: () => void;
  nav: NavSection[];
  registerNav: (nav: NavSection[]) => void;
}

const SidebarContext = createContext<SidebarContextValue>({
  collapsed: false,
  toggle: () => {},
  nav: [],
  registerNav: () => {},
});

export function SidebarProvider({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [nav, setNav] = useState<NavSection[]>([]);

  const toggle = useCallback(() => setCollapsed((v) => !v), []);
  const registerNav = useCallback((sections: NavSection[]) => setNav(sections), []);

  const value = useMemo(
    () => ({ collapsed, toggle, nav, registerNav }),
    [collapsed, toggle, nav, registerNav],
  );

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>;
}

export function useSidebar() {
  return useContext(SidebarContext);
}
