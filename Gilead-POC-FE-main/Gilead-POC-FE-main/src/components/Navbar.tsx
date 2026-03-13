'use client';
import React from 'react';
import Image from 'next/image';
import { useRouter, usePathname } from 'next/navigation';
import UserIcon from './UserIcon';
import { MOCK_USER } from '@/utils/constants';
import { useAuth } from '@/context/AuthContext';

const Navbar = () => {
  const router = useRouter();
  const pathname = usePathname();
  const { logout, isAuthenticated } = useAuth();

  const handleLogoClick = () => {
    router.push('/');
  };

  const handleLogout = () => {
    logout();
  };

  // Show only logo on login page
  const isLoginPage = pathname === '/login';

  return (
    <div className="flex items-center justify-between w-full h-full py-4 px-4 md:px-4 lg:px-6 bg-white">
      {/* Left side - Logos with separator */}
      <div className="flex items-center gap-6">
        {/* First Logo */}
        <button className="cursor-pointer" onClick={handleLogoClick}>
          <Image
            src="/Images/ProcDNALogo.svg"
            alt="ProcDNA Logo"
            width={120}
            height={32}
            priority
            className="h-8 lg:h-10 w-auto flex-shrink-0"
          />
        </button>

        {/* Vertical Separator */}
        <div className="h-8 lg:h-10 w-[2px] bg-primary"></div>

        {/* Second Logo */}
        <Image
          src="/Images/GileadLogo.png"
          alt="Gilead Logo"
          width={120}
          height={32}
          priority
          className="h-8 lg:h-8 w-auto flex-shrink-0"
        />
      </div>

      {/* Right side - User Icon & Logout (only show if authenticated and not on login page) */}
      {isAuthenticated && !isLoginPage && (
        <div className="flex items-center gap-4">
          <UserIcon user={MOCK_USER} size="md" />
           <button
            onClick={handleLogout}
            className="cursor-pointer p-3 rounded-full hover:bg-gray-100 hover:shadow-md transition-colors"
            aria-label="Logout"
          >
            <Image
              src="/Images/Logout.svg"
              alt="Logout"
              width={20}
              height={20}
              className="w-5 h-5"
            />
          </button>
        </div>
      )}
    </div>
  );
};

export default Navbar;
