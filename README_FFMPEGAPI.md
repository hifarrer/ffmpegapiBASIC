# FFMPEG API Documentation

## Overview

The FFMPEG API provides powerful video and audio processing capabilities through simple REST endpoints. This API allows you to programmatically create, merge, transform, and manipulate video and audio files using FFMPEG under the hood.

**Base URL:** `https://ffmpegapi.net/api/`

## Available Endpoints

1. **Image & Audio Merger** - Create videos from static images and audio files
2. **Video Merger** - Concatenate multiple videos into one
3. **Picture-in-Picture** - Overlay one video on top of another
4. **Add Subtitles** - Burn ASS subtitle files into videos
5. **Split Audio** - Split audio files into equal segments
6. **Trim Audio** - Trim audio files to specific durations
7. **Convert to Vertical** - Convert horizontal videos to vertical format (3:4 or 9:16)
8. **Convert video to GIF** - Encode a video as an animated GIF (optional chromakey transparency)
9. **Job Status** - Check status of asynchronous processing jobs

---

## Authentication

All API endpoints require authentication using an API key. Include your API key in one of three ways:

### 1. Header (Recommended)
```bash
X-API-Key: your_api_key_here
```

### 2. Query Parameter
```bash
?api_key=your_api_key_here
```

### 3. JSON Body
```json
{
  "api_key": "your_api_key_here"
}
```

**How to get an API key:**
1. Register at https://ffmpegapi.net/register
2. Access your dashboard at https://ffmpegapi.net/dashboard
3. Copy your API key

---

## Processing Modes

All endpoints support two processing modes:

### Synchronous (Default)
Response includes the processed file immediately. Best for small files.

### Asynchronous
Include `"async": true` in your request. Returns a job ID to check progress later. Best for large files or batch processing.

---

## API Endpoints

### 1. Image & Audio Merger

**Endpoint:** `POST /api/merge_image_audio`

**Description:** Creates a video by combining a static image with an audio file. The video duration matches the audio length.

**Request Body:**
```json
{
  "image_url": "https://example.com/image.jpg",
  "audio_url": "https://example.com/audio.mp3",
  "async": false
}
```

**Parameters:**
- `image_url` (required): URL of the image file (PNG, JPG, JPEG)
- `audio_url` (required): URL of the audio file (MP3, WAV, M4A)
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/merge_image_audio" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "image_url": "https://example.com/cover.jpg",
    "audio_url": "https://example.com/podcast.mp3"
  }'
```

**Success Response (Sync):**
```json
{
  "success": true,
  "message": "Video created successfully",
  "download_url": "https://ffmpegapi.net/api/storage/video_abc123.mp4",
  "filename": "video_abc123.mp4"
}
```

**Success Response (Async):**
```json
{
  "success": true,
  "job_id": "job_xyz789",
  "status": "pending",
  "message": "Job submitted for async processing",
  "status_url": "https://ffmpegapi.net/api/job/job_xyz789/status"
}
```

---

### 2. Video Merger

**Endpoint:** `POST /api/merge_videos`

**Description:** Concatenates multiple videos from URLs into a single video, or merges one video with an audio file. Optionally replace audio or specify output dimensions. Use either at least 2 video URLs, or 1 video URL with an `audio_url`.

**Request Body:**
```json
{
  "video_urls": [
    "https://example.com/video1.mp4",
    "https://example.com/video2.mp4",
    "https://example.com/video3.mp4"
  ],
  "audio_url": "https://example.com/background.mp3",
  "output_width": 1920,
  "output_height": 1080,
  "async": false
}
```

**Parameters:**
- `video_urls` (required): Array of video URLs to merge (at least 2, or exactly 1 if `audio_url` is provided)
- `audio_url` (optional): URL of audio file to replace merged video audio; required when using only 1 video URL
- `output_width` (optional): Output video width in pixels
- `output_height` (optional): Output video height in pixels
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/merge_videos" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "video_urls": [
      "https://example.com/intro.mp4",
      "https://example.com/main.mp4",
      "https://example.com/outro.mp4"
    ],
    "audio_url": "https://example.com/music.mp3",
    "output_width": 1920,
    "output_height": 1080
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Videos merged successfully",
  "download_url": "https://ffmpegapi.net/api/storage/merged_abc123.mp4",
  "filename": "merged_abc123.mp4"
}
```

---

### 3. Picture-in-Picture

**Endpoint:** `POST /api/picture_in_picture`

**Description:** Overlays one video (overlay) on top of another video (base) with customizable position, scale, and audio options.

