import type { ChatSummary, MessageRecord, ReasoningPreview, SendMessageResponse } from '@/utils/types';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const PENDING_PROMPT_PREFIX = 'pending-chat-prompt';

export const CHAT_HISTORY_UPDATED_EVENT = 'chat-history-updated';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    cache: 'no-store',
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  });

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === 'object' && payload !== null
        ? String(
            (payload as { detail?: string; error?: string }).detail ||
              (payload as { detail?: string; error?: string }).error ||
              'Request failed'
          )
        : String(payload || 'Request failed');
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

export async function listChats(): Promise<ChatSummary[]> {
  return apiRequest<ChatSummary[]>('/api/chats', { method: 'GET' });
}

export async function createChat(title?: string): Promise<ChatSummary> {
  return apiRequest<ChatSummary>('/api/chats', {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  });
}

export async function getChat(chatId: string): Promise<ChatSummary> {
  return apiRequest<ChatSummary>(`/api/chats/${chatId}`, { method: 'GET' });
}

export async function listChatMessages(chatId: string): Promise<MessageRecord[]> {
  return apiRequest<MessageRecord[]>(`/api/chats/${chatId}/messages`, { method: 'GET' });
}

export async function sendChatMessage(chatId: string, content: string): Promise<SendMessageResponse> {
  return apiRequest<SendMessageResponse>(`/api/chats/${chatId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

export async function getReasoningPreview(chatId: string, content: string): Promise<ReasoningPreview> {
  return apiRequest<ReasoningPreview>(`/api/chats/${chatId}/reasoning-preview`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

/**
 * Stream LLM-generated reasoning steps from the SSE endpoint.
 *
 * Calls `onStep` for each reasoning step as it arrives.
 * Calls `onSummary` when the summary event arrives.
 * Returns `true` if at least one step was received, `false` otherwise
 * (so the caller can decide whether to keep its local fallback).
 */
export async function streamReasoningSteps(
  chatId: string,
  content: string,
  callbacks: {
    onStep: (step: string, index: number, total: number) => void;
    onSummary?: (summary: string, total: number) => void;
  },
  signal?: AbortSignal,
): Promise<boolean> {
  const url = `${API_BASE_URL}/api/chats/${chatId}/reasoning-stream?content=${encodeURIComponent(content)}`;
  let receivedSteps = false;

  try {
    const response = await fetch(url, {
      cache: 'no-store',
      signal,
    });

    if (!response.ok || !response.body) {
      return false;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      // Keep the last potentially incomplete line in the buffer
      buffer = lines.pop() || '';

      let currentEventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEventType = line.slice(7).trim();
          if (currentEventType === 'done') break;
          continue;
        }

        if (!line.startsWith('data: ')) continue;

        const jsonStr = line.slice(6).trim();
        if (!jsonStr || jsonStr === '{}') continue;

        try {
          const parsed = JSON.parse(jsonStr);

          if (currentEventType === 'summary' && parsed.summary) {
            callbacks.onSummary?.(parsed.summary, parsed.total || 0);
            currentEventType = '';
            continue;
          }

          if (parsed.step) {
            callbacks.onStep(parsed.step, parsed.index ?? 0, parsed.total ?? 1);
            receivedSteps = true;
          }
        } catch {
          // Skip malformed JSON lines
        }
        currentEventType = '';
      }
    }
  } catch (error) {
    if (signal?.aborted) return receivedSteps;
    console.error('Reasoning stream error:', error);
  }

  return receivedSteps;
}

export async function deleteChat(chatId: string): Promise<{ deleted: boolean }> {
  return apiRequest<{ deleted: boolean }>(`/api/chats/${chatId}`, {
    method: 'DELETE',
  });
}

export function notifyChatHistoryUpdated() {
  if (typeof window === 'undefined') {
    return;
  }
  window.dispatchEvent(new Event(CHAT_HISTORY_UPDATED_EVENT));
}

function getPendingPromptKey(chatId: string) {
  return `${PENDING_PROMPT_PREFIX}:${chatId}`;
}

export function storePendingPrompt(chatId: string, prompt: string) {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.setItem(getPendingPromptKey(chatId), prompt);
}

export function consumePendingPrompt(chatId: string): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const key = getPendingPromptKey(chatId);
  const prompt = window.sessionStorage.getItem(key);
  if (prompt) {
    window.sessionStorage.removeItem(key);
  }
  return prompt;
}
