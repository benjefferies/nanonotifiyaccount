import datetime
import logging
import re

import bcrypt
import flask
import requests
from flask import render_template, Blueprint, url_for, request
from flask_login import login_required, login_user, current_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.utils import redirect

from app.config import RECAPTCHA_SECRET
from app.database import db_session
from app.models import Subscription, User

logger = logging.getLogger(__name__)

nano = Blueprint('profile', __name__, template_folder='templates', static_folder='static')

url_regex = re.compile(
        r'^(?:http|ftp)s?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' #domain...
        r'localhost|' #localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)


@nano.teardown_app_request
def shutdown_session(exception=None):
    if not exception:
        try:
            db_session.commit()
        except SQLAlchemyError:
            db_session.rollback()
            db_session.remove()
    else:
        db_session.rollback()
    db_session.remove()


@nano.before_request
def before_request():
    flask.session.permanent = True
    nano.permanent_session_lifetime = datetime.timedelta(minutes=30)
    flask.session.modified = True
    flask.g.user = current_user


@nano.app_errorhandler(Exception)
def handle_exception(e):
    logger.exception(str(e))
    return render_template('error.html', error='Invalid email or password')


@nano.route('/', methods=['POST'])
def login():
    email = request.form['email']
    password = request.form['password']
    user = db_session.query(User).filter(func.lower(User.email) == func.lower(email)).first()
    logger.info(f'Attempt to login user {email}')
    if user and bcrypt.checkpw(password.encode(), user.password):
        logger.info(f'{email} logged in')
        login_user(user)
        return redirect(url_for('.subscribe'))
    else:
        return render_template('index.html', error='Invalid email or password')


@nano.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for('.login'))


@nano.route('/register', methods=['POST'])
def get_register():
    if RECAPTCHA_SECRET:
        data = {'secret': RECAPTCHA_SECRET, 'response': request.form.get('g-recaptcha-response'),
                'remoteip': request.remote_addr}
        response = requests.post('https://www.google.com/recaptcha/api/siteverify', data=data).json()
        if not response.get('success'):
            return render_template('register.html', error='Invalid reCAPTCHA')
    email = request.form.get('email')
    password = request.form.get('password')
    if not email or not password or not re.match('(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)', email):
        return render_template('register.html', error='Enter a valid email and password')
    if len(password) < 8:
        return render_template('register.html', error='Password must be more than 8 characters')

    logger.info(f'Registering {email}')
    password = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    user = User(email, password)
    db_session.add(user)
    return redirect(url_for('.get_login'))


@nano.route('/register', methods=['GET'])
def register():
    return render_template('register.html')


@nano.route('/', methods=['GET'])
def get_login():
    return render_template('index.html')


@nano.route('/subscribe', methods=['POST'])
@login_required
def subscribe():
    account = request.form.get('account')
    subscriptions = get_subscriptions_for_user()
    if not account or not re.match('xrb_[a-zA-Z0-9]{60}', account):
        return render_template('subscribe.html', error='Add an account in the correct format', subscriptions=subscriptions)
    if request.form['action'] == 'delete':
        logger.info(f'{current_user.email} deleting subscription to {account}')
        for subscription in subscriptions:
            if subscription.account == account:
                subscriptions.remove(subscription)
                db_session.delete(subscription)
    elif not db_session.query(Subscription).filter(func.lower(Subscription.email) == func.lower(current_user.email)) \
            .filter(Subscription.account == account).first():
        logger.info(f'{current_user.email} adding subscription to {account}')
        subscription = Subscription(email=current_user.email, account=account, webhook=current_user.webhook)
        db_session.add(subscription)
        subscriptions.append(subscription)
    return render_template('subscribe.html', subscriptions=subscriptions)


def get_subscriptions_for_user():
    return db_session.query(Subscription).filter(Subscription.email == current_user.email).all()


@nano.route('/subscribe', methods=['GET'])
@login_required
def get_subscribe():
    email = current_user.email
    logger.info(f'{email} getting subscriptions')
    subscriptions = db_session.query(Subscription).filter(func.lower(Subscription.email) == func.lower(current_user.email)).all()
    return render_template('subscribe.html', subscriptions=subscriptions)


@nano.route('/settings', methods=['GET'])
@login_required
def get_settings():
    logger.info(f'{current_user.email} getting settings')
    return render_template('settings.html', webhook=current_user.webhook)


@nano.route('/settings', methods=['POST'])
@login_required
def save_settings():
    webhook = request.form.get('webhook')
    email = current_user.email
    logger.info(f'{email} saving webhook {webhook}')
    if webhook and url_regex.match(webhook):
        current_user.webhook=webhook
        db_session.merge(current_user)
    else:
        return render_template('settings.html', webhook=webhook, error='Webhook is invalid')
    return render_template('settings.html', webhook=webhook)
