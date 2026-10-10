/**
 * Pure helpers for the "anytime today" task window (AGENTS.md: a task's
 * window is the whole local day). Times come from the server's
 * `day_ends_at` (UTC ISO of the next local midnight in TIMEZONE), so they
 * stay right even when the phone's own timezone differs from the server's.
 */

const HOUR_MS = 60 * 60 * 1000;
/** From 6 pm (6 hours before midnight) we gently mention the day ends tonight. */
const EVENING_LEAD_MS = 6 * HOUR_MS;

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** True once local midnight has passed and today's task belongs to yesterday. */
export function hasDayEnded(dayEndsAt: string, now: number = Date.now()): boolean {
  return now >= Date.parse(dayEndsAt);
}

/** True from 6 pm local until midnight. DST changes happen around 2 am, so the day's last 6 h are always 18:00-24:00. */
export function isEveningBeforeEnd(dayEndsAt: string, now: number = Date.now()): boolean {
  const end = Date.parse(dayEndsAt);
  return now >= end - EVENING_LEAD_MS && now < end;
}

/** The capture's local calendar date, e.g. "Sat 10 Oct", for "Counts for <date>". */
export function countsForDate(capturedAtIso: string): string {
  const d = new Date(capturedAtIso);
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}
