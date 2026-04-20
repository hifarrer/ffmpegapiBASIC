import os
import logging
import subprocess
import uuid
import tempfile
import threading
import json
import re
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, url_for, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import OperationalError, PendingRollbackError
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
import mimetypes
import shutil

from models import db, User, ApiKey, SubscriptionPlan, StripeSettings, UserSubscription, SiteSettings, Job, ApiLog, SITE_DEFAULT_API_KEY
import time
from storage_utils import upload_to_storage, get_storage_download_url

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
# Railway/Neon: DATABASE_URL is set by Railway Postgres; or set NEON_DATABASE_URL (or DATABASE_URL) in Variables
_database_url = os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL")
if not _database_url:
    raise RuntimeError(
        "Database URL is required. Set DATABASE_URL or NEON_DATABASE_URL in Railway Variables "
        "(e.g. add a Postgres plugin or paste your Neon connection string)."
    )
# SQLAlchemy/psycopg2 require postgresql://; Railway may give postgres://
if _database_url.startswith("postgres://"):
    _database_url = "postgresql://" + _database_url[11:]
app.config["SQLALCHEMY_DATABASE_URI"] = _database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    'pool_pre_ping': True,
    "pool_recycle": 300,
    "pool_size": 20,  # Increased for concurrent video processing
    "max_overflow": 40,  # Increased for burst traffic
    "pool_timeout": 120,  # Increased to handle connection pool exhaustion
}

# Production detection: Replit or Railway (and similar platforms)
_IS_PRODUCTION = bool(os.environ.get('REPLIT_DEPLOYMENT') or os.environ.get('RAILWAY_ENVIRONMENT'))

# URL building configuration for async jobs
# Use localhost for development, production domain for production.
# SERVER_NAME is required in production so url_for(..., _external=True) works
# in background/async jobs (no active request context).
if _IS_PRODUCTION:
    app.config['PREFERRED_URL_SCHEME'] = 'https'
    app.config['APPLICATION_ROOT'] = '/'
    # Required for URL generation outside request context (e.g. async jobs).
    # Railway sets RAILWAY_PUBLIC_DOMAIN; override with SERVER_NAME if needed.
    app.config['SERVER_NAME'] = os.environ.get('SERVER_NAME') or os.environ.get('RAILWAY_PUBLIC_DOMAIN')
    if not app.config['SERVER_NAME']:
        logging.warning(
            "Production: SERVER_NAME and RAILWAY_PUBLIC_DOMAIN are unset; "
            "absolute download/status URLs in async responses may be wrong."
        )
else:
    # Development environment - use localhost
    app.config['SERVER_NAME'] = 'localhost:5000'
    app.config['PREFERRED_URL_SCHEME'] = 'http'
    app.config['APPLICATION_ROOT'] = '/'

# Upload/output paths: prefer env (Railway volume) when set, else production /tmp or local dirs
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or (
    '/tmp/uploads' if _IS_PRODUCTION else 'uploads'
)
OUTPUT_FOLDER = os.environ.get("OUTPUT_FOLDER") or (
    '/tmp/outputs' if _IS_PRODUCTION else 'outputs'
)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg'}
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'wav', 'm4a'}


def ensure_storage_dirs():
    """Create all storage directories required by the app (Railway volume, /tmp, or local)."""
    dirs = [UPLOAD_FOLDER, OUTPUT_FOLDER]
    for d in dirs:
        try:
            os.makedirs(d, exist_ok=True)
            logging.info(f"Storage dir ready: {d}")
        except OSError as e:
            logging.error(f"Failed to create storage dir {d}: {e}")
            raise


ensure_storage_dirs()

# Initialize extensions
db.init_app(app)

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

def _subscription_limits_enabled():
    """Internal-only deployment: default to bypassing subscription/usage checks.
    Set BYPASS_SUBSCRIPTION_LIMITS=0 (or false/no/off) to enforce plan limits."""
    val = os.environ.get('BYPASS_SUBSCRIPTION_LIMITS', '1').strip().lower()
    bypass = val in ('1', 'true', 'yes', 'on')
    return not bypass


