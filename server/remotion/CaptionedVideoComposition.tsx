import React from "react";
import { AbsoluteFill, OffthreadVideo, useCurrentFrame } from "remotion";
import type { SubtitleStyle } from "../../shared/subtitleStyles";
import type { TikTokCaptionPage } from "../services/captions";

export type CaptionPosition = "top" | "center" | "bottom";

export interface CaptionedVideoCompositionProps {
  videoSrc: string;
  pages: TikTokCaptionPage[];
  fps: number;
  stylePreset: SubtitleStyle;
  position?: CaptionPosition;
}

const styleMap: Record<
  SubtitleStyle,
  {
    fontSize: number;
    activeColor: string;
    activeBackground?: string;
    bottomOffset: number;
  }
> = {
  "plain-white": {
    fontSize: 60,
    activeColor: "#ffffff",
    activeBackground: "transparent",
    bottomOffset: 140,
  },
  "yellow-bg": {
    fontSize: 60,
    activeColor: "#ffffff",
    activeBackground: "#facc15",
    bottomOffset: 140,
  },
  "pink-bg": {
    fontSize: 60,
    activeColor: "#ffffff",
    activeBackground: "#ec4899",
    bottomOffset: 140,
  },
  "blue-bg": {
    fontSize: 60,
    activeColor: "#ffffff",
    activeBackground: "#3b82f6",
    bottomOffset: 140,
  },
  "red-bg": {
    fontSize: 60,
    activeColor: "#ffffff",
    activeBackground: "#ef4444",
    bottomOffset: 140,
  },
};

const getPositionStyles = (
  pos: CaptionPosition,
  offset: number,
): React.CSSProperties => {
  switch (pos) {
    case "top":
      return {
        justifyContent: "flex-start",
        paddingTop: offset,
      };
    case "center":
      return {
        justifyContent: "center",
      };
    case "bottom":
    default:
      return {
        justifyContent: "flex-end",
        paddingBottom: offset,
      };
  }
};

export const CaptionedVideoComposition: React.FC<
  CaptionedVideoCompositionProps
> = ({ videoSrc, pages, fps, stylePreset, position = "bottom" }) => {
  const frame = useCurrentFrame();
  const currentMs = (frame / fps) * 1000;
  const activeStyle = styleMap[stylePreset] ?? styleMap["plain-white"];

  const activePage = pages.find((page) => {
    const endMs = page.startMs + page.durationMs;
    return currentMs >= page.startMs && currentMs <= endMs;
  });

  const activeTokenIndex = activePage
    ? activePage.tokens.findIndex(
        (token) => currentMs >= token.fromMs && currentMs <= token.toMs,
      )
    : -1;
  const activeToken =
    activePage && activeTokenIndex >= 0
      ? activePage.tokens[activeTokenIndex]
      : null;

  const karaokeProgress =
    activeToken && activeToken.toMs > activeToken.fromMs
      ? Math.min(
          1,
          Math.max(
            0,
            (currentMs - activeToken.fromMs) / (activeToken.toMs - activeToken.fromMs),
          ),
        )
      : 0;

  const karaokeScale =
    1 + 0.22 * Math.sin(Math.min(1, karaokeProgress) * Math.PI) * (1 - karaokeProgress * 0.15);

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      <OffthreadVideo src={videoSrc} />
      {activePage ? (
        <AbsoluteFill
          style={{
            alignItems: "center",
            paddingLeft: 48,
            paddingRight: 48,
            ...getPositionStyles(position, activeStyle.bottomOffset),
          }}
        >
          <div
            style={{
              textAlign: "center",
              fontSize: activeStyle.fontSize,
              fontWeight: 800,
              lineHeight: 1.1,
              textShadow: "0 4px 24px rgba(0, 0, 0, 0.9)",
              WebkitTextStroke: "1px rgba(0,0,0,0.85)",
            }}
          >
            {activeToken ? (
              <span
                style={{
                  color: activeStyle.activeColor,
                  background: activeStyle.activeBackground,
                  borderRadius: 10,
                  padding: "4px 14px",
                  display: "inline-block",
                  transform: `scale(${karaokeScale})`,
                  transformOrigin: "center center",
                  transition: "transform 40ms linear",
                }}
              >
                {activeToken.text}
              </span>
            ) : null}
          </div>
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
