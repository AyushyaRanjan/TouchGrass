/**
 * XP for a completed task, matching AGENTS.md game rules.
 * Prefer a real xp_awarded from the server history when we have it.
 */

import type { HistoryRow, Task } from "@/api/types";
import type { PendingSubmission } from "@/db/local";

export function xpForPass(difficulty: number, firstCountedAttempt: boolean): number {
  return 10 * difficulty + (firstCountedAttempt ? 5 : 0);
}

export function xpForHonor(difficulty: number): number {
  return Math.floor((10 * difficulty) / 2);
}

/** True when the day was completed on honor, not a pass. */
export function isHonorCompletion(mine: PendingSubmission[], historyRows: HistoryRow[]): boolean {
  if (mine.some((p) => p.verdict === "pass") || historyRows.some((r) => r.verdict === "pass")) return false;
  return mine.some((p) => p.honor_queued) || historyRows.some((r) => r.honor > 0);
}

/**
 * XP shown on the completed card. Uses the server's xp_awarded when the
 * completing submission is in the cached history; otherwise the V1 formula.
 */
export function completionXp(task: Task, mine: PendingSubmission[], historyRows: HistoryRow[]): number {
  const forThisTask = historyRows.filter((r) => r.task_id === task.id);
  const passed = forThisTask.find((r) => r.verdict === "pass");
  if (passed && passed.xp_awarded > 0) return passed.xp_awarded;
  const honored = forThisTask.find((r) => r.honor > 0);
  if (honored && honored.xp_awarded > 0) return honored.xp_awarded;

  const honorOnly = isHonorCompletion(mine, forThisTask);
  if (honorOnly) return xpForHonor(task.difficulty);

  // Retake does not count as an attempt for the first-try bonus.
  const counted = mine.filter((p) => p.verdict && p.verdict !== "retake");
  const first = counted.length <= 1;
  return xpForPass(task.difficulty, first);
}
