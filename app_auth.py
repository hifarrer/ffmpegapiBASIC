import os
import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix
from models import db, User, ApiKey, SITE_DEFAULT_API_KEY

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
    "pool_size": 10,
    "max_overflow": 20,
    "pool_timeout": 60,
}

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)