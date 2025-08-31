import os
import logging
import subprocess
import uuid
import tempfile
import threading
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for, flash, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_required, current_user
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
import mimetypes

from models import db, User, ApiKey, SubscriptionPlan, StripeSettings, UserSubscription, SiteSettings, Job, SITE_DEFAULT_API_KEY
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

# URL building configuration for async jobs
# Use localhost for development, production domain for production
app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME', 'ffmpegapi.net')
app.config['PREFERRED_URL_SCHEME'] = os.environ.get('PREFERRED_URL_SCHEME', 'https')
app.config['APPLICATION_ROOT'] = '/'

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
login_manager.login_view = 'auth.login'  # type: ignore
login_manager.login_message = 'Please log in to access this page.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_site_settings():
    """Inject site settings into all templates"""
    try:
        settings = SiteSettings.get_settings()
        return {'site_settings': settings}
    except Exception as e:
        logging.error(f"Error loading site settings: {str(e)}")
        return {'site_settings': None}

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
    """Decorator to require API key for API endpoints with usage limit checking"""
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
        
        # Check user's subscription and API usage limits
        user = key_record.user
        subscription = UserSubscription.query.filter_by(user_id=user.id, status='active').first()
        
        if not subscription:
            # Try to assign free plan if user has no subscription
            free_plan = SubscriptionPlan.query.filter_by(name='Free', is_active=True).first()
            if free_plan:
                subscription = UserSubscription()
                subscription.user_id = user.id
                subscription.plan_id = free_plan.id
                subscription.status = 'active'
                subscription.billing_cycle = 'monthly'
                subscription.current_period_start = datetime.utcnow()
                subscription.current_period_end = datetime.utcnow() + timedelta(days=30)
                subscription.api_calls_used = 0
                
                db.session.add(subscription)
                db.session.commit()
            else:
                return jsonify({
                    'success': False,
                    'error': 'No active subscription found. Please contact support.'
                }), 403
        
        # Check if user can make API call
        if not subscription.can_make_api_call():
            plan_name = subscription.plan.name
            api_limit = subscription.plan.api_calls_per_month
            api_used = subscription.api_calls_used
            
            return jsonify({
                'success': False,
                'error': f'API call limit exceeded. You have used {api_used}/{api_limit} calls for your {plan_name} plan. Please upgrade your plan to continue using the API.',
                'current_plan': plan_name,
                'api_calls_used': api_used,
                'api_calls_limit': api_limit,
                'upgrade_url': url_for('pricing', _external=True)
            }), 429
        
        # Increment API usage
        subscription.increment_api_usage()
        
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
        
        # First try a safer approach - check if all videos have both video and audio streams
        # Build a more robust filter that handles stream mapping better
        filter_parts = []
        for i in range(num_videos):
            filter_parts.append(f"[{i}:v:0]")  # Explicitly specify video stream 0
            filter_parts.append(f"[{i}:a:0]")  # Explicitly specify audio stream 0
        
        # Create the concat filter with explicit stream specifications
        video_concat = ''.join([f"[{i}:v:0]" for i in range(num_videos)])
        audio_concat = ''.join([f"[{i}:a:0]" for i in range(num_videos)])
        filter_complex = f"{video_concat}concat=n={num_videos}:v=1:a=0[outv];{audio_concat}concat=n={num_videos}:v=0:a=1[outa]"
        
        # Debug: Log the video paths and filter construction
        logging.info(f"Video paths input: {video_paths}")
        logging.info(f"Number of videos: {num_videos}")
        logging.info(f"Video filters: {video_filters}")
        logging.info(f"Audio filters: {audio_filters}")
        logging.info(f"New filter complex: {filter_complex}")
        
        if audio_path:
            # If custom audio is provided, only concatenate video streams
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
        logging.info(f"Filter complex string: {filter_complex}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)  # Longer timeout for complex processing
        
        if result.returncode == 0:
            logging.info("Video merge with filter_complex completed successfully")
            return True, "Videos merged successfully using advanced method"
        else:
            logging.error(f"FFMPEG filter_complex error: {result.stderr}")
            
            # Try fallback approach using the original concat demuxer method
            logging.warning("Filter_complex failed, trying concat demuxer fallback")
            try:
                # Create temporary file list for concat demuxer
                temp_list_path = f"{output_path}_fallback.txt"
                
                # Extract video paths from inputs (every other element starting from index 1)
                video_paths_extracted = []
                for i in range(1, len(inputs), 2):  # inputs are ['-i', 'path1', '-i', 'path2', ...]
                    video_paths_extracted.append(inputs[i])
                
                logging.info(f"Extracted video paths for concat: {video_paths_extracted}")
                
                with open(temp_list_path, 'w') as f:
                    for video_path in video_paths_extracted:
                        # Convert to absolute path to avoid relative path issues
                        import os
                        absolute_path = os.path.abspath(video_path)
                        # Escape single quotes in file paths for FFMPEG
                        escaped_path = absolute_path.replace("'", "'\"'\"'")
                        f.write(f"file '{escaped_path}'\n")
                        
                # Log the content of the concat file for debugging
                with open(temp_list_path, 'r') as f:
                    concat_content = f.read()
                    logging.info(f"Concat file content:\n{concat_content}")
                
                fallback_cmd = [
                    'ffmpeg',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', temp_list_path,
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-preset', 'fast',
                    '-y',
                    output_path
                ]
                
                logging.info(f"Running FFMPEG concat demuxer fallback command: {' '.join(fallback_cmd)}")
                fallback_result = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=900)
                
                # Cleanup temp file
                if os.path.exists(temp_list_path):
                    os.remove(temp_list_path)
                
                if fallback_result.returncode == 0:
                    logging.info("Video merge with concat demuxer fallback completed successfully")
                    return True, "Videos merged successfully using concat demuxer fallback"
                else:
                    logging.error(f"FFMPEG concat demuxer fallback error: {fallback_result.stderr}")
                    return False, f"Video merge failed with both methods. Filter error: {result.stderr}. Fallback error: {fallback_result.stderr}"
                    
            except Exception as e:
                logging.error(f"Fallback method error: {str(e)}")
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

