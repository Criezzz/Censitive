import { useEffect, useRef } from "react";
import type { PanelProps } from "./PanelProps";
import { Shield } from "./Shield";
import { providerName } from "../data/providers";

export function ChatPanel({ model, actions, elements }: PanelProps) {
  const {
    provider,
    messages,
    phase,
    cancelPending,
    error,
    draft,
    session,
    providerConfirmed,
    draftSelection,
    selectionIssue,
    selectionNotice,
    selectionUndoAvailable,
    busy,
  } = model;
  const {
    clearError,
    selectDraft,
    protectSelection,
    undoSelection,
    changeDraft,
    prepare,
    cancelPrepare,
    cancel,
    reset,
  } = actions;
  const { bottom, input } = elements;
  const answered = messages.some((message) => message.role === "assistant");
  const conversation = useRef<HTMLElement>(null);
  const followLatest = useRef(true);
  useEffect(() => {
    followLatest.current = true;
  }, [session]);
  useEffect(() => {
    const container = bottom.current?.parentElement;
    if (!container) return;
    if (messages.length === 0) {
      container.scrollTop = 0;
      followLatest.current = true;
    } else if (followLatest.current)
      container.scrollTop = container.scrollHeight;
  }, [messages, bottom]);
  useEffect(() => {
    if (phase === "sending" && window.matchMedia("(max-width: 900px)").matches) {
      conversation.current?.scrollIntoView({
        block: "start",
        behavior: "instant",
      });
    }
  }, [phase]);
  return (
    <section
      ref={conversation}
      className="conversation"
      id="chat-panel"
      aria-label="Hội thoại"
      tabIndex={-1}
    >
      <div className="conversation-heading">
        <h2>Nội dung hội thoại</h2>
      </div>
      <div
        className="chat-scroll"
        onScroll={(event) => {
          const container = event.currentTarget;
          followLatest.current =
            container.scrollHeight -
              container.scrollTop -
              container.clientHeight <
            80;
        }}
      >
        {messages.length === 0 ? (
          <div className="welcome" hidden={phase !== "idle"}>
            <div className="welcome-icon">
              <Shield />
            </div>
            <h2>Bạn muốn trao đổi điều gì?</h2>
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message, index) => (
              <article key={index} className={`message ${message.role}`}>
                <div className="avatar">
                  {message.role === "user" ? "B" : <Shield small />}
                </div>
                <div className="message-body">
                  <div className="message-label">
                    {message.role === "user" ? "Bạn" : "Censitive"}
                  </div>
                  <div className="message-text">{message.content}</div>
                  {message.role === "assistant" &&
                    message.metadata?.public_plan && (
                      <details className="chat-plan">
                        <summary>Xem câu trả lời gốc từ bên nhận</summary>
                        <pre>{message.metadata.public_plan}</pre>
                      </details>
                    )}
                </div>
              </article>
            ))}
          </div>
        )}
        {phase === "sending" && (
          <div className="working" role="status">
            <span className="spinner" />{" "}
            {cancelPending
              ? "Đang hủy lượt…"
              : `${providerName(provider)} đang trả lời; Qwen sẽ hoàn tất đáp án trên máy…`}
          </div>
        )}
        <div ref={bottom} />
      </div>
      <div className="composer-area">
        {error && (
          <div className="error-box" role="alert">
            <span>!</span>
            <p>{error}</p>
            <button onClick={clearError} aria-label="Đóng thông báo">
              ×
            </button>
          </div>
        )}
        {answered ? (
          <div className="composer one-shot-done">
            <button
              className="primary"
              disabled={busy}
              onClick={() => void reset()}
            >
              Câu hỏi mới
            </button>
          </div>
        ) : (
          <>
            <div
              className={`composer ${phase === "ready" ? "has-preview" : ""}`}
            >
              <textarea
                ref={input}
                name="message"
                autoComplete="off"
                aria-label="Nội dung hội thoại"
                onSelect={(event) =>
                  selectDraft(
                    event.currentTarget.selectionStart,
                    event.currentTarget.selectionEnd,
                  )
                }
                value={draft}
                disabled={busy}
                placeholder="Nhập nội dung cần soạn, tóm tắt hoặc trao đổi…"
                onChange={(event) => changeDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    (event.ctrlKey || event.metaKey) &&
                    event.key === "Enter"
                  ) {
                    event.preventDefault();
                    void prepare();
                  }
                }}
              />
              {draftSelection && phase === "idle" && (
                <div
                  className="selection-protection"
                  aria-label="Bảo vệ đoạn đã chọn"
                >
                  <span>
                    Đang chọn: <q>{draftSelection.quote.trim()}</q>
                  </span>
                  <p id="selection-scope">
                    Che mọi lần khớp chính xác của cụm đã chọn.
                  </p>
                  <button
                    type="button"
                    aria-describedby="selection-scope"
                    disabled={
                      busy || !session || !providerConfirmed || !!selectionIssue
                    }
                    onPointerDown={(event) => event.preventDefault()}
                    onClick={protectSelection}
                  >
                    Thêm vào bảo vệ
                  </button>
                  {selectionIssue && <p role="status">{selectionIssue}</p>}
                </div>
              )}
              {selectionNotice && phase === "idle" && (
                <p className="selection-notice" role="status">
                  {selectionNotice}{" "}
                  {selectionUndoAvailable && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={undoSelection}
                    >
                      Hoàn tác
                    </button>
                  )}
                </p>
              )}
              <div className="composer-toolbar">
                <span>
                  <Shield small /> Văn bản{" "}
                  <span className="char-count">
                    {draft.length.toLocaleString("vi-VN")}
                  </span>
                </span>
                {phase === "preparing" ? (
                  <button
                    className="cancel-btn"
                    disabled={cancelPending}
                    onClick={() => void cancelPrepare()}
                  >
                    Hủy phân tích
                  </button>
                ) : phase === "sending" ? (
                  <button
                    className="cancel-btn"
                    disabled={cancelPending}
                    onClick={() => void cancel()}
                  >
                    Hủy lượt
                  </button>
                ) : phase === "ready" ? null : (
                  <button
                    className="primary"
                    disabled={
                      !session || !draft.trim() || busy || !providerConfirmed
                    }
                    onClick={() => void prepare()}
                  >
                    Chuẩn bị bản gửi <span>↑</span>
                  </button>
                )}
              </div>
            </div>
            {phase === "idle" && (
              <>
                <p className="composer-caption">
                  Ctrl + Enter để chuẩn bị bản gửi.
                </p>
                <p className="selection-hint">
                  Bôi chọn từ hoặc đoạn trong ô soạn thảo để thêm vào bảo vệ.
                </p>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}
