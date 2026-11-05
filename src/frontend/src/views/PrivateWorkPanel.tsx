import { useState } from "react";
import type { PanelProps } from "./PanelProps";
import { providerName } from "../data/providers";
import { ledgerFormFields } from "../data/structuredLedger";
import type { LedgerEvent, LedgerRevision } from "../data/contracts";

function shortHash(value: string) {
  return value.length > 16 ? `${value.slice(0, 16)}…` : value;
}

function displayValue(value: string | number | null | undefined) {
  return value === null || value === undefined || value === ""
    ? "—"
    : String(value);
}

type LedgerScalarField =
  "recipient" | "project" | "unit" | "surcharge_percent" | "budget";

const ledgerScalarLabels: Record<LedgerScalarField, string> = {
  recipient: "Người nhận",
  project: "Dự án",
  unit: "Đơn vị",
  surcharge_percent: "Phụ phí %",
  budget: "Ngân sách",
};

function scalarValue(
  revision: LedgerRevision | null,
  field: LedgerScalarField,
) {
  if (!revision) return null;
  const effective = revision.effective;
  if (effective && effective[field] !== undefined) return effective[field];
  return revision[field] ?? null;
}

function eventTarget(event: LedgerEvent, revision: LedgerRevision | null) {
  if (event.kind === "set_scalar") return event.field ?? "—";
  if (event.kind === "add") return event.label ?? "Khoản mới";
  const record = revision?.records.find(
    (item) => item.amount_id === event.amount_id,
  );
  if (record) return `${record.label} (${record.amount_id})`;
  return event.amount_id ?? "Khoản không xác định";
}

function eventValue(event: LedgerEvent) {
  if (event.kind === "cancel") return "—";
  if (event.kind === "set_scalar")
    return event.value ?? event.value_quote ?? "—";
  return event.amount ?? event.value_quote ?? "—";
}

