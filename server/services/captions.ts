import { createTikTokStyleCaptions, type Caption } from "@remotion/captions";
import {
  coerceSubtitleStyle,
  defaultSubtitleStyle,
  type SubtitleStyle,
} from "../../shared/subtitleStyles";

export interface AssCaptionToken {
  startMs: number;
  endMs: number;
  text: string;
}

export type TikTokCaptionPage = ReturnType<
  typeof createTikTokStyleCaptions
>["pages"][number];

const parseAssTimestampToMs = (input: string): number => {
  const [hours, minutes, secondsWithCs] = input.split(":");
  const [seconds, centiseconds = "0"] = secondsWithCs.split(".");
  return (
    Number(hours) * 3600 * 1000 +
    Number(minutes) * 60 * 1000 +
    Number(seconds) * 1000 +
    Number(centiseconds.padEnd(2, "0").slice(0, 2)) * 10
  );
};

const cleanAssText = (input: string): string => {
  return input.replace(/\\N/g, " ").replace(/\{[^}]*\}/g, "").trim();
};

export const parseAssToTokens = (assContent: string): AssCaptionToken[] => {
  const lines = assContent.split("\n");
  const tokens: AssCaptionToken[] = [];

  for (const line of lines) {
    if (!line.startsWith("Dialogue:")) {
      continue;
    }

    const parts = line.split(",");
    if (parts.length < 10) {
      continue;
    }

    const startMs = parseAssTimestampToMs(parts[1]);
    const endMs = parseAssTimestampToMs(parts[2]);
    const text = cleanAssText(parts.slice(9).join(","));

    if (!text || endMs <= startMs) {
      continue;
    }

    tokens.push({ startMs, endMs, text });
  }

  return tokens.sort((a, b) => a.startMs - b.startMs);
};

export const parseAssToCaptions = (assContent: string): Caption[] => {
  return parseAssToTokens(assContent).map((token) => {
    const midpointMs = token.startMs + (token.endMs - token.startMs) / 2;
    return {
      text: token.text,
      startMs: token.startMs,
      endMs: token.endMs,
      timestampMs: midpointMs,
      confidence: null,
    };
  });
};

export const normalizeSubtitleStyle = (input: unknown): SubtitleStyle => {
  return coerceSubtitleStyle(input ?? defaultSubtitleStyle);
};

export const isTikTokSubtitleStyle = (style: SubtitleStyle): boolean => {
  return true;
};

const getCombineWindowForStyle = (style: SubtitleStyle): number => {
  return 1100;
};

export const createTikTokPages = ({
  captions,
  style,
}: {
  captions: Caption[];
  style: SubtitleStyle;
}) => {
  return createTikTokStyleCaptions({
    captions,
    combineTokensWithinMilliseconds: getCombineWindowForStyle(style),
  }).pages;
};

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
}

export const wordTimestampsToCaptions = (
  words: WordTimestamp[],
): Caption[] => {
  return words
    .filter((w) => w.word && w.end > w.start)
    .map((w) => ({
      text: w.word,
      startMs: Math.round(w.start * 1000),
      endMs: Math.round(w.end * 1000),
      timestampMs: Math.round(((w.start + w.end) / 2) * 1000),
      confidence: null,
    }));
};
