import os
import uuid
import traceback
from flask import Blueprint, jsonify, request, make_response
from flask_jwt_extended import jwt_required, get_jwt_identity
from google.cloud import storage

from app.models import db, KnowledgeDocument, User
from app.utils.decorators import role_required

knowledge_bp = Blueprint("knowledge_base", __name__, url_prefix="/knowledge-base")

def get_current_user_name():
    try:
        user_id = get_jwt_identity()
        user = db.session.get(User, int(user_id)) if user_id else None
        return user.username if user else "sistema"
    except Exception:
        return "sistema"


def sync_documents_from_bucket():
    """Sincroniza registros do banco com os PDFs já existentes no bucket da base de conhecimento."""
    project_id = os.getenv("GCS_PROJECT_ID")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    knowledge_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE", "base_conhecimento")

    if not bucket_name:
        raise ValueError("GCS_BUCKET_NAME não configurado no servidor.")

    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)

    inserted = 0
    updated = 0
    seen_paths = set()

    for blob in bucket.list_blobs(prefix=knowledge_dir):
        if not blob.name or blob.name.endswith("/"):
            continue
        if not blob.name.lower().endswith(".pdf"):
            continue

        seen_paths.add(blob.name)
        filename = os.path.basename(blob.name)

        existing = KnowledgeDocument.query.filter_by(file_path=blob.name).first()
        if existing:
            if (
                existing.filename != filename
                or existing.file_size != blob.size
                or existing.mime_type != (blob.content_type or "application/pdf")
            ):
                existing.filename = filename
                existing.file_size = blob.size
                existing.mime_type = blob.content_type or "application/pdf"
                existing.is_active = True
                updated += 1
            continue

        doc = KnowledgeDocument(
            titulo=(filename.rsplit(".", 1)[0] if "." in filename else filename),
            categoria="Protocolo Clínico",
            descricao="Sincronizado automaticamente do bucket da base de conhecimento.",
            filename=filename,
            file_path=blob.name,
            file_size=blob.size,
            mime_type=blob.content_type or "application/pdf",
            created_by="sistema",
        )
        db.session.add(doc)
        inserted += 1

    # Marca como inativos os registros locais que não existem mais no bucket.
    stale_docs = KnowledgeDocument.query.filter(KnowledgeDocument.file_path.notin_(list(seen_paths))).all() if seen_paths else []
    for doc in stale_docs:
        doc.is_active = False

    db.session.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "active_in_bucket": len(seen_paths),
        "total_synced": inserted + updated,
    }


@knowledge_bp.route("", methods=["GET"])
@jwt_required()
def list_documents():
    """Lista todos os documentos ativos da base de conhecimento com busca e filtro."""
    try:
        try:
            sync_documents_from_bucket()
        except Exception as sync_error:
            print(f"[knowledge_base] Falha ao sincronizar bucket -> banco: {sync_error}")

        categoria = request.args.get("categoria")
        search = request.args.get("search")

        query = KnowledgeDocument.query.filter_by(is_active=True)

        if categoria and categoria != "Todos":
            query = query.filter_by(categoria=categoria)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (KnowledgeDocument.titulo.ilike(search_pattern)) |
                (KnowledgeDocument.descricao.ilike(search_pattern)) |
                (KnowledgeDocument.filename.ilike(search_pattern))
            )

        docs = query.order_by(KnowledgeDocument.created_at.desc()).all()
        return jsonify([d.to_dict() for d in docs]), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao listar documentos: {str(e)}"}), 500


@knowledge_bp.route("/sync", methods=["POST"])
@jwt_required()
def sync_documents_route():
    """Sincroniza os registros do banco com os PDFs já existentes no bucket da base de conhecimento."""
    try:
        result = sync_documents_from_bucket()
        return jsonify({
            "message": "Sincronização concluída com sucesso.",
            "result": result,
        }), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao sincronizar documentos: {str(e)}"}), 500


