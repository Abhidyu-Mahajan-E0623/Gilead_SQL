'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import MessageLeft from './MessageLeft';
import MessageRight from './MessageRight';
import {
  ApiError,
  consumePendingPrompt,
  getChat,
  listChatMessages,
  notifyChatHistoryUpdated,
  sendChatMessage,
  streamReasoningSteps,
} from '@/utils/api/chat';
import type { MessageRecord, ReasoningPreview } from '@/utils/types';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

interface ChatSessionProps {
  chatId: string;
}

function mapMessage(record: MessageRecord): ChatMessage {
  return {
    id: record.id,
    role: record.role,
    content: record.content,
  };
}

function buildFallbackReasoningPreview(
  userMessage: string,
  existingMessages: ChatMessage[],
): ReasoningPreview {
  const compact = userMessage.trim().replace(/\s+/g, ' ');
  const recentUserMessage = [...existingMessages]
    .reverse()
    .find((message) => message.role === 'user' && message.content.trim());
  const recentAssistantMessage = [...existingMessages]
    .reverse()
    .find((message) => message.role === 'assistant' && message.content.trim());
  const focus = compact.length > 88 ? `${compact.slice(0, 85).trimEnd()}...` : compact;
  const npiMatch = compact.match(/\bNPI\s*[:#-]?\s*(\d{10})\b|\b(\d{10})\b/i);
  const territoryMatch = compact.match(/\b[A-Z]{2,4}-\d{1,3}\b/i);
  const mentionsRetail = /\bretail\b/i.test(compact);
  const mentionsStatus = /\bstatus|active|inactive\b/i.test(compact);
  const mentionsDcr = /\bDCR\b|duplicate|merge|merged|credit\b/i.test(compact);
  const tokenCount = compact ? compact.split(/\s+/).length : 0;
  const followUpHint = recentAssistantMessage
    ? /please provide the|before proceeding/i.test(recentAssistantMessage.content)
    : false;
  const isLikelyFollowUp = Boolean(recentUserMessage) && (followUpHint || tokenCount <= 6);

  const details = [`I am understanding the user query and the key request around ${focus}.`];

  if (isLikelyFollowUp) {
    details.push(
      'I am applying recent context from this chat so the follow-up stays aligned with the earlier request.',
    );
  }

  if (npiMatch) {
    details.push(
      `I am preparing SQL to verify NPI ${npiMatch[1] || npiMatch[2]} across provider, alignment, and activity records.`,
    );
  } else if (territoryMatch) {
    details.push(
      `I am preparing SQL to verify territory mapping, effective dates, and related activity for ${territoryMatch[0].toUpperCase()}.`,
    );
  } else {
    details.push('I am mapping the request to the right filters, joins, and data path before pulling the relevant rows.');
  }

  if (mentionsDcr) {
    details.push('I am checking DCR, merge, duplicate-record, and credit-impact signals tied to this request.');
  }

  if (mentionsRetail) {
    details.push('I am performing SQL to find retail status, retail flags, and recent retail-linked activity.');
  }

  if (mentionsStatus && !mentionsDcr) {
    details.push('I am checking active or inactive status flags and any recent record-level changes tied to this request.');
  }

  details.push('I am gathering the returned rows, validating the context, and preparing the final response.');

  return {
    summary: `Tracing ${focus} through the relevant data checks.`,
    details,
  };
}

const ChatSession: React.FC<ChatSessionProps> = ({ chatId }) => {
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isInitializing, setIsInitializing] = useState(true);
  const [chatMissing, setChatMissing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reasoningPreview, setReasoningPreview] = useState<ReasoningPreview | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesRef = useRef<ChatMessage[]>([]);
  const isSendingRef = useRef(false);
  const reasoningRequestRef = useRef<string | null>(null);
  const reasoningAbortRef = useRef<AbortController | null>(null);
  const sendMessageRef = useRef<(text: string) => Promise<void>>(async () => {});
  const router = useRouter();

  const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  };

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    if (messages.length > 0 || isLoading) {
      setTimeout(() => {
        scrollToBottom(messages.length > 0 ? 'smooth' : 'auto');
      }, 10);
    }
  }, [isLoading, messages]);

  const sendMessage = useCallback(async (rawText: string) => {
    const text = rawText.trim();
    if (!text || isSendingRef.current || chatMissing) {
      return;
    }

    const optimisticMessage: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
    };

    setMessages((previous) => [...previous, optimisticMessage]);
    isSendingRef.current = true;
    setIsLoading(true);

    // Show local fallback reasoning immediately for instant UX
    const fallbackPreview = buildFallbackReasoningPreview(text, messagesRef.current);
    setReasoningPreview(fallbackPreview);
    reasoningRequestRef.current = optimisticMessage.id;

    // Start SSE stream for LLM-generated reasoning (replaces fallback as steps arrive)
    const abortController = new AbortController();
    reasoningAbortRef.current = abortController;

    void streamReasoningSteps(
      chatId,
      text,
      {
        onSummary: (summary) => {
          if (reasoningRequestRef.current === optimisticMessage.id) {
            setReasoningPreview((prev) => prev ? { ...prev, summary } : { summary, details: [] });
          }
        },
        onStep: (step, index) => {
          if (reasoningRequestRef.current === optimisticMessage.id) {
            setReasoningPreview((prev) => {
              if (!prev) return { summary: fallbackPreview.summary, details: [step] };
              // On first SSE step, replace fallback details entirely
              if (index === 0) return { ...prev, details: [step] };
              // Append subsequent steps
              return { ...prev, details: [...prev.details, step] };
            });
          }
        },
      },
      abortController.signal,
    ).catch((error) => {
      console.error('SSE reasoning stream failed, keeping fallback:', error);
    });

    try {
      const response = await sendChatMessage(chatId, text);

      setMessages((previous) => {
        const withoutOptimistic = previous.filter((message) => message.id !== optimisticMessage.id);
        return [
          ...withoutOptimistic,
          mapMessage(response.user_message),
          mapMessage(response.assistant_message),
        ];
      });

      notifyChatHistoryUpdated();
    } catch (error) {
      console.error('Failed to send message:', error);

      setMessages((previous) => {
        const withoutOptimistic = previous.filter((message) => message.id !== optimisticMessage.id);
        return [
          ...withoutOptimistic,
          optimisticMessage,
          {
            id: `error-${Date.now()}`,
            role: 'assistant',
            content: 'Sorry, I encountered an error processing your request. Please try again.',
          },
        ];
      });
    } finally {
      isSendingRef.current = false;
      reasoningRequestRef.current = null;
      // Abort the SSE reasoning stream if it's still running
      reasoningAbortRef.current?.abort();
      reasoningAbortRef.current = null;
      setReasoningPreview(null);
      setIsLoading(false);
    }
  }, [chatId, chatMissing]);

  // Keep the ref in sync so hydrateChat can call the latest sendMessage
  useEffect(() => {
    sendMessageRef.current = sendMessage;
  }, [sendMessage]);

  useEffect(() => {
    let isActive = true;

    const hydrateChat = async () => {
      setIsInitializing(true);
      setChatMissing(false);
      setLoadError(null);

      try {
        await getChat(chatId);
        const storedMessages = await listChatMessages(chatId);

        if (!isActive) {
          return;
        }

        setMessages(storedMessages.map(mapMessage));

        const pendingPrompt = consumePendingPrompt(chatId);
        if (pendingPrompt && storedMessages.length === 0) {
          void sendMessageRef.current(pendingPrompt);
        }
      } catch (error) {
        if (!isActive) {
          return;
        }

        console.error('Failed to hydrate chat:', error);
        if (error instanceof ApiError && error.status === 404) {
          setChatMissing(true);
        } else {
          setLoadError('Unable to load this chat right now.');
        }
      } finally {
        if (isActive) {
          setIsInitializing(false);
        }
      }
    };

    void hydrateChat();

    return () => {
      isActive = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatId]);

  const resetTextareaHeight = () => {
    const textarea = document.querySelector('textarea');
    if (textarea) {
      textarea.style.height = 'auto';
      textarea.style.height = '22px';
      textarea.style.overflowY = 'hidden';
    }
  };

  const handleSubmit = async () => {
    const text = inputValue.trim();
    if (!text || isLoading || isInitializing || chatMissing) {
      return;
    }

    setInputValue('');
    resetTextareaHeight();
    await sendMessage(text);
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void handleSubmit();
    }
  };

  const isInputDisabled = isLoading || isInitializing || chatMissing || Boolean(loadError);
  const showWelcomeScreen = !isInitializing && !chatMissing && !loadError && messages.length === 0;

  return (
    <div
      className="flex flex-col h-full bg-repeat"
      style={{
        backgroundImage: 'url(/Images/BackgroundImage.png)',
        backgroundSize: 'auto',
        backgroundPosition: '0 0',
      }}
    >
      <div className="flex-1 mx-auto w-full px-8 overflow-hidden mb-[30px] mt-[10px] min-h-0">
        <div
          className="h-full overflow-y-auto custom-scrollbar pb-4"
          style={{
            overflowAnchor: 'none',
            scrollbarGutter: 'stable',
          }}
        >
          <div className="py-4">
            {isInitializing ? (
              <div className="flex justify-center items-center py-12">
                <div className="text-gray-500">Loading conversation...</div>
              </div>
            ) : chatMissing ? (
              <div className="text-center text-gray-500 mt-20">
                <p className="text-lg font-medium">Chat not found</p>
                <p className="text-sm mt-2">This conversation no longer exists in local history.</p>
                <button
                  className="p-2 text-white bg-primary mt-3 rounded-md cursor-pointer font-semibold text-sm"
                  onClick={() => router.push('/')}
                >
                  Go to Home
                </button>
              </div>
            ) : loadError ? (
              <div className="text-center text-gray-500 mt-20">
                <p className="text-lg font-medium">Unable to load chat</p>
                <p className="text-sm mt-2">{loadError}</p>
                <button
                  className="p-2 text-white bg-primary mt-3 rounded-md cursor-pointer font-semibold text-sm"
                  onClick={() => router.push('/')}
                >
                  Back to Home
                </button>
              </div>
            ) : showWelcomeScreen ? (
              <div className="flex flex-col items-center justify-center gap-y-[20px] px-8 pt-[250px] pb-6 min-h-[60vh]">
                <div className="text-left max-w-4xl w-full fade-in delay-1">
                  <h1
                    className="text-[50px] font-semibold bg-clip-text text-transparent"
                    style={{
                      background: 'linear-gradient(180deg, #007ECC 0%, #001E96 50%, #005CD9 100%)',
                      WebkitBackgroundClip: 'text',
                      WebkitTextFillColor: 'transparent',
                      backgroundClip: 'text',
                    }}
                  >
                    Hi User
                  </h1>
                  <p className="text-black text-[18px] mt-2 font-normal leading-6">
                    I am your dedicated field assistant, here to empower you with intelligent insights.
                  </p>

                  <div className="bg-white rounded-[12px] shadow-[0px_0px_12px_0px_#0000001A] max-w-4xl mx-auto mt-[20px] mb-4 fade-in delay-2">
                    <div className="pl-[12px] pr-[14px] py-[12px]">
                      <div className="flex items-start pl-1 text-sm justify-between gap-2">
                        <Image
                          src="/Images/Star.svg"
                          alt="Star"
                          width={20}
                          height={20}
                          className="flex-shrink-0 "
                        />
                        <textarea
                          value={inputValue}
                          onChange={(event) => setInputValue(event.target.value)}
                          onKeyDown={handleKeyDown}
                          disabled={isInputDisabled}
                          placeholder="What would you like to know?"
                          rows={1}
                          className="flex-1 text-black custom-scrollbar font-normal text-[16px] leading-[22px] bg-transparent border-none outline-none resize-none overflow-hidden min-h-[22px] max-h-[88px]"
                          style={{
                            height: 'auto',
                            minHeight: '22px',
                            maxHeight: '88px',
                          }}
                          onInput={(event) => {
                            const target = event.target as HTMLTextAreaElement;
                            target.style.height = 'auto';
                            target.style.height = `${Math.min(target.scrollHeight, 88)}px`;
                            target.style.overflowY = target.scrollHeight > 88 ? 'scroll' : 'hidden';
                          }}
                        />
                        <button
                          onClick={() => void handleSubmit()}
                          className="flex cursor-pointer items-center justify-end rounded-full transition-colors duration-200 flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                          disabled={!inputValue.trim() || isInputDisabled}
                        >
                          <Image src="/Images/SendIcon.svg" alt="Submit" width={22} height={22} />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <>
                {messages.map((message) =>
                  message.role === 'user' ? (
                    <MessageRight key={message.id} content={message.content} />
                  ) : (
                    <MessageLeft key={message.id} content={message.content} isLoading={false} />
                  )
                )}
                {isLoading && (
                  <MessageLeft
                    key="reasoning-loading"
                    content=""
                    isLoading={true}
                    reasoningPreview={reasoningPreview}
                  />
                )}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>
        </div>
      </div>

      {!showWelcomeScreen && (
        <div className="flex-shrink-0 mx-auto w-full px-8 pb-8">
          <div className="bg-white rounded-[12px] shadow-[0px_0px_12px_0px_#0000001A]">
            <div className="pl-[12px] pr-[14px] py-[12px]">
              <div className="flex items-start pl-1 text-sm justify-between gap-2">
                <Image
                  src="/Images/Star.svg"
                  alt="Star"
                  width={20}
                  height={20}
                  className="flex-shrink-0 "
                />
                <textarea
                  value={inputValue}
                  onChange={(event) => setInputValue(event.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={isInputDisabled}
                  placeholder="What would you like to know?"
                  rows={1}
                  className="flex-1 text-black custom-scrollbar font-normal text-[14px] leading-[22px] bg-transparent border-none outline-none resize-none overflow-hidden min-h-[22px] max-h-[88px]"
                  style={{
                    height: 'auto',
                    minHeight: '22px',
                    maxHeight: '88px',
                  }}
                  onInput={(event) => {
                    const target = event.target as HTMLTextAreaElement;
                    target.style.height = 'auto';
                    target.style.height = `${Math.min(target.scrollHeight, 88)}px`;
                    target.style.overflowY = target.scrollHeight > 88 ? 'scroll' : 'hidden';
                  }}
                />
                <button
                  onClick={() => void handleSubmit()}
                  className="flex mt-0.5 cursor-pointer items-center justify-end rounded-full transition-colors duration-200 flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                  disabled={!inputValue.trim() || isInputDisabled}
                >
                  <Image src="/Images/SendIcon.svg" alt="Submit" width={18} height={22} />
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatSession;
