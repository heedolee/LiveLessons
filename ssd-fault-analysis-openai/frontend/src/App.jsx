/**
 * frontend/src/App.jsx  (OpenAI 버전)
 * =====================================
 * SSD 지능형 결함 분석 시스템 - 채팅 형태 UI
 *
 * 주요 기능:
 *  1. 채팅 메시지 히스토리 (사용자 / AI 구분)
 *  2. 자연어 질문 입력 폼 (예시 질문 칩 포함)
 *  3. Fetch API ReadableStream + TextDecoder 로 gpt-4o 스트리밍 응답 실시간 렌더링
 *  4. 윈도우 크기 선택, 스트리밍 중단, 히스토리 초기화 기능
 *  5. 분석 상태 배지 및 커서 깜빡임 애니메이션
 */

import { useState, useRef, useEffect, useCallback } from "react";

// ─────────────────────────────────────────────────────────────
// 전역 CSS (keyframe 애니메이션 등 인라인으로 처리 불가능한 것만)
// ─────────────────────────────────────────────────────────────
const GlobalStyle = () => (
  <style>{`
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background-color: #0f172a; }
    @keyframes blink  { 0%,100%{opacity:1} 50%{opacity:0} }
    @keyframes fadeIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
    @keyframes spin   { to{transform:rotate(360deg)} }
    textarea:focus    { border-color: #38bdf8 !important; outline: none; }
    ::-webkit-scrollbar       { width: 6px; }
    ::-webkit-scrollbar-track { background: #1e293b; }
    ::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  `}</style>
);

// ─────────────────────────────────────────────────────────────
// 색상 팔레트
// ─────────────────────────────────────────────────────────────
const C = {
  bg:       "#0f172a",
  surface:  "#1e293b",
  border:   "#334155",
  muted:    "#64748b",
  text:     "#e2e8f0",
  textSub:  "#94a3b8",
  accent:   "#38bdf8",
  accentBg: "#0ea5e9",
  userBg:   "#1d4ed8",
  aiBg:     "#1e293b",
  error:    "#fca5a5",
  errorBg:  "#7f1d1d",
};

// ─────────────────────────────────────────────────────────────
// 예시 질문 목록
// ─────────────────────────────────────────────────────────────
const EXAMPLES = [
  "최근 온도가 상승한 원인이 뭐야?",
  "어제 에러 횟수가 급증한 장치는?",
  "READ 커맨드 오류가 많은 구간을 찾아줘",
  "온도 70도 이상 + 에러 5회 이상 로그 보여줘",
  "최근 24시간 비정상 패턴이 있는 장치 알려줘",
];

