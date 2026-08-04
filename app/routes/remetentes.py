from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from app.models import db, Remetente

remetentes_bp = Blueprint("remetentes", __name__, url_prefix="/remetentes")

@remetentes_bp.route("/", methods=["GET"])
@jwt_required()
def list_remetentes():
    remetentes = Remetente.query.order_by(Remetente.id.asc()).all()
    return jsonify([r.to_dict() for r in remetentes]), 200

@remetentes_bp.route("/<int:remetente_id>", methods=["GET"])
@jwt_required()
def get_remetente(remetente_id):
    remetente = Remetente.query.get_or_404(remetente_id)
    return jsonify(remetente.to_dict()), 200

@remetentes_bp.route("/", methods=["POST"])
@jwt_required()
def create_remetente():
    data = request.get_json() or {}
    prefixo = data.get("prefixo", "").strip()
    nome_completo = data.get("nome_completo", "").strip()
    sigla = data.get("sigla", "").strip()
    cor = data.get("cor", "").strip()

    if not prefixo or not nome_completo or not sigla or not cor:
        return jsonify({"msg": "Todos os campos (prefixo, nome_completo, sigla, cor) são obrigatórios"}), 400

    novo_remetente = Remetente(
        prefixo=prefixo,
        nome_completo=nome_completo,
        sigla=sigla,
        cor=cor
    )
    db.session.add(novo_remetente)
    db.session.commit()

    return jsonify(novo_remetente.to_dict()), 201

@remetentes_bp.route("/<int:remetente_id>", methods=["PUT"])
@jwt_required()
def update_remetente(remetente_id):
    remetente = Remetente.query.get_or_404(remetente_id)
    data = request.get_json() or {}

    if "prefixo" in data:
        remetente.prefixo = data["prefixo"].strip()
    if "nome_completo" in data:
        remetente.nome_completo = data["nome_completo"].strip()
    if "sigla" in data:
        remetente.sigla = data["sigla"].strip()
    if "cor" in data:
        remetente.cor = data["cor"].strip()

    if not remetente.prefixo or not remetente.nome_completo or not remetente.sigla or not remetente.cor:
        return jsonify({"msg": "Campos não podem ficar vazios"}), 400

    db.session.commit()
    return jsonify(remetente.to_dict()), 200

@remetentes_bp.route("/<int:remetente_id>", methods=["DELETE"])
@jwt_required()
def delete_remetente(remetente_id):
    remetente = Remetente.query.get_or_404(remetente_id)
    db.session.delete(remetente)
    db.session.commit()
    return jsonify({"msg": "Remetente excluído com sucesso"}), 200
