from flask import Blueprint, redirect, url_for
from flask_login import login_required

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
@login_required
def index():
    return redirect(url_for('safety.index'))
