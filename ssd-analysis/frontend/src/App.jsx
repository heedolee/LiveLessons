/**
 * frontend/src/App.jsx
 * ====================
 * SSD 지능형 결함 분석 시스템 — 채팅형 스트리밍 UI
 *
 * 기능:
 *  1. 채팅 히스토리 (사용자 / AI 메시지 구분)
 *  2. POST /api/analyze → ReadableStream + TextDecoder 실시간 렌더링
 *  3. 윈도우 크기 선택, 스트리밍 중단, 히스토리 초기화
 *  4. 서버 상태 확인 (/api/health) 및 배지 표시
 */

import { useState, useRef, useEffect, useCallback } from "react";

/* ─── 전역 CSS ─────────────────────────────────────────────── */
const G = () => (
  <style>{`
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #0f172a; font-family: 'Pretendard','Noto Sans KR','Segoe UI',sans-serif; }
    @keyframes blink  { 0%,100%{opacity:1} 50%{opacity:0} }
    @keyframes fadeUp { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:translateY(0)} }
    @keyframes spin   { to{transform:rotate(360deg)} }
    textarea:focus { border-color:#38bdf8!important; outline:none; }
    ::-webkit-scrollbar       { width:5px }
    ::-webkit-scrollbar-track { background:#1e293b }
    ::-webkit-scrollbar-thumb { background:#334155; border-radius:3px }
  `}</style>
);

/* ─── 색상 ──────────────────────────────────────────────────── */
const C = {
  bg:      "#0f172a", surface:"#1e293b", border:"#334155",
  muted:   "#64748b", text:"#e2e8f0",   sub:"#94a3b8",
  accent:  "#38bdf8", accentBg:"#0ea5e9",
  userBg:  "#1d4ed8", aiBg:"#1e293b",
  errBg:   "#7f1d1d", err:"#fca5a5",
};

/* ─── 예시 질문 ──────────────────────────────────────────────── */
const EXAMPLES = [
  "최근 온도가 상승한 원인이 뭐야?",
  "어제 에러 횟수가 급증한 장치는?",
  "READ 커맨드 오류가 많은 구간 찾아줘",
  "온도 70도 이상 + 에러 5회 이상인 로그",
  "최근 24시간 비정상 패턴이 있는 장치",
];

/* ─── 상태 배지 ──────────────────────────────────────────────── */
const STATUS = {
  idle     : { label:"대기",           bg:"#1e293b", col:"#64748b" },
  loading  : { label:"SQL 생성 중…",   bg:"#1d4ed8", col:"#93c5fd" },
  streaming: { label:"분석 스트리밍…", bg:"#065f46", col:"#6ee7b7" },
  done     : { label:"완료",           bg:"#064e3b", col:"#34d399" },
  error    : { label:"오류",           bg:"#7f1d1d", col:"#fca5a5" },
};

function Badge({ s }) {
  const st = STATUS[s] || STATUS.idle;
  const spin = s === "loading" || s === "streaming";
  return (
    <span style={{
      padding:"3px 10px", borderRadius:"20px", fontSize:"0.77rem",
      fontWeight:600, backgroundColor:st.bg, color:st.col, userSelect:"none",
    }}>
      {spin && <span style={{
        display:"inline-block", width:"7px", height:"7px",
        border:`2px solid ${st.col}`, borderTop:"2px solid transparent",
        borderRadius:"50%", marginRight:"5px", verticalAlign:"middle",
        animation:"spin 0.8s linear infinite",
      }}/>}
      {st.label}
    </span>
  );
}

/* ─── 채팅 말풍선 ────────────────────────────────────────────── */
function Bubble({ msg }) {
  const isUser = msg.role === "user";
  return (
    <div style={{
      display:"flex", justifyContent:isUser?"flex-end":"flex-start",
      marginBottom:"14px", animation:"fadeUp 0.2s ease",
    }}>
      {!isUser && (
        <div style={{
          width:30, height:30, borderRadius:"50%", backgroundColor:C.accentBg,
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:"13px", flexShrink:0, marginRight:8, marginTop:2,
        }}>⚙</div>
      )}
      <div style={{
        maxWidth:"80%",
        backgroundColor: isUser ? C.userBg : C.aiBg,
        color: C.text,
        borderRadius: isUser ? "16px 16px 4px 16px" : "4px 16px 16px 16px",
        border:`1px solid ${isUser ? "transparent" : C.border}`,
        padding:"11px 15px", fontSize:"0.88rem", lineHeight:1.75,
        whiteSpace:"pre-wrap", wordBreak:"break-word",
        fontFamily: msg.role==="assistant"
          ? "'JetBrains Mono','Fira Code',monospace" : "inherit",
      }}>
        {msg.content}
        {msg.streaming && (
          <span style={{
            display:"inline-block", width:"2px", height:"1em",
            backgroundColor:C.accent, marginLeft:3, verticalAlign:"middle",
            animation:"blink 1s step-end infinite",
          }}/>
        )}
      </div>
      {isUser && (
        <div style={{
          width:30, height:30, borderRadius:"50%", backgroundColor:"#1d4ed8",
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:"13px", flexShrink:0, marginLeft:8, marginTop:2,
        }}>👤</div>
      )}
    </div>
  );
}

