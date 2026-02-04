import os
import logging
import subprocess
import uuid
import tempfile
import threading
import json
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, send_from_directory, url_for, flash, redirect, Response, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_required, current_user
from sqlalchemy.exc import OperationalError, PendingRollbackError
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
import mimetypes
import resend

from models import db, User, ApiKey, SubscriptionPlan, StripeSettings, UserSubscription, SiteSettings, Job, ApiLog, SITE_DEFAULT_API_KEY
import time
from forms import RegistrationForm, LoginForm, ApiKeyForm
from auth_routes import auth
from stripe_routes import stripe_bp
from storage_utils import upload_to_storage, get_storage_download_url

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
    "pool_size": 20,  # Increased for concurrent video processing
    "max_overflow": 40,  # Increased for burst traffic
    "pool_timeout": 120,  # Increased to handle connection pool exhaustion
}

# URL building configuration for async jobs
# Use localhost for development, production domain for production
# Check if we're in production deployment
if os.environ.get('REPLIT_DEPLOYMENT'):
    # Production environment - don't set SERVER_NAME to allow external requests
    # Flask will use the request's host header
    app.config['PREFERRED_URL_SCHEME'] = 'https'
else:
    # Development environment - use localhost
    app.config['SERVER_NAME'] = 'localhost:5000'
    app.config['PREFERRED_URL_SCHEME'] = 'http'
app.config['APPLICATION_ROOT'] = '/'

# In production, use /tmp which is the only writable directory
if os.environ.get('REPLIT_DEPLOYMENT'):
    UPLOAD_FOLDER = '/tmp/uploads'
    OUTPUT_FOLDER = '/tmp/outputs'
else:
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

# Register Stripe blueprint with /api prefix to match Stripe webhook URL
app.register_blueprint(stripe_bp, url_prefix='/api/stripe')

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
        
        # Store user info in request context for logging
        request.api_user_id = user.id
        request.api_username = user.username
        request.api_key_id = key_record.id
        
        return f(*args, **kwargs)
    
    return decorated_function

