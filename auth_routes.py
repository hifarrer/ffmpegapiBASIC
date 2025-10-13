from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, ApiKey, SubscriptionPlan, UserSubscription
from forms import RegistrationForm, LoginForm, ApiKeyForm
from datetime import datetime, timedelta

auth = Blueprint('auth', __name__)

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
        
        db.session.add(user)
        db.session.commit()
        
        # Generate initial API key for new user
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
        
        flash('Registration successful! You can now log in.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('register.html', form=form)

@auth.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        
        if user and user.check_password(form.password.data):
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