import React from "react";
import { Composition, registerRoot } from "remotion";
import {
  CaptionedVideoComposition,
  type CaptionedVideoCompositionProps,
} from "./CaptionedVideoComposition";
import type { SubtitleStyle } from "../../shared/subtitleStyles";

const defaultProps: CaptionedVideoCompositionProps = {
  videoSrc: "",
  pages: [],
  fps: 30,
  stylePreset: "plain-white" as SubtitleStyle,
};

const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="CaptionedVideo"
      component={CaptionedVideoComposition as any}
      durationInFrames={300}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps as any}
    />
  );
};

registerRoot(RemotionRoot);