def sanitize_sensitive_data(data, sensitive_keys=None):
    """Recursively sanitize sensitive data from dictionaries and lists"""
    if sensitive_keys is None:
        sensitive_keys = {'api_key', 'password', 'secret', 'token', 'authorization', 'x-api-key'}
    
    if isinstance(data, dict):
        return {
            k: '[REDACTED]' if k.lower() in sensitive_keys else sanitize_sensitive_data(v, sensitive_keys)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [sanitize_sensitive_data(item, sensitive_keys) for item in data]
    return data

def log_api_request(f):
    """Decorator to log API requests and responses to the database"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        start_time = time.time()
        
        # Collect request data
        endpoint = request.path
        method = request.method
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()
        user_agent = request.headers.get('User-Agent', '')[:500]
        
        # Get user info from request context (set by require_api_key)
        user_id = getattr(request, 'api_user_id', None)
        username = getattr(request, 'api_username', None)
        api_key_id = getattr(request, 'api_key_id', None)
        
        # Collect request data (form data, JSON, or query params)
        request_data = {}
        try:
            if request.is_json:
                json_data = request.get_json(silent=True) or {}
                request_data = sanitize_sensitive_data(json_data)
            elif request.form:
                request_data = sanitize_sensitive_data(dict(request.form.items()))
            if request.args:
                args_data = sanitize_sensitive_data(dict(request.args.items()))
                request_data.update(args_data)
            if request.files:
                request_data['_files'] = [f.filename for f in request.files.values()]
        except Exception as e:
            request_data = {'_error': f'Could not parse request: {str(e)}'}
        
        response = None
        response_data = None
        status_code = None
        error_message = None
        
        try:
            # Execute the actual function
            response = f(*args, **kwargs)
            
            # Handle tuple responses (response, status_code)
            if isinstance(response, tuple):
                response_obj, status_code = response[0], response[1] if len(response) > 1 else 200
            else:
                response_obj = response
                status_code = getattr(response_obj, 'status_code', 200) if hasattr(response_obj, 'status_code') else 200
            
            # Extract response data
            try:
                if hasattr(response_obj, 'get_json'):
                    response_data = response_obj.get_json()
                elif hasattr(response_obj, 'data'):
                    response_data = response_obj.data.decode('utf-8')[:5000]
                else:
                    response_data = str(response_obj)[:1000]
            except Exception:
                response_data = {'_note': 'Could not serialize response'}
            
            # Check for error in response
            if isinstance(response_data, dict) and not response_data.get('success', True):
                error_message = response_data.get('error', '')
                
        except Exception as e:
            status_code = 500
            error_message = str(e)
            response_data = {'error': str(e)}
            raise
        
        finally:
            # Calculate processing time
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            # Log to database in a separate thread to avoid blocking
            def save_log():
                try:
                    with app.app_context():
                        ApiLog.log_request(
                            endpoint=endpoint,
                            method=method,
                            user_id=user_id,
                            username=username,
                            api_key_id=api_key_id,
                            request_data=request_data,
                            response_data=response_data,
                            status_code=status_code,
                            error_message=error_message,
                            ip_address=ip_address,
                            user_agent=user_agent,
                            processing_time_ms=processing_time_ms
                        )
                except Exception as log_error:
                    logging.error(f"Failed to save API log: {str(log_error)}")
            
            # Run log saving in background thread
            log_thread = threading.Thread(target=save_log)
            log_thread.daemon = True
            log_thread.start()
        
        return response
    
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

def get_resend_credentials():
    """Get Resend API credentials from Replit connector"""
    try:
        hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
        logging.info(f"Hostname: {hostname}")
        
        # Get authentication token
        x_replit_token = None
        has_repl_identity = bool(os.environ.get('REPL_IDENTITY'))
        has_web_repl_renewal = bool(os.environ.get('WEB_REPL_RENEWAL'))
        
        logging.info(f"REPL_IDENTITY exists: {has_repl_identity}")
        logging.info(f"WEB_REPL_RENEWAL exists: {has_web_repl_renewal}")
        
        if os.environ.get('REPL_IDENTITY'):
            x_replit_token = 'repl ' + os.environ.get('REPL_IDENTITY')
            logging.info("Using REPL_IDENTITY for authentication")
        elif os.environ.get('WEB_REPL_RENEWAL'):
            x_replit_token = 'depl ' + os.environ.get('WEB_REPL_RENEWAL')
            logging.info("Using WEB_REPL_RENEWAL for authentication")
        
        if not x_replit_token or not hostname:
            logging.error(f"Missing Replit connector credentials - hostname: {hostname}, token exists: {bool(x_replit_token)}")
            return None, None
        
        # Fetch connection settings
        url = f'https://{hostname}/api/v2/connection?include_secrets=true&connector_names=resend'
        logging.info(f"Fetching credentials from: {url}")
        
        response = requests.get(
            url,
            headers={
                'Accept': 'application/json',
                'X_REPLIT_TOKEN': x_replit_token
            }
        )
        
        logging.info(f"Response status: {response.status_code}")
        
        if response.status_code != 200:
            logging.error(f"Failed to fetch Resend credentials: {response.status_code} - {response.text}")
            return None, None
        
        data = response.json()
        logging.info(f"Response data keys: {list(data.keys())}")
        
        items = data.get('items', [])
        logging.info(f"Number of items: {len(items)}")
        
        if not items:
            logging.error("No Resend connection found in response")
            return None, None
        
        settings = items[0].get('settings', {})
        logging.info(f"Settings keys: {list(settings.keys())}")
        
        api_key = settings.get('api_key')
        from_email = settings.get('from_email')
        
        if not api_key:
            logging.error("Resend API key not found in connection settings")
            return None, None
        
        logging.info(f"Successfully fetched Resend credentials. From email: {from_email}")
        return api_key, from_email
        
    except Exception as e:
        logging.error(f"Error getting Resend credentials: {str(e)}", exc_info=True)
        return None, None

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
            # Concatenate videos and preserve their original audio timeline
            # Then add custom audio as overlay - when custom audio ends, original audio continues
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
                '-preset', 'veryfast',
                '-crf', '23',
                '-g', '30',
                '-keyint_min', '30',
                '-sc_threshold', '0',
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
                '-preset', 'veryfast',
                '-crf', '23',
                '-g', '30',
                '-keyint_min', '30',
                '-sc_threshold', '0',
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
                    '-preset', 'veryfast',
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
        # Use overlay with eof_action=pass to continue main video when PiP ends
        cmd = [
            'ffmpeg',
            '-i', main_video_path,   # Input 0: main video
            '-i', pip_video_path,    # Input 1: pip video
            '-filter_complex', f'[1]scale={scale}[pip];[0][pip]overlay={overlay_position}:eof_action=pass',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
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

def split_audio_by_segments_with_ffmpeg(audio_path, output_dir, segment_duration):
    """Split audio into segments of specified duration using FFMPEG"""
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
        
        # Calculate number of segments needed
        num_segments = math.ceil(total_duration / segment_duration)
        output_files = []
        
        logging.info(f"Splitting {total_duration:.2f}s audio into segments of {segment_duration}s each ({num_segments} segments)")
        
        # Split audio into segments
        for i in range(num_segments):
            start_time = i * segment_duration
            # For the last segment, use the remaining duration
            if i == num_segments - 1:
                current_segment_duration = total_duration - start_time
            else:
                current_segment_duration = segment_duration
            
            output_filename = f"segment_{i+1:02d}.mp3"
            output_path = os.path.join(output_dir, output_filename)
            
            # FFMPEG command to extract segment
            cmd = [
                'ffmpeg',
                '-i', audio_path,
                '-ss', str(start_time),
                '-t', str(current_segment_duration),
                '-c:a', 'mp3',
                '-b:a', '192k',
                '-y',
                output_path
            ]
            
            logging.info(f"Creating segment {i+1}/{num_segments}: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                output_files.append(output_filename)
                logging.info(f"Successfully created audio segment {i+1}")
            else:
                logging.error(f"FFMPEG error for segment {i+1}: {result.stderr}")
                # Clean up already created files
                for created_file in output_files:
                    cleanup_file(os.path.join(output_dir, created_file))
                return False, f"Failed to create audio segment {i+1}: {result.stderr}", []
        
        logging.info(f"Successfully split audio into {len(output_files)} segments")
        return True, f"Audio successfully split into {len(output_files)} segments", output_files
        
    except subprocess.TimeoutExpired:
        logging.error("Audio segment splitting timed out")
        return False, "Audio segment splitting processing timed out", []
    except Exception as e:
        logging.error(f"Audio segment splitting error: {str(e)}")
        return False, f"Audio segment splitting error: {str(e)}", []

def convert_to_vertical_with_ffmpeg(video_path, output_path, watermark_path=None):
    """Convert horizontal video to vertical format with automatic aspect ratio selection (3:4 or 9:16)"""
    try:
        # Get video dimensions
        success, dimensions = get_video_dimensions(video_path)
        if not success:
            return False, f"Could not analyze video dimensions: {dimensions}"
        
        width, height = dimensions
        logging.info(f"Original video dimensions: {width}x{height}")
        
        # Calculate aspect ratio
        aspect_ratio = width / height
        
        # Determine target aspect ratio based on which is closer to the original
        # 3:4 = 0.75, 9:16 = 0.5625
        target_3_4 = 3 / 4  # 0.75
        target_9_16 = 9 / 16  # 0.5625
        
        # Calculate distances to each target ratio
        dist_to_3_4 = abs(aspect_ratio - target_3_4)
        dist_to_9_16 = abs(aspect_ratio - target_9_16)
        
        # Choose the closest ratio
        if dist_to_3_4 < dist_to_9_16:
            target_width = 1080
            target_height = 1440  # 3:4 ratio
            ratio_name = "3:4"
        else:
            target_width = 1080
            target_height = 1920  # 9:16 ratio
            ratio_name = "9:16"
        
        logging.info(f"Selected {ratio_name} aspect ratio for output ({target_width}x{target_height})")
        
        # Build FFMPEG filter complex
        # Scale the video to fit within the target dimensions while maintaining aspect ratio
        # Then add black bars (pillarbox) to fill the remaining space
        filter_parts = []
        
        # Scale and pad the video
        scale_filter = f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black[v]"
        filter_parts.append(scale_filter)
        
        # Build the complete filter
        if watermark_path:
            # Add watermark in top right corner, scaled to 20% of video width
            watermark_size = int(target_width * 0.2)
            watermark_filter = f"[v][1:v]overlay=W-w-20:20:format=auto,format=yuv420p[outv]"
            video_output = '[outv]'
            inputs = ['-i', video_path, '-i', watermark_path]
            filter_complex = scale_filter + ';' + watermark_filter
        else:
            video_output = '[v]'
            inputs = ['-i', video_path]
            filter_complex = scale_filter
        
        # Build FFMPEG command
        cmd = [
            'ffmpeg',
            '-hide_banner',
            *inputs,
            '-filter_complex', filter_complex,
            '-map', video_output,
            '-map', '0:a?',  # Copy audio if it exists
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-pix_fmt', 'yuv420p',
            '-y',
            output_path
        ]
        
        logging.info(f"Running FFMPEG command: {' '.join(cmd)}")
        
        # Run FFMPEG
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        if result.returncode == 0:
            logging.info(f"Successfully converted to vertical {ratio_name} format")
            return True, f"Video successfully converted to vertical {ratio_name} format"
        else:
            # Strip only the leading banner lines from stderr
            stderr_text = result.stderr.strip() if result.stderr else ""
            stderr_lines = stderr_text.split('\n') if stderr_text else []
            
            # Remove only actual banner lines (starting with specific prefixes)
            error_lines = []
            for line in stderr_lines:
                # Skip known banner prefixes but keep real error messages
                if line.startswith('ffmpeg version') or \
                   line.startswith('built with') or \
                   (line.startswith('configuration:') and '--' in line) or \
                   (line.strip().startswith('lib') and '=' in line and 'version' in line.lower()):
                    continue
                error_lines.append(line)
            
            error_msg = '\n'.join(error_lines).strip()
            
            # If stderr has no useful content after filtering, include stdout
            if not error_msg:
                error_msg = result.stdout.strip() if result.stdout else "Unknown ffmpeg error occurred"
            
            logging.error(f"FFMPEG error (returncode {result.returncode}): {error_msg}")
            return False, f"Video conversion failed: {error_msg}"
            
    except subprocess.TimeoutExpired:
        logging.error("Video conversion timed out")
        return False, "Video conversion processing timed out"
    except Exception as e:
        logging.error(f"Video conversion error: {str(e)}")
        return False, f"Video conversion error: {str(e)}"

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

@app.route('/download-readme')
def download_readme():
    """Download README_FFMPEGAPI.md file for AI agent integration"""
    try:
        readme_path = os.path.join(os.path.dirname(__file__), 'README_FFMPEGAPI.md')
        return send_file(
            readme_path,
            as_attachment=True,
            download_name='README_FFMPEGAPI.md',
            mimetype='text/markdown'
        )
    except Exception as e:
        logging.error(f"Error downloading README: {str(e)}")
        flash('Could not download README file', 'error')
        return redirect(url_for('api_docs'))

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """Contact page with email sending functionality"""
    if request.method == 'POST':
        try:
            # Get form data
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip()
            subject = request.form.get('subject', '').strip()
            message = request.form.get('message', '').strip()
            
            # Validate form data
            if not all([name, email, subject, message]):
                flash('All fields are required', 'error')
                return render_template('contact.html')
            
            # Get Resend credentials
            api_key, from_email = get_resend_credentials()
            
            if not api_key:
                flash('Email service is not configured. Please contact the administrator.', 'error')
                return render_template('contact.html')
            
            # Use configured from_email or fallback
            sender_email = from_email if from_email else 'noreply@ffmpegapi.net'
            
            # Initialize Resend client
            resend.api_key = api_key
            
            # Prepare email content
            email_subject = f"Contact Form: {subject}"
            email_html = f"""
            <h2>New Contact Form Submission</h2>
            <p><strong>From:</strong> {name} ({email})</p>
            <p><strong>Subject:</strong> {subject}</p>
            <p><strong>Message:</strong></p>
            <p>{message.replace(chr(10), '<br>')}</p>
            """
            
            # Send email using Resend
            params = {
                "from": sender_email,
                "to": ["info@ffmpegapi.net"],
                "subject": email_subject,
                "html": email_html,
                "reply_to": email
            }
            
            response = resend.Emails.send(params)
            
            logging.info(f"Contact email sent successfully. Response: {response}")
            flash('Thank you for your message! We will get back to you soon.', 'success')
            return redirect(url_for('contact'))
            
        except Exception as e:
            logging.error(f"Error sending contact email: {str(e)}")
            flash('Sorry, there was an error sending your message. Please try again later.', 'error')
            return render_template('contact.html')
    
    # GET request - show form
    return render_template('contact.html')

@app.route('/api/merge_image_audio', methods=['POST'])
@log_api_request
@require_api_key
def merge_image_audio():
    """API endpoint to merge image and audio into video from URLs or files (sync/async)"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[MERGE_IMAGE_AUDIO] Request received from API key: {api_key[:20]}...")
    logging.info(f"[MERGE_IMAGE_AUDIO] Headers: {dict(request.headers)}")
    logging.info(f"[MERGE_IMAGE_AUDIO] Files: {list(request.files.keys())}")
    if request.is_json:
        logging.info(f"[MERGE_IMAGE_AUDIO] JSON data: {request.get_json()}")
    logging.info(f"[MERGE_IMAGE_AUDIO] Form data: {dict(request.form)}")
    
    try:
        # Initialize variables at function level
        image_path = ""
        audio_path = ""
        
        # Check if this is a file upload request (FormData) or JSON request (URLs)
        is_file_upload = bool(request.files)
        async_processing = False
        
        if is_file_upload:
            # Handle file uploads from the UI
            if 'image' not in request.files or 'audio' not in request.files:
                return jsonify({
                    'success': False,
                    'error': 'Both image and audio files are required'
                }), 400
            
            image_file = request.files['image']
            audio_file = request.files['audio']
            
            if not image_file.filename or not audio_file.filename:
                return jsonify({
                    'success': False,
                    'error': 'Both image and audio files must be selected'
                }), 400
            
            # Save uploaded files
            request_id = str(uuid.uuid4())
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            
            image_filename = f"{request_id}_image.{image_file.filename.split('.')[-1]}"
            audio_filename = f"{request_id}_audio.{audio_file.filename.split('.')[-1]}"
            
            image_path = os.path.join(UPLOAD_FOLDER, image_filename)
            audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
            
            image_file.save(image_path)
            audio_file.save(audio_path)
            
        else:
            # Handle JSON request with URLs
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
            
            # Require JSON data for URL-based processing
            if not request.is_json:
                return jsonify({
                    'success': False,
                    'error': 'Content-Type must be application/json for URL-based processing'
                }), 400
                
            data = request.get_json()
            
            if not data or 'image' not in data or 'audio' not in data:
                return jsonify({
                    'success': False,
                    'error': 'Both image and audio URLs are required'
                }), 400
            
            # Generate unique filename for this request
            request_id = str(uuid.uuid4())
            
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
        
        # Ensure output directory exists
        os.makedirs(OUTPUT_FOLDER, exist_ok=True)
        logging.info(f"Output path: {output_path}")

        # Release database connection before long FFMPEG processing to prevent pool exhaustion
        db.session.close()
        
        # Create video using FFMPEG
        success, message = create_video_with_ffmpeg(image_path, audio_path, output_path)
        
        # Log file creation status
        if success:
            if os.path.exists(output_path):
                file_size = os.path.getsize(output_path)
                logging.info(f"Output file created successfully: {output_path} ({file_size} bytes)")
            else:
                logging.error(f"FFMPEG reported success but output file doesn't exist: {output_path}")
                success = False
                message = "Video processing completed but output file was not created"

        # Cleanup uploaded files
        cleanup_file(image_path)
        cleanup_file(audio_path)

        if success:
            # Upload to storage for persistence
            storage_url = upload_to_storage(output_path, output_filename)
            
            if storage_url:
                # Clean up local file after successful upload
                cleanup_file(output_path)
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'download_url': storage_url,
                    'filename': output_filename
                })
            else:
                # Fallback to local download if storage upload fails
                logging.warning("Storage upload failed, falling back to local download")
                
                # Fix for Replit: Generate proper URL based on environment
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    # In production deployment - files are ephemeral!
                    download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    logging.warning(f"Production deployment - file may be lost on container restart: {output_filename}")
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    # In Replit development environment
                    download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                else:
                    # Local environment
                    download_url = url_for('download_file', filename=output_filename, _external=True)
                
                logging.info(f"Generated download URL: {download_url}")
                return jsonify({
                    'success': True,
                    'message': f"{message} (⚠️ Download immediately - files are temporary in production!)",
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
@log_api_request
@require_api_key
def merge_videos():
    """API endpoint to merge multiple videos from URLs (sync/async)"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[MERGE_VIDEOS] Request received from API key: {api_key[:20]}...")
    logging.info(f"[MERGE_VIDEOS] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[MERGE_VIDEOS] JSON data: {request.get_json()}")
    logging.info(f"[MERGE_VIDEOS] Form data: {dict(request.form)}")
    
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
                    # Cleanup downloaded videos
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
                    # Cleanup downloaded files
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
                    # Cleanup downloaded files
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
            
            # Note: Removed aspect ratio check since we now handle different aspect ratios
            # during the normalization process in merge_videos_with_ffmpeg
            
            # Generate output filename
            output_filename = f"{request_id}_merged_videos.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Release database connection before long FFMPEG processing to prevent pool exhaustion
            db.session.close()
            
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
                # Generate filename for subtitled video
                subtitled_filename = f"{request_id}_merged_subtitled_videos.mp4"
                subtitled_output_path = os.path.join(OUTPUT_FOLDER, subtitled_filename)
                
                # Add subtitles to the merged video
                subtitle_success, subtitle_message = add_subtitles_with_ffmpeg(output_path, subtitle_path, subtitled_output_path)
                
                # Cleanup subtitle file
                cleanup_file(subtitle_path)
                
                if subtitle_success:
                    # Remove the non-subtitled version and use the subtitled one
                    cleanup_file(output_path)
                    output_path = subtitled_output_path
                    output_filename = subtitled_filename
                    message = f"Videos merged and subtitles added successfully"
                    subtitles_added = True
                else:
                    # Cleanup subtitle file if it failed
                    cleanup_file(subtitled_output_path)
                    return jsonify({
                        'success': False,
                        'error': f'Video merge succeeded but subtitle addition failed: {subtitle_message}'
                    }), 500
            elif subtitle_path:
                # Cleanup subtitle file if merge failed
                cleanup_file(subtitle_path)
            
            # Add watermark if provided and processing was successful so far
            if success and watermark_path:
                # Generate filename for watermarked video
                watermarked_filename = f"{request_id}_merged_watermarked_videos.mp4"
                watermarked_output_path = os.path.join(OUTPUT_FOLDER, watermarked_filename)
                
                # Add watermark to the video (could be merged or merged+subtitled)
                watermark_success, watermark_message = add_watermark_with_ffmpeg(output_path, watermark_path, watermarked_output_path)
                
                # Cleanup watermark file
                cleanup_file(watermark_path)
                
                if watermark_success:
                    # Remove the non-watermarked version and use the watermarked one
                    cleanup_file(output_path)
                    output_path = watermarked_output_path
                    output_filename = watermarked_filename
                    # Update message to reflect all processing done
                    if subtitles_added:
                        message = f"Videos merged, subtitles and watermark added successfully"
                    else:
                        message = f"Videos merged and watermark added successfully"
                else:
                    # Cleanup watermark file if it failed
                    cleanup_file(watermarked_output_path)
                    return jsonify({
                        'success': False,
                        'error': f'Video processing succeeded but watermark addition failed: {watermark_message}'
                    }), 500
            elif watermark_path:
                # Cleanup watermark file if merge failed
                cleanup_file(watermark_path)
            
            if success:
                # Upload to storage for persistence
                storage_url = upload_to_storage(output_path, output_filename)
                
                if storage_url:
                    # Clean up local file after successful upload
                    cleanup_file(output_path)
                    
                    return jsonify({
                        'success': True,
                        'message': message,
                        'download_url': storage_url,
                        'filename': output_filename
                    })
                else:
                    # Fallback to local download if storage upload fails
                    logging.warning("Storage upload failed, falling back to local download")
                    
                    # Fix for Replit: Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        # In production deployment
                        download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        # In Replit development environment
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                    else:
                        # Local environment
                        download_url = url_for('download_file', filename=output_filename, _external=True)
                    return jsonify({
                        'success': True,
                        'message': f"{message} (Note: Using temporary local storage - download soon)",
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
        logging.error(f"[MERGE_VIDEOS] Error in merge_videos: {str(e)}")
        logging.error(f"[MERGE_VIDEOS] Full traceback:", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/picture_in_picture', methods=['POST'])
@log_api_request
@require_api_key
def picture_in_picture():
    """API endpoint to create picture-in-picture video (sync/async)"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[PICTURE_IN_PICTURE] Request received from API key: {api_key[:20]}...")
    logging.info(f"[PICTURE_IN_PICTURE] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[PICTURE_IN_PICTURE] JSON data: {request.get_json()}")
    logging.info(f"[PICTURE_IN_PICTURE] Form data: {dict(request.form)}")
    
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
        
        # Release database connection before long FFMPEG processing to prevent pool exhaustion
        db.session.close()
        
        # Create picture-in-picture video using FFMPEG
        success, message = create_picture_in_picture_with_ffmpeg(
            main_video_path, pip_video_path, output_path, position, scale, audio_option
        )
        
        # Cleanup downloaded files
        cleanup_file(main_video_path)
        cleanup_file(pip_video_path)
        
        if success:
            # Upload to storage for persistence
            storage_url = upload_to_storage(output_path, output_filename)
            
            if storage_url:
                # Clean up local file after successful upload
                cleanup_file(output_path)
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'download_url': storage_url,
                    'filename': output_filename
                })
            else:
                # Fallback to local download if storage upload fails
                logging.warning("Storage upload failed, falling back to local download")
                
                # Fix for Replit: Generate proper URL based on environment
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    # In production deployment - files are ephemeral!
                    download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    logging.warning(f"Production deployment - file may be lost on container restart: {output_filename}")
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    # In Replit development environment
                    download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                else:
                    # Local environment
                    download_url = url_for('download_file', filename=output_filename, _external=True)
                
                logging.info(f"Generated download URL: {download_url}")
                return jsonify({
                    'success': True,
                    'message': f"{message} (⚠️ Download immediately - files are temporary in production!)",
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

@app.route('/api/storage/<path:filename>')
def serve_from_storage(filename):
    """Serve a file from Replit App Storage"""
    try:
        from replit.object_storage import Client
        client = Client()
        
        # Download the file from storage
        file_data = client.download_as_bytes(filename)
        
        # Determine content type
        import mimetypes
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        
        # Return the file
        from flask import Response
        return Response(
            file_data,
            mimetype=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{os.path.basename(filename)}"',
                'Cache-Control': 'public, max-age=3600'
            }
        )
    except Exception as e:
        # Don't log errors for poster images - these are optional video thumbnails
        # that external clients may request but aren't always generated
        if '-poster.jpg' in filename or '-poster.png' in filename:
            logging.debug(f"Poster image not found (expected): {filename}")
        else:
            logging.error(f"Error serving file from storage {filename}: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'File not found or error accessing storage'
        }), 404

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
        # Convert to absolute path for security comparison
        abs_full_path = os.path.abspath(full_path)
        abs_output_folder = os.path.abspath(OUTPUT_FOLDER)
        
        
        # Verify the file exists and is within the output folder (security check)
        if not os.path.exists(full_path):
            logging.error(f"File not found: {full_path}")
            raise FileNotFoundError(f"File not found: {filename}")
        
        if not abs_full_path.startswith(abs_output_folder):
            logging.error(f"Access denied to file outside output folder: {abs_full_path}")
            raise FileNotFoundError("File access denied")
        
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

