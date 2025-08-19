import os
import logging
import subprocess
import uuid
import tempfile
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for, flash, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
import mimetypes

from models import db, User, ApiKey, SubscriptionPlan, StripeSettings, UserSubscription, SITE_DEFAULT_API_KEY
from forms import RegistrationForm, LoginForm, ApiKeyForm
from auth_routes import auth
from stripe_routes import stripe_bp

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    'pool_pre_ping': True,
    "pool_recycle": 300,
}

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Register blueprints
app.register_blueprint(auth, url_prefix='/auth')

# Import and register admin blueprint
from admin_routes import admin_bp
app.register_blueprint(admin_bp)

# Register Stripe blueprint
app.register_blueprint(stripe_bp)

# Create tables and default data
with app.app_context():
    db.create_all()
    
    # Create default site user and API key if they don't exist
    site_user = User.query.filter_by(username='site_default').first()
    if not site_user:
        site_user = User()
        site_user.username = 'site_default'
        site_user.email = 'site@ffmpegapi.com'
        site_user.set_password('site_default_password')
        db.session.add(site_user)
        db.session.commit()
        
        # Create default API key for site use
        default_key = ApiKey()
        default_key.key = SITE_DEFAULT_API_KEY
        default_key.name = 'Site Default'
        default_key.user_id = site_user.id
        db.session.add(default_key)
        db.session.commit()
        
        logging.info(f"Created default site API key: {SITE_DEFAULT_API_KEY}")

