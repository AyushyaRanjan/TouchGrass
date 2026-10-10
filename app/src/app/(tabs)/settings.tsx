import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { ApiError, type AppSettings, type TouchGrassClient } from "@/api/client";
import {
  DEFAULT_PREFERENCES,
  type Category,
  type CategoryWeight,
  type Preferences,
} from "@/api/types";
import { BigButton, Card, Field, LoadingView, Screen, TextAction } from "@/components/ui";
import { useSettings } from "@/hooks/use-settings";
import { PREFERENCES_CACHE_KEY, readCache, writeCache } from "@/lib/cache";
import { useSync } from "@/sync/provider";
import { colors, radius, spacing, type } from "@/theme";

type TestResult = { ok: true; model: string } | { ok: false; message: string };

const CATEGORY_COPY: { id: Category; label: string; blurb: string }[] = [
  { id: "nature", label: "Nature", blurb: "Trees, sky, plants, weather." },
  { id: "movement", label: "Movement", blurb: "Walk, climb, or stretch outside." },
  { id: "exploration", label: "Exploration", blurb: "New streets, views, corners of town." },
  { id: "social", label: "Social", blurb: "Brief, kind contact with people nearby." },
  { id: "creative", label: "Creative", blurb: "Make something out in the open." },
  { id: "community", label: "Community", blurb: "Leave a place better than you found it." },
];

const WEIGHT_OPTIONS: { value: CategoryWeight; label: string }[] = [
  { value: "often", label: "Often" },
  { value: "normal", label: "Normal" },
  { value: "rarely", label: "Rarely" },
  { value: "off", label: "Never" },
];

const COMFORT_OPTIONS: { value: 1 | 2 | 3; label: string }[] = [
  { value: 1, label: "Gentle" },
  { value: 2, label: "Medium" },
  { value: 3, label: "Bold" },
];

const MINUTES_OPTIONS: { value: 15 | 30 | 60 | null; label: string }[] = [
  { value: 15, label: "15 min" },
  { value: 30, label: "30 min" },
  { value: 60, label: "60 min" },
  { value: null, label: "Any" },
];

export default function SettingsScreen() {
  const { settings, ready, save, api } = useSettings();
  if (!ready) return <LoadingView message="Loading settings…" />;
  return <SettingsForm initial={settings} save={save} api={api} />;
}

