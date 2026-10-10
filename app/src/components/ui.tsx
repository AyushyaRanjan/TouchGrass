/** Shared, calm UI building blocks used by all four screens. */

import { Ionicons } from "@expo/vector-icons";
import { type ReactNode } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import type { TextInputProps, ViewStyle } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors, maxContentWidth, radius, spacing, type } from "@/theme";
import type { Verdict } from "@/api/types";
import { countsForDate } from "@/lib/day";

export function Screen({
  children,
  style,
  scroll = true,
}: {
  children: ReactNode;
  style?: ViewStyle;
  /** Set false when the screen renders its own scrollable (e.g. a FlatList). */
  scroll?: boolean;
}) {
  const insets = useSafeAreaInsets();
  if (!scroll) {
    return (
      <View style={[styles.screen, { backgroundColor: colors.background }, style]}>
        <View
          style={[
            styles.screenColumn,
            {
              flex: 1,
              alignSelf: "center",
              paddingTop: insets.top + spacing.lg,
              paddingBottom: insets.bottom + spacing.xl,
            },
          ]}
        >
          {children}
        </View>
      </View>
    );
  }
  return (
    <View style={[styles.screen, { backgroundColor: colors.background }, style]}>
      <ScrollView
        contentContainerStyle={[
          styles.screenScroll,
          { paddingTop: insets.top + spacing.lg, paddingBottom: insets.bottom + spacing.xl },
        ]}
      >
        <View style={styles.screenColumn}>{children}</View>
      </ScrollView>
    </View>
  );
}

export function LoadingView({ message = "Loading…" }: { message?: string }) {
  return (
    <View style={styles.centeredFill}>
      <ActivityIndicator size="large" color={colors.accent} />
      <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.md }]}>{message}</Text>
    </View>
  );
}

