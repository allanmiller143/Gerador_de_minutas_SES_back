from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, create_refresh_token, jwt_required, get_jwt_identity, get_jwt
from app.models import db, User, Role

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    role_name = data.get("role", "analyst") # Default role is 'analyst'

    if not username or not email or not password:
        return jsonify({"msg": "Usuário, e-mail e senha são obrigatórios"}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"msg": "Nome de usuário já existe"}), 409
    if User.query.filter_by(email=email).first():
        return jsonify({"msg": "E-mail já registrado"}), 409

    role = Role.query.filter_by(name=role_name).first()
    if not role:
        return jsonify({"msg": f"Perfil {role_name} não encontrado"}), 400

    new_user = User(username=username, email=email, password=password, roles=[role])
    db.session.add(new_user)
    db.session.commit()

    return jsonify({"msg": "Usuário registrado com sucesso"}), 201

@auth_bp.route("/login", methods=["POST"])
def login():
    email = request.json.get("email", None)
    password = request.json.get("password", None)

    user = User.query.filter_by(email=email).first()

    if user and user.check_password(password):
        access_token = create_access_token(identity=str(user.id))
        refresh_token = create_refresh_token(identity=str(user.id))
        return jsonify(access_token=access_token, refresh_token=refresh_token), 200
    else:
        return jsonify({"msg": "Credenciais inválidas"}), 401

@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    current_user_id = get_jwt_identity()
    new_access_token = create_access_token(identity=str(current_user_id))
    return jsonify(access_token=new_access_token), 200

@auth_bp.route("/protected", methods=["GET"])
@jwt_required()
def protected():
    current_user_id = get_jwt_identity()
    user = db.session.get(User, current_user_id)
    return jsonify(logged_in_as=user.username, roles=[role.name for role in user.roles]), 200
