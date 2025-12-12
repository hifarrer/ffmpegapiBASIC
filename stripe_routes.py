from flask import Blueprint, request, jsonify, redirect, url_for, session, flash, render_template
import stripe
import logging
from datetime import datetime, timedelta
from models import db, User, SubscriptionPlan, StripeSettings, UserSubscription
from flask_login import login_required, current_user

stripe_bp = Blueprint('stripe', __name__)

def get_stripe_config():
    """Get current Stripe configuration"""
    settings = StripeSettings.get_settings()
    if not settings or not settings.secret_key:
        return None
    
    stripe.api_key = settings.secret_key
    return settings

@stripe_bp.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    """Create Stripe Checkout Session for subscription"""
    try:
        settings = get_stripe_config()
        if not settings:
            flash('Stripe is not configured. Please contact support.', 'error')
            return redirect(url_for('dashboard'))
        
        plan_id = request.form.get('plan_id')
        billing_cycle = request.form.get('billing_cycle')  # 'monthly' or 'yearly'
        
        plan = SubscriptionPlan.query.get_or_404(plan_id)
        
        # Get the correct Stripe price ID
        if billing_cycle == 'yearly':
            price_id = plan.stripe_yearly_price_id
        else:
            price_id = plan.stripe_monthly_price_id
            
        if not price_id:
            flash('This plan is not available for the selected billing cycle.', 'error')
            return redirect(url_for('dashboard'))
        
        # Check if user already has an active paid subscription
        existing_subscription = UserSubscription.query.filter_by(
            user_id=current_user.id,
            status='active'
        ).first()
        
        # Allow upgrades from free plan to paid plans
        if existing_subscription:
            existing_plan = SubscriptionPlan.query.get(existing_subscription.plan_id)
            # Only block if they have a paid plan, allow upgrades from free
            if existing_plan and existing_plan.name != 'Free' and existing_plan.stripe_monthly_price_id:
                flash('You already have an active paid subscription. Please cancel it first to change plans.', 'warning')
                return redirect(url_for('pricing'))
        
        # Get or create Stripe customer
        stripe_customer_id = None
        try:
            # Try to find existing customer by email
            customers = stripe.Customer.list(email=current_user.email, limit=1)
            if customers.data:
                stripe_customer_id = customers.data[0].id
            else:
                # Create new customer
                customer = stripe.Customer.create(
                    email=current_user.email,
                    name=current_user.username,
                    metadata={
                        'user_id': current_user.id,
                        'username': current_user.username
                    }
                )
                stripe_customer_id = customer.id
        except Exception as e:
            logging.error(f"Error creating/finding Stripe customer: {str(e)}")
            flash('Error setting up payment. Please try again.', 'error')
            return redirect(url_for('dashboard'))
        
        # Create checkout session
        success_url = request.url_root.rstrip('/') + url_for('stripe.subscription_success', _external=False)
        cancel_url = request.url_root.rstrip('/') + url_for('stripe.subscription_cancel', _external=False)
        
        checkout_session = stripe.checkout.Session.create(
            customer=stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=cancel_url,
            metadata={
                'user_id': str(current_user.id),
                'plan_id': str(plan_id),
                'billing_cycle': str(billing_cycle)
            }
        )
        
        return redirect(checkout_session.url or '', code=303)
        
    except Exception as e:
        logging.error(f"Error creating checkout session: {str(e)}")
        logging.error(f"Using price_id: {price_id}")
        logging.error(f"Live mode enabled: {settings.is_live_mode}")
        flash('Error creating checkout session. Please try again.', 'error')
        return redirect(url_for('dashboard'))

