import { useState } from "react";
import type { PanelProps } from "./PanelProps";
import { Shield } from "./Shield";

export function SessionPanel({ model, actions }: PanelProps) {
  const { session, sessions, sessionMutation, busy, providerConfirmed } = model;
  const { reset, openSession, renameSession, deleteSession, openProviders } =
    actions;
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const locked = busy || sessionMutation?.status === "unknown";
  return (
    <aside
      className="sidebar"
      id="provider-session"
      aria-label="Kết nối và phiên"
      tabIndex={-1}
    >
      <h2 className="sr-only">Kết nối và phiên</h2>
      <a className="brand" href="/" aria-label="Censitive">
        <span className="brand-mark">
          <Shield />
        </span>
        <span>Censitive</span>
      </a>
      <button
        className="new-session"
        onClick={() => void reset()}
        disabled={locked}
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
                      if (editingTitle.trim())
                        void renameSession(item.id, editingTitle).then(() =>
                          setEditingId(null),
                        );
                    }}
                  >
                    <input
                      aria-label={`Tên cuộc trò chuyện ${title}`}
                      value={editingTitle}
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
                      {item.provider_confirmed === false && (
                        <span className="session-warning">
                          Cần chọn lại kết nối
                        </span>
                      )}
                    </button>
                    <div className="session-actions">
                      <button
                        className="session-action"
                        aria-label={`Đổi tên ${title}`}
                        disabled={locked}
                        onClick={() => {
                          setEditingId(item.id);
                          setEditingTitle(title);
                        }}
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
      {!providerConfirmed && session && (
        <button
          className="reselect-provider"
          onClick={openProviders}
          disabled={locked}
        >
          Chọn lại kết nối để tiếp tục
        </button>
      )}
      {sessionMutation?.status === "unknown" && (
        <p className="session-recovery" role="alert">
          Chưa xác nhận thao tác phiên. Tải lại trang trước khi thử lại.
        </p>
      )}
    </aside>
  );
}
