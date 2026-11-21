import { useEffect, useRef, useState } from "react";
import type {
  Bootstrap,
  ChatMessage,
  Phase,
  Preview,
  SessionIdentity,
  SessionList,
  SessionMutation,
  SessionSummary,
  SessionView,
} from "../data/contracts";
import { errors } from "../data/errors";
import { termIssue } from "../data/protectedTerms";
import { api, setCsrf } from "../network/api";

const ACTIVE_SESSION_HINT_KEY = "censitive.active_session";

function readActiveSessionHint(): string {
  try {
    return window.localStorage.getItem(ACTIVE_SESSION_HINT_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeActiveSessionHint(id: string) {
  try {
    window.localStorage.setItem(ACTIVE_SESSION_HINT_KEY, id);
  } catch {}
}

export function useChatController() {
  const [provider, setProvider] = useState("openai-api");
  const [model, setModel] = useState("");
  const [showProviders, setShowProviders] = useState(false);
  const [providerConfirmed, setProviderConfirmed] = useState(false);
  const [localAvailable, setLocalAvailable] = useState(false);
  const [session, setSession] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionMutation, setSessionMutation] =
    useState<SessionMutation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [terms, setTerms] = useState("");
  const [appliedTerms, setAppliedTerms] = useState<string[]>([]);
  const [termInput, setTermInput] = useState("");
  const [termStatus, setTermStatus] = useState("");
  const [draftSelection, setDraftSelection] = useState<{
    quote: string;
  } | null>(null);
  const [selectionNotice, setSelectionNotice] = useState("");
  const [selectionUndo, setSelectionUndo] = useState("");
  const [phase, setPhase] = useState<Phase>("loading");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [inspect, setInspect] = useState<"preview" | "json">("preview");
  const [cancelPending, setCancelPending] = useState(false);
  const preparingId = useRef("");
  const generation = useRef(0);
  const discardTasks = useRef(new Map<string, Promise<void>>());
  const bottom = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const providerButton = useRef<HTMLButtonElement>(null);
  const busy =
    cancelPending ||
    ["loading", "preparing", "sending"].includes(phase) ||
    sessionMutation !== null;

  function explain(reason: unknown) {
    const code = reason instanceof Error ? reason.message : "REQUEST_FAILED";
    setError(
      errors[code] ??
        "Chưa kết nối được ứng dụng trên máy. Kiểm tra kết nối rồi thử lại.",
    );
  }
  function applySession(state: SessionView, restoreTerms = false) {
    writeActiveSessionHint(state.id);
    setProvider(state.provider);
    setModel(state.model);
    setProviderConfirmed(state.provider_confirmed);
    setAppliedTerms(state.terms);
    if (restoreTerms) setTerms(state.terms.join("\n"));
    setMessages(state.messages);
  }
  async function refresh(id: string, restoreTerms = false) {
    const state = await api<SessionView>(`/sessions/${id}`);
    applySession(state, restoreTerms);
    setSessions((current) =>
      current.map((item) =>
        item.id === id
          ? {
              ...item,
              title: state.title,
              provider: state.provider,
              model: state.model,
              provider_confirmed: state.provider_confirmed,
              message_count: state.messages.length,
            }
          : item,
      ),
    );
    return state;
  }
  async function refreshSessions() {
    const result = await api<SessionList>("/sessions");
    setSessions(result.sessions);
    return result.sessions;
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
    const task = discardTasks.current.get(id);
    if (task) await task;
    else if (preview && id === session) await queueDiscard(id, preview.id);
    setPreview(null);
  }
  function latestSession(items: SessionSummary[]) {
    return items[0] ?? null;
  }
  function clearSelection() {
    setDraftSelection(null);
    setSelectionNotice("");
    setSelectionUndo("");
  }
  function clearConversationDraft() {
    setDraft("");
    setTerms("");
    setAppliedTerms([]);
    setTermInput("");
    setTermStatus("");
    setPreview(null);
    clearSelection();
  }
  async function initialize() {
    try {
      const boot = await api<Bootstrap>("/bootstrap");
      setCsrf(boot.csrf);
      setProvider(boot.provider);
      setModel(boot.model);
      setLocalAvailable(boot.local_available);
      const listed = await refreshSessions();
      const hinted = readActiveSessionHint();
      const existing =
        listed.find((item) => item.id === hinted) ?? latestSession(listed);
      const id =
        existing?.id ?? (await api<SessionIdentity>("/sessions", "POST")).id;
      setSession(id);
      await refresh(id, true);
      if (!existing) await refreshSessions();
    } catch (reason) {
      explain(reason);
    } finally {
      setPhase("idle");
    }
  }
  useEffect(() => {
    void initialize();
  }, []);
  function invalidate() {
    generation.current++;
    clearSelection();
    if (preview && session)
      void queueDiscard(session, preview.id).catch(explain);
    setPreview(null);
    setError("");
    setPhase("idle");
  }
  async function selectProvider(nextProvider: string, nextModel: string) {
    if (busy) return;
    invalidate();
    setPhase("loading");
    setProviderConfirmed(false);
    try {
      const selected = await api<SessionIdentity>(
        `/sessions/${session}/provider`,
        "POST",
        { provider: nextProvider, model: nextModel },
      );
      setProvider(selected.provider);
      setModel(selected.model);
      setProviderConfirmed(true);
      await refresh(selected.id);
    } catch (reason) {
      await refresh(session).catch(() => {});
      setProviderConfirmed(false);
      setError(
        "Lựa chọn bên nhận chưa được xác nhận đầy đủ. Mở Kết nối AI để chọn lại nếu chưa thể chuẩn bị bản gửi.",
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
      phase !== "idle" ||
      !providerConfirmed ||
      messages.some((message) => message.role === "assistant")
    )
      return;
    const attempt = ++generation.current;
    preparingId.current = crypto.randomUUID().replaceAll("-", "");
    setError("");
    setPreview(null);
    setPhase("preparing");
    try {
      const result = await api<Preview>(
        `/sessions/${session}/prepare`,
        "POST",
        {
          text: draft,
          request_id: preparingId.current,
          terms: terms
            .split("\n")
            .map((t) => t.trim())
            .filter(Boolean),
        },
      );
      if (attempt !== generation.current) return;
      setPreview(result);
      setInspect("preview");
      setPhase("ready");
      if (window.matchMedia("(max-width: 900px)").matches)
        requestAnimationFrame(() => {
          if (attempt !== generation.current) return;
          const panel = document.getElementById("privacy-panel");
          panel?.focus({ preventScroll: true });
          panel?.scrollIntoView({ block: "start", behavior: "smooth" });
        });
    } catch (reason) {
      if (attempt === generation.current) {
        explain(reason);
        setPhase("idle");
      }
    }
  }
  async function adjustMethod(change: {
    released?: string[];
    masked?: string[];
  }) {
    if (!preview || !session || phase !== "ready" || busy) return;
    const attempt = ++generation.current;
    setPhase("loading");
    setError("");
    try {
      const next = await api<Preview>(
        `/sessions/${session}/prepared/${preview.id}/adjust`,
        "POST",
        { ...preview.method_adjust, ...change },
      );
      if (attempt !== generation.current) return;
      setPreview(next);
      setInspect("preview");
      setPhase("ready");
    } catch (reason) {
      if (attempt !== generation.current) return;
      setPreview(null);
      setPhase("idle");
      explain(reason);
    }
  }
  async function send() {
    if (busy || !preview || phase !== "ready") return;
    setPhase("sending");
    setError("");
    setCancelPending(false);
    try {
      await api(`/sessions/${session}/send`, "POST", {
        prepared_id: preview.id,
      });
      await refresh(session);
      setDraft("");
    } catch (reason) {
      explain(reason);
      await refresh(session).catch(() => {});
    } finally {
      setCancelPending(false);
      setPreview(null);
      setPhase("idle");
      clearSelection();
      input.current?.focus();
    }
  }
  async function cancel() {
    if (!preview) return;
    setCancelPending(true);
    try {
      await api(`/sessions/${session}/cancel`, "POST", {
        prepared_id: preview.id,
      });
    } catch (reason) {
      explain(reason);
      setCancelPending(false);
    }
  }
  async function cancelPrepare() {
    generation.current++;
    setCancelPending(true);
    clearSelection();
    try {
      await api(`/sessions/${session}/cancel-prepare`, "POST", {
        prepared_id: preparingId.current,
      });
      setError(
        "Đã hủy bản chuẩn bị. Mô hình trên máy có thể cần thêm thời gian để kết thúc lượt phân tích.",
      );
    } catch {
      setError(
        "Chưa xác nhận hủy. Bản cũ sẽ không được dùng để gửi. Bạn có thể chuẩn bị lại; Qwen có thể vẫn đang xử lý.",
      );
    } finally {
      setPreview(null);
      setPhase("idle");
      setCancelPending(false);
    }
  }
  async function reset() {
    if (busy) return;
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
      setPhase("idle");
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "new", status: "unknown" });
      setPhase("idle");
    }
  }

  async function openSession(id: string) {
    if (!id || id === session || busy) return;
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
    if (!id || !nextTitle || busy) return;
    setSessionMutation({ kind: "rename", session_id: id, status: "busy" });
    setError("");
    try {
      await api(`/sessions/${id}/title`, "POST", { title: nextTitle });
      await refreshSessions();
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "rename", session_id: id, status: "unknown" });
    }
  }

  async function deleteSession(id: string) {
    if (!id || busy) return;
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
      }
      setPhase("idle");
      setSessionMutation(null);
    } catch (reason) {
      explain(reason);
      setSessionMutation({ kind: "delete", session_id: id, status: "unknown" });
      setPhase("idle");
    }
  }

  function changeDraft(text: string) {
    if (!busy) {
      invalidate();
      setDraft(text);
    }
  }
  function changeTerms(text: string) {
    if (!busy) {
      invalidate();
      setTerms(text);
    }
  }
  const currentTerms = [
    ...new Set(
      terms
        .split("\n")
        .map((term) => term.trim())
        .filter(Boolean),
    ),
  ];
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
  function selectDraft(start: number, end: number) {
    setSelectionNotice("");
    setSelectionUndo("");
    setDraftSelection(end > start ? { quote: draft.slice(start, end) } : null);
  }
  const selectionIssue = draftSelection
    ? termIssue(draftSelection.quote, protectionTerms)
    : "";
  function protectSelection() {
    if (
      !draftSelection ||
      selectionIssue ||
      busy ||
      showProviders ||
      !providerConfirmed
    )
      return;
    const term = draftSelection.quote.trim();
    changeTerms([...currentTerms, term].join("\n"));
    setDraftSelection({ quote: term });
    setSelectionUndo(term);
    setSelectionNotice(
      "Đã thêm cụm vào danh sách. Chuẩn bị lại và kiểm tra mọi lần khớp trước khi duyệt.",
    );
  }
  function undoSelection() {
    if (!busy && selectionUndo) {
      removeTerm(selectionUndo);
      setSelectionNotice("");
      setSelectionUndo("");
    }
  }
  return {
    model: {
      provider,
      model,
      showProviders,
      providerConfirmed,
      localAvailable,
      session,
      sessions,
      sessionMutation,
      messages,
      draft,
      draftTerms,
      appliedTerms,
      termInput,
      termStatus,
      draftSelection,
      selectionIssue,
      selectionNotice,
      selectionUndoAvailable: !!selectionUndo,
      phase,
      preview,
      error,
      inspect,
      cancelPending,
      busy,
    },
    actions: {
      selectProvider,
      prepare,
      adjustMethod,
      send,
      cancel,
      cancelPrepare,
      reset,
      changeDraft,
      addTerm,
      removeTerm,
      selectDraft,
      protectSelection,
      undoSelection,
      openSession,
      renameSession,
      deleteSession,
      setInspect,
      changeTermInput: (text: string) => {
        if (!busy) {
          setTermInput(text);
          setTermStatus("");
        }
      },
      clearError: () => setError(""),
      openProviders: () => setShowProviders(true),
      closeProviders: () => setShowProviders(false),
    },
    elements: { bottom, input, providerButton },
  };
}
