import type { PanelProps } from "./PanelProps";
import { Shield } from "./Shield";
import { MethodReview } from "./MethodReview";
import { providerName } from "../data/providers";

export function PrivacyPanel({ model: state, actions }: PanelProps) {
  const {
    provider,
    model,
    localAvailable,
    messages,
    draft,
    error,
    phase,
    preview,
    draftTerms,
    appliedTerms,
    termInput,
    termStatus,
    inspect,
    busy,
  } = state;
  const {
    changeTermInput,
    addTerm,
    removeTerm,
    setInspect,
    adjustMethod,
    send,
  } = actions;
  const answered = messages.some((message) => message.role === "assistant");
  return (
    <aside
      className="privacy-panel method-panel"
      id="privacy-panel"
      aria-label="Kiểm tra riêng tư"
      tabIndex={-1}
    >
      <div className="panel-title">
        <div className="panel-symbol">
          <Shield small />
        </div>
        <div>
          <h2>Kiểm tra riêng tư</h2>
        </div>
        <span className={`status-pill ${preview ? "ready" : ""}`}>
          {phase === "sending"
            ? "Đang gửi"
            : phase === "preparing"
              ? "Đang phân tích"
              : phase === "loading"
                ? preview
                  ? "Đang cập nhật"
                  : "Đang mở"
                : preview
                  ? "Chờ duyệt"
                  : answered
                    ? "Đã hoàn tất"
                    : error
                      ? "Cần kiểm tra"
                      : "Chưa gửi"}
        </span>
      </div>
      {!answered && !localAvailable && (
        <div className="detector-status" role="status">
          <span className="detector-dot" />
          <div>
            <strong>Qwen trên máy</strong>
            <p>Qwen chưa sẵn sàng. Kiểm tra kết nối trên máy.</p>
          </div>
        </div>
      )}
      {!answered && (
        <div className="policy-section">
          <label htmlFor="protected-terms">Cụm cần bảo vệ</label>
          <ul className="protected-terms" aria-label="Danh sách cụm bảo vệ">
            {[...appliedTerms, ...draftTerms].map((term) => (
              <li className="term-chip" key={term}>
                <span>{term}</span>
                {appliedTerms.includes(term) ? (
                  <small>Đã áp dụng trong phiên · vẫn được che</small>
                ) : (
                  <button
                    type="button"
                    aria-label={`Bỏ cụm “${term}”`}
                    disabled={busy}
                    onClick={() => removeTerm(term)}
                  >
                    Bỏ
                  </button>
                )}
              </li>
            ))}
          </ul>
          <form
            className="term-entry"
            onSubmit={(event) => {
              event.preventDefault();
              addTerm();
            }}
          >
            <input
              id="protected-terms"
              name="protected-terms"
              autoComplete="off"
              value={termInput}
              disabled={busy}
              onChange={(event) => changeTermInput(event.target.value)}
              placeholder="Nhập một cụm"
            />
            <button type="submit" disabled={busy}>
              Thêm
            </button>
          </form>
          {termStatus && <p role="status">{termStatus}</p>}
        </div>
      )}
      <div className="preview-section">
        <div className="section-heading">
          <h3>Bản chuẩn bị gửi</h3>
        </div>
        {preview ? (
          <>
            <div className="preview-tabs">
              <button
                className={inspect === "preview" ? "selected" : ""}
                aria-pressed={inspect === "preview"}
                onClick={() => setInspect("preview")}
              >
                Nội dung
              </button>
              <button
                className={inspect === "json" ? "selected" : ""}
                aria-pressed={inspect === "json"}
                onClick={() => setInspect("json")}
              >
                Body JSON
              </button>
            </div>
            {inspect === "preview" ? (
              <MethodReview
                preview={preview}
                original={draft}
                locked={busy}
                onAdjust={adjustMethod}
              />
            ) : (
              <pre className="preview-content" aria-label="Bản chuẩn bị gửi">
                {JSON.stringify(JSON.parse(preview.body), null, 2)}
              </pre>
            )}
          </>
        ) : (
          <div className="empty-preview">
            <div>⌁</div>
            <strong>
              {answered
                ? "Đã hoàn tất câu hỏi"
                : phase === "preparing"
                  ? "Đang chuẩn bị bản gửi"
                  : "Bạn sẽ thấy bản gửi ở đây"}
            </strong>
            <p>
              {answered
                ? "Chọn “Câu hỏi mới” để bắt đầu phiên khác."
                : phase === "preparing"
                  ? "Nội dung đang được xử lý trên máy. Chưa gửi tới bên nhận."
                  : "Nhấn “Chuẩn bị bản gửi” rồi kiểm tra nội dung trước khi duyệt."}
            </p>
          </div>
        )}
      </div>
      {preview && (
        <div className="approval-footer">
          <button
            className="approve-btn"
            onClick={() => void send()}
            disabled={phase !== "ready" || busy}
          >
            <Shield small /> Duyệt & gửi bản này <span>→</span>
          </button>
          <div className="destination">
            Đích nhận:{" "}
            <strong>
              {provider === "codex-chatgpt"
                ? "Codex / OpenAI"
                : providerName(provider)}{" "}
              · {model}
            </strong>
          </div>
        </div>
      )}
    </aside>
  );
}
