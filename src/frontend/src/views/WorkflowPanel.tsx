import type { WorkflowStatus } from "../data/workspace";

export function WorkflowPanel({ status }: { status: WorkflowStatus }) {
  return (
    <section
      className={`workflow-panel ${status.attention ? "attention" : ""}`}
      aria-label="Tiến trình hội thoại"
    >
      <ol className="workflow-steps" aria-label="Các bước gửi hội thoại">
        {["Soạn trên máy", "Xem lại & duyệt", "Nhận phản hồi"].map(
          (label, index) => (
            <li
              key={label}
              aria-current={status.step === index + 1 ? "step" : undefined}
            >
              <span aria-hidden="true">{index + 1}</span>
              {label}
            </li>
          ),
        )}
      </ol>
      <div className="workflow-guidance">
        <div role="status">
          <strong>{status.title}</strong>
          <p>{status.detail}</p>
        </div>
        <a href={`#${status.target}`}>
          {status.action} <span aria-hidden="true">→</span>
        </a>
      </div>
    </section>
  );
}
