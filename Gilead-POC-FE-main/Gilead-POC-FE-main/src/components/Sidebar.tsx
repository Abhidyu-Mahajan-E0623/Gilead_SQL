'use client';

import React, { useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { usePathname, useRouter } from 'next/navigation';
import { FiMoreHorizontal, FiTrash2 } from 'react-icons/fi';
import {
  CHAT_HISTORY_UPDATED_EVENT,
  createChat,
  deleteChat,
  listChats,
  notifyChatHistoryUpdated,
} from '@/utils/api/chat';
import type { ChatSummary } from '@/utils/types';

interface SidebarProps {
  isCollapsed?: boolean;
  onToggleCollapse?: (collapsed: boolean) => void;
}

function formatUpdatedLabel(updatedAt: string) {
  const date = new Date(updatedAt);
  if (Number.isNaN(date.getTime())) {
    return '';
  }

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

  if (diffDays <= 0) {
    return date.toLocaleTimeString([], {
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  if (diffDays === 1) {
    return 'Yesterday';
  }

  if (diffDays < 7) {
    return date.toLocaleDateString([], { weekday: 'short' });
  }

  return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

const Sidebar: React.FC<SidebarProps> = ({ isCollapsed = false, onToggleCollapse }) => {
  const [search, setSearch] = useState('');
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [isLoadingChats, setIsLoadingChats] = useState(true);
  const [isCreatingChat, setIsCreatingChat] = useState(false);
  const [deletingChatId, setDeletingChatId] = useState<string | null>(null);
  const [openMenuChatId, setOpenMenuChatId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const sidebarRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    let isActive = true;

    const loadChats = async (showLoader: boolean) => {
      if (showLoader) {
        setIsLoadingChats(true);
      }

      try {
        const chatList = await listChats();
        if (!isActive) {
          return;
        }
        setChats(chatList.filter((chat) => !chat.archived));
        setLoadError(null);
      } catch (error) {
        if (!isActive) {
          return;
        }
        console.error('Failed to load chats:', error);
        setLoadError('Unable to load chat history.');
      } finally {
        if (isActive) {
          setIsLoadingChats(false);
        }
      }
    };

    void loadChats(true);

    const handleHistoryUpdate = () => {
      void loadChats(false);
    };

    window.addEventListener(CHAT_HISTORY_UPDATED_EVENT, handleHistoryUpdate);

    return () => {
      isActive = false;
      window.removeEventListener(CHAT_HISTORY_UPDATED_EVENT, handleHistoryUpdate);
    };
  }, [pathname]);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!sidebarRef.current?.contains(event.target as Node)) {
        setOpenMenuChatId(null);
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, []);

  const toggleSidebar = () => {
    onToggleCollapse?.(!isCollapsed);
  };

  const handleNewChat = async (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (isCreatingChat) {
      return;
    }

    setIsCreatingChat(true);
    setLoadError(null);

    try {
      const chat = await createChat();
      setOpenMenuChatId(null);
      notifyChatHistoryUpdated();
      router.push(`/chat/${chat.id}`);
    } catch (error) {
      console.error('Failed to create chat:', error);
      setLoadError('Unable to start a new chat.');
    } finally {
      setIsCreatingChat(false);
    }
  };

  const handleChatClick = (chatId: string) => {
    setOpenMenuChatId(null);
    router.push(`/chat/${chatId}`);
  };

  const handleDeleteChat = async (event: React.MouseEvent<HTMLButtonElement>, chatId: string) => {
    event.stopPropagation();
    if (deletingChatId) {
      return;
    }

    setDeletingChatId(chatId);
    setLoadError(null);

    try {
      await deleteChat(chatId);
      setChats((previous) => previous.filter((chat) => chat.id !== chatId));
      setOpenMenuChatId(null);
      notifyChatHistoryUpdated();

      if (pathname === `/chat/${chatId}`) {
        router.push('/');
      }
    } catch (error) {
      console.error('Failed to delete chat:', error);
      setLoadError('Unable to delete this chat.');
    } finally {
      setDeletingChatId(null);
    }
  };

  const filteredChats = chats.filter((chat) => {
    const query = search.toLowerCase().trim();
    if (!query) {
      return true;
    }
    return (
      chat.title.toLowerCase().includes(query) ||
      (chat.last_message_preview || '').toLowerCase().includes(query)
    );
  });

  return (
    <div
      ref={sidebarRef}
      className="h-full min-h-0 border-r cursor-pointer bg-white border-gray-200 flex flex-col transition-all duration-300 relative w-full"
      onClick={() => {
        if (isCollapsed) {
          onToggleCollapse?.(false);
        }
        setOpenMenuChatId(null);
      }}
    >
      <div className="p-5 flex flex-col h-full min-h-0">
        <div className="flex flex-col gap-4">
          <div className="relative flex items-center">
            {!isCollapsed && (
              <div className="ml-2 relative flex-1">
                <input
                  type="text"
                  placeholder="Search chats"
                  value={search}
                  className="w-full pr-9 py-2.5 pl-4 bg-secondary text-content rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                  onChange={(event) => setSearch(event.target.value)}
                />
                <Image
                  src="/Images/Search.svg"
                  alt="Search Icon"
                  width={16}
                  height={16}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                />
              </div>
            )}
            <button
              onClick={(event) => {
                event.stopPropagation();
                toggleSidebar();
              }}
              className="ml-2 bg-white cursor-pointer rounded-full p-1.5 hover:shadow-sm transition-shadow"
              title={isCollapsed ? 'Expand' : 'Collapse'}
            >
              <Image src="/Images/Sidebar.svg" alt="Toggle Sidebar" width={16} height={16} />
            </button>
          </div>

          {!isCollapsed && (
            <button
              onClick={handleNewChat}
              disabled={isCreatingChat}
              className="mx-2 bg-primary cursor-pointer text-white rounded-xl py-2.5 text-sm font-medium hover:bg-primary-dark transition-opacity disabled:cursor-not-allowed disabled:opacity-70"
            >
              {isCreatingChat ? 'Creating...' : 'New Chat'}
            </button>
          )}
        </div>

        {!isCollapsed && (
          <div className="flex-1 min-h-0 mt-6 flex flex-col">
            <div className="px-2 pb-3 border-b border-gray-200">
              <h3 className="text-[15px] font-semibold text-gray-800">Your chats</h3>
            </div>

            <div className="flex-1 min-h-0 overflow-hidden pt-3">
              <div className="h-full overflow-y-auto custom-scrollbar pr-1">
                {loadError ? (
                  <p className="text-xs text-red-600 px-2 py-3">{loadError}</p>
                ) : isLoadingChats ? (
                  <p className="text-xs text-gray-500 px-2 py-3">Loading chats...</p>
                ) : filteredChats.length === 0 ? (
                  <p className="text-xs text-gray-500 px-2 py-3">
                    {search ? 'No chats match your search.' : 'No chat history yet.'}
                  </p>
                ) : (
                  <div className="space-y-1 pb-4">
                    {filteredChats.map((chat) => {
                      const isActive = pathname === `/chat/${chat.id}`;
                      const isMenuOpen = openMenuChatId === chat.id;
                      const isDeleting = deletingChatId === chat.id;
                      return (
                        <div
                          key={chat.id}
                          className="relative"
                        >
                          <button
                            onClick={(event) => {
                              event.stopPropagation();
                              handleChatClick(chat.id);
                            }}
                            className={`w-full text-left px-3 py-3 pr-24 rounded-2xl transition-colors cursor-pointer group border ${
                              isActive
                                ? 'bg-red-50 border-red-100'
                                : 'bg-transparent border-transparent hover:bg-gray-50'
                            }`}
                          >
                            <div className="flex items-start gap-3">
                              <Image
                                src="/Images/ChatIcon.svg"
                                alt="Chat"
                                width={16}
                                height={16}
                                className="flex-shrink-0 mt-1"
                              />
                              <div className="min-w-0 flex-1">
                                <p
                                  className={`text-sm font-medium leading-5 truncate ${
                                    isActive ? 'text-primary' : 'text-gray-900 group-hover:text-primary'
                                  }`}
                                >
                                  {chat.title}
                                </p>
                                <p className="text-xs text-gray-500 leading-4 mt-1 overflow-hidden text-ellipsis whitespace-nowrap">
                                  {chat.last_message_preview || 'No messages yet'}
                                </p>
                              </div>
                            </div>
                          </button>

                          <div className="absolute top-3 right-3 flex items-center gap-1.5">
                            <span className="text-[11px] text-gray-400 flex-shrink-0">
                              {formatUpdatedLabel(chat.updated_at)}
                            </span>
                            <button
                              type="button"
                              aria-label={`Open actions for ${chat.title}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                setOpenMenuChatId((previous) => (previous === chat.id ? null : chat.id));
                              }}
                              className={`rounded-lg p-1.5 transition-colors ${
                                isMenuOpen
                                  ? 'bg-white text-gray-700 shadow-sm'
                                  : 'text-gray-400 hover:bg-white hover:text-gray-700'
                              }`}
                            >
                              <FiMoreHorizontal size={15} />
                            </button>
                          </div>

                          {isMenuOpen && (
                            <div className="absolute top-12 right-3 z-20 min-w-[144px] rounded-xl border border-gray-200 bg-white shadow-[0_12px_30px_rgba(15,23,42,0.12)] p-1.5">
                              <button
                                type="button"
                                onClick={(event) => void handleDeleteChat(event, chat.id)}
                                disabled={isDeleting}
                                className="w-full flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-60 disabled:cursor-not-allowed"
                              >
                                <FiTrash2 size={14} />
                                {isDeleting ? 'Deleting...' : 'Delete chat'}
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Sidebar;
