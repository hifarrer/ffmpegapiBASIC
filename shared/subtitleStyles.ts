export const subtitleStyles = [
  "plain-white",
  "yellow-bg",
  "pink-bg",
  "blue-bg",
  "red-bg",
] as const;

export type SubtitleStyle = (typeof subtitleStyles)[number];

export const defaultSubtitleStyle: SubtitleStyle = "plain-white";

export const isSubtitleStyle = (value: unknown): value is SubtitleStyle => {
  return (
    typeof value === "string" &&
    subtitleStyles.includes(value as SubtitleStyle)
  );
};

export const coerceSubtitleStyle = (value: unknown): SubtitleStyle => {
  if (isSubtitleStyle(value)) {
    return value;
  }
  return defaultSubtitleStyle;
};
