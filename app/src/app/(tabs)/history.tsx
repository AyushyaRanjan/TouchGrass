import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import type { HistoryRow, Task } from "@/api/types";
import { AuthedImage } from "@/components/authed-image";
import { Card, Chip, EmptyState, ErrorCard, LoadingView, Screen, StaleLabel, VerdictChip } from "@/components/ui";
import { getCachedTasks, listPending, subscribe, type PendingSubmission } from "@/db/local";
import { useFetch } from "@/hooks/use-fetch";
import { useSettings } from "@/hooks/use-settings";
import { HISTORY_CACHE_KEY, staleText } from "@/lib/cache";
import { countsForDate } from "@/lib/day";
import {
  formatDayLabel,
  groupHistoryDays,
  type DayGroup,
  type DayOutcome,
  type HistoryAttempt,
} from "@/lib/history-days";
import { useSync } from "@/sync/provider";
import { colors, radius, spacing, type } from "@/theme";

/** Queued items and the cached task titles, kept fresh as the local store changes. */
function useLocalHistory(): { pending: PendingSubmission[]; tasks: Task[] } {
  const [state, setState] = useState<{ pending: PendingSubmission[]; tasks: Task[] }>({ pending: [], tasks: [] });
  const { lastSyncAt } = useSync();
  const refresh = useCallback(async () => {
    const [pending, tasks] = await Promise.all([listPending(), getCachedTasks()]);
    setState({ pending, tasks });
  }, []);
  useEffect(() => subscribe(() => void refresh()), [refresh]);
  useFocusEffect(
    useCallback(() => {
      void lastSyncAt;
      void refresh();
    }, [refresh, lastSyncAt]),
  );
  return state;
}

export default function HistoryScreen() {
  const { api } = useSettings();
  const fetch = useFetch(() => api.history(50, 0), { cacheKey: HISTORY_CACHE_KEY });
  const { load } = fetch;
  const { pending, tasks } = useLocalHistory();
  const { lastSyncAt, online } = useSync();
  const [openDate, setOpenDate] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      void load();
    }, [load]),
  );
  useEffect(() => {
    if (lastSyncAt) void load();
  }, [lastSyncAt, load]);

  const serverRows = fetch.data?.submissions ?? [];
  const days = groupHistoryDays(serverRows, pending, tasks);

  if (fetch.loading && !fetch.data && days.length === 0) return <LoadingView message="Loading your history…" />;

  if (fetch.error && !fetch.data && days.length === 0) {
    return (
      <Screen>
        <Text style={type.title}>History</Text>
        <ErrorCard title="Couldn't load history" message={fetch.error.message} onRetry={() => void fetch.load()} />
      </Screen>
    );
  }

  if (days.length === 0) {
    return (
      <Screen>
        <Text style={type.title}>History</Text>
        <EmptyState
          icon="time-outline"
          title="Nothing yet"
          message="Your completed and attempted tasks will show up here with their verdicts."
        />
      </Screen>
    );
  }

  const showStale = (fetch.stale || !online) && fetch.updatedAt;

  return (
    <Screen scroll={false}>
      <Text style={type.title}>History</Text>
      {showStale && fetch.updatedAt ? <StaleLabel text={staleText(fetch.updatedAt)} /> : null}
      <FlatList
        style={styles.list}
        contentContainerStyle={styles.listContent}
        data={days}
        keyExtractor={(day) => day.date}
        renderItem={({ item: day }) => (
          <DayRow
            day={day}
            expanded={openDate === day.date}
            onToggle={() => setOpenDate((current) => (current === day.date ? null : day.date))}
          />
        )}
      />
    </Screen>
  );
}

function DayRow({ day, expanded, onToggle }: { day: DayGroup; expanded: boolean; onToggle: () => void }) {
  return (
    <View>
      <Pressable
        onPress={onToggle}
        hitSlop={8}
        accessibilityRole="button"
        style={({ pressed }) => [pressed && { opacity: 0.7 }]}
      >
        <Card style={styles.dayCard}>
          <AttemptThumb attempt={day.best} />
          <View style={styles.cardBody}>
            <Text style={type.heading} numberOfLines={2}>
              {day.title}
            </Text>
            <Text style={[type.small, { marginTop: spacing.xs }]}>{formatDayLabel(day.date)}</Text>
            <View style={styles.badgeRow}>
              <OutcomeChip outcome={day.outcome} />
            </View>
          </View>
        </Card>
      </Pressable>
      {expanded
        ? day.attempts.map((attempt) =>
            attempt.kind === "local" ? (
              <LocalCard key={attempt.key} item={attempt.item} title={day.title} />
            ) : (
              <HistoryCard key={attempt.key} row={attempt.row} />
            ),
          )
        : null}
    </View>
  );
}

