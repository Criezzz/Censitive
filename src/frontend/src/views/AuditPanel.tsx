import type { PanelProps } from "./PanelProps";

export function AuditPanel(props: PanelProps) {
  const { showAudit, audit } = props.model;
  return (
    <>
      {" "}
      {showAudit && (
        <section
          className="audit-box"
          id="session-audit"
          aria-label="Nhật ký phiên"
        >
          <h3>Nhật ký phiên · chỉ metadata</h3>
          {audit.length ? (
            audit.map((entry, i) => (
              <div key={i}>
                <code>{entry.event}</code>
                <span>{entry.status}</span>
                <small>v{entry.version}</small>
              </div>
            ))
          ) : (
            <p>Chưa có lượt hoàn tất. Nhật ký không ghi nội dung riêng tư.</p>
          )}
        </section>
      )}
    </>
  );
}
