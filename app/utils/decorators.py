from functools import wraps
from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.models import User, db
def role_required(role_name):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            current_user_id = get_jwt_identity()
            user = db.session.get(User, current_user_id)
            if user and user.has_role(role_name):
                return fn(*args, **kwargs)
            else:
                return jsonify({"msg": "Acesso negado: permissão insuficiente"}), 403
        return wrapper
    return decorator
