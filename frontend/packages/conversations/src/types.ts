export type MessageStatus = 'pending' | 'sent' | 'delivered' | 'read' | 'failed';
export type MessageDirection = 'in' | 'out';
export type MessageRole = 'user' | 'assistant' | 'agent';

export interface Conversation {
  id: string;
  merchant_id: string;
  wa_contact_phone: string | null;
  wa_phone_number_id: string | null;
  status: string;
  last_message_at: string | null;
  message_count: number;
  /** Per-thread bot takeover. AND-ed with merchant `bot.auto_reply_enabled`. */
  auto_reply: boolean;
  meta: Record<string, unknown> | null;
  created_at: string;
  // Hydrated client-side from the latest message in the thread
  last_message_preview?: string | null;
  last_message_role?: MessageRole | null;
  unread_count?: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: MessageRole;
  direction: MessageDirection;
  content: string;
  status: MessageStatus;
  client_message_id: string | null;
  wa_message_id: string | null;
  delivered_at: string | null;
  read_at: string | null;
  failed_at: string | null;
  error: Record<string, unknown> | null;
  created_at: string;
}

export interface ThreadFilters {
  status?: 'open' | 'closed' | 'all';
  merchantId?: string;
  search?: string;
}
