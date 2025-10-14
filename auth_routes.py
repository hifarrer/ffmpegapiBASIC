from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, ApiKey, SubscriptionPlan, UserSubscription
from forms import RegistrationForm, LoginForm, ApiKeyForm
from datetime import datetime, timedelta
import logging
import resend
import requests
import os

auth = Blueprint('auth', __name__)

def get_resend_credentials():
    """Get Resend API credentials from Replit connector"""
    try:
        hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
        
        # Get authentication token
        x_replit_token = None
        if os.environ.get('REPL_IDENTITY'):
            x_replit_token = 'repl ' + os.environ.get('REPL_IDENTITY')
        elif os.environ.get('WEB_REPL_RENEWAL'):
            x_replit_token = 'depl ' + os.environ.get('WEB_REPL_RENEWAL')
        
        if not x_replit_token or not hostname:
            logging.error("Missing Replit connector credentials")
            return None, None
        
        # Fetch connection settings
        response = requests.get(
            f'https://{hostname}/api/v2/connection?include_secrets=true&connector_names=resend',
            headers={
                'Accept': 'application/json',
                'X_REPLIT_TOKEN': x_replit_token
            }
        )
        
        if response.status_code != 200:
            logging.error(f"Failed to fetch Resend credentials: {response.status_code}")
            return None, None
        
        data = response.json()
        items = data.get('items', [])
        
        if not items:
            logging.error("No Resend connection found")
            return None, None
        
        settings = items[0].get('settings', {})
        api_key = settings.get('api_key')
        from_email = settings.get('from_email')
        
        if not api_key:
            logging.error("Resend API key not found")
            return None, None
        
        return api_key, from_email
        
    except Exception as e:
        logging.error(f"Error getting Resend credentials: {str(e)}")
        return None, None

def send_verification_email(user_email, username, verification_token):
    """Send email verification email using Resend"""
    try:
        # Get Resend credentials
        api_key, from_email = get_resend_credentials()
        
        if not api_key:
            logging.error("Cannot send verification email - Resend not configured")
            return False
        
        # Use configured from_email or fallback
        sender_email = from_email if from_email else 'noreply@ffmpegapi.net'
        
        # Initialize Resend client
        resend.api_key = api_key
        
        # Build verification URL
        verification_url = url_for('auth.verify_email', token=verification_token, _external=True)
        
        # Prepare email content
        email_subject = "Verify your FFMPEG API account"
        email_html = f"""
        <h2>Welcome to FFMPEG API, {username}!</h2>
        <p>Thank you for registering. Please verify your email address to activate your account.</p>
        <p>Click the button below to verify your email:</p>
        <p style="margin: 30px 0;">
            <a href="{verification_url}" 
               style="background-color: #007bff; color: white; padding: 12px 30px; 
                      text-decoration: none; border-radius: 5px; display: inline-block;">
                Verify Email Address
            </a>
        </p>
        <p>Or copy and paste this link into your browser:</p>
        <p><a href="{verification_url}">{verification_url}</a></p>
        <p>This verification link will expire in 24 hours.</p>
        <p>If you didn't create an account with FFMPEG API, please ignore this email.</p>
        <hr style="margin: 30px 0; border: none; border-top: 1px solid #eee;">
        <p style="color: #666; font-size: 12px;">FFMPEG API - Video Processing Made Easy</p>
        """
        
        # Send email
        params = {
            "from": sender_email,
            "to": [user_email],
            "subject": email_subject,
            "html": email_html
        }
        
        email = resend.Emails.send(params)
        logging.info(f"Verification email sent to {user_email}")
        return True
        
    except Exception as e:
        logging.error(f"Error sending verification email: {str(e)}", exc_info=True)
        return False

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = RegistrationForm()
    if form.validate_on_submit():
        user = User()
        user.username = form.username.data
        user.email = form.email.data
        user.set_password(form.password.data)
        user.email_verified = False
        
        db.session.add(user)
        db.session.commit()
        
        # Generate verification token
        token = user.generate_verification_token()
        
        # Send verification email
        email_sent = send_verification_email(user.email, user.username, token)
        
        if email_sent:
            flash('Registration successful! Please check your email to verify your account.', 'success')
        else:
            flash('Registration successful! However, we could not send the verification email. Please contact support.', 'warning')
        
        # Generate initial API key for new user (will be usable after verification)
        user.generate_api_key("My First API Key")
        
        # Assign free plan to new user
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
        
        return redirect(url_for('auth.login'))
    
    return render_template('register.html', form=form)

