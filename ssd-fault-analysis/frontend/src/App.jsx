/**
 * frontend/src/App.jsx
 * ====================
 * SSD 지능형 결함 분석 시스템 - 메인 UI 컴포넌트
 *
 * 주요 기능:
 *  1. 자연어 질문 입력 폼
 *  2. 백엔드 /api/analyze 엔드포인트로 POST 요청 전송
 *  3. Fetch API의 ReadableStream + TextDecoder 를 사용하여
 *     스트리밍 응답을 타자 치듯 실시간 렌더링
 *  4. 분석 상태 표시 (대기 / 분석중 / 완료 / 에러)
 */

import { useState, useRef, useEffect, useCallback } from "react";

// ─────────────────────────────────────────────────────────────
// 스타일 상수 (인라인 스타일 - 외부 CSS 파일 없이 완결)
// ─────────────────────────────────────────────────────────────
const STYLES = {
  // 전체 화면 컨테이너
  app: {
    minHeight: "100vh",
    backgroundColor: "#0f172a",        // 어두운 네이비 배경
    color: "#e2e8f0",
    fontFamily: "'Pretendard', 'Noto Sans KR', 'Segoe UI', sans-serif",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    padding: "40px 20px",
    boxSizing: "border-box",
  },

  // 헤더
  header: {
    textAlign: "center",
    marginBottom: "32px",
  },
  title: {
    fontSize: "1.8rem",
    fontWeight: 700,
    color: "#38bdf8",                  // 하늘색 강조
    margin: 0,
    letterSpacing: "-0.5px",
  },
  subtitle: {
    fontSize: "0.9rem",
    color: "#64748b",
    marginTop: "8px",
  },

  // 카드 컨테이너
  card: {
    width: "100%",
    maxWidth: "860px",
    backgroundColor: "#1e293b",
    borderRadius: "12px",
    border: "1px solid #334155",
    padding: "28px",
    boxSizing: "border-box",
    marginBottom: "20px",
  },

  // 입력 폼 영역
  formRow: {
    display: "flex",
    gap: "10px",
    alignItems: "flex-start",
  },
  textarea: {
    flex: 1,
    backgroundColor: "#0f172a",
    border: "1px solid #475569",
    borderRadius: "8px",
    color: "#e2e8f0",
    fontSize: "0.95rem",
    padding: "12px 14px",
    resize: "vertical",
    minHeight: "80px",
    outline: "none",
    lineHeight: 1.6,
    fontFamily: "inherit",
    transition: "border-color 0.2s",
  },

  // 분석 버튼
  buttonBase: {
    padding: "12px 24px",
    borderRadius: "8px",
    border: "none",
    fontWeight: 600,
    fontSize: "0.95rem",
    cursor: "pointer",
    transition: "opacity 0.2s, transform 0.1s",
    whiteSpace: "nowrap",
    alignSelf: "flex-end",
  },
  buttonActive: {
    backgroundColor: "#0ea5e9",
    color: "#fff",
  },
  buttonDisabled: {
    backgroundColor: "#334155",
    color: "#64748b",
    cursor: "not-allowed",
  },

  // 옵션 행
  optionRow: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    marginTop: "12px",
    fontSize: "0.85rem",
    color: "#94a3b8",
  },
  select: {
    backgroundColor: "#0f172a",
    border: "1px solid #475569",
    borderRadius: "6px",
    color: "#e2e8f0",
    padding: "4px 8px",
    fontSize: "0.85rem",
    outline: "none",
  },

  // 상태 배지
  badge: (status) => ({
    display: "inline-block",
    padding: "3px 10px",
    borderRadius: "20px",
    fontSize: "0.78rem",
    fontWeight: 600,
    backgroundColor: {
      idle:      "#1e293b",
      loading:   "#1d4ed8",
      streaming: "#065f46",
      done:      "#064e3b",
      error:     "#7f1d1d",
    }[status] || "#1e293b",
    color: {
      idle:      "#64748b",
      loading:   "#93c5fd",
      streaming: "#6ee7b7",
      done:      "#34d399",
      error:     "#fca5a5",
    }[status] || "#94a3b8",
    border: "1px solid transparent",
  }),
  badgeLabel: (status) => ({
    idle:      "대기",
    loading:   "SQL 생성 중...",
    streaming: "분석 스트리밍 중...",
    done:      "분석 완료",
    error:     "오류 발생",
  })[status] || "대기",

  // 결과 출력 영역
  resultBox: {
    backgroundColor: "#0f172a",
    border: "1px solid #1e3a5f",
    borderRadius: "8px",
    padding: "16px",
    minHeight: "200px",
    maxHeight: "520px",
    overflowY: "auto",
    whiteSpace: "pre-wrap",           // 줄바꿈 보존
    fontSize: "0.88rem",
    lineHeight: 1.75,
    color: "#cbd5e1",
    fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace",
  },

  // 커서 깜빡임 애니메이션 (CSS keyframe 대신 JS로 처리)
  cursor: {
    display: "inline-block",
    width: "2px",
    height: "1em",
    backgroundColor: "#38bdf8",
    marginLeft: "2px",
    verticalAlign: "middle",
    animation: "blink 1s step-end infinite",
  },

  // 예시 질문 칩
  exampleChip: {
    display: "inline-block",
    padding: "5px 12px",
    backgroundColor: "#1e293b",
    border: "1px solid #334155",
    borderRadius: "20px",
    fontSize: "0.82rem",
    color: "#94a3b8",
    cursor: "pointer",
    transition: "background-color 0.2s, color 0.2s",
    margin: "3px",
  },

  clearButton: {
    padding: "5px 12px",
    backgroundColor: "transparent",
    border: "1px solid #475569",
    borderRadius: "6px",
    color: "#94a3b8",
    fontSize: "0.82rem",
    cursor: "pointer",
  },
};

