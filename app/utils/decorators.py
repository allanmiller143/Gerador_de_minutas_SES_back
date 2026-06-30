from functools import wraps
from flask import jsonify
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from app.models import User, db

def role_required(role_name):
    """
    Decorator to require one or more roles.
    Accepts a single role as a string or multiple roles as a list/tuple/set.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()
            current_user_id = get_jwt_identity()
            user = db.session.get(User, current_user_id)

            if not user:
                return jsonify({"msg": "Acesso negado: permissão insuficiente"}), 403

            # If multiple roles provided, allow access if user has any of them
            if isinstance(role_name, (list, tuple, set)):
                allowed = set(role_name)
                if any(role.name in allowed for role in user.roles):
                    return fn(*args, **kwargs)
            else:
                if user.has_role(role_name):
                    return fn(*args, **kwargs)

            return jsonify({"msg": "Acesso negado: permissão insuficiente"}), 403

        return wrapper

    return decorator
