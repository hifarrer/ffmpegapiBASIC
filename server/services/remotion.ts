import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import {
  normalizeSubtitleStyle,
  isTikTokSubtitleStyle,
  parseAssToCaptions,
  createTikTokPages,
  wordTimestampsToCaptions,
  textLinesToPages,
  type WordTimestamp,
} from "./captions";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let cachedServeUrl: string | null = null;

const setupLibraryPath = () => {
  if (process.platform === "linux") {
    const currentLdPath = process.env.LD_LIBRARY_PATH || "";
    const commonLibPaths = [
      "/usr/lib",
      "/usr/lib/x86_64-linux-gnu",
      "/lib/x86_64-linux-gnu",
      "/lib64",
    ];

    const libPaths = commonLibPaths.filter(Boolean).join(":");

    if (currentLdPath && !currentLdPath.includes("/nix/store")) {
      if (!currentLdPath.includes(libPaths)) {
        process.env.LD_LIBRARY_PATH = `${currentLdPath}:${libPaths}`;
      }
    } else if (!currentLdPath) {
      process.env.LD_LIBRARY_PATH = libPaths;
    }
  }
};

setupLibraryPath();

const getCaptionResolution = (
  aspectRatio: string | undefined,
): { width: number; height: number } => {
  if (aspectRatio === "16:9") {
    return { width: 1920, height: 1080 };
  }
  if (aspectRatio === "4:3") {
    return { width: 1440, height: 1080 };
  }
  if (aspectRatio === "3:4") {
    return { width: 1080, height: 1440 };
  }
  return { width: 1080, height: 1440 };
};

const getServeUrl = async (): Promise<string> => {
  if (cachedServeUrl) {
    return cachedServeUrl;
  }

  const entryPoint = path.resolve(__dirname, "../remotion/Root.tsx");
  cachedServeUrl = await bundle(entryPoint);
  return cachedServeUrl;
};

export const renderVideoWithTikTokCaptions = async ({
  videoSrc,
  assContent,
  subtitleStyle,
  aspectRatio,
  audioDurationSeconds,
}: {
  videoSrc: string;
  assContent: string;
  subtitleStyle: string | null | undefined;
  aspectRatio?: string | null;
  audioDurationSeconds?: number | null;
}): Promise<string | null> => {
  const style = normalizeSubtitleStyle(subtitleStyle);
  if (!isTikTokSubtitleStyle(style)) {
    return null;
  }

  const captions = parseAssToCaptions(assContent);
  if (captions.length === 0) {
    throw new Error("No caption tokens parsed from ASS subtitle content");
  }

  const pages = createTikTokPages({ captions, style });
  const fps = 30;
  const { width, height } = getCaptionResolution(aspectRatio ?? undefined);

  const lastCaptionEndMs = captions.reduce((max, c) => Math.max(max, c.endMs), 0);
  const captionDurationSeconds = lastCaptionEndMs / 1000;

  const baseDuration = audioDurationSeconds && audioDurationSeconds > 0
    ? audioDurationSeconds
    : captionDurationSeconds;

  const estimatedDurationSeconds = Math.max(
    8,
    Math.ceil(baseDuration + 6),
  );
  const durationInFrames = estimatedDurationSeconds * fps;

  const outputLocation = path.join(
    os.tmpdir(),
    `captioned-video-${Date.now()}-${Math.random().toString(36).slice(2)}.mp4`,
  );

  const serveUrl = await getServeUrl();

  const composition = await selectComposition({
    serveUrl,
    id: "CaptionedVideo",
    inputProps: {
      videoSrc,
      pages,
      fps,
      stylePreset: style,
    },
  });

  await renderMedia({
    serveUrl,
    codec: "h264",
    composition: {
      ...composition,
      fps,
      width,
      height,
      durationInFrames,
    },
    inputProps: {
      videoSrc,
      pages,
      fps,
      stylePreset: style,
    },
    outputLocation,
    overwrite: true,
    muted: false,
    imageFormat: "jpeg",
    crf: 18,
  });

  return outputLocation;
};

