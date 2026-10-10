import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Alert, AppState, StyleSheet, Text, View } from "react-native";

import { ApiError } from "@/api/client";
import type { HistoryResponse, HistoryRow, Stats, Task } from "@/api/types";
import { ResultCard } from "@/components/result-card";
import {
  BigButton,
  Card,
  Chip,
  DifficultyDots,
  EmptyState,
  LoadingView,
  Screen,
  TextAction,
} from "@/components/ui";
import { getCachedTasks, listPending, subscribe, type PendingSubmission } from "@/db/local";
import { useSettings } from "@/hooks/use-settings";
import { HISTORY_CACHE_KEY, readCache } from "@/lib/cache";
import { hasDayEnded, isEveningBeforeEnd } from "@/lib/day";
import { dismiss, loadDismissed, subscribeDismissed } from "@/lib/dismissed";
import { completionXp, isHonorCompletion } from "@/lib/xp";
import { loadCachedStats } from "@/sync/engine";
import { pickToday } from "@/sync/rules";
import { useSync } from "@/sync/provider";
import { colors, radius, spacing, type } from "@/theme";

const MINUTE_MS = 60 * 1000;

/** The current time, refreshed every minute and whenever the app comes back to the foreground. */
function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const timer = setInterval(tick, MINUTE_MS);
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") tick();
    });
    return () => {
      clearInterval(timer);
      subscription.remove();
    };
  }, []);
  return now;
}

type LocalData = {
  tasks: Task[];
  pending: PendingSubmission[];
  stats: Stats | null;
  history: HistoryRow[];
  loaded: boolean;
};

/** Today reads only the phone's own store, so it works fully offline. */
function useLocalData(): LocalData & { reload: () => Promise<void>; lastSyncAt: unknown } {
  const [data, setData] = useState<LocalData>({ tasks: [], pending: [], stats: null, history: [], loaded: false });
  const { lastSyncAt } = useSync();

  const reload = useCallback(async () => {
    const [tasks, pending, stats, historyCache] = await Promise.all([
      getCachedTasks(),
      listPending(),
      loadCachedStats(),
      readCache<HistoryResponse>(HISTORY_CACHE_KEY),
    ]);
    setData({ tasks, pending, stats, history: historyCache?.data.submissions ?? [], loaded: true });
  }, []);

  // Re-read whenever the local store changes (the initial read is done by the focus effect).
  useEffect(() => subscribe(() => void reload()), [reload]);

  return { ...data, reload, lastSyncAt };
}

