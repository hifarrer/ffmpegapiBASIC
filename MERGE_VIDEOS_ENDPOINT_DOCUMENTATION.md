# Merge Multiple Videos Endpoint - Implementation Guide

This document provides complete instructions to replicate the Merge Multiple Videos endpoint functionality locally in your application.

---

## Table of Contents

1. [Overview](#overview)
2. [System Requirements](#system-requirements)
3. [API Endpoint Specification](#api-endpoint-specification)
4. [Request/Response Format](#requestresponse-format)
5. [Core Functions Implementation](#core-functions-implementation)
6. [Complete Code Reference](#complete-code-reference)
7. [Usage Examples](#usage-examples)
8. [Error Handling](#error-handling)

---

## Overview

The Merge Multiple Videos endpoint concatenates multiple video files into a single output video. It supports:

- **Video concatenation**: Merge 2 or more videos sequentially
- **Custom audio override**: Replace the merged video's audio with a custom audio track
- **Custom output dimensions**: Resize/scale output to specific dimensions
- **Subtitle burning**: Burn ASS/SSA subtitles into the video
- **Watermark overlay**: Add a PNG watermark to the bottom-right corner
- **Auto-normalization**: Automatically normalizes videos with different properties (resolution, frame rate, codec) for seamless merging
- **Smart optimization**: Skips normalization when videos are identical for faster processing

---

## System Requirements

### FFMPEG Installation

FFMPEG and FFPROBE must be installed and accessible from the command line.

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install ffmpeg
```

**macOS (Homebrew):**
```bash
brew install ffmpeg
```

**Verify installation:**
```bash
ffmpeg -version
ffprobe -version
```

### Python Dependencies

```txt
flask
requests
```

Install with:
```bash
pip install flask requests
```

---

## API Endpoint Specification

| Property | Value |
|----------|-------|
| **URL** | `/api/merge_videos` |
| **Method** | `POST` |
| **Content-Type** | `application/json` |

---

## Request/Response Format

### Request Body (JSON)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `video_urls` | `array[string]` | Yes | List of video URLs to merge (minimum 2) |
| `audio_url` | `string` | No | URL to custom audio file to replace video audio |
| `dimensions` | `string` | No | Output dimensions in format `WIDTHxHEIGHT` (e.g., `1920x1080`) |
| `subtitle_url` | `string` | No | URL to ASS/SSA subtitle file to burn into video |
| `watermark_url` | `string` | No | URL to PNG watermark image |

### Example Request

```json
{
  "video_urls": [
    "https://example.com/video1.mp4",
    "https://example.com/video2.mp4",
    "https://example.com/video3.mp4"
  ],
  "audio_url": "https://example.com/background_music.mp3",
  "dimensions": "1280x720",
  "subtitle_url": "https://example.com/captions.ass",
  "watermark_url": "https://example.com/logo.png"
}
```

### Success Response (200)

```json
{
  "success": true,
  "message": "Videos merged successfully",
  "download_url": "https://your-domain.com/download/uuid_merged_videos.mp4",
  "filename": "uuid_merged_videos.mp4"
}
```

### Error Response (400/500)

```json
{
  "success": false,
  "error": "Error description here"
}
```

---

## Core Functions Implementation

### 1. Configuration Setup

```python
import os
import logging
import subprocess
import uuid

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Folder configuration
UPLOAD_FOLDER = '/tmp/uploads'
OUTPUT_FOLDER = '/tmp/outputs'

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
```

### 2. File Download Functions

```python
def download_video_from_url(url, output_path):
    """Download video from URL to local path"""
    try:
        import urllib.request
        logging.info(f"Downloading video from: {url}")
        urllib.request.urlretrieve(url, output_path)
        return True, "Video downloaded successfully"
    except Exception as e:
        logging.error(f"Failed to download video from {url}: {str(e)}")
        return False, f"Failed to download video: {str(e)}"

def download_file_from_url(url, output_path, file_type="file"):
    """Download any file from URL to local path"""
    try:
        import urllib.request
        logging.info(f"Downloading {file_type} from: {url}")
        urllib.request.urlretrieve(url, output_path)
        return True, f"{file_type.capitalize()} downloaded successfully"
    except Exception as e:
        logging.error(f"Failed to download {file_type} from {url}: {str(e)}")
        return False, f"Failed to download {file_type}: {str(e)}"
```

### 3. File Cleanup Function

```python
def cleanup_file(file_path):
    """Safely remove a file"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logging.error(f"Failed to cleanup file {file_path}: {str(e)}")
```

### 4. Video Analysis Functions

```python
def get_video_dimensions(video_path):
    """Get video dimensions using ffprobe"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-select_streams', 'v:0',
            video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            
            if streams:
                width = streams[0].get('width')
                height = streams[0].get('height')
                if width and height:
                    return True, (int(width), int(height))
        
        return False, "Could not get video dimensions"
        
    except Exception as e:
        logging.error(f"Error getting video dimensions: {str(e)}")
        return False, f"Error analyzing video: {str(e)}"

def get_video_properties(video_path):
    """Get comprehensive video properties using ffprobe"""
    try:
        cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-show_format',
            video_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            
            properties = {
                'width': None,
                'height': None,
                'fps': None,
                'video_codec': None,
                'audio_codec': None,
                'audio_sample_rate': None,
                'audio_channels': None,
                'pixel_format': None
            }
            
            # Get video stream properties
            for stream in streams:
                if stream.get('codec_type') == 'video':
                    properties['width'] = stream.get('width')
                    properties['height'] = stream.get('height')
                    properties['video_codec'] = stream.get('codec_name')
                    properties['pixel_format'] = stream.get('pix_fmt')
                    
                    # Calculate FPS from r_frame_rate
                    fps_str = stream.get('r_frame_rate', '0/1')
                    if '/' in fps_str:
                        num, den = fps_str.split('/')
                        if int(den) > 0:
                            properties['fps'] = round(int(num) / int(den), 2)
                
                elif stream.get('codec_type') == 'audio':
                    properties['audio_codec'] = stream.get('codec_name')
                    properties['audio_sample_rate'] = stream.get('sample_rate')
                    properties['audio_channels'] = stream.get('channels')
            
            return True, properties
        
        return False, "Could not get video properties"
        
    except Exception as e:
        logging.error(f"Error getting video properties: {str(e)}")
        return False, f"Error analyzing video: {str(e)}"

def check_videos_identical(video_paths):
    """Check if all videos have identical properties (no normalization needed)"""
    if len(video_paths) < 2:
        return True, "Only one video, no comparison needed"
    
    all_properties = []
    
    # Get properties for all videos
    for i, video_path in enumerate(video_paths):
        success, props = get_video_properties(video_path)
        if not success:
            return False, f"Could not analyze video {i+1}: {props}"
        all_properties.append(props)
        logging.info(f"Video {i+1} properties: {props['width']}x{props['height']}, {props['fps']}fps, codec: {props['video_codec']}, audio: {props['audio_codec']}")
    
    # Compare all videos to the first one
    first_props = all_properties[0]
    for i, props in enumerate(all_properties[1:], 1):
        # Check critical properties that require normalization if different
        if (props['width'] != first_props['width'] or
            props['height'] != first_props['height'] or
            props['fps'] != first_props['fps'] or
            props['video_codec'] != first_props['video_codec'] or
            props['pixel_format'] != first_props['pixel_format'] or
            props['audio_codec'] != first_props['audio_codec'] or
            props['audio_sample_rate'] != first_props['audio_sample_rate'] or
            props['audio_channels'] != first_props['audio_channels']):
            
            logging.info(f"Videos have different properties - normalization required")
            return False, "Videos have different properties and need normalization"
    
    logging.info(f"All videos have identical properties - skipping normalization for faster processing")
    return True, "All videos are identical"
```

### 5. Core Video Merge Function

```python
def merge_videos_with_ffmpeg(video_paths, output_path, audio_path=None, dimensions=None):
    """Merge multiple videos using FFMPEG"""
    temp_list_path = None
    normalized_videos = []
    target_width = None
    target_height = None
    skip_normalization = False
    
    logging.info(f"merge_videos_with_ffmpeg called with dimensions parameter: {dimensions}")
    
    try:
        # Check if videos are identical and we can skip normalization for faster processing
        if not dimensions:
            identical, message = check_videos_identical(video_paths)
            if identical:
                skip_normalization = True
                videos_to_merge = video_paths
                logging.info("Videos are identical - skipping normalization step")
            else:
                # Videos have different properties, normalization required
                logging.info(f"Videos require normalization: {message}")
        
        # Normalize videos if needed (different properties or custom dimensions requested)
        if not skip_normalization:
            # First, normalize all videos to have the same properties
            # This prevents freezing at transitions
            for i, video_path in enumerate(video_paths):
                normalized_path = f"{video_path}_normalized.mp4"
                
                # Build normalization command
                if dimensions:
                    try:
                        width, height = dimensions.split('x')
                        width = int(width)
                        height = int(height)
                    except:
                        return False, "Invalid dimensions format. Use format like '864x480'"
                    
                    # Scale and normalize
                    normalize_cmd = [
                        'ffmpeg',
                        '-i', video_path,
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p',
                        '-c:v', 'libx264',
                        '-c:a', 'aac',
                        '-ar', '48000',  # Audio sample rate
                        '-ac', '2',  # Audio channels (stereo)
                        '-preset', 'veryfast',
                        '-crf', '23',
                        '-g', '30',  # Set keyframe interval
                        '-keyint_min', '30',  # Minimum keyframe interval
                        '-sc_threshold', '0',  # Disable scene change detection
                        '-video_track_timescale', '30000',  # Set video timescale
                        '-y',
                        normalized_path
                    ]
                    logging.info(f"Normalizing and scaling video {i+1} to {width}x{height}")
                else:
                    # Automatically determine target dimensions from first video
                    if i == 0:
                        # Get dimensions of first video to use as target
                        logging.info(f"Detecting dimensions from first video: {video_path}")
                        success, first_dims = get_video_dimensions(video_path)
                        if not success:
                            return False, f"Could not analyze first video dimensions: {first_dims}"
                        target_width, target_height = first_dims
                        logging.info(f"Detected dimensions from first video: {first_dims}")
                        logging.info(f"Using target dimensions from first video: {target_width}x{target_height}")
                    
                    # Scale and normalize to ensure consistent dimensions
                    normalize_cmd = [
                        'ffmpeg',
                        '-i', video_path,
                        '-vf', f'scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p',
                        '-c:v', 'libx264',
                        '-c:a', 'aac',
                        '-ar', '48000',  # Audio sample rate
                        '-ac', '2',  # Audio channels (stereo)
                        '-preset', 'veryfast',
                        '-crf', '23',
                        '-g', '30',  # Set keyframe interval
                        '-keyint_min', '30',  # Minimum keyframe interval
                        '-sc_threshold', '0',  # Disable scene change detection
                        '-video_track_timescale', '30000',  # Set video timescale
                        '-y',
                        normalized_path
                    ]
                    logging.info(f"Normalizing and scaling video {i+1} to {target_width}x{target_height}")
                
                result = subprocess.run(normalize_cmd, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    # Cleanup normalized videos
                    for path in normalized_videos:
                        cleanup_file(path)
                    return False, f"Failed to normalize video {i+1}: {result.stderr}"
                
                normalized_videos.append(normalized_path)
            
            # Use normalized videos for concatenation
            videos_to_merge = normalized_videos
        
        # Safety check: ensure videos_to_merge is defined
        if 'videos_to_merge' not in locals() or not videos_to_merge:
            return False, "Internal error: No videos available for merging"
        
        # Use concat filter instead of concat demuxer for better compatibility
        # Build input arguments
        inputs = []
        for video_path in videos_to_merge:
            inputs.extend(['-i', video_path])
        
        # Build filter complex for concatenation
        num_videos = len(videos_to_merge)
        
        if audio_path:
            # Concatenate videos and use custom audio
            video_concat = ''.join([f"[{i}:v:0]" for i in range(num_videos)])
            
            # Check which videos have audio streams
            videos_with_audio = []
            for i, video_path in enumerate(videos_to_merge):
                check_cmd = ['ffprobe', '-v', 'quiet', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', video_path]
                result = subprocess.run(check_cmd, capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    videos_with_audio.append(i)
            
            if videos_with_audio:
                # When external audio is provided, completely ignore video audio
                filter_complex = f"{video_concat}concat=n={num_videos}:v=1:a=0[outv]"
                
                cmd = [
                    'ffmpeg'
                ] + inputs + [
                    '-i', audio_path,
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-map', f'{num_videos}:a:0',  # Use only external audio
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-preset', 'veryfast',
                    '-crf', '23',
                    '-b:a', '192k',
                    '-y',
                    output_path
                ]
            else:
                # No videos have audio, just use custom audio
                filter_complex = f"{video_concat}concat=n={num_videos}:v=1:a=0[outv]"
                
                cmd = [
                    'ffmpeg'
                ] + inputs + [
                    '-i', audio_path,
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-map', f'{num_videos}:a',
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-preset', 'veryfast',
                    '-crf', '23',
                    '-b:a', '192k',
                    '-y',
                    output_path
                ]
        else:
            # Check which videos have audio streams
            videos_with_audio = []
            for i, video_path in enumerate(videos_to_merge):
                # Check if video has audio stream
                check_cmd = ['ffprobe', '-v', 'quiet', '-select_streams', 'a', '-show_entries', 'stream=index', '-of', 'csv=p=0', video_path]
                result = subprocess.run(check_cmd, capture_output=True, text=True)
                if result.returncode == 0 and result.stdout.strip():
                    videos_with_audio.append(i)
            
            # Build filter complex based on audio stream availability
            video_concat = ''.join([f"[{i}:v:0]" for i in range(num_videos)])
            
            if videos_with_audio:
                # Only concatenate audio from videos that have audio streams
                audio_concat = ''.join([f"[{i}:a:0]" for i in videos_with_audio])
                filter_complex = f"{video_concat}concat=n={num_videos}:v=1:a=0[outv];{audio_concat}concat=n={len(videos_with_audio)}:v=0:a=1[outa]"
                
                cmd = [
                    'ffmpeg'
                ] + inputs + [
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-map', '[outa]',
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-preset', 'veryfast',
                    '-crf', '23',
                    '-y',
                    output_path
                ]
            else:
                # No videos have audio, just concatenate video
                filter_complex = f"{video_concat}concat=n={num_videos}:v=1:a=0[outv]"
                
                cmd = [
                    'ffmpeg'
                ] + inputs + [
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',
                    '-crf', '23',
                    '-y',
                    output_path
                ]
        
        logging.info(f"Running FFMPEG concat filter command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 minutes
        
        if result.returncode == 0:
            logging.info("Video merge processing completed successfully")
            # Cleanup normalized videos if any
            for path in normalized_videos:
                cleanup_file(path)
            return True, "Videos merged successfully"
        else:
            logging.error(f"FFMPEG merge error: {result.stderr}")
            # Cleanup normalized videos if any
            for path in normalized_videos:
                cleanup_file(path)
            return False, f"Video merge failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("Video merge processing timed out")
        if temp_list_path:
            cleanup_file(temp_list_path)
        # Cleanup normalized videos if any
        for path in normalized_videos:
            cleanup_file(path)
        return False, "Video merge processing timed out"
    except Exception as e:
        logging.error(f"Video merge processing error: {str(e)}")
        if temp_list_path:
            cleanup_file(temp_list_path)
        # Cleanup normalized videos if any
        for path in normalized_videos:
            cleanup_file(path)
        return False, f"Video merge error: {str(e)}"
```

### 6. Subtitle Function

```python
def add_subtitles_with_ffmpeg(video_path, subtitle_path, output_path):
    """Add subtitles to video using FFMPEG"""
    try:
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vf', f'subtitles={subtitle_path}',
            '-c:a', 'copy',
            '-y',
            output_path
        ]
        
        logging.info(f"Running FFMPEG subtitle command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 minutes
        
        if result.returncode == 0:
            logging.info("Subtitle processing completed successfully")
            return True, "Subtitles added successfully"
        else:
            logging.error(f"FFMPEG subtitle error: {result.stderr}")
            return False, f"Subtitle processing failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("Subtitle processing timed out")
        return False, "Subtitle processing timed out"
    except Exception as e:
        logging.error(f"Subtitle processing error: {str(e)}")
        return False, f"Subtitle error: {str(e)}"
```

### 7. Watermark Function

```python
def add_watermark_with_ffmpeg(video_path, watermark_path, output_path):
    """Add watermark to video using FFMPEG with dynamic scaling"""
    try:
        # Create filter that scales watermark to 30% of video width and positions at bottom right
        # The watermark is scaled dynamically and positioned with 20px padding from edges
        watermark_filter = (
            f"[1:v]scale=iw*0.3:-1[watermark];"
            f"[0:v][watermark]overlay=main_w-overlay_w-20:main_h-overlay_h-20"
        )
        
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', watermark_path,
            '-filter_complex', watermark_filter,
            '-c:a', 'copy',
            '-y',
            output_path
        ]
        
        logging.info(f"Running FFMPEG watermark command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 minutes
        
        if result.returncode == 0:
            logging.info("Watermark processing completed successfully")
            return True, "Watermark added successfully"
        else:
            logging.error(f"FFMPEG watermark error: {result.stderr}")
            return False, f"Watermark processing failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("Watermark processing timed out")
        return False, "Watermark processing timed out"
    except Exception as e:
        logging.error(f"Watermark processing error: {str(e)}")
        return False, f"Watermark error: {str(e)}"
```

---

## Complete Code Reference

### Flask Endpoint Implementation

```python
from flask import Flask, request, jsonify, send_from_directory
import os
import uuid
import logging

app = Flask(__name__)

# Configuration
UPLOAD_FOLDER = '/tmp/uploads'
OUTPUT_FOLDER = '/tmp/outputs'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Include all helper functions from above here...

@app.route('/api/merge_videos', methods=['POST'])
def merge_videos():
    """API endpoint to merge multiple videos from URLs"""
    try:
        data = request.get_json()
        
        if not data or 'video_urls' not in data:
            return jsonify({
                'success': False,
                'error': 'video_urls is required'
            }), 400
        
        video_urls = data['video_urls']
        audio_url = data.get('audio_url')  # Optional audio override
        dimensions = data.get('dimensions')  # Optional output dimensions
        subtitle_url = data.get('subtitle_url')  # Optional subtitle file URL
        watermark_url = data.get('watermark_url')  # Optional watermark image URL
        
        if not isinstance(video_urls, list) or len(video_urls) < 2:
            return jsonify({
                'success': False,
                'error': 'At least 2 video URLs are required'
            }), 400
        
        # Generate unique ID for this request
        request_id = str(uuid.uuid4())
        downloaded_videos = []
        audio_path = None
        subtitle_path = None
        watermark_path = None
        
        try:
            # Download all videos
            for i, url in enumerate(video_urls):
                video_filename = f"{request_id}_video_{i}.mp4"
                video_path = os.path.join(UPLOAD_FOLDER, video_filename)
                
                success, message = download_video_from_url(url, video_path)
                if not success:
                    # Cleanup any downloaded files
                    for path in downloaded_videos:
                        cleanup_file(path)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download video {i+1}: {message}'
                    }), 400
                
                downloaded_videos.append(video_path)
            
            # Download audio if provided
            if audio_url:
                audio_filename = f"{request_id}_audio.mp3"
                audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
                
                success, message = download_video_from_url(audio_url, audio_path)
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download audio: {message}'
                    }), 400
            
            # Download subtitle if provided
            if subtitle_url:
                subtitle_filename = f"{request_id}_subtitle.ass"
                subtitle_path = os.path.join(UPLOAD_FOLDER, subtitle_filename)
                
                success, message = download_file_from_url(subtitle_url, subtitle_path, "subtitle")
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    if audio_path:
                        cleanup_file(audio_path)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download subtitle: {message}'
                    }), 400
            
            # Download watermark if provided
            if watermark_url:
                watermark_filename = f"{request_id}_watermark.png"
                watermark_path = os.path.join(UPLOAD_FOLDER, watermark_filename)
                
                success, message = download_file_from_url(watermark_url, watermark_path, "watermark image")
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    if audio_path:
                        cleanup_file(audio_path)
                    if subtitle_path:
                        cleanup_file(subtitle_path)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download watermark: {message}'
                    }), 400
            
            # Generate output filename
            output_filename = f"{request_id}_merged_videos.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Merge videos using FFMPEG
            success, message = merge_videos_with_ffmpeg(downloaded_videos, output_path, audio_path, dimensions)
            
            # Cleanup downloaded files
            for path in downloaded_videos:
                cleanup_file(path)
            if audio_path:
                cleanup_file(audio_path)
            
            # Track whether subtitles were successfully processed
            subtitles_added = False
            
            # Add subtitles if provided and merge was successful
            if success and subtitle_path:
                subtitled_filename = f"{request_id}_merged_subtitled_videos.mp4"
                subtitled_output_path = os.path.join(OUTPUT_FOLDER, subtitled_filename)
                
                subtitle_success, subtitle_message = add_subtitles_with_ffmpeg(output_path, subtitle_path, subtitled_output_path)
                cleanup_file(subtitle_path)
                
                if subtitle_success:
                    cleanup_file(output_path)
                    output_path = subtitled_output_path
                    output_filename = subtitled_filename
                    message = f"Videos merged and subtitles added successfully"
                    subtitles_added = True
                else:
                    cleanup_file(subtitled_output_path)
                    return jsonify({
                        'success': False,
                        'error': f'Video merge succeeded but subtitle addition failed: {subtitle_message}'
                    }), 500
            elif subtitle_path:
                cleanup_file(subtitle_path)
            
            # Add watermark if provided and processing was successful
            if success and watermark_path:
                watermarked_filename = f"{request_id}_merged_watermarked_videos.mp4"
                watermarked_output_path = os.path.join(OUTPUT_FOLDER, watermarked_filename)
                
                watermark_success, watermark_message = add_watermark_with_ffmpeg(output_path, watermark_path, watermarked_output_path)
                cleanup_file(watermark_path)
                
                if watermark_success:
                    cleanup_file(output_path)
                    output_path = watermarked_output_path
                    output_filename = watermarked_filename
                    if subtitles_added:
                        message = f"Videos merged, subtitles and watermark added successfully"
                    else:
                        message = f"Videos merged and watermark added successfully"
                else:
                    cleanup_file(watermarked_output_path)
                    return jsonify({
                        'success': False,
                        'error': f'Video processing succeeded but watermark addition failed: {watermark_message}'
                    }), 500
            elif watermark_path:
                cleanup_file(watermark_path)
            
            if success:
                # Generate download URL (adjust based on your setup)
                download_url = f"/download/{output_filename}"
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'download_url': download_url,
                    'filename': output_filename
                })
            else:
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
        
        except Exception as e:
            # Cleanup any downloaded files on error
            for path in downloaded_videos:
                cleanup_file(path)
            if audio_path:
                cleanup_file(audio_path)
            if subtitle_path:
                cleanup_file(subtitle_path)
            if watermark_path:
                cleanup_file(watermark_path)
            raise e
            
    except Exception as e:
        logging.error(f"Error in merge_videos: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500


@app.route('/download/<filename>')
def download_file(filename):
    """Serve merged video files for download"""
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
```

---

## Usage Examples

### Python Example (requests library)

```python
import requests

url = "http://localhost:5000/api/merge_videos"

payload = {
    "video_urls": [
        "https://example.com/video1.mp4",
        "https://example.com/video2.mp4"
    ],
    "dimensions": "1280x720"
}

response = requests.post(url, json=payload)
result = response.json()

if result['success']:
    print(f"Download URL: {result['download_url']}")
else:
    print(f"Error: {result['error']}")
```

### cURL Example

```bash
curl -X POST http://localhost:5000/api/merge_videos \
  -H "Content-Type: application/json" \
  -d '{
    "video_urls": [
      "https://example.com/video1.mp4",
      "https://example.com/video2.mp4",
      "https://example.com/video3.mp4"
    ],
    "audio_url": "https://example.com/music.mp3",
    "dimensions": "1920x1080",
    "watermark_url": "https://example.com/logo.png"
  }'
```

---

## Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| `video_urls is required` | Missing video_urls in request body | Include video_urls array in JSON |
| `At least 2 video URLs are required` | Less than 2 videos provided | Provide 2+ video URLs |
| `Failed to download video X` | Video URL inaccessible or invalid | Verify URL is publicly accessible |
| `Invalid dimensions format` | Wrong dimensions format | Use format like `1280x720` |
| `Video merge failed` | FFMPEG error during merge | Check video compatibility and FFMPEG logs |
| `Video merge processing timed out` | Processing took > 30 minutes | Split into smaller batches or use shorter videos |

---

## Processing Timeouts

| Operation | Timeout |
|-----------|---------|
| Video normalization | 5 minutes per video |
| Video merge (concat) | 30 minutes |
| Subtitle burning | 30 minutes |
| Watermark overlay | 30 minutes |

---

## Notes for Implementation

1. **Local Processing**: Since this runs locally, you won't have network latency for the FFMPEG processing, which should significantly reduce timeouts for larger videos.

2. **Storage Management**: Implement a cleanup job to periodically remove old output files from `OUTPUT_FOLDER` to prevent disk space issues.

3. **Memory Considerations**: Large videos require significant RAM. Monitor memory usage during processing.

4. **FFMPEG Version**: Ensure you have a recent version of FFMPEG (4.0+) for best compatibility.

5. **Subtitle Format**: This implementation expects ASS/SSA format subtitles. For SRT support, you may need to adjust the FFMPEG command.