**Request Body:**
```json
{
  "base_video_url": "https://example.com/main_video.mp4",
  "overlay_video_url": "https://example.com/overlay_video.mp4",
  "position": "top-right",
  "scale": 0.25,
  "audio_option": "base",
  "async": false
}
```

**Parameters:**
- `base_video_url` (required): URL of the base/background video
- `overlay_video_url` (required): URL of the overlay/picture-in-picture video
- `position` (optional): Position of overlay - `"top-left"`, `"top-right"`, `"bottom-left"`, `"bottom-right"`, `"center"` (default: `"top-right"`)
- `scale` (optional): Scale of overlay relative to base (0.1 to 1.0, default: 0.25)
- `audio_option` (optional): Audio source - `"base"`, `"overlay"`, `"mute"` (default: `"base"`)
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/picture_in_picture" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "base_video_url": "https://example.com/gameplay.mp4",
    "overlay_video_url": "https://example.com/facecam.mp4",
    "position": "bottom-right",
    "scale": 0.2,
    "audio_option": "base"
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Picture-in-picture video created successfully",
  "download_url": "https://ffmpegapi.net/api/storage/pip_abc123.mp4",
  "filename": "pip_abc123.mp4"
}
```

---

### 4. Add Subtitles

**Endpoint:** `POST /api/add_subtitles`

**Description:** Burns ASS subtitle files directly into a video with full styling support.

**Request Body:**
```json
{
  "video_url": "https://example.com/video.mp4",
  "subtitle_url": "https://example.com/subtitles.ass",
  "async": false
}
```

**Parameters:**
- `video_url` (required): URL of the video file
- `subtitle_url` (required): URL of the ASS subtitle file
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/add_subtitles" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://example.com/movie.mp4",
    "subtitle_url": "https://example.com/english.ass"
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Subtitles added successfully",
  "download_url": "https://ffmpegapi.net/api/storage/subtitled_abc123.mp4",
  "filename": "subtitled_abc123.mp4"
}
```

---

### 5. Split Audio

**Endpoint:** `POST /api/split_audio`

**Description:** Splits an audio file into equal segments. Perfect for creating podcast chapters or splitting long audio files.

**Request Body:**
```json
{
  "audio_url": "https://example.com/podcast.mp3",
  "num_segments": 4,
  "async": false
}
```

**Parameters:**
- `audio_url` (required): URL of the audio file to split
- `num_segments` (required): Number of equal segments to create (2-20)
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/split_audio" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/long_podcast.mp3",
    "num_segments": 3
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Audio split into 3 segments successfully",
  "segments": [
    {
      "segment": 1,
      "download_url": "https://ffmpegapi.net/api/storage/segment_1_abc123.mp3",
      "filename": "segment_1_abc123.mp3"
    },
    {
      "segment": 2,
      "download_url": "https://ffmpegapi.net/api/storage/segment_2_abc123.mp3",
      "filename": "segment_2_abc123.mp3"
    },
    {
      "segment": 3,
      "download_url": "https://ffmpegapi.net/api/storage/segment_3_abc123.mp3",
      "filename": "segment_3_abc123.mp3"
    }
  ]
}
```

---

### 5b. Split Audio by Segments

**Endpoint:** `POST /api/split_audio_segments`

**Description:** Splits an audio file into segments of a specified duration. Unlike the standard split audio endpoint which divides into equal parts, this endpoint creates segments of a fixed duration. For example, a 66-second audio with `segment_duration` of 10 seconds will produce 6 segments of 10 seconds each and 1 final segment of 6 seconds.

**Request Body:**
```json
{
  "audio_url": "https://example.com/podcast.mp3",
  "segment_duration": 10,
  "async": false
}
```

**Parameters:**
- `audio_url` (required): URL of the audio file to split
- `segment_duration` (required): Duration of each segment in seconds (1-3600). Default is 30 seconds.
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/split_audio_segments" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/long_podcast.mp3",
    "segment_duration": 10
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Audio successfully split into 7 segments",
  "segment_duration": 10,
  "total_segments": 7,
  "segments": [
    {
      "segment": "segment_01.mp3",
      "download_url": "https://ffmpegapi.net/download/abc123/segment_01.mp3"
    },
    {
      "segment": "segment_02.mp3",
      "download_url": "https://ffmpegapi.net/download/abc123/segment_02.mp3"
    },
    {
      "segment": "segment_03.mp3",
      "download_url": "https://ffmpegapi.net/download/abc123/segment_03.mp3"
    }
  ]
}
```

---

### 6. Trim Audio

