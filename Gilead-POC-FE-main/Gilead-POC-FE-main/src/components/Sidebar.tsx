'use client';
import React, { useState } from 'react';
import Image from 'next/image';
import { useRouter, usePathname } from 'next/navigation';
import { MOCK_CHATS } from '@/utils/constants';
import { Chat } from '@/utils/types';

interface SidebarProps {
  isCollapsed?: boolean;
  onToggleCollapse?: (collapsed: boolean) => void;
}

const Sidebar: React.FC<SidebarProps> = ({ isCollapsed = false, onToggleCollapse }) => {
  const [search, setSearch] = useState('');
  const router = useRouter();
  const pathname = usePathname();

  const toggleSidebar = () => {
    onToggleCollapse?.(!isCollapsed);
  };

  const handleNewChat = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    router.push('/');
  };

  const handleChatClick = (chatId: string) => {
    // Store mock messages for this chat
    const chat = MOCK_CHATS.find(c => c.id === chatId);
    const mockMessages = [
      { role: 'user', content: chat?.title || 'Hello' },
      { 
        role: 'assistant', 
        content: 'mock bot answer'
      },
    ];
    
    sessionStorage.setItem('currentChatMessages', JSON.stringify(mockMessages));
    router.push(`/chat/${chatId}`);
  };

  // Filter chats based on search
  const filteredChats = MOCK_CHATS.filter(chat =>
    chat.title.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div
      className="h-full border-r cursor-pointer bg-white border-gray-200 flex flex-col transition-all duration-300 relative w-full"
      onClick={() => {
        if (isCollapsed) onToggleCollapse?.(false);
      }}
    >
      <div className="p-6 flex flex-col h-full">
        <div className="flex flex-col gap-5">
          {/* Search bar and toggle button */}
          <div className="relative flex items-center">
            {!isCollapsed && (
              <div className="ml-2 relative flex-1">
                <input
                  type="text"
                  placeholder="Search"
                  value={search}
                  className="w-full pr-9 py-2.5 pl-4 bg-secondary text-content rounded-lg text-sm focus:outline-none focus:ring-2"
                  onChange={(e) => setSearch(e.target.value)}
                />
                <Image
                  src="/Images/Search.svg"
                  alt="Search Icon"
                  width={16}
                  height={16}
                  className="absolute right-2 top-1/2 -translate-y-1/2"
                />
              </div>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                toggleSidebar();
              }}
              className="ml-2 bg-white cursor-pointer rounded-full p-1.5 hover:shadow-sm transition-shadow"
              title={isCollapsed ? 'Expand' : 'Collapse'}
            >
              <Image src="/Images/Sidebar.svg" alt="Toggle Sidebar" width={16} height={16} />
            </button>
          </div>

          {/* New Chat button */}
          {!isCollapsed && (
            <button
              onClick={handleNewChat}
              className="mx-2 bg-primary cursor-pointer text-white rounded-xl py-2.5 text-sm font-medium hover:bg-primary-dark transition-opacity"
            >
              New Chat
            </button>
          )}
        </div>

       

        {/* Recent Chats */}
        {!isCollapsed && (
          <div className="flex-1 my-6 overflow-hidden flex flex-col">
            <h3 className="text-sm font-semibold text-gray-700 mb-3 mx-2">Recent Chats</h3>
          
          {/* Horizontal Separator */}
          {!isCollapsed && (
            <div className="my-1  mx-2 border-t border-gray-200"></div>
          )}

            <div className="flex-1 overflow-y-auto custom-scrollbar">
              {filteredChats.length === 0 ? (
                <p className="text-xs text-gray-500 text-center py-4">No chats found</p>
              ) : (
                <div className="space-y-1">
                  {filteredChats.map((chat) => {
                    const isActive = pathname === `/chat/${chat.id}`;
                    
                    return (
                      <button
                        key={chat.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleChatClick(chat.id);
                        }}
                        className={`w-full text-left py-3 rounded-lg transition-colors cursor-pointer group ${
                          isActive
                            ? 'bg-red-50 border border-red-100'
                            : 'hover:bg-gray-50 border border-transparent'
                        }`}
                      >
                        <div className="flex items-start gap-2">
                          <Image
                            src="/Images/ChatIcon.svg"
                            alt="Chat"
                            width={16}
                            height={16}
                            className="flex-shrink-0 mt-0.5"
                            onError={(e) => {
                              e.currentTarget.style.display = 'none';
                            }}
                          />
                          <div className="flex-1 min-w-0">
                            <p
                              className={`text-sm font-medium truncate ${
                                isActive ? 'text-primary' : 'text-gray-800 group-hover:text-primary'
                              }`}
                            >
                              {chat.title}
                            </p>
                          
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default Sidebar;
