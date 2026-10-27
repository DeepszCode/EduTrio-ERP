import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { supabase } from "../lib/supabase";

const Context = createContext(null);
const API_BASE_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");
const MAX_SAVED_CHATS = 20;
const MAX_SAVED_MESSAGES_PER_CHAT = 100;
const INITIAL_MESSAGE = {
  id: "welcome",
  sender: "ai",
  text: "Hello! Welcome to SMART ERP. I can help you search student records, 10th and 12th marksheets, Transfer Certificates, and other certificates. Ask me a question to get started.",
  source: "assistant",
  format: "text",
};

function createChat() {
  const id = globalThis.crypto?.randomUUID?.() || `chat-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return { id, title: "New chat", messages: [INITIAL_MESSAGE], updatedAt: Date.now() };
}

function fromSavedConversation(row) {
  const messages = Array.isArray(row.messages)
    ? row.messages.filter((message) => message && typeof message.text === "string")
    : [];
  return {
    id: String(row.id),
    title: typeof row.title === "string" && row.title.trim() ? row.title : "New chat",
    messages: messages.length ? messages : [INITIAL_MESSAGE],
    updatedAt: Date.parse(row.updated_at) || Date.now(),
  };
}

async function getActiveSession(forceRefresh = false) {
  if (!supabase) throw new Error("Supabase configuration is missing. Check frontend/.env and restart the Vite server.");

  if (!forceRefresh) {
    const { data, error } = await supabase.auth.getSession();
    if (!error && data.session?.access_token) return data.session;
  }

  const { data, error } = await supabase.auth.refreshSession();
  if (error || !data.session?.access_token) {
    throw new Error("Your login session has expired. Please sign in again.");
  }
  return data.session;
}

export function AIConversationProvider({ children }) {
  const [chats, setChats] = useState(() => [createChat()]);
  const [activeChatId, setActiveChatId] = useState(() => chats[0]?.id);
  const [ownerId, setOwnerId] = useState(null);
  const [historyReady, setHistoryReady] = useState(() => !supabase);
  const [historyStatus, setHistoryStatus] = useState(() => (supabase
    ? "Loading saved conversations..."
    : "Supabase is not configured; conversations cannot be saved."));
  const messages = useMemo(
    () => chats.find((chat) => chat.id === activeChatId)?.messages || [INITIAL_MESSAGE],
    [chats, activeChatId],
  );
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [thinkingStatus, setThinkingStatus] = useState("Thinking through your question…");
  const [error, setError] = useState("");
  const [compactOpen, setCompactOpen] = useState(false);
  const controllerRef = useRef(null);
  const requestIdRef = useRef(0);
  const ownerRef = useRef(undefined);
  const historyLoadRef = useRef(0);
  const saveQueueRef = useRef(Promise.resolve());
  const thinkingTimersRef = useRef([]);

  const clearThinkingTimers = useCallback(() => {
    thinkingTimersRef.current.forEach(window.clearTimeout);
    thinkingTimersRef.current = [];
    setThinkingStatus("Thinking through your question…");
  }, []);

  useEffect(() => () => {
    controllerRef.current?.abort();
    thinkingTimersRef.current.forEach(window.clearTimeout);
  }, []);

  useEffect(() => {
    if (!supabase) {
      return undefined;
    }

    let disposed = false;
    const loadForSession = (session) => {
      const nextOwner = session?.user?.id || null;
      if (ownerRef.current === nextOwner) return;
      ownerRef.current = nextOwner;
      setOwnerId(nextOwner);
      setHistoryReady(false);
      setHistoryStatus(nextOwner ? "Loading saved conversations..." : "Sign in to save conversations to your account.");
      controllerRef.current?.abort();
      controllerRef.current = null;
      clearThinkingTimers();
      setIsTyping(false);
      const loadId = ++historyLoadRef.current;

      if (!nextOwner) {
        const freshChat = createChat();
        setChats([freshChat]);
        setActiveChatId(freshChat.id);
        setHistoryReady(true);
        return;
      }

      window.setTimeout(async () => {
        const { data, error: loadError } = await supabase
          .from("ai_conversations")
          .select("id,title,messages,updated_at")
          .order("updated_at", { ascending: false })
          .limit(MAX_SAVED_CHATS);

        if (disposed || loadId !== historyLoadRef.current || ownerRef.current !== nextOwner) return;

        if (loadError) {
          const freshChat = createChat();
          setChats([freshChat]);
          setActiveChatId(freshChat.id);
          setHistoryStatus("Could not load saved chats. Apply the AI conversation history migration.");
        } else {
          const savedChats = (data || []).map(fromSavedConversation);
          const nextChats = savedChats.length ? savedChats : [createChat()];
          setChats(nextChats);
          setActiveChatId(nextChats[0].id);
          setHistoryStatus("Conversations are saved to your account.");
        }
        setHistoryReady(true);
      }, 0);
    };

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      loadForSession(session);
    });

    return () => {
      disposed = true;
      historyLoadRef.current += 1;
      subscription.unsubscribe();
    };
  }, [clearThinkingTimers]);

  useEffect(() => {
    if (!supabase || !ownerId || !historyReady || !chats.length) return undefined;
    const saveOwner = ownerId;
    const saveChats = chats.slice(0, MAX_SAVED_CHATS);
    const timeout = window.setTimeout(() => {
      setHistoryStatus("Saving conversations...");
      const rows = saveChats.map((chat) => ({
        id: chat.id,
        user_id: saveOwner,
        title: chat.title.slice(0, 120),
        messages: chat.messages.slice(-MAX_SAVED_MESSAGES_PER_CHAT),
        updated_at: new Date(chat.updatedAt).toISOString(),
      }));
      saveQueueRef.current = saveQueueRef.current.catch(() => {}).then(async () => {
        if (ownerRef.current !== saveOwner) return;
        const { error: saveError } = await supabase
          .from("ai_conversations")
          .upsert(rows, { onConflict: "user_id,id" });
        if (ownerRef.current !== saveOwner) return;
        setHistoryStatus(saveError
          ? "Could not save chats. Apply the AI conversation history migration."
          : "Conversations are saved to your account.");
      });
    }, 600);
    return () => window.clearTimeout(timeout);
  }, [chats, historyReady, ownerId]);

  const handleSend = useCallback(async (providedQuestion, options = {}) => {
    const question = (typeof providedQuestion === "string" ? providedQuestion : input).trim();
    if (!question || isTyping) return;
    setError("");
    const requestId = ++requestIdRef.current;
    const history = messages
      .filter((message) => message.id !== INITIAL_MESSAGE.id && ["user", "ai"].includes(message.sender) && message.source !== "supabase")
      .slice(-10)
      .map((message) => ({ role: message.sender === "ai" ? "assistant" : "user", content: message.text }));
    const chatId = activeChatId;
    const appendToChat = (message) => setChats((previous) => previous.map((chat) => chat.id === chatId
      ? { ...chat, title: chat.title === "New chat" && message.sender === "user" ? question.slice(0, 42) : chat.title, messages: [...chat.messages, message], updatedAt: Date.now() }
      : chat));
    if (!options.hideUserMessage) appendToChat({ id: `${Date.now()}-${requestId}-user`, sender: "user", text: question });
    setInput("");
    setIsTyping(true);
    setThinkingStatus("Thinking through your question…");
    thinkingTimersRef.current.forEach(window.clearTimeout);
    thinkingTimersRef.current = [
      window.setTimeout(() => setThinkingStatus("Still checking your request…"), 5000),
      window.setTimeout(() => setThinkingStatus("This is taking longer than usual. I’m still working on it…"), 10000),
    ];
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const sendRequest = (accessToken) => fetch(`${API_BASE_URL}/api/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify({ message: question, history, offset: options.offset || 0, page_size: 50 }),
        signal: controller.signal,
      });
      let session = await getActiveSession();
      let response = await sendRequest(session.access_token);
      let data = await response.json().catch(() => ({}));
      const sessionVerificationFailed = (result, body) => result.status === 401 || (
        result.status === 503 && /unable to verify your supabase session/i.test(String(body?.detail || ""))
      );
      if (sessionVerificationFailed(response, data)) {
        session = await getActiveSession(true);
        response = await sendRequest(session.access_token);
        data = await response.json().catch(() => ({}));
      }
      if (response.status === 401) {
        const detail = typeof data.detail === "string" ? data.detail : "";
        if (/authorization header is missing/i.test(detail)) {
          throw new Error("The AI backend did not receive the Authorization header. The request includes your current Supabase access token; check that any development proxy forwards Authorization headers.");
        }
        await supabase.auth.signOut();
        throw new Error("Your login session is no longer valid. Please sign in again.");
      }
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `AI request failed (HTTP ${response.status}). Check the FastAPI backend.`);
      const answer = data.reply || data.response || data.answer;
      if (typeof answer !== "string" || !answer.trim()) throw new Error("The AI returned an empty response. Check the backend response format.");
      if (controller.signal.aborted) return;
      appendToChat({ id: `${Date.now()}-${requestId}-ai`, sender: "ai", text: answer.trim(), source: data.source || "assistant", format: data.format || "text", data: data.data || null });
    } catch (requestError) {
      if (requestError?.name === "AbortError") return;
      const errorText = requestError instanceof TypeError
        ? "Cannot connect to the AI backend. Check FastAPI, VITE_API_URL, and CORS settings."
        : requestError?.message || "Something went wrong. Please try again.";
      setError(errorText);
      appendToChat({ id: `${Date.now()}-${requestId}-error`, sender: "ai", text: `Sorry, ${errorText}`, source: "error", format: "text" });
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setIsTyping(false);
        thinkingTimersRef.current.forEach(window.clearTimeout);
        thinkingTimersRef.current = [];
        setThinkingStatus("Thinking through your question…");
      }
    }
  }, [activeChatId, input, isTyping, messages]);

  const resetConversation = useCallback(() => {
    requestIdRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearThinkingTimers();
    const initialChat = createChat();
    setChats([initialChat]);
    setActiveChatId(initialChat.id);
    setInput("");
    setIsTyping(false);
    setError("");
  }, [clearThinkingTimers]);

  const startNewChat = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearThinkingTimers();
    setIsTyping(false);
    setError("");
    setInput("");
    const chat = createChat();
    setChats((previous) => [chat, ...previous].slice(0, MAX_SAVED_CHATS));
    setActiveChatId(chat.id);
  }, [clearThinkingTimers]);

  const selectChat = useCallback((chatId) => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearThinkingTimers();
    setIsTyping(false);
    setError("");
    setInput("");
    setActiveChatId(chatId);
  }, [clearThinkingTimers]);

  const value = {
    messages, chats, activeChatId, selectChat, startNewChat,
    historyReady, historyStatus,
    input, setInput, isTyping, setIsTyping, thinkingStatus, error, setError,
    compactOpen, setCompactOpen, handleSend, resetConversation,
  };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAIConversation() {
  const context = useContext(Context);
  if (!context) throw new Error("useAIConversation must be used inside AIConversationProvider.");
  return context;
}
