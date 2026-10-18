import { useState } from "react";

export type FactState = {
  enabled: boolean;
  epoch: number;
  facts: {
    id: string;
    revision: number;
    value: string;
    category: string;
    scope: string;
    superseded: boolean;
    source_id: string;
  }[];
  sources: { id: string; revision: number; text: string; digest?: string }[];
  entities?: {
    id: string;
    value: string;
    source_id: string;
    source_revision?: number;
    start?: number;
    end?: number;
    protected: boolean;
    aliases: {
      value: string;
      source_id: string;
      source_revision?: number;
      start?: number;
      end?: number;
    }[];
  }[];
};
export type Correction = {
  operation_id: string;
  fact_id: string;
  expected_revision: number;
  expected_epoch: number;
  text: string;
};
export const correctionErrors = new Set([
  "STATE_STALE",
  "STATE_IDENTITY_REVIEW_REQUIRED",
  "STATE_COMMAND_CONFLICT",
  "STATE_COMMAND_LIMIT",
  "STATE_LIMIT",
  "INVALID_INPUT",
  "SESSION_BUSY",
]);

export function FactPanel({
  state,
  busy,
  uncertain,
  onCorrect,
}: {
  state: FactState | null;
  busy: boolean;
  uncertain: boolean;
  onCorrect: (correction: Correction) => Promise<void>;
}) {
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState<Correction | null>(null);
  if (!state?.enabled) return null;
  const active = state.facts.filter((f) => !f.superseded);
  const fact = active.find((f) => f.id === selected);
  async function submit() {
    if (!fact || !state || busy || !text.trim()) return;
    const command = pending ?? {
      operation_id: crypto.randomUUID().replaceAll("-", ""),
      fact_id: fact.id,
      expected_revision: fact.revision,
      expected_epoch: state.epoch,
      text,
    };
    setPending(command);
    try {
      await onCorrect(command);
      setPending(null);
      setSelected("");
      setText("");
    } catch (reason) {
      if (reason instanceof Error && correctionErrors.has(reason.message))
        setPending(null);
    }
  }
  return (
    <details className="fact-panel">
      <summary>
        Thông tin đang bảo vệ <span>{active.length}</span>
      </summary>
      <p>
        Thông tin và căn cứ được giữ trên máy. Bản cũ vẫn được che sau khi sửa.
      </p>
      {!state.facts.length && (
        <p>Thông tin mới sẽ được lưu sau lượt gửi thành công.</p>
      )}
      <div className="fact-list">
        {state.facts.map((item) => (
          <article key={`${item.id}:${item.revision}`}>
            <div>
              <strong>{item.value}</strong>
              <small>
                {item.superseded ? "Bản cũ · vẫn bảo vệ" : "Hiện hành"}
              </small>
            </div>
            <details>
              <summary>Xem căn cứ</summary>
              <blockquote>
                {
                  state.sources.find((source) => source.id === item.source_id)
                    ?.text
                }
              </blockquote>
            </details>
            {!item.superseded && item.scope === "fact" && (
              <button
                type="button"
                disabled={busy || uncertain}
                onClick={() => {
                  setSelected(item.id);
                  setText("");
                  setPending(null);
                }}
              >
                Sửa thông tin
              </button>
            )}
          </article>
        ))}
      </div>
      {fact && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="fact-correction">
            Thông tin đúng thay cho “{fact.value}”
          </label>
          <textarea
            id="fact-correction"
            value={text}
            maxLength={800}
            disabled={busy || uncertain}
            onChange={(event) => {
              setText(event.target.value);
              setPending(null);
            }}
          />
          <p>Lưu cục bộ sẽ hủy bản duyệt cũ. Chuẩn bị lại trước khi gửi.</p>
          {uncertain && (
            <p role="alert">
              Chưa xác định lần lưu đã thành công. Thử lại cùng thao tác để kiểm
              tra; chat đang tạm khóa.
            </p>
          )}
          <button type="submit" disabled={busy || !text.trim()}>
            {uncertain ? "Kiểm tra / lưu lại" : "Lưu sửa đổi trên máy"}
          </button>
        </form>
      )}
    </details>
  );
}
