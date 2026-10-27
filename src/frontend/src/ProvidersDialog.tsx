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
          <div className="eyebrow">THIẾT LẬP HỘI THOẠI</div>
          <h2 id="providers-title">Provider & kết nối</h2>
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
      <p className="connection-intro">
        Chọn nơi trả lời chat. Kết nối tài khoản chưa gửi nội dung hội thoại.
      </p>
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
              <span className="provider-symbol">
                {p.id === "local-demo" ? "◇" : "⌘"}
              </span>
              <span>
                <strong>{p.name}</strong>
                <small>
                  {p.id === "local-demo"
                    ? "Không cần tài khoản"
                    : p.id === "local-qwen"
                      ? "Mô hình chạy trên máy"
                      : p.id === "openai-api"
                        ? "API key · cloud"
                        : "Tài khoản ChatGPT · cloud"}
                </small>
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
            {chosen.id === "local-demo" ? (
              <p>
                Phản hồi mẫu chạy tại máy. Phù hợp kiểm tra che và phục hồi mà
                không dùng model cloud.
              </p>
            ) : chosen.id === "local-qwen" ? (
              <p>
                Qwen trả lời bằng mô hình trên máy sau khi bạn duyệt bản đã che.
                Dùng chung runtime với bộ phát hiện; không gửi hội thoại lên
                cloud.
              </p>
            ) : chosen.id === "openai-api" ? (
              <>
                <p>
                  Gọi OpenAI trực tiếp bằng API key của backend, sau khi bạn
                  duyệt nội dung đã che. Không dùng phiên đăng nhập
                  ChatGPT/Codex.
                </p>
                <p role="status">
                  {chosen.status === "configured"
                    ? "Đã có cấu hình key và model. Chưa xác minh bằng lượt gọi thật."
                    : "Chưa có cấu hình API hợp lệ. Đặt OPENAI_API_KEY và CENSITIVE_OPENAI_MODEL cho backend, rồi khởi động lại."}
                </p>
                <p>
                  Key không được gửi tới trình duyệt. Quyền dùng model, hạn mức
                  và chi phí do tài khoản OpenAI API của bạn quyết định.
                </p>
              </>
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
                    nhận hạn mức model; hạn mức được kiểm khi gửi lượt chat.
                  </p>
                </details>
              </>
            )}
            <label htmlFor="provider-model">Model</label>
            <select
              id="provider-model"
              value={chosen.model}
              onChange={() => {}}
            >
              <option>{chosen.model}</option>
            </select>
            <small className="model-note">
              {chosen.id === "local-demo"
                ? "Bộ phản hồi mẫu"
                : chosen.id === "openai-api"
                  ? "Model được cấu hình ở backend; chưa kiểm chứng bằng API thật"
                  : chosen.id === "codex-chatgpt"
                    ? "Model từng được kiểm chứng cho tích hợp; vẫn cần kiểm tra kết nối hiện tại"
                    : "Model đã được kiểm chứng cho tích hợp hiện tại"}
            </small>
          </section>
        )}
      </div>
      {error && (
        <p className="connection-error" role="alert">
          {error}
        </p>
      )}
      <div className="connection-footer">
        <p>
          Đổi provider sẽ xóa bản duyệt hiện tại. Lịch sử đã che chỉ chuyển tới
          đích mới sau lần duyệt tiếp theo.
        </p>
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
