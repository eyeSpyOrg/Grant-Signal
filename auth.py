"""Authentication helpers for Eye Spy Grant Scout."""
from functools import wraps
from flask import request, jsonify, session, redirect, url_for

def require_auth(f):
    """Decorator for routes requiring authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check session first (web UI)
        if 'user_id' in session:
            return f(*args, **kwargs)
        
        # Check API token
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if token:
            import db
            user = db.get_user_by_token(token)
            if user:
                request.user = user
                return f(*args, **kwargs)
        
        # If JSON request, return 401
        if request.path.startswith('/api/'):
            return jsonify({"error": "Unauthorized"}), 401
        
        # Redirect to login
        return redirect(url_for('login'))
    return decorated_function

def require_api_token(f):
    """Decorator for API endpoints requiring token."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({"error": "API token required"}), 401
        
        import db
        user = db.get_user_by_token(token)
        if not user:
            return jsonify({"error": "Invalid or expired token"}), 401
        
        request.user = user
        return f(*args, **kwargs)
    return decorated_function