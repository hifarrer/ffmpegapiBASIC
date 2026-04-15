# YouTube to MP4 via RapidAPI (Porting Guide)

This document explains how to replicate the **exact YouTube URL -> MP4 URL conversion flow** used by the Swap page in this project.

Use this as an implementation brief for another AI agent or developer in a different codebase.

---

## What this feature does

When the user enters a YouTube URL, the app:

1. Detects that the URL is YouTube.
2. Calls a backend endpoint: `POST /api/tools/youtube-to-mp4`.
3. Backend calls RapidAPI (`youtube-info-download-api.p.rapidapi.com`) to start conversion.
4. Backend polls the returned `progress_url` every 5 seconds (up to 5 minutes).
5. Backend returns `{ success, download_url, title }` when ready.
6. Frontend uses `download_url` (direct MP4 URL) in the rest of the workflow.

---
---

## Required environment variables

In the target project, set:

- `RAPIDAPI_KEY`: your RapidAPI key for the YouTube converter API.

The host is hardcoded in current logic as:

- `youtube-info-download-api.p.rapidapi.com`

---

## Backend implementation (required)

### 1) Add conversion service function

Create a service module (or equivalent) with this behavior:

- Validate `RAPIDAPI_KEY` exists; if missing, throw `"RAPIDAPI_KEY is not configured"`.
- Build query params exactly:
  - `format=1080`
  - `add_info=0`
  - `url=<youtubeUrl>`
  - `audio_quality=128`
  - `allow_extended_duration=false`
  - `no_merge=false`
  - `audio_language=en`
- Send `GET` request to:
  - `https://youtube-info-download-api.p.rapidapi.com/ajax/download.php?<params>`
- Headers:
  - `Content-Type: application/json`
  - `x-rapidapi-host: youtube-info-download-api.p.rapidapi.com`
  - `x-rapidapi-key: <RAPIDAPI_KEY>`
- If non-2xx, throw with response text.
- Parse JSON and require:
  - `success` truthy
  - `progress_url` present
- Poll `progress_url` every 5 seconds:
  - Maximum 60 attempts (300 seconds / 5 minutes)
  - On each poll, parse JSON.
  - Success condition: `progressResult.success === 1` and `progressResult.download_url` exists.
  - Error condition: `progressResult.error` exists -> throw.
  - Timeout after max attempts -> throw `"YouTube to MP4 conversion timed out"`.
- Return:
  - `success: true`
  - `download_url`
  - `title` (fallback `"YouTube Video"` if missing)

### 2) Add HTTP endpoint

Create:

- `POST /api/tools/youtube-to-mp4`

Route behavior:

- Requires auth in this repo (`authenticateToken` middleware). Keep or remove based on your app policy.
- Body:
  - `youtubeUrl` (string, required)
- Validation:
  - If missing/invalid, return `400` with:
    - `{ "error": "Please provide a valid YouTube URL" }`
- Call conversion service with trimmed URL.
- Success response:
  - `{ "success": true, "download_url": "<mp4-url>", "title": "<video-title>" }`
- Failure response:
  - HTTP 500 with:
    - `{ "error": "<message>" }`

---

## Frontend integration (required)

### 1) YouTube URL detection

Use this exact regex (case-insensitive) from current implementation:

```ts
/^(https?:\/\/)?(www\.)?(youtube\.com\/(watch|shorts|embed)|youtu\.be\/)/i
```

### 2) Conversion trigger

When user clicks a "Select Range" / "Convert" action:

- If URL is YouTube and no converted URL exists yet:
  - call `POST /api/tools/youtube-to-mp4` with:
    - `{ youtubeUrl: videoUrl.trim() }`
  - expect JSON with:
    - `success`
    - `download_url`
  - if valid, store `download_url` and continue workflow
  - if invalid, show toast/error message

### 3) Request shape used by current app

The current frontend helper sends:

- `Content-Type: application/json` (for JSON requests)
- `Authorization: Bearer <token>` if token exists
- `credentials: include`

If your target app is public/no-auth, you can omit Authorization.

---

## Copy-ready backend example (Express + TypeScript)

