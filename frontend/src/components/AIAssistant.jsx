import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft,
  Bot,
  Send,
  Sparkles,
  Users,
  BarChart3,
  RotateCcw,
  AlertCircle,
  Database,
  X,
  Maximize2,
  MessageSquarePlus,
} from "lucide-react";

import { useAIConversation } from "./AIConversationContext";
import "./AIAssistant.css";

const SUGGESTION_GROUPS = [
  { label: "Database", icon: Database, questions: ["How many students and certificates are in my database?", "Show recent uploads", "Show certificate counts by type", "Show school-wise student count"] },
  { label: "Student Details", icon: Users, questions: ["Find a student by name in my certificate records", "Show a student's 10th marks", "Show a student's 12th marks", "Find a student's school name", "Show all students' marks in a table"] },
  { label: "Visualizations", icon: BarChart3, questions: ["Show a pie chart of certificates by type", "Show a 10th vs 12th comparison chart", "Show monthly certificate upload trends"] },
  { label: "Project Help", icon: Sparkles, questions: ["Explain my SMART ERP project", "What technologies are used in SMART ERP?", "Explain how OCR works in this project", "What are this project's advantages?"] },
];

/* -------------------------------------------------------
   MARKS CHART
------------------------------------------------------- */

function MarksChart({ data }) {
  if (data?.chart?.labels && data?.chart?.values) {
    const { chart } = data;
    const values = chart.values.map((value) => Number(value) || 0);
    const maxValue = Math.max(...values, 1);
    const colors = ["#2864d7", "#11a98b", "#8b5cf6", "#f59e0b", "#ef4444", "#06b6d4"];
    if (chart.type === "pie") {
      const total = values.reduce((sum, value) => sum + value, 0) || 1;
      const stops = values.map((value, index) => {
        const start = values.slice(0, index).reduce((sum, previous) => sum + (previous / total) * 100, 0);
        const end = start + (value / total) * 100;
        return `${colors[index % colors.length]} ${start}% ${end}%`;
      });
      return <div className="ai-chart"><h3>{chart.title}</h3><div className="ai-pie-layout"><div className="ai-pie" style={{ background: `conic-gradient(${stops.join(",")})` }} role="img" aria-label={chart.title} /><ul>{chart.labels.map((label, index) => <li key={`${label}-${index}`}><span style={{ background: colors[index % colors.length] }} />{label}<strong>{values[index]}</strong></li>)}</ul></div></div>;
    }
    if (chart.type === "line") {
      const width = Math.max(360, chart.labels.length * 72);
      const height = 190;
      const xStep = chart.labels.length > 1 ? (width - 56) / (chart.labels.length - 1) : 0;
      const coords = values.map((value, index) => ({ x: 28 + index * xStep, y: height - 24 - (value / maxValue) * (height - 54) }));
      return <div className="ai-chart"><h3>{chart.title}</h3><div className="ai-chart-scroll"><svg className="ai-chart-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={chart.title}><polyline points={coords.map((point) => `${point.x},${point.y}`).join(" ")} fill="none" stroke="#2864d7" strokeWidth="3" />{coords.map((point, index) => <g key={`${chart.labels[index]}-${index}`}><circle cx={point.x} cy={point.y} r="4" fill="#2864d7" /><text x={point.x} y={point.y - 9} textAnchor="middle" fontSize="10">{values[index]}</text><text x={point.x} y={height - 5} textAnchor="middle" fontSize="10">{chart.labels[index]}</text></g>)}</svg></div></div>;
    }
    return <div className="ai-chart"><h3>{chart.title}</h3><div className="ai-data-chart">{chart.labels.map((label, index) => <div className="ai-data-chart-row" key={`${label}-${index}`}><span title={label}>{label}</span><div><i style={{ width: `${Math.max(values[index] ? 2 : 0, values[index] / maxValue * 100)}%`, background: colors[index % colors.length] }} /></div><strong>{values[index]}</strong></div>)}</div></div>;
  }
  const certificates = data?.certificates || {};
  const points = [];

  Object.entries(certificates).forEach(([tableName, records]) => {
    if (!Array.isArray(records)) return;

    records.forEach((record, recordIndex) => {
      if (!record || typeof record !== "object") return;

      Object.entries(record).forEach(([key, value]) => {
        if (
          !/(mark|score)/i.test(key) ||
          /(total|maximum|max_marks|max_score)/i.test(key) ||
          typeof value === "boolean" ||
          value === null ||
          value === undefined ||
          String(value).trim() === ""
        ) {
          return;
        }

        const numericValue = Number(value);

        if (!Number.isFinite(numericValue) || numericValue < 0) {
          return;
        }

        points.push({
          label: key.replace(/_/g, " "),
          value: numericValue,
          table: tableName,
          recordIndex,
        });
      });
    });
  });

  if (!points.length) {
    return (
      <div className="ai-chart-empty">
        <BarChart3 size={24} />
        <p>
          No subject-wise numeric marks were returned. Verify that
          the backend returns marks in the certificate records.
        </p>
      </div>
    );
  }

  const chartPoints = points.slice(0, 20);
  const maxValue = Math.max(...chartPoints.map((point) => point.value), 1);
  const chartHeight = 200;
  const chartWidth = Math.max(500, chartPoints.length * 90);
  const slotWidth = (chartWidth - 60) / chartPoints.length;

  return (
    <div className="ai-chart">
      <h3>Academic Marks Chart</h3>
      <p className="ai-chart-caption">
        Based on numeric marks returned by the backend.
      </p>

      <div className="ai-chart-scroll">
        <svg
          viewBox={`0 0 ${chartWidth} 280`}
          role="img"
          aria-label="Bar chart of available academic marks"
          className="ai-chart-svg"
        >
          {chartPoints.map((point, index) => {
            const x = 30 + index * slotWidth + slotWidth * 0.2;
            const width = Math.max(8, slotWidth * 0.55);
            const height = (point.value / maxValue) * chartHeight;
            const y = chartHeight + 10 - height;

            return (
              <g
                key={`${point.table}-${point.recordIndex}-${point.label}-${index}`}
              >
                <rect
                  x={x}
                  y={y}
                  width={width}
                  height={height}
                  rx="4"
                  fill="#2864d7"
                />

                <text
                  x={x + width / 2}
                  y={Math.max(12, y - 6)}
                  fontSize="11"
                  textAnchor="middle"
                  fill="currentColor"
                >
                  {point.value}
                </text>

                <text
                  x={x + width / 2}
                  y={chartHeight + 32}
                  fontSize="10"
                  textAnchor="end"
                  transform={`rotate(-35 ${x + width / 2} ${chartHeight + 32})`}
                  fill="currentColor"
                >
                  {point.label.slice(0, 20)}
                </text>
              </g>
            );
          })}

          <line
            x1="20"
            y1={chartHeight + 10}
            x2={chartWidth - 15}
            y2={chartHeight + 10}
            stroke="currentColor"
            strokeOpacity="0.3"
          />
        </svg>
      </div>

      <p className="ai-chart-caption">
        Only returned numeric fields are shown. Missing marks are not inferred.
      </p>
    </div>
  );
}