**Endpoint:** `POST /api/trim_audio`

**Description:** Trims an audio file to a specific duration with optional fade-out effect.

**Request Body:**
```json
{
  "audio_url": "https://example.com/song.mp3",
  "duration": 30,
  "fade_out": true,
  "async": false
}
```

**Parameters:**
- `audio_url` (required): URL of the audio file to trim
- `duration` (required): Desired duration in seconds (1-600)
- `fade_out` (optional): Apply 2-second fade-out effect at the end (default: `false`)
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/trim_audio" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/full_song.mp3",
    "duration": 30,
    "fade_out": true
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Audio trimmed to 30 seconds successfully",
  "download_url": "https://ffmpegapi.net/api/storage/trimmed_abc123.mp3",
  "filename": "trimmed_abc123.mp3"
}
```

---

### 7. Convert to Vertical

**Endpoint:** `POST /api/convert_to_vertical`

**Description:** Converts horizontal videos to vertical format optimized for mobile viewing. Automatically selects 3:4 or 9:16 aspect ratio based on the original video dimensions. Optional watermark support.

**Request Body:**
```json
{
  "video_url": "https://example.com/landscape.mp4",
  "watermark_url": "https://example.com/logo.png",
  "async": false
}
```

**Parameters:**
- `video_url` (required): URL of the video to convert
- `watermark_url` (optional): URL of watermark image (placed at top right, 20% of video width)
- `async` (optional): Set to `true` for asynchronous processing

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/convert_to_vertical" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://example.com/horizontal_video.mp4",
    "watermark_url": "https://example.com/brand_logo.png"
  }'
```

**Success Response:**
```json
{
  "success": true,
  "message": "Video successfully converted to vertical 9:16 format",
  "download_url": "https://ffmpegapi.net/api/storage/vertical_abc123.mp4",
  "filename": "vertical_abc123.mp4",
  "aspect_ratio": "9:16"
}
```

---

### 8. Convert video to GIF

**Endpoint:** `POST /api/convert_video_to_gif`

**Description:** Downloads a video from a URL and encodes it as an animated GIF using a palette for better quality. Audio is omitted. Optional **transparent background** uses FFmpeg **colorkey** (RGB color-based keying): pixels close to the chosen color become transparent. This works well for solid-color backdrops (for example greenscreen); it is not automatic background removal for arbitrary scenes.

**Request Body:**
```json
{
  "video_url": "https://example.com/clip.mp4",
  "transparent_background": false,
  "chromakey_color": "0x00FF00",
  "similarity": 0.15,
  "blend": 0.1,
  "fps": 10,
  "max_width": 480
}
```

**Parameters:**
- `video_url` (required): URL of the source video (MP4, MOV, WebM, etc.)
- `transparent_background` (optional): If `true`, apply colorkey before building the GIF palette (default: `false`)
- `chromakey_color` (optional): Color to key out when `transparent_background` is true, as `0xRRGGBB`, `#RRGGBB`, or `RRGGBB` (default: `0x00FF00`)
- `similarity` (optional): How close a pixel must be to `chromakey_color` to be keyed out, 0.01–1.0 (default: `0.15`). Lower values are more precise; increase if edges of the backdrop remain.
- `blend` (optional): Softness of the transparency edge, 0.0–1.0 (default: `0.1`). Higher values produce a smoother falloff.
- `fps` (optional): Output frame rate, clamped between 1 and 30 (default: `10`)
- `max_width` (optional): Maximum width in pixels; height scales proportionally, clamped between 64 and 1280 (default: `480`)

**curl Example:**
```bash
curl -X POST "https://ffmpegapi.net/api/convert_video_to_gif" \
  -H "X-API-Key: your_api_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "video_url": "https://example.com/clip.mp4",
    "transparent_background": true,
    "chromakey_color": "0x00FF00",
    "similarity": 0.15,
    "blend": 0.1,
    "fps": 10,
    "max_width": 480
  }'
```

**Python Example:**
```python
import requests

url = "https://ffmpegapi.net/api/convert_video_to_gif"
headers = {
    "X-API-Key": "your_api_key_here",
    "Content-Type": "application/json",
}
data = {
    "video_url": "https://example.com/clip.mp4",
    "transparent_background": True,
    "chromakey_color": "0x00FF00",
    "similarity": 0.15,
    "blend": 0.1,
    "fps": 10,
    "max_width": 480,
}
response = requests.post(url, headers=headers, json=data)
result = response.json()
if result.get("success"):
    print(result["download_url"])
else:
    print(result.get("error"))
```