@auth.route('/verify-email/<token>')
def verify_email(token):
    """Verify user email with token"""
    user = User.query.filter_by(verification_token=token).first()
    
    if not user:
        flash('Invalid verification link.', 'danger')
        return redirect(url_for('auth.login'))
    
    if user.email_verified:
        flash('Your email is already verified. You can log in.', 'info')
        return redirect(url_for('auth.login'))
    
    if user.verify_email(token):
        flash('Your email has been verified! You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    else:
        flash('Verification link has expired. Please request a new one.', 'danger')
        return redirect(url_for('auth.resend_verification'))

@auth.route('/resend-verification', methods=['GET', 'POST'])
def resend_verification():
    """Resend verification email"""
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        
        if user and not user.email_verified:
            token = user.generate_verification_token()
            email_sent = send_verification_email(user.email, user.username, token)
            
            if email_sent:
                flash('Verification email has been sent. Please check your inbox.', 'success')
            else:
                flash('Could not send verification email. Please try again later.', 'danger')
        elif user and user.email_verified:
            flash('Your email is already verified.', 'info')
        else:
            flash('No account found with that email address.', 'danger')
        
        return redirect(url_for('auth.login'))
    
    return render_template('resend_verification.html')

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        
        if user and user.check_password(form.password.data):
            if not user.email_verified:
                flash('Please verify your email address before logging in. Check your inbox for the verification link.', 'warning')
                return render_template('login.html', form=form, show_resend=True, user_email=user.email)
            
            login_user(user, remember=form.remember_me.data)
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard'))
        
        flash('Invalid username or password', 'danger')
    
    return render_template('login.html', form=form)

@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@auth.route('/dashboard')
@login_required
def dashboard():
    api_keys = [key for key in current_user.api_keys if key.is_active]
    
    # Get user's subscription plan
    user_subscription = UserSubscription.query.filter_by(
        user_id=current_user.id, 
        status='active'
    ).first()
    
    # Check if user is on free plan
    is_free_plan = True  # Default to free plan if no subscription
    plan_name = 'Free'
    if user_subscription and user_subscription.plan:
        plan_name = user_subscription.plan.name
        # Consider plan as "free" if named "Free" or has $0 monthly price
        monthly_price = user_subscription.plan.monthly_price
        is_free_plan = (
            user_subscription.plan.name.lower() == 'free' or 
            (monthly_price is not None and float(monthly_price) == 0.0)
        )
    
    # Determine if user can create more API keys
    can_create_more_keys = True
    if is_free_plan and len(api_keys) >= 1:
        can_create_more_keys = False
    
    return render_template('dashboard.html', 
                         api_keys=api_keys, 
                         is_free_plan=is_free_plan,
                         plan_name=plan_name,
                         can_create_more_keys=can_create_more_keys)

@auth.route('/generate-api-key', methods=['GET', 'POST'])
@login_required
def generate_api_key():
    form = ApiKeyForm()
    if form.validate_on_submit():
        # Check user's subscription plan
        user_subscription = UserSubscription.query.filter_by(
            user_id=current_user.id, 
            status='active'
        ).first()
        
        # Count active API keys
        active_keys = ApiKey.query.filter_by(
            user_id=current_user.id, 
            is_active=True
        ).count()
        
        # Check if user is on free plan
        is_free_plan = True  # Default to free plan if no subscription
        if user_subscription and user_subscription.plan:
            # Consider plan as "free" if it's named "Free" or has $0 monthly price
            monthly_price = user_subscription.plan.monthly_price
            is_free_plan = (
                user_subscription.plan.name.lower() == 'free' or 
                (monthly_price is not None and float(monthly_price) == 0.0)
            )
        
        # Enforce limit for free users
        if is_free_plan and active_keys >= 1:
            flash('Free plan users can only have 1 API key. Please upgrade to a paid plan to create multiple API keys.', 'warning')
            return redirect(url_for('auth.dashboard'))
        
        # Generate API key
        api_key = current_user.generate_api_key(form.name.data)
        flash(f'New API key generated: {api_key.key}', 'success')
        return redirect(url_for('auth.dashboard'))
    
    return render_template('generate_api_key.html', form=form)

@auth.route('/delete-api-key/<int:key_id>', methods=['POST'])
@login_required
def delete_api_key(key_id):
    api_key = ApiKey.query.filter_by(id=key_id, user_id=current_user.id).first()
    if api_key:
        api_key.is_active = False
        db.session.commit()
        flash('API key deactivated successfully.', 'success')
    else:
        flash('API key not found.', 'danger')
    
    return redirect(url_for('auth.dashboard'))