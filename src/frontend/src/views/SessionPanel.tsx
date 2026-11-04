import { useState } from "react";
import type { PanelProps } from "./PanelProps";
import { Shield } from "./Shield";
import { providerName } from "../data/providers";

function recoveryLabel(status: string | null | undefined) {
  if (!status || status === "none" || status === "completed") return "";
  if (status === "unknown_send")
    return "Kết quả lượt trước chưa rõ; hãy kiểm tra lịch sử và tạo phiên mới, không tự gửi lại.";
  if (status === "may_have_been_sent")
    return "Có thể lượt trước đã được gửi; hãy kiểm tra lịch sử, không tự gửi lại.";
  if (status === "provider_unavailable")
    return "Provider của phiên chưa sẵn sàng; hãy chọn lại provider để tiếp tục.";
  if (status === "storage_failed")
    return "Chưa lưu được thay đổi của phiên. Hãy tải lại trang để kiểm tra.";
  return "Phiên cần được kiểm tra lại trước khi tiếp tục.";
}

export function SessionPanel(props: PanelProps) {
  const {
    provider,
    session,
    sessionTitle,
    sessions,
    persistenceEnabled,
    sessionMutation,
    recoveryStatus,
    interruptedTurn,
    cloud,
    localModel,
    model,
    showAudit,
    busy,
    identityConfirmed,
    providerConfirmed,
  } = props.model;
  const {
    reset,
    toggleAudit,
    openSession,
    renameSession,
    deleteSession,
    openProviders,
  } = props.actions;
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const locked = busy || sessionMutation?.status === "unknown";
  const currentRecovery = recoveryLabel(recoveryStatus);

  function beginRename(id: string, title: string) {
    setEditingId(id);
    setEditingTitle(title);
  }

  function commitRename(id: string) {
    if (!editingTitle.trim()) return;
    void renameSession(id, editingTitle).then(() => setEditingId(null));
  }

  return (
    <aside
      className="sidebar"
      id="provider-session"
      aria-label="Provider và phiên"
      tabIndex={-1}
    >
      <h2 className="sr-only">Provider và phiên</h2>
      <a className="brand" href="/" aria-label="Censitive">
        <span className="brand-mark">
          <Shield />
        </span>
        <span>
          Censitive
          <span className="brand-sub">Không gian hội thoại riêng tư</span>
        </span>
      </a>
      <button
        className="new-session"
        onClick={() => void reset()}
        disabled={locked || !identityConfirmed}
      >
        <span>＋</span> Phiên mới
      </button>
      <div className="nav-label">Hội thoại</div>
      <div className="session-list" aria-label="Các cuộc trò chuyện">
        {sessions.length === 0 ? (
          <p className="session-list-empty">Chưa có cuộc trò chuyện nào.</p>
        ) : (
          sessions.map((item) => {
            const title = item.title || "Cuộc trò chuyện mới";
            const itemRecovery = recoveryLabel(item.recovery_status);
            return (
              <div
                key={item.id}
                className={`session-item ${item.id === session ? "active" : ""}`}
              >
                {editingId === item.id ? (
                  <form
                    className="session-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      commitRename(item.id);
                    }}
                  >
                    <input
                      aria-label={`Tên cuộc trò chuyện ${title}`}
                      value={editingTitle}
                      maxLength={120}
                      disabled={locked}
                      onChange={(event) => setEditingTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape") setEditingId(null);
                      }}
                      autoFocus
                    />
                    <button
                      type="submit"
                      disabled={locked || !editingTitle.trim()}
                    >
                      Lưu
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      className="session-select"
                      onClick={() => void openSession(item.id)}
                      disabled={locked || item.id === session}
                      aria-current={item.id === session ? "page" : undefined}
                      title={title}
                    >
                      <span className="session-title">{title}</span>
                      <span className="session-meta">
                        {item.message_count ?? 0} tin nhắn
                        {item.private_work_count
                          ? ` · ${item.private_work_count} việc riêng`
                          : ""}{" "}
                        · {providerName(item.provider)}
                      </span>
                      {item.provider_confirmed === false && (
                        <span className="session-warning">
                          Cần chọn lại provider
                        </span>
                      )}
                      {itemRecovery && (
                        <span className="session-warning">{itemRecovery}</span>
                      )}
                    </button>
                    <div className="session-actions">
                      <button
                        className="session-action"
                        aria-label={`Đổi tên ${title}`}
                        disabled={locked}
                        onClick={() => beginRename(item.id, title)}
                      >
                        Đổi tên
                      </button>
                      <button
                        className="session-action delete"
                        aria-label={`Xóa ${title}`}
                        disabled={locked}
                        onClick={() => void deleteSession(item.id)}
                      >
                        Xóa
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })
        )}
      </div>
      <div className="session-note">
        <strong>{sessionTitle || "Cuộc trò chuyện mới"}</strong>
        <br />
        {session ? `Phiên ${session.slice(0, 8)}` : "Đang kết nối"}
        <br />
        {persistenceEnabled
          ? "Lưu trên máy để mở lại sau khi tải lại"
          : "Lưu trong bộ nhớ backend"}
      </div>
      {!providerConfirmed && session && (
        <button
          className="reselect-provider"
          onClick={openProviders}
          disabled={locked || !identityConfirmed}
        >
          Chọn lại provider để tiếp tục
        </button>
      )}
      {currentRecovery && (
        <p className="session-recovery" role="status">
          {currentRecovery}
        </p>
      )}
      {interruptedTurn && (
        <details className="session-interrupted">
          <summary>Lượt trước chưa rõ kết quả</summary>
          <p>
            Nội dung đã chuẩn bị (chỉ để bạn kiểm tra):
            <br />
            <span className="interrupted-text">{interruptedTurn.text}</span>
          </p>
          <small>
            Không tự gửi lại lượt này. Hãy tạo phiên mới để tiếp tục.
          </small>
        </details>
      )}
      {sessionMutation?.status === "unknown" && (
        <p className="session-recovery" role="alert">
          Chưa xác nhận thao tác phiên. Tải lại trang trước khi thử lại.
        </p>
      )}
      <div className="sidebar-bottom">
        <div className="local-card">
          <span className="online-dot" />{" "}
          {cloud
            ? `Kết nối ${providerName(provider)} · cloud`
            : "Chạy hoàn toàn local"}
          <p>
            {cloud || localModel ? model : "Provider giả cho buổi demo."}
            <br />
            {cloud
              ? "Chỉ gửi hội thoại sau khi bạn duyệt."
              : "Không gửi dữ liệu lên cloud."}
          </p>
        </div>
        <button className="quiet side-audit" onClick={toggleAudit}>
          {showAudit ? "Ẩn" : "Xem"} nhật ký phiên <span>↗</span>
        </button>
        <div className="sidebar-footer">
          Censitive <span>Local privacy gateway</span>
        </div>
      </div>
    </aside>
  );
}