/* -------------------------------------------------------
   ASSISTANT MESSAGE CONTENT
------------------------------------------------------- */

function AssistantContent({ message, onSend }) {
  const structured = message.data || {};
  return (
    <div className="ai-answer-content">
      {message.format === "chart" && message.data && (
        <MarksChart data={message.data} />
      )}

      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="ai-table-wrapper">
              <table className="ai-result-table">{children}</table>
            </div>
          ),
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {message.text}
      </ReactMarkdown>

      {structured.summary_cards?.length > 0 && <div className="ai-summary-cards">{structured.summary_cards.map((card) => <div key={card.label}><span>{card.label}</span><strong>{card.value}</strong></div>)}</div>}
      {structured.tables?.map((table) => (
        <section className="ai-structured-table" key={table.title}>
          <h3>{table.title}</h3>
          <div className="ai-table-wrapper"><table className="ai-result-table"><thead><tr>{table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{table.rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell ?? "—"}</td>)}</tr>)}</tbody></table></div>
        </section>
      ))}
      {structured.has_more && <button type="button" className="ai-load-more" onClick={() => onSend?.(structured.query, { offset: structured.next_offset, hideUserMessage: true })}>Show more records</button>}
      {structured.complete === false && <p className="ai-format-note">The configured record safety cap was reached; results may be incomplete.</p>}
      {structured.quick_replies?.length > 0 && <div className="ai-inline-replies">{structured.quick_replies.map((reply) => <button type="button" key={reply} onClick={() => onSend?.(reply)}>{reply}</button>)}</div>}

      {message.source === "supabase" && (
        <div className="ai-source-label">
          <Database size={13} />
          <span>Retrieved from Supabase</span>
        </div>
      )}

      {message.format === "image" && (
        <p className="ai-format-note">
          The backend must return an authorized image URL or image data
          to display an original certificate image.
        </p>
      )}

      {message.format === "report" && (
        <p className="ai-format-note">
          Report details are shown above. PDF or Excel downloads require
          a separate export action.
        </p>
      )}
    </div>
  );
}

