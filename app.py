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

@app.route('/')
def index():
    """Main page with upload form"""
    return render_template('index.html')

@app.route('/api/merge', methods=['POST'])
def merge_files():
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
        image_filename = f"{unique_id}_{secure_filename(image_file.filename)}"
        audio_filename = f"{unique_id}_{secure_filename(audio_file.filename)}"
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
