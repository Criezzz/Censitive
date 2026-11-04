import type {
  JevAssessment,
  ChatMessage,
  Preview,
  PreviewDecision,
  SessionView,
  Phase,
  InspectTab,
  FactState,
  Correction,
  DraftAction,
  DraftFact,
  DraftOccurrence,
  ProtectionCommand,
  SessionSummary,
  SessionMutation,
  PrivateWorkPhase,
  PrivateWorkPreview,
  PrivateWorkRecord,
  InterruptedPrivateWork,
  LedgerRevision,
  PrivateLedgerCandidate,
} from "./contracts";
import type { Sample } from "./samples";
import type { StructuredLedgerDraft } from "./structuredLedger";

export interface ChatModel {
  provider: string;
  model: string;
  showProviders: boolean;
  detector: string;
  semantic: boolean;
  cloud: boolean;
  codex: boolean;
  localModel: boolean;
  session: string;
  sessionTitle: string;
  sessions: SessionSummary[];
  persistenceEnabled: boolean;
  sessionMutation: SessionMutation | null;
  recoveryStatus: string | null;
  interruptedTurn: { text: string; status: string } | null;
  providerConfirmed: boolean;
  factState: FactState | null;
  factsConfirmed: boolean;
  identityConfirmed: boolean;
  draftActions: DraftAction[];
  draftFacts: DraftFact[];
  draftSelection: DraftOccurrence | null;
  selectionIssue: string;
  selectionNotice: string;
  messages: ChatMessage[];
  provisional: string | null;
  draft: string;
  terms: string;
  phase: Phase;
  preview: Preview | null;
  pendingReview: PreviewDecision | null;
  error: string;
  inspect: InspectTab;
  audit: SessionView["audit"];
  showAudit: boolean;
  cancelPending: boolean;
  busy: boolean;
  privateWorkAvailable: boolean;
  jevEnabled: boolean;
  jevConfigured: boolean;
  jevKeyOpen: boolean;
  jevKey: string;
  jevKeyBusy: boolean;
  latestJev: JevAssessment | null;
  privateWorkHistory: PrivateWorkRecord[];
  privatePublicTask: string;
  privateContext: string;
  privatePhase: PrivateWorkPhase;
  privatePreview: PrivateWorkPreview | null;
  privateError: string;
  privateCancelPending: boolean;
  interruptedPrivateWork: InterruptedPrivateWork | null;
  privateLedgerAvailable: boolean;
  privateLedgerModelAvailable: boolean;
  structuredLedgerDraft: StructuredLedgerDraft;
  privateLedgerHistory: LedgerRevision[];
  privateLedgerSource: string;
  privateLedgerPhase: "idle" | "proposing" | "candidate" | "confirming";
  privateLedgerCandidate: PrivateLedgerCandidate | null;
  privateLedgerUse: boolean;
  privateLedgerError: string;
  privateLedgerNotice: string;
  privateLedgerCancelPending: boolean;
  privateLedgerRerunPending: boolean;
}

export interface ChatActions {
  toggleJev(): Promise<void>;
  saveJevKey(): Promise<void>;
  removeJevKey(): Promise<void>;
  changeJevKey(key: string): void;
  closeJevKey(): void;
  selectProvider(provider: string, model: string): Promise<void>;
  prepare(): Promise<void>;
  reviewPreview(candidateId?: string): Promise<void>;
  send(): Promise<void>;
  cancel(): Promise<void>;
  cancelPrepare(): Promise<void>;
  reset(): Promise<void>;
  correctFact(command: Correction): Promise<void>;
  changeIdentity(command: ProtectionCommand): Promise<void>;
  stageIdentity(action: DraftAction): void;
  stageFact(mark: DraftFact): void;
  useSample(sample: Sample): void;
  semanticSample(): void;
  changeDraft(text: string): void;
  changeTerms(text: string): void;
  selectDraft(start: number, end: number): void;
  protectSelection(): void;
  setInspect(tab: InspectTab): void;
  clearDraft(): void;
  clearError(): void;
  clearPrivateError(): void;
  clearPrivateLedgerError(): void;
  openProviders(): void;
  closeProviders(): void;
  openSession(id: string): Promise<void>;
  renameSession(id: string, title: string): Promise<void>;
  deleteSession(id: string): Promise<void>;
  toggleAudit(): void;
  openAudit(): void;
  changePrivatePublicTask(text: string): void;
  changePrivateContext(text: string): void;
  preparePrivateWork(): Promise<void>;
  sendPrivateWork(): Promise<void>;
  rerunPrivateLedger(workId: string): Promise<void>;
  cancelPrivateWork(): Promise<void>;
  discardPrivateWork(): Promise<void>;
  changePrivateLedgerSource(text: string): void;
  proposePrivateLedger(): Promise<void>;
  proposeStructuredLedger(): Promise<void>;
  changeStructuredLedger(
    field: keyof StructuredLedgerDraft,
    value: string,
  ): void;
  confirmPrivateLedger(): Promise<void>;
  discardPrivateLedger(): Promise<void>;
  setPrivateLedgerUse(enabled: boolean): void;
}