def split_audio_with_ffmpeg(audio_path, output_dir, num_parts):
    """Split audio into equal parts using FFMPEG"""
    try:
        import math
        
        # First, get the duration of the audio file
        probe_cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-show_entries', 'format=duration',
            '-of', 'csv=p=0',
            audio_path
        ]
        
        logging.info(f"Getting audio duration: {' '.join(probe_cmd)}")
        probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        
        if probe_result.returncode != 0:
            logging.error(f"FFprobe error: {probe_result.stderr}")
            return False, f"Unable to get audio duration: {probe_result.stderr}", []
        
        try:
            total_duration = float(probe_result.stdout.strip())
        except ValueError:
            logging.error(f"Invalid duration value: {probe_result.stdout.strip()}")
            return False, "Invalid audio file or unable to determine duration", []
        
        if total_duration <= 0:
            return False, "Audio file appears to have zero duration", []
        
        # Calculate segment duration
        segment_duration = total_duration / num_parts
        output_files = []
        
        logging.info(f"Splitting {total_duration:.2f}s audio into {num_parts} parts of {segment_duration:.2f}s each")
        
        # Split audio into parts
        for i in range(num_parts):
            start_time = i * segment_duration
            output_filename = f"split_part_{i+1:02d}.mp3"
            output_path = os.path.join(output_dir, output_filename)
            
            # FFMPEG command to extract segment
            cmd = [
                'ffmpeg',
                '-i', audio_path,
                '-ss', str(start_time),
                '-t', str(segment_duration),
                '-c:a', 'mp3',
                '-b:a', '192k',
                '-y',
                output_path
            ]
            
            logging.info(f"Creating part {i+1}/{num_parts}: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                output_files.append(output_filename)
                logging.info(f"Successfully created audio part {i+1}")
            else:
                logging.error(f"FFMPEG error for part {i+1}: {result.stderr}")
                # Clean up already created files
                for created_file in output_files:
                    cleanup_file(os.path.join(output_dir, created_file))
                return False, f"Failed to create audio part {i+1}: {result.stderr}", []
        
        logging.info(f"Successfully split audio into {len(output_files)} parts")
        return True, f"Audio successfully split into {len(output_files)} parts", output_files
        
    except subprocess.TimeoutExpired:
        logging.error("Audio splitting timed out")
        return False, "Audio splitting processing timed out", []
    except Exception as e:
        logging.error(f"Audio splitting error: {str(e)}")
        return False, f"Audio splitting error: {str(e)}", []

# Routes
@app.route('/')
def index():
    """Main page with upload form and default API key for site use"""
    # Use user's API key if logged in, otherwise use site default
    api_key_to_use = SITE_DEFAULT_API_KEY
    
    if current_user.is_authenticated:
        # Get user's first active API key
        user_api_keys = [key for key in current_user.api_keys if key.is_active]
        if user_api_keys:
            api_key_to_use = user_api_keys[0].key
    
    return render_template('index.html', default_api_key=api_key_to_use)

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
    """API endpoint to merge image and audio into video from URLs (sync/async)"""
    try:
        # Check if async processing is requested
        async_processing = False
        if request.is_json:
            data = request.get_json()
            async_processing = data.get('async', False)
        
        # If async processing is requested, create job and return immediately
        if async_processing:
            # Get user from API key
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
            
            # Create job record
            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'merge_image_audio'
            job.status = 'pending'
            job.set_input_data(data)
            
            db.session.add(job)
            db.session.commit()
            
            # Start background processing
            thread = threading.Thread(target=process_job_async, args=(job.job_id,))
            thread.daemon = True
            thread.start()
            
            return jsonify({
                'success': True,
                'job_id': job.job_id,
                'status': 'pending',
                'message': 'Job submitted for async processing. Use /api/job/{job_id}/status to check progress.',
                'status_url': url_for('get_job_status', job_id=job.job_id, _external=True)
            }), 202
        # If not async, process synchronously
        # Require JSON data
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Content-Type must be application/json'
            }), 400
            
        data = request.get_json()
        
        if not data or 'image' not in data or 'audio' not in data:
            return jsonify({
                'success': False,
                'error': 'Both image and audio URLs are required'
            }), 400
        
        # Generate unique filename for this request
        request_id = str(uuid.uuid4())
        image_path = ""
        audio_path = ""
        
        image_url = data['image']
        audio_url = data['audio']
            
        # Validate URLs
        if not image_url or not audio_url:
            return jsonify({
                'success': False,
                'error': 'Both image and audio must be valid URLs'
            }), 400
            
        # Generate file paths for downloaded content
        image_ext = image_url.split('.')[-1].lower() if '.' in image_url else 'jpg'
        audio_ext = audio_url.split('.')[-1].lower() if '.' in audio_url else 'mp3'
        
        # Validate extensions
        if image_ext not in ALLOWED_IMAGE_EXTENSIONS:
            return jsonify({
                'success': False,
                'error': f'Invalid image format in URL. Allowed formats: {", ".join(ALLOWED_IMAGE_EXTENSIONS)}'
            }), 400
        
        if audio_ext not in ALLOWED_AUDIO_EXTENSIONS:
            return jsonify({
                'success': False,
                'error': f'Invalid audio format in URL. Allowed formats: {", ".join(ALLOWED_AUDIO_EXTENSIONS)}'
            }), 400
        
        image_filename = f"{request_id}_image.{image_ext}"
        audio_filename = f"{request_id}_audio.{audio_ext}"
        
        image_path = os.path.join(UPLOAD_FOLDER, image_filename)
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        
        # Download files from URLs
        success, message = download_file_from_url(image_url, image_path, "image")
        if not success:
            return jsonify({
                'success': False,
                'error': message
            }), 400
        
        success, message = download_file_from_url(audio_url, audio_path, "audio")
        if not success:
            cleanup_file(image_path)
            return jsonify({
                'success': False,
                'error': message
            }), 400

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
        # Cleanup files if an error occurred
        if image_path:
            cleanup_file(image_path)
        if audio_path:
            cleanup_file(audio_path)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/merge_videos', methods=['POST'])
