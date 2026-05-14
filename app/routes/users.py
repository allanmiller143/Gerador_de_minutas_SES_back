from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from app.models import db, User, Role
from app.utils.decorators import role_required
from flask_jwt_extended import get_jwt_identity

users_bp = Blueprint("users", __name__, url_prefix="/users")

@users_bp.route("/create", methods=["POST"])
@jwt_required()
@role_required("admin")
def create_user():
    data = request.get_json()
    username = data.get("username")
    email = data.get("email")
    password = data.get("password")
    role_name = data.get("role", "analyst")

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

    return jsonify({"msg": "Usuário criado com sucesso"}), 201


def delete_user(user_id):
    current_user_id = get_jwt_identity()
    # Usuário que será deletado
    user = User.query.get(user_id)

    if not user:
        return jsonify({
            "msg": "Usuário não encontrado"
        }), 404

    # Impede auto exclusão
    if str(current_user_id) == str(user.id):
        return jsonify({
            "msg": "Você não pode excluir sua própria conta"
        }), 400

    # Verifica se é admin
    if any(role.name == "admin" for role in user.roles):

        total_admins = User.query.join(User.roles).filter(Role.name == "admin").count()

        # Impede apagar o último admin
        if total_admins <= 1:
            return jsonify({
                "msg": "Não é possível excluir o último administrador"
            }), 400

    try:
        db.session.delete(user)
        db.session.commit()

        return jsonify({
            "msg": "Usuário deletado com sucesso"
        }), 200

    except Exception as e:
        db.session.rollback()

        return jsonify({
            "msg": "Erro ao deletar usuário",
            "error": str(e)
        }), 500

@users_bp.route("/<int:user_id>", methods=["DELETE"])
@jwt_required()
@role_required("admin")
def delete_user_endpoint(user_id):
    return delete_user(user_id)


@users_bp.route("/", methods=["GET"])
@jwt_required()
@role_required("admin")
def list_users():
    users = User.query.all()
    output = []
    for user in users:
        user_data = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "roles": [role.name for role in user.roles]
        }
        output.append(user_data)
    return jsonify({"users": output}), 200