@app.route('/blog')
def blog():
    """Blog listing page"""
    # Define blog articles (could be moved to database in the future)
    articles = [
        {
            'slug': 'best-tools-vibe-coding-2025',
            'title': 'Best Tools for Vibe Coding 2025: AI-Powered Developer Workflow',
            'description': 'Explore the best vibe coding tools in 2025 including Cursor, Claude, GitHub Copilot, and FFMPEG API for building modern applications faster.',
            'date': 'November 2025',
            'author': 'FFMPEG API Team',
            'image': '',
            'keywords': 'vibe coding, cursor ai, claude ai, github copilot, ai coding tools 2025, developer tools'
        },
        {
            'slug': 'best-video-editor-api-2025',
            'title': 'Best Video Editor API 2025: Top Features for Developers',
            'description': 'Discover the best video editor API of 2025 with powerful features like video merging, picture-in-picture, subtitle burning, and vertical video conversion for mobile platforms.',
            'date': 'November 2025',
            'author': 'FFMPEG API Team',
            'image': '',
            'keywords': 'best video editor api, video editing api 2025, video api for developers, automated video editing'
        },
        {
            'slug': 'ffmpeg-api-guide',
            'title': 'Complete Guide to FFMPEG API: Video Processing Made Simple',
            'description': 'Discover how FFMPEG API simplifies video merging, audio processing, subtitle addition, and format conversion with easy-to-use REST endpoints.',
            'date': 'November 2025',
            'author': 'FFMPEG API Team',
            'image': '',
            'keywords': 'ffmpeg api, video processing api, video merger api, audio processing, subtitle api'
        }
    ]
    return render_template('blog.html', articles=articles)

