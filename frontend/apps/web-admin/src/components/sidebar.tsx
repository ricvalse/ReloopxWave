import Link from 'next/link';
import { Users, LayoutDashboard, FileCode, Settings } from 'lucide-react';

const nav = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/merchants', label: 'Merchant', icon: Users },
  { href: '/templates', label: 'Template bot', icon: FileCode },
  { href: '/settings', label: 'Impostazioni', icon: Settings },
] as const;

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
