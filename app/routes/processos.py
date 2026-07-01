from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models import db, ProcessoSEI
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app.utils.decorators import role_required
import traceback
from app.utils.gcs_utils import upload_file_to_gcs

processos_bp = Blueprint("processos", __name__, url_prefix="/processos")


# ---------------------------------------------------------------------------
# Processos SEI - Endpoints
# ---------------------------------------------------------------------------

@processos_bp.route("/", methods=["GET"])
@jwt_required()
@role_required(["analyst", "admin"])
def list_processos():
    # Retorna todos os processos SEI do banco de dados
    processos = ProcessoSEI.query.order_by(ProcessoSEI.dataRecebimento.desc()).all()
    # Retorna como dicionário contendo "processos" para compatibilidade com o frontend
    output = [p.to_dict() for p in processos]
    return jsonify({"processos": output}), 200


@processos_bp.route('/<int:processo_id>', methods=['GET'])
@jwt_required()
@role_required(["analyst", "admin"])
def get_processo(processo_id):
    # Retorna um processo específico pelo ID
    processo = db.session.get(ProcessoSEI, processo_id)
    if not processo:
        return jsonify({'msg': 'Processo não encontrado'}), 404
    return jsonify(processo.to_dict()), 200


@processos_bp.route('/<int:processo_id>/status', methods=['PATCH'])
@jwt_required()
@role_required(["analyst", "admin"])
def update_status(processo_id):
    # Atualiza o status e/ou prioridade de um processo
    processo = db.session.get(ProcessoSEI, processo_id)
    if not processo:
        return jsonify({'msg': 'Processo não encontrado'}), 404

    data = request.get_json(silent=True) or {}
    if 'status' in data:
        processo.status = data['status']
    if 'prioridade' in data:
        if processo.prioridade_original is None and data['prioridade'] != processo.prioridade:
            processo.prioridade_original = processo.prioridade
        processo.prioridade = data['prioridade']
    if 'foi_alterado' in data:
        processo.foi_alterado = data['foi_alterado']
    if 'prioridade_original' in data:
        processo.prioridade_original = data['prioridade_original']
    if 'minuta' in data:
        processo.minuta = data['minuta']

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


# ---------------------------------------------------------------------------
# Upload e IA (Gemini)
# ---------------------------------------------------------------------------

@processos_bp.route("/upload", methods=["POST"])
@jwt_required()
@role_required(["analyst", "admin"])
def upload_processo():
    if "file" not in request.files:
        return jsonify({"error": "No file part in the request"}), 400
    
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
    
    numero = request.form.get("numero")
    assunto = request.form.get("assunto")
    prioridade = request.form.get("prioridade")

    if not all([numero, assunto, prioridade]):
        return jsonify({"error": "Missing required fields (numero, assunto, prioridade)"}), 400

    # Handle upload to GCS
    try:
        full_path = upload_file_to_gcs(file.stream, file.filename, file.content_type)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    novo_processo = ProcessoSEI(
        numero=numero,
        assunto=assunto,
        prioridade=prioridade,
        status="Pré-análise",
        arquivoPdf=full_path,
        iaConfidence=0.0,
    )
    
    try:
        db.session.add(novo_processo)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "Já existe um processo com este Número SEI. Por favor, utilize um número diferente."}), 409
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"error": f"Failed to save to database: {str(e)}"}), 500

    return jsonify({
        "message": "Upload successful",
        "processo": novo_processo.to_dict()
    }), 201


