# Neonvideo Merge Videos API

**Endpoint:** `POST /api/neonvideo_merge_videos`  
**Auth:** Required — send your API key in the `X-API-Key` header (or `api_key` query param).

This endpoint merges multiple videos with optional audio, subtitles, watermark, and an **outro video**. The outro is appended at the end and uses its **own audio** (the main `audio_url` stops when the outro plays). For use by the Neonvideo website only; not listed in the public API docs or UI.

---

## Request body (JSON)

| Parameter       | Type    | Required | Description |
|----------------|---------|----------|-------------|
| `video_urls`   | array   | Yes      | At least 2 video URLs to merge (in order). |
| `audio_url`    | string  | No       | Audio URL to use over the main merged video (not used during outro). |
| `outro_url`    | string  | No       | Video URL to append at the end. Uses the outro video’s own audio; main audio stops during outro. |
| `subtitle_url` | string  | No       | ASS subtitle file URL (burned into the merged video). |
| `watermark_url`| string  | No       | Image URL for watermark overlay. |
| `dimensions`   | string  | No       | Output size, e.g. `"1920x1080"`. |
| `async`        | boolean | No       | If `true`, job runs in background; response includes `job_id` and `status_url`. Default: `false`. |

---

## Example request (cURL)

```bash
curl -X POST "https://ffmpegapi.net/api/neonvideo_merge_videos" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{
    "video_urls": [
      "https://example.com/video1.mp4",
      "https://example.com/video2.mp4",
      "https://example.com/video3.mp4"
    ],
    "audio_url": "https://example.com/audio.mp3",
    "outro_url": "https://example.com/outro.mp4",
    "subtitle_url": "https://example.com/subtitles.ass",
    "watermark_url": "https://example.com/watermark.png",
    "dimensions": "1920x1080",
    "async": true
  }'
```

---

## Example response (async)

When `"async": true` you get a `202` with:

```json
{
  "success": true,
  "job_id": "uuid-here",
  "status": "pending",
  "message": "Job submitted for async processing. Use /api/job/{job_id}/status to check progress.",
  "status_url": "https://ffmpegapi.net/api/job/{job_id}/status"
}
```

Poll `status_url` until `status` is `completed`, then the job result will include `download_url` and `filename`.

---

## Example response (sync)

When `"async": false` (or omitted) you get a `200` with:

```json
{
  "success": true,
  "message": "Main and outro merged successfully",
  "download_url": "https://...",
  "filename": "request-id_merged_videos.mp4"
}
```

---

## Notes

- The **outro video** should contain an audio stream; otherwise the concat step will fail.
- Base URL may differ in your environment (e.g. Replit dev domain or local server). Replace `https://ffmpegapi.net` with your actual host.