/* ─── 메인 컴포넌트 ──────────────────────────────────────────── */
export default function App() {
  const [messages,   setMessages]   = useState([]);
  const [question,   setQuestion]   = useState("");
  const [windowSize, setWindowSize] = useState(15);
  const [status,     setStatus]     = useState("idle");
  const [health,     setHealth]     = useState(null); // null|"ok"|"error"

  const bottomRef  = useRef(null);
  const abortRef   = useRef(null);

  /* 서버 헬스 체크 (초기 1회) */
  useEffect(() => {
    fetch("/api/health")
      .then(r => r.json())
      .then(d => setHealth(d.status === "ok" ? "ok" : "error"))
      .catch(() => setHealth("error"));
  }, []);

  /* 자동 스크롤 */
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /* ── 분석 실행 ─────────────────────────────────────────────── */
  const handleAnalyze = useCallback(async () => {
    const q = question.trim();
    if (!q || status === "loading" || status === "streaming") return;

    abortRef.current?.abort();
    abortRef.current = new AbortController();

    const uid   = Date.now();
    const aiId  = uid + 1;

    setMessages(prev => [
      ...prev,
      { id: uid,  role:"user",      content: q },
      { id: aiId, role:"assistant", content:"", streaming:true },
    ]);
    setQuestion("");
    setStatus("loading");

    try {
      const resp = await fetch("/api/analyze", {
        method : "POST",
        headers: { "Content-Type":"application/json" },
        body   : JSON.stringify({ question: q, window_size: windowSize }),
        signal : abortRef.current.signal,
      });

      if (!resp.ok) {
        const e = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(e.detail || `HTTP ${resp.status}`);
      }

      const reader  = resp.body.getReader();
      const decoder = new TextDecoder("utf-8"); // 한글 멀티바이트 안전 처리
      setStatus("streaming");

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true }); // stream:true → 경계 처리
        setMessages(prev =>
          prev.map(m => m.id === aiId ? { ...m, content: m.content + chunk } : m)
        );
      }
      // 잔여 바이트 플러시
      const tail = decoder.decode();
      if (tail) setMessages(prev =>
        prev.map(m => m.id === aiId ? { ...m, content: m.content + tail } : m)
      );

      setMessages(prev =>
        prev.map(m => m.id === aiId ? { ...m, streaming: false } : m)
      );
      setStatus("done");

    } catch (err) {
      if (err.name === "AbortError") {
        setMessages(prev =>
          prev.map(m => m.id === aiId
            ? { ...m, content: m.content + "\n\n[중단됨]", streaming: false }
            : m)
        );
        setStatus("idle");
      } else {
        console.error(err);
        setMessages(prev =>
          prev.map(m => m.id === aiId
            ? { ...m, content: `오류: ${err.message}`, streaming: false }
            : m)
        );
        setStatus("error");
      }
    }
  }, [question, windowSize, status]);

  const handleCancel = () => abortRef.current?.abort();
  const handleClear  = () => { handleCancel(); setMessages([]); setStatus("idle"); };
  const handleKey    = e => { if (e.key==="Enter" && (e.ctrlKey||e.metaKey)) { e.preventDefault(); handleAnalyze(); } };

  const isRunning = status === "loading" || status === "streaming";

  /* ─── 렌더 ───────────────────────────────────────────────── */
  return (
    <>
      <G />
      <div style={{ height:"100vh", display:"flex", flexDirection:"column", backgroundColor:C.bg, color:C.text }}>

        {/* ── 헤더 ── */}
        <header style={{
          padding:"12px 20px", borderBottom:`1px solid ${C.border}`,
          backgroundColor:C.surface, display:"flex",
          alignItems:"center", justifyContent:"space-between", flexShrink:0,
        }}>
          <div style={{ display:"flex", alignItems:"center", gap:10 }}>
            <span style={{ fontSize:"1.25rem" }}>⚙️</span>
            <div>
              <div style={{ fontWeight:700, fontSize:"0.98rem", color:C.accent }}>
                SSD 지능형 결함 분석 시스템
              </div>
              <div style={{ fontSize:"0.72rem", color:C.muted }}>
                SQLAlchemy ORM · OpenAI gpt-4o · LangChain LCEL · Chroma RAG
              </div>
            </div>
          </div>
          <div style={{ display:"flex", alignItems:"center", gap:10 }}>
            {/* 서버 헬스 표시 */}
            {health && (
              <span style={{
                fontSize:"0.75rem", padding:"2px 8px", borderRadius:"12px",
                backgroundColor: health==="ok" ? "#064e3b" : C.errBg,
                color: health==="ok" ? "#34d399" : C.err,
              }}>
                서버 {health==="ok" ? "정상" : "오류"}
              </span>
            )}
            <Badge s={status} />
            {messages.length > 0 && (
              <button onClick={handleClear} style={{
                padding:"4px 10px", backgroundColor:"transparent",
                border:`1px solid ${C.border}`, borderRadius:6,
                color:C.sub, fontSize:"0.78rem", cursor:"pointer",
              }}>초기화</button>
            )}
          </div>
        </header>

        {/* ── 채팅 영역 ── */}
        <div style={{
          flex:1, overflowY:"auto", padding:"20px 16px",
          maxWidth:900, width:"100%", margin:"0 auto",
          alignSelf:"stretch",
        }}>
          {messages.length === 0 && (
            <div style={{ textAlign:"center", marginTop:50 }}>
              <div style={{ fontSize:"2.2rem", marginBottom:10 }}>⚙️</div>
              <div style={{ color:C.sub, fontSize:"0.9rem", marginBottom:20 }}>
                SSD 로그를 자연어로 질문하면 AI가 결함을 분석합니다.
              </div>
              <div style={{ display:"flex", flexWrap:"wrap", gap:8, justifyContent:"center" }}>
                {EXAMPLES.map(ex => (
                  <button key={ex} onClick={() => setQuestion(ex)} style={{
                    padding:"6px 14px", backgroundColor:C.surface,
                    border:`1px solid ${C.border}`, borderRadius:20,
                    color:C.sub, fontSize:"0.82rem", cursor:"pointer",
                  }}
                  onMouseEnter={e=>{e.target.style.backgroundColor=C.border;e.target.style.color=C.text;}}
                  onMouseLeave={e=>{e.target.style.backgroundColor=C.surface;e.target.style.color=C.sub;}}
                  >{ex}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map(msg => <Bubble key={msg.id} msg={msg} />)}
          <div ref={bottomRef} />
        </div>

        {/* ── 입력 영역 ── */}
        <div style={{
          borderTop:`1px solid ${C.border}`, backgroundColor:C.surface,
          padding:"12px 16px", flexShrink:0,
        }}>
          <div style={{ maxWidth:900, margin:"0 auto" }}>
            {/* 옵션 행 */}
            <div style={{ display:"flex", alignItems:"center", gap:10, marginBottom:8, fontSize:"0.8rem", color:C.muted }}>
              <label htmlFor="ws">윈도우:</label>
              <select id="ws" value={windowSize} onChange={e => setWindowSize(Number(e.target.value))}
                disabled={isRunning}
                style={{ backgroundColor:C.bg, border:`1px solid ${C.border}`, borderRadius:5,
                  color:C.text, padding:"3px 8px", fontSize:"0.8rem", outline:"none" }}>
                {[10,15,20,30,50].map(n => <option key={n} value={n}>{n}행</option>)}
              </select>
              <span style={{ color:C.muted, fontSize:"0.73rem" }}>Ctrl+Enter 전송</span>
            </div>

            {/* 텍스트 입력 + 버튼 */}
            <div style={{ display:"flex", gap:8 }}>
              <textarea
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={handleKey}
                disabled={isRunning}
                rows={2}
                placeholder="자연어 질문 입력 (예: 최근 온도가 상승한 원인이 뭐야?)"
                style={{
                  flex:1, backgroundColor:C.bg, border:`1px solid ${C.border}`,
                  borderRadius:10, color:C.text, fontSize:"0.91rem",
                  padding:"10px 14px", resize:"none", lineHeight:1.5,
                  fontFamily:"inherit", transition:"border-color 0.2s",
                }}
              />
              <button
                onClick={isRunning ? handleCancel : handleAnalyze}
                disabled={!isRunning && !question.trim()}
                style={{
                  padding:"10px 18px", borderRadius:10, border:"none",
                  fontWeight:600, fontSize:"0.88rem", alignSelf:"flex-end",
                  cursor: (!isRunning && !question.trim()) ? "not-allowed" : "pointer",
                  backgroundColor: isRunning ? "#7f1d1d"
                    : (!question.trim() ? C.border : C.accentBg),
                  color: isRunning ? C.err : "#fff",
                  transition:"background-color 0.2s", whiteSpace:"nowrap",
                }}
              >
                {isRunning ? "⏹ 중단" : "▶ 분석"}
              </button>
            </div>

            {/* 글자 수 */}
            {question.length > 0 && (
              <div style={{
                textAlign:"right", fontSize:"0.72rem", marginTop:4,
                color: question.length > 450 ? C.err : C.muted,
              }}>
                {question.length} / 500
              </div>
            )}
          </div>
        </div>

      </div>
    </>
  );
}