// 예시 질문 목록
const EXAMPLE_QUESTIONS = [
  "최근 온도가 상승한 원인이 뭐야?",
  "어제 에러 횟수가 급증한 장치는?",
  "READ 커맨드 오류가 많은 구간을 찾아줘",
  "온도 70도 이상이고 에러가 5회 이상인 로그 보여줘",
  "최근 24시간 동안 비정상 패턴이 있는 장치 알려줘",
];

// ─────────────────────────────────────────────────────────────
// 커서 깜빡임 전역 CSS (style 태그로 삽입)
// ─────────────────────────────────────────────────────────────
const GlobalStyle = () => (
  <style>{`
    @keyframes blink {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0; }
    }
    * { box-sizing: border-box; }
    textarea:focus { border-color: #38bdf8 !important; }
    body { margin: 0; }
  `}</style>
);

// ─────────────────────────────────────────────────────────────
// 메인 컴포넌트
// ─────────────────────────────────────────────────────────────
export default function App() {
  // 입력 상태
  const [question, setQuestion]     = useState("");
  const [windowSize, setWindowSize] = useState(15);

  // 분석 결과 / 상태
  const [result, setResult]   = useState("");  // 스트리밍 누적 텍스트
  const [status, setStatus]   = useState("idle"); // idle | loading | streaming | done | error

  // 스트롤 자동 하단 이동용 ref
  const resultBoxRef = useRef(null);
  // AbortController: 스트리밍 중 취소 가능
  const abortCtrlRef = useRef(null);

  // 결과 박스 자동 스크롤
  useEffect(() => {
    if (resultBoxRef.current) {
      resultBoxRef.current.scrollTop = resultBoxRef.current.scrollHeight;
    }
  }, [result]);

  // ── 분석 실행 함수 ──────────────────────────────────────
  const handleAnalyze = useCallback(async () => {
    if (!question.trim() || status === "loading" || status === "streaming") return;

    // 이전 스트림 취소
    if (abortCtrlRef.current) abortCtrlRef.current.abort();
    abortCtrlRef.current = new AbortController();

    setResult("");
    setStatus("loading");

    try {
      // POST /api/analyze 요청
      const response = await fetch("/api/analyze", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ question: question.trim(), window_size: windowSize }),
        signal:  abortCtrlRef.current.signal,
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(errData.detail || `HTTP ${response.status}`);
      }

      // ReadableStream으로 스트리밍 응답 처리
      const reader  = response.body.getReader();
      const decoder = new TextDecoder("utf-8");  // UTF-8 한글 디코딩
      setStatus("streaming");

      // 청크를 순서대로 읽어 화면에 누적
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // Uint8Array → UTF-8 문자열 디코딩 (stream: true로 멀티바이트 경계 처리)
        const chunk = decoder.decode(value, { stream: true });
        setResult((prev) => prev + chunk);
      }

      // 스트림 끝 후 잔여 바이트 플러시
      const tail = decoder.decode();
      if (tail) setResult((prev) => prev + tail);

      setStatus("done");
    } catch (err) {
      if (err.name === "AbortError") {
        // 사용자 취소
        setStatus("idle");
        setResult((prev) => prev + "\n\n[사용자에 의해 중단됨]");
      } else {
        console.error("[분석 오류]", err);
        setResult(`오류: ${err.message}`);
        setStatus("error");
      }
    }
  }, [question, windowSize, status]);

  // ── 취소 함수 ──────────────────────────────────────────
  const handleCancel = () => {
    if (abortCtrlRef.current) abortCtrlRef.current.abort();
  };

  // ── Enter(Ctrl+Enter) 단축키 ────────────────────────────
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
      <div style={STYLES.app}>

        {/* ── 헤더 ── */}
        <header style={STYLES.header}>
          <h1 style={STYLES.title}>⚙️ SSD 지능형 결함 분석 시스템</h1>
          <p style={STYLES.subtitle}>
            로컬 LLM(qwen2.5:7b) + RAG 기반 · 100% 오프라인 환경
          </p>
        </header>

        {/* ── 입력 카드 ── */}
        <section style={STYLES.card}>

          {/* 예시 질문 칩 */}
          <div style={{ marginBottom: "12px" }}>
            <span style={{ fontSize: "0.82rem", color: "#64748b", marginRight: "8px" }}>
              예시 질문:
            </span>
            {EXAMPLE_QUESTIONS.map((q) => (
              <span
                key={q}
                style={STYLES.exampleChip}
                onClick={() => setQuestion(q)}
                onMouseEnter={(e) => {
                  e.target.style.backgroundColor = "#334155";
                  e.target.style.color = "#e2e8f0";
                }}
                onMouseLeave={(e) => {
                  e.target.style.backgroundColor = "#1e293b";
                  e.target.style.color = "#94a3b8";
                }}
              >
                {q}
              </span>
            ))}
          </div>

          {/* 질문 입력 + 버튼 */}
          <div style={STYLES.formRow}>
            <textarea
              style={STYLES.textarea}
              placeholder="자연어 질문을 입력하세요. 예: '최근 온도가 상승한 원인이 뭐야?'  (Ctrl+Enter 로 분석)"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isRunning}
            />
            <button
              style={{
                ...STYLES.buttonBase,
                ...(isRunning ? STYLES.buttonDisabled : STYLES.buttonActive),
              }}
              onClick={isRunning ? handleCancel : handleAnalyze}
              disabled={!isRunning && !question.trim()}
            >
              {isRunning ? "중단" : "분석 시작"}
            </button>
          </div>

          {/* 옵션 행 */}
          <div style={STYLES.optionRow}>
            <label htmlFor="windowSize">윈도우 크기:</label>
            <select
              id="windowSize"
              style={STYLES.select}
              value={windowSize}
              onChange={(e) => setWindowSize(Number(e.target.value))}
              disabled={isRunning}
            >
              {[10, 15, 20, 30, 50].map((n) => (
                <option key={n} value={n}>{n}행</option>
              ))}
            </select>

            {/* 상태 배지 */}
            <span style={STYLES.badge(status)}>
              {STYLES.badgeLabel(status)}
            </span>

            {/* 결과 초기화 버튼 */}
            {result && !isRunning && (
              <button
                style={STYLES.clearButton}
                onClick={() => { setResult(""); setStatus("idle"); }}
              >
                초기화
              </button>
            )}
          </div>
        </section>

        {/* ── 결과 카드 ── */}
        <section style={{ ...STYLES.card, flex: 1 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
            }}
          >
            <span style={{ fontSize: "0.85rem", color: "#64748b" }}>
              분석 결과
            </span>
            {result && (
              <span style={{ fontSize: "0.78rem", color: "#475569" }}>
                {result.length.toLocaleString()}자
              </span>
            )}
          </div>

          <div ref={resultBoxRef} style={STYLES.resultBox}>
            {/* 결과 텍스트 */}
            {result || (
              <span style={{ color: "#334155" }}>
                질문을 입력하고 [분석 시작]을 누르면 결과가 여기에 실시간으로 표시됩니다.
              </span>
            )}

            {/* 스트리밍 중 커서 깜빡임 */}
            {status === "streaming" && (
              <span style={STYLES.cursor} aria-hidden="true" />
            )}
          </div>
        </section>

        {/* ── 푸터 ── */}
        <footer style={{ color: "#334155", fontSize: "0.75rem", marginTop: "12px" }}>
          SSD Fault Analysis System · 100% Local Offline · Powered by Ollama + LangChain
        </footer>

      </div>
    </>
  );
}
