from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import secrets
import string
import json
import uuid

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    active = db.Column(db.Boolean, default=True)
    email_verified = db.Column(db.Boolean, default=False)
    verification_token = db.Column(db.String(100), unique=True)
    verification_token_expiry = db.Column(db.DateTime)
    
    # Relationship with API keys
    api_keys = db.relationship('ApiKey', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def generate_verification_token(self):
        """Generate a new email verification token"""
        self.verification_token = secrets.token_urlsafe(32)
        self.verification_token_expiry = datetime.utcnow() + timedelta(hours=24)
        db.session.commit()
        return self.verification_token
    
    def verify_email(self, token):
        """Verify email with the provided token"""
        if self.verification_token == token and self.verification_token_expiry and self.verification_token_expiry > datetime.utcnow():
            self.email_verified = True
            self.verification_token = None
            self.verification_token_expiry = None
            db.session.commit()
            return True
        return False
    
    def generate_api_key(self, name="Default"):
        """Generate a new API key for this user"""
        # Generate a secure random API key
        key = 'ffmpeg_' + ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        
        api_key = ApiKey()
        api_key.key = key
        api_key.name = name
        api_key.user_id = self.id
        
        db.session.add(api_key)
        db.session.commit()
        return api_key
    
    def __repr__(self):
        return f'<User {self.username}>'

class ApiKey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    usage_count = db.Column(db.Integer, default=0)
    
    def mark_used(self):
        """Mark this API key as used with retry logic for stale connections"""
        from sqlalchemy.exc import OperationalError
        max_retries = 3
        for attempt in range(max_retries):
            try:
                self.last_used = datetime.utcnow()
                self.usage_count += 1
                db.session.commit()
                return
            except OperationalError as e:
                db.session.rollback()
                if attempt < max_retries - 1:
                    db.session.remove()
                    continue
                raise e
    
    def __repr__(self):
        return f'<ApiKey {self.name}>'

class SubscriptionPlan(db.Model):
    __tablename__ = 'subscription_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    description = db.Column(db.Text)
    api_calls_per_month = db.Column(db.Integer, nullable=False)
    monthly_price = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    yearly_price = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    stripe_monthly_price_id = db.Column(db.String(255))
    stripe_yearly_price_id = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<SubscriptionPlan {self.name}>'

class StripeSettings(db.Model):
    __tablename__ = 'stripe_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    publishable_key = db.Column(db.Text)
    secret_key = db.Column(db.Text)
    webhook_secret = db.Column(db.Text)
    is_live_mode = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_settings(cls):
        """Get the current Stripe settings"""
        return cls.query.first()
    
    @classmethod
    def update_settings(cls, publishable_key=None, secret_key=None, webhook_secret=None, is_live_mode=False):
        """Update or create Stripe settings"""
        settings = cls.get_settings()
        if not settings:
            settings = cls()
            db.session.add(settings)
        
        if publishable_key is not None:
            settings.publishable_key = publishable_key
        if secret_key is not None:
            settings.secret_key = secret_key
        if webhook_secret is not None:
            settings.webhook_secret = webhook_secret
        settings.is_live_mode = is_live_mode
        settings.updated_at = datetime.utcnow()
        
        db.session.commit()
        return settings
    
    def __repr__(self):
        return f'<StripeSettings {"Live" if self.is_live_mode else "Test"}>'

class UserSubscription(db.Model):
    __tablename__ = 'user_subscriptions'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plans.id'), nullable=False)
    stripe_subscription_id = db.Column(db.String(255), unique=True)
    stripe_customer_id = db.Column(db.String(255))
    status = db.Column(db.String(50), default='active')  # active, canceled, past_due, etc.
    billing_cycle = db.Column(db.String(20))  # monthly, yearly
    current_period_start = db.Column(db.DateTime)
    current_period_end = db.Column(db.DateTime)
    api_calls_used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref=db.backref('subscription', uselist=False))
    plan = db.relationship('SubscriptionPlan', backref='subscriptions')
    
    def reset_monthly_usage(self):
        """Reset API call usage for the month"""
        self.api_calls_used = 0
        self.updated_at = datetime.utcnow()
        db.session.commit()
    
    def can_make_api_call(self):
        """Check if user can make an API call based on their plan"""
        if self.status != 'active':
            return False
        return self.api_calls_used < self.plan.api_calls_per_month
    
    def increment_api_usage(self):
        """Increment API call usage"""
        self.api_calls_used += 1
        self.updated_at = datetime.utcnow()
        db.session.commit()
    
    def __repr__(self):
        return f'<UserSubscription {self.user.username} - {self.plan.name}>'

