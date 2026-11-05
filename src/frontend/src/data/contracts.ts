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
export type DraftOccurrence = { start: number; end: number; quote: string };
export type Occurrence = DraftOccurrence & {
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
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  provider?: string;
  metadata?: ChatMetadata | null;
};
export type ChatMetadata = {
  route: "derived_formula" | "ordinary";
  public_plan: string;
  local_execution: "qwen" | "none";
  derived_count: number;
};
export type Preview = {
  stage?: "planner";
  provider?: string;
  model?: string;
  streaming: boolean;
  state?: FactState | null;
  id: string;
  sanitized: string;
  body: string;
  expires_in: number;
  history_count: number;
  context?: {
    mode: "full_history";
    message_count: number;
    omitted_messages: number;
    body_bytes: number;
    body_limit_bytes: number;
    archive_messages: number;
    archive_limit_messages: number;
    input_tokens: number | null;
  };
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
    unmasked_once?: boolean;
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
export type ChatSendResult = {
  content?: string;
  provider?: string;
  metadata?: ChatMetadata | null;
};
export type PreviewDecision = {
  operation_id: string;
  prepared_id: string;
  expected_epoch: number;
  command: { kind: "reject_new_mask"; candidate_id: string };
};
export type PreviewDecisionResult = {
  receipt: {
    operation_id: string;
    old_prepared_id: string;
    new_prepared_id: string;
    body_sha256: string;
  };
  preview_available: boolean;
  preview: Preview | null;
};
export type SessionView = {
  id: string;
  title?: string;
  provider: string;
  model: string;
  provider_confirmed?: boolean;
  recovery_status?: string | null;
  interrupted_turn?: { text: string; status: string } | null;
  interrupted_private_work?: InterruptedPrivateWork | null;
  terms?: string[];
  messages: ChatMessage[];
  audit: { event: string; status: string; version: number }[];
  state?: FactState;
  private_work_available?: boolean;
  private_work_history?: PrivateWorkRecord[];
  private_ledger_available?: boolean;
  private_ledger_model_available?: boolean;
  private_ledger_history?: LedgerRevision[];
};
export type InterruptedPrivateWork = {
  id: string;
  public_task: string;
  private_context: string;
  status: "unknown";
};
export type Phase = "loading" | "idle" | "preparing" | "ready" | "sending";
export type PrivateWorkPhase = "idle" | "preparing" | "ready" | "sending";
export type PrivateWorkRecord = {
  id: string;
  public_task: string;
  private_context: string;
  answer: string;
  route: string;
  public_body_sha256: string;
  provider: string;
  model: string;
  trace: string[];
  ledger_revision?: number | null;
};
export type PrivateWorkPreview = {
  prepared_id: string;
  public_task: string;
  public_body: string;
  public_body_sha256: string;
  provider: string;
  model: string;
  ledger_revision?: number | null;
  stage?: "planner";
};
export type LedgerEvidence = {
  quote: string;
};
export type LedgerEvent = {
  kind: "add" | "replace_amount" | "cancel" | "set_scalar";
  label?: string;
  amount_id?: string;
  field?: string;
  amount?: string | number;
  value?: string | number;
  value_quote?: string;
  evidence: LedgerEvidence;
};
export type LedgerRecord = {
  amount_id: string;
  label: string;
  amount: string | number;
  source_quote: string;
  active: boolean;
};
export type LedgerRevision = {
  origin?: "model" | "user";
  revision: number;
  parent_revision: number | null;
  source_text?: string;
  source_sha256: string;
  events: LedgerEvent[];
  records: LedgerRecord[];
  recipient?: string | null;
  project?: string | null;
  unit?: string | null;
  surcharge_percent?: string | number | null;
  budget?: string | number | null;
  effective?: {
    active_amounts: Array<string | number>;
    surcharge_percent?: string | number | null;
    budget?: string | number | null;
    recipient?: string | null;
    project?: string | null;
    unit?: string | null;
  };
};
export type PrivateLedgerCandidate = {
  proposal_id: string;
  candidate: LedgerRevision;
};
export type PrivateLedgerProposalResult =
  PrivateLedgerCandidate | { status: "no_changes"; base_revision: number };
export type Bootstrap = {
  csrf: string;
  provider: string;
  model: string;
  detector: string;
  state_enabled?: boolean;
  persistence_enabled?: boolean;
};
export type SessionSummary = {
  id: string;
  title: string;
  provider: string;
  model: string;
  updated_at?: string | null;
  message_count?: number;
  private_work_count?: number;
  recovery_status?: string | null;
  provider_confirmed?: boolean;
};
export type SessionList = { sessions: SessionSummary[] };
export type SessionIdentity = {
  id: string;
  provider: string;
  model: string;
  title?: string;
  provider_confirmed?: boolean;
  recovery_status?: string | null;
};

export type SessionMutationKind = "new" | "switch" | "delete" | "rename";
export type SessionMutation = {
  kind: SessionMutationKind;
  session_id?: string;
  status: "busy" | "unknown";
};

export type InspectTab = "preview" | "json" | "evidence";
