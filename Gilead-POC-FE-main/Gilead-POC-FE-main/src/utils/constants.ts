import { Chat } from "./types";


export const PRESET_QUESTIONS = [
  "Could you please share the names and territories of the top 10 HCPs ranked by TRX count for 2025 period?",
  "From which date is the current ZIP-to-territory alignment effective in the data",
  "How many TRx have been attributed to CarePlus Medical Center for Q2 2025?",
  "Please share the bottom five territories ranked by IC goal attainment %."
];

export const MOCK_USER = {
  Username: "Demo User",
  ProfileUrl: "", // Leave empty to show initials
};

export const MOCK_CHATS: Chat[] = [
  { 
    id: 'chat-1', 
    title: 'Top 10 HCPs by TRX volume in Q4', 
    timestamp: '2 hours ago' 
  },
  { 
    id: 'chat-2', 
    title: 'Monthly sales by territory', 
    timestamp: '5 hours ago' 
  },
  { 
    id: 'chat-3', 
    title: 'Year-over-year sales comparison by region', 
    timestamp: '3 days ago' 
  },
];