import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ProvidersDialog } from "./ProvidersDialog";
import { readStream } from "./stream";
import {
  IdentityPanel,
  identityErrors,
  selectedOccurrence,
  type DraftAction,
  type DraftFact,
  type DraftOccurrence,
  type IdentityResult,
  type FactResult,
  type ProtectionCommand,
} from "./IdentityPanel";
import {
  FactPanel,
  correctionErrors,
  type Correction,
  type FactState,
} from "./FactPanel";
import "./style.css";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  provider?: string;
};
type Preview = {
  streaming: boolean;
  state?: FactState | null;
  id: string;
  sanitized: string;
  body: string;
  expires_in: number;
  history_count: number;
  semantic: null | {
    model: string;
    elapsed_ms: number;
    context_messages: number;
    findings: {
      quote: string;
      category: string;
      source: number;
      evidence_source: number;
      evidence_quote: string;
    }[];
  };
  detections: {
    start: number;
    end: number;
    category: string;
    value: string;
    restore_allowed: boolean;
  }[];
  review?: {
    expected_epoch: number;
    items: {
      candidate_id: string;
      start: number;
      end: number;
      quote: string;
      can_reject: boolean;
      locked_reason: string;
      rejected: boolean;
    }[];
  } | null;
};
type PreviewDecision = {
  operation_id: string;
  prepared_id: string;
  expected_epoch: number;
  command: { kind: "reject_new_mask"; candidate_id: string };
};
type PreviewDecisionResult = {
  receipt: {
    operation_id: string;
    old_prepared_id: string;
    new_prepared_id: string;
    body_sha256: string;
  };
  preview_available: boolean;
  preview: Preview | null;
};
const reviewErrors = new Set([
  "STALE_PREVIEW",
  "STATE_STALE",
  "SESSION_BUSY",
  "STATE_COMMAND_CONFLICT",
  "STATE_COMMAND_LIMIT",
  "PREVIEW_DECISION_LOCKED",
  "PREVIEW_DECISION_INVALID",
  "STATE_FACT_MISSING",
  "INVALID_INPUT",
  "STATE_INVALID",
  "STATE_LIMIT",
]);
type SessionView = {
  id: string;
  provider: string;
  model: string;
  messages: ChatMessage[];
  audit: { event: string; status: string; version: number }[];
  state?: FactState;
};
type Phase = "loading" | "idle" | "preparing" | "ready" | "sending";
type Bootstrap = {
  csrf: string;
  provider: string;
  model: string;
  detector: string;
  state_enabled?: boolean;
};
type SessionIdentity = { id: string; provider: string; model: string };

let csrf = "";
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers:
      method === "GET"
        ? {}
        : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.code ?? "REQUEST_FAILED");
  return result as T;
}

