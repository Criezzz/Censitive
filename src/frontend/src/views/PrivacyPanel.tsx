import type { PanelProps } from "./PanelProps";
import { ProtectionPanel } from "./ProtectionPanel";
import { Shield } from "./Shield";
import { providerName } from "../data/providers";

export function PrivacyPanel(props: PanelProps) {
  const {
    provider,
    cloud,
    codex,
    detector,
    phase,
    preview,
    semantic,
    draftTerms,
    appliedTerms,
    termInput,
    termStatus,
    legend,
    inspect,
    factsConfirmed,
    identityConfirmed,
    model,
    localModel,
    pendingReview,
    privatePhase,
    busy,
  } = props.model;
  const {
    changeTermInput,
    addTerm,
    removeTerm,
    setInspect,
    reviewPreview,
    revealEmail,
    send,
  } = props.actions;
  const categoryLabels: Record<string, string> = {
    manual: "Cụm thủ công",
    email: "Email",
    personal: "Thông tin cá nhân",
    health: "Sức khỏe",
    financial: "Tài chính",
    credential: "Thông tin truy cập",
    confidential: "Thông tin nội bộ",
  };
  const policyLocked = busy;
  return (
    <aside
      className="privacy-panel"
      id="privacy-panel"
      aria-label="Kiểm tra riêng tư"
      tabIndex={-1}
    >
      <div className="panel-title">
        <div className="panel-symbol">
          <Shield small />
        </div>
        <div>
          <h2>Kiểm tra riêng tư</h2>
          <p>
            Kiểm tra bản gửi tới {cloud ? providerName(provider) : "provider"}
          </p>
        </div>
        <span className={`status-pill ${preview ? "ready" : ""}`}>
          {phase === "sending"
            ? "Đang gửi"
            : phase === "preparing"
              ? "Đang phân tích"
              : preview
                ? "Chờ duyệt"
                : "Chưa gửi"}
        </span>
      </div>
      <div className="detector-status" role="status">
        <span className={phase === "preparing" ? "spinner" : "detector-dot"} />
        <div>
          <strong>
            {detector === "qwen3-8b-local"
              ? "Qwen3 8B trên máy"
              : detector === "qwen3-4b-local"
                ? "Qwen3 4B trên máy"
                : semantic
                  ? "Bộ phát hiện trên máy"
                  : "Bộ lọc quy tắc"}
          </strong>
          <p>
            {phase === "preparing"
              ? "Đang đọc nội dung và ngữ cảnh…"
              : preview?.semantic
                ? `${preview.semantic.context_messages} lượt người dùng · ${(preview.semantic.elapsed_ms / 1000).toFixed(1)} giây phân tích`
                : semantic
                  ? "Phân tích ngữ nghĩa trước mỗi bản gửi"
                  : "Email và các cụm bạn chọn"}
          </p>
        </div>
      </div>
      <div className="policy-section">
        <label htmlFor="protected-terms">
          Cụm cần bảo vệ <span>Bổ sung</span>
        </label>
        <ul className="protected-terms" aria-label="Danh sách cụm bảo vệ">
          {[...appliedTerms, ...draftTerms].map((term) => (
            <li className="term-chip" key={term}>
              <span>{term}</span>
              {appliedTerms.includes(term) ? (
                <small>Đã áp dụng trong phiên · vẫn được che</small>
              ) : (
                <button
                  type="button"
                  aria-label={`Bỏ cụm “${term}”`}
                  disabled={policyLocked}
                  onClick={() => removeTerm(term)}
                >
                  Bỏ
                </button>
              )}
            </li>
          ))}
        </ul>
        <form
          className="term-entry"
          onSubmit={(event) => {
            event.preventDefault();
            addTerm();
          }}
        >
          <input
            id="protected-terms"
            name="protected-terms"
            autoComplete="off"
            value={termInput}
            disabled={policyLocked}
            onChange={(event) => changeTermInput(event.target.value)}
            placeholder="Nhập một cụm"
          />
          <button type="submit" disabled={policyLocked}>
            Thêm
          </button>
        </form>
        {termStatus && <p role="status">{termStatus}</p>}
        <p>
          {semantic
            ? "Không bắt buộc. Thêm thông tin bạn muốn giữ riêng ngoài đề xuất của model. Quy tắc đã duyệt tiếp tục được giữ trong phiên."
            : "Khớp chính xác, phân biệt hoa thường. Các cụm đã bảo vệ được giữ trong phiên."}
        </p>
      </div>
      <div className="preview-section">
        <div className="section-heading">
          <h3>Bản chuẩn bị gửi</h3>
          <span>
            {preview
              ? `${preview.detections.filter((d) => !d.unmasked_once).length} vùng đã che`
              : "CHƯA GỬI"}
          </span>
        </div>
        {preview ? (
          <>
            <div className="preview-tabs">
              <button
                className={inspect === "preview" ? "selected" : ""}
                aria-pressed={inspect === "preview"}
                onClick={() => setInspect("preview")}
              >
                Nội dung
              </button>
              {preview.semantic && (
                <button
                  className={inspect === "evidence" ? "selected" : ""}
                  aria-pressed={inspect === "evidence"}
                  onClick={() => setInspect("evidence")}
                >
                  Phát hiện ({preview.semantic.findings.length})
                </button>
              )}
              <button
                className={inspect === "json" ? "selected" : ""}
                aria-pressed={inspect === "json"}
                onClick={() => setInspect("json")}
              >
                Body JSON
              </button>
            </div>
            {preview.semantic &&
              /^\s*\[\[PG_v1_[0-9a-f]{32}\]\]\s*$/.test(preview.sanitized) && (
                <p className="overmask-note" role="status">
                  Toàn bộ nội dung đang bị che. Hãy tách yêu cầu cần provider
                  thực hiện thành một câu riêng và chuẩn bị lại để giữ được ý
                  nghĩa của yêu cầu.
                </p>
              )}
            {inspect !== "evidence" && (
              <pre className="preview-content" aria-label="Bản chuẩn bị gửi">
                {inspect === "preview"
                  ? preview.sanitized
                      .split(/(\[\[PG_v1_[0-9a-f]{32}\]\])/g)
                      .map((part, index) =>
                        /^\[\[PG_v1_[0-9a-f]{32}\]\]$/.test(part) ? (
                          <mark key={index}>{part}</mark>
                        ) : (
                          part
                        ),
                      )
                  : JSON.stringify(JSON.parse(preview.body), null, 2)}
              </pre>
            )}
            {preview.stage === "planner" && provider === "openai-api" && (
              <p className="preview-detail">
                OpenAI trả lời trên nội dung được duyệt và có thể lập công thức
                trên biến. Qwen tính giá trị dẫn xuất tại máy khi có công thức.
              </p>
            )}
            {inspect !== "evidence" && (
              <p className="preview-detail">
                {preview.history_count
                  ? `Có ${preview.history_count} message lịch sử đã lọc trong body. `
                  : ""}
                Bản duyệt có hiệu lực tối đa 5 phút. Sửa nội dung sẽ cần chuẩn
                bị lại.
                {codex &&
                  " JSON này là hội thoại gửi vào Codex; CLI bổ sung chỉ dẫn cố định và metadata giao thức."}
              </p>
            )}
            {preview.context && (
              <p className="preview-detail" aria-label="Ngữ cảnh gửi provider">
                Gửi đủ {preview.context.message_count} tin nhắn đã lọc; không
                cắt lịch sử. Kích thước bản duyệt: {preview.context.body_bytes}/
                {preview.context.body_limit_bytes} byte. Chưa đo số token của
                model; đây không phải giới hạn context window của model.
              </p>
            )}
            {preview.semantic && inspect === "evidence" && (
              <details className="semantic-evidence" open>
                <summary>
                  Model đã nhận diện gì?{" "}
                  <span>{preview.semantic.findings.length}</span>
                </summary>
                {preview.semantic.findings.length ? (
                  preview.semantic.findings.map((finding, index) => (
                    <div className="evidence-item" key={index}>
                      <span className="evidence-category">
                        {
                          (
                            {
                              personal: "Thông tin cá nhân",
                              health: "Sức khỏe",
                              financial: "Tài chính",
                              credential: "Thông tin truy cập",
                              confidential: "Thông tin nội bộ",
                            } as Record<string, string>
                          )[finding.category]
                        }
                      </span>
                      <p>{finding.quote}</p>
                      <small>
                        Nguồn {finding.source + 1} · căn cứ ở nguồn{" "}
                        {finding.evidence_source + 1}
                      </small>
                      <blockquote>{finding.evidence_quote}</blockquote>
                    </div>
                  ))
                ) : (
                  <p>
                    Không có đề xuất ngữ nghĩa. Bạn vẫn cần kiểm tra nội dung
                    trước khi gửi.
                  </p>
                )}
              </details>
            )}
            {preview.detections.some(
              (d) => d.category === "email" && d.restore_allowed,
            ) && (
              <section
                className="detections"
                aria-label="Email cho lần gửi này"
              >
                <h4>Email cho lần gửi này</h4>
                {preview.detections
                  .filter((d) => d.category === "email" && d.restore_allowed)
                  .map((d) => (
                    <div className="detection" key={`${d.start}:${d.end}`}>
                      <code>{d.value}</code>
                      {d.unmasked_once ? (
                        <strong role="status">
                          Sẽ gửi lộ lần này · lượt sau che lại
                        </strong>
                      ) : (
                        <button
                          type="button"
                          disabled={
                            policyLocked ||
                            [...appliedTerms, ...draftTerms].some((term) =>
                              d.value.includes(term),
                            )
                          }
                          onClick={() => void revealEmail(d.start, d.end)}
                        >
                          Gửi lộ lần này
                        </button>
                      )}
                    </div>
                  ))}
                <p>
                  Lựa chọn chỉ áp dụng cho bản gửi đang duyệt, không thay đổi
                  chính sách bảo vệ.
                </p>
              </section>
            )}
            {preview.detections.some((d) => !d.unmasked_once) &&
              (!legend || inspect === "preview") && (
                <section
                  className="detections"
                  aria-label={
                    legend
                      ? "Chỉ hiển thị trên máy — không gửi"
                      : "Giá trị giữ tại máy"
                  }
                >
                  <h4>
                    {legend
                      ? "Chỉ hiển thị trên máy — không gửi"
                      : "Giá trị giữ tại máy"}
                  </h4>
                  {(
                    legend ?? preview.detections.filter((d) => !d.unmasked_once)
                  ).map((d, i) => (
                    <div className="detection" key={i}>
                      {"token" in d && (
                        <code className="legend-token" title={d.token}>
                          {d.token.slice(8, 14)}…{d.token.slice(-6, -2)} →
                        </code>
                      )}
                      <code>{d.value}</code>
                      {"count" in d && d.count > 1 && <span>× {d.count}</span>}
                      <small>
                        {categoryLabels[d.category] ?? d.category} ·{" "}
                        {d.restore_allowed
                          ? "phục hồi tại máy"
                          : "không phục hồi"}
                      </small>
                      {d.category === "manual" &&
                      d.restore_allowed &&
                      draftTerms.includes(d.value) &&
                      !appliedTerms.includes(d.value) ? (
                        <button
                          type="button"
                          disabled={policyLocked}
                          onClick={() => removeTerm(d.value)}
                        >
                          Bỏ cụm này
                        </button>
                      ) : appliedTerms.includes(d.value) ||
                        !preview.semantic ||
                        d.category === "manual" ||
                        d.category === "email" ||
                        d.category === "credential" ||
                        !d.restore_allowed ? (
                        <small>
                          {appliedTerms.includes(d.value)
                            ? "Đã áp dụng trong phiên"
                            : "Quy tắc bắt buộc"}
                        </small>
                      ) : null}
                    </div>
                  ))}
                </section>
              )}
            {preview.review && preview.review.items.length > 0 && (
              <details className="preview-decisions" open>
                <summary>Điều chỉnh đề xuất mới</summary>
                <p>
                  Chỉ bỏ che đề xuất tự động chưa được lưu, đúng lần xuất hiện
                  này. Quy tắc, thông tin truy cập và bảo vệ đã xác nhận không
                  thay đổi. Lựa chọn chỉ áp dụng bản gửi này; lượt sau model có
                  thể nhận diện lại.
                </p>
                {preview.review.items.map((item) => (
                  <div key={item.candidate_id} className="preview-decision">
                    <blockquote>
                      “{item.quote}” · ký tự {item.start + 1}–{item.end}
                    </blockquote>
                    <p>
                      {item.rejected
                        ? "Bạn đã chọn không che trong bản gửi này."
                        : item.can_reject
                          ? "Đề xuất tự động mới, chưa lưu."
                          : "Giữ che: liên quan đến bảo vệ đã xác nhận, quy tắc hoặc quan hệ khác."}
                    </p>
                    <button
                      type="button"
                      disabled={policyLocked || !item.can_reject}
                      onClick={() => void reviewPreview(item.candidate_id)}
                    >
                      Không che đề xuất này trong bản gửi này
                    </button>
                  </div>
                ))}
              </details>
            )}
          </>
        ) : (
          <div className="empty-preview">
            <div>⌁</div>
            <strong>Bạn sẽ thấy bản gửi ở đây</strong>
            <p>
              Nhấn “Chuẩn bị bản gửi” để kiểm tra những gì được che trước khi
              duyệt.
            </p>
          </div>
        )}
      </div>
      <div className="scope-note">
        <span>i</span>
        <p>
          <strong>Bạn vẫn quyết định bản gửi</strong>
          {semantic
            ? "Model có thể bỏ sót hoặc che quá nhiều. Kiểm tra ngữ cảnh còn lại và bổ sung cụm bảo vệ khi cần."
            : "Bộ lọc quy tắc chưa nhận diện ngữ nghĩa. Kiểm tra nội dung và bổ sung các cụm cần giữ riêng."}
        </p>
      </div>
      {preview && (
        <div className="approval-footer">
          <button
            className="approve-btn"
            onClick={() => void send()}
            disabled={
              phase !== "ready" ||
              !factsConfirmed ||
              !identityConfirmed ||
              privatePhase !== "idle" ||
              busy
            }
          >
            <Shield small /> Duyệt & gửi bản này <span>→</span>
          </button>
          <div className="destination">
            Đích nhận:{" "}
            <strong>
              {cloud
                ? `${codex ? "Codex / OpenAI" : providerName(provider)} · ${model}`
                : localModel
                  ? `Qwen trên máy · ${model}`
                  : "Provider demo trên máy"}
            </strong>
          </div>
        </div>
      )}
      {pendingReview && (
        <div className="preview-review-pending" role="alert">
          <p>
            Chưa xác nhận thay đổi bản gửi. Các thao tác khác đang tạm khóa.
          </p>
          <button
            type="button"
            disabled={phase === "loading"}
            onClick={() => void reviewPreview()}
          >
            Kiểm tra lại thay đổi bản gửi
          </button>
        </div>
      )}
      <ProtectionPanel {...props} />
    </aside>
  );
}
