from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import SQLAlchemyError

from app.models import TextoCategoria, TextoPadrao, db


textos_padroes_bp = Blueprint(
    "textos_padroes",
    __name__,
    url_prefix="/textos-padroes",
)
categorias_textos_padroes_bp = Blueprint(
    "categorias_textos_padroes",
    __name__,
    url_prefix="/categorias-textos-padroes",
)


@textos_padroes_bp.errorhandler(SQLAlchemyError)
@categorias_textos_padroes_bp.errorhandler(SQLAlchemyError)
def handle_database_error(error):
    db.session.rollback()
    textos_padroes_bp.logger.exception("Erro ao acessar o banco de dados", exc_info=error)
    return jsonify({"msg": "Falha ao acessar o banco de dados"}), 500


def _get_json_object():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def _required_text(data, field, label):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        return None, f"{label} é obrigatório"
    return value.strip(), None


def _get_category_id(data):
    category_id = data.get("categoriaId")
    if isinstance(category_id, bool) or not isinstance(category_id, int) or category_id <= 0:
        return None, "categoriaId deve ser um número inteiro positivo"
    category = db.session.get(TextoCategoria, category_id)
    if category is None:
        return None, "Categoria não encontrada"
    return category.id, None


@textos_padroes_bp.route("/", methods=["GET"])
@jwt_required()
def list_textos_padroes():
    textos = TextoPadrao.query.order_by(TextoPadrao.id.asc()).all()
    return jsonify([texto.to_dict() for texto in textos]), 200


@textos_padroes_bp.route("/", methods=["POST"])
@jwt_required()
def create_texto_padrao():
    data = _get_json_object()
    if data is None:
        return jsonify({"msg": "O corpo da requisição deve ser um objeto JSON"}), 400

    titulo, error = _required_text(data, "titulo", "Título")
    if error:
        return jsonify({"msg": error}), 400
    conteudo, error = _required_text(data, "conteudo", "Conteúdo")
    if error:
        return jsonify({"msg": error}), 400
    categoria_id, error = _get_category_id(data)
    if error:
        return jsonify({"msg": error}), 404 if error == "Categoria não encontrada" else 400

    texto = TextoPadrao(
        titulo=titulo,
        conteudo=conteudo,
        categoria_id=categoria_id,
    )
    db.session.add(texto)
    db.session.commit()
    return jsonify(texto.to_dict()), 201


@textos_padroes_bp.route("/<int:texto_id>", methods=["PUT"])
@jwt_required()
def update_texto_padrao(texto_id):
    texto = db.session.get(TextoPadrao, texto_id)
    if texto is None:
        return jsonify({"msg": "Texto padrão não encontrado"}), 404

    data = _get_json_object()
    if data is None:
        return jsonify({"msg": "O corpo da requisição deve ser um objeto JSON"}), 400

    if not any(field in data for field in ("titulo", "conteudo", "categoriaId")):
        return jsonify({"msg": "Informe ao menos um campo para atualizar"}), 400

    updated_values = {}
    if "titulo" in data:
        titulo, error = _required_text(data, "titulo", "Título")
        if error:
            return jsonify({"msg": error}), 400
        updated_values["titulo"] = titulo

    if "conteudo" in data:
        conteudo, error = _required_text(data, "conteudo", "Conteúdo")
        if error:
            return jsonify({"msg": error}), 400
        updated_values["conteudo"] = conteudo

    if "categoriaId" in data:
        categoria_id, error = _get_category_id(data)
        if error:
            return jsonify({"msg": error}), 404 if error == "Categoria não encontrada" else 400
        updated_values["categoria_id"] = categoria_id

    for field, value in updated_values.items():
        setattr(texto, field, value)
    db.session.commit()
    return jsonify(texto.to_dict()), 200


@textos_padroes_bp.route("/<int:texto_id>", methods=["DELETE"])
@jwt_required()
def delete_texto_padrao(texto_id):
    texto = db.session.get(TextoPadrao, texto_id)
    if texto is None:
        return jsonify({"msg": "Texto padrão não encontrado"}), 404

    db.session.delete(texto)
    db.session.commit()
    return jsonify({"msg": "Texto padrão excluído com sucesso"}), 200


@categorias_textos_padroes_bp.route("/", methods=["GET"])
@jwt_required()
def list_categorias_textos_padroes():
    categorias = TextoCategoria.query.order_by(TextoCategoria.id.asc()).all()
    return jsonify([categoria.to_dict() for categoria in categorias]), 200


@categorias_textos_padroes_bp.route("/", methods=["POST"])
@jwt_required()
def create_categoria_textos_padroes():
    data = _get_json_object()
    if data is None:
        return jsonify({"msg": "O corpo da requisição deve ser um objeto JSON"}), 400

    nome, error = _required_text(data, "nome", "Nome da categoria")
    if error:
        return jsonify({"msg": error}), 400

    categoria = TextoCategoria(nome=nome)
    db.session.add(categoria)
    db.session.commit()
    return jsonify(categoria.to_dict()), 201


@categorias_textos_padroes_bp.route("/<int:categoria_id>", methods=["DELETE"])
@jwt_required()
def delete_categoria_textos_padroes(categoria_id):
    categoria = db.session.get(TextoCategoria, categoria_id)
    if categoria is None:
        return jsonify({"msg": "Categoria não encontrada"}), 404

    db.session.delete(categoria)
    db.session.commit()
    return jsonify({"msg": "Categoria excluída com sucesso"}), 200
