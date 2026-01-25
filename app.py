import os
import logging
import subprocess
import uuid
import tempfile
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import mimetypes

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

def allowed_file(filename, allowed_extensions):
    """Check if file has allowed extension"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def validate_file_type(file_path, expected_type):
    """Validate file type using mimetypes"""
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        return False
    
    if expected_type == 'image':
        return mime_type.startswith('image/')
    elif expected_type == 'audio':
        return mime_type.startswith('audio/')
    
    return False

def cleanup_file(file_path):
    """Safely remove a file"""
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Cleaned up file: {file_path}")
    except Exception as e:
        logging.error(f"Failed to cleanup file {file_path}: {str(e)}")

def create_video_with_ffmpeg(image_path, audio_path, output_path):
    """Create video by merging image and audio using FFMPEG"""
    try:
        # FFMPEG command to create video from image and audio
        cmd = [
            'ffmpeg',
            '-loop', '1',  # Loop the image
            '-i', image_path,  # Input image
            '-i', audio_path,  # Input audio
            '-c:v', 'libx264',  # Video codec
            '-c:a', 'aac',  # Audio codec
            '-b:a', '192k',  # Audio bitrate
            '-pix_fmt', 'yuv420p',  # Pixel format for compatibility
            '-shortest',  # End when shortest input ends (audio)
            '-y',  # Overwrite output file
            output_path
        ]
        
        logging.info(f"Running FFMPEG command: {' '.join(cmd)}")
        
        # Run FFMPEG
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            logging.info("FFMPEG processing completed successfully")
            return True, "Video created successfully"
        else:
            logging.error(f"FFMPEG error: {result.stderr}")
            return False, f"FFMPEG processing failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("FFMPEG processing timed out")
        return False, "Video processing timed out"
    except Exception as e:
        logging.error(f"FFMPEG processing error: {str(e)}")
        return False, f"Video processing error: {str(e)}"

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

def check_video_compatibility(video_paths):
    """Check if all videos have compatible dimensions"""
    dimensions = []
    
    for i, video_path in enumerate(video_paths):
        success, result = get_video_dimensions(video_path)
        if not success:
            return False, f"Could not analyze video {i+1}: {result}"
        
        dimensions.append(result)
        logging.info(f"Video {i+1} dimensions: {result[0]}x{result[1]}")
    
    # Check if all videos have the same dimensions
    first_dimensions = dimensions[0]
    for i, dims in enumerate(dimensions[1:], 1):
        if dims != first_dimensions:
            # Calculate aspect ratios for better error message
            first_ratio = round(first_dimensions[0] / first_dimensions[1], 2)
            current_ratio = round(dims[0] / dims[1], 2)
            
            return False, (
                f"Videos have different aspect ratios and cannot be merged:\n"
                f"• Video 1: {first_dimensions[0]}x{first_dimensions[1]} (aspect ratio {first_ratio}:1)\n"
                f"• Video {i+1}: {dims[0]}x{dims[1]} (aspect ratio {current_ratio}:1)\n\n"
                f"Please use videos with the same aspect ratio for best results."
            )
    
    return True, "All videos are compatible"

def merge_videos_with_ffmpeg(video_paths, output_path, audio_path=None):
    """Merge multiple videos using FFMPEG"""
    temp_list_path = None
    try:
        # First, let's try the simple concat approach
        temp_list_path = f"{output_path}.txt"
        
        with open(temp_list_path, 'w') as f:
            for video_path in video_paths:
                # Escape single quotes in file paths for FFMPEG
                escaped_path = video_path.replace("'", "'\"'\"'")
                f.write(f"file '{escaped_path}'\n")
        
        # If audio is provided, we need a more complex command
        if audio_path:
            # First, merge videos without audio using re-encoding for compatibility
            temp_video_path = f"{output_path}_temp.mp4"
            temp_cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', temp_list_path,
                '-c:v', 'libx264',  # Re-encode video for compatibility
                '-c:a', 'aac',      # Re-encode audio
                '-preset', 'fast',  # Fast encoding preset
                '-an',  # Remove audio from concatenated video
                '-y',
                temp_video_path
            ]
            
            logging.info(f"Running FFMPEG concat command: {' '.join(temp_cmd)}")
            result = subprocess.run(temp_cmd, capture_output=True, text=True, timeout=1800)  # 30 minutes for large videos
            
            if result.returncode != 0:
                # If concat fails, try the filter_complex approach
                logging.warning("Concat method failed, trying filter_complex approach")
                return merge_videos_filter_complex(video_paths, output_path, audio_path)
            
            # Then add the audio to the concatenated video
            final_cmd = [
                'ffmpeg',
                '-i', temp_video_path,
                '-i', audio_path,
                '-c:v', 'copy',  # Copy video without re-encoding
                '-c:a', 'aac',   # Encode audio as AAC
                '-b:a', '192k',  # Audio bitrate
                '-shortest',     # End when shortest input ends
                '-y',
                output_path
            ]
            
            logging.info(f"Running FFMPEG audio merge command: {' '.join(final_cmd)}")
            result = subprocess.run(final_cmd, capture_output=True, text=True, timeout=300)
            
            # Cleanup temporary files
            cleanup_file(temp_list_path)
            cleanup_file(temp_video_path)
            
        else:
            # Try simple concat first with re-encoding for compatibility
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', temp_list_path,
                '-c:v', 'libx264',  # Re-encode video for compatibility
                '-c:a', 'aac',      # Re-encode audio
                '-preset', 'fast',  # Fast encoding preset
                '-y',
                output_path
            ]
            
            logging.info(f"Running FFMPEG concat command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 minutes for large videos
            
            if result.returncode != 0:
                # If concat fails, try the filter_complex approach
                logging.warning("Concat method failed, trying filter_complex approach")
                cleanup_file(temp_list_path)
                return merge_videos_filter_complex(video_paths, output_path, audio_path)
                
            cleanup_file(temp_list_path)
        
        if result.returncode == 0:
            logging.info("Video merge processing completed successfully")
            return True, "Videos merged successfully"
        else:
            logging.error(f"FFMPEG merge error: {result.stderr}")
            return False, f"Video merge failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("Video merge processing timed out")
        if temp_list_path:
            cleanup_file(temp_list_path)
        return False, "Video merge processing timed out"
    except Exception as e:
        logging.error(f"Video merge processing error: {str(e)}")
        if temp_list_path:
            cleanup_file(temp_list_path)
        return False, f"Video merge error: {str(e)}"

def merge_videos_filter_complex(video_paths, output_path, audio_path=None):
    """Alternative video merging using filter_complex (more compatible but slower)"""
    try:
        # Build filter_complex command for concatenating videos
        inputs = []
        for video_path in video_paths:
            inputs.extend(['-i', video_path])
        
        # Create filter_complex string
        num_videos = len(video_paths)
        video_filters = []
        audio_filters = []
        
        for i in range(num_videos):
            video_filters.append(f"[{i}:v]")
            audio_filters.append(f"[{i}:a]")
        
        filter_complex = f"{''.join(video_filters)}concat=n={num_videos}:v=1:a=1[outv][outa]"
        
        if audio_path:
            # If custom audio is provided, only use video streams
            filter_complex = f"{''.join(video_filters)}concat=n={num_videos}:v=1:a=0[outv]"
            
            cmd = [
                'ffmpeg'
            ] + inputs + [
                '-i', audio_path,
                '-filter_complex', filter_complex,
                '-map', '[outv]',
                '-map', f'{num_videos}:a',  # Map the custom audio file
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-preset', 'fast',
                '-b:a', '192k',
                '-shortest',
                '-y',
                output_path
            ]
        else:
            cmd = [
                'ffmpeg'
            ] + inputs + [
                '-filter_complex', filter_complex,
                '-map', '[outv]',
                '-map', '[outa]',
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-preset', 'fast',
                '-y',
                output_path
            ]
        
        logging.info(f"Running FFMPEG filter_complex command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30 minutes for large/high-res videos
        
        if result.returncode == 0:
            logging.info("Video merge with filter_complex completed successfully")
            return True, "Videos merged successfully using advanced method"
        else:
            logging.error(f"FFMPEG filter_complex error: {result.stderr}")
            return False, f"Video merge failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("Video merge with filter_complex timed out")
        return False, "Video merge processing timed out"
    except Exception as e:
        logging.error(f"Video merge with filter_complex error: {str(e)}")
        return False, f"Video merge error: {str(e)}"

def create_picture_in_picture_with_ffmpeg(main_video_path, pip_video_path, output_path, position='bottom-right', scale='iw/4:ih/4'):
    """Create picture-in-picture video using FFMPEG"""
    try:
        # Position mappings
        position_overlays = {
            'top-left': '10:10',
            'top-center': '(main_w-overlay_w)/2:10', 
            'top-right': 'main_w-overlay_w-10:10',
            'middle-left': '10:(main_h-overlay_h)/2',
            'middle': '(main_w-overlay_w)/2:(main_h-overlay_h)/2',
            'middle-right': 'main_w-overlay_w-10:(main_h-overlay_h)/2',
            'bottom-left': '10:main_h-overlay_h-10',
            'bottom-center': '(main_w-overlay_w)/2:main_h-overlay_h-10',
            'bottom-right': 'main_w-overlay_w-10:main_h-overlay_h-10'
        }
        
        overlay_position = position_overlays.get(position, position_overlays['bottom-right'])
        
        # Build FFMPEG command for picture-in-picture
        cmd = [
            'ffmpeg',
            '-i', main_video_path,
            '-i', pip_video_path,
            '-filter_complex', f'[1]scale={scale}[pip];[0][pip]overlay={overlay_position}',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'fast',
            '-y',
            output_path
        ]
        
        logging.info(f"Running FFMPEG PiP command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        
        if result.returncode == 0:
            logging.info("Picture-in-picture processing completed successfully")
            return True, "Picture-in-picture video created successfully"
        else:
            logging.error(f"FFMPEG PiP error: {result.stderr}")
            return False, f"Picture-in-picture creation failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("Picture-in-picture processing timed out")
        return False, "Picture-in-picture processing timed out"
    except Exception as e:
        logging.error(f"Picture-in-picture processing error: {str(e)}")
        return False, f"Picture-in-picture error: {str(e)}"

@app.route('/')
def index():
    """Main page with upload form"""
    return render_template('index.html')

@app.route('/api/merge_image_audio', methods=['POST'])
def merge_image_audio():
    """API endpoint to merge image and audio into video"""
    try:
        # Check if files are present
        if 'image' not in request.files or 'audio' not in request.files:
            return jsonify({
                'success': False,
                'error': 'Both image and audio files are required'
            }), 400

        image_file = request.files['image']
        audio_file = request.files['audio']

        # Check if files are selected
        if image_file.filename == '' or audio_file.filename == '':
            return jsonify({
                'success': False,
                'error': 'Please select both image and audio files'
            }), 400

        # Validate file extensions
        if not allowed_file(image_file.filename, ALLOWED_IMAGE_EXTENSIONS):
            return jsonify({
                'success': False,
                'error': f'Invalid image format. Allowed: {", ".join(ALLOWED_IMAGE_EXTENSIONS).upper()}'
            }), 400

        if not allowed_file(audio_file.filename, ALLOWED_AUDIO_EXTENSIONS):
            return jsonify({
                'success': False,
                'error': f'Invalid audio format. Allowed: {", ".join(ALLOWED_AUDIO_EXTENSIONS).upper()}'
            }), 400

        # Generate unique filenames
        unique_id = str(uuid.uuid4())
        image_filename = f"{unique_id}_{secure_filename(image_file.filename or 'image')}"
        audio_filename = f"{unique_id}_{secure_filename(audio_file.filename or 'audio')}"
        output_filename = f"{unique_id}_output.mp4"

        # Save uploaded files
        image_path = os.path.join(UPLOAD_FOLDER, image_filename)
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        image_file.save(image_path)
        audio_file.save(audio_path)

        # Validate file types
        if not validate_file_type(image_path, 'image'):
            cleanup_file(image_path)
            cleanup_file(audio_path)
            return jsonify({
                'success': False,
                'error': 'Uploaded image file is not a valid image format'
            }), 400

        if not validate_file_type(audio_path, 'audio'):
            cleanup_file(image_path)
            cleanup_file(audio_path)
            return jsonify({
                'success': False,
                'error': 'Uploaded audio file is not a valid audio format'
            }), 400

        # Process with FFMPEG
        success, message = create_video_with_ffmpeg(image_path, audio_path, output_path)

        # Cleanup input files
        cleanup_file(image_path)
        cleanup_file(audio_path)

        if success:
            # Return success response with download URL
            download_url = url_for('download_video', filename=output_filename, _external=True)
            return jsonify({
                'success': True,
                'message': message,
                'download_url': download_url,
                'filename': output_filename
            })
        else:
            # Cleanup output file on failure
            cleanup_file(output_path)
            return jsonify({
                'success': False,
                'error': message
            }), 500

    except RequestEntityTooLarge:
        return jsonify({
            'success': False,
            'error': 'File too large. Maximum size allowed is 100MB'
        }), 413
    except Exception as e:
        logging.error(f"Unexpected error in merge_files: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An unexpected error occurred during processing'
        }), 500

@app.route('/api/merge_videos', methods=['POST'])
def merge_videos():
    """API endpoint to merge multiple videos from URLs"""
    try:
        # Get video URLs from form data
        video_urls = []
        for key, value in request.form.items():
            if key.startswith('video_url_'):
                if value.strip():
                    video_urls.append(value.strip())
        
        if len(video_urls) < 2:
            return jsonify({
                'success': False,
                'error': 'At least 2 video URLs are required'
            }), 400
        
        # Check for optional audio file
        audio_file = None
        audio_path = None
        if 'audio' in request.files and request.files['audio'].filename != '':
            audio_file = request.files['audio']
            if not allowed_file(audio_file.filename, ALLOWED_AUDIO_EXTENSIONS):
                return jsonify({
                    'success': False,
                    'error': f'Invalid audio format. Allowed: {", ".join(ALLOWED_AUDIO_EXTENSIONS).upper()}'
                }), 400

        # Generate unique ID for this operation
        unique_id = str(uuid.uuid4())
        
        # Download all videos
        downloaded_videos = []
        temp_files_to_cleanup = []
        
        try:
            for i, url in enumerate(video_urls):
                video_filename = f"{unique_id}_video_{i}.mp4"
                video_path = os.path.join(UPLOAD_FOLDER, video_filename)
                
                success, message = download_video_from_url(url, video_path)
                if not success:
                    # Cleanup any downloaded files
                    for temp_file in temp_files_to_cleanup:
                        cleanup_file(temp_file)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download video {i+1}: {message}'
                    }), 400
                
                downloaded_videos.append(video_path)
                temp_files_to_cleanup.append(video_path)
            
            # Handle optional audio file
            if audio_file:
                audio_filename = f"{unique_id}_{secure_filename(audio_file.filename or 'audio')}"
                audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
                audio_file.save(audio_path)
                temp_files_to_cleanup.append(audio_path)
                
                # Validate audio file type
                if not validate_file_type(audio_path, 'audio'):
                    for temp_file in temp_files_to_cleanup:
                        cleanup_file(temp_file)
                    return jsonify({
                        'success': False,
                        'error': 'Uploaded audio file is not a valid audio format'
                    }), 400
            
            # Check video compatibility (aspect ratios)
            compatibility_success, compatibility_message = check_video_compatibility(downloaded_videos)
            if not compatibility_success:
                for temp_file in temp_files_to_cleanup:
                    cleanup_file(temp_file)
                return jsonify({
                    'success': False,
                    'error': compatibility_message
                }), 400
            
            # Generate output filename
            output_filename = f"{unique_id}_merged_output.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Merge videos
            success, message = merge_videos_with_ffmpeg(downloaded_videos, output_path, audio_path)
            
            # Cleanup temporary files
            for temp_file in temp_files_to_cleanup:
                cleanup_file(temp_file)
            
            if success:
                # Return success response with download URL
                download_url = url_for('download_video', filename=output_filename, _external=True)
                return jsonify({
                    'success': True,
                    'message': message,
                    'download_url': download_url,
                    'filename': output_filename
                })
            else:
                # Cleanup output file on failure
                cleanup_file(output_path)
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as processing_error:
            # Cleanup any temporary files on error
            for temp_file in temp_files_to_cleanup:
                cleanup_file(temp_file)
            raise processing_error
            
    except Exception as e:
        logging.error(f"Unexpected error in merge_videos: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An unexpected error occurred during video processing'
        }), 500

@app.route('/download/<filename>')
def download_video(filename):
    """Serve generated videos for download"""
    try:
        return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({
            'success': False,
            'error': 'Video file not found'
        }), 404

@app.route('/api/picture_in_picture', methods=['POST'])
def picture_in_picture():
    """API endpoint to create picture-in-picture video from two URLs"""
    try:
        # Get video URLs from form data
        main_video_url = request.form.get('main_video_url', '').strip()
        pip_video_url = request.form.get('pip_video_url', '').strip()
        
        if not main_video_url or not pip_video_url:
            return jsonify({
                'success': False,
                'error': 'Both main video URL and picture-in-picture video URL are required'
            }), 400
        
        # Get optional parameters
        position = request.form.get('position', 'bottom-right')
        scale = request.form.get('scale', 'iw/4:ih/4')
        
        # Generate unique ID for this operation
        unique_id = str(uuid.uuid4())
        
        # Download both videos
        temp_files_to_cleanup = []
        
        try:
            # Download main video
            main_video_filename = f"{unique_id}_main_video.mp4"
            main_video_path = os.path.join(UPLOAD_FOLDER, main_video_filename)
            
            success, message = download_video_from_url(main_video_url, main_video_path)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'Failed to download main video: {message}'
                }), 400
            
            temp_files_to_cleanup.append(main_video_path)
            
            # Download PiP video
            pip_video_filename = f"{unique_id}_pip_video.mp4"
            pip_video_path = os.path.join(UPLOAD_FOLDER, pip_video_filename)
            
            success, message = download_video_from_url(pip_video_url, pip_video_path)
            if not success:
                for temp_file in temp_files_to_cleanup:
                    cleanup_file(temp_file)
                return jsonify({
                    'success': False,
                    'error': f'Failed to download picture-in-picture video: {message}'
                }), 400
            
            temp_files_to_cleanup.append(pip_video_path)
            
            # Generate output filename
            output_filename = f"{unique_id}_pip_output.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Create picture-in-picture video
            success, message = create_picture_in_picture_with_ffmpeg(
                main_video_path, pip_video_path, output_path, position, scale
            )
            
            # Cleanup temporary files
            for temp_file in temp_files_to_cleanup:
                cleanup_file(temp_file)
            
            if success:
                # Return success response with download URL
                download_url = url_for('download_video', filename=output_filename, _external=True)
                return jsonify({
                    'success': True,
                    'message': message,
                    'download_url': download_url,
                    'filename': output_filename
                })
            else:
                # Cleanup output file on failure
                cleanup_file(output_path)
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as processing_error:
            # Cleanup any temporary files on error
            for temp_file in temp_files_to_cleanup:
                cleanup_file(temp_file)
            raise processing_error
            
    except Exception as e:
        logging.error(f"Unexpected error in picture_in_picture: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'An unexpected error occurred during picture-in-picture processing'
        }), 500

@app.route('/api/cleanup/<filename>', methods=['POST'])
def cleanup_video(filename):
    """Clean up generated video file"""
    try:
        file_path = os.path.join(OUTPUT_FOLDER, secure_filename(filename))
        cleanup_file(file_path)
        return jsonify({
            'success': True,
            'message': 'File cleaned up successfully'
        })
    except Exception as e:
        logging.error(f"Cleanup error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Failed to cleanup file'
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