export function ErrorCard({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <View style={[styles.card, styles.errorCard]}>
      <Ionicons name="cloud-offline-outline" size={26} color={colors.danger} />
      <Text style={[type.heading, { color: colors.danger, marginTop: spacing.sm }]}>{title}</Text>
      <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.xs }]}>{message}</Text>
      {onRetry ? (
        <Pressable onPress={onRetry} style={({ pressed }) => [styles.textAction, pressed && styles.pressed]}>
          <Ionicons name="refresh" size={16} color={colors.accentDark} style={{ marginTop: 1 }} />
          <Text style={styles.textActionLabel}>Try again</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function EmptyState({
  icon,
  title,
  message,
  actionLabel,
  onAction,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  message: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <View style={[styles.card, styles.emptyCard]}>
      <Ionicons name={icon} size={34} color={colors.accent} />
      <Text style={[type.heading, { marginTop: spacing.md }]}>{title}</Text>
      <Text style={[type.body, { color: colors.textMuted, marginTop: spacing.xs, textAlign: "center" }]}>
        {message}
      </Text>
      {actionLabel && onAction ? (
        <Pressable onPress={onAction} style={({ pressed }) => [styles.textAction, pressed && styles.pressed]}>
          <Text style={styles.textActionLabel}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

const CHIP_TONES: Record<string, { bg: string; text: string }> = {
  success: { bg: colors.accentSoft, text: colors.accentDark },
  danger: { bg: colors.dangerSoft, text: colors.danger },
  warning: { bg: colors.warningSoft, text: colors.warning },
  neutral: { bg: colors.neutralSoft, text: colors.neutral },
};

export function Chip({ label, tone = "neutral" }: { label: string; tone?: string }) {
  const palette = CHIP_TONES[tone] ?? CHIP_TONES.neutral;
  return (
    <View style={[styles.chip, { backgroundColor: palette.bg }]}>
      <Text style={[type.label, { color: palette.text, textTransform: "uppercase" }]}>{label}</Text>
    </View>
  );
}

const VERDICT_TONE: Record<Verdict, string> = {
  pass: "success",
  fail: "danger",
  unsure: "warning",
  retake: "neutral",
  rejected: "danger",
};

export function VerdictChip({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return null;
  return <Chip label={verdict} tone={VERDICT_TONE[verdict]} />;
}

export function Field({
  label,
  placeholder,
  value,
  onChangeText,
  secureTextEntry,
  keyboardType,
  autoCapitalize = "none",
  autoCorrect = false,
  right,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChangeText: (text: string) => void;
  secureTextEntry?: boolean;
  keyboardType?: TextInputProps["keyboardType"];
  autoCapitalize?: TextInputProps["autoCapitalize"];
  autoCorrect?: boolean;
  right?: ReactNode;
}) {
  return (
    <View>
      <Text style={[type.label, { marginBottom: spacing.xs }]}>{label}</Text>
      <View style={styles.fieldRow}>
        <TextInput
          style={[type.body, styles.fieldInput]}
          placeholder={placeholder}
          placeholderTextColor={colors.neutral}
          value={value}
          onChangeText={onChangeText}
          secureTextEntry={secureTextEntry}
          keyboardType={keyboardType}
          autoCapitalize={autoCapitalize}
          autoCorrect={autoCorrect}
        />
        {right}
      </View>
    </View>
  );
}

export function BigButton({
  label,
  sublabel,
  onPress,
  disabled = false,
}: {
  label: string;
  sublabel?: string;
  onPress?: () => void;
  disabled?: boolean;
}) {
  return (
    <Pressable
      onPress={disabled ? undefined : onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.bigButton,
        disabled ? styles.bigButtonDisabled : pressed && styles.pressed,
      ]}
    >
      <Text style={[styles.bigButtonLabel, disabled && styles.bigButtonLabelDisabled]}>{label}</Text>
      {sublabel ? (
        <Text style={[styles.bigButtonSub, disabled && styles.bigButtonSubDisabled]}>{sublabel}</Text>
      ) : null}
    </Pressable>
  );
}

export function TextAction({
  label,
  onPress,
  disabled = false,
  busy = false,
}: {
  label: string;
  onPress?: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  return (
    <Pressable
      onPress={disabled || busy ? undefined : onPress}
      disabled={disabled || busy}
      hitSlop={8}
      style={({ pressed }) => [styles.textAction, pressed && styles.pressed]}
    >
      {busy ? <ActivityIndicator size="small" color={colors.accentDark} /> : (
        <Text style={[styles.textActionLabel, disabled && styles.textActionDisabled]}>{label}</Text>
      )}
    </Pressable>
  );
}

/** Small muted note shown when a screen displays cached data because the server is unreachable. */
export function StaleLabel({ text }: { text: string }) {
  return <Text style={styles.staleLabel}>{text}</Text>;
}

export function DifficultyDots({ difficulty }: { difficulty: number }) {
  return (
    <View
      style={styles.dotsRow}
      accessible
      accessibilityLabel={`Difficulty ${difficulty} of 3`}
    >
      {[1, 2, 3].map((n) => (
        <View
          key={n}
          style={[styles.dot, { backgroundColor: n <= difficulty ? colors.accent : colors.border }]}
        />
      ))}
    </View>
  );
}

/** A photo waiting to upload. The date is the capture's local day, which is the day it counts for. */
export function QueuedItemRow({ title, capturedAt }: { title: string; capturedAt: string }) {
  return (
    <View style={styles.queuedRow}>
      <Ionicons name="cloud-upload-outline" size={18} color={colors.textMuted} />
      <View style={{ flex: 1 }}>
        <Text style={type.body} numberOfLines={1}>{title}</Text>
        <Text style={type.small}>Counts for {countsForDate(capturedAt)}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  screenScroll: { alignItems: "center" },
  screenColumn: { width: "100%", maxWidth: maxContentWidth, paddingHorizontal: spacing.lg, gap: spacing.lg },
  centeredFill: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background, padding: spacing.xl },
  card: {
    backgroundColor: colors.card,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    padding: spacing.lg,
    width: "100%",
  },
  errorCard: { alignItems: "flex-start", gap: spacing.xs },
  emptyCard: { alignItems: "center", gap: spacing.xs },
  chip: { alignSelf: "flex-start", paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderRadius: radius.pill },
  textAction: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.xs, paddingVertical: spacing.sm, minHeight: 44 },
  staleLabel: { fontSize: 13, color: colors.textMuted },
  textActionLabel: { color: colors.accentDark, fontSize: 16, fontWeight: "600" },
  textActionDisabled: { color: colors.neutral },
  pressed: { opacity: 0.7 },
  fieldRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.background,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
  },
  fieldInput: { flex: 1, paddingVertical: spacing.md, color: colors.text },
  bigButton: {
    backgroundColor: colors.accent,
    borderRadius: radius.lg,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 64,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  bigButtonDisabled: { backgroundColor: colors.neutralSoft },
  bigButtonLabel: { color: colors.onAccent, fontSize: 18, fontWeight: "700" },
  bigButtonLabelDisabled: { color: colors.neutral },
  bigButtonSub: { color: colors.onAccent, fontSize: 13, marginTop: 2, opacity: 0.9 },
  bigButtonSubDisabled: { color: colors.textMuted },
  dotsRow: { flexDirection: "row", alignItems: "center", gap: 5 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  queuedRow: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingVertical: spacing.sm },
});