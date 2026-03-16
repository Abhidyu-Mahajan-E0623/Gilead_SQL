export interface User {
  Username: string;
  ProfileUrl?: string;
}

export interface UserIconProps {
  user: User | null;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  className?: string;
}

export interface ChatSummary {
  id: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
  last_message_preview: string | null;
}

export interface MessageRecord {
  id: string;
  chat_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  metadata?: Record<string, unknown> | null;
}

export interface SendMessageResponse {
  chat: ChatSummary;
  user_message: MessageRecord;
  assistant_message: MessageRecord;
  matched_inquiry_id?: string | null;
  matched_title?: string | null;
  confidence?: number | null;
}

export interface ReasoningPreview {
  summary: string;
  details: string[];
}

export interface ReasoningStepEvent {
  step: string;
  index: number;
  total: number;
}
