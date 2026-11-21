import type { PanelProps } from "./PanelProps";
import { providerBadge } from "../data/providers";

export function ProviderHeader(props: PanelProps) {
  const { provider, providerConfirmed, busy } = props.model;
  const { openProviders } = props.actions;
  const { providerButton } = props.elements;
  return (
    <header className="topbar">
      <div>
        <h1>Hội thoại riêng tư</h1>
      </div>
      <button
        className="demo-badge provider-trigger"
        ref={providerButton}
        onClick={openProviders}
        disabled={busy}
        aria-label="Kết nối AI"
      >
        <span />{" "}
        {providerConfirmed ? providerBadge(provider) : "Xác nhận kết nối"}
        <span className="provider-chevron">⌄</span>
      </button>
    </header>
  );
}
