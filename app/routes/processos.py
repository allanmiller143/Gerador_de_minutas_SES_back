from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, get_jwt_identity
from app.models import db, ProcessoSEI
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app.utils.decorators import role_required
import traceback
from app.utils.gcs_utils import upload_file_to_gcs
import queue
import threading
import base64
import io
import uuid

from app.utils import rpasei

processos_bp = Blueprint("processos", __name__, url_prefix="/processos")

# Queue-based sequential background analysis worker
analysis_queue = queue.Queue()
worker_started = False
worker_lock = threading.Lock()

def start_worker_thread():
    global worker_started
    with worker_lock:
        if not worker_started:
            t = threading.Thread(target=_worker_loop, daemon=True)
            t.start()
            worker_started = True
            print("Background analysis worker thread spawned.")

def _worker_loop():
    while True:
        item = analysis_queue.get()
        try:
            app, processo_id = item
            with app.app_context():
                _process_queued_analysis(processo_id)
        except Exception as e:
            print(f"Error processing queued analysis task: {e}")
            traceback.print_exc()
        finally:
            analysis_queue.task_done()

def _execute_analise_processo(processo: ProcessoSEI) -> None:
    import os
    from app.utils.gemini_service import GeminiService

    file_uri = None
    mime_type = "application/pdf"
    
    if processo.arquivoPdf:
        bucket_name = os.getenv("GCS_BUCKET_NAME")
        if processo.arquivoPdf.startswith("gs://"):
            file_uri = processo.arquivoPdf
        elif bucket_name:
            file_uri = f"gs://{bucket_name}/{processo.arquivoPdf}"
            
    gemini_service = GeminiService()
    result = gemini_service.generate_response_with_file(
        file_uri=file_uri,
        mime_type=mime_type
    )
        
    if not result or not result.get("text"):
        raise ValueError("O Gemini retornou uma resposta vazia.")
        
    processo.iaSugestao = result["text"]
    processo.iaConfidence = result["confidence"]
    processo.jurisprudenciasSugeridas = result["files"]
    processo.status = "Pré-análise"

def _process_queued_analysis(processo_id: int):
    import time
    from app.models import db, ProcessoSEI
    from app.routes.mock_data import _persist_generated_resumo

    processo = db.session.get(ProcessoSEI, processo_id)
    if not processo:
        print(f"Async worker: Process {processo_id} not found in database.")
        return

    print(f"Async worker: Starting analysis sequence for process {processo_id}.")
    start_time = time.time()

    try:
        # Phase 1: Geração do Resumo (technical summary version in database)
        sei_dict = processo.to_dict()
        _persist_generated_resumo(sei_dict, "sistema", "automático")
        print(f"Async worker: Resumo generated and versioned for process {processo_id}.")

        # Phase 2: Analisar Processo (GenAI analysis on ProcessoSEI)
        _execute_analise_processo(processo)

        # Finalizing: update status to Concluído and save duration
        duration = int(round(time.time() - start_time))
        processo.tempo_analise = duration
        processo.status_processamento = "Concluído"
        db.session.commit()
        print(f"Async worker: Gemini analysis completed in {duration}s and status marked Concluído for process {processo_id}.")

    except Exception as e:
        db.session.rollback()
        print(f"Async worker: Exception occurred during background analysis for process {processo_id}: {e}")
        traceback.print_exc()
        try:
            processo = db.session.get(ProcessoSEI, processo_id)
            if processo:
                processo.status_processamento = "Falhou"
                db.session.commit()
                print(f"Async worker: Process {processo_id} marked as Falhou in database.")
        except Exception as inner_ex:
            db.session.rollback()
            print(f"Async worker: Failed to write failure status to DB for process {processo_id}: {inner_ex}")


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
        status_processamento="Processando",
        arquivoPdf=full_path,
        iaConfidence=0.0,
    )
    
    try:
        db.session.add(novo_processo)
        db.session.commit()
        from flask import current_app
        analysis_queue.put((current_app._get_current_object(), novo_processo.id))
        print(f"Queued process {novo_processo.id} for background analysis.")
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
    # 1. Obter o processo do banco de dados
    processo = ProcessoSEI.query.get_or_404(processo_id)
    
    # 2. Atualizar status de processamento para indicar execução em andamento
    processo.status_processamento = "Processando"
    
    try:
        db.session.commit()
        # 3. Enfileirar a tarefa na fila de análise sequencial
        from flask import current_app
        analysis_queue.put((current_app._get_current_object(), processo.id))
        print(f"Queued process {processo.id} for manual analysis via Gemini IA.")
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"error": f"Erro ao enfileirar o processo para análise: {str(e)}"}), 500
        
    # 4. Retornar status 202 com o payload informando o status de processamento
    return jsonify({
        "message": "Análise enfileirada com sucesso",
        "processo": processo.to_dict()
    }), 202


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

"""
#Rotina agendada.
Busca processo no SEI, verifica no banco, faz upload pro GCS e joga na fila de análise.
Devido ao RPA tem a limitação de precisar receber um número do processo.
"""
def sincronizar_processos_sei_rotina(numero_sei: str, app_context): 
    with app_context.app_context():
        #Verifica no banco se o processo já está lá.
        processo_existente = ProcessoSEI.query.filter_by(numero=numero_sei).first()
        if processo_existente:
            return {f"error: [{numero_sei}] processo já existe no banco."}

        #Se não existir, utiliza o RPA do SEI para baixar novo documento.
        try:
            #Chama o RPA do SEI.
            resultado_rpa = rpasei.run(numero_sei)
            
            #Erro no RPA.
            if resultado_rpa.get("status") == "erro":
                print(f"[{numero_sei}] erro no RPA: {resultado_rpa.get('mensagem')}")
                return resultado_rpa

            #Documento não encontrado.
            documentos = resultado_rpa.get("documentos", [])
            if not documentos:
                # Retorno corrigido para dicionário
                return {f"error: [{numero_sei}] nenhum documento anexado."}

            #Pega o documento.
            doc_principal = documentos[0] 
            
            #Converte para b64 e cria um stream de arquivo exigido pelo GCS.
            pdf_bytes = base64.b64decode(doc_principal["base64"])
            file_stream = io.BytesIO(pdf_bytes) 
            
            nome_arquivo_gcs = f"sei_import/{uuid.uuid4()}_{doc_principal['nome']}"
            
            #Pega o caminho do PDF no GCS.
            url_gcs = upload_file_to_gcs(file_stream, nome_arquivo_gcs, content_type="application/pdf")

            #Salva no banco de dados.
            novo_processo = ProcessoSEI(
                numero=numero_sei,
                assunto="Importado via Rotina SEI", #Temporário para checagem.
                status="Pré-análise",
                prioridade="Média",
                arquivoPdf=url_gcs 
            )
            
            db.session.add(novo_processo)
            db.session.commit()
            
            #Envia para a fila de análise da IA.
            analysis_queue.put((app_context, novo_processo.id))
            
            return {f"[{numero_sei}] salvo e enviado para análise com sucesso."}
            
        except Exception as e:
            return {f"[{numero_sei}] error: {str(e)}"}