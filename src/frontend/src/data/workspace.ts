export interface WorkflowStatus {
  step: 0 | 1 | 2 | 3;
  title: string;
  detail: string;
  attention: boolean;
}