/* -------------------------------------------------------
   MAIN AI ASSISTANT COMPONENT
------------------------------------------------------- */

export default function AIAssistant({ mode = "full", onBack, onClose, onExpand, onReturnToSmall }) {
  const { messages, chats, activeChatId, selectChat, startNewChat, input, setInput, isTyping, thinkingStatus, error, handleSend, historyReady, historyStatus } = useAIConversation();
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  /* Scroll to the latest message */

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [messages, isTyping]);

  /* -----------------------------------------------------
     INPUT HANDLERS
  ----------------------------------------------------- */

  const handleKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  };

  const handleInputChange = (event) => {
    setInput(event.target.value);

    event.target.style.height = "auto";
    event.target.style.height = `${Math.min(
      event.target.scrollHeight,
      120
    )}px`;
  };

  /* -----------------------------------------------------
     RESET CONVERSATION
  ----------------------------------------------------- */

  const handleReset = () => {
    startNewChat();

    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.focus();
    }
  };

  /* -----------------------------------------------------
     RENDER
  ----------------------------------------------------- */

  return (
    <div className={mode === "compact" ? "ai-compact" : "ai-page"}>
      <div className={mode === "compact" ? "ai-compact-container" : "ai-container"}>
        {mode === "full" && <header className="ai-header">
          <div className="ai-page-nav-actions">
            <button type="button" className="ai-back-btn" onClick={onBack}><ArrowLeft size={17} />Back to Dashboard</button>
            <button type="button" className="ai-back-btn ai-return-small-btn" onClick={onReturnToSmall}>Return to small chat</button>
          </div>
          <div className="ai-title-section"><div className="ai-title-icon"><Sparkles size={23} /></div><div><h1>AI Assistant</h1><p>Your SMART ERP intelligent assistant</p></div></div>
        </header>}

        <div className={mode === "full" ? "ai-full-layout" : "ai-compact-layout"}>
        {mode === "full" && <aside className="ai-recent-chats" aria-label="Recent chats">
          <div className="ai-recent-heading"><div><h2>Recent chats</h2><p>{historyReady ? historyStatus : "Loading saved conversations..."}</p></div><button type="button" onClick={startNewChat} aria-label="Start a new chat" title="Start a new chat"><MessageSquarePlus size={17} /></button></div>
          <div className="ai-recent-list">{[...chats].sort((a, b) => b.updatedAt - a.updatedAt).map((chat) => <button type="button" key={chat.id} className={`ai-recent-item ${activeChatId === chat.id ? "active" : ""}`} onClick={() => selectChat(chat.id)}><span>{chat.title}</span><small>{chat.messages.filter((message) => message.sender === "user").length ? `${chat.messages.filter((message) => message.sender === "user").length} messages` : "No messages yet"}</small></button>)}</div>
        </aside>}
        <main className={`ai-chat-card ${mode === "compact" ? "is-compact" : ""}`}>
          <div className="ai-chat-header">
            <div className="ai-avatar">
              <Bot size={24} />
            </div>

            <div className="ai-chat-heading-text">
              <h2>SMART ERP Assistant</h2>

              <span className="ai-online">
                <span className="ai-online-dot" />
                {isTyping ? "Thinking…" : "Local AI Assistant"}
              </span>
            </div>

            {mode === "full" && <button
              type="button"
              className="ai-reset-btn"
              onClick={handleReset}
              title="Start a new conversation"
              aria-label="Start a new conversation"
            >
              <RotateCcw size={17} />
            </button>}
            {mode === "compact" && <>
              <button type="button" className="ai-compact-action" onClick={handleReset} title="Refresh conversation" aria-label="Refresh conversation"><RotateCcw size={16} /></button>
              <button type="button" className="ai-compact-action ai-compact-expand" onClick={onExpand} title="Expand to full screen" aria-label="Expand to full screen"><Maximize2 size={15} /><span>Expand</span></button>
              <button type="button" className="ai-compact-close" onClick={onClose} aria-label="Close chat"><X size={19} /></button>
            </>}
          </div>

          <div
            className="ai-messages"
            role="log"
            aria-label="Chat messages"
            aria-live="polite"
          >
            {messages.map((message) => (
              <div
                key={message.id}
                className={`message-row ${
                  message.sender === "user"
                    ? "user-message-row"
                    : "ai-message-row"
                }`}
              >
                {message.sender !== "user" && (
                  <div className="message-avatar">
                    {message.source === "error" ? (
                      <AlertCircle size={18} />
                    ) : (
                      <Bot size={18} />
                    )}
                  </div>
                )}

                <div
                  className={`message-bubble ${
                    message.sender === "user"
                      ? "user-bubble"
                      : "ai-bubble"
                  }`}
                >
                  {message.sender === "user" ? (
                    message.text
                  ) : (
                    <AssistantContent message={message} onSend={handleSend} />
                  )}
                </div>
              </div>
            ))}

            {isTyping && (
              <div className="message-row ai-message-row">
                <div className="message-avatar">
                  <Bot size={18} />
                </div>

                <div
                  className="message-bubble ai-bubble ai-typing"
                  aria-label={thinkingStatus}
                  role="status"
                >
                  <span />
                  <span />
                  <span />
                  <small>{thinkingStatus}</small>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {error && (
            <p className="ai-error-message" role="alert">
              {error}
            </p>
          )}

          {mode === "full" && <div className="ai-suggestions">
            {SUGGESTION_GROUPS.map((group) => {
              const Icon = group.icon;
              return <details className="ai-suggestion-group" key={group.label}><summary><Icon size={15} />{group.label}</summary><div>{group.questions.filter((question) => question.trim()).map((question) => <button type="button" key={question} onClick={() => handleSend(question)} disabled={isTyping}>{question}</button>)}</div></details>;
            })}
          </div>}

          <form
            className="ai-input-area"
            onSubmit={(event) => {
              event.preventDefault();
              handleSend();
            }}
          >
            <textarea
              ref={textareaRef}
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleKeyDown}
              placeholder={mode === "compact" ? "Ask me anything about SMART ERP..." : "Ask about students, marksheets, TCs, or certificates..."}
              rows={1}
              aria-label="Message the SMART ERP Assistant"
              disabled={isTyping}
            />

            <button
              type="submit"
              className="ai-send-btn"
              disabled={!input.trim() || isTyping}
              aria-label="Send message"
              title="Send message"
            >
              <Send size={18} />
            </button>
          </form>

          {mode === "full" && <p className="ai-footer-text">SMART ERP AI Assistant · Powered by local Ollama</p>}
        </main>
        </div>
      </div>
    </div>
  );
}

export function AIAssistantDock({ user, currentPage, onExpand }) {
  const { compactOpen, setCompactOpen } = useAIConversation();
  if (!user || currentPage === "aiAssistant") return null;
  const dashboardDockClass = currentPage === "dashboard" ? " is-dashboard-dock" : "";
  return (
    <>
      {!compactOpen && <button type="button" className={`ai-floating-launcher${dashboardDockClass}`} onClick={() => setCompactOpen(true)} aria-label="Open SMART ERP Assistant"><Bot size={19} /><span>AI Assistant</span></button>}
      {compactOpen && <div className={`ai-floating-panel${dashboardDockClass}`}><AIAssistant mode="compact" onClose={() => setCompactOpen(false)} onExpand={onExpand} /></div>}
    </>
  );
}