export default function TodayScreen() {
  const router = useRouter();
  const { api, configured } = useSettings();
  const sync = useSync();
  const now = useMinuteClock();
  const local = useLocalData();
  const [rerolling, setRerolling] = useState(false);
  // Closed result cards. React state, so Close re-renders immediately (the React
  // Compiler would not see a plain module-level Set change).
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(() => new Set());
  useEffect(() => subscribeDismissed(() => void loadDismissed().then(setDismissedIds)), []);

  const { reload, lastSyncAt } = local;
  useFocusEffect(
    useCallback(() => {
      // Reading lastSyncAt makes a finished sync re-read stats too.
      void lastSyncAt;
      void reload();
      void loadDismissed().then(setDismissedIds);
    }, [reload, lastSyncAt]),
  );

  const task = pickToday(local.tasks, now);

  // When local midnight passes with the app open, fetch the new day's task.
  const dayEndsAt = task?.day_ends_at;
  const { syncNow } = sync;
  useEffect(() => {
    if (configured && dayEndsAt && hasDayEnded(dayEndsAt, now)) void syncNow(true);
  }, [configured, dayEndsAt, now, syncNow]);

  if (!configured) {
    return (
      <Screen>
        <Text style={type.title}>Today</Text>
        <EmptyState
          icon="server-outline"
          title="Connect your server"
          message="Add your server address and API token in Settings so Touch Grass can fetch today's task."
          actionLabel="Open Settings"
          onAction={() => router.push("/settings")}
        />
      </Screen>
    );
  }

  if (!local.loaded) return <LoadingView message="Loading today's task…" />;

  if (!task) {
    return (
      <Screen>
        <Text style={type.title}>Today</Text>
        <EmptyState
          icon="cloud-offline-outline"
          title="Nothing saved yet"
          message="Open the app once while connected to your server."
          actionLabel="Try again"
          onAction={() => void syncNow(true)}
        />
        {sync.authBlocked ? <AuthBanner message={sync.authMessage} /> : null}
      </Screen>
    );
  }

  // Local items for today's task, newest last.
  const mine = local.pending.filter((p) => p.task_id === task.id);
  const latest = mine[mine.length - 1] ?? null;
  const confirmedDone = task.completed || mine.some((p) => p.verdict === "pass" || p.honor_queued);
  const inFlight = mine.some((p) => p.status === "queued" || p.status === "uploading" || p.status === "uploaded");
  const provisional = !confirmedDone && inFlight;

  const openCapture = () =>
    // Cast: typed-route definitions are regenerated by `expo start`, which may not have seen /capture yet.
    router.push({
      pathname: "/capture",
      params: { taskId: String(task.id), title: task.title, notePrompt: task.note_prompt ?? "" },
    } as never);

  // A finished attempt that needs the user (fail / unsure / retake / rejected, or a 4xx failure).
  const needsAttention =
    !confirmedDone &&
    latest !== null &&
    !dismissedIds.has(latest.client_submission_id) &&
    (latest.status === "failed" || (latest.status === "done" && latest.verdict !== "pass"));
  if (needsAttention && latest) {
    return (
      <ResultCard
        item={latest}
        onRetake={() => {
          setDismissedIds(dismiss(latest.client_submission_id));
          openCapture();
        }}
        onDone={() => setDismissedIds(dismiss(latest.client_submission_id))}
      />
    );
  }

  const confirmReroll = () => {
    Alert.alert(
      "Get a different task?",
      "This uses your one free reroll for today. The current task will be replaced.",
      [
        { text: "Keep this one", style: "cancel" },
        {
          text: "Reroll",
          style: "destructive",
          onPress: () => {
            setRerolling(true);
            api
              .reroll(task.id)
              .then(() => syncNow(true))
              .catch((err) => {
                Alert.alert("Reroll failed", err instanceof ApiError ? err.message : String(err));
              })
              .finally(() => setRerolling(false));
          },
        },
      ],
    );
  };

  return (
    <Screen>
      <StatsHeader stats={local.stats} provisional={provisional} />

      {sync.queued > 0 ? <WaitingPill count={sync.queued} /> : null}
      {sync.authBlocked ? <AuthBanner message={sync.authMessage} /> : null}

      <View style={styles.row}>
        <Text style={type.title}>Today</Text>
      </View>

      {confirmedDone ? (
        <CompletedCard
          task={task}
          mine={mine}
          history={local.history.filter((r) => r.task_id === task.id)}
          streak={local.stats?.streak_current ?? 0}
        />
      ) : (
        <TaskCard task={task} now={now} />
      )}

      {confirmedDone ? null : provisional ? (
        <Card style={styles.doneCard}>
          <Ionicons name="checkmark-circle-outline" size={30} color={colors.accent} />
          <Text style={[type.heading, { marginTop: spacing.sm, textAlign: "center" }]}>Saved.</Text>
          <Text style={[type.body, { color: colors.textMuted, textAlign: "center", marginTop: spacing.xs }]}>
            I&apos;ll check it when I can reach your laptop.
          </Text>
        </Card>
      ) : (
        <BigButton label="Take proof photo" onPress={openCapture} />
      )}

      {!confirmedDone && !provisional ? (
        <View>
          <TextAction
            label="Different task"
            busy={rerolling}
            disabled={rerolling || !sync.online}
            onPress={confirmReroll}
          />
          {!sync.online ? (
            <Text style={[type.small, { textAlign: "center" }]}>Rerolling needs a connection to your server.</Text>
          ) : null}
        </View>
      ) : null}
    </Screen>
  );
}

function StatsHeader({ stats, provisional }: { stats: Stats | null; provisional: boolean }) {
  if (!stats) return null;
  return (
    <View style={styles.statsRow}>
      <View style={styles.statPill}>
        {provisional ? (
          // Dotted flame: today is saved but not confirmed yet.
          <View style={styles.dottedFlame}>
            <Ionicons name="flame-outline" size={14} color={colors.accentDark} />
          </View>
        ) : (
          <Ionicons name="flame" size={14} color={colors.accentDark} />
        )}
        <Text style={styles.statPillValue}>{stats.streak_current}</Text>
        <Text style={styles.statPillLabel}>{provisional ? "day streak +1 pending" : "day streak"}</Text>
      </View>
      <View style={styles.statPill}>
        <Text style={styles.statPillValue}>{stats.xp}</Text>
        <Text style={styles.statPillLabel}>xp</Text>
      </View>
    </View>
  );
}

