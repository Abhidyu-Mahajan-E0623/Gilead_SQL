'use client';
import React, { useEffect, useState } from 'react';
import { AiOutlineStar, AiFillStar } from 'react-icons/ai';
import { FiChevronDown, FiChevronUp, FiCopy } from 'react-icons/fi';
import Image from 'next/image';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import styles from './MessageLeft.module.css';
import type { ReasoningPreview } from '@/utils/types';

interface MessageLeftProps {
  content: string;
  isLoading?: boolean;
  reasoningPreview?: ReasoningPreview | null;
}

const FALLBACK_REASONING_PREVIEW: ReasoningPreview = {
  summary: 'Reviewing the request and the latest chat context.',
  details: [
    'I am understanding the user request and the key filters or entities it refers to.',
    'I am checking the relevant chat context and data path before drafting the response.',
    'I will replace this with the final answer once the response is ready.',
  ],
};

const MessageLeft: React.FC<MessageLeftProps> = ({ content, isLoading = false, reasoningPreview = null }) => {
  const [rating, setRating] = useState(0);
  const [hoveredRating, setHoveredRating] = useState(0);
  const [copied, setCopied] = useState(false);
  const [isReasoningExpanded, setIsReasoningExpanded] = useState(true);
  const [completedDetailsCount, setCompletedDetailsCount] = useState(0);
  const [typedCharacters, setTypedCharacters] = useState(0);
  const [isRevealComplete, setIsRevealComplete] = useState(false);

  const handleRatingClick = (star: number) => {
    setRating(star);
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const activePreview = reasoningPreview || FALLBACK_REASONING_PREVIEW;
  const previewSignature = `${activePreview.summary}::${activePreview.details.join('||')}`;

  // Reset animation state whenever the preview content changes (e.g. SSE steps arrive)
  useEffect(() => {
    setCompletedDetailsCount(0);
    setTypedCharacters(0);
    setIsRevealComplete(false);
  }, [previewSignature]);

  useEffect(() => {
    if (!isLoading || (content && content.trim() !== '')) {
      return;
    }

    const details = activePreview.details.filter((detail) => detail.trim().length > 0);
    if (details.length === 0) {
      const completeTimer = window.setTimeout(() => {
        setIsRevealComplete(true);
      }, 0);

      return () => window.clearTimeout(completeTimer);
    }

    let detailIndex = 0;
    let characterIndex = 0;
    let pauseFrames = 0;

    const timer = window.setInterval(() => {
      const currentDetail = details[detailIndex] || '';

      if (characterIndex < currentDetail.length) {
        characterIndex += 1;
        setTypedCharacters(characterIndex);
        return;
      }

      if (pauseFrames < 10) {
        pauseFrames += 1;
        return;
      }

      detailIndex += 1;
      setCompletedDetailsCount(detailIndex);
      pauseFrames = 0;

      if (detailIndex >= details.length) {
        setTypedCharacters(0);
        setIsRevealComplete(true);
        window.clearInterval(timer);
        return;
      }

      characterIndex = 0;
      setTypedCharacters(0);
    }, 18);

    return () => window.clearInterval(timer);
  }, [activePreview.details, content, isLoading, previewSignature]);

  // Only render loading state if there's no content
  if (isLoading && (!content || content.trim() === '')) {
    const visibleDetails = activePreview.details.slice(0, completedDetailsCount);
    const currentStreamingDetail =
      !isRevealComplete && completedDetailsCount < activePreview.details.length
        ? activePreview.details[completedDetailsCount]
        : null;

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
              <button
                type="button"
                onClick={() => setIsReasoningExpanded((previous) => !previous)}
                className={`${styles.reasoningBubble} bg-white rounded-[20px] rounded-tl-[4px] px-5 py-4 shadow-sm border border-gray-100 text-left`}
                aria-expanded={isReasoningExpanded}
              >
                <div className={styles.reasoningHeader}>
                  <div className={styles.reasoningTitleWrap}>
                    <span className={styles.reasoningTitle}>Thinking</span>
                    <div className={styles.reasoningDots} aria-hidden="true">
                      <span className={styles.reasoningDot} />
                      <span className={styles.reasoningDot} />
                      <span className={styles.reasoningDot} />
                    </div>
                  </div>
                  {isReasoningExpanded ? (
                    <FiChevronUp className={styles.reasoningChevron} />
                  ) : (
                    <FiChevronDown className={styles.reasoningChevron} />
                  )}
                </div>
                <p className={styles.reasoningSummary}>{activePreview.summary}</p>
                {isReasoningExpanded && (
                  <div className={styles.reasoningDetails}>
                    {visibleDetails.map((detail, index) => (
                      <p key={`${detail}-${index}`} className={styles.reasoningDetail}>
                        {detail}
                      </p>
                    ))}
                    {currentStreamingDetail && (
                      <p className={`${styles.reasoningDetail} ${styles.reasoningDetailCurrent}`}>
                        {currentStreamingDetail.slice(0, typedCharacters)}
                        <span className={styles.reasoningCursor} aria-hidden="true" />
                      </p>
                    )}
                    {isRevealComplete && (
                      <p className={styles.reasoningStatus}>
                        Still validating the result before I send the final response
                        <span className={styles.reasoningStatusDots} aria-hidden="true">
                          <span />
                          <span />
                          <span />
                        </span>
                      </p>
                    )}
                  </div>
                )}
                {!isReasoningExpanded && (
                  <p className={styles.reasoningHint}>
                    Tap to view live processing notes
                  </p>
                )}
              </button>
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
                  <div className={styles.inlineThinking} aria-hidden="true">
                    <span className={styles.inlineThinkingDot} />
                    <span className={styles.inlineThinkingDot} />
                    <span className={styles.inlineThinkingDot} />
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
