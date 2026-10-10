/** The verdict screen for one submission, built from the local (synced) item. */

import { Ionicons } from "@expo/vector-icons";
import { useEffect, useState } from "react";
import { Alert, Text, View } from "react-native";

import { ApiError } from "@/api/client";
import type { Stats } from "@/api/types";
import { BigButton, Card, Screen, TextAction, VerdictChip } from "@/components/ui";
import { updatePending, type PendingSubmission } from "@/db/local";
import { useSettings } from "@/hooks/use-settings";
import { loadCachedStats } from "@/sync/engine";
import { useSync } from "@/sync/provider";
import { colors, spacing, type } from "@/theme";

export function ResultCard({
  item,
  onRetake,
  onDone,
}: {
  item: PendingSubmission;
  onRetake: () => void;
  onDone: () => void;
}) {
  const { api } = useSettings();
  const { online, syncNow } = useSync();
  const [honoredXp, setHonoredXp] = useState<number | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [busy, setBusy] = useState(false);

  const honorQueued = item.honor_queued;
  const passedOrHonored = item.verdict === "pass" || honoredXp !== null || honorQueued;
  useEffect(() => {
    if (passedOrHonored) void loadCachedStats().then(setStats);
  }, [passedOrHonored, item.status]);

  const honor = async () => {
    setBusy(true);
    try {
      if (!online || item.server_submission_id === null) throw new ApiError("offline");
      const res = await api.honor(item.server_submission_id);
      setHonoredXp(res.xp_awarded);
      await updatePending(item.client_submission_id, { honor_available: false });
      void syncNow(true); // refresh cached tasks/stats so Today shows it as done
    } catch (err) {
      if (err instanceof ApiError && !err.isNetworkError && err.message !== "offline") {
        Alert.alert("Couldn't mark it done", err.message);
      } else {
        // Offline: queue it; the sync engine sends it later.
        await updatePending(item.client_submission_id, { honor_queued: true });
      }
    } finally {
      setBusy(false);
    }
  };

  const confirmHonor = () =>
    Alert.alert(
      "Mark done anyway?",
      "This counts the task as done for half the XP, on the honor system. You can only do this once per task.",
      [
        { text: "Cancel", style: "cancel" },
        { text: "Mark done", onPress: () => void honor() },
      ],
    );

  if (honoredXp !== null || honorQueued) {
    return (
      <Screen>
        <Card>
          <Text style={type.heading}>Marked done</Text>
          <Text style={[type.body, { marginTop: spacing.xs }]}>
            {honoredXp !== null
              ? `+${honoredXp} XP. Now go enjoy the outdoors.`
              : "I'll send this when you're back online. Now go enjoy the outdoors."}
          </Text>
          <StreakLine stats={stats} />
        </Card>
        <BigButton label="Back to Today" onPress={onDone} />
      </Screen>
    );
  }

  if (item.status === "failed") {
    return (
      <Screen>
        <Card>
          <Text style={type.heading}>This photo can&apos;t be used</Text>
          <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.xs }]}>
            {item.last_error ?? "The server couldn't check the photo."}
          </Text>
        </Card>
        <BigButton label="Retake" onPress={onRetake} />
        <TextAction label="Close" onPress={onDone} />
      </Screen>
    );
  }

  if (item.verdict === "pass") {
    return (
      <Screen>
        <Card>
          <Ionicons name="leaf" size={32} color={colors.accent} />
          <Text style={[type.heading, { marginTop: spacing.md }]}>Nice work. Grass touched.</Text>
          <StreakLine stats={stats} />
        </Card>
        <BigButton label="Back to Today" onPress={onDone} />
      </Screen>
    );
  }

  const heading =
    item.verdict === "unsure"
      ? "Couldn't tell"
      : item.verdict === "retake"
        ? "Let's try that photo again"
        : item.verdict === "rejected"
          ? "Looks like an earlier photo"
          : "Not quite";
  const isBad = item.verdict === "fail" || item.verdict === "rejected";
  const body =
    item.verdict === "rejected"
      ? "This matched a photo you already used for another task. Take a fresh one for today."
      : item.hint;

  return (
    <Screen>
      <Card>
        <VerdictChip verdict={item.verdict} />
        <Text style={[type.heading, { marginTop: spacing.md }, isBad && { color: colors.danger }]}>{heading}</Text>
        {body ? <Text style={[type.body, { marginTop: spacing.xs }]}>{body}</Text> : null}
        {item.evidence.length > 0 ? (
          <View style={{ marginTop: spacing.md, gap: spacing.xs }}>
            {item.evidence.map((e) => (
              <Text key={e.id} style={type.small}>
                {e.answer === "yes" ? "Yes" : e.answer === "no" ? "No" : "Unclear"}: {e.evidence}
              </Text>
            ))}
          </View>
        ) : null}
      </Card>
      <BigButton label="Retake" onPress={onRetake} />
      {item.honor_available ? (
        <TextAction label="Mark done anyway (half XP)" busy={busy} onPress={confirmHonor} />
      ) : null}
      <TextAction label="Close" onPress={onDone} />
    </Screen>
  );
}

function StreakLine({ stats }: { stats: Stats | null }) {
  if (!stats) return null;
  return (
    <Text style={[type.small, { marginTop: spacing.sm }]}>
      {stats.streak_current}-day streak · {stats.xp} XP total
    </Text>
  );
}