@require_api_key
def merge_videos():
    """API endpoint to merge multiple videos from URLs (sync/async)"""
    try:
        # Check if async processing is requested
        data = request.get_json()
        async_processing = data.get('async', False) if data else False
        
        # If async processing is requested, create job and return immediately
        if async_processing:
            # Get user from API key
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
            
            # Create job record
            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'merge_videos'
            job.status = 'pending'
            job.set_input_data(data)
            
            db.session.add(job)
            db.session.commit()
            
            # Start background processing
            thread = threading.Thread(target=process_job_async, args=(job.job_id,))
            thread.daemon = True
            thread.start()
            
            return jsonify({
                'success': True,
                'job_id': job.job_id,
                'status': 'pending',
                'message': 'Job submitted for async processing. Use /api/job/{job_id}/status to check progress.',
                'status_url': url_for('get_job_status', job_id=job.job_id, _external=True)
            }), 202
        
        # If not async, process synchronously (existing logic)
        if not data:
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
    """API endpoint to create picture-in-picture video (sync/async)"""
    try:
        # Check if async processing is requested
        data = request.get_json()
        async_processing = data.get('async', False) if data else False
        
        # If async processing is requested, create job and return immediately
        if async_processing:
            # Get user from API key
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
            
            # Create job record
            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'picture_in_picture'
            job.status = 'pending'
            job.set_input_data(data)
            
            db.session.add(job)
            db.session.commit()
            
            # Start background processing
            thread = threading.Thread(target=process_job_async, args=(job.job_id,))
            thread.daemon = True
            thread.start()
            
            return jsonify({
                'success': True,
                'job_id': job.job_id,
                'status': 'pending',
                'message': 'Job submitted for async processing. Use /api/job/{job_id}/status to check progress.',
                'status_url': url_for('get_job_status', job_id=job.job_id, _external=True)
            }), 202
        
        # If not async, process synchronously (existing logic)
        if not data:
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