function SettingsForm({
  initial,
  save,
  api,
}: {
  initial: AppSettings;
  save: (next: AppSettings) => Promise<void>;
  api: TouchGrassClient;
}) {
  const { online } = useSync();
  const [server1, setServer1] = useState(initial.server1);
  const [server2, setServer2] = useState(initial.server2);
  const [token, setToken] = useState(initial.token);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<TestResult | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);
  const [prefs, setPrefs] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [connectionOpen, setConnectionOpen] = useState(initial.server1.trim().length === 0);
  const [busyPrefs, setBusyPrefs] = useState(false);

  const loadPrefs = useCallback(async () => {
    try {
      const fresh = await api.getPreferences();
      setPrefs(fresh);
      await writeCache(PREFERENCES_CACHE_KEY, fresh);
    } catch {
      const cached = await readCache<Preferences>(PREFERENCES_CACHE_KEY);
      if (cached) setPrefs(cached.data);
    }
  }, [api]);

  useFocusEffect(
    useCallback(() => {
      void loadPrefs();
    }, [loadPrefs]),
  );

  const applyPatch = async (patch: Parameters<TouchGrassClient["putPreferences"]>[0]) => {
    if (!online) return;
    setBusyPrefs(true);
    try {
      const next = await api.putPreferences(patch);
      setPrefs(next);
      await writeCache(PREFERENCES_CACHE_KEY, next);
    } catch (err) {
      Alert.alert("Couldn't save preferences", err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusyPrefs(false);
    }
  };

  const confirmReset = () => {
    Alert.alert("Reset to defaults?", "Category weights, social comfort and longest task go back to the defaults.", [
      { text: "Keep mine", style: "cancel" },
      {
        text: "Reset",
        style: "destructive",
        onPress: () => {
          setBusyPrefs(true);
          api
            .resetPreferences()
            .then(async (next) => {
              setPrefs(next);
              await writeCache(PREFERENCES_CACHE_KEY, next);
            })
            .catch((err) => {
              Alert.alert("Couldn't reset", err instanceof ApiError ? err.message : String(err));
            })
            .finally(() => setBusyPrefs(false));
        },
      },
    ]);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await save({ server1, server2, token });
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      await handleSave();
      setTest(await api.testConnection({ server1, server2, token }));
    } finally {
      setTesting(false);
    }
  };

  const locked = !online || busyPrefs;

  return (
    <Screen>
      <Text style={type.title}>Settings</Text>

      <Card>
        <Text style={type.label}>Your tasks</Text>
        <Text style={[type.small, { marginTop: spacing.xs }]}>
          Changes apply from tomorrow. A reroll uses them right away.
        </Text>
        {!online ? (
          <Text style={[type.small, { marginTop: spacing.sm, color: colors.warning }]}>
            Connect to your server to change these.
          </Text>
        ) : null}

        {CATEGORY_COPY.map((cat) => (
          <View key={cat.id} style={styles.prefBlock}>
            <Text style={type.body}>{cat.label}</Text>
            <Text style={type.small}>{cat.blurb}</Text>
            <Segmented
              options={WEIGHT_OPTIONS}
              value={prefs.category_weights[cat.id]}
              disabled={locked}
              onChange={(weight) => void applyPatch({ category_weights: { [cat.id]: weight } })}
            />
          </View>
        ))}

        <View style={styles.prefBlock}>
          <Text style={type.body}>Social comfort</Text>
          <Text style={type.small}>How outgoing a social task can be.</Text>
          <Segmented
            options={COMFORT_OPTIONS}
            value={prefs.social_comfort_max}
            disabled={locked}
            onChange={(comfort) => void applyPatch({ social_comfort_max: comfort })}
          />
        </View>

        <View style={styles.prefBlock}>
          <Text style={type.body}>Longest task</Text>
          <Text style={type.small}>Skip anything estimated longer than this.</Text>
          <Segmented
            options={MINUTES_OPTIONS}
            value={prefs.max_minutes}
            disabled={locked}
            onChange={(minutes) => void applyPatch({ max_minutes: minutes })}
          />
        </View>

        <TextAction label="Reset to defaults" disabled={locked} onPress={confirmReset} />
      </Card>

      <Pressable
        onPress={() => setConnectionOpen((open) => !open)}
        hitSlop={8}
        accessibilityRole="button"
        style={({ pressed }) => [styles.connectionHeader, pressed && { opacity: 0.7 }]}
      >
        <Text style={type.heading}>Connection</Text>
        <Ionicons name={connectionOpen ? "chevron-up" : "chevron-down"} size={20} color={colors.textMuted} />
      </Pressable>

      {connectionOpen ? (
        <>
          <Card>
            <Text style={[type.body, { color: colors.textMuted }]}>
              Point the app at your server — a laptop reachable from your phone, e.g. over Tailscale or your home Wi-Fi.
              The URL includes the port: <Text style={styles.mono}>http://100.x.x.x:8000</Text>.
            </Text>
          </Card>

          <Card style={styles.form}>
            <Field
              label="Server address (main)"
              placeholder="http://100.24.12.8:8000"
              value={server1}
              onChangeText={(text) => {
                setServer1(text);
                setTest(null);
              }}
              keyboardType="url"
            />
            <Field
              label="Server address (backup, optional)"
              placeholder="http://192.168.1.20:8000"
              value={server2}
              onChangeText={(text) => {
                setServer2(text);
                setTest(null);
              }}
              keyboardType="url"
            />
            <Field
              label="API token"
              placeholder="change-me"
              value={token}
              onChangeText={(text) => {
                setToken(text);
                setTest(null);
              }}
              secureTextEntry
            />

            {savedFlash ? (
              <View style={styles.savedRow}>
                <Ionicons name="checkmark-circle" size={16} color={colors.accentDark} />
                <Text style={[type.small, { color: colors.accentDark, fontWeight: "700" }]}>Saved</Text>
              </View>
            ) : null}

            <BigButton label="Save settings" onPress={() => void handleSave()} disabled={saving} />
            <TextAction label="Test connection" busy={testing} disabled={testing} onPress={() => void handleTest()} />

            {test ? (
              test.ok ? (
                <View style={[styles.testBox, { backgroundColor: colors.accentSoft }]}>
                  <Ionicons name="checkmark-circle" size={18} color={colors.accentDark} />
                  <Text style={[type.small, { color: colors.accentDark, flex: 1 }]}>Connected to {test.model}</Text>
                </View>
              ) : (
                <View style={[styles.testBox, { backgroundColor: colors.dangerSoft }]}>
                  <Ionicons name="warning" size={18} color={colors.danger} />
                  <Text style={[type.small, { color: colors.danger, flex: 1 }]}>{test.message}</Text>
                </View>
              )
            ) : null}
          </Card>
        </>
      ) : null}

      <Card>
        <Text style={type.label}>About</Text>
        <Text style={[type.small, { marginTop: spacing.xs }]}>
          Touch Grass gives you one outdoor task a day. Your photos never leave your own server.
        </Text>
      </Card>
    </Screen>
  );
}

function Segmented<T extends string | number | null>({
  options,
  value,
  onChange,
  disabled,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  disabled: boolean;
}) {
  return (
    <View style={styles.segmentRow}>
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={String(option.value)}
            onPress={() => !disabled && onChange(option.value)}
            disabled={disabled}
            hitSlop={4}
            style={[
              styles.segment,
              selected && styles.segmentSelected,
              disabled && !selected && { opacity: 0.6 },
            ]}
          >
            <Text style={[styles.segmentLabel, selected && styles.segmentLabelSelected]}>{option.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  form: { gap: spacing.md },
  mono: { fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  savedRow: { flexDirection: "row", alignItems: "center", gap: spacing.xs },
  testBox: { flexDirection: "row", alignItems: "center", gap: spacing.sm, borderRadius: radius.sm, padding: spacing.md },
  prefBlock: { marginTop: spacing.md, gap: spacing.xs },
  connectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    minHeight: 44,
  },
  segmentRow: { flexDirection: "row", gap: 4, marginTop: spacing.xs },
  segment: {
    flex: 1,
    minHeight: 44,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.border,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
    backgroundColor: colors.card,
  },
  segmentSelected: { backgroundColor: colors.accentSoft, borderColor: colors.accent },
  segmentLabel: { fontSize: 12, fontWeight: "600", color: colors.textMuted, textAlign: "center" },
  segmentLabelSelected: { color: colors.accentDark },
});
