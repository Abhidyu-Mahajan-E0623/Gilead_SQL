/* eslint-disable react-hooks/set-state-in-effect */
'use client';
import React, { useState, useRef, useEffect } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import MessageLeft from './MessageLeft';
import MessageRight from './MessageRight';
import { queryChat, getOrCreateSessionId } from '@/utils/api/chat';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface ChatSessionProps {
  chatId: string;
}

const ChatSession: React.FC<ChatSessionProps> = ({ chatId }) => {
  const [inputValue, setInputValue] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [hasInitialized, setHasInitialized] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const router = useRouter();

  const scrollToBottom = (behavior: ScrollBehavior = 'smooth') => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  };

  useEffect(() => {
    if (messages.length > 0) {
      setTimeout(() => {
        scrollToBottom();
      }, 10);
    }
  }, [messages]);

  useEffect(() => {
    if (!hasInitialized && typeof window !== 'undefined') {
      const storedMessages = sessionStorage.getItem('currentChatMessages');
      if (storedMessages) {
        try {
          const parsedMessages = JSON.parse(storedMessages);
          if (Array.isArray(parsedMessages)) {
            setMessages(parsedMessages);
          }
        } catch (e) {
          console.error('Failed to parse messages:', e);
        }
      }
      setHasInitialized(true);
    }
  }, [hasInitialized]);

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
    if (!text || isLoading) return;

    setInputValue('');
    resetTextareaHeight();

    const userMessage: Message = {
      role: 'user',
      content: text,
    };

    // Add user message immediately
    const updatedMessagesWithUser = [...messages, userMessage];
    setMessages(updatedMessagesWithUser);
    sessionStorage.setItem('currentChatMessages', JSON.stringify(updatedMessagesWithUser));

    // Show loading state
    setIsLoading(true);

    try {
      const sessionId = getOrCreateSessionId();
      const answer = await queryChat({
        question: text,
        sessionId,
      });

      const botMessage: Message = {
        role: 'assistant',
        content: answer,
      };

      const updatedMessages = [...updatedMessagesWithUser, botMessage];
      setMessages(updatedMessages);
      sessionStorage.setItem('currentChatMessages', JSON.stringify(updatedMessages));
    } catch (error) {
      console.error('Failed to get response:', error);
      
      const errorMessage: Message = {
        role: 'assistant',
        content: 'Sorry, I encountered an error processing your request. Please try again.',
      };

      const updatedMessages = [...updatedMessagesWithUser, errorMessage];
      setMessages(updatedMessages);
      sessionStorage.setItem('currentChatMessages', JSON.stringify(updatedMessages));
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div
      className="flex flex-col h-full bg-repeat"
      style={{
        backgroundImage: 'url(/Images/BackgroundImage.png)',
        backgroundSize: 'auto',
        backgroundPosition: '0 0',
      }}
    >
      {/* Messages Container */}
      <div className="flex-1 mx-auto w-full px-8 overflow-hidden mb-[30px] mt-[10px] min-h-0">
        <div
          ref={messagesContainerRef}
          className="h-full overflow-y-auto custom-scrollbar pb-4"
          style={{
            overflowAnchor: 'none',
            scrollbarGutter: 'stable',
          }}
        >
          <div className="py-4">
            {!hasInitialized ? (
              <div className="flex justify-center items-center py-12">
                <div className="text-gray-500">Loading...</div>
              </div>
            ) : messages.length === 0 ? (
              <div className="text-center text-gray-500 mt-20">
                <p className="text-lg font-medium">Failed to fetch chat</p>
                <p className="text-sm mt-2">Please try again later</p>
                <button
                  className="p-2 text-white bg-primary mt-3 rounded-md cursor-pointer font-semibold text-sm"
                  onClick={() => router.push('/')}
                >
                  Go to Home
                </button>
              </div>
            ) : (
              <>
                {messages.map((message, index) => {
                  if (message.role === 'user') {
                    return <MessageRight key={index} content={message.content} />;
                  }
                  return <MessageLeft key={index} content={message.content} isLoading={false} />;
                })}
                {isLoading && <MessageLeft content="" isLoading={true} />}
                <div ref={messagesEndRef} />
              </>
            )}
          </div>
        </div>
      </div>

      {/* Input Container - Fixed to bottom */}
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
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isLoading}
                placeholder="What would you like to know?"
                rows={1}
                className="flex-1 text-black custom-scrollbar font-normal text-[14px] leading-[22px] bg-transparent border-none outline-none resize-none overflow-hidden min-h-[22px] max-h-[88px]"
                style={{
                  height: 'auto',
                  minHeight: '22px',
                  maxHeight: '88px',
                }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = 'auto';
                  target.style.height = `${Math.min(target.scrollHeight, 88)}px`;
                  target.style.overflowY = target.scrollHeight > 88 ? 'scroll' : 'hidden';
                }}
              />
              <button
                onClick={handleSubmit}
                className="flex mt-0.5 cursor-pointer items-center justify-end rounded-full transition-colors duration-200 flex-shrink-0"
                disabled={!inputValue.trim() || isLoading}
              >
                <Image src="/Images/SendIcon.svg" alt="Submit" width={18} height={22} />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ChatSession;