def require_api_key(f):
    """Decorator to require API key for API endpoints with usage limit checking"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check for API key in header, query param, or form data
        api_key = request.headers.get('X-API-Key') or request.args.get('api_key') or request.form.get('api_key')

        if not api_key:
            logging.warning(f"[AUTH] Missing API key for {request.method} {request.path}")
            return jsonify({
                'success': False,
                'error': 'API key is required. Please provide it in X-API-Key header, api_key query parameter, or form data.'
            }), 401

        masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else '***'
        logging.info(f"[AUTH] {request.method} {request.path} key={masked}")

        # Validate API key
        key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
        if not key_record:
            logging.warning(f"[AUTH] Invalid or inactive API key {masked} for {request.path}")
            return jsonify({
                'success': False,
                'error': 'Invalid or inactive API key.'
            }), 401

        user = key_record.user
        logging.info(f"[AUTH] Key ok user_id={user.id} username={user.username} key_id={key_record.id}")

        enforce_limits = _subscription_limits_enabled()
        subscription = None
        if enforce_limits:
            # Check user's subscription and API usage limits
            subscription = UserSubscription.query.filter_by(user_id=user.id, status='active').first()

            if not subscription:
                # Try to assign free plan if user has no subscription
                free_plan = SubscriptionPlan.query.filter_by(name='Free', is_active=True).first()
                if free_plan:
                    logging.info(f"[AUTH] No subscription for {user.username}; assigning Free plan id={free_plan.id}")
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
                    logging.warning(
                        f"[AUTH] No subscription for {user.username} and no active 'Free' plan. "
                        f"Set BYPASS_SUBSCRIPTION_LIMITS=1 for internal use, or create a Free plan."
                    )
                    return jsonify({
                        'success': False,
                        'error': 'No active subscription found. Please contact support.'
                    }), 403

            # Check if user can make API call
            if not subscription.can_make_api_call():
                plan_name = subscription.plan.name
                api_limit = subscription.plan.api_calls_per_month
                api_used = subscription.api_calls_used
                logging.warning(
                    f"[AUTH] Limit exceeded user={user.username} plan={plan_name} used={api_used}/{api_limit}"
                )

                return jsonify({
                    'success': False,
                    'error': f'API call limit exceeded. You have used {api_used}/{api_limit} calls for your {plan_name} plan. Please upgrade your plan to continue using the API.',
                    'current_plan': plan_name,
                    'api_calls_used': api_used,
                    'api_calls_limit': api_limit,
                }), 429

            # Increment API usage
            subscription.increment_api_usage()
        else:
            logging.info(f"[AUTH] Subscription limits bypassed (internal mode) for {user.username}")

        # Mark API key as used
        try:
            key_record.mark_used()
        except Exception as e:
            logging.warning(f"[AUTH] mark_used failed for key_id={key_record.id}: {e}")

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
            
            # Re-fetch user info after require_api_key has run (it sets these on request)
            final_user_id = getattr(request, 'api_user_id', None)
            final_username = getattr(request, 'api_username', None)
            final_api_key_id = getattr(request, 'api_key_id', None)
            
            # Log synchronously to ensure it's saved before response returns
            try:
                ApiLog.log_request(
                    endpoint=endpoint,
                    method=method,
                    user_id=final_user_id,
                    username=final_username,
                    api_key_id=final_api_key_id,
                    request_data=request_data,
                    response_data=response_data,
                    status_code=status_code,
                    error_message=error_message,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    processing_time_ms=processing_time_ms
                )
            except Exception as log_error:
                logging.error(f"Failed to save API log: {str(log_error)}", exc_info=True)
        
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

def validate_url(url):
    """Validate URL is safe to download from (not localhost, private IPs, etc.)"""
    from urllib.parse import urlparse
    import socket
    import ipaddress
    
    if not url:
        return False, "URL is empty"
    
    try:
        parsed = urlparse(url)
        
        # Check scheme
        if parsed.scheme not in ('http', 'https'):
            return False, f"Invalid URL scheme: {parsed.scheme}. Only http and https are allowed."
        
        # Get hostname
        hostname = parsed.hostname
        if not hostname:
            return False, "URL has no hostname"
        
        # Block localhost variations
        localhost_names = {'localhost', '127.0.0.1', '::1', '0.0.0.0'}
        if hostname.lower() in localhost_names:
            return False, f"localhost URLs are not allowed: {hostname}"
        
        # Try to resolve the hostname
        try:
            ip_addresses = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            if not ip_addresses:
                return False, f"Could not resolve hostname: {hostname}"
            
            # Check each resolved IP for private/local addresses
            for addr_info in ip_addresses:
                ip_str = addr_info[4][0]
                try:
                    ip_obj = ipaddress.ip_address(ip_str)
                    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                        return False, f"URL resolves to private/local IP address: {ip_str}"
                except ValueError:
                    pass
                    
        except socket.gaierror as e:
            return False, f"Could not resolve hostname '{hostname}': {str(e)}"
        except socket.timeout:
            return False, f"Timeout resolving hostname: {hostname}"
        
        return True, "URL is valid"
        
    except Exception as e:
        return False, f"Invalid URL format: {str(e)}"

def download_with_timeout(url, output_path, timeout=60, file_type="file"):
    """Download file from URL with timeout and proper error handling"""
    # Fast-path for files already produced by this app (e.g. /download/<file> URLs).
    # This avoids round-tripping over HTTPS to ourselves and hitting socket read timeouts.
    resolved_local, local_path = resolve_local_download_url(url)
    if resolved_local and local_path:
        try:
            shutil.copy2(local_path, output_path)
            logging.info(f"Copied local {file_type} from {local_path} to {output_path}")
            return True, f"{file_type.capitalize()} copied from local storage"
        except Exception as e:
            logging.warning(f"Failed local copy for {file_type} from {local_path}: {str(e)}")
    
    # First validate the URL
    is_valid, validation_msg = validate_url(url)
    if not is_valid:
        logging.error(f"URL validation failed for {url}: {validation_msg}")
        return False, validation_msg
    
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            logging.info(f"Downloading {file_type} from: {url} (attempt {attempt}/{max_attempts})")
            
            # Use requests with timeout for both connect and read.
            response = requests.get(url, timeout=(10, timeout), stream=True)
            response.raise_for_status()
            
            # Download in chunks to handle large files
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logging.info(f"Successfully downloaded {file_type} to {output_path}")
            return True, f"{file_type.capitalize()} downloaded successfully"

        except requests.exceptions.Timeout:
            logging.error(f"Timeout downloading {file_type} from {url} on attempt {attempt}/{max_attempts}")
            if attempt == max_attempts:
                return False, f"Download timed out after {timeout} seconds"
            time.sleep(1)
        except requests.exceptions.ConnectionError as e:
            logging.error(f"Connection error downloading {file_type} from {url}: {str(e)}")
            return False, f"Connection error: Could not connect to {url}"
        except requests.exceptions.HTTPError as e:
            logging.error(f"HTTP error downloading {file_type} from {url}: {str(e)}")
            return False, f"HTTP error: {e.response.status_code} - {e.response.reason}"
        except Exception as e:
            logging.error(f"Failed to download {file_type} from {url}: {str(e)}")
            return False, f"Failed to download {file_type}: {str(e)}"

    return False, f"Failed to download {file_type}"

def download_video_from_url(url, output_path):
    """Download video from URL to local path"""
    return download_with_timeout(url, output_path, timeout=120, file_type="video")

def download_file_from_url(url, output_path, file_type="file"):
    """Download any file from URL to local path"""
    return download_with_timeout(url, output_path, timeout=180, file_type=file_type)


def resolve_local_download_url(url):
    """If url points to our /download/ or /api/storage/ endpoint, resolve to local file path.
    Returns (True, local_abspath) if found, else (False, None). Use this to avoid HTTP
    download timeouts when video_loop or other endpoints use a previous output URL from the same server.
    """
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        path = (p.path or '').strip('/')
        if 'download/' in path or path.startswith('download'):
            filename = path.split('download/')[-1] if 'download/' in path else path.replace('download', '', 1).lstrip('/')
        elif 'api/storage/' in path or path.startswith('api/storage'):
            filename = path.split('api/storage/')[-1] if 'api/storage/' in path else path.replace('api/storage', '', 1).lstrip('/')
        else:
            return False, None
        filename = filename.split('?')[0]
        if not filename:
            return False, None
        if '/' in filename:
            parts = filename.split('/')
            secure_name = '/'.join(secure_filename(part) for part in parts)
        else:
            secure_name = secure_filename(filename)
        if not secure_name:
            return False, None
        for folder in (OUTPUT_FOLDER, UPLOAD_FOLDER):
            full = os.path.abspath(os.path.join(folder, secure_name))
            folder_abs = os.path.abspath(folder)
            if full.startswith(folder_abs) and os.path.isfile(full):
                return True, full
        return False, None
    except Exception as e:
        logging.debug(f"resolve_local_download_url failed for {url}: {e}")
        return False, None

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
                # When external audio is provided, completely ignore video audio and use external audio for full output
                filter_complex = f"{video_concat}concat=n={num_videos}:v=1:a=0[outv]"

                cmd = [
                    'ffmpeg'
                ] + inputs + [
                    '-i', audio_path,
                    '-filter_complex', filter_complex,
                    '-map', '[outv]',
                    '-map', f'{num_videos}:a:0',  # Use only external audio for full video
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-preset', 'veryfast',
                    '-crf', '23',
                    '-shortest',  # End when shortest stream ends so audio is in sync with video
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
                    '-b:a', '192k',
                    '-preset', 'veryfast',
                    '-crf', '23',
                    '-shortest',
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

def merge_main_and_outro_with_ffmpeg(main_video_path, outro_video_path, output_path, dimensions=None):
    """Append outro video to main video. Main part keeps its audio; outro part uses outro's own audio (no main audio during outro)."""
    normalized_outro_path = None
    try:
        # Resolve dimensions: use param or probe main video
        if dimensions:
            try:
                width, height = dimensions.split('x')
                target_width = int(width)
                target_height = int(height)
            except Exception:
                return False, "Invalid dimensions format. Use format like '1920x1080'"
        else:
            success, dims = get_video_dimensions(main_video_path)
            if not success:
                return False, f"Could not get main video dimensions: {dims}"
            target_width, target_height = dims

        # Normalize outro to same dimensions as main (scale + pad)
        normalized_outro_path = f"{outro_video_path}_normalized_outro.mp4"
        normalize_cmd = [
            'ffmpeg',
            '-i', outro_video_path,
            '-vf', f'scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-ar', '48000',
            '-ac', '2',
            '-preset', 'veryfast',
            '-crf', '23',
            '-y',
            normalized_outro_path
        ]
        logging.info(f"Normalizing outro to {target_width}x{target_height}: {' '.join(normalize_cmd)}")
        result = subprocess.run(normalize_cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            if normalized_outro_path and os.path.exists(normalized_outro_path):
                cleanup_file(normalized_outro_path)
            return False, f"Failed to normalize outro video: {result.stderr}"

        # Concat main (video+audio) then outro (video+audio) so main audio stops and outro audio plays during outro
        cmd = [
            'ffmpeg',
            '-i', main_video_path,
            '-i', normalized_outro_path,
            '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[outv];[0:a][1:a]concat=n=2:v=0:a=1[outa]',
            '-map', '[outv]',
            '-map', '[outa]',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-preset', 'veryfast',
            '-crf', '23',
            '-b:a', '192k',
            '-y',
            output_path
        ]
        logging.info(f"Running FFMPEG main+outro concat: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if normalized_outro_path:
            cleanup_file(normalized_outro_path)
        if result.returncode == 0:
            logging.info("Main + outro concat completed successfully")
            return True, "Main and outro merged successfully"
        else:
            logging.error(f"FFMPEG main+outro concat error: {result.stderr}")
            return False, f"Main+outro concat failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        if normalized_outro_path and os.path.exists(normalized_outro_path):
            cleanup_file(normalized_outro_path)
        return False, "Main+outro processing timed out"
    except Exception as e:
        if normalized_outro_path and os.path.exists(normalized_outro_path):
            cleanup_file(normalized_outro_path)
        logging.error(f"Merge main+outro error: {str(e)}")
        return False, f"Merge main+outro error: {str(e)}"

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
        # Use a minimum segment threshold (1 second) to avoid creating tiny unusable segments
        MIN_SEGMENT_DURATION = 1.0
        
        num_segments = math.ceil(total_duration / segment_duration)
        output_files = []
        
        # Check if the last segment would be too short and skip it
        last_segment_duration = total_duration - ((num_segments - 1) * segment_duration)
        if last_segment_duration < MIN_SEGMENT_DURATION and num_segments > 1:
            num_segments -= 1
            logging.info(f"Skipping last segment as it would be only {last_segment_duration:.2f}s (below {MIN_SEGMENT_DURATION}s threshold)")
        
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

def split_audio_by_time_with_ffmpeg(audio_path, output_dir, start_time_ms, end_time_ms):
    """Split audio by start and end time in milliseconds using FFMPEG"""
    try:
        start_time_sec = start_time_ms / 1000.0
        end_time_sec = end_time_ms / 1000.0
        duration_sec = end_time_sec - start_time_sec
        
        if duration_sec <= 0:
            return False, "End time must be greater than start time", None
        
        output_filename = f"audio_clip_{int(start_time_ms)}_{int(end_time_ms)}.mp3"
        output_path = os.path.join(output_dir, output_filename)
        
        cmd = [
            'ffmpeg',
            '-i', audio_path,
            '-ss', str(start_time_sec),
            '-t', str(duration_sec),
            '-c:a', 'mp3',
            '-b:a', '192k',
            '-y',
            output_path
        ]
        
        logging.info(f"Splitting audio by time: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            logging.info(f"Successfully created audio clip from {start_time_sec}s to {end_time_sec}s")
            return True, f"Audio successfully clipped from {start_time_ms}ms to {end_time_ms}ms", output_filename
        else:
            logging.error(f"FFMPEG error: {result.stderr}")
            return False, f"Failed to split audio: {result.stderr}", None
        
    except subprocess.TimeoutExpired:
        logging.error("Audio time splitting timed out")
        return False, "Audio time splitting processing timed out", None
    except Exception as e:
        logging.error(f"Audio time splitting error: {str(e)}")
        return False, f"Audio time splitting error: {str(e)}", None


# Routes
@app.route('/')
@app.route('/health')
def health_check():
    """Liveness for Railway / internal callers (no HTML)."""
    return jsonify({'service': 'ffmpegapi', 'status': 'ok'}), 200

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
        db.session.remove()
        
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
                    download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
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
        
        if not isinstance(video_urls, list) or len(video_urls) < 1:
            return jsonify({
                'success': False,
                'error': 'video_urls is required and must be a non-empty list'
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
            db.session.remove()
            
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
                        download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
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

@app.route('/api/neonvideo_merge_videos', methods=['POST'])
@log_api_request
@require_api_key
def neonvideo_merge_videos():
    """Neonvideo-only endpoint: merge videos with optional outro_url. Outro plays with its own sound (main audio stops during outro). Not public (no UI/docs)."""
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[NEONVIDEO_MERGE_VIDEOS] Request from API key: {api_key[:20] if api_key else '?'}...")
    try:
        data = request.get_json()
        async_processing = data.get('async', False) if data else False

        if async_processing:
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'neonvideo_merge_videos'
            job.status = 'pending'
            job.set_input_data(data)
            db.session.add(job)
            db.session.commit()
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

        if not data or 'video_urls' not in data:
            return jsonify({'success': False, 'error': 'video_urls is required'}), 400

        video_urls = data['video_urls']
        audio_url = data.get('audio_url')
        dimensions = data.get('dimensions')
        subtitle_url = data.get('subtitle_url')
        watermark_url = data.get('watermark_url')
        outro_url = data.get('outro_url')

        if not isinstance(video_urls, list) or len(video_urls) < 1:
            return jsonify({'success': False, 'error': 'video_urls is required and must be a non-empty list'}), 400

        request_id = str(uuid.uuid4())
        downloaded_videos = []
        audio_path = None
        subtitle_path = None
        watermark_path = None
        outro_path = None

        try:
            for i, url in enumerate(video_urls):
                video_filename = f"{request_id}_video_{i}.mp4"
                video_path = os.path.join(UPLOAD_FOLDER, video_filename)
                success, message = download_video_from_url(url, video_path)
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    return jsonify({'success': False, 'error': f'Failed to download video {i+1}: {message}'}), 400
                downloaded_videos.append(video_path)

            if audio_url:
                audio_filename = f"{request_id}_audio.mp3"
                audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
                success, message = download_video_from_url(audio_url, audio_path)
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    return jsonify({'success': False, 'error': f'Failed to download audio: {message}'}), 400

            if subtitle_url:
                subtitle_filename = f"{request_id}_subtitle.ass"
                subtitle_path = os.path.join(UPLOAD_FOLDER, subtitle_filename)
                success, message = download_file_from_url(subtitle_url, subtitle_path, "subtitle")
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    if audio_path:
                        cleanup_file(audio_path)
                    return jsonify({'success': False, 'error': f'Failed to download subtitle: {message}'}), 400

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
                    return jsonify({'success': False, 'error': f'Failed to download watermark: {message}'}), 400

            if outro_url:
                outro_filename = f"{request_id}_outro.mp4"
                outro_path = os.path.join(UPLOAD_FOLDER, outro_filename)
                success, message = download_video_from_url(outro_url, outro_path)
                if not success:
                    for path in downloaded_videos:
                        cleanup_file(path)
                    if audio_path:
                        cleanup_file(audio_path)
                    if subtitle_path:
                        cleanup_file(subtitle_path)
                    if watermark_path:
                        cleanup_file(watermark_path)
                    return jsonify({'success': False, 'error': f'Failed to download outro video: {message}'}), 400

            use_outro = bool(outro_url)
            output_filename = f"{request_id}_merged_videos.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)
            if use_outro:
                main_part_filename = f"{request_id}_main_part.mp4"
                main_part_path = os.path.join(OUTPUT_FOLDER, main_part_filename)
                current_path = main_part_path
            else:
                current_path = output_path

            db.session.remove()

            success, message = merge_videos_with_ffmpeg(downloaded_videos, current_path, audio_path, dimensions)
            for path in downloaded_videos:
                cleanup_file(path)
            if audio_path:
                cleanup_file(audio_path)

            if not success:
                if outro_path:
                    cleanup_file(outro_path)
                if subtitle_path:
                    cleanup_file(subtitle_path)
                if watermark_path:
                    cleanup_file(watermark_path)
                return jsonify({'success': False, 'error': message}), 500

            subtitles_added = False
            if subtitle_path:
                subtitled_filename = f"{request_id}_merged_subtitled_videos.mp4"
                subtitled_output_path = os.path.join(OUTPUT_FOLDER, subtitled_filename)
                subtitle_success, subtitle_message = add_subtitles_with_ffmpeg(current_path, subtitle_path, subtitled_output_path)
                cleanup_file(subtitle_path)
                if subtitle_success:
                    cleanup_file(current_path)
                    current_path = subtitled_output_path
                    output_filename = subtitled_filename
                    message = "Videos merged and subtitles added successfully"
                    subtitles_added = True
                else:
                    cleanup_file(subtitled_output_path)
                    if outro_path:
                        cleanup_file(outro_path)
                    if watermark_path:
                        cleanup_file(watermark_path)
                    return jsonify({'success': False, 'error': f'Subtitle addition failed: {subtitle_message}'}), 500
            else:
                if subtitle_path:
                    cleanup_file(subtitle_path)

            if watermark_path:
                watermarked_filename = f"{request_id}_merged_watermarked_videos.mp4"
                watermarked_output_path = os.path.join(OUTPUT_FOLDER, watermarked_filename)
                watermark_success, watermark_message = add_watermark_with_ffmpeg(current_path, watermark_path, watermarked_output_path)
                cleanup_file(watermark_path)
                if watermark_success:
                    cleanup_file(current_path)
                    current_path = watermarked_output_path
                    output_filename = watermarked_filename
                    message = "Videos merged and watermark added successfully" if not subtitles_added else "Videos merged, subtitles and watermark added successfully"
                else:
                    cleanup_file(watermarked_output_path)
                    if outro_path:
                        cleanup_file(outro_path)
                    return jsonify({'success': False, 'error': f'Watermark addition failed: {watermark_message}'}), 500
            else:
                if watermark_path:
                    cleanup_file(watermark_path)

            if use_outro:
                success, message = merge_main_and_outro_with_ffmpeg(current_path, outro_path, output_path, dimensions)
                cleanup_file(outro_path)
                cleanup_file(current_path)
                if not success:
                    return jsonify({'success': False, 'error': message}), 500
                output_filename = f"{request_id}_merged_videos.mp4"
                current_path = output_path

            storage_url = upload_to_storage(current_path, output_filename)
            if storage_url:
                cleanup_file(current_path)
                return jsonify({
                    'success': True,
                    'message': message,
                    'download_url': storage_url,
                    'filename': output_filename
                })
            if os.environ.get('REPLIT_DEPLOYMENT'):
                download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
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
        except Exception as e:
            for path in downloaded_videos:
                cleanup_file(path)
            if audio_path:
                cleanup_file(audio_path)
            if subtitle_path:
                cleanup_file(subtitle_path)
            if watermark_path:
                cleanup_file(watermark_path)
            if outro_path:
                cleanup_file(outro_path)
            raise e
    except Exception as e:
        logging.error(f"[NEONVIDEO_MERGE_VIDEOS] Error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500

@app.route('/api/add_watermark', methods=['POST'])
@log_api_request
@require_api_key
def add_watermark():
    """API endpoint to add a watermark image to a video (sync/async)"""
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[ADD_WATERMARK] Request received from API key: {api_key[:20]}...")

    try:
        data = request.get_json()
        async_processing = data.get('async', False) if data else False

        if async_processing:
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()

            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'add_watermark'
            job.status = 'pending'
            job.set_input_data(data)

            db.session.add(job)
            db.session.commit()

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

        if not data or 'video_url' not in data or 'watermark_url' not in data:
            return jsonify({
                'success': False,
                'error': 'video_url and watermark_url are required'
            }), 400

        video_url = data['video_url']
        watermark_url = data['watermark_url']
        position = data.get('position', 'bottom-right')
        scale = data.get('scale', 0.25)

        valid_positions = [
            'top-left', 'top-center', 'top-right',
            'middle-left', 'middle', 'middle-right',
            'bottom-left', 'bottom-center', 'bottom-right'
        ]
        if position not in valid_positions:
            return jsonify({
                'success': False,
                'error': f'Invalid position. Must be one of: {", ".join(valid_positions)}'
            }), 400

        try:
            scale = float(scale)
            if scale < 0.05 or scale > 1.0:
                raise ValueError()
        except (ValueError, TypeError):
            return jsonify({
                'success': False,
                'error': 'scale must be a number between 0.05 and 1.0'
            }), 400

        request_id = str(uuid.uuid4())
        video_path = None
        watermark_path = None

        try:
            video_filename = f"{request_id}_video.mp4"
            video_path = os.path.join(UPLOAD_FOLDER, video_filename)
            success, message = download_video_from_url(video_url, video_path)
            if not success:
                return jsonify({
                    'success': False,
                    'error': f'Failed to download video: {message}'
                }), 400

            watermark_filename = f"{request_id}_watermark.png"
            watermark_path = os.path.join(UPLOAD_FOLDER, watermark_filename)
            success, message = download_file_from_url(watermark_url, watermark_path, "watermark image")
            if not success:
                cleanup_file(video_path)
                return jsonify({
                    'success': False,
                    'error': f'Failed to download watermark image: {message}'
                }), 400

            output_filename = f"{request_id}_watermarked.mp4"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)

            db.session.remove()

            success, message = add_watermark_with_ffmpeg(video_path, watermark_path, output_path, position, scale)

            cleanup_file(video_path)
            cleanup_file(watermark_path)

            if success:
                storage_url = upload_to_storage(output_path, output_filename)

                if storage_url:
                    cleanup_file(output_path)
                    return jsonify({
                        'success': True,
                        'message': 'Watermark added successfully',
                        'download_url': storage_url,
                        'filename': output_filename
                    })
                else:
                    logging.warning("Storage upload failed, falling back to local download")
                    if os.environ.get('REPLIT_DEPLOYMENT'):
                        download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
                    elif os.environ.get('REPLIT_DEV_DOMAIN'):
                        download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                    else:
                        download_url = url_for('download_file', filename=output_filename, _external=True)
                    return jsonify({
                        'success': True,
                        'message': 'Watermark added successfully (Note: Using temporary local storage - download soon)',
                        'download_url': download_url,
                        'filename': output_filename
                    })
            else:
                return jsonify({
                    'success': False,
                    'error': message
                }), 500

        except Exception as e:
            if video_path:
                cleanup_file(video_path)
            if watermark_path:
                cleanup_file(watermark_path)
            raise e

    except Exception as e:
        logging.error(f"[ADD_WATERMARK] Error: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@app.route('/api/storage/<path:filename>')
def serve_from_storage(filename):
    """Serve a file from storage. On Railway serves from volume (OUTPUT_FOLDER/UPLOAD_FOLDER)."""
    try:
        # Railway: serve from volume (OUTPUT_FOLDER, then UPLOAD_FOLDER)
        if os.environ.get('RAILWAY_ENVIRONMENT'):
            secure_path = secure_filename(filename)
            if '/' in filename:
                path_parts = filename.split('/')
                secure_path = '/'.join(secure_filename(p) for p in path_parts)
            for folder in (OUTPUT_FOLDER, UPLOAD_FOLDER):
                full_path = os.path.abspath(os.path.join(folder, secure_path))
                folder_abs = os.path.abspath(folder)
                if full_path.startswith(folder_abs) and os.path.exists(full_path):
                    return send_from_directory(
                        os.path.dirname(full_path),
                        os.path.basename(full_path),
                        mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                        as_attachment=True,
                        download_name=os.path.basename(filename)
                    )
            return jsonify({'success': False, 'error': 'File not found'}), 404

        from replit.object_storage import Client
        client = Client()
        file_data = client.download_as_bytes(filename)
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        return Response(
            file_data,
            mimetype=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="{os.path.basename(filename)}"',
                'Cache-Control': 'public, max-age=3600'
            }
        )
    except Exception as e:
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
            elif job.job_type == 'split_audio':
                result = process_split_audio_job(job, input_data)
            elif job.job_type == 'split_audio_segments':
                result = process_split_audio_segments_job(job, input_data)
            elif job.job_type == 'split_audio_time':
                result = process_split_audio_time_job(job, input_data)
            elif job.job_type == 'neonvideo_merge_videos':
                result = process_neonvideo_merge_videos_job(job, input_data)
            elif job.job_type == 'add_watermark':
                result = process_add_watermark_job(job, input_data)
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
                download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
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
                    download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
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

def process_neonvideo_merge_videos_job(job, input_data):
    """Process neonvideo_merge_videos job (merge with optional outro; outro uses its own audio)."""
    try:
        request_id = str(uuid.uuid4())
        video_urls = input_data['video_urls']
        audio_url = input_data.get('audio_url')
        dimensions = input_data.get('dimensions')
        subtitle_url = input_data.get('subtitle_url')
        watermark_url = input_data.get('watermark_url')
        outro_url = input_data.get('outro_url')

        downloaded_videos = []
        audio_path = None
        subtitle_path = None
        watermark_path = None
        outro_path = None

        for i, url in enumerate(video_urls):
            video_filename = f"{request_id}_video_{i}.mp4"
            video_path = os.path.join(UPLOAD_FOLDER, video_filename)
            success, message = download_video_from_url(url, video_path)
            if not success:
                for path in downloaded_videos:
                    cleanup_file(path)
                return {'success': False, 'error': f'Failed to download video {i+1}: {message}'}
            downloaded_videos.append(video_path)

        if audio_url:
            audio_filename = f"{request_id}_audio.mp3"
            audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
            success, message = download_video_from_url(audio_url, audio_path)
            if not success:
                for path in downloaded_videos:
                    cleanup_file(path)
                return {'success': False, 'error': f'Failed to download audio: {message}'}

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

        if outro_url:
            outro_filename = f"{request_id}_outro.mp4"
            outro_path = os.path.join(UPLOAD_FOLDER, outro_filename)
            success, message = download_video_from_url(outro_url, outro_path)
            if not success:
                for path in downloaded_videos:
                    cleanup_file(path)
                if audio_path:
                    cleanup_file(audio_path)
                if subtitle_path:
                    cleanup_file(subtitle_path)
                if watermark_path:
                    cleanup_file(watermark_path)
                return {'success': False, 'error': f'Failed to download outro video: {message}'}

        use_outro = bool(outro_url)
        output_filename = f"{request_id}_merged_videos.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        if use_outro:
            main_part_path = os.path.join(OUTPUT_FOLDER, f"{request_id}_main_part.mp4")
            current_path = main_part_path
        else:
            current_path = output_path

        success, message = merge_videos_with_ffmpeg(downloaded_videos, current_path, audio_path, dimensions)
        for path in downloaded_videos:
            cleanup_file(path)
        if audio_path:
            cleanup_file(audio_path)
        if not success:
            if outro_path:
                cleanup_file(outro_path)
            if subtitle_path:
                cleanup_file(subtitle_path)
            if watermark_path:
                cleanup_file(watermark_path)
            return {'success': False, 'error': message}

        subtitles_added = False
        if subtitle_path:
            subtitled_output_path = os.path.join(OUTPUT_FOLDER, f"{request_id}_merged_subtitled_videos.mp4")
            subtitle_success, subtitle_message = add_subtitles_with_ffmpeg(current_path, subtitle_path, subtitled_output_path)
            cleanup_file(subtitle_path)
            if subtitle_success:
                cleanup_file(current_path)
                current_path = subtitled_output_path
                output_filename = f"{request_id}_merged_subtitled_videos.mp4"
                message = "Videos merged and subtitles added successfully"
                subtitles_added = True
            else:
                cleanup_file(subtitled_output_path)
                if outro_path:
                    cleanup_file(outro_path)
                if watermark_path:
                    cleanup_file(watermark_path)
                return {'success': False, 'error': f'Subtitle addition failed: {subtitle_message}'}
        elif subtitle_path:
            cleanup_file(subtitle_path)

        if watermark_path:
            watermarked_output_path = os.path.join(OUTPUT_FOLDER, f"{request_id}_merged_watermarked_videos.mp4")
            watermark_success, watermark_message = add_watermark_with_ffmpeg(current_path, watermark_path, watermarked_output_path)
            cleanup_file(watermark_path)
            if watermark_success:
                cleanup_file(current_path)
                current_path = watermarked_output_path
                output_filename = f"{request_id}_merged_watermarked_videos.mp4"
                message = "Videos merged and watermark added successfully" if not subtitles_added else "Videos merged, subtitles and watermark added successfully"
            else:
                cleanup_file(watermarked_output_path)
                if outro_path:
                    cleanup_file(outro_path)
                return {'success': False, 'error': f'Watermark addition failed: {watermark_message}'}
        elif watermark_path:
            cleanup_file(watermark_path)

        if use_outro:
            success, message = merge_main_and_outro_with_ffmpeg(current_path, outro_path, output_path, dimensions)
            cleanup_file(outro_path)
            cleanup_file(current_path)
            if not success:
                return {'success': False, 'error': message}
            output_filename = f"{request_id}_merged_videos.mp4"
            current_path = output_path

        storage_url = upload_to_storage(current_path, output_filename)
        if storage_url:
            cleanup_file(current_path)
            return {'success': True, 'message': message, 'download_url': storage_url, 'filename': output_filename}
        if os.environ.get('REPLIT_DEPLOYMENT'):
            download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
        elif os.environ.get('REPLIT_DEV_DOMAIN'):
            download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
        else:
            download_url = url_for('download_file', filename=output_filename, _external=True)
        return {'success': True, 'message': message, 'download_url': download_url, 'filename': output_filename}
    except Exception as e:
        logging.error(f"[ASYNC_NEONVIDEO_MERGE_VIDEOS] Error in process_neonvideo_merge_videos_job: {str(e)}", exc_info=True)
        return {'success': False, 'error': f'Server error: {str(e)}'}

def process_add_watermark_job(job, input_data):
    """Process add_watermark job"""
    try:
        request_id = str(uuid.uuid4())
        video_url = input_data['video_url']
        watermark_url = input_data['watermark_url']
        position = input_data.get('position', 'bottom-right')
        scale = float(input_data.get('scale', 0.25))

        video_filename = f"{request_id}_video.mp4"
        video_path = os.path.join(UPLOAD_FOLDER, video_filename)

        success, message = download_video_from_url(video_url, video_path)
        if not success:
            return {'success': False, 'error': f'Failed to download video: {message}'}

        watermark_filename = f"{request_id}_watermark.png"
        watermark_path = os.path.join(UPLOAD_FOLDER, watermark_filename)

        success, message = download_file_from_url(watermark_url, watermark_path, "watermark image")
        if not success:
            cleanup_file(video_path)
            return {'success': False, 'error': f'Failed to download watermark image: {message}'}

        output_filename = f"{request_id}_watermarked.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)

        db.session.remove()

        success, message = add_watermark_with_ffmpeg(video_path, watermark_path, output_path, position, scale)

        cleanup_file(video_path)
        cleanup_file(watermark_path)

        if success:
            storage_url = upload_to_storage(output_path, output_filename)

            if storage_url:
                cleanup_file(output_path)
                return {
                    'success': True,
                    'message': 'Watermark added successfully',
                    'download_url': storage_url,
                    'filename': output_filename
                }
            else:
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    download_url = f"https://www.ffmpegapi.net/download/{output_filename}"
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                else:
                    download_url = url_for('download_file', filename=output_filename, _external=True)
                return {
                    'success': True,
                    'message': 'Watermark added successfully',
                    'download_url': download_url,
                    'filename': output_filename
                }
        else:
            return {'success': False, 'error': message}

    except Exception as e:
        logging.error(f"Error in process_add_watermark_job: {str(e)}")
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
                        download_url = f"https://www.ffmpegapi.net/download/{request_id}/{filename}"
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

def process_split_audio_time_job(job, input_data):
    """Process split_audio_time job - splits audio by start and end time in milliseconds"""
    audio_path = ""
    try:
        request_id = str(uuid.uuid4())
        
        if 'audio_url' not in input_data:
            return {'success': False, 'error': 'audio_url is required'}
        
        audio_url = input_data['audio_url']
        start_time_ms = input_data.get('start_time')
        end_time_ms = input_data.get('end_time')
        
        if start_time_ms is None or end_time_ms is None:
            return {'success': False, 'error': 'start_time and end_time are required (in milliseconds)'}
        
        if not isinstance(start_time_ms, (int, float)) or not isinstance(end_time_ms, (int, float)):
            return {'success': False, 'error': 'start_time and end_time must be numbers (in milliseconds)'}
        
        if start_time_ms < 0 or end_time_ms < 0:
            return {'success': False, 'error': 'start_time and end_time must be non-negative'}
        
        if end_time_ms <= start_time_ms:
            return {'success': False, 'error': 'end_time must be greater than start_time'}
        
        audio_ext = audio_url.split('.')[-1].lower() if '.' in audio_url else 'mp3'
        audio_filename = f"{request_id}_audio.{audio_ext}"
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        output_dir = os.path.join(OUTPUT_FOLDER, request_id)
        
        os.makedirs(output_dir, exist_ok=True)
        
        success, message = download_file_from_url(audio_url, audio_path, "audio")
        if not success:
            cleanup_file(audio_path)
            return {'success': False, 'error': message}
        
        success, message, output_filename = split_audio_by_time_with_ffmpeg(audio_path, output_dir, start_time_ms, end_time_ms)
        
        cleanup_file(audio_path)
        
        if success:
            if os.environ.get('REPLIT_DEPLOYMENT'):
                download_url = f"https://www.ffmpegapi.net/download/{request_id}/{output_filename}"
            elif os.environ.get('REPLIT_DEV_DOMAIN'):
                download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{request_id}/{output_filename}"
            else:
                download_url = url_for('download_file', filename=f"{request_id}/{output_filename}", _external=True)
            
            return {
                'success': True,
                'message': message,
                'start_time_ms': start_time_ms,
                'end_time_ms': end_time_ms,
                'duration_ms': end_time_ms - start_time_ms,
                'download_url': download_url
            }
        else:
            import shutil
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir)
            return {'success': False, 'error': message}
            
    except Exception as e:
        logging.error(f"Error in process_split_audio_time_job: {str(e)}")
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
                        download_url = f"https://www.ffmpegapi.net/download/{request_id}/{filename}"
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
                # Explicit top-level download URL so clients can show the final video
                if result_data.get('download_url'):
                    response_data['download_url'] = result_data['download_url']
                    response_data['result_url'] = result_data['download_url']  # alias for compatibility
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
                        download_url = f"https://www.ffmpegapi.net/download/{request_id}/{filename}"
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
                        download_url = f"https://www.ffmpegapi.net/download/{request_id}/{filename}"
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

@app.route('/api/split_audio_time', methods=['POST'])
@log_api_request
@require_api_key
def split_audio_time():
    """API endpoint to split audio by start and end time in milliseconds (sync/async)"""
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[SPLIT_AUDIO_TIME] Request received from API key: {api_key[:20]}...")
    logging.info(f"[SPLIT_AUDIO_TIME] Headers: {dict(request.headers)}")
    if request.is_json:
        logging.info(f"[SPLIT_AUDIO_TIME] JSON data: {request.get_json()}")
    logging.info(f"[SPLIT_AUDIO_TIME] Form data: {dict(request.form)}")
    
    try:
        data = request.get_json()
        async_processing = data.get('async', False) if data else False
        
        if async_processing:
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            key_record = ApiKey.query.filter_by(key=api_key, is_active=True).first()
            
            job = Job()
            job.user_id = key_record.user_id
            job.job_type = 'split_audio_time'
            job.status = 'pending'
            job.set_input_data(data)
            
            db.session.add(job)
            db.session.commit()
            
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
        
        if not data:
            data = request.get_json()
        
        if not data or 'audio_url' not in data:
            return jsonify({
                'success': False,
                'error': 'audio_url is required'
            }), 400
        
        audio_url = data['audio_url']
        start_time_ms = data.get('start_time')
        end_time_ms = data.get('end_time')
        
        if start_time_ms is None or end_time_ms is None:
            return jsonify({
                'success': False,
                'error': 'start_time and end_time are required (in milliseconds)'
            }), 400
        
        if not isinstance(start_time_ms, (int, float)) or not isinstance(end_time_ms, (int, float)):
            return jsonify({
                'success': False,
                'error': 'start_time and end_time must be numbers (in milliseconds)'
            }), 400
        
        if start_time_ms < 0 or end_time_ms < 0:
            return jsonify({
                'success': False,
                'error': 'start_time and end_time must be non-negative'
            }), 400
        
        if end_time_ms <= start_time_ms:
            return jsonify({
                'success': False,
                'error': 'end_time must be greater than start_time'
            }), 400
        
        request_id = str(uuid.uuid4())
        audio_filename = f"{request_id}_audio.mp3"
        audio_path = os.path.join(UPLOAD_FOLDER, audio_filename)
        output_dir = os.path.join(OUTPUT_FOLDER, request_id)
        
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            success, message = download_file_from_url(audio_url, audio_path, "audio")
            if not success:
                cleanup_file(audio_path)
                return jsonify({
                    'success': False,
                    'error': message
                }), 400
            
            success, message, output_filename = split_audio_by_time_with_ffmpeg(audio_path, output_dir, start_time_ms, end_time_ms)
            
            cleanup_file(audio_path)
            
            if success:
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    download_url = f"https://www.ffmpegapi.net/download/{request_id}/{output_filename}"
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{request_id}/{output_filename}"
                else:
                    download_url = url_for('download_file', filename=f"{request_id}/{output_filename}", _external=True)
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'start_time_ms': start_time_ms,
                    'end_time_ms': end_time_ms,
                    'duration_ms': end_time_ms - start_time_ms,
                    'download_url': download_url
                })
            else:
                import shutil
                if os.path.exists(output_dir):
                    shutil.rmtree(output_dir)
                
                return jsonify({
                    'success': False,
                    'error': message
                }), 500
                
        except Exception as e:
            cleanup_file(audio_path)
            import shutil
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir)
            
            logging.error(f"Audio time splitting error: {str(e)}")
            return jsonify({
                'success': False,
                'error': f'Server error: {str(e)}'
            }), 500
            
    except Exception as e:
        logging.error(f"Split audio time API error: {str(e)}")
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

def add_watermark_with_ffmpeg(video_path, watermark_path, output_path, position='bottom-right', scale=0.25):
    """Add watermark to video using FFMPEG with dynamic scaling and configurable position.
    
    Args:
        position: One of top-left, top-center, top-right, middle-left, middle,
                  middle-right, bottom-left, bottom-center, bottom-right
        scale: Watermark width as a fraction of the video width (0.05 to 1.0)
    """
    try:
        padding = 20
        dims_success, dims_data = get_video_dimensions(video_path)
        if not dims_success:
            return False, f"Could not determine video dimensions: {dims_data}"

        video_width, _video_height = dims_data
        target_watermark_width = max(1, int(round(video_width * float(scale))))

        position_map = {
            'top-left':      f'{padding}:{padding}',
            'top-center':    f'(main_w-overlay_w)/2:{padding}',
            'top-right':     f'main_w-overlay_w-{padding}:{padding}',
            'middle-left':   f'{padding}:(main_h-overlay_h)/2',
            'middle':        f'(main_w-overlay_w)/2:(main_h-overlay_h)/2',
            'middle-right':  f'main_w-overlay_w-{padding}:(main_h-overlay_h)/2',
            'bottom-left':   f'{padding}:main_h-overlay_h-{padding}',
            'bottom-center': f'(main_w-overlay_w)/2:main_h-overlay_h-{padding}',
            'bottom-right':  f'main_w-overlay_w-{padding}:main_h-overlay_h-{padding}',
        }

        overlay_pos = position_map.get(position, position_map['bottom-right'])

        # Normalize SAR to square pixels first, then scale with -1 to preserve logo proportions.
        watermark_filter = (
            f"[0:v]setsar=1[video_square];"
            f"[1:v]format=rgba,setsar=1[watermark_src];"
            f"[watermark_src]scale={target_watermark_width}:-1:flags=lanczos[watermark_scaled];"
            f"[video_square][watermark_scaled]overlay={overlay_pos}:format=auto[outv]"
        )

        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', watermark_path,
            '-filter_complex', watermark_filter,
            '-map', '[outv]',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-c:a', 'copy',
            '-pix_fmt', 'yuv420p',
            '-y',
            output_path
        ]
        
        logging.info(f"Running FFMPEG watermark command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        
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


def get_video_duration(video_path):
    """Get video duration in seconds using ffprobe"""
    try:
        probe_cmd = [
            'ffprobe',
            '-v', 'quiet',
            '-show_entries', 'format=duration',
            '-of', 'csv=p=0',
            video_path
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logging.error(f"[GET_VIDEO_DURATION] FFprobe error: {result.stderr}")
            return None, f"Unable to get video duration: {result.stderr}"
        try:
            duration = float(result.stdout.strip())
        except ValueError:
            logging.error(f"[GET_VIDEO_DURATION] Invalid duration value: {result.stdout.strip()}")
            return None, "Invalid video file or unable to determine duration"
        if duration <= 0:
            return None, "Video file appears to have zero duration"
        return duration, None
    except subprocess.TimeoutExpired:
        return None, "Video duration probe timed out"
    except Exception as e:
        logging.error(f"[GET_VIDEO_DURATION] Error: {str(e)}")
        return None, str(e)


def extract_frame_at_time(video_path, output_image_path, time_seconds):
    """Extract a single frame at the given time using FFmpeg. Returns (success, error_message)."""
    try:
        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-ss', str(time_seconds),
            '-vframes', '1',
            '-q:v', '2',
            output_image_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            err = result.stderr or result.stdout or "Unknown error"
            logging.error(f"[EXTRACT_FRAME] FFmpeg error: {err}")
            return False, f"Frame extraction failed: {err}"
        if not os.path.exists(output_image_path) or os.path.getsize(output_image_path) == 0:
            return False, "Frame extraction produced no output"
        return True, None
    except subprocess.TimeoutExpired:
        return False, "Frame extraction timed out"
    except Exception as e:
        logging.error(f"[EXTRACT_FRAME] Error: {str(e)}")
        return False, str(e)


@app.route('/api/get_first_frame_image', methods=['POST'])
@log_api_request
@require_api_key
def get_first_frame_image():
    """API endpoint to extract the first frame of a video and return the image URL."""
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[GET_FIRST_FRAME] Request from API key: {api_key[:20] if api_key else 'None'}...")

    try:
        request_id = str(uuid.uuid4())

        if request.is_json:
            data = request.get_json()
            video_url = data.get('video_url')
        else:
            video_url = request.form.get('video_url')

        if not video_url or not str(video_url).strip():
            return jsonify({'success': False, 'error': 'video_url is required'}), 400

        video_url = str(video_url).strip()
        video_ext = video_url.split('.')[-1].split('?')[0].lower() if '.' in video_url else 'mp4'
        if video_ext not in ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv']:
            video_ext = 'mp4'
        video_filename = f"{request_id}_video.{video_ext}"
        video_path = os.path.join(UPLOAD_FOLDER, video_filename)

        success, message = download_file_from_url(video_url, video_path, "video")
        if not success:
            return jsonify({'success': False, 'error': message}), 400

        output_path = None
        try:
            output_filename = f"{request_id}_frame.jpg"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)

            success, err = extract_frame_at_time(video_path, output_path, 0.0)
            cleanup_file(video_path)

            if not success:
                if os.path.exists(output_path):
                    cleanup_file(output_path)
                return jsonify({'success': False, 'error': err or 'Frame extraction failed'}), 500

            storage_url = upload_to_storage(output_path, output_filename)
            if storage_url:
                cleanup_file(output_path)
                image_url = storage_url
            else:
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    image_url = f"https://www.ffmpegapi.net/download/{output_filename}"
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    image_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                else:
                    image_url = url_for('download_file', filename=output_filename, _external=True)

            return jsonify({
                'success': True,
                'message': 'First frame extracted successfully',
                'image_url': image_url,
                'download_url': image_url,
                'filename': output_filename
            })
        except Exception as e:
            cleanup_file(video_path)
            if output_path and os.path.exists(output_path):
                cleanup_file(output_path)
            raise e

    except Exception as e:
        logging.error(f"[GET_FIRST_FRAME] Error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500


@app.route('/api/get_last_frame_image', methods=['POST'])
@log_api_request
@require_api_key
def get_last_frame_image():
    """API endpoint to extract the last frame of a video and return the image URL."""
    api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
    logging.info(f"[GET_LAST_FRAME] Request from API key: {api_key[:20] if api_key else 'None'}...")

    try:
        request_id = str(uuid.uuid4())

        if request.is_json:
            data = request.get_json()
            video_url = data.get('video_url')
        else:
            video_url = request.form.get('video_url')

        if not video_url or not str(video_url).strip():
            return jsonify({'success': False, 'error': 'video_url is required'}), 400

        video_url = str(video_url).strip()
        video_ext = video_url.split('.')[-1].split('?')[0].lower() if '.' in video_url else 'mp4'
        if video_ext not in ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', 'wmv']:
            video_ext = 'mp4'
        video_filename = f"{request_id}_video.{video_ext}"
        video_path = os.path.join(UPLOAD_FOLDER, video_filename)

        success, message = download_file_from_url(video_url, video_path, "video")
        if not success:
            return jsonify({'success': False, 'error': message}), 400

        output_path = None
        try:
            duration, err = get_video_duration(video_path)
            if err is not None:
                cleanup_file(video_path)
                return jsonify({'success': False, 'error': err}), 400

            seek_time = max(0.0, duration - 0.1)

            output_filename = f"{request_id}_frame.jpg"
            output_path = os.path.join(OUTPUT_FOLDER, output_filename)

            success, extract_err = extract_frame_at_time(video_path, output_path, seek_time)
            cleanup_file(video_path)

            if not success:
                if output_path and os.path.exists(output_path):
                    cleanup_file(output_path)
                return jsonify({'success': False, 'error': extract_err or 'Frame extraction failed'}), 500

            storage_url = upload_to_storage(output_path, output_filename)
            if storage_url:
                cleanup_file(output_path)
                image_url = storage_url
            else:
                if os.environ.get('REPLIT_DEPLOYMENT'):
                    image_url = f"https://www.ffmpegapi.net/download/{output_filename}"
                elif os.environ.get('REPLIT_DEV_DOMAIN'):
                    image_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/download/{output_filename}"
                else:
                    image_url = url_for('download_file', filename=output_filename, _external=True)

            return jsonify({
                'success': True,
                'message': 'Last frame extracted successfully',
                'image_url': image_url,
                'download_url': image_url,
                'filename': output_filename
            })
        except Exception as e:
            cleanup_file(video_path)
            if output_path and os.path.exists(output_path):
                cleanup_file(output_path)
            raise e

    except Exception as e:
        logging.error(f"[GET_LAST_FRAME] Error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500


def extract_audio_from_video(video_path, audio_path):
    """Extract audio from video as mono 16kHz MP3 (compact for Whisper API upload)."""
    try:
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vn', '-ac', '1', '-ar', '16000', '-b:a', '64k',
            '-y', audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return False, f"Audio extraction failed: {result.stderr[:500]}"
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            return False, "Audio extraction produced empty file"
        return True, "Audio extracted successfully"
    except subprocess.TimeoutExpired:
        return False, "Audio extraction timed out"
    except Exception as e:
        return False, f"Audio extraction error: {str(e)}"


def transcribe_audio_with_whisper(audio_path, language="auto"):
    """Call OpenAI Whisper API to get word-level timestamps."""
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None, "OPENAI_API_KEY environment variable is not set"

    file_size = os.path.getsize(audio_path)
    if file_size > 25 * 1024 * 1024:
        return None, f"Audio file too large for Whisper API ({file_size // (1024*1024)}MB, max 25MB)"

    client = OpenAI(api_key=api_key)

    kwargs = {
        "model": "whisper-1",
        "file": open(audio_path, "rb"),
        "response_format": "verbose_json",
        "timestamp_granularities": ["word"],
    }
    if language and language != "auto":
        kwargs["language"] = language

    try:
        transcript = client.audio.transcriptions.create(**kwargs)
    except Exception as e:
        return None, f"Whisper API error: {str(e)}"
    finally:
        kwargs["file"].close()

    words = []
    if hasattr(transcript, 'words') and transcript.words:
        for w in transcript.words:
            words.append({
                "word": w.word.strip(),
                "start": float(w.start),
                "end": float(w.end),
            })

    if not words:
        return None, "Whisper returned no word-level timestamps"

    return words, None


def generate_srt_from_words(words, max_chars=40, max_words_per_segment=8):
    """Group word timestamps into SRT subtitle segments."""
    segments = []
    current_segment_words = []
    current_chars = 0

    for w in words:
        word_text = w["word"]
        if current_segment_words and (
            current_chars + len(word_text) + 1 > max_chars
            or len(current_segment_words) >= max_words_per_segment
        ):
            segments.append(current_segment_words)
            current_segment_words = []
            current_chars = 0
        current_segment_words.append(w)
        current_chars += len(word_text) + (1 if current_chars > 0 else 0)

    if current_segment_words:
        segments.append(current_segment_words)

    def fmt_ts(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, seg in enumerate(segments, 1):
        start = seg[0]["start"]
        end = seg[-1]["end"]
        text = " ".join(w["word"] for w in seg)
        lines.append(f"{i}")
        lines.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


def generate_vtt_from_words(words, max_chars=40, max_words_per_segment=8):
    """Group word timestamps into WebVTT subtitle segments."""
    segments = []
    current_segment_words = []
    current_chars = 0

    for w in words:
        word_text = w["word"]
        if current_segment_words and (
            current_chars + len(word_text) + 1 > max_chars
            or len(current_segment_words) >= max_words_per_segment
        ):
            segments.append(current_segment_words)
            current_segment_words = []
            current_chars = 0
        current_segment_words.append(w)
        current_chars += len(word_text) + (1 if current_chars > 0 else 0)

    if current_segment_words:
        segments.append(current_segment_words)

    def fmt_ts(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    lines = ["WEBVTT", ""]
    for seg in segments:
        start = seg[0]["start"]
        end = seg[-1]["end"]
        text = " ".join(w["word"] for w in seg)
        lines.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


@app.route('/api/videos/add-tiktok-captions', methods=['POST'])
@log_api_request
@require_api_key
def add_tiktok_captions():
    """Auto-caption endpoint: extract audio, transcribe with Whisper, render TikTok-style captions."""
    request_id = str(uuid.uuid4())
    temp_files = []

    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Request body must be JSON'}), 400

        video_url = data.get('video_url')
        if not video_url:
            return jsonify({'success': False, 'error': 'video_url is required'}), 400

        subtitle_style = data.get('subtitle_style', 'plain-white')
        language = data.get('language', 'auto')
        aspect_ratio = data.get('aspect_ratio', '9:16')
        max_chars_per_line = data.get('max_chars_per_line', 20)
        max_lines = data.get('max_lines', 1)
        position = data.get('position', 'bottom')

        valid_styles = ['plain-white', 'yellow-bg', 'pink-bg', 'blue-bg', 'red-bg']
        if subtitle_style not in valid_styles:
            subtitle_style = 'plain-white'
        if aspect_ratio not in ('16:9', '9:16', '4:3', '3:4'):
            aspect_ratio = '9:16'
        if position not in ('top', 'center', 'bottom'):
            position = 'bottom'
        max_chars_per_line = max(5, min(80, int(max_chars_per_line or 20)))
        max_lines = max(1, min(4, int(max_lines or 1)))

        # Step 1: Download video
        video_path = os.path.join(UPLOAD_FOLDER, f"{request_id}_autocaption_video.mp4")
        temp_files.append(video_path)

        logging.info(f"[AUTO_CAPTION] Downloading video: {video_url[:80]}...")
        success, message = download_file_from_url(video_url, video_path, "video")
        if not success:
            return jsonify({'success': False, 'error': f'Failed to download video: {message}'}), 400

        # Step 2: Extract audio
        audio_path = os.path.join(UPLOAD_FOLDER, f"{request_id}_autocaption_audio.mp3")
        temp_files.append(audio_path)

        logging.info("[AUTO_CAPTION] Extracting audio from video...")
        success, message = extract_audio_from_video(video_path, audio_path)
        if not success:
            return jsonify({'success': False, 'error': message}), 500

        # Step 3: Transcribe with Whisper
        logging.info(f"[AUTO_CAPTION] Transcribing audio (language={language})...")
        words, error = transcribe_audio_with_whisper(audio_path, language)
        if error:
            return jsonify({'success': False, 'error': error}), 500

        logging.info(f"[AUTO_CAPTION] Got {len(words)} word timestamps from Whisper")

        # Step 4: Generate caption artifacts
        captions_json = json.dumps(words, indent=2)
        srt_content = generate_srt_from_words(words, max_chars=max_chars_per_line * max_lines)
        vtt_content = generate_vtt_from_words(words, max_chars=max_chars_per_line * max_lines)

        captions_json_path = os.path.join(OUTPUT_FOLDER, f"{request_id}_captions.json")
        srt_path = os.path.join(OUTPUT_FOLDER, f"{request_id}_captions.srt")
        vtt_path = os.path.join(OUTPUT_FOLDER, f"{request_id}_captions.vtt")
        temp_files.extend([captions_json_path, srt_path, vtt_path])

        with open(captions_json_path, 'w', encoding='utf-8') as f:
            f.write(captions_json)
        with open(srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        with open(vtt_path, 'w', encoding='utf-8') as f:
            f.write(vtt_content)

        # Upload artifacts to storage
        artifact_urls = {}
        for label, fpath in [("captions_json", captions_json_path), ("srt", srt_path), ("vtt", vtt_path)]:
            fname = os.path.basename(fpath)
            try:
                url = upload_to_storage(fpath, f"auto-captions/{fname}")
                if url:
                    artifact_urls[label] = url
                    continue
            except Exception:
                pass
            artifact_urls[label] = url_for('download_file', filename=fname, _external=True)
            import shutil
            try:
                shutil.copy2(fpath, os.path.join(OUTPUT_FOLDER, fname))
            except Exception:
                pass

        # Step 5: Get audio duration for Remotion
        audio_duration_seconds = None
        if words:
            audio_duration_seconds = words[-1]["end"]

        # Step 6: Render with Remotion
        render_input = json.dumps({
            'video_url': video_url,
            'word_timestamps': words,
            'subtitle_style': subtitle_style,
            'aspect_ratio': aspect_ratio,
            'audio_duration_seconds': audio_duration_seconds,
            'max_chars_per_line': max_chars_per_line,
            'max_lines': max_lines,
            'position': position,
        })

        logging.info("[AUTO_CAPTION] Starting Remotion render...")

        try:
            result = subprocess.run(
                ['npx', 'tsx', 'server/render-tiktok-captions.ts'],
                input=render_input,
                capture_output=True,
                text=True,
                timeout=600,
                cwd=os.path.dirname(os.path.abspath(__file__)) or '.'
            )
        except subprocess.TimeoutExpired:
            logging.error("[AUTO_CAPTION] Rendering timed out after 600 seconds")
            return jsonify({'success': False, 'error': 'Video rendering timed out. The video may be too long.'}), 504

        logging.info(f"[AUTO_CAPTION] Process exit code: {result.returncode}")
        if result.stderr:
            logging.info(f"[AUTO_CAPTION] stderr: {result.stderr[:3000]}")
        if result.stdout:
            logging.info(f"[AUTO_CAPTION] stdout: {result.stdout[:3000]}")

        if result.returncode != 0:
            error_msg = 'Rendering process failed'
            try:
                err_data = json.loads(result.stdout)
                error_msg = err_data.get('error', error_msg)
            except (json.JSONDecodeError, TypeError):
                if result.stderr:
                    error_msg = result.stderr[:500]
                elif result.stdout:
                    error_msg = f"Render stdout: {result.stdout[:500]}"
            logging.error(f"[AUTO_CAPTION] Render failed: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 500

        try:
            output_data = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            logging.error(f"[AUTO_CAPTION] Could not parse output: {result.stdout[:500]}")
            return jsonify({'success': False, 'error': 'Failed to parse rendering output'}), 500

        if not output_data.get('success'):
            return jsonify({'success': False, 'error': output_data.get('error', 'Unknown rendering error')}), 500

        output_video_path = output_data.get('output_video_path')
        if not output_video_path or not os.path.exists(output_video_path):
            return jsonify({'success': False, 'error': 'Rendered video file not found'}), 500

        temp_files.append(output_video_path)
        output_filename = os.path.basename(output_video_path)

        # Upload rendered video to storage
        download_url = None
        try:
            storage_url = upload_to_storage(output_video_path, f"auto-captions/{output_filename}")
            if storage_url:
                download_url = storage_url
        except Exception as storage_error:
            logging.warning(f"[AUTO_CAPTION] Storage upload failed: {str(storage_error)}")

        if not download_url:
            try:
                final_output = os.path.join(OUTPUT_FOLDER, output_filename)
                import shutil
                shutil.copy2(output_video_path, final_output)
                download_url = url_for('download_file', filename=output_filename, _external=True)
            except Exception:
                download_url = output_video_path

        logging.info("[AUTO_CAPTION] Completed successfully")
        return jsonify({
            'success': True,
            'download_url': download_url,
            'captions_json_url': artifact_urls.get('captions_json'),
            'srt_url': artifact_urls.get('srt'),
            'vtt_url': artifact_urls.get('vtt'),
            'word_count': len(words),
            'message': 'Video with auto-generated TikTok captions rendered successfully'
        })

    except Exception as e:
        logging.error(f"[AUTO_CAPTION] Error: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500
    finally:
        for f in temp_files:
            cleanup_file(f)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)