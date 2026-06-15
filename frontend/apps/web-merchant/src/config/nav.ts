import {
  BarChart3,
  Bot,
  Database,
  FileWarning,
  FlaskConical,
  LayoutTemplate,
  MessageSquare,
  Play,
  Plug,
  Settings,
  Workflow,
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
    title: 'Messaggistica',
    items: [
      { href: '/whatsapp-templates', label: 'Template WhatsApp', icon: LayoutTemplate },
      { href: '/flussi', label: 'Flussi', icon: Workflow },
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