@app.route('/download/<path:filename>')
def download_file(filename):
    """Download processed video/audio file, supports subdirectories"""
    try:
        # Secure the filename to prevent path traversal attacks
        secure_filename_path = secure_filename(filename)
        
        # For files in subdirectories (like request_id/part_01.mp3), handle the path properly
        if '/' in filename:
            # Split the path and secure each part
            path_parts = filename.split('/')
            secured_parts = [secure_filename(part) for part in path_parts]
            secure_filename_path = '/'.join(secured_parts)
        
        full_path = os.path.join(OUTPUT_FOLDER, secure_filename_path)
        
        # Verify the file exists and is within the output folder (security check)
        if not os.path.exists(full_path) or not full_path.startswith(os.path.abspath(OUTPUT_FOLDER)):
            raise FileNotFoundError("File not found or access denied")
        
        # Get the directory and filename for send_from_directory
        if '/' in secure_filename_path:
            directory = os.path.dirname(full_path)
            filename_only = os.path.basename(full_path)
            return send_from_directory(directory, filename_only, as_attachment=True)
        else:
            return send_from_directory(OUTPUT_FOLDER, secure_filename_path, as_attachment=True)
            
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

@app.route('/profile')
@login_required
def profile():
    """User profile page"""
    return render_template('profile.html')

