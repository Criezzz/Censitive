import { useState, useEffect } from "react";
import type {
  FactState,
  DraftOccurrence,
  DraftAction,
  DraftFact,
  FactCategory,
  ProtectionCommand,
} from "./data/contracts";
import { identityErrors } from "./data/errors";
import { selectedOccurrence, occurrenceContext } from "./data/occurrences";

export function IdentityPanel({
  state,
  busy,
  uncertain,
  draftSelection,
  actions,
  factMarks,
  onCommand,
  onStage,
  onStageFact,
  onClearDraft,
}: {
  state: FactState | null;
  busy: boolean;
  uncertain: boolean;
  draftSelection: DraftOccurrence | null;
  actions: DraftAction[];
  factMarks: DraftFact[];
  onCommand: (command: ProtectionCommand) => Promise<void>;
  onStage: (action: DraftAction) => void;
  onStageFact: (mark: DraftFact) => void;
  onClearDraft: () => void;
}) {
  const [sourceId, setSourceId] = useState("");
  const [selection, setSelection] = useState<DraftOccurrence | null>(null);
  const [entityId, setEntityId] = useState("");
  const [protect, setProtect] = useState(false);
  const [category, setCategory] = useState<FactCategory>("confidential");
  const [pending, setPending] = useState<ProtectionCommand | null>(null);
  useEffect(() => {
    setSelection(null);
    setProtect(false);
    setCategory("confidential");
  }, [state?.epoch]);
  if (!state?.enabled || !Array.isArray(state.entities)) return null;
  const sources = state.sources.filter((source) =>
    /^[a-f0-9]{64}$/.test(source.digest ?? ""),
  );
  const source = sources.find((item) => item.id === sourceId);
  const entity = state.entities.find((item) => item.id === entityId);
  const entityContext = entity ? occurrenceContext(state, entity) : null;
  const locked = busy || uncertain || pending !== null;
  const occurrence =
    source && selection
      ? {
          ...selection,
          source_id: source.id,
          source_revision: source.revision,
          source_digest: source.digest!,
        }
      : null;
  async function submit(command?: ProtectionCommand["command"]) {
    if (busy || (!pending && (uncertain || !command))) return;
    const operation = pending ?? {
      operation_id: crypto.randomUUID().replaceAll("-", ""),
      command: command!,
    };
    setPending(operation);
    try {
      await onCommand(operation);
      setPending(null);
      setSelection(null);
    } catch (reason) {
      if (reason instanceof Error && identityErrors.has(reason.message))
        setPending(null);
    }
  }
  return (
    <details className="identity-panel">
      <summary>
        Định danh, bí danh và fact <span>{state.entities.length}</span>
      </summary>
      <p>
        Chọn đúng lần xuất hiện. Hai người cùng tên vẫn là hai định danh riêng;
        không tự gộp theo tên.
      </p>
      {uncertain && (
        <div role="alert">
          <p>
            Chưa xác nhận thao tác bảo vệ. Giữ nguyên yêu cầu để kiểm tra lại;
            chat và các thay đổi khác đang tạm khóa.
          </p>
          <button
            type="button"
            disabled={busy || !pending}
            onClick={() => void submit()}
          >
            {pending?.command.kind === "create_fact"
              ? "Kiểm tra lại thao tác bảo vệ fact"
              : "Kiểm tra lại thao tác định danh"}
          </button>
        </div>
      )}
      <label htmlFor="identity-target">Định danh đã lưu</label>
      <select
        id="identity-target"
        value={entityId}
        disabled={locked}
        onChange={(event) => setEntityId(event.target.value)}
      >
        <option value="">Chọn đúng người hoặc đối tượng</option>
        {state.entities.map((item, index) => {
          const context = occurrenceContext(state, item);
          return (
            <option key={item.id} value={item.id} disabled={!context}>
              {item.value} — định danh {index + 1} ·{" "}
              {context
                ? `nguồn ${context.sourceNumber}, ký tự ${context.start}–${context.end}: ${context.before}«${item.value}»${context.after}`
                : "chưa có căn cứ đầy đủ"}
            </option>
          );
        })}
      </select>
      {entity && (
        <div className="identity-detail">
          <strong>{entity.value}</strong>
          {entityContext && (
            <blockquote className="identity-context">
              <small>
                Nguồn {entityContext.sourceNumber} · ký tự {entityContext.start}
                –{entityContext.end}
              </small>
              {entityContext.before}
              <mark>{entity.value}</mark>
              {entityContext.after}
            </blockquote>
          )}
          <span>
            {entity.protected
              ? "Đang bảo vệ định danh"
              : "Chưa đặt bảo vệ định danh"}
          </span>
          <p>
            Bí danh đã xác nhận:{" "}
            {entity.aliases.length
              ? entity.aliases.map((alias) => alias.value).join("; ")
              : "Chưa có"}
          </p>
          <button
            type="button"
            disabled={locked || entity.protected || !entityContext}
            onClick={() =>
              void submit({
                kind: "protect",
                expected_epoch: state.epoch,
                entity_id: entity.id,
              })
            }
          >
            Bảo vệ định danh đã chọn
          </button>
        </div>
      )}
      <label className="identity-checkbox">
        <input
          type="checkbox"
          checked={protect}
          disabled={locked}
          onChange={(event) => setProtect(event.target.checked)}
        />
        Bảo vệ định danh mới
      </label>
      <p>
        Tạo định danh không tự bật bảo vệ. Bật lựa chọn trên nếu muốn bảo vệ
        người hoặc đối tượng này.
      </p>
      <label htmlFor="fact-category">Loại fact cần bảo vệ</label>
      <select
        id="fact-category"
        value={category}
        disabled={locked}
        onChange={(event) => setCategory(event.target.value as FactCategory)}
      >
        <option value="confidential">Thông tin nội bộ</option>
        <option value="personal">Thông tin cá nhân</option>
        <option value="health">Sức khỏe</option>
        <option value="financial">Tài chính</option>
        <option value="credential">Thông tin truy cập — không phục hồi</option>
      </select>
      <p>
        Bảo vệ fact giữ riêng đúng thông tin đã chọn; không bảo vệ mọi lần xuất
        hiện của một tên. Không bỏ quy tắc bảo vệ đã có.
      </p>
      <fieldset disabled={locked}>
        <legend>Từ nguồn đã lưu</legend>
        <label htmlFor="identity-source">Nguồn để chọn lần xuất hiện</label>
        <select
          id="identity-source"
          value={sourceId}
          onChange={(event) => {
            setSourceId(event.target.value);
            setSelection(null);
          }}
        >
          <option value="">Chọn một nguồn đã lưu</option>
          {sources.map((item, index) => (
            <option key={item.id} value={item.id}>
              Nguồn {index + 1}: {Array.from(item.text).slice(0, 48).join("")}
            </option>
          ))}
        </select>
        {source && (
          <textarea
            aria-label="Chọn chính xác đoạn trong nguồn đã lưu"
            value={source.text}
            readOnly
            onSelect={(event) =>
              setSelection(
                selectedOccurrence(
                  source.text,
                  event.currentTarget.selectionStart,
                  event.currentTarget.selectionEnd,
                ),
              )
            }
          />
        )}
        <p className="identity-selection">
          {selection
            ? `Đã chọn “${selection.quote}” · vị trí ${selection.start}–${selection.end}`
            : "Bôi chọn tên, bí danh hoặc thông tin trong nguồn. Không nhập lại để đoán lần xuất hiện."}
        </p>
        <button
          type="button"
          disabled={!occurrence}
          onClick={() =>
            occurrence &&
            void submit({
              kind: "create_fact",
              expected_epoch: state.epoch,
              evidence: occurrence,
              category,
            })
          }
        >
          Bảo vệ fact từ đoạn đã chọn
        </button>
        <button
          type="button"
          disabled={!occurrence}
          onClick={() =>
            occurrence &&
            void submit({
              kind: "create",
              expected_epoch: state.epoch,
              evidence: occurrence,
              protect,
            })
          }
        >
          Thêm định danh từ đoạn đã chọn
        </button>
        <button
          type="button"
          disabled={!occurrence || !entity || !entityContext}
          onClick={() =>
            occurrence &&
            entity &&
            void submit({
              kind: "alias",
              expected_epoch: state.epoch,
              entity_id: entity.id,
              mention: occurrence,
            })
          }
        >
          Xác nhận đoạn đã chọn là bí danh
        </button>
      </fieldset>
      <fieldset disabled={locked}>
        <legend>Trong bản nháp hiện tại</legend>
        <p>
          Bôi chọn trong ô soạn hội thoại. Các lựa chọn chỉ được lưu khi lượt
          gửi thành công.
        </p>
        <p className="identity-draft-selection">
          {draftSelection
            ? `Đã chọn “${draftSelection.quote}” · vị trí ${draftSelection.start}–${draftSelection.end}`
            : "Chưa chọn đoạn trong ô soạn."}
        </p>
        <button
          type="button"
          disabled={!draftSelection || factMarks.length >= 16}
          onClick={() =>
            draftSelection &&
            onStageFact({ occurrence: draftSelection, category })
          }
        >
          Bảo vệ fact trong bản nháp
        </button>
        <p>{factMarks.length}/16 fact trong bản nháp; chưa lưu.</p>
        <ol className="fact-draft-marks">
          {factMarks.map((mark, index) => (
            <li key={index}>
              “{mark.occurrence.quote}” · {mark.occurrence.start}–
              {mark.occurrence.end} ·{" "}
              {mark.category === "credential"
                ? "Không phục hồi"
                : "Bảo vệ fact"}
            </li>
          ))}
        </ol>
        <button
          type="button"
          disabled={!draftSelection || actions.length >= 16}
          onClick={() =>
            draftSelection &&
            onStage({ kind: "create", occurrence: draftSelection, protect })
          }
        >
          Thêm định danh vào bản nháp
        </button>
        <button
          type="button"
          disabled={
            !draftSelection || !entity || !entityContext || actions.length >= 16
          }
          onClick={() =>
            draftSelection &&
            entity &&
            onStage({
              kind: "alias",
              occurrence: draftSelection,
              entity_id: entity.id,
            })
          }
        >
          Gắn đoạn nháp với định danh đã chọn
        </button>
        <p>
          {actions.length}/16 lựa chọn trong bản nháp. Chỉ liên kết tới định
          danh đã lưu.
        </p>
        <ol className="identity-draft-actions">
          {actions.map((action, index) => (
            <li key={index}>
              “{action.occurrence.quote}” · {action.occurrence.start}–
              {action.occurrence.end} ·{" "}
              {action.kind === "create"
                ? action.protect
                  ? "Định danh mới, có bảo vệ"
                  : "Định danh mới, chưa bảo vệ"
                : `Bí danh của định danh ${state.entities!.findIndex((item) => item.id === action.entity_id) + 1}`}
            </li>
          ))}
        </ol>
        <button
          type="button"
          disabled={!actions.length && !factMarks.length}
          onClick={onClearDraft}
        >
          Bỏ lựa chọn bảo vệ trong nháp
        </button>
      </fieldset>
    </details>
  );
}
