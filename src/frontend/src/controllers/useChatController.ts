import { useEffect, useRef, useState } from "react";
import type {
  ChatMessage,
  Preview,
  PreviewDecision,
  PreviewDecisionResult,
  SessionView,
  Phase,
  Bootstrap,
  SessionIdentity,
  FactState,
  Correction,
  DraftAction,
  DraftFact,
  DraftOccurrence,
  ProtectionCommand,
  FactResult,
  IdentityResult,
} from "../data/contracts";
import {
  errors,
  reviewErrors,
  correctionErrors,
  identityErrors,
} from "../data/errors";
import type { Sample } from "../data/samples";
import { selectedOccurrence } from "../data/occurrences";
import { api, setCsrf, sendStream } from "../network/api";
import { readStream } from "../stream";

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
      setCsrf(boot.csrf);
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
    setSelectionNotice("");
  }, [factState?.epoch, session, provider]);

  function clearDraftIdentity() {
    setDraftActions([]);
    setDraftFacts([]);
    setDraftSelection(null);
    setSelectionNotice("");
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
      setCsrf(boot.csrf);
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
  function useSample(sample: Sample) {
    invalidate();
    setDraft(sample.text);
    setTerms(sample.terms);
    input.current?.focus();
  }

  function changeDraft(text: string) {
    invalidate();
    setDraft(text);
  }
  function changeTerms(text: string) {
    invalidate();
    setTerms(text);
  }
  function selectDraft(start: number, end: number) {
    setDraftSelection(selectedOccurrence(draft, start, end));
    setSelectionNotice("");
  }
  const selectedTerm = draftSelection?.quote.trim() ?? "";
  const currentTerms = terms
    .split("\n")
    .map((term) => term.trim())
    .filter(Boolean);
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
    } else if (/[\r\n]/.test(draftSelection.quote))
      selectionIssue =
        "Chọn một cụm trên cùng một dòng để bảo vệ bằng khớp chính xác.";
    else if (Array.from(selectedTerm).length > 200)
      selectionIssue = "Chọn cụm không quá 200 ký tự.";
    else if (selectedTerm.includes("[[PG_"))
      selectionIssue = "Đoạn chọn chứa ký hiệu dành riêng của ứng dụng.";
    else if (currentTerms.includes(selectedTerm))
      selectionIssue = "Cụm này đã có trong danh sách bảo vệ.";
    else if (currentTerms.length >= 30)
      selectionIssue =
        "Đã đủ 30 cụm bảo vệ. Hãy kiểm tra danh sách trước khi thêm.";
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
      stageFact({ occurrence: draftSelection, category: "confidential" });
      setSelectionNotice(
        "Đã đánh dấu đoạn trong bản nháp. Chuẩn bị rồi duyệt bản gửi để áp dụng.",
      );
    } else {
      invalidate();
      setTerms([...currentTerms, selectedTerm].join("\n"));
      setSelectionNotice(
        "Đã thêm cụm vào danh sách. Chuẩn bị lại và kiểm tra mọi lần khớp trước khi duyệt.",
      );
    }
    setDraftSelection(draftSelection);
  }
  function closeProviders() {
    setShowProviders(false);
    requestAnimationFrame(() => providerButton.current?.focus());
  }
  function semanticSample() {
    invalidate();
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
      providerConfirmed,
      factState,
      factsConfirmed,
      identityConfirmed,
      draftActions,
      draftFacts,
      draftSelection,
      selectionIssue,
      selectionNotice,
      messages,
      provisional,
      draft,
      terms,
      phase,
      preview,
      pendingReview,
      error,
      inspect,
      audit,
      showAudit,
      cancelPending,
      busy,
    },
    actions: {
      selectProvider,
      prepare,
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
      selectDraft,
      protectSelection,
      closeProviders,
      semanticSample,
      setInspect,
      clearDraft: () => invalidate(),
      clearError: () => setError(""),
      openProviders: () => setShowProviders(true),
      toggleAudit: () => setShowAudit(!showAudit),
      openAudit: () => setShowAudit(true),
    },
    elements: { bottom, input, providerButton },
  };
}
