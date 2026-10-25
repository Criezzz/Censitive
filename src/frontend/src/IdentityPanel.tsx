import { useEffect, useState } from "react";
import type { FactState } from "./FactPanel";

export type DraftOccurrence = { start: number; end: number; quote: string };
type Occurrence = DraftOccurrence & {
  source_id: string;
  source_revision: number;
  source_digest: string;
};
export type DraftAction =
  | { kind: "create"; occurrence: DraftOccurrence; protect: boolean }
  | { kind: "alias"; occurrence: DraftOccurrence; entity_id: string };
export type FactCategory =
  "confidential" | "personal" | "health" | "financial" | "credential";
export type DraftFact = { occurrence: DraftOccurrence; category: FactCategory };
export type CreateFactCommand = {
  operation_id: string;
  command: {
    kind: "create_fact";
    expected_epoch: number;
    evidence: Occurrence;
    category: FactCategory;
  };
};
export type ProtectionCommand = {
  operation_id: string;
  command: IdentityCommand["command"] | CreateFactCommand["command"];
};
export type FactResult = {
  receipt: {
    operation_id: string;
    committed_epoch: number;
    fact_id: string;
    fact_revision: number;
  };
  state: FactState;
};
export type IdentityCommand = {
  operation_id: string;
  command:
    | {
        kind: "create";
        expected_epoch: number;
        evidence: Occurrence;
        protect: boolean;
      }
    | { kind: "protect"; expected_epoch: number; entity_id: string }
    | {
        kind: "alias";
        expected_epoch: number;
        entity_id: string;
        mention: Occurrence;
      };
};
export type IdentityResult = {
  receipt: {
    operation_id: string;
    committed_epoch: number;
    entity_id: string;
    policy_fact_id: string | null;
  };
  state: FactState;
};
export const identityErrors = new Set([
  "STATE_STALE",
  "STATE_ENTITY_MISSING",
  "STATE_EVIDENCE_MISMATCH",
  "STATE_AMBIGUOUS_LINK",
  "STATE_COMMAND_CONFLICT",
  "STATE_COMMAND_LIMIT",
  "STATE_LIMIT",
  "STATE_INVALID",
  "STATE_SOURCE_MISSING",
  "STATE_CATEGORY_INVALID",
  "INVALID_INPUT",
  "SESSION_BUSY",
]);

function occurrenceContext(
  state: FactState,
  item: {
    value: string;
    source_id: string;
    source_revision?: number;
    start?: number;
    end?: number;
  },
) {
  const source = state.sources.find(
    (candidate) => candidate.id === item.source_id,
  );
  if (
    !source ||
    source.revision !== item.source_revision ||
    typeof item.start !== "number" ||
    typeof item.end !== "number" ||
    !Number.isInteger(item.start) ||
    !Number.isInteger(item.end)
  )
    return null;
  const chars = Array.from(source.text);
  if (
    item.start < 0 ||
    item.end <= item.start ||
    item.end > chars.length ||
    chars.slice(item.start, item.end).join("") !== item.value
  )
    return null;
  return {
    before: chars.slice(Math.max(0, item.start - 20), item.start).join(""),
    after: chars.slice(item.end, item.end + 20).join(""),
    sourceNumber: state.sources.indexOf(source) + 1,
    start: item.start + 1,
    end: item.end,
  };
}

export function selectedOccurrence(
  text: string,
  start: number,
  end: number,
): DraftOccurrence | null {
  if (start >= end || start < 0 || end > text.length) return null;
  const boundaries = [0];
  for (const character of text)
    boundaries.push(boundaries.at(-1)! + character.length);
  const a = boundaries.indexOf(start),
    b = boundaries.indexOf(end);
  const quote = text.slice(start, end);
  if (a < 0 || b < 0 || !quote.trim() || b - a > 800) return null;
  return { start: a, end: b, quote };
}

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
