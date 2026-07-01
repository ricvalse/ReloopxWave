import {
  BarChart3,
  Bot,
  CalendarClock,
  CalendarDays,
  CalendarOff,
  Database,
  FileWarning,
  FlaskConical,
  HelpCircle,
  LayoutTemplate,
  MessageSquare,
  Play,
  Plug,
  ScrollText,
  Settings,
  Store,
  Waypoints,
} from 'lucide-react';
import type { NavSection } from '@reloop/ui';

export const merchantNav: NavSection[] = [
  {
    items: [
      { href: '/dashboard', label: 'Dashboard', icon: BarChart3, exact: true },
      { href: '/conversations', label: 'Conversazioni', icon: MessageSquare },
      { href: '/agenda', label: 'Agenda', icon: CalendarDays },
    ],
  },
  {
    title: 'Prenotazioni',
    items: [
      { href: '/prenotazioni/servizi', label: 'Servizi', icon: CalendarClock },
      { href: '/prenotazioni/orari', label: 'Orari e chiusure', icon: CalendarOff },
    ],
  },
  {
    title: 'Brand',
    items: [
      { href: '/brand/info', label: 'Informazioni', icon: Store },
      { href: '/brand/policies', label: 'Policy', icon: ScrollText },
      { href: '/brand/faq', label: 'FAQ', icon: HelpCircle },
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
      { href: '/automazioni', label: 'Automazioni', icon: Waypoints },
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
