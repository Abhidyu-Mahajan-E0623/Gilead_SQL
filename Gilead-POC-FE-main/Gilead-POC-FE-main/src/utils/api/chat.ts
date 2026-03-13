interface QueryResponse {
  success: boolean;
  response: string;
  error?: string;
}

interface QueryParams {
  question: string;
  sessionId: string;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_SERVER_URL;

if (!API_BASE_URL) {
  throw new Error('NEXT_PUBLIC_SERVER_URL is not defined');
}

export const queryChat = async ({ question, sessionId }: QueryParams): Promise<string> => {
  try {
    const url = `${API_BASE_URL}/query?question=${encodeURIComponent(question)}&session_id=${sessionId}`;
    
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error(`API Error: ${response.status}`);
    }

    const data: QueryResponse = await response.json();
    
    // Backend returns {success: true/false, response: "text", error?: "..."}
    if (data.success) {
      return data.response;
    } else {
      // Handle error case
      throw new Error(data.error || data.response || 'Unknown error occurred');
    }
    
  } catch (error) {
    console.error('Query API Error:', error);
    throw error;
  }
};
// Generate a session ID for the user (can be stored in localStorage)
export const getOrCreateSessionId = (): string => {
  if (typeof window === 'undefined') return '1234';
  
  let sessionId = localStorage.getItem('chatSessionId');
  if (!sessionId) {
    sessionId = Date.now().toString();
    localStorage.setItem('chatSessionId', sessionId);
  }
  return sessionId;
};