@app.route('/update-profile', methods=['POST'])
@login_required
def update_profile():
    """Update user profile information"""
    try:
        username = request.form.get('username')
        email = request.form.get('email')
        
        if not username or not email:
            flash('Username and email are required', 'error')
            return redirect(url_for('profile'))
        
        # Check if username is already taken by another user
        existing_user = User.query.filter_by(username=username).first()
        if existing_user and existing_user.id != current_user.id:
            flash('Username already taken', 'error')
            return redirect(url_for('profile'))
        
        # Check if email is already taken by another user
        existing_email = User.query.filter_by(email=email).first()
        if existing_email and existing_email.id != current_user.id:
            flash('Email already registered', 'error')
            return redirect(url_for('profile'))
        
        # Update user information
        current_user.username = username
        current_user.email = email
        db.session.commit()
        
        flash('Profile updated successfully', 'success')
        
    except Exception as e:
        logging.error(f"Error updating profile: {str(e)}")
        db.session.rollback()
        flash('Error updating profile', 'error')
    
    return redirect(url_for('profile'))

@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    """Change user password"""
    try:
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not all([current_password, new_password, confirm_password]):
            flash('All password fields are required', 'error')
            return redirect(url_for('profile'))
        
        # Verify current password
        if not current_user.check_password(current_password):
            flash('Current password is incorrect', 'error')
            return redirect(url_for('profile'))
        
        # Check if new passwords match
        if new_password != confirm_password:
            flash('New passwords do not match', 'error')
            return redirect(url_for('profile'))
        
        # Check password length
        if new_password and len(new_password) < 6:
            flash('Password must be at least 6 characters long', 'error')
            return redirect(url_for('profile'))
        
        # Update password
        current_user.set_password(new_password)
        db.session.commit()
        
        flash('Password changed successfully', 'success')
        
    except Exception as e:
        logging.error(f"Error changing password: {str(e)}")
        db.session.rollback()
        flash('Error changing password', 'error')
    
    return redirect(url_for('profile'))

@app.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    """Delete user account"""
    try:
        user_id = current_user.id
        
        # Cancel any active Stripe subscriptions
        if current_user.subscription and current_user.subscription.stripe_subscription_id:
            try:
                settings = StripeSettings.get_settings()
                if settings and settings.secret_key:
                    import stripe
                    stripe.api_key = settings.secret_key
                    stripe.Subscription.delete(current_user.subscription.stripe_subscription_id)
            except Exception as e:
                logging.error(f"Error cancelling Stripe subscription: {str(e)}")
        
        # Delete user and all associated data (cascade deletes)
        db.session.delete(current_user)
        db.session.commit()
        
        flash('Account deleted successfully', 'info')
        return redirect(url_for('index'))
        
    except Exception as e:
        logging.error(f"Error deleting account: {str(e)}")
        db.session.rollback()
        flash('Error deleting account', 'error')
        return redirect(url_for('profile'))

# Background job processing functions
def process_job_async(job_id):
    """Process a job asynchronously in background thread"""
    with app.app_context():
        job = Job.query.filter_by(job_id=job_id).first()
        if not job:
            logging.error(f"Job {job_id} not found")
            return
        
        try:
            job.update_status('processing')
            input_data = job.get_input_data()
            
            if job.job_type == 'merge_image_audio':
                result = process_merge_image_audio_job(job, input_data)
            elif job.job_type == 'merge_videos':
                result = process_merge_videos_job(job, input_data)
            elif job.job_type == 'picture_in_picture':
                result = process_picture_in_picture_job(job, input_data)
            elif job.job_type == 'split_audio':
                result = process_split_audio_job(job, input_data)
            else:
                job.update_status('failed', f'Unknown job type: {job.job_type}')
                return
            
            if result['success']:
                job.set_result_data(result)
                job.update_status('completed')
            else:
                job.update_status('failed', result.get('error', 'Unknown error'))
                
        except Exception as e:
            logging.error(f"Error processing job {job_id}: {str(e)}")
            job.update_status('failed', str(e))