```ts
import express from "express";

const router = express.Router();
const RAPIDAPI_KEY = process.env.RAPIDAPI_KEY || "";
const RAPIDAPI_YT_HOST = "youtube-info-download-api.p.rapidapi.com";

async function youtubeToMp4(youtubeUrl: string) {
  if (!RAPIDAPI_KEY) {
    throw new Error("RAPIDAPI_KEY is not configured");
  }

  const params = new URLSearchParams({
    format: "1080",
    add_info: "0",
    url: youtubeUrl,
    audio_quality: "128",
    allow_extended_duration: "false",
    no_merge: "false",
    audio_language: "en",
  });

  const submitResponse = await fetch(
    `https://${RAPIDAPI_YT_HOST}/ajax/download.php?${params.toString()}`,
    {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
        "x-rapidapi-host": RAPIDAPI_YT_HOST,
        "x-rapidapi-key": RAPIDAPI_KEY,
      },
    },
  );

  if (!submitResponse.ok) {
    throw new Error(`YouTube download API error: ${submitResponse.status} - ${await submitResponse.text()}`);
  }

  const submitResult = await submitResponse.json();
  if (!submitResult.success) {
    throw new Error(submitResult.message || "YouTube download submission failed");
  }

  const progressUrl = submitResult.progress_url;
  if (!progressUrl) {
    throw new Error("No progress_url returned from YouTube download API");
  }

  const title = submitResult.title || "YouTube Video";

  for (let attempt = 0; attempt < 60; attempt++) {
    await new Promise((r) => setTimeout(r, 5000));
    const progressResponse = await fetch(progressUrl);
    if (!progressResponse.ok) continue;

    const progressResult = await progressResponse.json();
    if (progressResult.success === 1 && progressResult.download_url) {
      return {
        success: true,
        download_url: progressResult.download_url as string,
        title,
      };
    }
    if (progressResult.error) {
      throw new Error(progressResult.error);
    }
  }

  throw new Error("YouTube to MP4 conversion timed out");
}

router.post("/api/tools/youtube-to-mp4", async (req, res) => {
  try {
    const { youtubeUrl } = req.body || {};
    if (!youtubeUrl || typeof youtubeUrl !== "string") {
      return res.status(400).json({ error: "Please provide a valid YouTube URL" });
    }

    const result = await youtubeToMp4(youtubeUrl.trim());
    return res.json({
      success: true,
      download_url: result.download_url,
      title: result.title,
    });
  } catch (error) {
    return res.status(500).json({
      error: error instanceof Error ? error.message : "Failed to convert YouTube to MP4",
    });
  }
});

export default router;
```

---

## Copy-ready frontend example (React)

```ts
const isYouTubeUrl = (url: string) =>
  /^(https?:\/\/)?(www\.)?(youtube\.com\/(watch|shorts|embed)|youtu\.be\/)/i.test(url.trim());

async function convertYoutubeUrl(youtubeUrl: string, token?: string) {
  const res = await fetch("/api/tools/youtube-to-mp4", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    credentials: "include",
    body: JSON.stringify({ youtubeUrl: youtubeUrl.trim() }),
  });

  const data = await res.json();
  if (!res.ok || !data.success || !data.download_url) {
    throw new Error(data.error || "YouTube conversion failed");
  }
  return data.download_url as string;
}
```

---

## End-to-end test checklist

1. Add valid `RAPIDAPI_KEY` in environment.
2. Start backend and frontend.
3. Paste a valid YouTube link (`youtube.com/watch...` or `youtu.be/...`).
4. Trigger conversion.
5. Confirm backend logs show:
   - conversion start
   - polling
   - conversion complete
6. Confirm frontend receives `download_url`.
7. Open returned `download_url` directly and verify MP4 is playable.
8. Test invalid URL -> expect graceful `400`/UI error.
9. Test missing API key -> expect clear server error.
10. Test timeout/error path and ensure user-facing message is shown.

---

## Notes for portability

- This flow is async-by-polling. Conversion can take time; do not set short frontend timeouts.
- Keep secrets server-side; do not expose RapidAPI key in browser code.
- If moving to serverless, ensure function timeout allows polling window, or offload polling to a background job.
- API output should stay stable: `success`, `download_url`, `title` to avoid frontend breakage.