function WaitingPill({ count }: { count: number }) {
  return (
    <View style={styles.waitingPill}>
      <Ionicons name="cloud-upload-outline" size={14} color={colors.textMuted} />
      <Text style={styles.waitingText}>Waiting to sync ({count})</Text>
    </View>
  );
}

function AuthBanner({ message }: { message: string | null }) {
  return (
    <Card>
      <Text style={type.body}>{message ?? "Token mismatch, check Settings"}</Text>
    </Card>
  );
}

function CompletedCard({
  task,
  mine,
  history,
  streak,
}: {
  task: Task;
  mine: PendingSubmission[];
  history: HistoryRow[];
  streak: number;
}) {
  const honor = isHonorCompletion(mine, history);
  const xp = completionXp(task, mine, history);
  return (
    <Card style={styles.completedCard}>
      <Ionicons name="checkmark-circle" size={32} color={colors.accentDark} />
      <Text style={[type.heading, { marginTop: spacing.md, color: colors.textMuted, textAlign: "center" }]}>
        {task.title}
      </Text>
      {honor ? (
        <Text style={[type.small, { marginTop: spacing.sm, textAlign: "center" }]}>Marked done (half XP)</Text>
      ) : (
        <Text style={[type.heading, { marginTop: spacing.sm, textAlign: "center" }]}>Done for today. Go outside.</Text>
      )}
      <Text style={[type.small, { marginTop: spacing.sm, textAlign: "center" }]}>
        +{xp} XP · {streak} day streak
      </Text>
    </Card>
  );
}

function TaskCard({ task, now }: { task: Task; now: number }) {
  const showEvening = !task.completed && isEveningBeforeEnd(task.day_ends_at, now);
  return (
    <Card>
      <View style={styles.row}>
        <Chip label={task.category} tone="success" />
        <View style={styles.headerSpacer} />
        <DifficultyDots difficulty={task.difficulty} />
      </View>
      <Text style={[type.heading, { marginTop: spacing.md }]}>{task.title}</Text>
      <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.xs }]}>{task.why}</Text>
      <View style={{ marginTop: spacing.md }}>
        <Text style={type.body}>Anytime today</Text>
        <Text style={type.small}>Usually about {task.minutes} min</Text>
        {showEvening ? (
          <Text style={[type.small, { marginTop: spacing.xs }]}>Ends tonight at midnight</Text>
        ) : null}
      </View>
      <View style={styles.divider} />
      <Text style={[type.label, { color: colors.accentDark }]}>Proof</Text>
      <Text style={[type.body, { marginTop: spacing.xs }]}>{task.proof_hint}</Text>
      {task.note_prompt ? (
        <>
          <View style={styles.divider} />
          <Text style={[type.label, { color: colors.accentDark }]}>Add a note</Text>
          <Text style={[type.body, { marginTop: spacing.xs }]}>{task.note_prompt}</Text>
        </>
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  doneCard: { alignItems: "center" },
  completedCard: { alignItems: "center", backgroundColor: colors.accentSoft, borderColor: colors.accentSoft },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  headerSpacer: { flex: 1 },
  divider: { height: 1, backgroundColor: colors.border, marginVertical: spacing.md },
  statsRow: { flexDirection: "row", gap: spacing.md },
  statPill: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: spacing.xs,
    backgroundColor: colors.card,
    borderRadius: radius.pill,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  dottedFlame: {
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: colors.accentDark,
    borderRadius: radius.pill,
    padding: 2,
  },
  statPillValue: { fontSize: 18, fontWeight: "800", color: colors.accentDark },
  statPillLabel: { fontSize: 13, color: colors.textMuted, fontWeight: "600" },
  waitingPill: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: spacing.xs,
    backgroundColor: colors.neutralSoft,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  waitingText: { fontSize: 13, color: colors.textMuted, fontWeight: "600" },
});