def process_merge_image_audio_job(job, input_data):
    """Process merge_image_audio job"""
    try:
        request_id = str(uuid.uuid4())
        image_path = ""
        audio_path = ""
        
        # Handle URL-based inputs
        if 'image' in input_data and 'audio' in input_data:
            image_url = input_data['image']
            audio_url = input_data['audio']
            
            # Generate file paths
            image_ext = image_url.split('.')[-1].lower() if '.' in image_url else 'jpg'
            audio_ext = audio_url.split('.')[-1].lower() if '.' in audio_url else 'mp3'
            
            image_filename = f"{request_id}_image.{image_ext}"
            audio_filename = f"{request_id}_audio.{audio_ext}"
            
            image_path = os.path.join(UPLOAD_FOLDER, image_filename)
            audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
            
            # Download files
            success, message = download_file_from_url(image_url, image_path, "image")
            if not success:
                return {'success': False, 'error': message}
            
            success, message = download_file_from_url(audio_url, audio_path, "audio")
            if not success:
                cleanup_file(image_path)
                return {'success': False, 'error': message}
        
        # Generate output filename
        output_filename = f"{request_id}_merged_output.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        # Create video using FFMPEG
        success, message = create_video_with_ffmpeg(image_path, audio_path, output_path)
        
        # Cleanup input files
        cleanup_file(image_path)
        cleanup_file(audio_path)
        
        if success:
            download_url = url_for('download_file', filename=output_filename, _external=True)
            return {
                'success': True,
                'message': message,
                'download_url': download_url,
                'filename': output_filename
            }
        else:
            return {'success': False, 'error': message}
            
    except Exception as e:
        logging.error(f"Error in process_merge_image_audio_job: {str(e)}")
        return {'success': False, 'error': f'Server error: {str(e)}'}

def process_merge_videos_job(job, input_data):
    """Process merge_videos job"""
    try:
        request_id = str(uuid.uuid4())
        video_urls = input_data['video_urls']
        audio_url = input_data.get('audio_url')
        
        downloaded_videos = []
        audio_path = None
        
        # Download all videos
        for i, url in enumerate(video_urls):
            video_filename = f"{request_id}_video_{i}.mp4"
            video_path = os.path.join(UPLOAD_FOLDER, video_filename)
            
            success, message = download_video_from_url(url, video_path)
            if not success:
                for path in downloaded_videos:
                    cleanup_file(path)
                return {'success': False, 'error': f'Failed to download video {i+1}: {message}'}
            
            downloaded_videos.append(video_path)
        
        # Download audio if provided
        if audio_url:
            audio_filename = f"{request_id}_audio.mp3"
            audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
            
            success, message = download_video_from_url(audio_url, audio_path)
            if not success:
                for path in downloaded_videos:
                    cleanup_file(path)
                return {'success': False, 'error': f'Failed to download audio: {message}'}
        
        # Check video compatibility
        success, message = check_video_compatibility(downloaded_videos)
        if not success:
            for path in downloaded_videos:
                cleanup_file(path)
            if audio_path:
                cleanup_file(audio_path)
            return {'success': False, 'error': message}
        
        # Generate output filename
        output_filename = f"{request_id}_merged_output.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        # Merge videos using FFMPEG
        success, message = merge_videos_with_ffmpeg(downloaded_videos, output_path, audio_path)
        
        # Cleanup input files
        for path in downloaded_videos:
            cleanup_file(path)
        if audio_path:
            cleanup_file(audio_path)
        
        if success:
            download_url = url_for('download_file', filename=output_filename, _external=True)
            return {
                'success': True,
                'message': message,
                'download_url': download_url,
                'filename': output_filename
            }
        else:
            return {'success': False, 'error': message}
            
    except Exception as e:
        logging.error(f"Error in process_merge_videos_job: {str(e)}")
        return {'success': False, 'error': f'Server error: {str(e)}'}