def require_api_key(f):
    """Decorator to require API key for API endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for API key in header, query param, or form data
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key') or request.form.get('api_key')
        
        if not api_key:
            return jsonify({
                'success': False,
                'error': 'API key is required. Please provide it in X-API-Key header, api_key query parameter, or form data.'
            }), 401
        
        # Validate API key
        key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
        if not key_record:
            return jsonify({
                'success': False,
                'error': 'Invalid or inactive API key.'
            }), 401
        
        # Mark API key as used
        key_record.mark_used()
        
        return f(*args, **kwargs)
    
    return decorated_function

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
            result = subprocess.run(temp_cmd, capture_output=True, text=True, timeout=600)
            
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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
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
        cleanup_file(temp_list_path)
        return False, "Video merge processing timed out"
    except Exception as e:
        logging.error(f"Video merge processing error: {str(e)}")
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)  # Longer timeout for complex processing
        
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

def create_picture_in_picture_with_ffmpeg(main_video_path, pip_video_path, output_path, position='bottom-right', scale='iw/4:ih/4', audio_option='video1'):
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
        
        # Build FFMPEG command for picture-in-picture with audio options
        cmd = [
            'ffmpeg',
            '-i', main_video_path,   # Input 0: main video
            '-i', pip_video_path,    # Input 1: pip video
            '-filter_complex', f'[1]scale={scale}[pip];[0][pip]overlay={overlay_position}',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-y'
        ]
        
        # Handle different audio options
        if audio_option == 'mute':
            # No audio output
            cmd.extend(['-an'])
            logging.info("PiP: Muting final video (no audio)")
        elif audio_option == 'video1':
            # Use audio from main video (input 0)
            cmd.extend(['-c:a', 'aac', '-map', '0:a'])
            logging.info("PiP: Using audio from main video")
        elif audio_option == 'video2':
            # Use audio from pip video (input 1)
            cmd.extend(['-c:a', 'aac', '-map', '1:a'])
            logging.info("PiP: Using audio from pip video")
        else:
            # Default to main video audio
            cmd.extend(['-c:a', 'aac'])
            logging.info("PiP: Using default audio handling")
        
        cmd.append(output_path)
        
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

# Routes
@app.route('/')
def index():
    """Main page with upload form and default API key for site use"""
    return render_template('index.html', default_api_key=SITE_DEFAULT_API_KEY)

@app.route('/dashboard')
@login_required
def dashboard():
    """User dashboard showing their API keys"""
    return redirect(url_for('auth.dashboard'))

@app.route('/docs')
def api_docs():
    """API documentation page - no login required"""
    return render_template('api_docs.html')

@app.route('/api/merge_image_audio', methods=['POST'])
@require_api_key
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
                'error': f'Invalid image format. Allowed formats: {", ".join(ALLOWED_IMAGE_EXTENSIONS)}'
            }), 400

        if not allowed_file(audio_file.filename, ALLOWED_AUDIO_EXTENSIONS):
            return jsonify({
                'success': False,
                'error': f'Invalid audio format. Allowed formats: {", ".join(ALLOWED_AUDIO_EXTENSIONS)}'
            }), 400

        # Generate unique filename for this request
        request_id = str(uuid.uuid4())
        
        # Save uploaded files
        image_filename = secure_filename(f"{request_id}_image_{image_file.filename}")
        audio_filename = secure_filename(f"{request_id}_audio_{audio_file.filename}")
        
        image_path = os.path.join(UPLOAD_FOLDER, image_filename)
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        
        image_file.save(image_path)
        audio_file.save(audio_path)

        # Additional validation using mimetypes
        if not validate_file_type(image_path, 'image'):
            cleanup_file(image_path)
            cleanup_file(audio_path)
            return jsonify({
                'success': False,
                'error': 'Invalid image file type'
            }), 400

        if not validate_file_type(audio_path, 'audio'):
            cleanup_file(image_path)
            cleanup_file(audio_path)
            return jsonify({
                'success': False,
                'error': 'Invalid audio file type'
            }), 400

        # Generate output filename
        output_filename = f"{request_id}_merged_video.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        # Create video using FFMPEG
        success, message = create_video_with_ffmpeg(image_path, audio_path, output_path)

        # Cleanup uploaded files
        cleanup_file(image_path)
        cleanup_file(audio_path)

        if success:
            # Return download URL
            download_url = url_for('download_file', filename=output_filename, _external=True)
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

    except RequestEntityTooLarge:
        return jsonify({
            'success': False,
            'error': 'File too large. Maximum file size is 100MB.'
        }), 413
    except Exception as e:
        logging.error(f"Error in merge_image_audio: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/merge_videos', methods=['POST'])
@require_api_key
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
        
        if not isinstance(video_urls, list) or len(video_urls) < 2:
            return jsonify({
                'success': False,
                'error': 'At least 2 video URLs are required'
            }), 400
        
        # Generate unique ID for this request
        request_id = str(uuid.uuid4())
        downloaded_videos = []
        audio_path = None
        
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
                    # Cleanup downloaded videos
                    for path in downloaded_videos:
                        cleanup_file(path)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download audio: {message}'
                    }), 400
            
            # Check video compatibility
            success, message = check_video_compatibility(downloaded_videos)
            if not success:
                # Cleanup downloaded files
                for path in downloaded_videos:
                    cleanup_file(path)
                if audio_path:
                    cleanup_file(audio_path)
                
                return jsonify({
                    'success': False,
                    'error': message
                }), 400
            
            # Generate output filename
            output_filename = f"{request_id}_merged_videos.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Merge videos using FFMPEG
            success, message = merge_videos_with_ffmpeg(downloaded_videos, output_path, audio_path)
            
            # Cleanup downloaded files
            for path in downloaded_videos:
                cleanup_file(path)
            if audio_path:
                cleanup_file(audio_path)
            
            if success:
                # Return download URL
                download_url = url_for('download_file', filename=output_filename, _external=True)
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
            raise e
            
    except Exception as e:
        logging.error(f"Error in merge_videos: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/picture_in_picture', methods=['POST'])
@require_api_key
def picture_in_picture():
    """API endpoint to create picture-in-picture video"""
    try:
        data = request.get_json()
        
        if not data or 'main_video_url' not in data or 'pip_video_url' not in data:
            return jsonify({
                'success': False,
                'error': 'main_video_url and pip_video_url are required'
            }), 400
        
        main_video_url = data['main_video_url']
        pip_video_url = data['pip_video_url']
        position = data.get('position', 'bottom-right')
        scale = data.get('scale', 'iw/4:ih/4')
        audio_option = data.get('audio_option', 'video1')
        
        # Generate unique ID for this request
        request_id = str(uuid.uuid4())
        
        # Download main video
        main_video_filename = f"{request_id}_main_video.mp4"
        main_video_path = os.path.join(UPLOAD_FOLDER, main_video_filename)
        
        success, message = download_video_from_url(main_video_url, main_video_path)
        if not success:
            return jsonify({
                'success': False,
                'error': f'Failed to download main video: {message}'
            }), 400
        
        # Download PiP video
        pip_video_filename = f"{request_id}_pip_video.mp4"
        pip_video_path = os.path.join(UPLOAD_FOLDER, pip_video_filename)
        
        success, message = download_video_from_url(pip_video_url, pip_video_path)
        if not success:
            cleanup_file(main_video_path)
            return jsonify({
                'success': False,
                'error': f'Failed to download PiP video: {message}'
            }), 400
        
        # Generate output filename
        output_filename = f"{request_id}_pip_output.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        # Create picture-in-picture video using FFMPEG
        success, message = create_picture_in_picture_with_ffmpeg(
            main_video_path, pip_video_path, output_path, position, scale, audio_option
        )
        
        # Cleanup downloaded files
        cleanup_file(main_video_path)
        cleanup_file(pip_video_path)
        
        if success:
            # Return download URL
            download_url = url_for('download_file', filename=output_filename, _external=True)
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
        logging.error(f"Error in picture_in_picture: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/download/<filename>')
def download_file(filename):
    """Download processed video file"""
    try:
        return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)
    except Exception as e:
        logging.error(f"Error downloading file {filename}: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'File not found or expired'
        }), 404

@app.route('/pricing')
def pricing():
    """Pricing page"""
    plans = SubscriptionPlan.query.filter_by(is_active=True).order_by(SubscriptionPlan.sort_order).all()
    return render_template('pricing.html', plans=plans)

@app.route('/plans')
def plans():
    """Plans page - alias for pricing"""
    return redirect(url_for('pricing'))

@app.route('/subscribe-free', methods=['POST'])
@login_required
def subscribe_free():
    """Subscribe to free plan"""
    try:

        
        # Find the free plan
        free_plan = SubscriptionPlan.query.filter_by(name='Free', is_active=True).first()
        if not free_plan:
            flash('Free plan not available', 'error')
            return redirect(url_for('pricing'))
        
        # Check if user already has a subscription
        existing_subscription = UserSubscription.query.filter_by(user_id=current_user.id).first()
        
        if existing_subscription:
            # Update existing subscription to free plan
            existing_subscription.plan_id = free_plan.id
            existing_subscription.status = 'active'
            existing_subscription.billing_cycle = 'monthly'
            existing_subscription.api_calls_used = 0
            existing_subscription.stripe_subscription_id = None
            existing_subscription.stripe_customer_id = None
            existing_subscription.current_period_start = datetime.utcnow()
            existing_subscription.current_period_end = datetime.utcnow() + timedelta(days=30)
        else:
            # Create new free subscription
            subscription = UserSubscription()
            subscription.user_id = current_user.id
            subscription.plan_id = free_plan.id
            subscription.status = 'active'
            subscription.billing_cycle = 'monthly'
            subscription.api_calls_used = 0
            subscription.current_period_start = datetime.utcnow()
            subscription.current_period_end = datetime.utcnow() + timedelta(days=30)
            db.session.add(subscription)
        
        db.session.commit()
        flash('Successfully subscribed to the Free plan!', 'success')
        
    except Exception as e:
        logging.error(f"Error subscribing to free plan: {str(e)}")
        db.session.rollback()
        flash('Error subscribing to free plan. Please try again.', 'error')
    
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)