import Link from 'next/link';
import type { Route } from 'next';
import { Users, LayoutDashboard, FileCode, Settings, Inbox } from 'lucide-react';

type NavItem = { href: Route; label: string; icon: typeof LayoutDashboard };

const nav: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/inbox', label: 'Inbox', icon: Inbox },
  { href: '/merchants', label: 'Merchant', icon: Users },
  { href: '/templates', label: 'Template bot', icon: FileCode },
  { href: '/settings', label: 'Impostazioni', icon: Settings },
];

export function Sidebar() {
  return (
    <nav className="flex h-full flex-col gap-1 p-4">
      <div className="mb-6 px-2 text-lg font-semibold">Admin</div>
      {nav.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground"
        >
          <item.icon className="h-4 w-4" />
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