def process_picture_in_picture_job(job, input_data):
    """Process picture_in_picture job"""
    try:
        request_id = str(uuid.uuid4())
        main_video_url = input_data['main_video_url']
        pip_video_url = input_data['pip_video_url']
        position = input_data.get('position', 'bottom-right')
        scale = input_data.get('scale', 'iw/4:ih/4')
        audio_option = input_data.get('audio_option', 'video1')
        
        # Download main video
        main_video_filename = f"{request_id}_main_video.mp4"
        main_video_path = os.path.join(UPLOAD_FOLDER, main_video_filename)
        
        success, message = download_video_from_url(main_video_url, main_video_path)
        if not success:
            return {'success': False, 'error': f'Failed to download main video: {message}'}
        
        # Download PiP video
        pip_video_filename = f"{request_id}_pip_video.mp4"
        pip_video_path = os.path.join(UPLOAD_FOLDER, pip_video_filename)
        
        success, message = download_video_from_url(pip_video_url, pip_video_path)
        if not success:
            cleanup_file(main_video_path)
            return {'success': False, 'error': f'Failed to download PiP video: {message}'}
        
        # Generate output filename
        output_filename = f"{request_id}_pip_output.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        # Create picture-in-picture video using FFMPEG
        success, message = create_picture_in_picture_with_ffmpeg(
            main_video_path, pip_video_path, output_path, position, scale, audio_option
        )
        
        # Cleanup input files
        cleanup_file(main_video_path)
        cleanup_file(pip_video_path)
        
        if success:
            download_url = url_for('download_file', filename=output_filename, _external=True)
            return {
                'success': True,
                'message': message,
                'download_url': download_url,
                'filename': output_filename
            }
        else:
            return {'success': False, 'error': message}
            
    except Exception as e:
        logging.error(f"Error in process_picture_in_picture_job: {str(e)}")
        return {'success': False, 'error': f'Server error: {str(e)}'}

def process_split_audio_job(job, input_data):
    """Process split_audio job"""
    try:
        request_id = str(uuid.uuid4())
        audio_path = ""
        
        # Handle URL-based input
        if 'audio_url' in input_data:
            audio_url = input_data['audio_url']
            num_parts = input_data.get('parts', 2)
            
            # Validate parts parameter
            if not isinstance(num_parts, int) or num_parts < 2 or num_parts > 20:
                return {'success': False, 'error': 'parts must be an integer between 2 and 20'}
            
            # Generate file paths
            audio_ext = audio_url.split('.')[-1].lower() if '.' in audio_url else 'mp3'
            audio_filename = f"{request_id}_audio.{audio_ext}"
            audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
            output_dir = os.path.join(OUTPUT_FOLDER, request_id)
            
            # Create output directory
            os.makedirs(output_dir, exist_ok=True)
            
            # Download file
            success, message = download_file_from_url(audio_url, audio_path, "audio")
            if not success:
                cleanup_file(audio_path)
                return {'success': False, 'error': message}
            
            # Split audio using FFMPEG
            success, message, output_files = split_audio_with_ffmpeg(audio_path, output_dir, num_parts)
            
            # Cleanup input file
            cleanup_file(audio_path)
            
            if success:
                # Generate download URLs for all parts
                download_urls = []
                for filename in output_files:
                    download_url = url_for('download_file', filename=f"{request_id}/{filename}", _external=True)
                    download_urls.append({
                        'part': filename,
                        'download_url': download_url
                    })
                
                return {
                    'success': True,
                    'message': message,
                    'parts': len(output_files),
                    'audio_parts': download_urls
                }
            else:
                # Cleanup output directory on failure
                import shutil
                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
                return {'success': False, 'error': message}
        else:
            return {'success': False, 'error': 'audio_url is required'}
            
    except Exception as e:
        logging.error(f"Error in process_split_audio_job: {str(e)}")
        # Cleanup files on error
        if audio_path and os.path.exists(audio_path):
            cleanup_file(audio_path)
        return {'success': False, 'error': f'Server error: {str(e)}'}

