from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models import db, ProcessoSEI
from sqlalchemy import func

processos_bp = Blueprint('processos', __name__, url_prefix='/api')


# ---------------------------------------------------------------------------
# Processos SEI
# ---------------------------------------------------------------------------

@processos_bp.route('/processos', methods=['GET'])
@jwt_required()
def list_processos():
    # Retorna todos os processos SEI
    processos = ProcessoSEI.query.order_by(ProcessoSEI.data_recebimento.desc()).all()
    return jsonify([p.to_dict() for p in processos]), 200


@processos_bp.route('/processos/<int:processo_id>', methods=['GET'])
@jwt_required()
def get_processo(processo_id):
    # Retorna um processo específico pelo ID
    processo = db.session.get(ProcessoSEI, processo_id)
    if not processo:
        return jsonify({'msg': 'Processo não encontrado'}), 404
    return jsonify(processo.to_dict()), 200


@processos_bp.route('/processos/<int:processo_id>/status', methods=['PATCH'])
@jwt_required()
def update_status(processo_id):
    # Atualiza o status e/ou prioridade de um processo
    processo = db.session.get(ProcessoSEI, processo_id)
    if not processo:
        return jsonify({'msg': 'Processo não encontrado'}), 404

    data = request.get_json()
    if 'status' in data:
        processo.status = data['status']
    if 'prioridade' in data:
        processo.prioridade = data['prioridade']
    if 'foi_alterado' in data:
        processo.foi_alterado = data['foi_alterado']
    if 'prioridade_original' in data:
        processo.prioridade_original = data['prioridade_original']

    db.session.commit()
    return jsonify(processo.to_dict()), 200


# ---------------------------------------------------------------------------
# Dashboard — métricas
# ---------------------------------------------------------------------------

@processos_bp.route('/dashboard/metrics', methods=['GET'])
@jwt_required()
def dashboard_metrics():
    # Retorna os valores por status para os cards do Dashboard
    counts = db.session.query(
        ProcessoSEI.status,
        func.count(ProcessoSEI.id)
    ).group_by(ProcessoSEI.status).all()

    result = {status: count for status, count in counts}

    return jsonify({
        'preAnalisadosIA': result.get('Pré-análise', 0),
        'emRevisaoHumana': result.get('Em revisão', 0),
        'concluidos': result.get('Concluído', 0),
        'total': sum(result.values()),
    }), 200