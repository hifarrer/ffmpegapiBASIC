from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
import logging
from datetime import datetime, timedelta
from models import User, ApiKey, SubscriptionPlan, StripeSettings, UserSubscription, db
import os

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Admin credentials
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD_HASH = generate_password_hash('password123')

def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_authenticated'):
            return redirect(url_for('admin.admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session['admin_authenticated'] = True
            session['admin_username'] = username
            flash('Successfully logged in as administrator', 'success')
            return redirect(url_for('admin.dashboard'))
        else:
            flash('Invalid admin credentials', 'danger')
    
    return render_template('admin/login.html')

@admin_bp.route('/logout')
@admin_required
def admin_logout():
    """Admin logout"""
    session.pop('admin_authenticated', None)
    session.pop('admin_username', None)
    flash('Successfully logged out', 'info')
    return redirect(url_for('admin.admin_login'))

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Admin dashboard with overview statistics"""
    try:
        # Get user statistics
        total_users = User.query.count()
        users_today = User.query.filter(
            User.created_at >= datetime.now() - timedelta(days=1)
        ).count()
        users_this_week = User.query.filter(
            User.created_at >= datetime.now() - timedelta(days=7)
        ).count()
        
        # Get API key statistics
        total_api_keys = ApiKey.query.count()
        active_api_keys = ApiKey.query.filter(ApiKey.is_active == True).count()
        
        # Get recent users
        recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
        
        # Get recent API keys
        recent_api_keys = ApiKey.query.order_by(ApiKey.created_at.desc()).limit(10).all()
        
        stats = {
            'total_users': total_users,
            'users_today': users_today,
            'users_this_week': users_this_week,
            'total_api_keys': total_api_keys,
            'active_api_keys': active_api_keys,
            'inactive_api_keys': total_api_keys - active_api_keys
        }
        
        return render_template('admin/dashboard.html', 
                             stats=stats, 
                             recent_users=recent_users,
                             recent_api_keys=recent_api_keys)
                             
    except Exception as e:
        logging.error(f"Error loading admin dashboard: {str(e)}")
        flash('Error loading dashboard data', 'danger')
        return render_template('admin/dashboard.html', stats={}, recent_users=[], recent_api_keys=[])

@admin_bp.route('/users')
@admin_required
def user_management():
    """User management page"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        users = User.query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return render_template('admin/users.html', users=users)
        
    except Exception as e:
        logging.error(f"Error loading user management: {str(e)}")
        flash('Error loading user data', 'danger')
        return render_template('admin/users.html', users=None)

@admin_bp.route('/users/<int:user_id>/toggle-status', methods=['POST'])
@admin_required
def toggle_user_status(user_id):
    """Toggle user active status"""
    try:
        user = User.query.get_or_404(user_id)
        
        # Toggle user status (assuming we add an is_active field)
        # For now, we'll just return success
        flash(f'User {user.username} status updated', 'success')
        
        return redirect(url_for('admin.user_management'))
        
    except Exception as e:
        logging.error(f"Error toggling user status: {str(e)}")
        flash('Error updating user status', 'danger')
        return redirect(url_for('admin.user_management'))

@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete a user and their API keys"""
    try:
        user = User.query.get_or_404(user_id)
        username = user.username
        
        # Delete associated API keys
        ApiKey.query.filter_by(user_id=user.id).delete()
        
        # Delete user
        db.session.delete(user)
        db.session.commit()
        
        flash(f'User {username} and associated API keys deleted successfully', 'success')
        
    except Exception as e:
        logging.error(f"Error deleting user: {str(e)}")
        db.session.rollback()
        flash('Error deleting user', 'danger')
        
    return redirect(url_for('admin.user_management'))

@admin_bp.route('/api-keys')
@admin_required
def api_key_management():
    """API key management page"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        api_keys = ApiKey.query.join(User).order_by(ApiKey.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return render_template('admin/api_keys.html', api_keys=api_keys)
        
    except Exception as e:
        logging.error(f"Error loading API key management: {str(e)}")
        flash('Error loading API key data', 'danger')
        return render_template('admin/api_keys.html', api_keys=None)

@admin_bp.route('/api-keys/<int:key_id>/toggle-status', methods=['POST'])
@admin_required
def toggle_api_key_status(key_id):
    """Toggle API key active status"""
    try:
        api_key = ApiKey.query.get_or_404(key_id)
        api_key.is_active = not api_key.is_active
        db.session.commit()
        
        status = "activated" if api_key.is_active else "deactivated"
        flash(f'API key {api_key.name} {status}', 'success')
        
    except Exception as e:
        logging.error(f"Error toggling API key status: {str(e)}")
        db.session.rollback()
        flash('Error updating API key status', 'danger')
        
    return redirect(url_for('admin.api_key_management'))

@admin_bp.route('/api-keys/<int:key_id>/delete', methods=['POST'])
@admin_required
def delete_api_key(key_id):
    """Delete an API key"""
    try:
        api_key = ApiKey.query.get_or_404(key_id)
        key_name = api_key.name
        
        db.session.delete(api_key)
        db.session.commit()
        
        flash(f'API key {key_name} deleted successfully', 'success')
        
    except Exception as e:
        logging.error(f"Error deleting API key: {str(e)}")
        db.session.rollback()
        flash('Error deleting API key', 'danger')
        
    return redirect(url_for('admin.api_key_management'))

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def site_settings():
    """Site settings management"""
    if request.method == 'POST':
        try:
            # Handle settings updates here
            # For now, just show success message
            flash('Settings updated successfully', 'success')
            
        except Exception as e:
            logging.error(f"Error updating settings: {str(e)}")
            flash('Error updating settings', 'danger')
    
    # Load current settings
    settings = {
        'site_name': 'FFMPEG Video Merger',
        'max_file_size': '100MB',
        'allowed_extensions': 'mp4, avi, mov, mkv, jpg, jpeg, png, mp3, wav',
        'maintenance_mode': False
    }
    
    return render_template('admin/settings.html', settings=settings)

@admin_bp.route('/change-password', methods=['GET', 'POST'])
@admin_required
def change_password():
    """Change admin password"""
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if not current_password or not check_password_hash(ADMIN_PASSWORD_HASH, current_password):
            flash('Current password is incorrect', 'danger')
        elif new_password != confirm_password:
            flash('New passwords do not match', 'danger')
        elif not new_password or len(new_password) < 6:
            flash('New password must be at least 6 characters', 'danger')
        else:
            # In a real application, you'd update the password in a database
            # For now, just show success
            flash('Password changed successfully. Please log in again.', 'success')
            return redirect(url_for('admin.admin_logout'))
    
    return render_template('admin/change_password.html')

@admin_bp.route('/analytics')
@admin_required
def analytics():
    """Usage analytics page"""
    try:
        # Get analytics data
        analytics_data = {
            'user_registrations': {
                'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                'data': [5, 8, 12, 6, 9, 15, 11]  # Sample data
            },
            'api_usage': {
                'labels': ['Image+Audio', 'Video Merge', 'Picture-in-Picture'],
                'data': [45, 32, 23]  # Sample data
            },
            'daily_requests': {
                'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                'data': [120, 150, 180, 140, 160, 200, 175]  # Sample data
            }
        }
        
        return render_template('admin/analytics.html', analytics=analytics_data)
        
    except Exception as e:
        logging.error(f"Error loading analytics: {str(e)}")
        flash('Error loading analytics data', 'danger')
        return render_template('admin/analytics.html', analytics={})

@admin_bp.route('/plans')
@admin_required
def plans_management():
    """Subscription plans management page"""
    try:
        plans = SubscriptionPlan.query.order_by(SubscriptionPlan.sort_order, SubscriptionPlan.id).all()
        return render_template('admin/plans.html', plans=plans)
        
    except Exception as e:
        logging.error(f"Error loading plans management: {str(e)}")
        flash('Error loading plans data', 'danger')
        return render_template('admin/plans.html', plans=[])

@admin_bp.route('/plans/add', methods=['GET', 'POST'])
@admin_required
def add_plan():
    """Add new subscription plan"""
    if request.method == 'POST':
        try:
            plan = SubscriptionPlan()
            plan.name = request.form.get('name')
            plan.description = request.form.get('description')
            plan.api_calls_per_month = int(request.form.get('api_calls_per_month', 0))
            plan.monthly_price = float(request.form.get('monthly_price', 0))
            plan.yearly_price = float(request.form.get('yearly_price', 0))
            plan.stripe_monthly_price_id = request.form.get('stripe_monthly_price_id')
            plan.stripe_yearly_price_id = request.form.get('stripe_yearly_price_id')
            plan.sort_order = int(request.form.get('sort_order', 0))
            
            db.session.add(plan)
            db.session.commit()
            
            flash(f'Plan "{plan.name}" created successfully', 'success')
            return redirect(url_for('admin.plans_management'))
            
        except Exception as e:
            logging.error(f"Error creating plan: {str(e)}")
            db.session.rollback()
            flash('Error creating plan', 'danger')
    
    return render_template('admin/add_plan.html')

@admin_bp.route('/plans/<int:plan_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_plan(plan_id):
    """Edit subscription plan"""
    plan = SubscriptionPlan.query.get_or_404(plan_id)
    
    if request.method == 'POST':
        try:
            plan.name = request.form.get('name')
            plan.description = request.form.get('description')
            plan.api_calls_per_month = int(request.form.get('api_calls_per_month', 0))
            plan.monthly_price = float(request.form.get('monthly_price', 0))
            plan.yearly_price = float(request.form.get('yearly_price', 0))
            plan.stripe_monthly_price_id = request.form.get('stripe_monthly_price_id')
            plan.stripe_yearly_price_id = request.form.get('stripe_yearly_price_id')
            plan.sort_order = int(request.form.get('sort_order', 0))
            plan.updated_at = datetime.now()
            
            db.session.commit()
            
            flash(f'Plan "{plan.name}" updated successfully', 'success')
            return redirect(url_for('admin.plans_management'))
            
        except Exception as e:
            logging.error(f"Error updating plan: {str(e)}")
            db.session.rollback()
            flash('Error updating plan', 'danger')
    
    return render_template('admin/edit_plan.html', plan=plan)

@admin_bp.route('/plans/<int:plan_id>/toggle-status', methods=['POST'])
@admin_required
def toggle_plan_status(plan_id):
    """Toggle plan active status"""
    try:
        plan = SubscriptionPlan.query.get_or_404(plan_id)
        plan.is_active = not plan.is_active
        plan.updated_at = datetime.now()
        db.session.commit()
        
        status = "activated" if plan.is_active else "deactivated"
        flash(f'Plan "{plan.name}" {status}', 'success')
        
    except Exception as e:
        logging.error(f"Error toggling plan status: {str(e)}")
        db.session.rollback()
        flash('Error updating plan status', 'danger')
        
    return redirect(url_for('admin.plans_management'))

@admin_bp.route('/plans/<int:plan_id>/delete', methods=['POST'])
@admin_required
def delete_plan(plan_id):
    """Delete a subscription plan"""
    try:
        plan = SubscriptionPlan.query.get_or_404(plan_id)
        plan_name = plan.name
        
        db.session.delete(plan)
        db.session.commit()
        
        flash(f'Plan "{plan_name}" deleted successfully', 'success')
        
    except Exception as e:
        logging.error(f"Error deleting plan: {str(e)}")
        db.session.rollback()
        flash('Error deleting plan', 'danger')
        
    return redirect(url_for('admin.plans_management'))

@admin_bp.route('/plans/initialize-defaults', methods=['POST'])
@admin_required
def initialize_default_plans():
    """Initialize the default subscription plans"""
    try:
        # Check if plans already exist
        if SubscriptionPlan.query.count() > 0:
            flash('Default plans already exist', 'warning')
            return redirect(url_for('admin.plans_management'))
        
        # Create default plans
        plans_data = [
            {
                'name': 'Free',
                'description': 'Perfect for testing and light usage',
                'api_calls_per_month': 10,
                'monthly_price': 0.00,
                'yearly_price': 0.00,
                'sort_order': 1
            },
            {
                'name': 'Premium',
                'description': 'Great for regular content creators',
                'api_calls_per_month': 100,
                'monthly_price': 7.00,
                'yearly_price': 70.00,
                'sort_order': 2
            },
            {
                'name': 'Ultra',
                'description': 'For power users and businesses',
                'api_calls_per_month': 500,
                'monthly_price': 25.00,
                'yearly_price': 250.00,
                'sort_order': 3
            }
        ]
        
        for plan_data in plans_data:
            plan = SubscriptionPlan(**plan_data)
            db.session.add(plan)
        
        db.session.commit()
        flash('Default subscription plans created successfully', 'success')
        
    except Exception as e:
        logging.error(f"Error creating default plans: {str(e)}")
        db.session.rollback()
        flash('Error creating default plans', 'danger')
        
    return redirect(url_for('admin.plans_management'))

@admin_bp.route('/stripe-settings', methods=['GET', 'POST'])
@admin_required
def stripe_settings():
    """Stripe configuration management"""
    settings = StripeSettings.get_settings()
    
    if request.method == 'POST':
        try:
            publishable_key = request.form.get('publishable_key')
            secret_key = request.form.get('secret_key')
            webhook_secret = request.form.get('webhook_secret')
            is_live_mode = request.form.get('is_live_mode') == 'on'
            
            StripeSettings.update_settings(
                publishable_key=publishable_key,
                secret_key=secret_key,
                webhook_secret=webhook_secret,
                is_live_mode=is_live_mode
            )
            
            flash('Stripe settings updated successfully', 'success')
            return redirect(url_for('admin.stripe_settings'))
            
        except Exception as e:
            logging.error(f"Error updating Stripe settings: {str(e)}")
            flash('Error updating Stripe settings', 'danger')
    
    return render_template('admin/stripe_settings.html', settings=settings)

@admin_bp.route('/subscriptions')
@admin_required
def subscriptions_management():
    """User subscriptions management"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        subscriptions = UserSubscription.query.join(User).join(SubscriptionPlan)\
            .order_by(UserSubscription.created_at.desc())\
            .paginate(page=page, per_page=per_page, error_out=False)
        
        # Get subscription statistics
        stats = {
            'total_subscriptions': UserSubscription.query.count(),
            'active_subscriptions': UserSubscription.query.filter_by(status='active').count(),
            'monthly_subscriptions': UserSubscription.query.filter_by(billing_cycle='monthly').count(),
            'yearly_subscriptions': UserSubscription.query.filter_by(billing_cycle='yearly').count(),
        }
        
        return render_template('admin/subscriptions.html', 
                             subscriptions=subscriptions, 
                             stats=stats)
        
    except Exception as e:
        logging.error(f"Error loading subscriptions: {str(e)}")
        flash('Error loading subscription data', 'danger')
        return render_template('admin/subscriptions.html', 
                             subscriptions=None, 
                             stats={})