@processos_bp.route("/<int:processo_id>/analisar", methods=["POST"])
@jwt_required()
@role_required(["analyst", "admin"])
def analisar_processo(processo_id):
    import os
    
    # 1. Obter o processo do banco de dados
    processo = ProcessoSEI.query.get_or_404(processo_id)
    
    # 2. Obter o file_uri e mime_type do processo.arquivoPdf
    file_uri = None
    mime_type = "application/pdf"
    
    if processo.arquivoPdf:
        bucket_name = os.getenv("GCS_BUCKET_NAME")
        if processo.arquivoPdf.startswith("gs://"):
            file_uri = processo.arquivoPdf
        elif bucket_name:
            file_uri = f"gs://{bucket_name}/{processo.arquivoPdf}"
            
    # 3. Invocar o método generate_response_with_file em gemini_service.py
    from app.utils.gemini_service import GeminiService
    gemini_service = GeminiService()
    
    try:
        result = gemini_service.generate_response_with_file(
            file_uri=file_uri,
            mime_type=mime_type
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao chamar o Gemini: {str(e)}"}), 500
        
    if not result or not result.get("text"):
        return jsonify({"error": "O Gemini retornou uma resposta vazia."}), 500
        
    # 4. Atualizar o registro para ProcessoSEI com as informações iaConfidence, iaSugestao e jurisprudenciasSugeridas
    processo.iaSugestao = result["text"]
    processo.iaConfidence = result["confidence"]
    processo.jurisprudenciasSugeridas = result["files"]
    processo.status = "Pré-análise" # Atualiza o status do processo
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"error": f"Erro ao salvar atualizações do processo no banco: {str(e)}"}), 500
        
    # 5. Retornar o processo atualizado
    return jsonify({
        "message": "Processo analisado com sucesso por IA",
        "processo": processo.to_dict()
    }), 200


@processos_bp.route("/<int:processo_id>/download", methods=["GET"])
@jwt_required()
@role_required(["analyst", "admin"])
def download_processo(processo_id):
    from flask import send_file
    import io
    import re
    import os
    from google.cloud import storage
    
    processo = ProcessoSEI.query.get_or_404(processo_id)
    
    if not processo.arquivoPdf:
        return jsonify({"error": "Nenhum arquivo PDF associado a este processo."}), 400
        
    project_id = os.getenv("GCS_PROJECT_ID")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    
    if not bucket_name:
        return jsonify({"error": "Configuração GCS_BUCKET_NAME não definida no servidor."}), 500
        
    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(processo.arquivoPdf)
        
        if not blob.exists():
            return jsonify({"error": "O arquivo PDF não existe no Cloud Storage."}), 404
            
        file_data = blob.download_as_bytes()
        
        # Extrair nome original limpo (removendo prefixo UUID se houver)
        file_basename = os.path.basename(processo.arquivoPdf)
        match = re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_(.+)$", file_basename, re.IGNORECASE)
        original_filename = match.group(1) if match else file_basename
        
        return send_file(
            io.BytesIO(file_data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=original_filename
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao baixar arquivo do GCS: {str(e)}"}), 500


@processos_bp.route("/knowledge-base/download", methods=["GET"])
@jwt_required()
@role_required(["analyst", "admin"])
def download_knowledge_base_file():
    from flask import send_file
    import io
    import os
    from google.cloud import storage
    
    file_path = request.args.get("file")
    if not file_path:
        return jsonify({"error": "Parâmetro 'file' é obrigatório."}), 400
        
    project_id = os.getenv("GCS_PROJECT_ID")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    knowledge_base_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE", "base_conhecimento")
    
    if not bucket_name:
        return jsonify({"error": "Configuração GCS_BUCKET_NAME não definida no servidor."}), 500
        
    # Garantir que o caminho do arquivo comece com o prefixo da base de conhecimento para segurança
    if not file_path.startswith(knowledge_base_dir):
        if ".." in file_path or file_path.startswith("/") or file_path.startswith("."):
            return jsonify({"error": "Acesso não autorizado ao caminho especificado."}), 403
        file_path = f"{knowledge_base_dir}/{file_path}"
        
    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(file_path)
        
        if not blob.exists():
            return jsonify({"error": f"O arquivo '{file_path}' não existe na base de conhecimento."}), 404
            
        file_data = blob.download_as_bytes()
        
        # Extrair o nome limpo para download
        original_filename = os.path.basename(file_path)
        
        return send_file(
            io.BytesIO(file_data),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=original_filename
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao baixar arquivo da base de conhecimento do GCS: {str(e)}"}), 500
