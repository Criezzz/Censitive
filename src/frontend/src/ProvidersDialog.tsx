import {
  useProviderConnections,
  type ProviderDialogProps,
} from "./controllers/useProviderConnections";
import { connectionMessages } from "./data/providers";

export function ProvidersDialog(props: ProviderDialogProps) {
  const { current, onClose } = props;
  const {
    dialog,
    providers,
    selected,
    working,
    error,
    chosen,
    canSelect,
    action,
    apply,
    selectChoice,
    model,
    setModel,
  } = useProviderConnections(props);
  return (
    <dialog
      ref={dialog}
      className="providers-dialog"
      aria-labelledby="providers-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!working) onClose();
      }}
    >
      <div className="connection-heading">
        <div>
          <h2 id="providers-title">Kết nối AI</h2>
        </div>
        <button
          className="quiet"
          aria-label="Đóng kết nối"
          disabled={working}
          onClick={onClose}
        >
          ×
        </button>
      </div>
      <div className="connection-layout">
        <div className="provider-options" aria-label="Nhà cung cấp">
          {providers.map((p) => (
            <button
              key={p.id}
              className={
                selected === p.id
                  ? "provider-option selected"
                  : "provider-option"
              }
              aria-pressed={selected === p.id}
              disabled={working}
              onClick={() => selectChoice(p.id)}
            >
              <span className="provider-symbol">⌘</span>
              <span>
                <strong>{p.name}</strong>
              </span>
              {current === p.id && (
                <span className="current-provider">Đang dùng</span>
              )}
            </button>
          ))}
        </div>
        {chosen && (
          <section className="connection-detail" aria-label="Chi tiết kết nối">
            <h3>{chosen.name}</h3>
            {chosen.id === "openai-api" ? (
              <p role="status">
                {chosen.status === "configured" || chosen.status === "ready"
                  ? "Gọi OpenAI sau khi bạn duyệt bản gửi. Khóa API được giữ trên máy."
                  : (connectionMessages[chosen.code] ??
                    "Chưa có OPENAI_API_KEY. Đặt biến môi trường này rồi khởi động lại.")}
              </p>
            ) : (
              <>
                <label htmlFor="auth-method">Cách kết nối</label>
                <select
                  id="auth-method"
                  value="chatgpt-cli"
                  onChange={() => {}}
                >
                  <option value="chatgpt-cli">
                    Đăng nhập ChatGPT qua Codex
                  </option>
                </select>
                <p>
                  Codex quản lý đăng nhập trên trình duyệt chính thức. Censitive
                  không nhận mật khẩu hoặc API key.
                </p>
                <div
                  className={`connection-status ${chosen.status}`}
                  role="status"
                >
                  {working
                    ? "Đang xử lý…"
                    : chosen.status === "ready"
                      ? "Đã nhận phiên đăng nhập ChatGPT"
                      : chosen.status === "signing_in"
                        ? "Đang chờ bạn đăng nhập trên trình duyệt…"
                        : chosen.status === "unchecked"
                          ? "Chưa kiểm tra kết nối trên máy"
                          : (connectionMessages[chosen.code] ??
                            "Kết nối hiện chưa sẵn sàng.")}
                </div>
                <div className="connection-actions">
                  {chosen.status !== "signing_in" && (
                    <button
                      className="secondary"
                      disabled={working}
                      onClick={() => void action("/providers/codex/check")}
                    >
                      Kiểm tra kết nối
                    </button>
                  )}
                  {chosen.status === "needs_login" && (
                    <button
                      className="primary"
                      disabled={working}
                      onClick={() => void action("/providers/codex/login")}
                    >
                      Đăng nhập bằng trình duyệt ↗
                    </button>
                  )}
                  {chosen.status === "signing_in" && (
                    <button
                      className="secondary"
                      disabled={working}
                      onClick={() =>
                        void action("/providers/codex/login/cancel")
                      }
                    >
                      Hủy bước đăng nhập
                    </button>
                  )}
                </div>
                <details className="connection-help">
                  <summary>Hướng dẫn kết nối</summary>
                  <ol>
                    <li>
                      Cài Codex CLI và chạy <code>codex login</code> nếu trình
                      duyệt không tự mở.
                    </li>
                    <li>
                      Đăng nhập ChatGPT, quay lại đây và nhấn “Kiểm tra kết
                      nối”.
                    </li>
                    <li>
                      Chọn “Dùng cho hội thoại này”, rồi chuẩn bị và duyệt bản
                      gửi.
                    </li>
                  </ol>
                  <p>
                    Phiên đăng nhập dùng chung với Codex CLI/IDE trên máy. Đóng
                    hộp này không đăng xuất tài khoản. Kiểm tra kết nối chưa xác
                    nhận hạn mức mô hình; hạn mức được kiểm khi gửi câu hỏi.
                  </p>
                </details>
              </>
            )}
            <label htmlFor="provider-model">Mô hình</label>
            <select
              id="provider-model"
              value={model}
              disabled={working || (chosen.models?.length ?? 0) < 2}
              onChange={(event) => setModel(event.target.value)}
            >
              {(chosen.models?.length ? chosen.models : [chosen.model]).map(
                (item) => (
                  <option key={item} value={item}>
                    {item || "Chưa chọn mô hình"}
                  </option>
                ),
              )}
            </select>
          </section>
        )}
      </div>
      {error && (
        <p className="connection-error" role="alert">
          {error}
        </p>
      )}
      <div className="connection-footer">
        <p>Đổi kết nối hoặc mô hình sẽ xóa bản duyệt hiện tại.</p>
        <button
          className="primary"
          disabled={!chosen || working || !canSelect}
          onClick={() => void apply()}
        >
          Dùng cho hội thoại này
        </button>
      </div>
    </dialog>
  );
}
