import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import type { Category, Stats } from "@/api/types";
import { Card, ErrorCard, LoadingView, Screen, StaleLabel } from "@/components/ui";
import { useFetch } from "@/hooks/use-fetch";
import { useSettings } from "@/hooks/use-settings";
import { STATS_CACHE_KEY, staleText } from "@/lib/cache";
import { formatDuration, usageSevenDayAverage, usageToday } from "@/lib/usage";
import { useSync } from "@/sync/provider";
import { colors, radius, spacing, type } from "@/theme";

const CATEGORY_LABELS: Record<Category, string> = {
  nature: "Nature",
  movement: "Movement",
  exploration: "Exploration",
  social: "Social",
  creative: "Creative",
  community: "Community",
};

const DAY_CELLS = 30;

export default function StatsScreen() {
  const { api } = useSettings();
  const { online } = useSync();
  const fetch = useFetch(() => api.stats(), { cacheKey: STATS_CACHE_KEY });
  const { load } = fetch;
  const [todaySeconds, setTodaySeconds] = useState(0);
  const [avgSeconds, setAvgSeconds] = useState(0);

  const loadUsage = useCallback(async () => {
    const [today, avg] = await Promise.all([usageToday(), usageSevenDayAverage()]);
    setTodaySeconds(today);
    setAvgSeconds(avg);
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load();
      void loadUsage();
    }, [load, loadUsage]),
  );

  if (fetch.loading && !fetch.data) return <LoadingView message="Crunching the numbers…" />;

  if (fetch.error && !fetch.data) {
    return (
      <Screen>
        <Text style={type.title}>Stats</Text>
        <UsageCard todaySeconds={todaySeconds} avgSeconds={avgSeconds} />
        <ErrorCard title="Couldn't load stats" message={fetch.error.message} onRetry={() => void fetch.load()} />
      </Screen>
    );
  }

  const stats: Stats = fetch.data ?? {
    xp: 0,
    level: 1,
    streak_current: 0,
    streak_longest: 0,
    tasks_this_month: 0,
    completed_today: false,
    category_counts_30d: {},
    last_30_days: [],
  };
  const showStale = (fetch.stale || !online) && fetch.updatedAt;

  return (
    <Screen>
      <Text style={type.title}>Stats</Text>
      {showStale && fetch.updatedAt ? <StaleLabel text={staleText(fetch.updatedAt)} /> : null}

      <UsageCard todaySeconds={todaySeconds} avgSeconds={avgSeconds} />

      <LevelCard xp={stats.xp} level={stats.level} />
      <SummaryGrid stats={stats} />
      <CategoryBars counts={stats.category_counts_30d} />
      <CalendarStrip days={stats.last_30_days} />
    </Screen>
  );
}

function UsageCard({ todaySeconds, avgSeconds }: { todaySeconds: number; avgSeconds: number }) {
  return (
    <Card>
      <Text style={type.label}>Time in app</Text>
      <Text style={[type.heading, { marginTop: spacing.sm }]}>
        Time in app today: {formatDuration(todaySeconds)}
      </Text>
      <Text style={[type.body, { marginTop: spacing.xs }]}>
        7-day average: {formatDuration(Math.round(avgSeconds))}
      </Text>
      <Text style={[type.small, { marginTop: spacing.sm }]}>
        Honesty metric: the screen should be the shortest part of the experience.
      </Text>
    </Card>
  );
}

function LevelCard({ xp, level }: { xp: number; level: number }) {
  const into = xp % 100;
  return (
    <Card>
      <Text style={type.label}>Level {level}</Text>
      <View style={[styles.barTrack, { marginTop: spacing.md }]}>
        <View style={[styles.barFill, { width: `${into}%` }]} />
      </View>
      <Text style={[type.small, { marginTop: spacing.sm }]}>
        {into} / 100 XP to level {level + 1}
      </Text>
    </Card>
  );
}

function SummaryGrid({ stats }: { stats: Stats }) {
  const cells = [
    { label: "XP", value: String(stats.xp) },
    { label: "Level", value: String(stats.level) },
    { label: "Current streak", value: `${stats.streak_current}d` },
    { label: "Best streak", value: `${stats.streak_longest}d` },
    { label: "This month", value: String(stats.tasks_this_month) },
  ];
  return (
    <View style={styles.grid}>
      {cells.map((cell) => (
        <Card key={cell.label} style={styles.gridCell}>
          <Text style={styles.gridValue}>{cell.value}</Text>
          <Text style={type.small}>{cell.label}</Text>
        </Card>
      ))}
    </View>
  );
}

function CategoryBars({ counts }: { counts: Partial<Record<Category, number>> }) {
  const entries = (Object.keys(counts) as Category[])
    .map((category) => ({ category, count: counts[category] ?? 0 }))
    .sort((a, b) => b.count - a.count);

  if (entries.length === 0) return null;
  const max = Math.max(1, ...entries.map((entry) => entry.count));

  return (
    <Card>
      <Text style={type.label}>Last 30 days by category</Text>
      <View style={styles.barList}>
        {entries.map(({ category, count }) => (
          <View key={category} style={styles.barRow}>
            <Text style={[type.small, styles.barLabel]}>{CATEGORY_LABELS[category]}</Text>
            <View style={styles.barTrack}>
              <View style={[styles.barFill, { width: `${Math.round((count / max) * 100)}%` }]} />
            </View>
            <Text style={[type.small, styles.barCount]}>{count}</Text>
          </View>
        ))}
      </View>
    </Card>
  );
}

function CalendarStrip({ days }: { days: Stats["last_30_days"] }) {
  const cells = days.slice(-DAY_CELLS); // oldest to newest, left to right
  return (
    <Card>
      <Text style={type.label}>Last {DAY_CELLS} days</Text>
      <View style={styles.calendar}>
        {cells.map((day) => (
          <View
            key={day.date}
            style={[
              styles.dayCell,
              day.completed ? styles.dayDone : styles.dayMissed,
            ]}
          />
        ))}
      </View>
      <View style={styles.legend}>
        <View style={[styles.dayCell, styles.dayDone]} />
        <Text style={type.small}>completed</Text>
        <View style={[styles.dayCell, styles.dayMissed, { marginLeft: spacing.md }]} />
        <Text style={type.small}>skipped</Text>
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  grid: { flexDirection: "row", flexWrap: "wrap", gap: spacing.md },
  gridCell: { width: "46%", flexGrow: 1, alignItems: "flex-start" },
  gridValue: { fontSize: 28, fontWeight: "800", color: colors.accentDark },
  barList: { gap: spacing.sm, marginTop: spacing.md },
  barRow: { flexDirection: "row", alignItems: "center", gap: spacing.sm },
  barLabel: { width: 84 },
  barTrack: { flex: 1, height: 10, borderRadius: radius.pill, backgroundColor: colors.neutralSoft, overflow: "hidden" },
  barFill: { height: "100%", borderRadius: radius.pill, backgroundColor: colors.accent },
  barCount: { width: 28, textAlign: "right", fontWeight: "700" },
  calendar: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: spacing.md },
  dayCell: { width: 12, height: 12, borderRadius: 4 },
  dayDone: { backgroundColor: colors.accent },
  dayMissed: { backgroundColor: colors.neutralSoft },
  legend: { flexDirection: "row", alignItems: "center", gap: spacing.xs, marginTop: spacing.md },
});