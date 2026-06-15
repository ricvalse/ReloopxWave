import { FileCode, Inbox, LayoutDashboard, Plug, Settings, Sparkles, Users } from 'lucide-react';
import type { NavSection } from '@reloop/ui';

export const adminNav: NavSection[] = [
  {
    items: [
      { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, exact: true },
      { href: '/inbox', label: 'Inbox', icon: Inbox },
    ],
  },
  {
    title: 'Gestione',
    items: [
      { href: '/merchants', label: 'Merchant', icon: Users },
      { href: '/integrations', label: 'Integrazioni', icon: Plug },
      { href: '/templates', label: 'Template bot', icon: FileCode },
      { href: '/fine-tuning', label: 'Fine-tuning', icon: Sparkles },
    ],
  },
  {
    title: 'Sistema',
    items: [{ href: '/settings', label: 'Impostazioni', icon: Settings }],
  },
];