@app.route('/blog/<slug>')
def blog_article(slug):
    """Individual blog article page"""
    # Define articles (could be moved to database in the future)
    articles = {
        'best-tools-vibe-coding-2025': {
            'title': 'Best Tools for Vibe Coding 2025: AI-Powered Developer Workflow',
            'description': 'Explore the best vibe coding tools in 2025 including Cursor, Claude, GitHub Copilot, and FFMPEG API for building modern applications faster.',
            'date': 'November 2025',
            'author': 'FFMPEG API Team',
            'image': '',
            'keywords': 'vibe coding, vibe coding tools, cursor ai, claude ai, github copilot, ai coding tools 2025, developer tools, ai pair programming, code assistant, developer productivity tools, best coding tools 2025',
            'content_file': 'blog_vibe_coding_tools.html'
        },
        'best-video-editor-api-2025': {
            'title': 'Best Video Editor API 2025: Top Features for Developers',
            'description': 'Discover the best video editor API of 2025 with powerful features like video merging, picture-in-picture, subtitle burning, and vertical video conversion for mobile platforms.',
            'date': 'November 2025',
            'author': 'FFMPEG API Team',
            'image': '',
            'keywords': 'best video editor api, video editing api 2025, video api for developers, automated video editing, video processing api, cloud video editor api, rest api video editing, api video merger, programmatic video editing',
            'content_file': 'blog_best_video_editor_api.html'
        },
        'ffmpeg-api-guide': {
            'title': 'Complete Guide to FFMPEG API: Video Processing Made Simple',
            'description': 'Discover how FFMPEG API simplifies video merging, audio processing, subtitle addition, and format conversion with easy-to-use REST endpoints.',
            'date': 'November 2025',
            'author': 'FFMPEG API Team',
            'image': '',
            'keywords': 'ffmpeg api, video processing api, video merger api, audio processing, subtitle api, ffmpeg rest api, video api service',
            'content_file': 'blog_ffmpeg_api_guide.html'
        }
    }
    
    article = articles.get(slug)
    if not article:
        return render_template('404.html'), 404
    
    return render_template('blog_article.html', article=article)