export const renderVideoWithAutoCaption = async ({
  videoSrc,
  wordTimestamps,
  subtitleStyle,
  aspectRatio,
  audioDurationSeconds,
  position,
}: {
  videoSrc: string;
  wordTimestamps: WordTimestamp[];
  subtitleStyle: string | null | undefined;
  aspectRatio?: string | null;
  audioDurationSeconds?: number | null;
  position?: string | null;
}): Promise<string | null> => {
  const style = normalizeSubtitleStyle(subtitleStyle);
  if (!isTikTokSubtitleStyle(style)) {
    return null;
  }

  const captions = wordTimestampsToCaptions(wordTimestamps);
  if (captions.length === 0) {
    throw new Error("No captions produced from word timestamps");
  }

  const pages = createTikTokPages({ captions, style });
  const fps = 30;
  const { width, height } = getCaptionResolution(aspectRatio ?? undefined);

  const lastCaptionEndMs = captions.reduce((max, c) => Math.max(max, c.endMs), 0);
  const captionDurationSeconds = lastCaptionEndMs / 1000;

  const baseDuration =
    audioDurationSeconds && audioDurationSeconds > 0
      ? audioDurationSeconds
      : captionDurationSeconds;

  const estimatedDurationSeconds = Math.max(8, Math.ceil(baseDuration + 6));
  const durationInFrames = estimatedDurationSeconds * fps;

  const outputLocation = path.join(
    os.tmpdir(),
    `captioned-video-${Date.now()}-${Math.random().toString(36).slice(2)}.mp4`,
  );

  const serveUrl = await getServeUrl();

  const captionPosition = position || "bottom";

  const composition = await selectComposition({
    serveUrl,
    id: "CaptionedVideo",
    inputProps: {
      videoSrc,
      pages,
      fps,
      stylePreset: style,
      position: captionPosition,
    },
  });

  await renderMedia({
    serveUrl,
    codec: "h264",
    composition: {
      ...composition,
      fps,
      width,
      height,
      durationInFrames,
    },
    inputProps: {
      videoSrc,
      pages,
      fps,
      stylePreset: style,
      position: captionPosition,
    },
    outputLocation,
    overwrite: true,
    muted: false,
    imageFormat: "jpeg",
    crf: 18,
  });

  return outputLocation;
};

export const renderVideoWithTextOverlay = async ({
  videoSrc,
  textLines,
  subtitleStyle,
  aspectRatio,
  position,
  durationPerLineMs,
}: {
  videoSrc: string;
  textLines: string[];
  subtitleStyle: string | null | undefined;
  aspectRatio?: string | null;
  position?: string | null;
  durationPerLineMs?: number | null;
}): Promise<string | null> => {
  const style = normalizeSubtitleStyle(subtitleStyle);
  if (!isTikTokSubtitleStyle(style)) {
    return null;
  }

  const perLineMs = durationPerLineMs && durationPerLineMs > 0 ? durationPerLineMs : 5000;
  const pages = textLinesToPages(textLines, perLineMs);
  if (pages.length === 0) {
    throw new Error("No valid text lines provided");
  }

  const fps = 30;
  const { width, height } = getCaptionResolution(aspectRatio ?? undefined);

  const totalDurationMs = pages.length * perLineMs;
  const estimatedDurationSeconds = Math.max(8, Math.ceil(totalDurationMs / 1000 + 2));
  const durationInFrames = estimatedDurationSeconds * fps;

  const outputLocation = path.join(
    os.tmpdir(),
    `text-overlay-${Date.now()}-${Math.random().toString(36).slice(2)}.mp4`,
  );

  const serveUrl = await getServeUrl();
  const captionPosition = position || "center";

  const composition = await selectComposition({
    serveUrl,
    id: "CaptionedVideo",
    inputProps: {
      videoSrc,
      pages,
      fps,
      stylePreset: style,
      position: captionPosition,
    },
  });

  await renderMedia({
    serveUrl,
    codec: "h264",
    composition: {
      ...composition,
      fps,
      width,
      height,
      durationInFrames,
    },
    inputProps: {
      videoSrc,
      pages,
      fps,
      stylePreset: style,
      position: captionPosition,
    },
    outputLocation,
    overwrite: true,
    muted: false,
    imageFormat: "jpeg",
    crf: 18,
  });

  return outputLocation;
};

export const deleteRenderedVideoIfExists = (filePath: string | null) => {
  if (!filePath) return;
  if (fs.existsSync(filePath)) {
    fs.unlinkSync(filePath);
  }
};
