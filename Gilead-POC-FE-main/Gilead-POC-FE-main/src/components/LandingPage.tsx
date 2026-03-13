'use client';
import React, { useState } from 'react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { PRESET_QUESTIONS } from '@/utils/constants';
import { queryChat, getOrCreateSessionId } from '@/utils/api/chat';

const LandingPage = () => {
  const [inputValue, setInputValue] = useState('');
  const router = useRouter();

  const handleQuestionClick = (question: string) => {
    setInputValue(question);
    setTimeout(() => {
      const textarea = document.querySelector('textarea');
      if (textarea) {
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      }
    }, 0);
  };

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
    sessionStorage.setItem('pendingQuestion', userQuestion);

    resetTextareaHeight();
    setInputValue('');

    router.push('/chat/pending');

    // Call API in the background
    try {
      const sessionId = getOrCreateSessionId();
      const answer = await queryChat({
        question: userQuestion,
        sessionId,
      });

      // Generate chat ID
      const mockChatId = 'chat-' + Date.now();

      // Store messages in sessionStorage
      sessionStorage.setItem(
        'currentChatMessages',
        JSON.stringify([
          { role: 'user', content: userQuestion },
          { role: 'assistant', content: answer },
        ])
      );

      // Navigate to chat page
      router.push(`/chat/${mockChatId}`);
    } catch (error) {
      console.error('Failed to get response:', error);
      
      // Store error state
      sessionStorage.setItem(
        'currentChatMessages',
        JSON.stringify([
          { role: 'user', content: userQuestion },
          { role: 'assistant', content: 'Sorry, I encountered an error processing your request. Please try again.' },
        ])
      );

      // Navigate anyway with error message
      const mockChatId = 'chat-' + Date.now();
      router.push(`/chat/${mockChatId}`);
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
              background: 'linear-gradient(180deg, #8a162c 0%, #c5203f 50%, #e63946 100%)',
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
                  disabled={!inputValue.trim()}
                >
                  <Image src="/Images/SendIcon.svg" alt="Submit" width={22} height={22} />
                </button>
              </div>
            </div>
          </div>

          {/* Preset Questions */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-[20px] max-w-4xl mx-auto fade-in delay-3">
            {PRESET_QUESTIONS.map((question, index) => (
              <button
                key={index}
                onClick={() => handleQuestionClick(question)}
                className="group cursor-pointer text-left p-3 bg-tertiary rounded-lg hover:bg-primary2 transition-all duration-200 leading-[16px] flex items-center"
              >
                <div className="flex items-center justify-between w-full">
                  <span className="text-black group-hover:text-white text-[14px] leading-[18px] pr-2 font-normal">
                    {question}
                  </span>
                  <Image
                    src="/Images/SlantedArrow.svg"
                    alt="Arrow"
                    width={10}
                    height={10}
                    className="group-hover:brightness-0 group-hover:invert"
                  />
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LandingPage;
