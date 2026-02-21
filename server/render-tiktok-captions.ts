import {
  renderVideoWithTikTokCaptions,
  renderVideoWithAutoCaption,
} from "./services/remotion";
import type { WordTimestamp } from "./services/captions";

interface RenderInput {
  video_url: string;
  ass_content?: string;
  word_timestamps?: WordTimestamp[];
  subtitle_style?: string;
  aspect_ratio?: string;
  audio_duration_seconds?: number;
  max_chars_per_line?: number;
  max_lines?: number;
}

const main = async () => {
  let inputData = "";

  process.stdin.setEncoding("utf-8");

  for await (const chunk of process.stdin) {
    inputData += chunk;
  }

  const input: RenderInput = JSON.parse(inputData);

  if (!input.video_url) {
    console.log(JSON.stringify({ success: false, error: "video_url is required" }));
    process.exit(1);
  }

  if (!input.ass_content && !input.word_timestamps) {
    console.log(
      JSON.stringify({
        success: false,
        error: "Either ass_content or word_timestamps is required",
      }),
    );
    process.exit(1);
  }

  let outputVideoPath: string | null;

  if (input.word_timestamps && input.word_timestamps.length > 0) {
    outputVideoPath = await renderVideoWithAutoCaption({
      videoSrc: input.video_url,
      wordTimestamps: input.word_timestamps,
      subtitleStyle: input.subtitle_style || null,
      aspectRatio: input.aspect_ratio || null,
      audioDurationSeconds: input.audio_duration_seconds || null,
    });
  } else if (input.ass_content) {
    outputVideoPath = await renderVideoWithTikTokCaptions({
      videoSrc: input.video_url,
      assContent: input.ass_content,
      subtitleStyle: input.subtitle_style || null,
      aspectRatio: input.aspect_ratio || null,
      audioDurationSeconds: input.audio_duration_seconds || null,
    });
  } else {
    console.log(
      JSON.stringify({ success: false, error: "No valid caption data provided" }),
    );
    process.exit(1);
  }

  if (!outputVideoPath) {
    console.log(JSON.stringify({
      success: false,
      error: "Failed to render video with subtitles",
    }));
    process.exit(1);
  }

  console.log(JSON.stringify({
    success: true,
    output_video_path: outputVideoPath,
    message: "Video with TikTok subtitles rendered successfully",
  }));
};

main().catch((err) => {
  console.log(JSON.stringify({
    success: false,
    error: err instanceof Error ? err.message : "Unknown error occurred",
  }));
  process.exit(1);
});
