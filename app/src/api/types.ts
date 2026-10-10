/** Types mirroring the server API contract (see server/main.py + AGENTS.md). */

export type Category =
  | "nature"
  | "movement"
  | "exploration"
  | "social"
  | "creative"
  | "community";

export type Verdict = "pass" | "fail" | "unsure" | "retake" | "rejected";

export type Answer = "yes" | "no" | "unclear";

export type SubmissionStatus = "pending" | "processing" | "done" | "error";

export type Health = {
  ok: boolean;
  model: string;
  server_time: string;
};

/** Display fields only — the rubric never leaves the server (AGENTS.md rule 1). */
export type Task = {
  id: number;
  date: string; // YYYY-MM-DD, the server's local day
  template_id: string;
  category: Category;
  title: string;
  why: string;
  minutes: number;
  difficulty: 1 | 2 | 3;
  proof_hint: string;
  note_prompt: string | null;
  completed: boolean;
  rerolled: boolean;
  latest_verdict: Verdict | null;
  day_ends_at: string; // ISO 8601 UTC of the next local midnight (end of the task's day)
};

export type TasksResponse = { tasks: Task[] };

export type RerollResponse = { task: Task };

export type ProofImage = {
  uri: string;
  name?: string;
  type?: string;
};

export type SubmitInput = {
  taskId: number;
  clientSubmissionId: string;
  capturedAt: string; // ISO 8601 UTC (the photo's capture time)
  note?: string;
  lat?: number;
  lon?: number;
  image: ProofImage;
};

export type SubmissionCreate = { submission_id: number; status: SubmissionStatus };

export type EvidenceItem = { id: string; answer: Answer; evidence: string };

export type Submission = {
  status: SubmissionStatus;
  verdict: Verdict | null;
  hint: string | null;
  evidence: EvidenceItem[];
  xp_awarded: number;
  honor_available: boolean;
  error: string | null;
};

export type HonorResponse = {
  submission_id: number;
  status: SubmissionStatus;
  verdict: Verdict | null;
  honor: number;
  xp_awarded: number;
};

export type DayFlag = { date: string; completed: boolean };

export type Stats = {
  xp: number;
  level: number;
  streak_current: number;
  streak_longest: number;
  tasks_this_month: number;
  completed_today: boolean;
  category_counts_30d: Partial<Record<Category, number>>;
  last_30_days: DayFlag[];
};

export type HistoryRow = {
  id: number;
  client_submission_id: string;
  task_id: number;
  task_date: string | null;
  template_id: string | null;
  verdict: Verdict | null;
  honor: number;
  xp_awarded: number;
  status: SubmissionStatus | string;
  captured_at: string | null;
  local_date: string | null;
  received_at: string | null;
  note: string | null;
  title: string | null;
  thumbnail_url: string | null;
};

export type HistoryResponse = { submissions: HistoryRow[]; limit: number; offset: number };

export type CategoryWeight = "often" | "normal" | "rarely" | "off";

export type Preferences = {
  category_weights: Record<Category, CategoryWeight>;
  social_comfort_max: 1 | 2 | 3;
  max_minutes: 15 | 30 | 60 | null;
  is_default: boolean;
};

export type PreferencesPatch = {
  category_weights?: Partial<Record<Category, CategoryWeight>>;
  social_comfort_max?: 1 | 2 | 3;
  max_minutes?: 15 | 30 | 60 | null;
};

export const DEFAULT_PREFERENCES: Preferences = {
  category_weights: {
    nature: "normal",
    movement: "normal",
    exploration: "normal",
    social: "normal",
    creative: "normal",
    community: "normal",
  },
  social_comfort_max: 2,
  max_minutes: null,
  is_default: true,
};