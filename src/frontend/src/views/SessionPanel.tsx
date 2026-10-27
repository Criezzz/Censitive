import type { PanelProps } from "./PanelProps";
import { Shield } from "./Shield";
import { providerName } from "../data/providers";

export function SessionPanel(props: PanelProps) {
  const {
    provider,
    session,
    cloud,
    localModel,
    model,
    showAudit,
    busy,
    identityConfirmed,
  } = props.model;
  const { reset, toggleAudit } = props.actions;
  return (
    <aside
      className="sidebar"
      id="provider-session"
      aria-label="Provider và phiên"
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
        disabled={busy || !identityConfirmed}
      >
        <span>＋</span> Phiên mới
      </button>
      <div className="nav-label">Hội thoại</div>
      <div className="nav-active">
        <span>▤</span> Hội thoại riêng tư <span className="nav-dot" />
      </div>
      <div className="session-note">
        Phiên {session ? session.slice(0, 8) : "đang kết nối"}
        <br />
        Lưu trong bộ nhớ của backend
      </div>
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
