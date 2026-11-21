import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { Preview } from "../data/contracts";

type Adjust = { released?: string[]; masked?: string[] };
type Row = NonNullable<Preview["method_changes"]>[number];

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const shownAs = (row: Row) => (row.released ? row.original : row.sent);

export function MethodReview({
  preview,
  original,
  locked,
  onAdjust,
}: {
  preview: Preview;
  original: string;
  locked: boolean;
  onAdjust: (change: Adjust) => Promise<void>;
}) {
  const current = preview.method_adjust ?? { released: [], masked: [] };
  const rows = preview.method_changes ?? [];
  const [selection, setSelection] = useState("");
  const [active, setActive] = useState("");
  const [card, setCard] = useState<{ label: string; anchor: DOMRect } | null>(
    null,
  );
  const previewRef = useRef<HTMLPreElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const cardId = useId();
  const closing = useRef<number | undefined>(undefined);

  useEffect(() => {
    setSelection("");
    setCard(null);
    setActive("");
    return () => window.clearTimeout(closing.current);
  }, [preview.id, locked]);

  useLayoutEffect(() => {
    const element = cardRef.current;
    if (!card || !element) return;
    const { width, height } = element.getBoundingClientRect();
    element.style.left = `${Math.max(12, Math.min(card.anchor.left, window.innerWidth - width - 12))}px`;
    const top =
      card.anchor.bottom + height + 16 <= window.innerHeight
        ? card.anchor.bottom + 6
        : card.anchor.top - height - 6;
    element.style.top = `${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px`;
  }, [card]);

  useEffect(() => {
    if (!card) return;
    const dismiss = () => {
      setCard(null);
      setActive("");
    };
    const pointer = (event: PointerEvent) => {
      if (
        event.target instanceof Element &&
        !cardRef.current?.contains(event.target) &&
        !event.target.closest(".map-hit")
      )
        dismiss();
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        const restore = cardRef.current?.contains(document.activeElement);
        dismiss();
        if (restore) triggerRef.current?.focus();
      }
    };
    const scroll = (event: Event) => {
      if (
        !(event.target instanceof Node) ||
        !cardRef.current?.contains(event.target)
      )
        dismiss();
    };
    document.addEventListener("pointerdown", pointer);
    document.addEventListener("keydown", key);
    document.addEventListener("scroll", scroll, true);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("pointerdown", pointer);
      document.removeEventListener("keydown", key);
      document.removeEventListener("scroll", scroll, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [card]);

  const mappedLabels = [...new Set(rows.map(shownAs).filter(Boolean))];
  const unexplained = [
    ...new Set(
      [
        ...preview.sanitized.matchAll(
          /(?<!\[)\[(?=[^\[\]\r\n]*\p{L})[\p{L}\p{N}_ -]+\](?!\])/gu,
        ),
      ]
        .filter(
          (match) =>
            !mappedLabels.includes(match[0]) &&
            !original.includes(match[0]) &&
            !/^\s*\(/.test(
              preview.sanitized.slice(match.index + match[0].length),
            ),
        )
        .map((match) => match[0]),
    ),
  ];
  const labels = [...mappedLabels, ...unexplained].sort(
    (a, b) => b.length - a.length,
  );
  const parts = labels.length
    ? preview.sanitized.split(
        new RegExp(`(${labels.map(escape).join("|")})`, "g"),
      )
    : [preview.sanitized];

  function pick() {
    const selected = window.getSelection();
    const text = selected?.toString().trim() ?? "";
    setSelection(
      !locked &&
        selected?.anchorNode &&
        selected.focusNode &&
        previewRef.current?.contains(selected.anchorNode) &&
        previewRef.current.contains(selected.focusNode) &&
        text.length > 0 &&
        preview.sanitized.includes(text)
        ? text
        : "",
    );
  }
  function open(label: string, target: HTMLElement) {
    window.clearTimeout(closing.current);
    triggerRef.current = target;
    setActive(label);
    setCard({ label, anchor: target.getBoundingClientRect() });
  }
  function close() {
    window.clearTimeout(closing.current);
    closing.current = window.setTimeout(() => {
      if (cardRef.current?.contains(document.activeElement)) return;
      setCard(null);
      setActive("");
    }, 200);
  }
  function action(row: Row) {
    if (row.kind === "candidate")
      return {
        text: "Che cụm này",
        run: () => onAdjust({ masked: [...current.masked, row.original] }),
      };
    if (row.kind === "user")
      return {
        text: "Bỏ che",
        run: () =>
          onAdjust({
            masked: current.masked.filter((v) => v !== row.original),
          }),
      };
    if (row.released)
      return {
        text: "Che lại",
        run: () =>
          onAdjust({
            released: current.released.filter((v) => v !== row.original),
          }),
      };
    if (row.releasable)
      return {
        text: "Gửi nguyên văn",
        run: () => onAdjust({ released: [...current.released, row.original] }),
      };
    return null;
  }
  function rowView(row: Row, i: number) {
    const act = action(row);
    return (
      <div
        className={`detection${active === shownAs(row) ? " active" : ""}`}
        key={i}
        onMouseEnter={() => setActive(shownAs(row))}
        onMouseLeave={() => setActive("")}
      >
        <code>{row.original}</code>
        <span aria-label="thành">→</span>
        <code>
          {row.kind === "rewritten"
            ? "không còn trong bản gửi"
            : row.released || row.kind === "candidate"
              ? "gửi nguyên văn"
              : row.sent}
        </code>
        {act ? (
          <button
            type="button"
            disabled={locked}
            onClick={() => {
              setCard(null);
              void act.run();
            }}
          >
            {act.text}
          </button>
        ) : row.kind === "rewritten" ? null : (
          <small>Luôn che</small>
        )}
      </div>
    );
  }

  return (
    <>
      <p className="review-hint">
        Muốn che thêm: bôi đen một chữ hoặc cụm trong bản gửi dưới đây, rồi bấm
        “Che cụm này”.
      </p>
      <pre
        ref={previewRef}
        className="preview-content"
        aria-label="Bản chuẩn bị gửi"
        onMouseUp={pick}
        onKeyUp={pick}
      >
        {parts.map((part, i) =>
          labels.includes(part) ? (
            <mark
              key={i}
              role="button"
              tabIndex={0}
              aria-label={`Xem thay đổi ${part}`}
              aria-expanded={card?.label === part}
              aria-controls={card?.label === part ? cardId : undefined}
              className={`map-hit${active === part ? " active" : ""}${rows.some((r) => r.released && r.original === part) ? " released" : ""}${rows.some((r) => r.kind === "candidate" && r.original === part) ? " exposed" : ""}`}
              onMouseEnter={(event) => open(part, event.currentTarget)}
              onMouseLeave={close}
              onBlur={close}
              onClick={(event) => open(part, event.currentTarget)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  open(part, event.currentTarget);
                  requestAnimationFrame(() =>
                    cardRef.current
                      ?.querySelector<HTMLButtonElement>("button")
                      ?.focus(),
                  );
                }
              }}
            >
              {part}
            </mark>
          ) : (
            part
          ),
        )}
      </pre>
      {card && (
        <div
          ref={cardRef}
          id={cardId}
          className="map-card"
          role="region"
          aria-label={`Thay đổi ${card.label}`}
          onMouseEnter={() => window.clearTimeout(closing.current)}
          onMouseLeave={close}
          onFocus={() => window.clearTimeout(closing.current)}
          onBlur={close}
        >
          {unexplained.includes(card.label) ? (
            <>
              <strong>Chỗ trống do Qwen đặt</strong>
              <p>
                Qwen đã bỏ thông tin thật khi viết lại và thay bằng chỗ trống
                này. Thông tin thật không được gửi đi.
              </p>
            </>
          ) : (
            rows.filter((row) => shownAs(row) === card.label).map(rowView)
          )}
        </div>
      )}
      {selection && (
        <div className="selection-protection">
          <span>
            Đang chọn: <q>{selection}</q>
          </span>
          <button
            type="button"
            disabled={locked}
            onClick={() => {
              setSelection("");
              void onAdjust({ masked: [...current.masked, selection] });
            }}
          >
            Che cụm này
          </button>
          <button type="button" onClick={() => setSelection("")}>
            Bỏ chọn
          </button>
        </div>
      )}
      <section
        className="detections"
        aria-label="Thông tin riêng trong câu hỏi"
      >
        <h4>Thông tin riêng trong câu hỏi</h4>
        {rows.map(rowView)}
        {unexplained.length > 0 ? (
          <div className="unmapped-note">
            <strong>Chỗ trống do Qwen đặt</strong>
            <p>
              {unexplained.join(", ")}: thông tin thật không được gửi đi, không
              cần làm gì.
            </p>
          </div>
        ) : rows.length === 0 ? (
          <p>Không tìm thấy tên riêng nào trong câu hỏi.</p>
        ) : null}
      </section>
    </>
  );
}
