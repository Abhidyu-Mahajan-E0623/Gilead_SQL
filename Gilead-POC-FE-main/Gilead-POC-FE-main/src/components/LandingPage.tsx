'use client';
import React, { useState } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { createChat, notifyChatHistoryUpdated, storePendingPrompt } from '@/utils/api/chat';

const LandingPage = () => {
  const [inputValue, setInputValue] = useState('');
  const [isCreatingChat, setIsCreatingChat] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const router = useRouter();

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const resetTextareaHeight = () => {
    const textarea = document.querySelector('textarea');
    if (textarea) {
      textarea.style.height = 'auto';
      textarea.style.height = '22px';
      textarea.style.overflowY = 'hidden';
    }
  };

  const handleSubmit = async () => {
    if (!inputValue.trim()) {
      return;
    }

    const userQuestion = inputValue.trim();
    try {
      setIsCreatingChat(true);
      setSubmitError(null);

      const chat = await createChat();
      storePendingPrompt(chat.id, userQuestion);
      notifyChatHistoryUpdated();

      resetTextareaHeight();
      setInputValue('');
      router.push(`/chat/${chat.id}`);
    } catch (error) {
      console.error('Failed to create chat:', error);
      setSubmitError('Unable to start a new chat right now. Please try again.');
    } finally {
      setIsCreatingChat(false);
    }
  };

  return (
    <div
      className="min-h-full bg-repeat"
      style={{
        backgroundImage: 'url(/Images/BackgroundImage.png)',
        backgroundSize: 'auto',
        backgroundPosition: '0 0',
      }}
    >
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

          {/* Input Box */}
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
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyPress={handleKeyPress}
                  disabled={isCreatingChat}
                  placeholder="What would you like to know?"
                  rows={1}
                  className="flex-1 text-black custom-scrollbar font-normal text-[16px] leading-[22px] bg-transparent border-none outline-none resize-none overflow-hidden min-h-[22px] max-h-[88px]"
                  style={{
                    height: 'auto',
                    minHeight: '22px',
                    maxHeight: '88px',
                  }}
                  onInput={(e) => {
                    const target = e.target as HTMLTextAreaElement;
                    target.style.height = 'auto';
                    target.style.height = `${Math.min(target.scrollHeight, 88)}px`;
                    if (target.scrollHeight > 88) {
                      target.style.overflowY = 'scroll';
                    } else {
                      target.style.overflowY = 'hidden';
                    }
                  }}
                />

                <button
                  onClick={handleSubmit}
                  className="flex cursor-pointer items-center justify-end rounded-full transition-colors duration-200 flex-shrink-0"
                  disabled={!inputValue.trim() || isCreatingChat}
                >
                  <Image src="/Images/SendIcon.svg" alt="Submit" width={22} height={22} />
                </button>
              </div>
            </div>
          </div>

          {submitError && <p className="text-sm text-red-600 mt-3">{submitError}</p>}
        </div>
      </div>
    </div>
  );
};

export default LandingPage;
