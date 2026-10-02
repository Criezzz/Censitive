export const MAX_STREAM_WIRE_BYTES = 4 * 1024 * 1024;
export const MAX_STREAM_FRAME_BYTES = 1_210_000;
export const MAX_STREAM_TEXT_BYTES = 200_000;

type Complete = {
  type: "complete";
  content: string;
  provider: string;
  prepared_id: string;
};

function invalid(): never {
  throw new Error("STREAM_INVALID");
}

function textBytes(value: unknown): number {
  if (typeof value !== "string") invalid();
  for (const character of value) {
    const point = character.codePointAt(0)!;
    if (point >= 0xd800 && point <= 0xdfff) invalid();
  }
  return new TextEncoder().encode(value).byteLength;
}

export async function readStream(
  response: Response,
  preparedId: string,
  onDelta: (text: string) => void,
  signal: AbortSignal,
): Promise<Complete> {
  const reader = response.body?.getReader();
  if (!reader) invalid();
  let ended = false;
  const abort = () => void reader.cancel().catch(() => {});
  signal.addEventListener("abort", abort, { once: true });
  try {
    if (
      !response.ok ||
      response.headers
        .get("content-type")
        ?.split(";")[0]
        .trim()
        .toLowerCase() !== "application/x-ndjson"
    )
      invalid();
    const frame = new Uint8Array(MAX_STREAM_FRAME_BYTES);
    let frameSize = 0;
    let wireBytes = 0;
    let outputBytes = 0;
    let accumulated = "";
    let terminal: Complete | null = null;
    const decoder = new TextDecoder("utf-8", { fatal: true });
    while (true) {
      if (signal.aborted) throw new Error("CANCELLED");
      const { value, done } = await reader.read();
      if (signal.aborted) throw new Error("CANCELLED");
      if (done) {
        ended = true;
        if (frameSize !== 0 || terminal === null)
          throw new Error("STREAM_INCOMPLETE");
        return terminal;
      }
      wireBytes += value.byteLength;
      if (wireBytes > MAX_STREAM_WIRE_BYTES) throw new Error("RESPONSE_LIMIT");
      let offset = 0;
      while (offset < value.byteLength) {
        if (terminal !== null) invalid();
        const newline = value.indexOf(10, offset);
        const end = newline === -1 ? value.byteLength : newline;
        const size = end - offset;
        if (frameSize + size > MAX_STREAM_FRAME_BYTES)
          throw new Error("RESPONSE_LIMIT");
        frame.set(value.subarray(offset, end), frameSize);
        frameSize += size;
        offset = end + (newline === -1 ? 0 : 1);
        if (newline === -1) continue;
        let parsed: unknown;
        try {
          parsed = JSON.parse(decoder.decode(frame.subarray(0, frameSize)));
        } catch {
          invalid();
        }
        frameSize = 0;
        if (
          parsed === null ||
          typeof parsed !== "object" ||
          Array.isArray(parsed)
        )
          invalid();
        const event = parsed as Record<string, unknown>;
        const fields = Object.keys(event).sort().join(",");
        if (event.prepared_id !== preparedId) invalid();
        if (event.type === "delta" && fields === "prepared_id,text,type") {
          outputBytes += textBytes(event.text);
          if (outputBytes > MAX_STREAM_TEXT_BYTES)
            throw new Error("RESPONSE_LIMIT");
          accumulated += event.text as string;
          onDelta(event.text as string);
        } else if (
          event.type === "complete" &&
          fields === "content,prepared_id,provider,type"
        ) {
          if (
            textBytes(event.content) > MAX_STREAM_TEXT_BYTES ||
            event.content !== accumulated ||
            typeof event.provider !== "string" ||
            !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(event.provider)
          )
            invalid();
          terminal = event as Complete;
        } else if (
          event.type === "error" &&
          fields === "code,prepared_id,type"
        ) {
          if (
            typeof event.code !== "string" ||
            !/^[A-Z][A-Z0-9_]{0,63}$/.test(event.code)
          )
            invalid();
          throw new Error(event.code);
        } else invalid();
      }
    }
  } finally {
    signal.removeEventListener("abort", abort);
    if (!ended) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