class SiteSettings(db.Model):
    __tablename__ = 'site_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    site_name = db.Column(db.String(100), default='FFMPEG API')
    site_description = db.Column(db.Text, default='Professional video processing API with FFMPEG')
    max_file_size = db.Column(db.String(20), default='100MB')
    allowed_extensions = db.Column(db.String(200), default='mp4,avi,mov,mkv,jpg,jpeg,png,mp3,wav,m4a')
    maintenance_mode = db.Column(db.Boolean, default=False)
    support_email = db.Column(db.String(100), default='support@example.com')
    admin_username = db.Column(db.String(50), default='admin')
    admin_password_hash = db.Column(db.String(256))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_settings(cls):
        """Get the current site settings (create default if none exist)"""
        settings = cls.query.first()
        if not settings:
            settings = cls()
            # Set default admin password hash
            settings.admin_password_hash = generate_password_hash('password123')
            db.session.add(settings)
            db.session.commit()
        elif not settings.admin_password_hash:
            # Migrate existing settings to include admin password
            settings.admin_password_hash = generate_password_hash('password123')
            db.session.commit()
        return settings
    
    @classmethod
    def update_settings(cls, **kwargs):
        """Update site settings"""
        settings = cls.get_settings()
        for key, value in kwargs.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        return settings
    
    @classmethod
    def update_admin_password(cls, new_password):
        """Update admin password"""
        settings = cls.get_settings()
        settings.admin_password_hash = generate_password_hash(new_password)
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        return settings
    
    @classmethod
    def get_admin_credentials(cls):
        """Get admin username and password hash"""
        settings = cls.get_settings()
        return settings.admin_username, settings.admin_password_hash
    
    def __repr__(self):
        return f'<SiteSettings {self.site_name}>'

class Job(db.Model):
    __tablename__ = 'jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    job_type = db.Column(db.String(50), nullable=False)  # merge_image_audio, merge_videos, picture_in_picture
    status = db.Column(db.String(20), default='pending')  # pending, processing, completed, failed
    input_data = db.Column(db.Text)  # JSON string of input parameters
    result_data = db.Column(db.Text)  # JSON string of result data
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = db.relationship('User', backref='jobs')
    
    def set_input_data(self, data):
        """Set input data as JSON string"""
        self.input_data = json.dumps(data)
        db.session.commit()
    
    def get_input_data(self):
        """Get input data as Python object"""
        if self.input_data:
            return json.loads(self.input_data)
        return None
    
    def set_result_data(self, data):
        """Set result data as JSON string"""
        self.result_data = json.dumps(data)
        self.updated_at = datetime.utcnow()
        db.session.commit()
    
    def get_result_data(self):
        """Get result data as Python object"""
        if self.result_data:
            return json.loads(self.result_data)
        return None
    
    def update_status(self, status, error_message=None):
        """Update job status"""
        self.status = status
        if error_message:
            self.error_message = error_message
        self.updated_at = datetime.utcnow()
        db.session.commit()
    
    def __repr__(self):
        return f'<Job {self.job_id} - {self.job_type} - {self.status}>'

class ApiLog(db.Model):
    __tablename__ = 'api_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    username = db.Column(db.String(80), nullable=True, index=True)
    api_key_id = db.Column(db.Integer, db.ForeignKey('api_key.id'), nullable=True)
    endpoint = db.Column(db.String(255), nullable=False, index=True)
    method = db.Column(db.String(10), nullable=False)
    request_data = db.Column(db.Text)
    response_data = db.Column(db.Text)
    status_code = db.Column(db.Integer, index=True)
    error_message = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.String(500))
    processing_time_ms = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    user = db.relationship('User', backref='api_logs')
    api_key = db.relationship('ApiKey', backref='api_logs')
    
    def set_request_data(self, data):
        """Set request data as JSON string, sanitizing sensitive info"""
        if data:
            sanitized = self._sanitize_data(data)
            self.request_data = json.dumps(sanitized, default=str)
    
    def get_request_data(self):
        """Get request data as Python object"""
        if self.request_data:
            try:
                return json.loads(self.request_data)
            except:
                return self.request_data
        return None
    
    def set_response_data(self, data):
        """Set response data as JSON string"""
        if data:
            try:
                if isinstance(data, str):
                    self.response_data = data[:10000]
                else:
                    self.response_data = json.dumps(data, default=str)[:10000]
            except:
                self.response_data = str(data)[:10000]
    
    def get_response_data(self):
        """Get response data as Python object"""
        if self.response_data:
            try:
                return json.loads(self.response_data)
            except:
                return self.response_data
        return None
    
    def _sanitize_data(self, data):
        """Remove sensitive data like API keys from logs"""
        if isinstance(data, dict):
            sanitized = {}
            for key, value in data.items():
                if key.lower() in ['api_key', 'password', 'secret', 'token', 'authorization']:
                    sanitized[key] = '[REDACTED]'
                elif isinstance(value, (dict, list)):
                    sanitized[key] = self._sanitize_data(value)
                else:
                    sanitized[key] = value
            return sanitized
        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data]
        return data
    
    @classmethod
    def log_request(cls, endpoint, method, user_id=None, username=None, api_key_id=None,
                   request_data=None, response_data=None, status_code=None, 
                   error_message=None, ip_address=None, user_agent=None, processing_time_ms=None):
        """Create a new API log entry"""
        import logging
        try:
            log = cls()
            log.endpoint = endpoint
            log.method = method
            log.user_id = user_id
            log.username = username
            log.api_key_id = api_key_id
            log.set_request_data(request_data)
            log.set_response_data(response_data)
            log.status_code = status_code
            log.error_message = error_message[:1000] if error_message else None
            log.ip_address = ip_address
            log.user_agent = user_agent[:500] if user_agent else None
            log.processing_time_ms = processing_time_ms
            
            db.session.add(log)
            db.session.commit()
            logging.debug(f"API log saved: {endpoint} - {status_code}")
            return log
        except Exception as e:
            logging.error(f"Failed to save API log for {endpoint}: {str(e)}", exc_info=True)
            db.session.rollback()
            return None
    
    def __repr__(self):
        return f'<ApiLog {self.endpoint} - {self.status_code}>'

# Default site API key - this will be created when the app starts
SITE_DEFAULT_API_KEY = "ffmpeg_site_default_key_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))