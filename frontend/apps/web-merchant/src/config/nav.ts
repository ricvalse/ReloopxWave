import {
  BarChart3,
  Bot,
  Database,
  FileWarning,
  FlaskConical,
  MessageSquare,
  Play,
  Plug,
  Settings,
} from 'lucide-react';
import type { NavSection } from '@reloop/ui';

export const merchantNav: NavSection[] = [
  {
    items: [
      { href: '/dashboard', label: 'Dashboard', icon: BarChart3, exact: true },
      { href: '/conversations', label: 'Conversazioni', icon: MessageSquare },
    ],
  },
  {
    title: 'Bot',
    items: [
      { href: '/bot/config', label: 'Configurazione', icon: Bot },
      { href: '/bot/knowledge-base', label: 'Knowledge base', icon: Database },
      { href: '/bot/playground', label: 'Playground', icon: Play },
      { href: '/bot/ab-testing', label: 'A/B testing', icon: FlaskConical },
    ],
  },
  {
    title: 'Report',
    items: [{ href: '/reports/objections', label: 'Obiezioni', icon: FileWarning }],
  },
  {
    title: 'Sistema',
    items: [
      { href: '/integrations', label: 'Integrazioni', icon: Plug },
      { href: '/settings', label: 'Impostazioni', icon: Settings },
    ],
  },
];