@app.route('/sitemap.xml')
def sitemap():
    """Generate sitemap.xml for search engines"""
    pages = []
    
    # Static pages with priority and changefreq
    static_pages = [
        {'loc': url_for('index', _external=True), 'priority': '1.0', 'changefreq': 'daily'},
        {'loc': url_for('api_docs', _external=True), 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': url_for('pricing', _external=True), 'priority': '0.9', 'changefreq': 'weekly'},
        {'loc': url_for('blog', _external=True), 'priority': '0.8', 'changefreq': 'weekly'},
        {'loc': url_for('blog_article', slug='best-tools-vibe-coding-2025', _external=True), 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': url_for('blog_article', slug='best-video-editor-api-2025', _external=True), 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': url_for('blog_article', slug='ffmpeg-api-guide', _external=True), 'priority': '0.8', 'changefreq': 'monthly'},
        {'loc': url_for('contact', _external=True), 'priority': '0.7', 'changefreq': 'monthly'},
        {'loc': url_for('auth.login', _external=True), 'priority': '0.6', 'changefreq': 'monthly'},
        {'loc': url_for('auth.register', _external=True), 'priority': '0.6', 'changefreq': 'monthly'},
    ]
    
    for page in static_pages:
        pages.append(page)
    
    # Build XML
    sitemap_xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    sitemap_xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    
    for page in pages:
        sitemap_xml.append('  <url>')
        sitemap_xml.append(f'    <loc>{page["loc"]}</loc>')
        sitemap_xml.append(f'    <priority>{page["priority"]}</priority>')
        sitemap_xml.append(f'    <changefreq>{page["changefreq"]}</changefreq>')
        sitemap_xml.append('  </url>')
    
    sitemap_xml.append('</urlset>')
    
    response = Response('\n'.join(sitemap_xml), mimetype='application/xml')
    return response

@app.route('/robots.txt')
def robots():
    """Generate robots.txt for search engine crawlers"""
    robots_txt = [
        'User-agent: *',
        'Allow: /',
        'Allow: /docs',
        'Allow: /pricing',
        'Allow: /blog',
        'Allow: /contact',
        'Disallow: /dashboard',
        'Disallow: /admin',
        'Disallow: /profile',
        'Disallow: /api/',
        'Disallow: /download/',
        'Disallow: /storage/',
        '',
        f'Sitemap: {url_for("sitemap", _external=True)}',
    ]
    
    response = Response('\n'.join(robots_txt), mimetype='text/plain')
    return response

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
def safe_update_job_status(job, status, error_message=None):
    """Safely update job status with proper error handling for database connection issues"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Create a fresh query to ensure we have the latest job state
            fresh_job = Job.query.filter_by(job_id=job.job_id).first()
            if fresh_job:
                fresh_job.status = status
                if error_message:
                    fresh_job.error_message = error_message
                fresh_job.updated_at = datetime.utcnow()
                db.session.commit()
                logging.info(f"Successfully updated job {job.job_id} status to {status}")
                return True
            else:
                logging.error(f"Job {job.job_id} not found during status update")
                return False
        except (OperationalError, PendingRollbackError) as db_error:
            logging.warning(f"Database error updating job {job.job_id} status (attempt {attempt + 1}/{max_retries}): {str(db_error)}")
            db.session.rollback()
            db.session.remove()  # Remove stale connection from pool
            if attempt == max_retries - 1:
                logging.error(f"Failed to update job {job.job_id} status after {max_retries} attempts")
                return False
            # Wait a bit before retrying with fresh connection
            import time
            time.sleep(1.0)  # Increased wait time for connection recovery
        except Exception as e:
            logging.error(f"Unexpected error updating job {job.job_id} status: {str(e)}")
            db.session.rollback()
            db.session.remove()
            return False
    return False

def safe_set_result_data(job_id, result_data):
    """Safely set job result data with proper error handling for database connection issues"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Create a fresh query to get the job
            job = Job.query.filter_by(job_id=job_id).first()
            if job:
                job.result_data = json.dumps(result_data)
                job.updated_at = datetime.utcnow()
                db.session.commit()
                logging.info(f"Successfully set result data for job {job_id}")
                return True
            else:
                logging.error(f"Job {job_id} not found during result data update")
                return False
        except (OperationalError, PendingRollbackError) as db_error:
            logging.warning(f"Database error setting result data for job {job_id} (attempt {attempt + 1}/{max_retries}): {str(db_error)}")
            db.session.rollback()
            db.session.remove()  # Remove stale connection from pool
            if attempt == max_retries - 1:
                logging.error(f"Failed to set result data for job {job_id} after {max_retries} attempts")
                return False
            # Wait a bit before retrying with fresh connection
            import time
            time.sleep(1.0)  # Increased wait time for connection recovery
        except Exception as e:
            logging.error(f"Unexpected error setting result data for job {job_id}: {str(e)}")
            db.session.rollback()
            db.session.remove()
            return False
    return False

def safe_update_job_status_by_id(job_id, status, error_message=None):
    """Safely update job status by job ID with proper error handling for database connection issues"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Create a fresh query to get the job
            job = Job.query.filter_by(job_id=job_id).first()
            if job:
                job.status = status
                if error_message:
                    job.error_message = error_message
                job.updated_at = datetime.utcnow()
                db.session.commit()
                logging.info(f"Successfully updated job {job_id} status to {status}")
                return True
            else:
                logging.error(f"Job {job_id} not found during status update")
                return False
        except (OperationalError, PendingRollbackError) as db_error:
            logging.warning(f"Database error updating job {job_id} status (attempt {attempt + 1}/{max_retries}): {str(db_error)}")
            db.session.rollback()
            db.session.remove()  # Remove stale connection from pool
            if attempt == max_retries - 1:
                logging.error(f"Failed to update job {job_id} status after {max_retries} attempts")
                return False
            # Wait a bit before retrying with fresh connection
            import time
            time.sleep(1.0)  # Increased wait time for connection recovery
        except Exception as e:
            logging.error(f"Unexpected error updating job {job_id} status: {str(e)}")
            db.session.rollback()
            db.session.remove()
            return False
    return False

def process_job_async(job_id):
    """Process a job asynchronously in background thread"""
    with app.app_context():
        job = None
        try:
            job = Job.query.filter_by(job_id=job_id).first()
            if not job:
                logging.error(f"Job {job_id} not found")
                return
            
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
            elif job.job_type == 'split_audio_segments':
                result = process_split_audio_segments_job(job, input_data)
            elif job.job_type == 'add_subtitles':
                result = process_add_subtitles_job(job, input_data)
            elif job.job_type == 'convert_to_vertical':
                result = process_convert_to_vertical_job(job, input_data)
            else:
                safe_update_job_status_by_id(job_id, 'failed', f'Unknown job type: {job.job_type}')
                return
            
            if result['success']:
                # Try to set result data safely, but mark as completed even if this fails
                result_data_success = safe_set_result_data(job_id, result)
                if result_data_success:
                    safe_update_job_status_by_id(job_id, 'completed')
                else:
                    # Job succeeded but couldn't save result data - still mark as completed
                    logging.warning(f"Job {job_id} completed successfully but couldn't save result data due to DB issues")
                    safe_update_job_status_by_id(job_id, 'completed')
            else:
                safe_update_job_status_by_id(job_id, 'failed', result.get('error', 'Unknown error'))
                
        except (OperationalError, PendingRollbackError) as db_error:
            logging.error(f"Database error processing job {job_id}: {str(db_error)}")
            # Roll back the session due to database connection issues
            db.session.rollback()
            # Try to update job status with a fresh transaction
            safe_update_job_status_by_id(job_id, 'failed', f'Database connection error: {str(db_error)}')
        except Exception as e:
            logging.error(f"Error processing job {job_id}: {str(e)}")
            safe_update_job_status_by_id(job_id, 'failed', str(e))
        finally:
            # Always release the database connection back to the pool
            # This is critical for background threads to prevent pool exhaustion
            db.session.remove()

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
            # Fix for Replit: Generate proper URL based on environment
            if os.environ.get('REPLIT_DEPLOYMENT'):
                # In production deployment
                download_url = f"https://ffmpegapi.net/download/{output_filename}"
            elif os.environ.get('REPLIT_DEV_DOMAIN'):
                # In Replit development environment
                download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
            else:
                # Local environment
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
        dimensions = input_data.get('dimensions')
        subtitle_url = input_data.get('subtitle_url')
        watermark_url = input_data.get('watermark_url')
        
        downloaded_videos = []
        audio_path = None
        subtitle_path = None
        watermark_path = None
        
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
                return {'success': False, 'error': f'Failed to download subtitle: {message}'}
        
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
                return {'success': False, 'error': f'Failed to download watermark: {message}'}
        
        # Note: Removed aspect ratio check since we now handle different aspect ratios
        # during the normalization process in merge_videos_with_ffmpeg
        
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
            # Generate filename for subtitled video
            subtitled_filename = f"{request_id}_merged_subtitled_videos.mp4"
            subtitled_output_path = os.path.join(OUTPUT_FOLDER, subtitled_filename)
            
            # Add subtitles to the merged video
            subtitle_success, subtitle_message = add_subtitles_with_ffmpeg(output_path, subtitle_path, subtitled_output_path)
            
            # Cleanup subtitle file
            cleanup_file(subtitle_path)
            
            if subtitle_success:
                # Remove the non-subtitled version and use the subtitled one
                cleanup_file(output_path)
                output_path = subtitled_output_path
                output_filename = subtitled_filename
                message = f"Videos merged and subtitles added successfully"
                subtitles_added = True
            else:
                # Cleanup subtitle file if it failed
                cleanup_file(subtitled_output_path)
                return {'success': False, 'error': f'Video merge succeeded but subtitle addition failed: {subtitle_message}'}
        elif subtitle_path:
            # Cleanup subtitle file if merge failed
            cleanup_file(subtitle_path)
        
        # Add watermark if provided and processing was successful so far
        if success and watermark_path:
            # Generate filename for watermarked video
            watermarked_filename = f"{request_id}_merged_watermarked_videos.mp4"
            watermarked_output_path = os.path.join(OUTPUT_FOLDER, watermarked_filename)
            
            # Add watermark to the video (could be merged or merged+subtitled)
            watermark_success, watermark_message = add_watermark_with_ffmpeg(output_path, watermark_path, watermarked_output_path)
            
            # Cleanup watermark file
            cleanup_file(watermark_path)
            
            if watermark_success:
                # Remove the non-watermarked version and use the watermarked one
                cleanup_file(output_path)
                output_path = watermarked_output_path
                output_filename = watermarked_filename
                # Update message to reflect all processing done
                if subtitles_added:
                    message = f"Videos merged, subtitles and watermark added successfully"
                else:
                    message = f"Videos merged and watermark added successfully"
            else:
                # Cleanup watermark file if it failed
                cleanup_file(watermarked_output_path)
                return {'success': False, 'error': f'Video processing succeeded but watermark addition failed: {watermark_message}'}
        elif watermark_path:
            # Cleanup watermark file if merge failed
            cleanup_file(watermark_path)
        
        if success:
            # Upload to storage for persistence
            storage_url = upload_to_storage(output_path, output_filename)
            
            if storage_url:
                # Clean up local file after successful upload
                cleanup_file(output_path)
                
                return {
                    'success': True,
                    'message': message,
                    'download_url': storage_url,
                    'filename': output_filename
                }
            else:
                # Fallback to local download if storage upload fails
                logging.warning("Storage upload failed, falling back to local download")
                
                # Fix for Replit: Generate proper URL based on environment
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    # In production deployment
                    download_url = f"https://ffmpegapi.net/download/{output_filename}"
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    # In Replit development environment
                    download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                else:
                    # Local environment
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
        logging.error(f"[ASYNC_MERGE_VIDEOS] Error in process_merge_videos_job: {str(e)}")
        logging.error(f"[ASYNC_MERGE_VIDEOS] Full traceback:", exc_info=True)
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
        
        # Release database connection before long FFMPEG processing to prevent pool exhaustion
        db.session.close()
        
        # Create picture-in-picture video using FFMPEG
        success, message = create_picture_in_picture_with_ffmpeg(
            main_video_path, pip_video_path, output_path, position, scale, audio_option
        )
        
        # Cleanup input files
        cleanup_file(main_video_path)
        cleanup_file(pip_video_path)
        
        if success:
            # Fix for Replit: Generate proper URL based on environment
            if os.environ.get('REPLIT_DEPLOYMENT'):
                # In production deployment
                download_url = f"https://ffmpegapi.net/download/{output_filename}"
            elif os.environ.get('REPLIT_DEV_DOMAIN'):
                # In Replit development environment
                download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
            else:
                # Local environment
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
                    # Fix for Replit: Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        # In production deployment
                        download_url = f"https://ffmpegapi.net/download/{request_id}/{filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        # In Replit development environment
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{request_id}/{filename}"
                    else:
                        # Local environment
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

def process_split_audio_segments_job(job, input_data):
    """Process split_audio_segments job - splits audio by segment duration"""
    try:
        request_id = str(uuid.uuid4())
        audio_path = ""
        
        # Handle URL-based input
        if 'audio_url' in input_data:
            audio_url = input_data['audio_url']
            segment_duration = input_data.get('segment_duration', 30)
            
            # Validate segment_duration parameter
            if not isinstance(segment_duration, (int, float)) or segment_duration < 1 or segment_duration > 3600:
                return {'success': False, 'error': 'segment_duration must be a number between 1 and 3600 seconds'}
            
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
            
            # Split audio using FFMPEG by segment duration
            success, message, output_files = split_audio_by_segments_with_ffmpeg(audio_path, output_dir, segment_duration)
            
            # Cleanup input file
            cleanup_file(audio_path)
            
            if success:
                # Generate download URLs for all segments
                download_urls = []
                for filename in output_files:
                    # Fix for Replit: Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        # In production deployment
                        download_url = f"https://ffmpegapi.net/download/{request_id}/{filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        # In Replit development environment
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{request_id}/{filename}"
                    else:
                        # Local environment
                        download_url = url_for('download_file', filename=f"{request_id}/{filename}", _external=True)
                    download_urls.append({
                        'segment': filename,
                        'download_url': download_url
                    })
                
                return {
                    'success': True,
                    'message': message,
                    'segment_duration': segment_duration,
                    'total_segments': len(output_files),
                    'segments': download_urls
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
        logging.error(f"Error in process_split_audio_segments_job: {str(e)}")
        # Cleanup files on error
        if audio_path and os.path.exists(audio_path):
            cleanup_file(audio_path)
        return {'success': False, 'error': f'Server error: {str(e)}'}

def process_add_subtitles_job(job, input_data):
    """Process add_subtitles job"""
    try:
        request_id = str(uuid.uuid4())
        video_path = ""
        subtitle_path = ""
        
        # Handle URL-based inputs
        if 'video_url' in input_data and 'subtitle_url' in input_data:
            video_url = input_data['video_url']
            subtitle_url = input_data['subtitle_url']
            
            # Generate file paths
            video_path = os.path.join(UPLOAD_FOLDER, f"{request_id}_video.mp4")
            subtitle_path = os.path.join(UPLOAD_FOLDER, f"{request_id}_subtitle.ass")
            
            # Download video file
            success, message = download_file_from_url(video_url, video_path, "video")
            if not success:
                cleanup_file(video_path)
                return {'success': False, 'error': message}
            
            # Download subtitle file
            success, message = download_file_from_url(subtitle_url, subtitle_path, "subtitle")
            if not success:
                cleanup_file(video_path)
                cleanup_file(subtitle_path)
                return {'success': False, 'error': message}
            
            # Generate output filename
            output_filename = f"{request_id}_subtitled_video.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Add subtitles using FFMPEG
            success, message = add_subtitles_with_ffmpeg(video_path, subtitle_path, output_path)
            
            # Cleanup downloaded files
            cleanup_file(video_path)
            cleanup_file(subtitle_path)
            
            if success:
                # Upload to storage for persistence
                storage_url = upload_to_storage(output_path, output_filename)
                
                if storage_url:
                    # Clean up local file after successful upload
                    cleanup_file(output_path)
                    
                    return {
                        'success': True,
                        'message': message,
                        'download_url': storage_url,
                        'filename': output_filename
                    }
                else:
                    # Fallback to local download if storage upload fails
                    logging.warning("Storage upload failed, falling back to local download")
                    
                    # Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                    else:
                        download_url = url_for('download_file', filename=output_filename, _external=True)
                    
                    return {
                        'success': True,
                        'message': f"{message} (Note: Using temporary local storage - download soon)",
                        'download_url': download_url,
                        'filename': output_filename
                    }
            else:
                return {'success': False, 'error': message}
        else:
            return {'success': False, 'error': 'Both video_url and subtitle_url are required'}
            
    except Exception as e:
        logging.error(f"Error in process_add_subtitles_job: {str(e)}")
        # Cleanup files on error
        if video_path and os.path.exists(video_path):
            cleanup_file(video_path)
        if subtitle_path and os.path.exists(subtitle_path):
            cleanup_file(subtitle_path)
        return {'success': False, 'error': f'Server error: {str(e)}'}

def process_convert_to_vertical_job(job, input_data):
    """Process convert_to_vertical job"""
    try:
        request_id = str(uuid.uuid4())
        video_url = input_data.get('video_url')
        watermark_url = input_data.get('watermark_url')
        
        if video_url:
            # Download video file
            video_ext = video_url.split('.')[-1].lower() if '.' in video_url else 'mp4'
            video_filename = f"{request_id}_video.{video_ext}"
            video_path = os.path.join(UPLOAD_FOLDER, video_filename)
            
            success, message = download_file_from_url(video_url, video_path, "video")
            if not success:
                return {'success': False, 'error': message}
            
            try:
                # Download watermark if provided
                watermark_path = None
                if watermark_url:
                    watermark_ext = watermark_url.split('.')[-1].lower() if '.' in watermark_url else 'png'
                    watermark_filename = f"{request_id}_watermark.{watermark_ext}"
                    watermark_path = os.path.join(UPLOAD_FOLDER, watermark_filename)
                    
                    success, message = download_file_from_url(watermark_url, watermark_path, "watermark")
                    if not success:
                        cleanup_file(video_path)
                        return {'success': False, 'error': f'Failed to download watermark: {message}'}
                
                # Generate output filename
                output_filename = f"{request_id}_vertical.mp4"
                output_path = os.path.join(OUTPUT_FOLDER, output_filename)
                
                # Convert video using FFMPEG
                success, message = convert_to_vertical_with_ffmpeg(video_path, output_path, watermark_path)
                
                # Cleanup downloaded files
                cleanup_file(video_path)
                if watermark_path:
                    cleanup_file(watermark_path)
                
                if success:
                    # Upload to storage for persistence
                    storage_url = upload_to_storage(output_path, output_filename)
                    
                    if storage_url:
                        # Clean up local file after successful upload
                        cleanup_file(output_path)
                        
                        return {
                            'success': True,
                            'message': message,
                            'download_url': storage_url,
                            'filename': output_filename
                        }
                    else:
                        # Fallback to local download if storage upload fails
                        logging.warning("Storage upload failed, falling back to local download")
                        
                        # Generate proper URL based on environment
                        if os.environ.get('REPLIT_DEPLOYMENT'):
                            download_url = f"https://ffmpegapi.net/download/{output_filename}"
                        elif os.environ.get('REPLIT_DEV_DOMAIN'):
                            download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                        else:
                            download_url = url_for('download_file', filename=output_filename, _external=True)
                        
                        return {
                            'success': True,
                            'message': f"{message} (Note: Using temporary local storage - download soon)",
                            'download_url': download_url,
                            'filename': output_filename
                        }
                else:
                    return {'success': False, 'error': message}
            except Exception as e:
                # Cleanup files on error
                cleanup_file(video_path)
                if watermark_path:
                    cleanup_file(watermark_path)
                raise e
        else:
            return {'success': False, 'error': 'video_url is required'}
            
    except Exception as e:
        logging.error(f"Error in process_convert_to_vertical_job: {str(e)}")
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
@log_api_request
@require_api_key
def split_audio():
    """API endpoint to split audio into equal parts (sync/async)"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[SPLIT_AUDIO] Request received from API key: {api_key[:20]}...")
    logging.info(f"[SPLIT_AUDIO] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[SPLIT_AUDIO] JSON data: {request.get_json()}")
    logging.info(f"[SPLIT_AUDIO] Form data: {dict(request.form)}")
    
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
                    # Fix for Replit: Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        # In production deployment
                        download_url = f"https://ffmpegapi.net/download/{request_id}/{filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        # In Replit development environment
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{request_id}/{filename}"
                    else:
                        # Local environment
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

