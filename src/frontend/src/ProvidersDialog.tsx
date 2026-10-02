import { useEffect, useRef, useState } from "react";

type ProviderChoice = {
  id: string;
  name: string;
  model: string;
  auth: string;
  status: string;
  code: string;
};
type Catalog = { providers: ProviderChoice[] };
type Request = <T>(path: string, method?: string, body?: unknown) => Promise<T>;

const messages: Record<string, string> = {
  CODEX_NOT_FOUND:
    "Chưa tìm thấy Codex CLI trên máy. Cài Codex rồi kiểm tra lại.",
  CODEX_VERSION_UNVERIFIED:
    "Bản Codex này chưa được kiểm chứng với gateway. Kết nối đang bị khóa.",
  CODEX_CHATGPT_LOGIN_REQUIRED: "Chưa có phiên đăng nhập ChatGPT trong Codex.",
  CODEX_LOGIN_FAILED:
    "Đăng nhập chưa hoàn tất. Thử lại hoặc dùng lệnh bên dưới.",
  CODEX_LOGIN_TIMEOUT:
    "Đã hết thời gian chờ đăng nhập. Bạn có thể bắt đầu lại.",
  CODEX_LOGIN_CANCELLED:
    "Đã dừng chờ đăng nhập. Tài khoản đã đăng nhập trước đó không bị đăng xuất.",
  LOGIN_BUSY: "Một cửa sổ khác đang đăng nhập Codex. Hãy chờ rồi kiểm tra lại.",
  SESSION_BUSY: "Hãy chờ lượt chat kết thúc trước khi đổi provider.",
  SESSION_CHANGED:
    "Phiên đã thay đổi trong lúc kiểm tra kết nối. Hãy đóng hộp này rồi kiểm tra lại.",
  AUTH_REQUIRED:
    "Kết nối ứng dụng đã hết hiệu lực. Đóng hộp này và tạo phiên mới.",
};

export function ProvidersDialog({
  current,
  request,
  onApply,
  onClose,
}: {
  current: string;
  request: Request;
  onApply: (provider: string, model: string) => Promise<void>;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [providers, setProviders] = useState<ProviderChoice[]>([]);
  const [selected, setSelected] = useState(current);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const chosen = providers.find((p) => p.id === selected);
  const signingIn = providers.some((p) => p.status === "signing_in");

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    let active = true;
    request<Catalog>("/providers")
      .then((result) => {
        if (active) setProviders(result.providers);
      })
      .catch(() => {
        if (active) setError("Không thể tải danh sách provider.");
      });
    return () => {
      active = false;
      element.close();
    };
  }, [request]);

  useEffect(() => {
    if (!signingIn || working) return;
    let active = true;
    let pending = false;
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      request<Catalog>("/providers")
        .then((result) => {
          if (active) setProviders(result.providers);
        })
        .catch(() => {
          if (active) setError("Mất kết nối ứng dụng. Hãy kiểm tra lại.");
        })
        .finally(() => {
          pending = false;
        });
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [signingIn, working, request]);

  async function action(path: string) {
    if (working) return;
    setWorking(true);
    setError("");
    try {
      const result = await request<Catalog>(path, "POST");
      setProviders(result.providers);
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        messages[code] ?? "Không thể kết nối provider. Hãy thử kiểm tra lại.",
      );
    } finally {
      setWorking(false);
    }
  }

  async function apply() {
    if (!chosen || working || chosen.status !== "ready") return;
    setWorking(true);
    setError("");
    try {
      await onApply(chosen.id, chosen.model);
      onClose();
    } catch (reason) {
      const code = reason instanceof Error ? reason.message : "";
      setError(
        messages[code] ??
          "Chưa xác nhận được kết quả đổi provider. Hãy thử lại để xác nhận đích nhận trước khi gửi.",
      );
    } finally {
      setWorking(false);
    }
  }

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
              onClick={() => {
                setSelected(p.id);
                setError("");
              }}
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
                          : (messages[chosen.code] ??
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
          disabled={!chosen || working || chosen.status !== "ready"}
          onClick={() => void apply()}
        >
          Dùng cho hội thoại này
        </button>
      </div>
    </dialog>
  );
}
