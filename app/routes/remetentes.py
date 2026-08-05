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

    existente_prefixo = Remetente.query.filter(
        db.func.lower(Remetente.prefixo) == db.func.lower(prefixo)
    ).first()
    if existente_prefixo:
        return jsonify({"msg": f"Já existe um remetente com o prefixo '{prefixo}'"}), 400

    existente_sigla = Remetente.query.filter(
        db.func.lower(Remetente.sigla) == db.func.lower(sigla)
    ).first()
    if existente_sigla:
        return jsonify({"msg": f"Já existe um remetente com a sigla '{sigla}'"}), 400

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

    novo_prefixo = data.get("prefixo", remetente.prefixo).strip() if "prefixo" in data else remetente.prefixo
    novo_nome_completo = data.get("nome_completo", remetente.nome_completo).strip() if "nome_completo" in data else remetente.nome_completo
    nova_sigla = data.get("sigla", remetente.sigla).strip() if "sigla" in data else remetente.sigla
    nova_cor = data.get("cor", remetente.cor).strip() if "cor" in data else remetente.cor

    if not novo_prefixo or not novo_nome_completo or not nova_sigla or not nova_cor:
        return jsonify({"msg": "Campos não podem ficar vazios"}), 400

    existente_prefixo = Remetente.query.filter(
        db.func.lower(Remetente.prefixo) == db.func.lower(novo_prefixo),
        Remetente.id != remetente_id
    ).first()
    if existente_prefixo:
        return jsonify({"msg": f"Já existe outro remetente com o prefixo '{novo_prefixo}'"}), 400

    existente_sigla = Remetente.query.filter(
        db.func.lower(Remetente.sigla) == db.func.lower(nova_sigla),
        Remetente.id != remetente_id
    ).first()
    if existente_sigla:
        return jsonify({"msg": f"Já existe outro remetente com a sigla '{nova_sigla}'"}), 400

    remetente.prefixo = novo_prefixo
    remetente.nome_completo = novo_nome_completo
    remetente.sigla = nova_sigla
    remetente.cor = nova_cor

    db.session.commit()
    return jsonify(remetente.to_dict()), 200

@remetentes_bp.route("/<int:remetente_id>", methods=["DELETE"])
@jwt_required()
def delete_remetente(remetente_id):
    remetente = Remetente.query.get_or_404(remetente_id)
    db.session.delete(remetente)
    db.session.commit()
    return jsonify({"msg": "Remetente excluído com sucesso"}), 200