**JavaScript Example:**
```javascript
const response = await fetch("https://ffmpegapi.net/api/convert_video_to_gif", {
  method: "POST",
  headers: {
    "X-API-Key": "your_api_key_here",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    video_url: "https://example.com/clip.mp4",
    transparent_background: true,
    chromakey_color: "0x00FF00",
    similarity: 0.15,
    blend: 0.1,
    fps: 10,
    max_width: 480,
  }),
});
const result = await response.json();
if (result.success) {
  console.log(result.download_url);
} else {
  console.error(result.error);
}
```

**Success Response:**
```json
{
  "success": true,
  "message": "Video converted to GIF successfully",
  "download_url": "https://ffmpegapi.net/api/storage/uuid_output.gif",
  "filename": "uuid_output.gif"
}
```

**Error Response:**
```json
{
  "success": false,
  "error": "GIF conversion failed: ..."
}
```

---

### 9. Job Status

**Endpoint:** `GET /api/job/{job_id}/status`

**Description:** Check the status of an asynchronous processing job.

**URL Parameters:**
- `job_id` (required): The job ID returned from an async request

**curl Example:**
```bash
curl -X GET "https://ffmpegapi.net/api/job/job_xyz789/status?api_key=your_api_key_here"
```

**Response (Processing):**
```json
{
  "success": true,
  "job_id": "job_xyz789",
  "status": "processing",
  "job_type": "merge_videos",
  "created_at": "2025-10-13T12:00:00Z"
}
```

**Response (Completed):**
```json
{
  "success": true,
  "job_id": "job_xyz789",
  "status": "completed",
  "job_type": "merge_videos",
  "result": {
    "success": true,
    "download_url": "https://ffmpegapi.net/api/storage/merged_abc123.mp4",
    "filename": "merged_abc123.mp4"
  },
  "created_at": "2025-10-13T12:00:00Z",
  "completed_at": "2025-10-13T12:05:30Z"
}
```

**Response (Failed):**
```json
{
  "success": true,
  "job_id": "job_xyz789",
  "status": "failed",
  "job_type": "merge_videos",
  "result": {
    "success": false,
    "error": "Failed to download video from URL"
  },
  "created_at": "2025-10-13T12:00:00Z",
  "completed_at": "2025-10-13T12:01:15Z"
}
```

---

## Error Responses

All endpoints return consistent error responses:

**400 Bad Request:**
```json
{
  "success": false,
  "error": "video_url is required"
}
```

**401 Unauthorized:**
```json
{
  "success": false,
  "error": "Invalid or missing API key"
}
```

**500 Internal Server Error:**
```json
{
  "success": false,
  "error": "Video processing failed: [detailed error message]"
}
```

---



## AI Agent Integration Guide

### Key Considerations for AI Agents

1. **Always use async mode for large files** - Set `"async": true` for videos > 50MB or audio > 20MB
2. **Poll job status** - Check job status every 5-10 seconds for async jobs
3. **Handle errors gracefully** - Parse error messages and retry with exponential backoff
4. **Validate URLs before sending** - Ensure URLs are publicly accessible
5. **Store download URLs** - Download URLs are valid for 7 days

### Sample Workflow (Python)

```python
import requests
import time

API_KEY = "your_api_key_here"
BASE_URL = "https://ffmpegapi.net/api"

# Submit async job
response = requests.post(
    f"{BASE_URL}/merge_videos",
    headers={"X-API-Key": API_KEY},
    json={
        "video_urls": [
            "https://example.com/video1.mp4",
            "https://example.com/video2.mp4"
        ],
        "async": True
    }
)

job_data = response.json()
job_id = job_data["job_id"]

# Poll for completion
while True:
    status_response = requests.get(
        f"{BASE_URL}/job/{job_id}/status",
        headers={"X-API-Key": API_KEY}
    )
    
    status_data = status_response.json()
    
    if status_data["status"] == "completed":
        download_url = status_data["result"]["download_url"]
        print(f"Video ready: {download_url}")
        break
    elif status_data["status"] == "failed":
        print(f"Job failed: {status_data['result']['error']}")
        break
    
    time.sleep(5)  # Wait 5 seconds before checking again
```

---

## Support

- **Documentation:** https://ffmpegapi.net/docs
- **Contact:** https://ffmpegapi.net/contact
- **Email:** info@ffmpegapi.net

---

## Changelog

### Version 1.0 (October 2025)
- Initial release with 7 video/audio processing endpoints
- Synchronous and asynchronous processing support
- Cloud storage integration for reliable downloads
- Subscription-based rate limiting
