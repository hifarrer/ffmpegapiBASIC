from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
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
    
    # Relationship with API keys
    api_keys = db.relationship('ApiKey', backref='user', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
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
        """Mark this API key as used"""
        self.last_used = datetime.utcnow()
        self.usage_count += 1
        db.session.commit()
    
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
    site_name = db.Column(db.String(100), default='FFMPEG Video Merger')
    site_description = db.Column(db.Text, default='Professional video processing API with FFMPEG')
    max_file_size = db.Column(db.String(20), default='100MB')
    allowed_extensions = db.Column(db.String(200), default='mp4,avi,mov,mkv,jpg,jpeg,png,mp3,wav,m4a')
    maintenance_mode = db.Column(db.Boolean, default=False)
    support_email = db.Column(db.String(100), default='support@example.com')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_settings(cls):
        """Get the current site settings (create default if none exist)"""
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
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

# Default site API key - this will be created when the app starts
SITE_DEFAULT_API_KEY = "ffmpeg_site_default_key_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))