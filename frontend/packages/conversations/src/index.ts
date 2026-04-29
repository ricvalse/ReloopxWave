// Top-level workspace
export { ConversationsWorkspace } from './components/conversations-workspace';

// Context provider that the apps wrap their conversations route with
export { ConversationsProvider, useConversationsContext } from './lib/context';
export type { ConversationsContextValue, AnySupabaseClient } from './lib/context';

// Lower-level pieces (rarely used directly, but exported for composition)
export { ThreadList } from './components/thread-list';
export { ThreadListItem } from './components/thread-list-item';
export { ThreadHeader } from './components/thread-header';
export { MessageList } from './components/message-list';
export { MessageBubble } from './components/message-bubble';
export { DaySeparator } from './components/day-separator';
export { StatusTicks } from './components/status-ticks';
export { Composer } from './components/composer';

// Hooks
export { useConversations } from './hooks/use-conversations';
export { useThread, threadQueryKey } from './hooks/use-thread';
export { useSendMessage } from './hooks/use-send-message';
export { useToggleAutoReply } from './hooks/use-toggle-auto-reply';

// Types
export type {
  Conversation,
  Message,
  MessageStatus,
  MessageDirection,
  MessageRole,
  ThreadFilters,
} from './types';

// Lib utils
export {
  formatThreadTime,
  formatBubbleTime,
  formatDaySeparator,
  isSameDay,
} from './lib/time';
export { contactInitials, contactDisplayName } from './lib/initials';