const errors: Record<string, string> = {
  STATE_REVIEW_REQUIRED:
    "Chưa xác định chắc chắn thông tin hoặc quan hệ đang được nói tới. Hãy viết rõ người/sự việc và phạm vi cần giữ riêng rồi chuẩn bị lại. Chưa gửi dữ liệu ra ngoài.",
  STATE_STALE:
    "Thông tin đã thay đổi. Kiểm tra bản hiện hành rồi lưu lại sửa đổi.",
  STATE_LIMIT:
    "Bộ nhớ bảo vệ đã đạt giới hạn của phiên. Hãy tạo phiên mới; ứng dụng không tự bỏ thông tin cũ.",
  STATE_COMMAND_CONFLICT:
    "Mã thao tác đã được dùng cho một thay đổi khác. Kiểm tra trạng thái trước khi sửa tiếp.",
  STATE_IDENTITY_REVIEW_REQUIRED:
    "Thay đổi danh tính cần xác định lại đúng người và phạm vi. Chưa áp dụng sửa đổi này.",
  STATE_EVIDENCE_MISMATCH:
    "Đoạn đã chọn không còn khớp nguồn. Hãy chọn lại đúng lần xuất hiện.",
  STATE_ENTITY_MISSING:
    "Định danh đã chọn không còn trong phiên. Hãy tải lại trạng thái và chọn lại.",
  STATE_AMBIGUOUS_LINK:
    "Các lựa chọn chồng lên nhau nhưng chỉ tới những định danh khác nhau. Hãy bỏ lựa chọn nháp rồi chọn lại.",
  STATE_IDENTITY_NOT_APPLIED:
    "Bản chuẩn bị chưa giữ đầy đủ lựa chọn bảo vệ danh tính. Chưa gửi; hãy chuẩn bị lại.",
  STATE_FACT_NOT_APPLIED:
    "Bản chuẩn bị chưa giữ đầy đủ thông tin bạn chọn bảo vệ. Chưa gửi; hãy chuẩn bị lại.",
  IDENTITY_OUTCOME_UNKNOWN:
    "Chưa xác nhận kết quả thao tác bảo vệ. Hãy kiểm tra lại đúng thao tác đang chờ trước khi tiếp tục.",
  PREVIEW_DECISION_LOCKED:
    "Đề xuất này liên quan đến quy tắc hoặc thông tin đang được bảo vệ, nên không thể bỏ che tại đây. Hãy chuẩn bị lại.",
  PREVIEW_DECISION_INVALID:
    "Lựa chọn không còn khớp bản chuẩn bị. Chưa gửi; hãy chuẩn bị lại.",
  PREVIEW_OUTCOME_UNKNOWN:
    "Chưa xác nhận thay đổi bản gửi. Kiểm tra lại đúng thao tác đang chờ; không tự gửi lại.",
  SEMANTIC_MODEL_MISMATCH:
    "Runtime đang trả lời bằng model khác cấu hình. Hãy khởi động lại đúng profile trước khi chuẩn bị bản gửi.",
  SEMANTIC_UNAVAILABLE:
    "Model local chưa sẵn sàng. Khởi động bộ phân tích Qwen rồi chuẩn bị lại. Chưa gửi dữ liệu ra ngoài.",
  SEMANTIC_TIMEOUT:
    "Phân tích local hết thời gian chờ. Hãy rút ngắn nội dung rồi thử lại.",
  SEMANTIC_CONTEXT_LIMIT:
    "Ngữ cảnh vượt 6.000 ký tự cho bộ phân tích local. Hãy tạo phiên mới hoặc rút ngắn nội dung; ứng dụng không tự cắt lịch sử.",
  SEMANTIC_BUSY:
    "Model local đang hoàn tất một lượt phân tích. Chờ một lúc rồi chuẩn bị lại.",
  SEMANTIC_INVALID_OUTPUT:
    "Kết quả phân tích local chưa hợp lệ. Chưa có bản được phép gửi; hãy thử chuẩn bị lại.",
  SEMANTIC_UNGROUNDED:
    "Model đề xuất một đoạn không khớp nguồn. Yêu cầu được giữ tại máy; hãy chuẩn bị lại.",
  SEMANTIC_INCOMPLETE:
    "Model chưa hoàn tất phân tích. Hãy rút ngắn nội dung rồi chuẩn bị lại.",
  RESERVED_TOKEN:
    "Nội dung có ký hiệu [[PG_ dành riêng cho hệ thống. Hãy bỏ ký hiệu đó trước khi tiếp tục.",
  OVERLAPPING_DETECTIONS:
    "Các vùng cần che chồng lấn. Hãy chỉnh danh sách cụm bảo vệ để mỗi vùng được xác định rõ.",
  STALE_PREVIEW:
    "Bản xem trước đã hết hạn hoặc thay đổi. Hãy chuẩn bị lại trước khi gửi.",
  CANCELLED: "Đã hủy. Phản hồi này không được thêm vào lịch sử.",
  SESSION_BUSY: "Phiên đang xử lý. Hãy chờ hoặc hủy lượt hiện tại.",
  HISTORY_LIMIT: "Phiên đã đạt giới hạn demo. Hãy tạo phiên mới để tiếp tục.",
  DETECTION_LIMIT:
    "Nội dung có quá nhiều vùng cần che. Hãy chia thành yêu cầu ngắn hơn.",
  VAULT_LIMIT: "Phiên đã đạt giới hạn giá trị bảo vệ. Hãy tạo phiên mới.",
  RESPONSE_LIMIT:
    "Phản hồi vượt giới hạn kích thước; lượt này chưa được lưu vào lịch sử.",
  INVALID_INPUT:
    "Nội dung không hợp lệ. Chỉ nhập text, tối đa 20.000 ký tự và 30 cụm bảo vệ.",
  INVALID_TERMS:
    "Mỗi cụm bảo vệ cần 1–200 ký tự, không chứa ký hiệu token dành riêng.",
  OUTBOUND_BLOCKED:
    "Bước kiểm tra cuối phát hiện nội dung chưa được che. Yêu cầu đã bị chặn.",
  UNKNOWN_TOKEN:
    "Phản hồi chứa token không được cấp cho lượt này. Không phục hồi hoặc lưu phản hồi.",
  MALFORMED_TOKEN:
    "Token trong phản hồi không hợp lệ. Không phục hồi hoặc lưu phản hồi.",
  AUTH_REQUIRED: "Phiên kết nối đã hết hiệu lực. Hãy tải lại trang.",
  SESSION_NOT_FOUND:
    "Phiên không còn tồn tại. Hãy tải lại trang hoặc tạo phiên mới.",
  PROVIDER_FAILED:
    "Provider không hoàn tất phản hồi. Bạn có thể chuẩn bị lại và thử lại.",
  PROVIDER_BUSY: "Codex đang xử lý một lượt khác. Hãy thử lại sau.",
  CODEX_VERSION_UNVERIFIED:
    "Phiên bản Codex đã thay đổi. Cần kiểm chứng lại trước khi kết nối.",
  CODEX_CHATGPT_LOGIN_REQUIRED:
    "Hãy đăng nhập Codex bằng ChatGPT rồi chuẩn bị lại lượt này.",
  CODEX_UNEXPECTED_TOOL:
    "Codex trả về thao tác công cụ ngoài phạm vi chat. Lượt này đã bị chặn.",
};

const samples = [
  {
    icon: "↗",
    title: "Soạn email riêng tư",
    subtitle: "Tên, email và dự án",
    text: "Viết email cho Lan tại lan@example.com về tiến độ dự án Maple.",
    terms: "Lan\nMaple",
  },
  {
    icon: "⌘",
    title: "Giữ kín mã truy cập",
    subtitle: "Che mã và không phục hồi",
    text: "Nhắc tôi thay mã truy cập DEMO_SECRET_ALPHA_001 trước buổi demo.",
    terms: "",
  },
  {
    icon: "◎",
    title: "Bảo vệ một cụm từ",
    subtitle: "Bạn xác định phạm vi cần che",
    text: "Soạn lời nhắn về kế hoạch giảm 30 vị trí cho nhóm quản lý.",
    terms: "kế hoạch giảm 30 vị trí",
  },
];

