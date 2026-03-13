'use client';
import React, { useState } from 'react';
import { BeatLoader } from 'react-spinners';
import { AiOutlineStar, AiFillStar } from 'react-icons/ai';
import { FiCopy } from 'react-icons/fi';
import Image from 'next/image';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './MessageLeft.module.css';

interface MessageLeftProps {
  content: string;
  isLoading?: boolean;
}

const MessageLeft: React.FC<MessageLeftProps> = ({ content, isLoading = false }) => {
  const [rating, setRating] = useState(0);
  const [hoveredRating, setHoveredRating] = useState(0);
  const [copied, setCopied] = useState(false);

  const handleRatingClick = (star: number) => {
    setRating(star);
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // Only render loading state if there's no content
  if (isLoading && (!content || content.trim() === '')) {
    return (
      <div className="flex justify-start mb-6">
        <div className="flex-shrink-0">
          <div className="w-[44px] h-[44px] mb-2 bg-white rounded-full flex items-center justify-center mr-3 shadow-sm border border-gray-100">
            <Image
              src="/Images/BotIconInsightSphere.svg"
              alt="Bot"
              width={32}
              height={32}
              className="block"
            />
          </div>
        </div>
        <div className="max-w-[70%]">
          <div className="flex items-start space-x-3">
            <div className="flex-1">
              <div className="bg-white rounded-[20px] rounded-tl-[4px] px-6 py-4 shadow-sm border border-gray-100">
                <div className="flex items-center space-x-3">
                  <BeatLoader color="#8a162c" size={8} margin={2} />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Render message with content
  return (
    <div className="flex justify-start mb-2">
      <div className="flex-shrink-0">
        <div className="w-[44px] h-[44px] mb-2 bg-white rounded-full flex items-center justify-center mr-3 shadow-sm border border-gray-100">
          <Image
            src="/Images/BotIconInsightSphere.svg"
            alt="Bot"
            width={32}
            height={32}
            className="block"
          />
        </div>
      </div>
      <div className="max-w-[70%]">
        <div className="flex items-start space-x-3">
          <div className="flex-1">
            <div className="bg-white rounded-[20px] rounded-tl-[4px] px-6 py-4 shadow-sm border border-gray-100">
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                <div
                  className={`${styles.messageMarkdownContent} text-content text-sm leading-[22px] font-normal`}
                  style={{
                    wordWrap: 'break-word',
                    overflowWrap: 'break-word',
                    flex: 1,
                  }}
                >
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {content}
                  </ReactMarkdown>
                </div>
                {/* Show loader next to content if still streaming */}
                {isLoading && (
                  <div style={{ display: 'flex', alignItems: 'center', paddingLeft: 8 }}>
                    <BeatLoader color="#8a162c" size={6} margin={2} />
                  </div>
                )}
              </div>
            </div>

            {/* Rating and Copy Controls */}
            <div className="relative mt-2 flex items-center px-2">
              <div className="flex space-x-1">
                {[1, 2, 3, 4, 5].map((star) => (
                  <button
                    key={star}
                    onClick={() => handleRatingClick(star)}
                    onMouseEnter={() => setHoveredRating(star)}
                    onMouseLeave={() => setHoveredRating(0)}
                    className="cursor-pointer transition-opacity text-gray-400"
                    aria-label={`Rate ${star} star${star > 1 ? 's' : ''}`}
                  >
                    {hoveredRating ? (
                      star <= hoveredRating ? (
                        <AiFillStar size={18} color="#8a162c" />
                      ) : (
                        <AiOutlineStar size={18} />
                      )
                    ) : rating >= star ? (
                      <AiFillStar size={18} color="#8a162c" />
                    ) : (
                      <AiOutlineStar size={18} />
                    )}
                  </button>
                ))}
                <button
                  onClick={handleCopy}
                  className="flex cursor-pointer items-center ml-2 space-x-1 text-gray-500 hover:text-gray-700 text-xs"
                  aria-label="Copy message"
                >
                  <FiCopy />
                </button>
              </div>
              {copied && (
                <div className={styles.copiedTooltip}>
                  Copied to clipboard
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default MessageLeft;
