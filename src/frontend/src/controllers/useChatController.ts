import { useEffect, useRef, useState } from "react";
import type {
  ChatMessage,
  ChatSendResult,
  Preview,
  PreviewDecision,
  PreviewDecisionResult,
  SessionView,
  Phase,
  Bootstrap,
  SessionIdentity,
  SessionList,
  SessionSummary,
  SessionMutation,
  FactState,
  Correction,
  DraftAction,
  DraftFact,
  DraftOccurrence,
  ProtectionCommand,
  FactResult,
  IdentityResult,
  PrivateWorkPhase,
  PrivateWorkPreview,
  PrivateWorkRecord,
  InterruptedPrivateWork,
  LedgerRevision,
  PrivateLedgerCandidate,
  PrivateLedgerProposalResult,
} from "../data/contracts";
import {
  errors,
  reviewErrors,
  correctionErrors,
  identityErrors,
} from "../data/errors";
import type { Sample } from "../data/samples";
import { termIssue } from "../data/protectedTerms";
import { previewLegend } from "../data/previewLegend";
import { selectedOccurrence } from "../data/occurrences";
import { api, setCsrf, sendStream } from "../network/api";
import { readStream } from "../stream";
import {
  emptyLedgerDraft,
  structuredLedgerValues,
  type StructuredLedgerDraft,
  type StructuredLedgerValues,
} from "../data/structuredLedger";

const ACTIVE_SESSION_HINT_KEY = "censitive.active_session";

function validPrivatePreview(value: PrivateWorkPreview): boolean {
  return (
    (value.stage === undefined || value.stage === "planner") &&
    typeof value.prepared_id === "string" &&
    typeof value.public_body === "string" &&
    typeof value.public_body_sha256 === "string" &&
    typeof value.provider === "string" &&
    typeof value.model === "string"
  );
}

function readActiveSessionHint(): string {
  try {
    const value = window.localStorage.getItem(ACTIVE_SESSION_HINT_KEY) ?? "";
    return /^[a-f0-9]{32}$/.test(value) ? value : "";
  } catch {
    return "";
  }
}

function writeActiveSessionHint(id: string) {
  if (!/^[a-f0-9]{32}$/.test(id)) return;
  try {
    window.localStorage.setItem(ACTIVE_SESSION_HINT_KEY, id);
  } catch {
  }
}

function clearActiveSessionHint() {
  try {
    window.localStorage.removeItem(ACTIVE_SESSION_HINT_KEY);
  } catch {
  }
}