function Shield({ small = false }: { small?: boolean }) {
  return (
    <svg
      width={small ? 18 : 25}
      height={small ? 18 : 25}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M12 3 4 6v6c0 4.4 4.8 7.9 8 9 3.2-1.1 8-4.6 8-9V6l-8-3Z"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="m8 12 2.5 2.5L16 9"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function App() {
  const [provider, setProvider] = useState("local-demo");
  const [model, setModel] = useState("");
  const [showProviders, setShowProviders] = useState(false);
  const [detector, setDetector] = useState("rules");
  const semantic = detector !== "rules";
  const preparingId = useRef("");
  const [providerConfirmed, setProviderConfirmed] = useState(false);
  const cloud = provider === "codex-chatgpt";
  const localModel = provider === "local-qwen";
  const [session, setSession] = useState("");
  const [factState, setFactState] = useState<FactState | null>(null);
  const [factsConfirmed, setFactsConfirmed] = useState(true);
  const [identityConfirmed, setIdentityConfirmed] = useState(true);
  const identityOperation = useRef<string | null>(null);
  const [draftActions, setDraftActions] = useState<DraftAction[]>([]);
  const [draftFacts, setDraftFacts] = useState<DraftFact[]>([]);
  const [draftSelection, setDraftSelection] = useState<DraftOccurrence | null>(
    null,
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [provisional, setProvisional] = useState<string | null>(null);
  const activeStream = useRef<AbortController | null>(null);
  const streamCancelRequested = useRef(false);
  const streamCancellation = useRef<Promise<unknown> | null>(null);
  const [draft, setDraft] = useState("");
  const [terms, setTerms] = useState("");
  const [phase, setPhase] = useState<Phase>("loading");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [pendingReview, setPendingReview] = useState<PreviewDecision | null>(
    null,
  );
  const [error, setError] = useState("");
  const [inspect, setInspect] = useState<"preview" | "json" | "evidence">(
    "preview",
  );
  const [audit, setAudit] = useState<SessionView["audit"]>([]);
  const [showAudit, setShowAudit] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const generation = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const providerButton = useRef<HTMLButtonElement>(null);
  const busy =
    phase === "preparing" ||
    phase === "sending" ||
    phase === "loading" ||
    pendingReview !== null;

  function explain(reason: unknown) {
    const code = reason instanceof Error ? reason.message : "REQUEST_FAILED";
    setError(
      errors[code] ??
        "Không thể kết nối ứng dụng local. Kiểm tra backend rồi thử lại.",
    );
  }
  async function refresh(id: string) {
    const state = await api<SessionView>(`/sessions/${id}`);
    setProvider(state.provider);
    setModel(state.model);
    setProviderConfirmed(true);
    setMessages(state.messages);
    setAudit(state.audit);
    setFactState(state.state ?? null);
  }
  async function initialize() {
    try {
      const boot = await api<Bootstrap>("/bootstrap");
      csrf = boot.csrf;
      setDetector(boot.detector);
      setProvider(boot.provider);
      setModel(boot.model);
      const created = await api<SessionIdentity>("/sessions", "POST");
      setProvider(created.provider);
      setModel(created.model);
      setSession(created.id);
      setFactState({
        enabled: !!boot.state_enabled,
        epoch: 0,
        facts: [],
        sources: [],
        entities: [],
      });
      setFactsConfirmed(true);
      setProviderConfirmed(true);
      setPhase("idle");
    } catch (reason) {
      explain(reason);
      setPhase("idle");
    }
  }
  useEffect(() => {
    void initialize();
    return () => activeStream.current?.abort();
  }, []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, phase, provisional]);
  useEffect(() => {
    setDraftActions([]);
    setDraftFacts([]);
    setDraftSelection(null);
  }, [factState?.epoch, session, provider]);

  function clearDraftIdentity() {
    setDraftActions([]);
    setDraftFacts([]);
    setDraftSelection(null);
  }
  function invalidate(clearIdentity = true) {
    if (clearIdentity) clearDraftIdentity();
    generation.current++;
    if (preview && session)
      void api(`/sessions/${session}/discard`, "POST", {
        prepared_id: preview.id,
      }).catch(explain);
    setPreview(null);
    setError("");
    if (phase !== "sending" && phase !== "loading") setPhase("idle");
  }
  async function selectProvider(nextProvider: string, nextModel: string) {
    if (busy || !identityConfirmed) throw new Error("SESSION_BUSY");
    clearDraftIdentity();
    setPhase("loading");
    generation.current++;
    setPreview(null);
    setProviderConfirmed(false);
    try {
      const selected = await api<SessionIdentity>(
        session ? `/sessions/${session}/provider` : "/sessions",
        "POST",
        { provider: nextProvider, model: nextModel },
      );
      setSession(selected.id);
      setProvider(selected.provider);
      setModel(selected.model);
      setProviderConfirmed(true);
      setError("");
      await refresh(selected.id).catch(() => {
        setError(
          "Đã đổi provider, nhưng chưa tải lại được lịch sử và audit. Hãy kiểm tra kết nối ứng dụng.",
        );
      });
    } catch (reason) {
      if (session) await refresh(session).catch(() => {});
      setProviderConfirmed(false);
      setError(
        "Lựa chọn provider chưa được xác nhận đầy đủ. Mở Provider & kết nối để chọn lại nếu chưa thể chuẩn bị bản gửi.",
      );
      throw reason;
    } finally {
      setPhase("idle");
    }
  }
  async function prepare() {
    if (
      !draft.trim() ||
      !session ||
      busy ||
      !providerConfirmed ||
      !factsConfirmed ||
      !identityConfirmed
    )
      return;
    const attempt = ++generation.current;
    preparingId.current = crypto.randomUUID().replaceAll("-", "");
    setError("");
    setPreview(null);
    setPhase("preparing");
    try {
      const text = draft;
      const actions = draftActions;
      const marks = draftFacts;
      const epoch = factState?.epoch;
      let draftIdentity;
      let draftFactInput;
      if (actions.length || marks.length) {
        const bytes = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(text),
        );
        if (attempt !== generation.current) return;
        const textDigest = Array.from(new Uint8Array(bytes), (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
        if (actions.length)
          draftIdentity = {
            expected_epoch: epoch,
            text_digest: textDigest,
            actions,
          };
        if (marks.length)
          draftFactInput = {
            expected_epoch: epoch,
            text_digest: textDigest,
            marks,
          };
      }
      const result = await api<Preview>(
        `/sessions/${session}/prepare`,
        "POST",
        {
          text,
          request_id: preparingId.current,
          terms: terms
            .split("\n")
            .map((t) => t.trim())
            .filter(Boolean),
          ...(draftIdentity ? { draft_identity: draftIdentity } : {}),
          ...(draftFactInput ? { draft_facts: draftFactInput } : {}),
        },
      );
      if (attempt !== generation.current) return;
      setPreview(result);
      setInspect("preview");
      setPhase("ready");
    } catch (reason) {
      if (attempt === generation.current) {
        explain(reason);
        setPhase("idle");
      }
    }
  }
  async function waitStreamCancellation() {
    await streamCancellation.current?.catch(() => {});
  }
  async function reviewPreview(candidateId?: string) {
    if (
      phase === "loading" ||
      phase === "preparing" ||
      phase === "sending" ||
      !session ||
      !identityConfirmed ||
      !factsConfirmed ||
      !providerConfirmed
    )
      return;
    if (
      !pendingReview &&
      (!preview?.review ||
        !candidateId ||
        !preview.review.items.some(
          (item) => item.candidate_id === candidateId && item.can_reject,
        ))
    )
      return;
    const operation = pendingReview ?? {
      operation_id: crypto.randomUUID().replaceAll("-", ""),
      prepared_id: preview!.id,
      expected_epoch: preview!.review!.expected_epoch,
      command: { kind: "reject_new_mask" as const, candidate_id: candidateId! },
    };
    const attempt = pendingReview ? generation.current : ++generation.current;
    setPendingReview(operation);
    setPreview(null);
    setPhase("loading");
    setError("");
    try {
      const { prepared_id, ...body } = operation;
      const result = await api<PreviewDecisionResult>(
        `/sessions/${session}/prepared/${prepared_id}/decisions`,
        "POST",
        body,
      );
      const receipt = result.receipt;
      if (
        receipt?.operation_id !== operation.operation_id ||
        receipt.old_prepared_id !== operation.prepared_id ||
        receipt.new_prepared_id === operation.prepared_id ||
        !/^[a-f0-9]{32}$/.test(receipt.new_prepared_id) ||
        !/^[a-f0-9]{64}$/.test(receipt.body_sha256) ||
        typeof result.preview_available !== "boolean"
      )
        throw new Error("PREVIEW_OUTCOME_UNKNOWN");
      if (result.preview_available) {
        if (
          !result.preview ||
          result.preview.id !== receipt.new_prepared_id ||
          typeof result.preview.body !== "string"
        )
          throw new Error("PREVIEW_OUTCOME_UNKNOWN");
        const digest = await crypto.subtle.digest(
          "SHA-256",
          new TextEncoder().encode(result.preview.body),
        );
        const actual = Array.from(new Uint8Array(digest), (byte) =>
          byte.toString(16).padStart(2, "0"),
        ).join("");
        if (actual !== receipt.body_sha256)
          throw new Error("PREVIEW_OUTCOME_UNKNOWN");
      } else if (result.preview !== null)
        throw new Error("PREVIEW_OUTCOME_UNKNOWN");
      if (attempt !== generation.current) return;
      setPendingReview(null);
      setPreview(result.preview);
      setInspect("preview");
      setPhase(result.preview ? "ready" : "idle");
      setError(
        result.preview
          ? "Đã tạo bản gửi mới. Hãy xem lại và duyệt trước khi gửi."
          : "Đã xác nhận thao tác, nhưng bản gửi đó không còn hiệu lực. Hãy chuẩn bị lại; không tự gửi lại.",
      );
    } catch (reason) {
      if (attempt !== generation.current) return;
      explain(reason);
      if (reason instanceof Error && reviewErrors.has(reason.message)) {
        try {
          await refresh(session);
          setPendingReview(null);
        } catch {
          setError(errors.PREVIEW_OUTCOME_UNKNOWN);
        }
      }
      setPhase("idle");
    }
  }
  async function send() {
    if (
      !preview ||
      phase !== "ready" ||
      !factsConfirmed ||
      !identityConfirmed ||
      pendingReview
    )
      return;
    const streaming = preview.streaming === true;
    const controller = streaming ? new AbortController() : null;
    activeStream.current = controller;
    streamCancelRequested.current = false;
    streamCancellation.current = null;
    setProvisional(null);
    setPhase("sending");
    setError("");
    setCancelPending(false);
    try {
      if (controller) {
        const response = await fetch(`/api/sessions/${session}/send-stream`, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
          },
          body: JSON.stringify({ prepared_id: preview.id }),
          signal: controller.signal,
        });
        await readStream(
          response,
          preview.id,
          (text) => {
            if (!streamCancelRequested.current)
              setProvisional((current) => (current ?? "") + text);
          },
          controller.signal,
        );
        if (streamCancelRequested.current) {
          await waitStreamCancellation();
          throw new Error("CANCELLED");
        }
      } else {
        await api(`/sessions/${session}/send`, "POST", {
          prepared_id: preview.id,
        });
      }
      await refresh(session);
      setDraft("");
    } catch (reason) {
      setProvisional(null);
      if (streaming) await waitStreamCancellation();
      if (streaming)
        setError(
          "Phản hồi trực tiếp đã bị gián đoạn. Nội dung tạm thời đã được bỏ; lịch sử được tải lại để xác nhận những gì đã lưu. Không tự gửi lại lượt này.",
        );
      else explain(reason);
      await refresh(session).catch(() => {
        if (streaming)
          setError(
            "Phản hồi trực tiếp đã bị gián đoạn. Chưa tải lại được lịch sử nên chưa xác nhận kết quả lưu. Nội dung tạm thời đã được bỏ; không tự gửi lại lượt này.",
          );
      });
    } finally {
      controller?.abort();
      activeStream.current = null;
      setProvisional(null);
      setPreview(null);
      setPhase("idle");
      setCancelPending(false);
      clearDraftIdentity();
      input.current?.focus();
    }
  }
  async function cancel() {
    clearDraftIdentity();
    setCancelPending(true);
    const controller = activeStream.current;
    if (controller) {
      streamCancelRequested.current = true;
      setProvisional(null);
    }
    try {
      const cancellation = api(`/sessions/${session}/cancel`, "POST", {
        prepared_id: preview?.id,
      });
      if (controller) {
        streamCancellation.current = cancellation;
        controller.abort();
      }
      await cancellation;
    } catch (reason) {
      explain(reason);
      setCancelPending(false);
    } finally {
      controller?.abort();
    }
  }
  async function correctFact(command: Correction) {
    if (busy || !session || !identityConfirmed || !providerConfirmed)
      throw new Error("SESSION_BUSY");
    clearDraftIdentity();
    generation.current++;
    setPhase("loading");
    setPreview(null);
    setFactsConfirmed(false);
    setError("");
    try {
      const state = await api<FactState>(
        `/sessions/${session}/facts/correct`,
        "POST",
        command,
      );
      setFactState(state);
      setFactsConfirmed(true);
      await refresh(session).catch(() =>
        setError("Đã lưu sửa đổi trên máy; chưa tải lại được audit."),
      );
    } catch (reason) {
      explain(reason);
      if (reason instanceof Error && correctionErrors.has(reason.message)) {
        await refresh(session)
          .then(() => setFactsConfirmed(true))
          .catch(() => {});
      }
      throw reason;
    } finally {
      setPhase("idle");
    }
  }
  async function changeIdentity(command: ProtectionCommand) {
    if (busy || !session || !factsConfirmed || !providerConfirmed)
      throw new Error("SESSION_BUSY");
    if (
      identityOperation.current &&
      identityOperation.current !== command.operation_id
    )
      throw new Error("IDENTITY_OUTCOME_UNKNOWN");
    if (!identityOperation.current) {
      identityOperation.current = command.operation_id;
      generation.current++;
      setPreview(null);
      clearDraftIdentity();
    }
    setIdentityConfirmed(false);
    setPhase("loading");
    setError("");
    try {
      const factCommand = command.command.kind === "create_fact";
      const result = await api<IdentityResult | FactResult>(
        `/sessions/${session}/${factCommand ? "facts/create" : "identity"}`,
        "POST",
        command,
      );
      if (
        result.receipt?.operation_id !== command.operation_id ||
        !result.state?.enabled ||
        !Array.isArray(result.state.entities) ||
        !Number.isInteger(result.receipt.committed_epoch) ||
        result.state.epoch < result.receipt.committed_epoch
      )
        throw new Error("IDENTITY_OUTCOME_UNKNOWN");
      if (factCommand) {
        const receipt = result.receipt;
        if (
          !("fact_id" in receipt) ||
          !/^[a-f0-9]{32}$/.test(receipt.fact_id) ||
          !Number.isInteger(receipt.fact_revision) ||
          receipt.fact_revision < 1 ||
          !result.state.facts.some(
            (fact) =>
              fact.id === receipt.fact_id &&
              fact.revision === receipt.fact_revision,
          )
        )
          throw new Error("IDENTITY_OUTCOME_UNKNOWN");
      }
      setFactState(result.state);
      setIdentityConfirmed(true);
      identityOperation.current = null;
    } catch (reason) {
      explain(reason);
      if (reason instanceof Error && identityErrors.has(reason.message)) {
        try {
          await refresh(session);
          setIdentityConfirmed(true);
          identityOperation.current = null;
        } catch {
          throw new Error("IDENTITY_OUTCOME_UNKNOWN");
        }
      }
      throw reason;
    } finally {
      setPhase("idle");
    }
  }
  function stageIdentity(action: DraftAction) {
    if (
      busy ||
      !identityConfirmed ||
      !factsConfirmed ||
      !providerConfirmed ||
      !factState?.enabled ||
      !Array.isArray(factState.entities) ||
      draftActions.length >= 16
    )
      return;
    if (
      Array.from(draft)
        .slice(action.occurrence.start, action.occurrence.end)
        .join("") !== action.occurrence.quote
    )
      return;
    if (
      action.kind === "alias" &&
      !factState.entities.some((entity) => entity.id === action.entity_id)
    )
      return;
    invalidate(false);
    setDraftActions((current) =>
      current.length < 16 ? [...current, action] : current,
    );
  }
  function stageFact(mark: DraftFact) {
    if (
      busy ||
      !identityConfirmed ||
      !factsConfirmed ||
      !providerConfirmed ||
      !factState?.enabled ||
      draftFacts.length >= 16 ||
      Array.from(draft)
        .slice(mark.occurrence.start, mark.occurrence.end)
        .join("") !== mark.occurrence.quote
    )
      return;
    invalidate(false);
    setDraftFacts((current) =>
      current.length < 16 ? [...current, mark] : current,
    );
  }
  async function cancelPrepare() {
    clearDraftIdentity();
    generation.current++;
    setCancelPending(true);
    try {
      await api(`/sessions/${session}/cancel-prepare`, "POST", {
        prepared_id: preparingId.current,
      });
      setPreview(null);
      setPhase("idle");
      setError(
        "Đã hủy bản chuẩn bị. Model local có thể cần thêm thời gian để kết thúc lượt phân tích.",
      );
    } catch (reason) {
      explain(reason);
    } finally {
      setCancelPending(false);
    }
  }
  async function reset() {
    if (busy || !identityConfirmed) return;
    clearDraftIdentity();
    setPhase("loading");
    setError("");
    generation.current++;
    setPreview(null);
    try {
      if (session) {
        try {
          await api(`/sessions/${session}`, "DELETE");
        } catch (reason) {
          if (
            !(reason instanceof Error) ||
            !["SESSION_NOT_FOUND", "AUTH_REQUIRED"].includes(reason.message)
          )
            throw reason;
        }
      }
      setSession("");
      setMessages([]);
      setAudit([]);
      const boot = await api<Bootstrap>("/bootstrap");
      csrf = boot.csrf;
      const created = await api<SessionIdentity>("/sessions", "POST", {
        provider: model ? provider : boot.provider,
        model: model || boot.model,
      });
      setProvider(created.provider);
      setModel(created.model);
      setProviderConfirmed(true);
      generation.current++;
      setSession(created.id);
      setMessages([]);
      setDraft("");
      setTerms("");
      setFactState({
        enabled: !!boot.state_enabled,
        epoch: 0,
        facts: [],
        sources: [],
        entities: [],
      });
      setFactsConfirmed(true);
      setPreview(null);
      setAudit([]);
      setShowAudit(false);
    } catch (reason) {
      explain(reason);
    } finally {
      setPhase("idle");
    }
  }
  function useSample(sample: (typeof samples)[number]) {
    invalidate();
    setDraft(sample.text);
    setTerms(sample.terms);
    input.current?.focus();
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Đến nội dung hội thoại
      </a>
      {showProviders && (
        <ProvidersDialog
          current={provider}
          request={api}
          onApply={selectProvider}
          onClose={() => {
            setShowProviders(false);
            requestAnimationFrame(() => providerButton.current?.focus());
          }}
        />
      )}
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Censitive">
          <span className="brand-mark">
            <Shield />
          </span>
          <span>
            Censitive
            <span className="brand-sub">Không gian hội thoại riêng tư</span>
          </span>
        </a>
        <button
          className="new-session"
          onClick={() => void reset()}
          disabled={busy || !identityConfirmed}
        >
          <span>＋</span> Phiên mới
        </button>
        <div className="nav-label">Hội thoại</div>
        <div className="nav-active">
          <span>▤</span> Hội thoại riêng tư <span className="nav-dot" />
        </div>
        <div className="session-note">
          Phiên {session ? session.slice(0, 8) : "đang kết nối"}
          <br />
          Lưu trong bộ nhớ của backend
        </div>
        <div className="sidebar-bottom">
          <div className="local-card">
            <span className="online-dot" />{" "}
            {cloud ? "Kết nối Codex · cloud" : "Chạy hoàn toàn local"}
            <p>
              {cloud || localModel ? model : "Provider giả cho buổi demo."}
              <br />
              {cloud
                ? "Chỉ gửi hội thoại sau khi bạn duyệt."
                : "Không gửi dữ liệu lên cloud."}
            </p>
          </div>
          <button
            className="quiet side-audit"
            onClick={() => setShowAudit(!showAudit)}
          >
            {showAudit ? "Ẩn" : "Xem"} nhật ký phiên <span>↗</span>
          </button>
          <div className="sidebar-footer">
            Censitive <span>Local privacy gateway</span>
          </div>
        </div>
      </aside>

      <main className="workspace" id="main-content">
        <header className="topbar">
          <div>
            <span className="breadcrumb">Bản gốc ở lại máy bạn</span>
            <h1>Hội thoại riêng tư</h1>
          </div>
          <button
            className="demo-badge provider-trigger"
            ref={providerButton}
            onClick={() => setShowProviders(true)}
            disabled={busy || !identityConfirmed}
            aria-label="Provider & kết nối"
          >
            <span />{" "}
            {providerConfirmed
              ? cloud
                ? "Codex · ChatGPT"
                : localModel
                  ? "Qwen local"
                  : "Demo local"
              : "Xác nhận provider"}
            <span className="provider-chevron">⌄</span>
          </button>
        </header>
        <div className="demo-strip">
          <Shield small />
          <span>
            <strong>Bạn kiểm soát bản gửi.</strong> Xem nội dung đã che và duyệt
            trước mỗi lượt.
          </span>
          <span className="strip-right">
            01 Chuẩn bị <i>→</i> 02 Duyệt <i>→</i> 03 Nhận
          </span>
        </div>
        <div className="work-area">
          <section className="conversation" aria-label="Hội thoại">
            <div className="chat-scroll">
              {messages.length === 0 && provisional === null ? (
                <div className="welcome">
                  <div className="welcome-icon">
                    <Shield />
                  </div>
                  <h2>
                    Có những điều
                    <br />
                    nên ở lại đây.
                  </h2>
                  <p>
                    {semantic
                      ? "Qwen đọc ngữ cảnh ngay trên máy để đề xuất che thông tin nhạy cảm. Bạn xem lại bản gửi trước khi provider trả lời."
                      : "Nhập yêu cầu, chọn thông tin cần giữ riêng và kiểm tra bản chuẩn bị gửi."}
                  </p>
                  {semantic && (
                    <button
                      className="semantic-example"
                      disabled={busy}
                      onClick={() => {
                        invalidate();
                        setDraft(
                          "Thông tin nội bộ, chưa công bố: dự án Aurora sẽ cắt giảm 30 nhân sự vào tháng 12. Soạn lời nhắn mời nhóm quản lý họp để trao đổi kế hoạch này.",
                        );
                        setTerms("");
                        input.current?.focus();
                      }}
                    >
                      <strong>Thử một kế hoạch chưa công bố</strong>
                      <span>
                        Để model tự phát hiện, không nhập cụm thủ công
                      </span>
                    </button>
                  )}
                  <div className="sample-grid">
                    {samples.map((sample) => (
                      <button
                        key={sample.title}
                        onClick={() => useSample(sample)}
                        disabled={busy}
                        className="sample"
                      >
                        <span className="sample-icon">{sample.icon}</span>
                        <strong>{sample.title}</strong>
                        <small>{sample.subtitle}</small>
                        <span className="sample-arrow">↗</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="message-list">
                  {messages.map((message, index) => (
                    <article key={index} className={`message ${message.role}`}>
                      <div className="avatar">
                        {message.role === "user" ? "B" : <Shield small />}
                      </div>
                      <div className="message-body">
                        <div className="message-label">
                          {message.role === "user" ? "Bạn" : "Censitive"}
                          {message.role === "assistant" && (
                            <span>
                              {message.provider === "codex-chatgpt"
                                ? "Codex"
                                : message.provider === "local-qwen"
                                  ? "Qwen local"
                                  : "Phản hồi mẫu"}{" "}
                              · đã phục hồi local
                            </span>
                          )}
                        </div>
                        <div className="message-text">{message.content}</div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
              {phase === "sending" && provisional !== null && (
                <div className="message-list">
                  <article
                    className="message assistant stream-provisional"
                    aria-label="Phản hồi tạm thời"
                  >
                    <div className="avatar">
                      <Shield small />
                    </div>
                    <div className="message-body">
                      <div className="message-label">
                        Đang nhận · tạm thời, chưa xác nhận lưu
                      </div>
                      <div className="message-text">{provisional}</div>
                    </div>
                  </article>
                </div>
              )}
              {phase === "sending" && (
                <div className="working" role="status">
                  <span className="spinner" />{" "}
                  {cancelPending
                    ? "Đang hủy lượt…"
                    : cloud
                      ? "Codex đang soạn phản hồi…"
                      : localModel
                        ? "Qwen local đang soạn phản hồi…"
                        : "Provider demo đang soạn phản hồi…"}
                </div>
              )}
              {showAudit && (
                <section className="audit-box">
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
                    <p>
                      Chưa có lượt hoàn tất. Nhật ký không ghi nội dung riêng
                      tư.
                    </p>
                  )}
                </section>
              )}
              <div ref={bottom} />
            </div>
            <div className="composer-area">
              {error && (
                <div className="error-box" role="alert">
                  <span>!</span>
                  <p>{error}</p>
                  <button
                    onClick={() => setError("")}
                    aria-label="Đóng thông báo"
                  >
                    ×
                  </button>
                </div>
              )}
              <div
                className={`composer ${phase === "ready" ? "has-preview" : ""}`}
              >
                <textarea
                  ref={input}
                  name="message"
                  autoComplete="off"
                  aria-label="Nội dung hội thoại"
                  onSelect={(event) =>
                    setDraftSelection(
                      selectedOccurrence(
                        draft,
                        event.currentTarget.selectionStart,
                        event.currentTarget.selectionEnd,
                      ),
                    )
                  }
                  value={draft}
                  maxLength={20000}
                  disabled={busy}
                  placeholder="Nhập nội dung cần soạn, tóm tắt hoặc trao đổi…"
                  onChange={(e) => {
                    invalidate();
                    setDraft(e.target.value);
                  }}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                      e.preventDefault();
                      void prepare();
                    }
                  }}
                />
                <div className="composer-toolbar">
                  <span>
                    <Shield small /> Text-only{" "}
                    <span className="char-count">
                      {draft.length.toLocaleString("vi-VN")} / 20.000
                    </span>
                  </span>
                  {phase === "preparing" ? (
                    <button
                      className="cancel-btn"
                      disabled={cancelPending}
                      onClick={() => void cancelPrepare()}
                    >
                      Hủy phân tích
                    </button>
                  ) : phase === "sending" ? (
                    <button
                      className="cancel-btn"
                      disabled={cancelPending}
                      onClick={() => void cancel()}
                    >
                      Hủy lượt
                    </button>
                  ) : (
                    <button
                      className="primary"
                      disabled={
                        !session ||
                        !draft.trim() ||
                        busy ||
                        !providerConfirmed ||
                        !factsConfirmed ||
                        !identityConfirmed
                      }
                      onClick={() => void prepare()}
                    >
                      Chuẩn bị bản gửi <span>↑</span>
                    </button>
                  )}
                </div>
              </div>
              <p className="composer-caption">
                Ctrl + Enter để chuẩn bị <span>·</span>{" "}
                {cloud
                  ? "Hủy sẽ dừng chờ; dữ liệu đã gửi không thể thu hồi."
                  : localModel
                    ? "Qwen trả lời trên máy từ bản bạn đã duyệt."
                    : "Demo dùng phản hồi mẫu, chưa kết nối mô hình AI."}
              </p>
            </div>
          </section>

          <aside className="privacy-panel" aria-label="Kiểm tra riêng tư">
            <div className="panel-title">
              <div className="panel-symbol">
                <Shield small />
              </div>
              <div>
                <h2>Kiểm tra riêng tư</h2>
                <p>Kiểm tra bản gửi tới {cloud ? "Codex" : "provider"}</p>
              </div>
              <span className={`status-pill ${preview ? "ready" : ""}`}>
                {preview ? "Sẵn sàng" : "Chờ nội dung"}
              </span>
            </div>
            <div className="detector-status" role="status">
              <span
                className={phase === "preparing" ? "spinner" : "detector-dot"}
              />
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
              <textarea
                id="protected-terms"
                name="protected-terms"
                autoComplete="off"
                value={terms}
                maxLength={6030}
                disabled={busy}
                onChange={(e) => {
                  invalidate();
                  setTerms(e.target.value);
                }}
                placeholder={"Mỗi dòng một cụm, ví dụ:\nLan\nMaple"}
              />
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
                    ? `${preview.detections.length} vùng đã che`
                    : "CHƯA GỬI"}
                </span>
              </div>
              {preview ? (
                <>
                  <div className="preview-tabs">
                    <button
                      className={inspect === "preview" ? "selected" : ""}
                      onClick={() => setInspect("preview")}
                    >
                      Nội dung
                    </button>
                    {preview.semantic && (
                      <button
                        className={inspect === "evidence" ? "selected" : ""}
                        onClick={() => setInspect("evidence")}
                      >
                        Phát hiện ({preview.semantic.findings.length})
                      </button>
                    )}
                    <button
                      className={inspect === "json" ? "selected" : ""}
                      onClick={() => setInspect("json")}
                    >
                      Body JSON
                    </button>
                  </div>
                  {preview.semantic &&
                    /^\s*\[\[PG_v1_[0-9a-f]{32}\]\]\s*$/.test(
                      preview.sanitized,
                    ) && (
                      <p className="overmask-note" role="status">
                        Toàn bộ nội dung đang bị che. Hãy tách yêu cầu cần Codex
                        thực hiện thành một câu riêng và chuẩn bị lại để giữ
                        được ý nghĩa của yêu cầu.
                      </p>
                    )}
                  {inspect !== "evidence" && (
                    <pre className="preview-content" aria-label="Bản đã che">
                      {inspect === "preview"
                        ? preview.sanitized
                        : JSON.stringify(JSON.parse(preview.body), null, 2)}
                    </pre>
                  )}
                  {inspect !== "evidence" && (
                    <p className="preview-detail">
                      {preview.history_count
                        ? `Có ${preview.history_count} message lịch sử đã lọc trong body. `
                        : ""}
                      Bản duyệt có hiệu lực tối đa 5 phút. Sửa nội dung sẽ cần
                      chuẩn bị lại.
                      {cloud &&
                        " JSON này là hội thoại gửi vào Codex; CLI bổ sung chỉ dẫn cố định và metadata giao thức."}
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
                          Không có đề xuất ngữ nghĩa. Bạn vẫn cần kiểm tra nội
                          dung trước khi gửi.
                        </p>
                      )}
                    </details>
                  )}
                  {preview.detections.length > 0 && (
                    <div className="detections">
                      <h4>Giá trị giữ tại máy</h4>
                      {preview.detections.map((d, i) => (
                        <div className="detection" key={i}>
                          <span>
                            {d.category === "email"
                              ? "@"
                              : d.category === "manual"
                                ? "◇"
                                : "⌘"}
                          </span>
                          <code>{d.value}</code>
                          <small>
                            {d.restore_allowed ? "Che" : "Không phục hồi"}
                          </small>
                        </div>
                      ))}
                    </div>
                  )}
                  {preview.review && preview.review.items.length > 0 && (
                    <details className="preview-decisions" open>
                      <summary>Điều chỉnh đề xuất mới</summary>
                      <p>
                        Chỉ bỏ che đề xuất tự động chưa được lưu, đúng lần xuất
                        hiện này. Quy tắc, thông tin truy cập và bảo vệ đã xác
                        nhận không thay đổi. Lựa chọn chỉ áp dụng bản gửi này;
                        lượt sau model có thể nhận diện lại.
                      </p>
                      {preview.review.items.map((item) => (
                        <div
                          key={item.candidate_id}
                          className="preview-decision"
                        >
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
                            disabled={busy || !item.can_reject}
                            onClick={() =>
                              void reviewPreview(item.candidate_id)
                            }
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
                    Nhấn “Chuẩn bị bản gửi” để kiểm tra những gì được che trước
                    khi duyệt.
                  </p>
                </div>
              )}
            </div>
            {preview && (
              <div className="approval-footer">
                <button
                  className="approve-btn"
                  onClick={() => void send()}
                  disabled={
                    phase !== "ready" || !factsConfirmed || !identityConfirmed
                  }
                >
                  <Shield small /> Duyệt & gửi bản này <span>→</span>
                </button>
                <div className="destination">
                  Đích nhận:{" "}
                  <strong>
                    {cloud
                      ? `Codex / OpenAI · ${model}`
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
                  Chưa xác nhận thay đổi bản gửi. Các thao tác khác đang tạm
                  khóa.
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
            <FactPanel
              key={session}
              state={factState}
              busy={busy || !identityConfirmed || !providerConfirmed}
              uncertain={!factsConfirmed}
              onCorrect={correctFact}
            />
            <IdentityPanel
              key={`identity:${session}`}
              state={factState}
              busy={busy || !factsConfirmed || !providerConfirmed}
              uncertain={!identityConfirmed}
              draftSelection={draftSelection}
              actions={draftActions}
              factMarks={draftFacts}
              onCommand={changeIdentity}
              onStage={stageIdentity}
              onStageFact={stageFact}
              onClearDraft={() => invalidate()}
            />
            <div className="scope-note">
              <span>i</span>
              <p>
                <strong>Bạn vẫn quyết định bản gửi</strong>
                {semantic
                  ? "Model có thể bỏ sót hoặc che quá nhiều. Kiểm tra ngữ cảnh còn lại và bổ sung cụm bảo vệ khi cần."
                  : "Bộ lọc quy tắc chưa nhận diện ngữ nghĩa. Kiểm tra nội dung và bổ sung các cụm cần giữ riêng."}
              </p>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