# Job status endpoint
@app.route('/api/job/<job_id>/status', methods=['GET'])
@require_api_key
def get_job_status(job_id):
    """Get the status of an async job"""
    try:
        job = Job.query.filter_by(job_id=job_id).first()
        if not job:
            return jsonify({
                'success': False,
                'error': 'Job not found'
            }), 404
        
        # Check if the job belongs to the current user
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
        
        if job.user_id != key_record.user_id:
            return jsonify({
                'success': False,
                'error': 'Access denied'
            }), 403
        
        response_data = {
            'success': True,
            'job_id': job.job_id,
            'job_type': job.job_type,
            'status': job.status,
            'created_at': job.created_at.isoformat(),
            'updated_at': job.updated_at.isoformat()
        }
        
        if job.status == 'completed':
            result_data = job.get_result_data()
            if result_data:
                response_data.update(result_data)
        elif job.status == 'failed':
            response_data['error'] = job.error_message
        
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"Error getting job status: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/split_audio', methods=['POST'])
@require_api_key
def split_audio():
    """API endpoint to split audio into equal parts (sync/async)"""
    try:
        # Check if async processing is requested
        data = request.get_json()
        async_processing = data.get('async', False) if data else False
        
        # If async processing is requested, create job and return immediately
        if async_processing:
            # Get user from API key
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
            
            # Create job record
            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'split_audio'
            job.status = 'pending'
            job.set_input_data(data)
            
            db.session.add(job)
            db.session.commit()
            
            # Start background processing
            thread = threading.Thread(target=process_job_async, args=(job.job_id,))
            thread.daemon = True
            thread.start()
            
            return jsonify({
                'success': True,
                'job_id': job.job_id,
                'status': 'pending',
                'message': 'Job submitted for async processing. Use /api/job/{job_id}/status to check progress.',
                'status_url': url_for('get_job_status', job_id=job.job_id, _external=True)
            }), 202
        
        # If not async, process synchronously
        if not data:
            data = request.get_json()
        
        if not data or 'audio_url' not in data:
            return jsonify({
                'success': False,
                'error': 'audio_url is required'
            }), 400
        
        audio_url = data['audio_url']
        num_parts = data.get('parts', 2)  # Default to 2 parts
        
        # Validate parts parameter
        if not isinstance(num_parts, int) or num_parts < 2 or num_parts > 20:
            return jsonify({
                'success': False,
                'error': 'parts must be an integer between 2 and 20'
            }), 400
        
        # Generate unique ID for this request
        request_id = str(uuid.uuid4())
        audio_filename = f"{request_id}_audio.mp3"
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        output_dir = os.path.join(OUTPUT_FOLDER, request_id)
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            # Download audio file
            success, message = download_file_from_url(audio_url, audio_path, "audio")
            if not success:
                cleanup_file(audio_path)
                return jsonify({
                    'success': False,
                    'error': message
                }), 400
            
            # Split audio using FFMPEG
            success, message, output_files = split_audio_with_ffmpeg(audio_path, output_dir, num_parts)
            
            # Cleanup input file
            cleanup_file(audio_path)
            
            if success:
                # Generate download URLs for all parts
                download_urls = []
                for filename in output_files:
                    download_url = url_for('download_file', filename=f"{request_id}/{filename}", _external=True)
                    download_urls.append({
                        'part': filename,
                        'download_url': download_url
                    })
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'parts': len(output_files),
                    'audio_parts': download_urls
                })
            else:
                # Cleanup output directory on failure
                import shutil
                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
                
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as e:
            # Cleanup files on error
            cleanup_file(audio_path)
            import shutil
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir)
            
            logging.error(f"Audio splitting error: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Server error: {str(e)}'
            }), 500
            
    except Exception as e:
        logging.error(f"Split audio API error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)