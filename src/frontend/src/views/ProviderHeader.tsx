import type { PanelProps } from "./PanelProps";
import { providerBadge } from "../data/providers";

export function ProviderHeader(props: PanelProps) {
  const { provider, providerConfirmed, busy, identityConfirmed } = props.model;
  const { openProviders } = props.actions;
  const { providerButton } = props.elements;
  return (
    <header className="topbar">
      <div>
        <span className="breadcrumb">Bản gốc ở lại máy bạn</span>
        <h1>Hội thoại riêng tư</h1>
      </div>
      <button
        className="demo-badge provider-trigger"
        ref={providerButton}
        onClick={openProviders}
        disabled={busy || !identityConfirmed}
        aria-label="Provider & kết nối"
      >
        <span />{" "}
        {providerConfirmed ? providerBadge(provider) : "Xác nhận provider"}
        <span className="provider-chevron">⌄</span>
      </button>
    </header>
  );
}
