from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import secrets
import string

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

# Default site API key - this will be created when the app starts
SITE_DEFAULT_API_KEY = "ffmpeg_site_default_key_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))