// ─────────────────────────────────────────────────────────────
// 채팅 메시지 컴포넌트
// ─────────────────────────────────────────────────────────────
function ChatMessage({ msg }) {
  const isUser = msg.role === "user";

  return (
    <div
      style={{
        display:       "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        marginBottom:  "16px",
        animation:     "fadeIn 0.25s ease",
      }}
    >
      {/* AI 아바타 */}
      {!isUser && (
        <div
          style={{
            width:           "32px",
            height:          "32px",
            borderRadius:    "50%",
            backgroundColor: C.accentBg,
            display:         "flex",
            alignItems:      "center",
            justifyContent:  "center",
            fontSize:        "14px",
            flexShrink:      0,
            marginRight:     "10px",
            marginTop:       "2px",
          }}
        >
          ⚙
        </div>
      )}

      {/* 말풍선 */}
      <div
        style={{
          maxWidth:        "78%",
          backgroundColor: isUser ? C.userBg : C.aiBg,
          color:           C.text,
          borderRadius:    isUser ? "18px 18px 4px 18px" : "4px 18px 18px 18px",
          border:          `1px solid ${isUser ? "transparent" : C.border}`,
          padding:         "12px 16px",
          fontSize:        "0.9rem",
          lineHeight:      1.7,
          whiteSpace:      "pre-wrap",
          wordBreak:       "break-word",
          fontFamily:      msg.role === "assistant"
            ? "'JetBrains Mono','Fira Code','Consolas',monospace"
            : "inherit",
        }}
      >
        {msg.content}
        {/* 스트리밍 중 커서 */}
        {msg.streaming && (
          <span
            style={{
              display:         "inline-block",
              width:           "2px",
              height:          "1em",
              backgroundColor: C.accent,
              marginLeft:      "3px",
              verticalAlign:   "middle",
              animation:       "blink 1s step-end infinite",
            }}
          />
        )}
      </div>

      {/* 사용자 아바타 */}
      {isUser && (
        <div
          style={{
            width:           "32px",
            height:          "32px",
            borderRadius:    "50%",
            backgroundColor: "#1d4ed8",
            display:         "flex",
            alignItems:      "center",
            justifyContent:  "center",
            fontSize:        "14px",
            flexShrink:      0,
            marginLeft:      "10px",
            marginTop:       "2px",
          }}
        >
          👤
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────
// 상태 배지
// ─────────────────────────────────────────────────────────────
const STATUS_MAP = {
  idle:      { label: "대기",           bg: C.surface, color: C.muted    },
  loading:   { label: "SQL 생성 중...", bg: "#1d4ed8", color: "#93c5fd"  },
  streaming: { label: "분석 중...",     bg: "#065f46", color: "#6ee7b7"  },
  done:      { label: "완료",           bg: "#064e3b", color: "#34d399"  },
  error:     { label: "오류",           bg: C.errorBg, color: C.error    },
};

function StatusBadge({ status }) {
  const s = STATUS_MAP[status] || STATUS_MAP.idle;
  return (
    <span
      style={{
        padding:         "3px 10px",
        borderRadius:    "20px",
        fontSize:        "0.78rem",
        fontWeight:      600,
        backgroundColor: s.bg,
        color:           s.color,
        border:          `1px solid ${s.bg}`,
        userSelect:      "none",
      }}
    >
      {status === "loading" || status === "streaming" ? (
        <>
          <span
            style={{
              display:      "inline-block",
              width:        "8px",
              height:       "8px",
              border:       `2px solid ${s.color}`,
              borderTop:    "2px solid transparent",
              borderRadius: "50%",
              marginRight:  "5px",
              animation:    "spin 0.8s linear infinite",
              verticalAlign:"middle",
            }}
          />
          {s.label}
        </>
      ) : s.label}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────
// 메인 컴포넌트
// ─────────────────────────────────────────────────────────────
export default function App() {
  // 채팅 히스토리: [{id, role: 'user'|'assistant', content, streaming?}]
  const [messages, setMessages]     = useState([]);
  const [question, setQuestion]     = useState("");
  const [windowSize, setWindowSize] = useState(15);
  const [status, setStatus]         = useState("idle");

  const chatEndRef  = useRef(null);   // 자동 스크롤용
  const abortCtrlRef = useRef(null);  // 스트리밍 중단용
  const textareaRef  = useRef(null);

  // 채팅창 자동 스크롤 (메시지 추가·업데이트 시)
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── 분석 실행 ─────────────────────────────────────────────
  const handleAnalyze = useCallback(async () => {
    const q = question.trim();
    if (!q || status === "loading" || status === "streaming") return;

    // 이전 스트림 취소
    abortCtrlRef.current?.abort();
    abortCtrlRef.current = new AbortController();

    // 사용자 메시지 추가
    const userMsgId = Date.now();
    const aiMsgId   = userMsgId + 1;

    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: "user",      content: q },
      { id: aiMsgId,   role: "assistant", content: "", streaming: true },
    ]);
    setQuestion("");
    setStatus("loading");

    try {
      const resp = await fetch("/api/analyze", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ question: q, window_size: windowSize }),
        signal:  abortCtrlRef.current.signal,
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      // ── ReadableStream 처리 ────────────────────────────────
      const reader  = resp.body.getReader();
      // stream: true → 멀티바이트(한글) 문자 경계에서 안전하게 처리
      const decoder = new TextDecoder("utf-8");
      setStatus("streaming");

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });

        // AI 메시지에 청크 누적 (함수형 업데이트로 race condition 방지)
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiMsgId
              ? { ...m, content: m.content + chunk }
              : m
          )
        );
      }

      // 스트림 종료: 잔여 바이트 플러시
      const tail = decoder.decode();
      if (tail) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiMsgId
              ? { ...m, content: m.content + tail }
              : m
          )
        );
      }

      // streaming 플래그 해제 (커서 숨김)
      setMessages((prev) =>
        prev.map((m) =>
          m.id === aiMsgId ? { ...m, streaming: false } : m
        )
      );
      setStatus("done");

    } catch (err) {
      if (err.name === "AbortError") {
        // 사용자 중단
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiMsgId
              ? { ...m, content: m.content + "\n\n[사용자에 의해 중단됨]", streaming: false }
              : m
          )
        );
        setStatus("idle");
      } else {
        console.error("[분석 오류]", err);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiMsgId
              ? { ...m, content: `오류: ${err.message}`, streaming: false }
              : m
          )
        );
        setStatus("error");
      }
    }
  }, [question, windowSize, status]);

  // ── 중단 ──────────────────────────────────────────────────
  const handleCancel = () => abortCtrlRef.current?.abort();

  // ── 히스토리 초기화 ───────────────────────────────────────
  const handleClear = () => {
    if (status === "streaming") handleCancel();
    setMessages([]);
    setStatus("idle");
  };

  // ── 키보드 단축키 (Ctrl+Enter) ────────────────────────────
  const handleKeyDown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleAnalyze();
    }
  };

  const isRunning = status === "loading" || status === "streaming";

  // ─────────────────────────────────────────────────────────
  // 렌더
  // ─────────────────────────────────────────────────────────
  return (
    <>
      <GlobalStyle />
      <div
        style={{
          height:          "100vh",
          display:         "flex",
          flexDirection:   "column",
          backgroundColor: C.bg,
          color:           C.text,
          fontFamily:      "'Pretendard','Noto Sans KR','Segoe UI',sans-serif",
        }}
      >

        {/* ── 헤더 ── */}
        <header
          style={{
            padding:         "14px 24px",
            borderBottom:    `1px solid ${C.border}`,
            backgroundColor: C.surface,
            display:         "flex",
            alignItems:      "center",
            justifyContent:  "space-between",
            flexShrink:      0,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "1.3rem" }}>⚙️</span>
            <div>
              <div style={{ fontWeight: 700, fontSize: "1rem", color: C.accent }}>
                SSD 지능형 결함 분석 시스템
              </div>
              <div style={{ fontSize: "0.75rem", color: C.muted }}>
                OpenAI gpt-4o · LangChain LCEL · Chroma RAG
              </div>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <StatusBadge status={status} />
            {messages.length > 0 && (
              <button
                onClick={handleClear}
                style={{
                  padding:         "5px 12px",
                  backgroundColor: "transparent",
                  border:          `1px solid ${C.border}`,
                  borderRadius:    "6px",
                  color:           C.textSub,
                  fontSize:        "0.8rem",
                  cursor:          "pointer",
                }}
              >
                대화 초기화
              </button>
            )}
          </div>
        </header>

        {/* ── 채팅 히스토리 ── */}
        <div
          style={{
            flex:       1,
            overflowY:  "auto",
            padding:    "24px 16px",
            maxWidth:   "900px",
            width:      "100%",
            margin:     "0 auto",
            alignSelf:  "stretch",
            boxSizing:  "border-box",
          }}
        >
          {/* 초기 안내 메시지 */}
          {messages.length === 0 && (
            <div style={{ textAlign: "center", marginTop: "60px" }}>
              <div style={{ fontSize: "2.5rem", marginBottom: "12px" }}>⚙️</div>
              <div style={{ fontSize: "1rem", color: C.textSub, marginBottom: "24px" }}>
                SSD 로그를 자연어로 질문하면 AI가 결함을 분석합니다.
              </div>
              {/* 예시 질문 칩 */}
              <div
                style={{
                  display:        "flex",
                  flexWrap:       "wrap",
                  gap:            "8px",
                  justifyContent: "center",
                }}
              >
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    onClick={() => setQuestion(ex)}
                    style={{
                      padding:         "7px 14px",
                      backgroundColor: C.surface,
                      border:          `1px solid ${C.border}`,
                      borderRadius:    "20px",
                      color:           C.textSub,
                      fontSize:        "0.83rem",
                      cursor:          "pointer",
                      transition:      "background-color 0.15s",
                    }}
                    onMouseEnter={(e) => {
                      e.target.style.backgroundColor = C.border;
                      e.target.style.color = C.text;
                    }}
                    onMouseLeave={(e) => {
                      e.target.style.backgroundColor = C.surface;
                      e.target.style.color = C.textSub;
                    }}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* 메시지 목록 */}
          {messages.map((msg) => (
            <ChatMessage key={msg.id} msg={msg} />
          ))}

          {/* 자동 스크롤 앵커 */}
          <div ref={chatEndRef} />
        </div>

        {/* ── 입력 영역 ── */}
        <div
          style={{
            borderTop:       `1px solid ${C.border}`,
            backgroundColor: C.surface,
            padding:         "14px 16px",
            flexShrink:      0,
          }}
        >
          <div
            style={{
              maxWidth: "900px",
              margin:   "0 auto",
            }}
          >
            {/* 옵션 행 */}
            <div
              style={{
                display:        "flex",
                alignItems:     "center",
                gap:            "12px",
                marginBottom:   "10px",
                fontSize:       "0.82rem",
                color:          C.muted,
              }}
            >
              <label htmlFor="ws">윈도우:</label>
              <select
                id="ws"
                value={windowSize}
                onChange={(e) => setWindowSize(Number(e.target.value))}
                disabled={isRunning}
                style={{
                  backgroundColor: C.bg,
                  border:          `1px solid ${C.border}`,
                  borderRadius:    "5px",
                  color:           C.text,
                  padding:         "3px 8px",
                  fontSize:        "0.82rem",
                  outline:         "none",
                }}
              >
                {[10, 15, 20, 30, 50].map((n) => (
                  <option key={n} value={n}>{n}행</option>
                ))}
              </select>
              <span style={{ color: C.muted, fontSize: "0.75rem" }}>
                Ctrl+Enter 로 전송
              </span>
            </div>

            {/* 입력 폼 */}
            <div style={{ display: "flex", gap: "10px" }}>
              <textarea
                ref={textareaRef}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={handleKeyDown}
                disabled={isRunning}
                placeholder="자연어로 질문을 입력하세요. 예: '최근 온도가 상승한 원인이 뭐야?'"
                rows={2}
                style={{
                  flex:            1,
                  backgroundColor: C.bg,
                  border:          `1px solid ${C.border}`,
                  borderRadius:    "10px",
                  color:           C.text,
                  fontSize:        "0.92rem",
                  padding:         "10px 14px",
                  resize:          "none",
                  lineHeight:      1.5,
                  fontFamily:      "inherit",
                  transition:      "border-color 0.2s",
                }}
              />

              {/* 전송 / 중단 버튼 */}
              <button
                onClick={isRunning ? handleCancel : handleAnalyze}
                disabled={!isRunning && !question.trim()}
                style={{
                  padding:         "10px 20px",
                  borderRadius:    "10px",
                  border:          "none",
                  fontWeight:      600,
                  fontSize:        "0.9rem",
                  cursor:          (!isRunning && !question.trim()) ? "not-allowed" : "pointer",
                  backgroundColor: isRunning
                    ? "#7f1d1d"
                    : (!question.trim() ? C.border : C.accentBg),
                  color:           isRunning ? "#fca5a5" : "#fff",
                  transition:      "background-color 0.2s",
                  alignSelf:       "flex-end",
                  whiteSpace:      "nowrap",
                }}
              >
                {isRunning ? "⏹ 중단" : "▶ 분석"}
              </button>
            </div>

            {/* 문자 수 표시 */}
            {question.length > 0 && (
              <div
                style={{
                  textAlign: "right",
                  fontSize:  "0.73rem",
                  color:     question.length > 450 ? C.error : C.muted,
                  marginTop: "4px",
                }}
              >
                {question.length} / 500
              </div>
            )}
          </div>
        </div>

      </div>
    </>
  );
}
