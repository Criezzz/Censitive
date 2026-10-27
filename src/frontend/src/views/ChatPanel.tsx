import type { PanelProps } from "./PanelProps";
import { samples } from "../data/samples";
import { AuditPanel } from "./AuditPanel";
import { Shield } from "./Shield";
import { providerName } from "../data/providers";

export function ChatPanel(props: PanelProps) {
  const {
    provider,
    messages,
    provisional,
    semantic,
    busy,
    phase,
    cancelPending,
    cloud,
    localModel,
    error,
    draft,
    session,
    providerConfirmed,
    factsConfirmed,
    identityConfirmed,
    factState,
    draftSelection,
    selectionIssue,
    selectionNotice,
  } = props.model;
  const {
    semanticSample,
    useSample,
    clearError,
    selectDraft,
    protectSelection,
    changeDraft,
    prepare,
    cancelPrepare,
    cancel,
  } = props.actions;
  const { bottom, input } = props.elements;
  return (
    <section className="conversation" id="chat-panel" aria-label="Hội thoại">
      <h2 className="sr-only">Nội dung hội thoại</h2>
      <div className="chat-scroll">
        {messages.length === 0 && provisional === null ? (
          <div className="welcome">
            <div className="welcome-icon">
              <Shield />
            </div>
            <h2>
              Có những điều
              <br />
              nên ở lại đây.
            </h2>
            <p>
              {semantic
                ? "Qwen đọc ngữ cảnh ngay trên máy để đề xuất che thông tin nhạy cảm. Bạn xem lại bản gửi trước khi provider trả lời."
                : "Nhập yêu cầu, chọn thông tin cần giữ riêng và kiểm tra bản chuẩn bị gửi."}
            </p>
            {semantic && (
              <button
                className="semantic-example"
                disabled={busy}
                onClick={semanticSample}
              >
                <strong>Thử một kế hoạch chưa công bố</strong>
                <span>Để model tự phát hiện, không nhập cụm thủ công</span>
              </button>
            )}
            <div className="sample-grid">
              {samples.map((sample) => (
                <button
                  key={sample.title}
                  onClick={() => useSample(sample)}
                  disabled={busy}
                  className="sample"
                >
                  <span className="sample-icon">{sample.icon}</span>
                  <strong>{sample.title}</strong>
                  <small>{sample.subtitle}</small>
                  <span className="sample-arrow">↗</span>
                </button>
              ))}
            </div>
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
                    {message.role === "assistant" && (
                      <span>
                        {message.provider === "local-demo" || !message.provider
                          ? "Phản hồi mẫu"
                          : providerName(message.provider)}{" "}
                        · đã phục hồi local
                      </span>
                    )}
                  </div>
                  <div className="message-text">{message.content}</div>
                </div>
              </article>
            ))}
          </div>
        )}
        {phase === "sending" && provisional !== null && (
          <div className="message-list">
            <article
              className="message assistant stream-provisional"
              aria-label="Phản hồi tạm thời"
            >
              <div className="avatar">
                <Shield small />
              </div>
              <div className="message-body">
                <div className="message-label">
                  Đang nhận · tạm thời, chưa xác nhận lưu
                </div>
                <div className="message-text">{provisional}</div>
              </div>
            </article>
          </div>
        )}
        {phase === "sending" && (
          <div className="working" role="status">
            <span className="spinner" />{" "}
            {cancelPending
              ? "Đang hủy lượt…"
              : cloud
                ? `${providerName(provider)} đang soạn phản hồi…`
                : localModel
                  ? "Qwen local đang soạn phản hồi…"
                  : "Provider demo đang soạn phản hồi…"}
          </div>
        )}
        <AuditPanel {...props} />
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
        <div className={`composer ${phase === "ready" ? "has-preview" : ""}`}>
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
            maxLength={20000}
            disabled={busy}
            placeholder="Nhập nội dung cần soạn, tóm tắt hoặc trao đổi…"
            onChange={(e) => changeDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                e.preventDefault();
                void prepare();
              }
            }}
          />
          {draftSelection && (
            <div
              className="selection-protection"
              aria-label="Bảo vệ đoạn đã chọn"
            >
              <span>
                Đang chọn:{" "}
                <q>
                  {factState?.enabled
                    ? draftSelection.quote
                    : draftSelection.quote.trim()}
                </q>
              </span>
              <p id="selection-scope">
                {factState?.enabled
                  ? "Đánh dấu đúng lần xuất hiện này trong bản nháp, không tạo định danh. Chỉ lưu khi gửi thành công."
                  : "Thêm cụm khớp chính xác, phân biệt hoa thường và áp dụng cho mọi lần khớp; bỏ khoảng trắng đầu/cuối. Sau khi duyệt, bảo vệ được giữ trong phiên."}
              </p>
              <button
                type="button"
                aria-describedby="selection-scope"
                disabled={
                  busy ||
                  !session ||
                  !providerConfirmed ||
                  !factsConfirmed ||
                  !identityConfirmed ||
                  !!selectionIssue
                }
                onPointerDown={(event) => event.preventDefault()}
                onClick={protectSelection}
              >
                Thêm vào bảo vệ
              </button>
              {selectionIssue && <p role="status">{selectionIssue}</p>}
            </div>
          )}
          {selectionNotice && (
            <p className="selection-notice" role="status">
              {selectionNotice}
            </p>
          )}
          <div className="composer-toolbar">
            <span>
              <Shield small /> Text-only{" "}
              <span className="char-count">
                {draft.length.toLocaleString("vi-VN")} / 20.000
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
            ) : (
              <button
                className="primary"
                disabled={
                  !session ||
                  !draft.trim() ||
                  busy ||
                  !providerConfirmed ||
                  !factsConfirmed ||
                  !identityConfirmed
                }
                onClick={() => void prepare()}
              >
                Chuẩn bị bản gửi <span>↑</span>
              </button>
            )}
          </div>
        </div>
        <p className="composer-caption">
          Ctrl + Enter để chuẩn bị <span>·</span>{" "}
          {cloud
            ? "Hủy sẽ dừng chờ; dữ liệu đã gửi không thể thu hồi."
            : localModel
              ? "Qwen trả lời trên máy từ bản bạn đã duyệt."
              : "Demo dùng phản hồi mẫu, chưa kết nối mô hình AI."}
        </p>
        <p className="selection-hint">
          Bôi chọn từ hoặc đoạn trong ô soạn thảo để thêm vào bảo vệ.
          {semantic &&
            " Qwen vẫn tự đề xuất che theo ngữ cảnh; thao tác chọn tay là bổ sung, không bắt buộc."}
        </p>
      </div>
    </section>
  );
}
