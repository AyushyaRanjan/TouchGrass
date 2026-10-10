/** One place for the calm palette, spacing and type used by every screen. */

export const colors = {
  background: "#F5FAF5", // soft green-tinted off-white
  card: "#FFFFFF",
  border: "#DEEAE0",
  text: "#20302A",
  textMuted: "#6B7B72",
  accent: "#3E8E5A", // calm grass green
  accentDark: "#2F6B45",
  accentSoft: "#E3F1E7",
  onAccent: "#FFFFFF",
  danger: "#B3514C",
  dangerSoft: "#FBE9E8",
  warning: "#A8821F",
  warningSoft: "#FBF3DC",
  neutral: "#7A8680",
  neutralSoft: "#ECF1EE",
} as const;

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
} as const;

export const radius = {
  sm: 10,
  md: 16,
  lg: 22,
  pill: 999,
} as const;

export const type = {
  title: { fontSize: 30, lineHeight: 38, fontWeight: "700", color: colors.text },
  heading: { fontSize: 20, lineHeight: 26, fontWeight: "700", color: colors.text },
  body: { fontSize: 16, lineHeight: 24, color: colors.text },
  small: { fontSize: 13, lineHeight: 18, color: colors.textMuted },
  label: {
    fontSize: 12,
    lineHeight: 16,
    fontWeight: "600",
    letterSpacing: 1.4,
    color: colors.textMuted,
    textTransform: "uppercase",
  },
} as const;

/** Content columns never stretch wider than this, even on tablets. */
export const maxContentWidth = 560;