@knowledge_bp.route("/upload", methods=["POST"])
@jwt_required()
def upload_document():
    """Faz o upload de um novo documento PDF para o GCS e registra na tabela."""
    if "file" not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nenhum arquivo selecionado."}), 400

    titulo = request.form.get("titulo")
    categoria = request.form.get("categoria", "Protocolo Clínico")
    descricao = request.form.get("descricao", "")

    if not titulo:
        return jsonify({"error": "O campo 'titulo' é obrigatório."}), 400

    project_id = os.getenv("GCS_PROJECT_ID")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    knowledge_dir = os.getenv("GCS_BUCKET_KNOWLEDGE_BASE", "base_conhecimento")

    if not bucket_name:
        return jsonify({"error": "GCS_BUCKET_NAME não configurado no servidor."}), 500

    try:
        # Gera nome único no Cloud Storage
        unique_filename = f"{uuid.uuid4()}_{file.filename}"
        gcs_path = f"{knowledge_dir}/{unique_filename}"

        # Upload para o Google Cloud Storage
        storage_client = storage.Client(project=project_id)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(gcs_path)

        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        blob.upload_from_file(file.stream, content_type=file.content_type or "application/pdf")

        # Salva o registro no Banco de Dados
        user_name = get_current_user_name()
        doc = KnowledgeDocument(
            titulo=titulo,
            categoria=categoria,
            descricao=descricao,
            filename=file.filename,
            file_path=gcs_path,
            file_size=file_size,
            mime_type=file.content_type or "application/pdf",
            created_by=user_name
        )

        db.session.add(doc)
        db.session.commit()

        return jsonify({
            "message": "Documento cadastrado com sucesso!",
            "documento": doc.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({"error": f"Falha no upload do documento: {str(e)}"}), 500

@knowledge_bp.route("/<int:doc_id>", methods=["PUT"])
@jwt_required()
def update_document(doc_id):
    """Edita os metadados do documento (Título, Categoria, Descrição)."""
    doc = db.session.get(KnowledgeDocument, doc_id)
    if not doc or not doc.is_active:
        return jsonify({"error": "Documento não encontrado."}), 404

    data = request.get_json(silent=True) or {}

    if "titulo" in data and data["titulo"].strip():
        doc.titulo = data["titulo"].strip()
    if "categoria" in data and data["categoria"].strip():
        doc.categoria = data["categoria"].strip()
    if "descricao" in data:
        doc.descricao = data["descricao"].strip()

    try:
        db.session.commit()
        return jsonify(doc.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Erro ao atualizar documento: {str(e)}"}), 500

@knowledge_bp.route("/<int:doc_id>", methods=["DELETE"])
@jwt_required()
def delete_document(doc_id):
    """Realiza a exclusão lógica (soft delete) do documento."""
    doc = db.session.get(KnowledgeDocument, doc_id)
    if not doc or not doc.is_active:
        return jsonify({"error": "Documento não encontrado."}), 404

    doc.is_active = False

    try:
        db.session.commit()
        return jsonify({"message": "Documento removido da Base de Conhecimento com sucesso."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Erro ao remover documento: {str(e)}"}), 500

@knowledge_bp.route("/<int:doc_id>/download", methods=["GET"])
@jwt_required()
def download_document(doc_id):
    """Realiza o download direto do PDF a partir do GCS."""
    doc = db.session.get(KnowledgeDocument, doc_id)
    if not doc or not doc.is_active:
        return jsonify({"error": "Documento não encontrado."}), 404

    project_id = os.getenv("GCS_PROJECT_ID")
    bucket_name = os.getenv("GCS_BUCKET_NAME")

    if not bucket_name:
        return jsonify({"error": "Configuração GCS_BUCKET_NAME não definida no servidor."}), 500

    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(doc.file_path)

        if not blob.exists():
            return jsonify({"error": "Arquivo não encontrado no Cloud Storage."}), 404

        file_data = blob.download_as_bytes()

        response = make_response(file_data)
        response.headers.set('Content-Type', doc.mime_type or 'application/pdf')
        response.headers.set('Content-Disposition', 'attachment', filename=doc.filename)
        return response
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Erro ao baixar arquivo do GCS: {str(e)}"}), 500