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
    return render_template('dashboard.html', api_keys=api_keys)

@auth.route('/generate-api-key', methods=['GET', 'POST'])
@login_required
def generate_api_key():
    form = ApiKeyForm()
    if form.validate_on_submit():
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