export function useChatController() {
  const [provider, setProvider] = useState("local-demo");
  const [model, setModel] = useState("");
  const [showProviders, setShowProviders] = useState(false);
  const [detector, setDetector] = useState("rules");
  const semantic = detector !== "rules";
  const preparingId = useRef("");
  const [providerConfirmed, setProviderConfirmed] = useState(false);
  const cloud = provider === "codex-chatgpt" || provider === "openai-api";
  const codex = provider === "codex-chatgpt";
  const localModel = provider === "local-qwen";
  const [session, setSession] = useState("");
  const [sessionTitle, setSessionTitle] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [persistenceEnabled, setPersistenceEnabled] = useState(false);
  const [sessionMutation, setSessionMutation] =
    useState<SessionMutation | null>(null);
  const [recoveryStatus, setRecoveryStatus] = useState<string | null>(null);
  const [interruptedTurn, setInterruptedTurn] = useState<{
    text: string;
    status: string;
  } | null>(null);
  const [factState, setFactState] = useState<FactState | null>(null);
  const [factsConfirmed, setFactsConfirmed] = useState(true);
  const [identityConfirmed, setIdentityConfirmed] = useState(true);
  const identityOperation = useRef<string | null>(null);
  const [draftActions, setDraftActions] = useState<DraftAction[]>([]);
  const [draftFacts, setDraftFacts] = useState<DraftFact[]>([]);
  const [draftSelection, setDraftSelection] = useState<DraftOccurrence | null>(
    null,
  );
  const [selectionNotice, setSelectionNotice] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [provisional, setProvisional] = useState<string | null>(null);
  const activeStream = useRef<AbortController | null>(null);
  const streamCancelRequested = useRef(false);
  const streamCancellation = useRef<Promise<unknown> | null>(null);
  const [draft, setDraft] = useState("");
  const [terms, setTerms] = useState("");
  const [appliedTerms, setAppliedTerms] = useState<string[]>([]);
  const [termInput, setTermInput] = useState("");
  const [termStatus, setTermStatus] = useState("");
  const [selectionUndo, setSelectionUndo] = useState<
    { term: string } | { mark: DraftFact } | null
  >(null);
  const [preparedText, setPreparedText] = useState("");
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
  const [privateWorkAvailable, setPrivateWorkAvailable] = useState(false);
  const [privateWorkHistory, setPrivateWorkHistory] = useState<
    PrivateWorkRecord[]
  >([]);
  const [privatePublicTask, setPrivatePublicTask] = useState("");
  const [privateContext, setPrivateContext] = useState("");
  const [privatePhase, setPrivatePhase] = useState<PrivateWorkPhase>("idle");
  const [privatePreview, setPrivatePreview] =
    useState<PrivateWorkPreview | null>(null);
  const [privateError, setPrivateError] = useState("");
  const [privateCancelPending, setPrivateCancelPending] = useState(false);
  const [interruptedPrivateWork, setInterruptedPrivateWork] =
    useState<InterruptedPrivateWork | null>(null);
  const [privateLedgerAvailable, setPrivateLedgerAvailable] = useState(false);
  const [privateLedgerModelAvailable, setPrivateLedgerModelAvailable] =
    useState(false);
  const [structuredLedgerDraft, setStructuredLedgerDraft] =
    useState<StructuredLedgerDraft>(emptyLedgerDraft);
  const [privateLedgerHistory, setPrivateLedgerHistory] = useState<
    LedgerRevision[]
  >([]);
  const [privateLedgerSource, setPrivateLedgerSource] = useState("");
  const [privateLedgerPhase, setPrivateLedgerPhase] = useState<
    "idle" | "proposing" | "candidate" | "confirming"
  >("idle");
  const [privateLedgerCandidate, setPrivateLedgerCandidate] =
    useState<PrivateLedgerCandidate | null>(null);
  const [privateLedgerUse, setPrivateLedgerUseState] = useState(false);
  const [privateLedgerError, setPrivateLedgerError] = useState("");
  const [privateLedgerNotice, setPrivateLedgerNotice] = useState("");
  const [privateLedgerCancelPending, setPrivateLedgerCancelPending] =
    useState(false);
  const [privateLedgerRerunPending, setPrivateLedgerRerunPending] =
    useState(false);
  const privateLedgerRequestId = useRef("");
  const generation = useRef(0);
  const discardTasks = useRef(new Map<string, Promise<void>>());
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const providerButton = useRef<HTMLButtonElement>(null);
  const chatBusy =
    cancelPending ||
    phase === "preparing" ||
    phase === "sending" ||
    phase === "loading" ||
    pendingReview !== null ||
    privatePhase === "preparing" ||
    privatePhase === "sending" ||
    privateCancelPending ||
    privateLedgerPhase === "proposing" ||
    privateLedgerPhase === "confirming" ||
    privateLedgerCandidate !== null ||
    privateLedgerCancelPending ||
    privateLedgerRerunPending;
  const busy = chatBusy || sessionMutation !== null;

  function explain(reason: unknown) {
    const code = reason instanceof Error ? reason.message : "REQUEST_FAILED";
    setError(
      errors[code] ??
        "Không thể kết nối ứng dụng local. Kiểm tra backend rồi thử lại.",
    );
  }
  function applySession(state: SessionView, restoreDraftState = false) {
    writeActiveSessionHint(state.id);
    setSessionTitle(state.title ?? "");
    setProvider(state.provider);
    setModel(state.model);
    setProviderConfirmed(
      typeof state.provider_confirmed === "boolean"
        ? state.provider_confirmed
        : true,
    );
    setRecoveryStatus(state.recovery_status ?? null);
    setInterruptedTurn(state.interrupted_turn ?? null);
    setInterruptedPrivateWork(state.interrupted_private_work ?? null);
    setAppliedTerms(Array.isArray(state.terms) ? state.terms : []);
    if (restoreDraftState && Array.isArray(state.terms))
      setTerms(state.terms.join("\n"));
    setMessages(state.messages);
    setAudit(state.audit);
    setFactState(state.state ?? null);
    setPrivateWorkAvailable(state.private_work_available === true);
    setPrivateWorkHistory(
      Array.isArray(state.private_work_history)
        ? state.private_work_history
        : [],
    );
    setPrivateLedgerAvailable(state.private_ledger_available === true);
    setPrivateLedgerModelAvailable(
      (state.private_ledger_model_available ??
        state.private_ledger_available) === true,
    );
    setStructuredLedgerDraft(emptyLedgerDraft);
    setPrivateLedgerHistory(
      Array.isArray(state.private_ledger_history)
        ? state.private_ledger_history
        : [],
    );
    setPrivateLedgerSource("");
    setPrivateLedgerCandidate(null);
    setPrivateLedgerPhase("idle");
    setPrivateLedgerUseState(false);
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
    setPrivateLedgerCancelPending(false);
    setPrivateLedgerRerunPending(false);
    privateLedgerRequestId.current = "";
    setPrivatePhase("idle");
    setPrivatePreview(null);
    setPrivateError("");
    setPrivateCancelPending(false);
  }
  async function refresh(
    id: string,
    restoreDraftState = false,
    expectedGeneration?: number,
  ) {
    const state = await api<SessionView>(`/sessions/${id}`);
    if (
      expectedGeneration !== undefined &&
      (expectedGeneration !== generation.current || id !== session)
    )
      return null;
    applySession(state, restoreDraftState);
    setSessions((current) =>
      current.map((item) =>
        item.id === id
          ? {
              ...item,
              ...(state.title !== undefined ? { title: state.title } : {}),
              provider: state.provider,
              model: state.model,
              provider_confirmed: state.provider_confirmed,
              recovery_status: state.recovery_status,
              message_count: state.messages.length,
              private_work_count: state.private_work_history?.length ?? 0,
            }
          : item,
      ),
    );
    return state;
  }
  async function refreshSessions() {
    const result = await api<SessionList>("/sessions");
    const next = Array.isArray(result.sessions) ? result.sessions : [];
    setSessions(next);
    return next;
  }
  function queueDiscard(id: string, preparedId: string) {
    const task = api(`/sessions/${id}/discard`, "POST", {
      prepared_id: preparedId,
    }).then(() => undefined);
    discardTasks.current.set(id, task);
    const clear = () => {
      if (discardTasks.current.get(id) === task)
        discardTasks.current.delete(id);
    };
    task.then(clear, clear);
    return task;
  }
  async function waitForSessionDiscard(id: string) {
    const inFlight = discardTasks.current.get(id);
    if (inFlight) {
      await inFlight;
      return;
    }
    if (id !== session) return;
    const preparedIds = [
      preview?.id,
      pendingReview?.prepared_id,
      privatePreview?.prepared_id,
    ].filter((value): value is string => Boolean(value));
    const uniqueIds = [...new Set(preparedIds)];
    if (uniqueIds.length === 0) return;
    setPreview(null);
    setPendingReview(null);
    setPrivatePreview(null);
    await Promise.all(
      uniqueIds.map((preparedId) => queueDiscard(id, preparedId)),
    );
  }
  function latestSession(items: SessionSummary[]) {
    return items[0] ?? null;
  }
  function clearConversationDraft() {
    setDraft("");
    setTerms("");
    setAppliedTerms([]);
    setTermInput("");
    setTermStatus("");
    setPreview(null);
    setPendingReview(null);
    setProvisional(null);
    clearDraftIdentity();
    identityOperation.current = null;
    setFactsConfirmed(true);
    setIdentityConfirmed(true);
    setPrivatePublicTask("");
    setPrivateContext("");
    setPrivatePhase("idle");
    setPrivatePreview(null);
    setPrivateError("");
    setPrivateCancelPending(false);
    setPrivateLedgerSource("");
    setPrivateLedgerCandidate(null);
    setPrivateLedgerPhase("idle");
    setPrivateLedgerUseState(false);
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
    setPrivateLedgerCancelPending(false);
    setPrivateLedgerRerunPending(false);
    setPrivateLedgerHistory([]);
    setPrivateLedgerAvailable(false);
    privateLedgerRequestId.current = "";
    setInterruptedPrivateWork(null);
  }
  async function initialize() {
    try {
      const boot = await api<Bootstrap>("/bootstrap");
      setCsrf(boot.csrf);
      setDetector(boot.detector);
      setProvider(boot.provider);
      setModel(boot.model);
      setPersistenceEnabled(boot.persistence_enabled === true);
      const listed = await refreshSessions();
      const hinted = readActiveSessionHint();
      const existing =
        listed.find((item) => item.id === hinted) ?? latestSession(listed);
      if (existing) {
        setSession(existing.id);
        await refresh(existing.id, true);
      } else {
        if (hinted) clearActiveSessionHint();
        const created = await api<SessionIdentity>("/sessions", "POST");
        setSession(created.id);
        await refresh(created.id, true);
        await refreshSessions();
      }
      setFactsConfirmed(true);
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
    setDraftActions([]);
    setDraftFacts([]);
    setDraftSelection(null);
    setSelectionNotice("");
    setSelectionUndo(null);
  }, [factState?.epoch, session, provider]);

  function clearDraftIdentity() {
    setDraftActions([]);
    setDraftFacts([]);
    setDraftSelection(null);
    setSelectionNotice("");
    setSelectionUndo(null);
  }
  function invalidate(clearIdentity = true) {
    if (clearIdentity) clearDraftIdentity();
    generation.current++;
    if (preview && session)
      void queueDiscard(session, preview.id).catch(explain);
    setPreview(null);
    setError("");
    if (phase !== "sending" && phase !== "loading") setPhase("idle");
  }
  async function selectProvider(nextProvider: string, nextModel: string) {
    if (busy || !identityConfirmed) throw new Error("SESSION_BUSY");
    clearDraftIdentity();
    if (preview && session)
      void queueDiscard(session, preview.id).catch(explain);
    setPendingReview(null);
    invalidatePrivatePreview();
    setPrivatePublicTask("");
    setPrivateContext("");
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
  async function prepare(unmaskOnce: { start: number; end: number }[] = []) {
    if (
      !draft.trim() ||
      !session ||
      (phase !== "idle" && !(phase === "ready" && unmaskOnce.length)) ||
      busy ||
      !providerConfirmed ||
      !factsConfirmed ||
      !identityConfirmed ||
      recoveryStatus === "unknown_send" ||
      recoveryStatus === "storage_failed"
    )
      return;
    if (privatePhase === "ready") invalidatePrivatePreview();
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
          ...(unmaskOnce.length ? { unmask_once: unmaskOnce } : {}),
        },
      );
      if (attempt !== generation.current) return;
      if (result.stage !== undefined && result.stage !== "planner")
        throw new Error("PREVIEW_OUTCOME_UNKNOWN");
      setPreparedText(text);
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
  async function revealEmail(start: number, end: number) {
    if (!preview || phase !== "ready" || busy) return;
    const detection = preview.detections.find(
      (item) => item.start === start && item.end === end,
    );
    if (
      !detection ||
      detection.category !== "email" ||
      !detection.restore_allowed ||
      detection.unmasked_once ||
      !window.confirm(
        `Gửi lộ email “${detection.value}” cho đúng một lần gửi? Bản xem trước sẽ được chuẩn bị lại; bạn vẫn phải duyệt trước khi gửi. Lượt sau email được che lại.`,
      )
    )
      return;
    await prepare([
      ...preview.detections
        .filter((item) => item.unmasked_once)
        .map((item) => ({ start: item.start, end: item.end })),
      { start, end },
    ]);
  }
  function explainPrivate(reason: unknown) {
    const code = reason instanceof Error ? reason.message : "REQUEST_FAILED";
    setPrivateError(
      errors[code] ??
        "Chưa xác nhận được kết quả private work. Hãy kiểm tra lịch sử hoặc tạo phiên mới; không tự gửi lại.",
    );
  }
  function discardPrivatePreview(sessionId: string, preparedId: string) {
    return queueDiscard(sessionId, preparedId);
  }
  function invalidatePrivatePreview() {
    const oldPreview = privatePreview;
    generation.current++;
    if (oldPreview && session)
      void discardPrivatePreview(session, oldPreview.prepared_id).catch(
        explainPrivate,
      );
    setPrivatePreview(null);
    setPrivatePhase("idle");
    setPrivateError("");
    setPrivateCancelPending(false);
  }
  function latestPrivateLedgerRevision(): LedgerRevision | null {
    return privateLedgerHistory[privateLedgerHistory.length - 1] ?? null;
  }
  function explainPrivateLedger(reason: unknown) {
    const code = reason instanceof Error ? reason.message : "REQUEST_FAILED";
    setPrivateLedgerError(
      errors[code] ??
        "Chưa xác nhận được sổ dữ liệu local. Hãy kiểm tra trạng thái phiên rồi thử lại.",
    );
  }
  function isLedgerRevision(value: unknown): value is LedgerRevision {
    if (!value || typeof value !== "object") return false;
    const revision = value as Partial<LedgerRevision>;
    return (
      typeof revision.revision === "number" &&
      Number.isInteger(revision.revision) &&
      revision.revision > 0 &&
      typeof revision.source_sha256 === "string" &&
      Array.isArray(revision.events) &&
      Array.isArray(revision.records)
    );
  }
  async function cancelPrivateLedgerProposal(
    sessionId: string,
    requestId: string,
  ) {
    if (!requestId) return;
    await api(`/sessions/${sessionId}/cancel`, "POST", {
      prepared_id: requestId,
    });
  }
  async function proposePrivateLedger(values?: StructuredLedgerValues) {
    if (
      !privateLedgerAvailable ||
      (!values &&
        (!privateLedgerModelAvailable || !privateLedgerSource.trim())) ||
      !session ||
      busy ||
      phase !== "idle" ||
      privatePhase !== "idle" ||
      pendingReview !== null ||
      sessionMutation !== null ||
      recoveryStatus === "unknown_send" ||
      recoveryStatus === "storage_failed" ||
      privateLedgerPhase !== "idle" ||
      privateLedgerCandidate !== null
    )
      return;
    if (preview) invalidate(false);
    const attempt = ++generation.current;
    const sessionId = session;
    const source = privateLedgerSource;
    const baseRevision = latestPrivateLedgerRevision()?.revision ?? 0;
    const requestId = crypto.randomUUID().replaceAll("-", "");
    privateLedgerRequestId.current = requestId;
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
    setPrivateLedgerPhase("proposing");
    try {
      const result = await api<PrivateLedgerProposalResult>(
        `/sessions/${sessionId}/private-ledger/propose${values ? "-structured" : ""}`,
        "POST",
        {
          ...(values ? { values } : { private_source: source }),
          base_revision: baseRevision,
          request_id: requestId,
        },
      );
      if (attempt !== generation.current || sessionId !== session) {
        if (privateLedgerRequestId.current === requestId)
          await cancelPrivateLedgerProposal(sessionId, requestId).catch(
            () => {},
          );
        return;
      }
      if (result && "status" in result) {
        if (
          result.status !== "no_changes" ||
          result.base_revision !== baseRevision ||
          Object.keys(result).length !== 2
        )
          throw new Error("PRIVATE_LEDGER_OUTCOME_UNKNOWN");
        setPrivateLedgerNotice(
          values
            ? "Giá trị nhập tay không thay đổi dữ liệu đã xác nhận."
            : "Model không đề xuất thay đổi. Dữ liệu đã xác nhận chưa được sửa; hãy kiểm tra lại nếu bạn mong đợi thay đổi.",
        );
        setPrivateLedgerPhase("idle");
        privateLedgerRequestId.current = "";
        return;
      }
      if (
        !result ||
        typeof result.proposal_id !== "string" ||
        result.proposal_id.length === 0 ||
        !isLedgerRevision(result.candidate)
      )
        throw new Error("PRIVATE_LEDGER_OUTCOME_UNKNOWN");
      setPrivateLedgerCandidate(result);
      setPrivateLedgerPhase("candidate");
    } catch (reason) {
      if (attempt === generation.current && sessionId === session) {
        explainPrivateLedger(reason);
        setPrivateLedgerPhase("idle");
        privateLedgerRequestId.current = "";
      }
    }
  }
  async function proposeStructuredLedger() {
    if (busy) return;
    try {
      await proposePrivateLedger(structuredLedgerValues(structuredLedgerDraft));
    } catch (reason) {
      explainPrivateLedger(reason);
    }
  }
  function changeStructuredLedger(
    field: keyof StructuredLedgerDraft,
    value: string,
  ) {
    if (busy || privateLedgerPhase !== "idle") return;
    if (preview) invalidate(false);
    if (privatePreview) invalidatePrivatePreview();
    setStructuredLedgerDraft((previous) => ({ ...previous, [field]: value }));
    setPrivateLedgerUseState(false);
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
  }
  async function confirmPrivateLedger() {
    const pending = privateLedgerCandidate;
    if (
      !pending ||
      !session ||
      privateLedgerPhase !== "candidate" ||
      phase !== "idle" ||
      privatePhase !== "idle" ||
      pendingReview !== null ||
      sessionMutation !== null ||
      recoveryStatus === "unknown_send" ||
      recoveryStatus === "storage_failed"
    )
      return;
    const attempt = ++generation.current;
    const sessionId = session;
    setPrivateLedgerPhase("confirming");
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
    let acknowledged = false;
    let refreshed: SessionView | null = null;
    try {
      const result = await api<{ ok: boolean }>(
        `/sessions/${sessionId}/private-ledger/confirm`,
        "POST",
        { proposal_id: pending.proposal_id },
      );
      if (result.ok !== true) throw new Error("PRIVATE_LEDGER_OUTCOME_UNKNOWN");
      acknowledged = true;
    } catch (reason) {
      explainPrivateLedger(reason);
    }
    try {
      refreshed = await refresh(sessionId, false, attempt);
    } catch {
      refreshed = null;
    }
    if (attempt !== generation.current || sessionId !== session) return;
    const observed = refreshed?.private_ledger_history?.some(
      (item) => item.revision === pending.candidate.revision,
    );
    setPrivateLedgerCandidate(null);
    setPrivateLedgerSource("");
    setPrivateLedgerPhase("idle");
    privateLedgerRequestId.current = "";
    if (acknowledged && observed) {
      setPrivateLedgerUseState(true);
      setPrivateLedgerError(
        "Đã lưu bản ghi local. Bạn có thể dùng bản ghi này cho việc riêng.",
      );
    } else {
      setPrivateLedgerUseState(false);
      setPrivateLedgerError(
        "Chưa thể xác nhận chắc chắn thay đổi local. Đã tải lại trạng thái phiên; hãy kiểm tra sổ trước khi dùng.",
      );
    }
  }
  async function discardPrivateLedger() {
    const requestId =
      privateLedgerCandidate?.proposal_id ?? privateLedgerRequestId.current;
    const sourceDraft = privateLedgerSource;
    const formDraft = structuredLedgerDraft;
    if (
      !requestId &&
      !privateLedgerCandidate &&
      privateLedgerPhase !== "proposing"
    )
      return;
    const sessionId = session;
    const attempt = ++generation.current;
    setPrivateLedgerCancelPending(true);
    setPrivateLedgerCandidate(null);
    setPrivateLedgerPhase("confirming");
    setPrivateLedgerUseState(false);
    privateLedgerRequestId.current = "";
    if (!sessionId || !requestId) {
      setPrivateLedgerPhase("idle");
      setPrivateLedgerCancelPending(false);
      return;
    }
    try {
      await cancelPrivateLedgerProposal(sessionId, requestId);
      if (attempt !== generation.current || sessionId !== session) return;
      await refresh(sessionId, false, attempt);
      if (attempt !== generation.current || sessionId !== session) return;
      setPrivateLedgerSource(sourceDraft);
      setStructuredLedgerDraft(formDraft);
      setPrivateLedgerError(
        "Đã nhận xác nhận hủy đề xuất local; không có bản ghi nào được thay đổi.",
      );
    } catch (reason) {
      if (attempt !== generation.current || sessionId !== session) return;
      await refresh(sessionId, false, attempt).catch(() => null);
      if (attempt === generation.current && sessionId === session) {
        setPrivateLedgerSource(sourceDraft);
        setStructuredLedgerDraft(formDraft);
        explainPrivateLedger(reason);
      }
    } finally {
      if (attempt === generation.current && sessionId === session) {
        setPrivateLedgerPhase("idle");
        setPrivateLedgerCancelPending(false);
      }
    }
  }
  function changePrivateLedgerSource(text: string) {
    if (
      privateLedgerPhase === "proposing" ||
      privateLedgerPhase === "confirming" ||
      privateLedgerCandidate !== null
    )
      return;
    if (privatePreview) invalidatePrivatePreview();
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
    setPrivateLedgerSource(text);
    setPrivateLedgerUseState(false);
  }
  function setPrivateLedgerUse(enabled: boolean) {
    if (
      !enabled ||
      privateLedgerHistory.length === 0 ||
      privateLedgerPhase !== "idle" ||
      busy ||
      phase !== "idle" ||
      privatePhase !== "idle" ||
      pendingReview !== null ||
      sessionMutation !== null ||
      recoveryStatus === "unknown_send" ||
      recoveryStatus === "storage_failed"
    ) {
      if (!enabled) {
        if (privatePreview) invalidatePrivatePreview();
        setPrivateLedgerUseState(false);
      }
      return;
    }
    invalidatePrivatePreview();
    setPrivateLedgerUseState(true);
    setPrivateLedgerError("");
    setPrivateLedgerNotice("");
  }
  async function preparePrivateWork() {
    const ledgerRevision = latestPrivateLedgerRevision();
    const useLedger =
      privateLedgerUse &&
      ledgerRevision !== null &&
      privateLedgerPhase === "idle";
    const ledgerRevisionId = useLedger ? ledgerRevision?.revision : undefined;
    if (
      !privateWorkAvailable ||
      !privatePublicTask.trim() ||
      (!useLedger && !privateContext.trim()) ||
      !session ||
      phase === "loading" ||
      phase === "preparing" ||
      phase === "sending" ||
      sessionMutation !== null ||
      !factsConfirmed ||
      !identityConfirmed ||
      pendingReview !== null ||
      privatePhase !== "idle" ||
      privateCancelPending ||
      !providerConfirmed ||
      privateLedgerCandidate !== null ||
      privateLedgerPhase !== "idle" ||
      recoveryStatus === "unknown_send" ||
      recoveryStatus === "storage_failed"
    )
      return;
    if (preview || pendingReview) invalidate(false);
    const attempt = ++generation.current;
    const sessionId = session;
    const publicTask = privatePublicTask;
    const context = privateContext;
    setPrivateError("");
    setPrivatePreview(null);
    setPrivatePhase("preparing");
    try {
      const result = await api<PrivateWorkPreview>(
        `/sessions/${sessionId}/private-work/prepare`,
        "POST",
        {
          public_task: publicTask,
          private_context: useLedger ? "" : context,
          ...(ledgerRevisionId !== undefined
            ? { ledger_revision: ledgerRevisionId }
            : {}),
        },
      );
      if (!validPrivatePreview(result))
        throw new Error("PRIVATE_WORK_OUTCOME_UNKNOWN");
      if (attempt !== generation.current || sessionId !== session) {
        await discardPrivatePreview(sessionId, result.prepared_id).catch(
          explainPrivate,
        );
        return;
      }
      setPrivatePreview(result);
      setPrivatePhase("ready");
    } catch (reason) {
      if (attempt === generation.current) {
        explainPrivate(reason);
        setPrivatePhase("idle");
      }
    }
  }
  async function sendPrivateWork() {
    const prepared = privatePreview;
    if (
      !prepared ||
      privatePhase !== "ready" ||
      phase !== "idle" ||
      sessionMutation !== null ||
      !session ||
      !privateWorkAvailable ||
      !providerConfirmed ||
      !factsConfirmed ||
      !identityConfirmed ||
      pendingReview !== null
    )
      return;
    const attempt = ++generation.current;
    const sessionId = session;
    setPrivateError("");
    setPrivateCancelPending(false);
    setPrivatePhase("sending");
    try {
      const result = await api<{
        answer?: string;
      }>(`/sessions/${sessionId}/private-work/send`, "POST", {
        prepared_id: prepared.prepared_id,
      });
      if ("jev_preview" in result || typeof result.answer !== "string")
        throw new Error("PRIVATE_WORK_OUTCOME_UNKNOWN");
      if (attempt !== generation.current || sessionId !== session) return;
      await refresh(sessionId, false, attempt);
      if (attempt !== generation.current || sessionId !== session) return;
      setPrivatePublicTask("");
      setPrivateContext("");
    } catch (reason) {
      if (attempt !== generation.current || sessionId !== session) return;
      explainPrivate(reason);
      await refresh(sessionId, false, attempt).catch(() => undefined);
      if (attempt === generation.current && sessionId === session) {
        const code =
          reason instanceof Error ? reason.message : "REQUEST_FAILED";
        setPrivateError(
          errors[code] ??
            "Chưa xác nhận được kết quả private work. Hãy kiểm tra lịch sử hoặc tạo phiên mới; không tự gửi lại.",
        );
      }
    } finally {
      if (attempt === generation.current && sessionId === session) {
        setPrivatePreview(null);
        setPrivatePhase("idle");
        setPrivateCancelPending(false);
      }
    }
  }
  async function rerunPrivateLedger(workId: string) {
    const revision = latestPrivateLedgerRevision();
    if (
      !workId ||
      !revision ||
      !session ||
      phase !== "idle" ||
      privatePhase !== "idle" ||
      privateLedgerPhase !== "idle" ||
      privateLedgerCandidate !== null ||
      sessionMutation !== null ||
      pendingReview !== null ||
      recoveryStatus === "unknown_send" ||
      recoveryStatus === "storage_failed"
    )
      return;
    const attempt = ++generation.current;
    const sessionId = session;
    setPrivateError("");
    setPrivateLedgerRerunPending(true);
    setPrivatePhase("sending");
    try {
      const result = await api<{ answer: string }>(
        `/sessions/${sessionId}/private-ledger/rerun`,
        "POST",
        { work_id: workId, ledger_revision: revision.revision },
      );
      if (typeof result.answer !== "string")
        throw new Error("PRIVATE_WORK_OUTCOME_UNKNOWN");
      if (attempt !== generation.current || sessionId !== session) return;
      await refresh(sessionId, false, attempt);
      if (attempt !== generation.current || sessionId !== session) return;
      setPrivateError("Đã tính lại theo bản ghi local hiện tại.");
    } catch (reason) {
      if (attempt !== generation.current || sessionId !== session) return;
      explainPrivate(reason);
      await refresh(sessionId, false, attempt).catch(() => undefined);
    } finally {
      if (attempt === generation.current && sessionId === session) {
        setPrivatePhase("idle");
        setPrivateLedgerRerunPending(false);
      }
    }
  }
  async function cancelPrivateWork() {
    if (privatePhase === "idle" || !session) return;
    const sessionId = session;
    const preparedId = privatePreview?.prepared_id;
    const attempt = ++generation.current;
    setPrivateCancelPending(true);
    setPrivateError("");
    try {
      if (preparedId)
        await api(`/sessions/${sessionId}/cancel`, "POST", {
          prepared_id: preparedId,
        });
      if (attempt !== generation.current || sessionId !== session) return;
      await refresh(sessionId, false, attempt);
      if (attempt !== generation.current || sessionId !== session) return;
      setPrivateError(
        "Đã nhận xác nhận hủy. Hãy kiểm tra lịch sử; không tự gửi lại nếu kết quả vẫn chưa rõ.",
      );
    } catch (reason) {
      if (attempt !== generation.current || sessionId !== session) return;
      const code = reason instanceof Error ? reason.message : "REQUEST_FAILED";
      const message =
        errors[code] ??
        "Chưa xác nhận được thao tác hủy. Hãy kiểm tra lịch sử; không tự gửi lại.";
      await refresh(sessionId, false, attempt).catch(() => undefined);
      if (attempt === generation.current && sessionId === session)
        setPrivateError(message);
    } finally {
      if (attempt === generation.current && sessionId === session) {
        setPrivatePreview(null);
        setPrivatePhase("idle");
        setPrivateCancelPending(false);
      }
    }
  }
  async function discardPrivateWork() {
    const prepared = privatePreview;
    if (!prepared || !session || privatePhase !== "ready") return;
    const sessionId = session;
    generation.current++;
    setPrivatePreview(null);
    setPrivatePhase("idle");
    setPrivateError("");
    try {
      await discardPrivatePreview(sessionId, prepared.prepared_id);
    } catch (reason) {
      explainPrivate(reason);
    }
  }
  async function waitStreamCancellation() {
    await streamCancellation.current?.catch(() => {});
  }
  async function reviewPreview(candidateId?: string) {
    if (
      (busy && !pendingReview) ||
      phase === "loading" ||
      phase === "preparing" ||
      phase === "sending" ||
      !session ||
      !identityConfirmed ||
      !factsConfirmed ||
      !providerConfirmed ||
      privatePhase !== "idle" ||
      privateCancelPending
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
          (result.preview.stage !== undefined &&
            result.preview.stage !== "planner") ||
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
      busy ||
      !preview ||
      phase !== "ready" ||
      !factsConfirmed ||
      !identityConfirmed ||
      pendingReview ||
      privatePhase !== "idle" ||
      privateCancelPending
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
        const response = await sendStream(
          session,
          preview.id,
          controller.signal,
        );
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
        const result = await api<ChatSendResult>(
          `/sessions/${session}/send`,
          "POST",
          {
            prepared_id: preview.id,
          },
        );
        if ("jev_preview" in result || typeof result.content !== "string")
          throw new Error("PREVIEW_OUTCOME_UNKNOWN");
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
      setCancelPending(false);
      setPreview(null);
      setPhase("idle");
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
    const attempt = ++generation.current;
    setSessionMutation({ kind: "new", status: "busy" });
    setPhase("loading");
    setError("");
    try {
      if (session) await waitForSessionDiscard(session);
      if (attempt !== generation.current) return;
      clearConversationDraft();
      const created = await api<SessionIdentity>("/sessions", "POST", {
        ...(providerConfirmed ? { provider, model } : {}),
      });
      if (attempt !== generation.current) return;
      setSession(created.id);
      await refresh(created.id, true);
      await refreshSessions();
      setShowAudit(false);
      setPhase("idle");
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "new", status: "unknown" });
      setPhase("idle");
    }
  }

  async function openSession(id: string) {
    if (!id || id === session || busy || !identityConfirmed) return;
    const attempt = ++generation.current;
    setSessionMutation({ kind: "switch", session_id: id, status: "busy" });
    setPhase("loading");
    setError("");
    try {
      if (session) await waitForSessionDiscard(session);
      const state = await api<SessionView>(`/sessions/${id}`);
      if (attempt !== generation.current) return;
      clearConversationDraft();
      setSession(id);
      applySession(state, true);
      setShowAudit(false);
      setPhase("idle");
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "switch", session_id: id, status: "unknown" });
      setPhase("idle");
    }
  }

  async function renameSession(id: string, title: string) {
    const nextTitle = title.trim();
    if (!id || !nextTitle || busy || !identityConfirmed) return;
    setSessionMutation({ kind: "rename", session_id: id, status: "busy" });
    setError("");
    try {
      await api(`/sessions/${id}/title`, "POST", { title: nextTitle });
      await refreshSessions();
      if (id === session) setSessionTitle(nextTitle);
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "rename", session_id: id, status: "unknown" });
    }
  }

  async function deleteSession(id: string) {
    if (!id || busy || !identityConfirmed) return;
    if (!window.confirm("Xóa cuộc trò chuyện này? Lịch sử sẽ bị xóa khỏi máy."))
      return;
    const deletingCurrent = id === session;
    const attempt = ++generation.current;
    setSessionMutation({ kind: "delete", session_id: id, status: "busy" });
    setPhase("loading");
    setError("");
    try {
      if (deletingCurrent) await waitForSessionDiscard(id);
      await api(`/sessions/${id}`, "DELETE");
      const remaining = await refreshSessions();
      if (deletingCurrent) {
        const next = latestSession(remaining);
        if (next) {
          const state = await api<SessionView>(`/sessions/${next.id}`);
          if (attempt !== generation.current) return;
          clearConversationDraft();
          setSession(next.id);
          applySession(state, true);
        } else {
          const created = await api<SessionIdentity>("/sessions", "POST", {
            ...(providerConfirmed ? { provider, model } : {}),
          });
          if (attempt !== generation.current) return;
          clearConversationDraft();
          setSession(created.id);
          await refresh(created.id, true);
          await refreshSessions();
        }
        setShowAudit(false);
      }
      setPhase("idle");
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "delete", session_id: id, status: "unknown" });
      setPhase("idle");
    }
  }
  function useSample(sample: Sample) {
    if (busy) return;
    invalidate();
    if (privatePhase !== "idle") invalidatePrivatePreview();
    setDraft(sample.text);
    setTerms(sample.terms);
    input.current?.focus();
  }

  function changeDraft(text: string) {
    if (busy) return;
    invalidate();
    if (privatePhase !== "idle") invalidatePrivatePreview();
    setDraft(text);
  }
  function changeTerms(text: string) {
    if (busy) return;
    invalidate();
    if (privatePhase !== "idle") invalidatePrivatePreview();
    setTerms(text);
  }
  function changePrivatePublicTask(text: string) {
    if (busy) return;
    if (text === privatePublicTask) return;
    if (phase !== "idle" || preview || pendingReview) invalidate(false);
    invalidatePrivatePreview();
    setPrivatePublicTask(text);
  }
  function changePrivateContext(text: string) {
    if (busy) return;
    if (text === privateContext) return;
    if (phase !== "idle" || preview || pendingReview) invalidate(false);
    invalidatePrivatePreview();
    setPrivateLedgerUseState(false);
    setPrivateContext(text);
  }
  function selectDraft(start: number, end: number) {
    setDraftSelection(selectedOccurrence(draft, start, end));
    setSelectionNotice("");
    setSelectionUndo(null);
  }
  const selectedTerm = draftSelection?.quote.trim() ?? "";
  const currentTerms = terms
    .split("\n")
    .map((term) => term.trim())
    .filter(Boolean);
  const draftTerms = currentTerms.filter(
    (term) => !appliedTerms.includes(term),
  );
  const protectionTerms = [...new Set([...appliedTerms, ...currentTerms])];
  function addTerm() {
    if (busy) return;
    const issue = termIssue(termInput, protectionTerms);
    if (issue) {
      setTermStatus(issue);
      return;
    }
    changeTerms([...currentTerms, termInput.trim()].join("\n"));
    setTermInput("");
    setTermStatus("Đã thêm cụm bảo vệ.");
  }
  function removeTerm(term: string) {
    if (busy || appliedTerms.includes(term) || !draftTerms.includes(term))
      return;
    changeTerms(currentTerms.filter((value) => value !== term).join("\n"));
    setTermStatus("Đã bỏ cụm. Chuẩn bị lại bản gửi.");
  }
  function undoSelection() {
    if (busy || !selectionUndo) return;
    if ("term" in selectionUndo) removeTerm(selectionUndo.term);
    else if (draftFacts.includes(selectionUndo.mark)) {
      invalidate(false);
      setDraftFacts(draftFacts.filter((mark) => mark !== selectionUndo.mark));
    }
    setSelectionNotice("");
    setSelectionUndo(null);
  }
  let selectionIssue = "";
  if (draftSelection) {
    if (factState?.enabled) {
      if (
        draftFacts.some(
          (mark) =>
            mark.occurrence.start === draftSelection.start &&
            mark.occurrence.end === draftSelection.end &&
            mark.occurrence.quote === draftSelection.quote,
        )
      )
        selectionIssue = "Lần xuất hiện này đã được đánh dấu trong bản nháp.";
      else if (draftFacts.length >= 16)
        selectionIssue =
          "Đã đủ 16 đoạn đánh dấu. Hãy chuẩn bị bản gửi hoặc xóa các đánh dấu nháp.";
    } else selectionIssue = termIssue(draftSelection.quote, protectionTerms);
  }
  function protectSelection() {
    if (
      !draftSelection ||
      selectionIssue ||
      !session ||
      busy ||
      showProviders ||
      !providerConfirmed ||
      !factsConfirmed ||
      !identityConfirmed
    )
      return;
    if (
      Array.from(draft)
        .slice(draftSelection.start, draftSelection.end)
        .join("") !== draftSelection.quote
    )
      return;
    if (factState?.enabled) {
      const mark: DraftFact = {
        occurrence: draftSelection,
        category: "confidential",
      };
      stageFact(mark);
      setSelectionUndo({ mark });
      setSelectionNotice(
        "Đã đánh dấu đoạn trong bản nháp. Chuẩn bị rồi duyệt bản gửi để áp dụng.",
      );
    } else {
      changeTerms([...currentTerms, selectedTerm].join("\n"));
      setSelectionUndo({ term: selectedTerm });
      setSelectionNotice(
        "Đã thêm cụm vào danh sách. Chuẩn bị lại và kiểm tra mọi lần khớp trước khi duyệt.",
      );
    }
    setDraftSelection(draftSelection);
  }
  function closeProviders() {
    setShowProviders(false);
  }
  function semanticSample() {
    if (busy) return;
    invalidate();
    if (privatePhase !== "idle") invalidatePrivatePreview();
    setDraft(
      "Thông tin nội bộ, chưa công bố: dự án Aurora sẽ cắt giảm 30 nhân sự vào tháng 12. Soạn lời nhắn mời nhóm quản lý họp để trao đổi kế hoạch này.",
    );
    setTerms("");
    input.current?.focus();
  }
  return {
    model: {
      provider,
      model,
      showProviders,
      detector,
      semantic,
      cloud,
      codex,
      localModel,
      session,
      sessionTitle,
      sessions,
      persistenceEnabled,
      sessionMutation,
      recoveryStatus,
      interruptedTurn,
      providerConfirmed,
      factState,
      factsConfirmed,
      identityConfirmed,
      draftActions,
      draftFacts,
      draftSelection,
      selectionIssue,
      selectionNotice,
      selectionUndoAvailable: selectionUndo !== null,
      messages,
      provisional,
      draft,
      terms,
      draftTerms,
      appliedTerms,
      termInput,
      termStatus,
      legend: preview
        ? previewLegend(preview.sanitized, preview.detections, preparedText)
        : null,
      phase,
      preview,
      pendingReview,
      error,
      inspect,
      audit,
      showAudit,
      cancelPending,
      busy,
      privateWorkAvailable,
      privateWorkHistory,
      privatePublicTask,
      privateContext,
      privatePhase,
      privatePreview,
      privateError,
      privateCancelPending,
      interruptedPrivateWork,
      privateLedgerAvailable,
      privateLedgerModelAvailable,
      structuredLedgerDraft,
      privateLedgerHistory,
      privateLedgerSource,
      privateLedgerPhase,
      privateLedgerCandidate,
      privateLedgerUse,
      privateLedgerError,
      privateLedgerNotice,
      privateLedgerCancelPending,
      privateLedgerRerunPending,
    },
    actions: {
      selectProvider,
      prepare,
      revealEmail,
      reviewPreview,
      send,
      cancel,
      correctFact,
      changeIdentity,
      stageIdentity,
      stageFact,
      cancelPrepare,
      reset,
      useSample,
      changeDraft,
      changeTerms,
      changeTermInput: (text: string) => {
        if (!busy) {
          setTermInput(text);
          setTermStatus("");
        }
      },
      addTerm,
      removeTerm,
      undoSelection,
      selectDraft,
      protectSelection,
      closeProviders,
      openSession,
      renameSession,
      deleteSession,
      semanticSample,
      setInspect,
      clearDraft: () => {
        if (busy) return;
        invalidate();
        if (privatePhase !== "idle") invalidatePrivatePreview();
      },
      clearError: () => setError(""),
      clearPrivateError: () => setPrivateError(""),
      clearPrivateLedgerError: () => setPrivateLedgerError(""),
      openProviders: () => setShowProviders(true),
      toggleAudit: () => setShowAudit(!showAudit),
      openAudit: () => setShowAudit(true),
      changePrivatePublicTask,
      changePrivateContext,
      preparePrivateWork,
      sendPrivateWork,
      rerunPrivateLedger,
      cancelPrivateWork,
      discardPrivateWork,
      changePrivateLedgerSource,
      proposePrivateLedger,
      proposeStructuredLedger,
      changeStructuredLedger,
      confirmPrivateLedger,
      discardPrivateLedger,
      setPrivateLedgerUse,
    },
    elements: { bottom, input, providerButton },
  };
}