@app.route('/api/split_audio_segments', methods=['POST'])
@log_api_request
@require_api_key
def split_audio_segments():
    """API endpoint to split audio by segment duration (sync/async)"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[SPLIT_AUDIO_SEGMENTS] Request received from API key: {api_key[:20]}...")
    logging.info(f"[SPLIT_AUDIO_SEGMENTS] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[SPLIT_AUDIO_SEGMENTS] JSON data: {request.get_json()}")
    logging.info(f"[SPLIT_AUDIO_SEGMENTS] Form data: {dict(request.form)}")
    
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
            job.job_type = 'split_audio_segments'
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
        segment_duration = data.get('segment_duration', 30)  # Default to 30 seconds
        
        # Validate segment_duration parameter
        if not isinstance(segment_duration, (int, float)) or segment_duration < 1 or segment_duration > 3600:
            return jsonify({
                'success': False,
                'error': 'segment_duration must be a number between 1 and 3600 seconds'
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
            
            # Split audio using FFMPEG by segment duration
            success, message, output_files = split_audio_by_segments_with_ffmpeg(audio_path, output_dir, segment_duration)
            
            # Cleanup input file
            cleanup_file(audio_path)
            
            if success:
                # Generate download URLs for all segments
                download_urls = []
                for filename in output_files:
                    # Fix for Replit: Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        # In production deployment
                        download_url = f"https://ffmpegapi.net/download/{request_id}/{filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        # In Replit development environment
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{request_id}/{filename}"
                    else:
                        # Local environment
                        download_url = url_for('download_file', filename=f"{request_id}/{filename}", _external=True)
                    download_urls.append({
                        'segment': filename,
                        'download_url': download_url
                    })
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'segment_duration': segment_duration,
                    'total_segments': len(output_files),
                    'segments': download_urls
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
            
            logging.error(f"Audio segment splitting error: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Server error: {str(e)}'
            }), 500
            
    except Exception as e:
        logging.error(f"Split audio segments API error: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

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

def trim_audio_with_ffmpeg(audio_path, output_path, desired_length, fade_duration=0):
    """Trim audio to desired length using FFMPEG with optional fade out"""
    try:
        # Build audio filter for fade effect
        audio_filter = None
        if fade_duration > 0:
            # Calculate fade start time (desired_length - fade_duration)
            fade_start = max(0, desired_length - fade_duration)
            audio_filter = f"afade=t=out:st={fade_start}:d={fade_duration}"
            logging.info(f"[TRIM_AUDIO] Adding fade out: start={fade_start}s, duration={fade_duration}s")
        
        # First, try with stream copy if no fade is needed (most efficient)
        if fade_duration == 0:
            cmd = [
                'ffmpeg',
                '-i', audio_path,
                '-t', str(desired_length),  # Duration in seconds
                '-c:a', 'copy',  # Copy audio stream without re-encoding
                '-y',
                output_path
            ]
            
            logging.info(f"[TRIM_AUDIO] Attempting trim with stream copy to {desired_length} seconds")
            logging.info(f"[TRIM_AUDIO] Command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                logging.info("[TRIM_AUDIO] Audio trimming completed successfully with stream copy")
                return True, "Audio trimmed successfully"
            else:
                logging.warning(f"[TRIM_AUDIO] Stream copy failed: {result.stderr}")
        
        # If stream copy failed or fade is needed, use MP3 re-encoding with optional filter
        cmd_reencode = [
            'ffmpeg',
            '-i', audio_path,
            '-t', str(desired_length)  # Duration in seconds
        ]
        
        # Add audio filter if fade is specified
        if audio_filter:
            cmd_reencode.extend(['-af', audio_filter])
        
        # Add encoding settings
        cmd_reencode.extend([
            '-c:a', 'mp3',  # Use MP3 codec (more compatible)
            '-b:a', '192k',  # Audio bitrate
            '-y',
            output_path
        ])
        
        action_desc = "with fade effect" if fade_duration > 0 else "with MP3 re-encoding"
        logging.info(f"[TRIM_AUDIO] Processing {action_desc}")
        logging.info(f"[TRIM_AUDIO] Command: {' '.join(cmd_reencode)}")
        result = subprocess.run(cmd_reencode, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logging.info(f"[TRIM_AUDIO] Audio trimming completed successfully {action_desc}")
            return True, "Audio trimmed successfully"
        else:
            logging.error(f"[TRIM_AUDIO] Audio processing failed: {result.stderr}")
            return False, f"Audio trimming failed: {result.stderr}"
            
    except subprocess.TimeoutExpired:
        logging.error("[TRIM_AUDIO] Audio trimming timed out")
        return False, "Audio trimming processing timed out"
    except Exception as e:
        logging.error(f"[TRIM_AUDIO] Audio trimming error: {str(e)}")
        return False, f"Audio trimming error: {str(e)}"

@app.route('/api/add_subtitles', methods=['POST'])
@log_api_request
@require_api_key
def add_subtitles():
    """API endpoint to add subtitles to video"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[ADD_SUBTITLES] Request received from API key: {api_key[:20]}...")
    logging.info(f"[ADD_SUBTITLES] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[ADD_SUBTITLES] JSON data: {request.get_json()}")
    logging.info(f"[ADD_SUBTITLES] Form data: {dict(request.form)}")
    
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
            job.job_type = 'add_subtitles'
            job.status = 'pending'
            job.set_input_data(data)
            
            db.session.add(job)
            db.session.commit()
            
            # Start background processing
            from threading import Thread
            thread = Thread(target=process_job_async, args=(job.id,))
            thread.start()
            
            return jsonify({
                'success': True,
                'job_id': job.id,
                'status': 'pending',
                'message': 'Job submitted for async processing. Use /api/job/{job_id}/status to check progress.',
                'status_url': url_for('job_status', job_id=job.id, _external=True)
            })
        
        # Synchronous processing
        if not data:
            return jsonify({
                'success': False,
                'error': 'Invalid JSON data'
            }), 400
            
        video_url = data.get('video_url')
        subtitle_url = data.get('subtitle_url')
        
        # Validate required parameters
        if not video_url or not subtitle_url:
            return jsonify({
                'success': False,
                'error': 'Both video_url and subtitle_url are required'
            }), 400
            
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Set up file paths
        video_path = os.path.join(UPLOAD_FOLDER, f"{request_id}_video.mp4")
        subtitle_path = os.path.join(UPLOAD_FOLDER, f"{request_id}_subtitle.ass")
        
        try:
            # Download video file
            success, message = download_file_from_url(video_url, video_path, "video")
            if not success:
                cleanup_file(video_path)
                return jsonify({
                    'success': False,
                    'error': message
                }), 400
            
            # Download subtitle file
            success, message = download_file_from_url(subtitle_url, subtitle_path, "subtitle")
            if not success:
                cleanup_file(video_path)
                cleanup_file(subtitle_path)
                return jsonify({
                    'success': False,
                    'error': message
                }), 400
            
            # Generate output filename
            output_filename = f"{request_id}_subtitled_video.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Add subtitles using FFMPEG
            success, message = add_subtitles_with_ffmpeg(video_path, subtitle_path, output_path)
            
            # Cleanup downloaded files
            cleanup_file(video_path)
            cleanup_file(subtitle_path)
            
            if success:
                # Upload to storage for persistence
                storage_url = upload_to_storage(output_path, output_filename)
                
                if storage_url:
                    # Clean up local file after successful upload
                    cleanup_file(output_path)
                    
                    return jsonify({
                        'success': True,
                        'message': message,
                        'download_url': storage_url,
                        'filename': output_filename
                    })
                else:
                    # Fallback to local download if storage upload fails
                    logging.warning("Storage upload failed, falling back to local download")
                    
                    # Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                    else:
                        download_url = url_for('download_file', filename=output_filename, _external=True)
                    
                    return jsonify({
                        'success': True,
                        'message': f"{message} (Note: Using temporary local storage - download soon)",
                        'download_url': download_url,
                        'filename': output_filename
                    })
            else:
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as e:
            # Cleanup downloaded files on error
            cleanup_file(video_path)
            cleanup_file(subtitle_path)
            raise e
            
    except Exception as e:
        logging.error(f"[ADD_SUBTITLES] Error in add_subtitles: {str(e)}")
        logging.error(f"[ADD_SUBTITLES] Full traceback:", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/trim_audio', methods=['POST'])
@log_api_request
@require_api_key
def trim_audio():
    """API endpoint to trim audio to desired length"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[TRIM_AUDIO] Request received from API key: {api_key[:20]}...")
    logging.info(f"[TRIM_AUDIO] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[TRIM_AUDIO] JSON data: {request.get_json()}")
    logging.info(f"[TRIM_AUDIO] Form data: {dict(request.form)}")
    
    try:
        request_id = str(uuid.uuid4())
        
        # Get parameters from JSON or form data
        if request.is_json:
            data = request.get_json()
            audio_url = data.get('audio_url')
            desired_length = data.get('desired_length')
            fade_duration = data.get('fade_duration', 0)
        else:
            audio_url = request.form.get('audio_url')
            desired_length = request.form.get('desired_length')
            fade_duration = request.form.get('fade_duration', 0)
        
        # Validate inputs
        if not audio_url:
            return jsonify({
                'success': False,
                'error': 'audio_url is required'
            }), 400
        
        if not desired_length:
            return jsonify({
                'success': False,
                'error': 'desired_length is required'
            }), 400
        
        try:
            desired_length = float(desired_length)
            if desired_length <= 0:
                return jsonify({
                    'success': False,
                    'error': 'desired_length must be a positive number'
                }), 400
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'desired_length must be a valid number'
            }), 400
        
        try:
            fade_duration = float(fade_duration)
            if fade_duration < 0:
                return jsonify({
                    'success': False,
                    'error': 'fade_duration must be a non-negative number'
                }), 400
            if fade_duration >= desired_length:
                return jsonify({
                    'success': False,
                    'error': 'fade_duration must be less than desired_length'
                }), 400
        except ValueError:
            return jsonify({
                'success': False,
                'error': 'fade_duration must be a valid number'
            }), 400
        
        # Download audio file
        audio_filename = f"{request_id}_audio.mp3"
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        
        success, message = download_file_from_url(audio_url, audio_path, "audio")
        if not success:
            return jsonify({
                'success': False,
                'error': message
            }), 400
        
        try:
            # Generate output filename
            output_filename = f"{request_id}_trimmed_audio.mp3"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Trim audio using FFMPEG
            success, message = trim_audio_with_ffmpeg(audio_path, output_path, desired_length, fade_duration)
            
            # Cleanup downloaded file
            cleanup_file(audio_path)
            
            if success:
                # Upload to storage for persistence
                storage_url = upload_to_storage(output_path, output_filename)
                
                if storage_url:
                    # Clean up local file after successful upload
                    cleanup_file(output_path)
                    
                    return jsonify({
                        'success': True,
                        'message': f"Audio trimmed to {desired_length} seconds successfully",
                        'download_url': storage_url,
                        'filename': output_filename,
                        'trimmed_length': desired_length
                    })
                else:
                    # Fallback to local download if storage upload fails
                    logging.warning("Storage upload failed, falling back to local download")
                    
                    # Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                    else:
                        download_url = url_for('download_file', filename=output_filename, _external=True)
                    
                    return jsonify({
                        'success': True,
                        'message': f"Audio trimmed to {desired_length} seconds successfully (Note: Using temporary local storage - download soon)",
                        'download_url': download_url,
                        'filename': output_filename,
                        'trimmed_length': desired_length
                    })
            else:
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as e:
            # Cleanup downloaded file on error
            cleanup_file(audio_path)
            raise e
            
    except Exception as e:
        logging.error(f"[TRIM_AUDIO] Error in trim_audio: {str(e)}")
        logging.error(f"[TRIM_AUDIO] Full traceback:", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/convert_to_vertical', methods=['POST'])
@log_api_request
@require_api_key
def convert_to_vertical():
    """API endpoint to convert horizontal videos to vertical format (sync/async)"""
    # Log full request details for debugging
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[CONVERT_TO_VERTICAL] Request received from API key: {api_key[:20] if api_key else 'None'}...")
    logging.info(f"[CONVERT_TO_VERTICAL] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[CONVERT_TO_VERTICAL] JSON data: {request.get_json()}")
    
    try:
        # Parse JSON request
        if not request.is_json:
            return jsonify({
                'success': False,
                'error': 'Content-Type must be application/json'
            }), 400
        
        data = request.get_json()
        video_url = data.get('video_url')
        watermark_url = data.get('watermark_url')  # Optional
        async_processing = data.get('async', False)
        
        # Validate required parameters
        if not video_url:
            return jsonify({
                'success': False,
                'error': 'video_url is required'
            }), 400
        
        # If async processing is requested, create job and return immediately
        if async_processing:
            job = Job(
                user_id=current_user.id,
                job_type='convert_to_vertical',
                status='pending'
            )
            job.set_input_data({
                'video_url': video_url,
                'watermark_url': watermark_url
            })
            db.session.add(job)
            db.session.commit()
            
            # Start background processing
            thread = threading.Thread(target=process_job_async, args=(job.job_id,))
            thread.start()
            
            status_url = url_for('get_job_status', job_id=job.job_id, _external=True)
            
            return jsonify({
                'success': True,
                'job_id': job.job_id,
                'status': 'pending',
                'message': 'Job submitted for async processing. Use /api/job/{job_id}/status to check progress.',
                'status_url': status_url
            })
        
        # Synchronous processing
        request_id = str(uuid.uuid4())
        
        # Download video file
        video_ext = video_url.split('.')[-1].lower() if '.' in video_url else 'mp4'
        video_filename = f"{request_id}_video.{video_ext}"
        video_path = os.path.join(UPLOAD_FOLDER, video_filename)
        
        success, message = download_file_from_url(video_url, video_path, "video")
        if not success:
            return jsonify({
                'success': False,
                'error': message
            }), 400
        
        try:
            # Download watermark if provided
            watermark_path = None
            if watermark_url:
                watermark_ext = watermark_url.split('.')[-1].lower() if '.' in watermark_url else 'png'
                watermark_filename = f"{request_id}_watermark.{watermark_ext}"
                watermark_path = os.path.join(UPLOAD_FOLDER, watermark_filename)
                
                success, message = download_file_from_url(watermark_url, watermark_path, "watermark")
                if not success:
                    cleanup_file(video_path)
                    return jsonify({
                        'success': False,
                        'error': f'Failed to download watermark: {message}'
                    }), 400
            
            # Generate output filename
            output_filename = f"{request_id}_vertical.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            
            # Convert video using FFMPEG
            success, message = convert_to_vertical_with_ffmpeg(video_path, output_path, watermark_path)
            
            # Cleanup downloaded files
            cleanup_file(video_path)
            if watermark_path:
                cleanup_file(watermark_path)
            
            if success:
                # Upload to storage for persistence
                storage_url = upload_to_storage(output_path, output_filename)
                
                if storage_url:
                    # Clean up local file after successful upload
                    cleanup_file(output_path)
                    
                    return jsonify({
                        'success': True,
                        'message': message,
                        'download_url': storage_url,
                        'filename': output_filename
                    })
                else:
                    # Fallback to local download if storage upload fails
                    logging.warning("Storage upload failed, falling back to local download")
                    
                    # Generate proper URL based on environment
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        download_url = f"https://ffmpegapi.net/download/{output_filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                    else:
                        download_url = url_for('download_file', filename=output_filename, _external=True)
                    
                    return jsonify({
                        'success': True,
                        'message': f"{message} (Note: Using temporary local storage - download soon)",
                        'download_url': download_url,
                        'filename': output_filename
                    })
            else:
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as e:
            # Cleanup downloaded files on error
            cleanup_file(video_path)
            if watermark_path:
                cleanup_file(watermark_path)
            raise e
            
    except Exception as e:
        logging.error(f"[CONVERT_TO_VERTICAL] Error: {str(e)}")
        logging.error(f"[CONVERT_TO_VERTICAL] Full traceback:", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)