function OutcomeChip({ outcome }: { outcome: DayOutcome }) {
  if (outcome === "pass") return <Chip label="pass" tone="success" />;
  if (outcome === "honor") return <Chip label="honor" tone="warning" />;
  if (outcome === "waiting") return <Chip label="Waiting to sync" />;
  return <Chip label="not done" tone="neutral" />;
}

function AttemptThumb({ attempt }: { attempt: HistoryAttempt }) {
  if (attempt.kind === "server") {
    return (
      <AuthedImage
        submissionId={attempt.row.id}
        thumb
        style={styles.thumb}
        placeholderStyle={styles.thumbPlaceholder}
      />
    );
  }
  return (
    <AuthedImage
      submissionId={attempt.item.server_submission_id}
      thumb
      localUri={attempt.item.image_uri}
      style={styles.thumb}
      placeholderStyle={styles.thumbPlaceholder}
    />
  );
}

function HistoryCard({ row }: { row: HistoryRow }) {
  return (
    <Card style={styles.attemptCard}>
      <AuthedImage
        submissionId={row.id}
        thumb
        style={styles.thumb}
        placeholderStyle={styles.thumbPlaceholder}
      />
      <View style={styles.cardBody}>
        <Text style={type.body} numberOfLines={2}>
          {row.title ?? `Task #${row.task_id}`}
        </Text>
        <View style={styles.badgeRow}>
          {row.honor > 0 && row.verdict !== "pass" ? null : <VerdictChip verdict={row.verdict} />}
          {row.xp_awarded > 0 ? <XpBadge xp={row.xp_awarded} /> : null}
          {row.honor > 0 ? <HonorBadge /> : null}
        </View>
        {row.note ? (
          <Text style={[type.small, { marginTop: spacing.sm }]} numberOfLines={3}>
            “{row.note}”
          </Text>
        ) : null}
      </View>
    </Card>
  );
}

function LocalCard({ item, title }: { item: PendingSubmission; title: string }) {
  return (
    <Card style={styles.attemptCard}>
      <AuthedImage
        submissionId={item.server_submission_id}
        thumb
        localUri={item.image_uri}
        style={styles.thumb}
        placeholderStyle={styles.thumbPlaceholder}
      />
      <View style={styles.cardBody}>
        <Text style={type.body} numberOfLines={2}>
          {title}
        </Text>
        <Text style={[type.small, { marginTop: spacing.xs }]}>Counts for {countsForDate(item.captured_at)}</Text>
        <View style={styles.badgeRow}>
          <LocalStatus item={item} />
        </View>
        {item.status === "failed" && item.last_error ? (
          <Text style={[type.small, { marginTop: spacing.sm }]} numberOfLines={3}>
            {item.last_error}
          </Text>
        ) : null}
      </View>
    </Card>
  );
}

function LocalStatus({ item }: { item: PendingSubmission }) {
  switch (item.status) {
    case "queued":
      return <Chip label="Waiting to sync" />;
    case "uploading":
      return <Chip label="Uploading" />;
    case "uploaded":
      return <Chip label="Checking" tone="warning" />;
    case "failed":
      return <Chip label="Failed" tone="danger" />;
    default:
      return <VerdictChip verdict={item.verdict} />;
  }
}

function XpBadge({ xp }: { xp: number }) {
  return (
    <View style={[styles.badge, { backgroundColor: colors.accentSoft }]}>
      <Ionicons name="star" size={12} color={colors.accentDark} />
      <Text style={[styles.badgeText, { color: colors.accentDark }]}>+{xp} xp</Text>
    </View>
  );
}

function HonorBadge() {
  return (
    <View style={[styles.badge, { backgroundColor: colors.warningSoft }]}>
      <Ionicons name="hand-left" size={12} color={colors.warning} />
      <Text style={[styles.badgeText, { color: colors.warning }]}>honor</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  list: { alignSelf: "stretch" },
  listContent: { gap: spacing.md, paddingBottom: spacing.xl },
  dayCard: { flexDirection: "row", gap: spacing.md, alignItems: "flex-start", minHeight: 44 },
  attemptCard: { flexDirection: "row", gap: spacing.md, alignItems: "flex-start", marginTop: spacing.sm },
  cardBody: { flex: 1 },
  thumb: { width: 64, height: 64, borderRadius: radius.sm },
  thumbPlaceholder: { width: 64, height: 64, borderRadius: radius.sm },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: spacing.xs, marginTop: spacing.sm },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
    borderRadius: radius.pill,
  },
  badgeText: { fontSize: 12, fontWeight: "700" },
});
