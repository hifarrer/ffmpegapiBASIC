import { renderVideoWithTikTokCaptions } from "./services/remotion";

interface RenderInput {
  video_url: string;
  ass_content: string;
  subtitle_style?: string;
  aspect_ratio?: string;
  audio_duration_seconds?: number;
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

  if (!input.ass_content) {
    console.log(JSON.stringify({ success: false, error: "ass_content is required" }));
    process.exit(1);
  }

  const outputVideoPath = await renderVideoWithTikTokCaptions({
    videoSrc: input.video_url,
    assContent: input.ass_content,
    subtitleStyle: input.subtitle_style || null,
    aspectRatio: input.aspect_ratio || null,
    audioDurationSeconds: input.audio_duration_seconds || null,
  });

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
