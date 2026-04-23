import Link from 'next/link';
import {
  BarChart3,
  Bot,
  Database,
  FlaskConical,
  MessageSquare,
  Plug,
  Play,
  Settings as SettingsIcon,
  FileWarning,
} from 'lucide-react';

const nav = [
  { href: '/dashboard', label: 'Dashboard', icon: BarChart3 },
  { href: '/bot/config', label: 'Config bot', icon: Bot },
  { href: '/bot/knowledge-base', label: 'Knowledge base', icon: Database },
  { href: '/bot/playground', label: 'Playground', icon: Play },
  { href: '/bot/ab-testing', label: 'A/B testing', icon: FlaskConical },
  { href: '/conversations', label: 'Conversazioni', icon: MessageSquare },
  { href: '/reports/objections', label: 'Obiezioni', icon: FileWarning },
  { href: '/integrations', label: 'Integrazioni', icon: Plug },
  { href: '/settings', label: 'Impostazioni', icon: SettingsIcon },
] as const;

export function Sidebar() {
  return (
    <nav className="flex h-full flex-col gap-1 p-4">
      <div className="mb-6 px-2 text-lg font-semibold">Merchant</div>
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