export function PrivateWorkPanel({ model, actions }: PanelProps) {
  const [expanded, setExpanded] = useState(false);
  const canCollapse =
    !model.privateWorkAvailable &&
    !model.privateError &&
    !model.privateLedgerError &&
    !model.privateLedgerNotice &&
    !model.interruptedPrivateWork &&
    model.privateWorkHistory.length === 0 &&
    model.privateLedgerHistory.length === 0 &&
    model.privatePhase === "idle" &&
    !model.privatePreview &&
    !model.privateCancelPending &&
    model.privateLedgerPhase === "idle" &&
    !model.privateLedgerCandidate &&
    !model.privateLedgerCancelPending &&
    !model.privateLedgerRerunPending;
  const editingLocked =
    model.phase === "loading" ||
    model.phase === "preparing" ||
    model.phase === "sending" ||
    model.cancelPending ||
    model.privatePhase === "preparing" ||
    model.privatePhase === "sending" ||
    model.privateCancelPending ||
    model.pendingReview !== null ||
    model.sessionMutation !== null ||
    model.privateLedgerPhase === "proposing" ||
    model.privateLedgerPhase === "confirming" ||
    model.privateLedgerCandidate !== null ||
    model.privateLedgerCancelPending ||
    model.privateLedgerRerunPending;
  const currentLedger =
    model.privateLedgerHistory[model.privateLedgerHistory.length - 1] ?? null;
  const candidateRevision = model.privateLedgerCandidate?.candidate ?? null;
  const candidateParent = candidateRevision?.parent_revision
    ? (model.privateLedgerHistory.find(
        (revision) => revision.revision === candidateRevision.parent_revision,
      ) ?? null)
    : null;
  const ledgerScalarFields: LedgerScalarField[] = [
    "recipient",
    "project",
    "unit",
    "surcharge_percent",
    "budget",
  ];
  const usingLedger = model.privateLedgerUse && currentLedger !== null;
  const canRerunLedger =
    currentLedger !== null &&
    model.phase === "idle" &&
    model.privatePhase === "idle" &&
    model.privateLedgerPhase === "idle" &&
    model.privateLedgerCandidate === null &&
    model.pendingReview === null &&
    model.sessionMutation === null &&
    model.recoveryStatus !== "unknown_send" &&
    model.recoveryStatus !== "storage_failed";
  const canPrepare =
    model.privateWorkAvailable &&
    model.providerConfirmed &&
    model.factsConfirmed &&
    model.identityConfirmed &&
    model.pendingReview === null &&
    model.recoveryStatus !== "unknown_send" &&
    model.recoveryStatus !== "storage_failed" &&
    !editingLocked &&
    model.privatePublicTask.trim().length > 0 &&
    (usingLedger || model.privateContext.trim().length > 0);

  if (canCollapse && !expanded) {
    return (
      <section
        className="private-work-panel"
        id="private-work-panel"
        aria-label="Private work cục bộ"
        tabIndex={-1}
      >
        <div className="private-work-toggle">
          <p>
            Việc riêng (private work) chưa khả dụng với provider hiện tại. Sổ dữ
            liệu local vẫn dùng được.
          </p>
          <button
            type="button"
            className="secondary"
            aria-expanded={false}
            onClick={() => setExpanded(true)}
          >
            Mở rộng
          </button>
        </div>
      </section>
    );
  }

  return (
    <section
      className="private-work-panel"
      id="private-work-panel"
      aria-label="Private work cục bộ"
      tabIndex={-1}
    >
      {canCollapse && (
        <div className="private-work-toggle">
          <button
            type="button"
            className="secondary"
            aria-expanded={true}
            onClick={() => setExpanded(false)}
          >
            Thu gọn
          </button>
        </div>
      )}
      <div className="private-work-heading">
        <div>
          <span className="eyebrow">Hybrid workflow</span>
          <h2>Việc riêng: lập kế hoạch công khai, tính toán local</h2>
          <p>
            Chuẩn bị một bản gửi công khai và để local worker thực hiện phần cần
            dữ liệu riêng.
          </p>
        </div>
        <span
          className={`status-pill ${model.privateWorkAvailable ? "ready" : ""}`}
        >
          {model.privateWorkAvailable
            ? "Private work đã cấu hình"
            : "Private work chưa khả dụng"}
        </span>
      </div>

      {model.privateError && (
        <div className="private-work-error" role="alert">
          <span>{model.privateError}</span>
          <button
            type="button"
            onClick={() => actions.clearPrivateError()}
            aria-label="Đóng lỗi private work"
          >
            ×
          </button>
        </div>
      )}

      {model.interruptedPrivateWork && (
        <div className="private-work-recovery" role="alert">
          <strong>Kết quả private work trước đây chưa được xác nhận.</strong>
          <p>
            Không tự gửi lại và không dựng lại nút duyệt. Kiểm tra trạng thái
            bên dưới; nội dung này vẫn thuộc local session.
          </p>
          <details>
            <summary>Xem task và nguồn đang chờ kiểm tra</summary>
            <strong>{model.interruptedPrivateWork.public_task}</strong>
            <pre>{model.interruptedPrivateWork.private_context}</pre>
          </details>
        </div>
      )}

      {!model.privateWorkAvailable &&
      !(
        model.privateLedgerAvailable || model.privateLedgerHistory.length > 0
      ) ? (
        <div className="private-work-unavailable" role="status">
          <strong>Luồng này cần worker local và provider lập kế hoạch.</strong>
          <p>
            Mở Provider & kết nối để chọn OpenAI API đã cấu hình hoặc provider
            tương thích. Kiểm tra worker Qwen ở backend nếu luồng chưa khả dụng.
          </p>
        </div>
      ) : (
        <>
          {(model.privateLedgerAvailable ||
            model.privateLedgerHistory.length > 0) && (
            <section
              className="private-ledger"
              aria-label="Sổ dữ liệu riêng local"
            >
              <div className="private-ledger-heading">
                <div>
                  <span className="eyebrow">Tùy chọn local</span>
                  <h3>Sổ dữ liệu riêng</h3>
                  <p>
                    Nhập trực tiếp hoặc nhờ model local đề xuất. Bạn phải kiểm
                    tra bảng và xác nhận rõ ràng trước khi bản ghi được dùng cho
                    việc riêng.
                  </p>
                </div>
                <span className="private-ledger-revision">
                  {currentLedger
                    ? `Bản ghi v${currentLedger.revision}`
                    : "Chưa có bản ghi"}
                </span>
              </div>

              {currentLedger && (
                <div className="private-ledger-current">
                  <p>
                    Nguồn revision:{" "}
                    {currentLedger.origin === "user"
                      ? "Người dùng nhập trực tiếp"
                      : "Model local đề xuất từ nguồn"}
                    .
                  </p>
                  <div className="section-heading">
                    <h4>Bản ghi đã xác nhận</h4>
                    <span>{currentLedger.records.length} khoản</span>
                  </div>
                  <table aria-label="Bản ghi local đã xác nhận">
                    <thead>
                      <tr>
                        <th>Nhãn</th>
                        <th>Số lượng</th>
                        <th>Trạng thái</th>
                      </tr>
                    </thead>
                    <tbody>
                      {currentLedger.records.map((record) => (
                        <tr key={record.amount_id}>
                          <td>{record.label}</td>
                          <td>{displayValue(record.amount)}</td>
                          <td>{record.active ? "Đang dùng" : "Đã hủy"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <dl className="private-ledger-scalars">
                    <div>
                      <dt>Người nhận</dt>
                      <dd>
                        {displayValue(
                          currentLedger.effective?.recipient ??
                            currentLedger.recipient,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Dự án</dt>
                      <dd>
                        {displayValue(
                          currentLedger.effective?.project ??
                            currentLedger.project,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Đơn vị</dt>
                      <dd>
                        {displayValue(
                          currentLedger.effective?.unit ?? currentLedger.unit,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Phụ phí %</dt>
                      <dd>
                        {displayValue(
                          currentLedger.effective?.surcharge_percent ??
                            currentLedger.surcharge_percent,
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Ngân sách</dt>
                      <dd>
                        {displayValue(
                          currentLedger.effective?.budget ??
                            currentLedger.budget,
                        )}
                      </dd>
                    </div>
                  </dl>
                  <label className="private-ledger-use">
                    <input
                      type="checkbox"
                      checked={usingLedger}
                      disabled={
                        editingLocked || model.privateLedgerPhase !== "idle"
                      }
                      onChange={(event) =>
                        actions.setPrivateLedgerUse(event.target.checked)
                      }
                    />
                    <span>
                      Dùng bản ghi này cho việc riêng tiếp theo
                      <small>
                        Chỉ revision được xác nhận đi vào local executor; nguồn
                        riêng vẫn không gửi lên planner.
                      </small>
                    </span>
                  </label>
                  {usingLedger && (
                    <p className="private-ledger-formula" role="note">
                      Công thức: tổng khoản còn hiệu lực × (1 + phụ phí/100), so
                      với ngân sách. Ô công khai chỉ yêu cầu cách trình bày.
                    </p>
                  )}
                </div>
              )}

              <details className="private-ledger-structured">
                <summary>Nhập dữ liệu trực tiếp (không gọi model)</summary>
                <p>
                  Chỉ nhập khoản hoặc trường muốn thêm/sửa. Nhãn khoản trùng sẽ
                  sửa số của khoản đó; ô trống giữ nguyên. Đây là dữ liệu do bạn
                  nhập, không phải kết quả Qwen trích xuất.
                </p>
                <fieldset
                  disabled={
                    editingLocked || model.privateLedgerPhase !== "idle"
                  }
                >
                  <label htmlFor="structured-ledger-records">
                    Các khoản nhập tay (mỗi dòng Nhãn: số)
                  </label>
                  <textarea
                    id="structured-ledger-records"
                    value={model.structuredLedgerDraft.records}
                    maxLength={3000}
                    placeholder={"Hoa: 14.5\nSen: 9"}
                    onChange={(event) =>
                      actions.changeStructuredLedger(
                        "records",
                        event.target.value,
                      )
                    }
                  />
                  <div className="structured-ledger-scalars">
                    {ledgerFormFields.map(([field, label]) => (
                      <label key={field} htmlFor={`structured-ledger-${field}`}>
                        {label}
                        <input
                          id={`structured-ledger-${field}`}
                          type="text"
                          inputMode={
                            field === "surcharge_percent" || field === "budget"
                              ? "decimal"
                              : "text"
                          }
                          maxLength={
                            field === "surcharge_percent" || field === "budget"
                              ? 64
                              : 256
                          }
                          value={model.structuredLedgerDraft[field]}
                          onChange={(event) =>
                            actions.changeStructuredLedger(
                              field,
                              event.target.value,
                            )
                          }
                        />
                      </label>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="secondary"
                    disabled={
                      !Object.values(model.structuredLedgerDraft).some(
                        (value) => value.trim(),
                      )
                    }
                    onClick={() => void actions.proposeStructuredLedger()}
                  >
                    Xem bản nhập tay trước khi xác nhận
                  </button>
                </fieldset>
              </details>

              <div className="private-ledger-propose">
                <label htmlFor="private-ledger-source">
                  {currentLedger ? "Phần cập nhật mới" : "Nguồn dữ liệu local"}
                  <span>
                    {currentLedger
                      ? "Chỉ nhập thay đổi mới, không cần nhập lại toàn bộ sổ."
                      : "Chỉ local model được đọc; chưa được coi là dữ liệu đã xác nhận."}
                  </span>
                </label>
                <textarea
                  id="private-ledger-source"
                  value={model.privateLedgerSource}
                  maxLength={4000}
                  disabled={
                    editingLocked ||
                    model.privateLedgerPhase !== "idle" ||
                    model.privateLedgerCandidate !== null
                  }
                  onChange={(event) =>
                    actions.changePrivateLedgerSource(event.target.value)
                  }
                  placeholder={
                    currentLedger
                      ? "Ví dụ: thêm hóa đơn Sen 24, hủy khoản Lan 12…"
                      : "Ví dụ: Lan 12, Sen 8; người nhận là nhóm A."
                  }
                />
              </div>

              <p className="private-ledger-warning">
                Quote trong nguồn chỉ là bằng chứng để kiểm tra vị trí. Việc bấm
                xác nhận là quyết định của bạn, không phải chứng minh model đã
                hiểu đúng.
              </p>

              {model.privateLedgerPhase === "proposing" && (
                <div
                  className="private-work-status private-ledger-status"
                  role="status"
                >
                  <span className="spinner" /> Đang đề xuất thay đổi local…
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void actions.discardPrivateLedger()}
                  >
                    Hủy đề xuất
                  </button>
                </div>
              )}
              {model.privateLedgerCancelPending && (
                <div className="private-work-status" role="status">
                  <span className="spinner" /> Đang xác nhận hủy đề xuất local…
                </div>
              )}

              {model.privateLedgerCandidate && (
                <div className="private-ledger-candidate">
                  <div className="section-heading">
                    <h4>Bản đề xuất chưa xác nhận</h4>
                    <span>CHỈ XEM</span>
                  </div>
                  <p>
                    {model.privateLedgerCandidate.candidate.origin === "user"
                      ? "Nguồn: Người dùng nhập trực tiếp. "
                      : "Nguồn: Model local đề xuất. "}
                    Revision v{model.privateLedgerCandidate.candidate.revision}
                    {model.privateLedgerCandidate.candidate.parent_revision
                      ? ` kế thừa v${model.privateLedgerCandidate.candidate.parent_revision}`
                      : " là bản ghi đầu tiên"}
                    .
                  </p>
                  <table aria-label="Bảng đề xuất ledger">
                    <thead>
                      <tr>
                        <th>Nhãn</th>
                        <th>Số lượng</th>
                        <th>Trạng thái</th>
                      </tr>
                    </thead>
                    <tbody>
                      {model.privateLedgerCandidate.candidate.records.map(
                        (record) => (
                          <tr key={record.amount_id}>
                            <td>{record.label}</td>
                            <td>{displayValue(record.amount)}</td>
                            <td>{record.active ? "Đang dùng" : "Đã hủy"}</td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                  <div className="private-ledger-effective">
                    <h5>Giá trị hiệu lực trước và sau</h5>
                    <table aria-label="Effective scalar ledger candidate">
                      <thead>
                        <tr>
                          <th>Trường</th>
                          <th>Trước</th>
                          <th>Sau</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ledgerScalarFields.map((field) => (
                          <tr key={field}>
                            <td>{ledgerScalarLabels[field]}</td>
                            <td>
                              {displayValue(
                                scalarValue(candidateParent, field),
                              )}
                            </td>
                            <td>
                              {displayValue(
                                scalarValue(candidateRevision, field),
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <table
                    className="private-ledger-operation-table"
                    aria-label="Chi tiết operation ledger"
                  >
                    <thead>
                      <tr>
                        <th>Operation</th>
                        <th>Target / field</th>
                        <th>Giá trị</th>
                        <th>Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {model.privateLedgerCandidate.candidate.events.map(
                        (event, index) => (
                          <tr key={`${event.kind}-${index}`}>
                            <td>{event.kind}</td>
                            <td>{eventTarget(event, candidateRevision)}</td>
                            <td>{displayValue(eventValue(event))}</td>
                            <td>{event.evidence.quote}</td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                  <details className="private-ledger-evidence" open>
                    <summary>
                      {model.privateLedgerCandidate.candidate.origin === "user"
                        ? "Xem bản ghi các trường đã nhập"
                        : "Xem evidence quote"}
                    </summary>
                    <ul>
                      {model.privateLedgerCandidate.candidate.events.map(
                        (event, index) => (
                          <li key={`${event.kind}-${index}`}>
                            <strong>{event.kind}</strong>: “
                            {event.evidence.quote}”
                          </li>
                        ),
                      )}
                    </ul>
                  </details>
                  <div className="private-ledger-actions">
                    <button
                      type="button"
                      className="secondary"
                      disabled={model.privateLedgerPhase === "confirming"}
                      onClick={() => void actions.discardPrivateLedger()}
                    >
                      Bỏ đề xuất
                    </button>
                    <button
                      type="button"
                      className="approve-btn"
                      disabled={model.privateLedgerPhase === "confirming"}
                      onClick={() => void actions.confirmPrivateLedger()}
                    >
                      {model.privateLedgerPhase === "confirming"
                        ? "Đang lưu…"
                        : "Xác nhận dữ liệu local"}
                    </button>
                  </div>
                </div>
              )}

              {model.privateLedgerNotice && (
                <p role="status">{model.privateLedgerNotice}</p>
              )}
              {model.privateLedgerError && (
                <div className="private-work-error" role="alert">
                  <span>{model.privateLedgerError}</span>
                  <button
                    type="button"
                    onClick={() => actions.clearPrivateLedgerError()}
                    aria-label="Đóng trạng thái ledger"
                  >
                    ×
                  </button>
                </div>
              )}
              {model.privateLedgerPhase === "idle" &&
                !model.privateLedgerCandidate && (
                  <div className="private-ledger-actions">
                    <button
                      type="button"
                      className="primary"
                      disabled={
                        editingLocked ||
                        !model.privateLedgerModelAvailable ||
                        !model.privateLedgerSource.trim()
                      }
                      onClick={() => void actions.proposePrivateLedger()}
                    >
                      Đề xuất thay đổi bằng model local
                    </button>
                  </div>
                )}
            </section>
          )}
          {model.privateLedgerRerunPending && (
            <div className="private-work-status" role="status">
              <span className="spinner" /> Đang tính lại bằng dữ liệu local đã
              xác nhận…
            </div>
          )}
          {model.privateWorkAvailable ? (
            <>
              <div className="private-work-form">
                <label htmlFor="private-public-task">
                  Yêu cầu công khai
                  <span>Được phép gửi tới planner</span>
                </label>
                <textarea
                  id="private-public-task"
                  value={model.privatePublicTask}
                  maxLength={3000}
                  disabled={editingLocked}
                  onChange={(event) =>
                    actions.changePrivatePublicTask(event.target.value)
                  }
                  placeholder="Ví dụ: Tính tổng và viết email ngắn cho khách hàng."
                />
                <label htmlFor="private-context">
                  Dữ liệu riêng tư
                  <span>
                    {usingLedger
                      ? "Đang dùng sổ local đã xác nhận ở trên"
                      : "Chỉ local worker được đọc"}
                  </span>
                </label>
                <textarea
                  id="private-context"
                  value={model.privateContext}
                  maxLength={12000}
                  disabled={editingLocked || usingLedger}
                  onChange={(event) =>
                    actions.changePrivateContext(event.target.value)
                  }
                  placeholder={
                    usingLedger
                      ? "Đã chọn bản ghi local; ô này không được gửi."
                      : "Ví dụ: Hóa đơn A là 12, hóa đơn B là 8…"
                  }
                />
              </div>

              {model.privatePhase === "preparing" && (
                <div className="private-work-status" role="status">
                  <span className="spinner" /> Đang chuẩn bị bản gửi…
                </div>
              )}

              {model.privatePreview && (
                <div className="private-work-preview">
                  <div className="section-heading">
                    <h3>Nội dung sẽ gửi tới planner</h3>
                    <span>CHƯA GỬI</span>
                  </div>
                  <p className="private-work-boundary">
                    Đây là nội dung public planner sẽ nhận. Ô dữ liệu riêng
                    không được đưa vào bản gửi; hãy kiểm tra ô công khai trước
                    khi duyệt.
                  </p>
                  <pre aria-label="Public planner payload">
                    {model.privatePreview.public_body}
                  </pre>
                  <dl className="private-work-metadata">
                    <div>
                      <dt>Provider</dt>
                      <dd>{providerName(model.privatePreview.provider)}</dd>
                    </div>
                    <div>
                      <dt>Model</dt>
                      <dd>{model.privatePreview.model}</dd>
                    </div>
                    {model.privatePreview.ledger_revision !== null &&
                      model.privatePreview.ledger_revision !== undefined && (
                        <div>
                          <dt>Ledger</dt>
                          <dd>
                            Revision v{model.privatePreview.ledger_revision}
                          </dd>
                        </div>
                      )}
                    <div>
                      <dt>SHA-256</dt>
                      <dd title={model.privatePreview.public_body_sha256}>
                        {shortHash(model.privatePreview.public_body_sha256)}
                      </dd>
                    </div>
                  </dl>
                </div>
              )}

              <div className="private-work-actions">
                {model.privatePhase === "preparing" ||
                model.privatePhase === "sending" ? (
                  model.privateLedgerRerunPending ? (
                    <button type="button" className="cancel-btn" disabled>
                      Đang tính lại local…
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="cancel-btn"
                      disabled={model.privateCancelPending}
                      onClick={() => void actions.cancelPrivateWork()}
                    >
                      {model.privateCancelPending
                        ? "Đang hủy…"
                        : "Hủy private work"}
                    </button>
                  )
                ) : model.privatePreview ? (
                  <>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => void actions.discardPrivateWork()}
                    >
                      Bỏ bản xem trước
                    </button>
                    <button
                      type="button"
                      className="approve-btn"
                      onClick={() => void actions.sendPrivateWork()}
                    >
                      Duyệt và thực hiện
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="primary"
                    disabled={!canPrepare}
                    onClick={() => void actions.preparePrivateWork()}
                  >
                    Chuẩn bị bản gửi công khai
                  </button>
                )}
              </div>
            </>
          ) : (
            <div className="private-work-unavailable" role="status">
              <strong>Public planner chưa khả dụng.</strong>
              <p>
                Bạn vẫn có thể xem và cập nhật sổ local. Kết nối provider chính
                thống để chuẩn bị việc riêng dùng revision đã xác nhận.
              </p>
            </div>
          )}
        </>
      )}

      <div className="private-work-history" aria-label="Lịch sử private work">
        <div className="section-heading">
          <h3>Kết quả local đã lưu</h3>
          <span>{model.privateWorkHistory.length} lượt</span>
        </div>
        {model.privateWorkHistory.length === 0 ? (
          <p className="private-work-empty">
            Chưa có kết quả. Lịch sử này tách khỏi lịch sử gửi lên provider.
          </p>
        ) : (
          model.privateWorkHistory.map((item) => {
            const hasLedgerRevision =
              item.ledger_revision !== null &&
              item.ledger_revision !== undefined;
            const isCurrentLedger =
              hasLedgerRevision &&
              item.ledger_revision === currentLedger?.revision;
            return (
              <article className="private-work-record" key={item.id}>
                <strong>
                  {hasLedgerRevision
                    ? `Kết quả từ sổ local · v${item.ledger_revision}`
                    : item.public_task}
                </strong>
                <p className="private-work-answer">{item.answer}</p>
                <details>
                  <summary>Xem nguồn local và trace</summary>
                  {hasLedgerRevision && (
                    <>
                      <strong>Yêu cầu công khai đầy đủ</strong>
                      <pre>{item.public_task}</pre>
                    </>
                  )}
                  <pre>{item.private_context}</pre>
                  <ul>
                    {item.trace.map((step, index) => (
                      <li key={`${item.id}-${index}`}>{step}</li>
                    ))}
                  </ul>
                </details>
                <small>
                  {item.route} · {providerName(item.provider)} · {item.model}
                </small>
                {hasLedgerRevision && (
                  <small
                    className={`private-ledger-history-revision${
                      isCurrentLedger ? "" : " historical"
                    }`}
                  >
                    Dữ liệu local: revision v{item.ledger_revision}
                    {!isCurrentLedger && " · Kết quả lịch sử"}
                  </small>
                )}
                {hasLedgerRevision && (
                  <button
                    type="button"
                    className="secondary private-ledger-rerun"
                    disabled={!canRerunLedger}
                    onClick={() => void actions.rerunPrivateLedger(item.id)}
                  >
                    Tính lại với dữ liệu đã xác nhận (v{currentLedger?.revision}
                    )
                  </button>
                )}
              </article>
            );
          })
        )}
      </div>
    </section>
  );
}