@stripe_bp.route('/subscription-success')
@login_required
def subscription_success():
    """Handle successful subscription"""
    session_id = request.args.get('session_id')
    
    if not session_id:
        flash('Invalid session. Please try again.', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        settings = get_stripe_config()
        if not settings:
            flash('Configuration error. Please contact support.', 'error')
            return redirect(url_for('dashboard'))
        
        # Retrieve checkout session
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        
        # Initialize tracking data
        transaction_value = 1.0
        transaction_id = session_id
        plan_name = 'Premium Plan'
        
        if checkout_session.payment_status == 'paid':
            # Get subscription details
            subscription_id = checkout_session.subscription
            if subscription_id:
                subscription = stripe.Subscription.retrieve(str(subscription_id))
                
                # Create or update user subscription record
                user_subscription = UserSubscription.query.filter_by(
                    user_id=current_user.id
                ).first()
                
                if not user_subscription:
                    user_subscription = UserSubscription()
                    user_subscription.user_id = current_user.id
                    db.session.add(user_subscription)
                else:
                    # If upgrading from free plan, reset usage
                    existing_plan = SubscriptionPlan.query.get(user_subscription.plan_id) if user_subscription.plan_id else None
                    if existing_plan and existing_plan.name == 'Free':
                        user_subscription.api_calls_used = 0
                
                metadata = checkout_session.metadata or {}
                plan_id = int(metadata.get('plan_id', 0))
                user_subscription.plan_id = plan_id
                user_subscription.stripe_subscription_id = subscription.id
                user_subscription.stripe_customer_id = str(subscription.customer)
                user_subscription.status = str(subscription.status)
                user_subscription.billing_cycle = metadata.get('billing_cycle', 'monthly')
                user_subscription.current_period_start = datetime.fromtimestamp(int(subscription.get('current_period_start', 0)))
                user_subscription.current_period_end = datetime.fromtimestamp(int(subscription.get('current_period_end', 0)))
                user_subscription.api_calls_used = 0  # Reset usage
                
                # Get plan details for conversion tracking
                plan = SubscriptionPlan.query.get(plan_id)
                if plan:
                    plan_name = plan.name
                    # Get transaction value from checkout session
                    if checkout_session.amount_total:
                        transaction_value = checkout_session.amount_total / 100.0  # Stripe uses cents
            
            db.session.commit()
            
            # Render success page with conversion tracking
            return render_template('subscription_success.html',
                                   transaction_value=transaction_value,
                                   transaction_id=transaction_id,
                                   plan_name=plan_name)
        else:
            flash('Payment was not completed. Please try again.', 'warning')
            
    except Exception as e:
        logging.error(f"Error processing subscription success: {str(e)}")
        flash('Error activating subscription. Please contact support.', 'error')
    
    return redirect(url_for('dashboard'))

@stripe_bp.route('/subscription-cancel')
@login_required
def subscription_cancel():
    """Handle cancelled subscription checkout"""
    flash('Subscription signup was cancelled. You can try again anytime.', 'info')
    return redirect(url_for('dashboard'))

@stripe_bp.route('/cancel-subscription', methods=['POST'])
@login_required
def cancel_subscription():
    """Cancel user's subscription"""
    try:
        settings = get_stripe_config()
        if not settings:
            flash('Configuration error. Please contact support.', 'error')
            return redirect(url_for('dashboard'))
        
        user_subscription = UserSubscription.query.filter_by(
            user_id=current_user.id,
            status='active'
        ).first()
        
        if not user_subscription or not user_subscription.stripe_subscription_id:
            flash('No active subscription found.', 'error')
            return redirect(url_for('dashboard'))
        
        # Cancel subscription in Stripe
        stripe.Subscription.modify(
            user_subscription.stripe_subscription_id,
            cancel_at_period_end=True
        )
        
        # Update local record
        user_subscription.status = 'canceling'
        db.session.commit()
        
        flash('Your subscription has been cancelled and will remain active until the end of your current billing period.', 'info')
        
    except Exception as e:
        logging.error(f"Error cancelling subscription: {str(e)}")
        flash('Error cancelling subscription. Please contact support.', 'error')
    
    return redirect(url_for('dashboard'))

@stripe_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhooks"""
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        settings = StripeSettings.get_settings()
        if not settings or not settings.webhook_secret:
            logging.error("Webhook secret not configured")
            return jsonify({'error': 'Webhook not configured'}), 400
        
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.webhook_secret
        )
        
    except ValueError as e:
        logging.error(f"Invalid payload: {e}")
        return jsonify({'error': 'Invalid payload'}), 400
    except Exception as e:  # Catch all stripe errors
        logging.error(f"Invalid signature: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    
    # Handle the event
    try:
        if event['type'] == 'customer.subscription.created':
            handle_subscription_created(event['data']['object'])
        elif event['type'] == 'customer.subscription.updated':
            handle_subscription_updated(event['data']['object'])
        elif event['type'] == 'customer.subscription.deleted':
            handle_subscription_deleted(event['data']['object'])
        elif event['type'] == 'invoice.payment_succeeded':
            handle_payment_succeeded(event['data']['object'])
        elif event['type'] == 'invoice.payment_failed':
            handle_payment_failed(event['data']['object'])
        else:
            logging.info(f"Unhandled event type: {event['type']}")
    
    except Exception as e:
        logging.error(f"Error handling webhook: {str(e)}")
        return jsonify({'error': 'Webhook handling failed'}), 500
    
    return jsonify({'status': 'success'})

def handle_subscription_created(subscription):
    """Handle subscription created webhook"""
    logging.info(f"Subscription created: {subscription['id']}")
    # Subscription is usually handled in the success callback
    # This is mainly for logging and backup handling

def handle_subscription_updated(subscription):
    """Handle subscription updated webhook"""
    user_subscription = UserSubscription.query.filter_by(
        stripe_subscription_id=subscription['id']
    ).first()
    
    if user_subscription:
        user_subscription.status = str(subscription['status'])
        user_subscription.current_period_start = datetime.fromtimestamp(int(subscription.get('current_period_start', 0)))
        user_subscription.current_period_end = datetime.fromtimestamp(int(subscription.get('current_period_end', 0)))
        db.session.commit()
        logging.info(f"Updated subscription {subscription['id']} status to {subscription['status']}")

def handle_subscription_deleted(subscription):
    """Handle subscription deleted webhook"""
    user_subscription = UserSubscription.query.filter_by(
        stripe_subscription_id=subscription['id']
    ).first()
    
    if user_subscription:
        user_subscription.status = 'canceled'
        db.session.commit()
        logging.info(f"Cancelled subscription {subscription['id']}")

def handle_payment_succeeded(invoice):
    """Handle successful payment webhook"""
    subscription_id = invoice.get('subscription')
    if subscription_id:
        user_subscription = UserSubscription.query.filter_by(
            stripe_subscription_id=subscription_id
        ).first()
        
        if user_subscription:
            # Reset API usage for new billing period
            user_subscription.api_calls_used = 0
            user_subscription.status = 'active'
            db.session.commit()
            logging.info(f"Payment succeeded for subscription {subscription_id}, reset usage")

def handle_payment_failed(invoice):
    """Handle failed payment webhook"""
    subscription_id = invoice.get('subscription')
    if subscription_id:
        user_subscription = UserSubscription.query.filter_by(
            stripe_subscription_id=subscription_id
        ).first()
        
        if user_subscription:
            user_subscription.status = 'past_due'
            db.session.commit()
            logging.info(f"Payment failed for subscription {subscription_id}, marked as past_due")