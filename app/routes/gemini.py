from flask import Blueprint, request, jsonify
from app.utils.gemini_service import GeminiService
from flask_jwt_extended import jwt_required

gemini_bp = Blueprint('gemini', __name__, url_prefix='/api/gemini')
gemini_service = GeminiService()

@gemini_bp.route('/generate', methods=['POST'])
# @jwt_required() # Opcional: descomente se quiser que a rota seja protegida por JWT
def generate_content():
    data = request.get_json()
    
    if not data or 'prompt' not in data:
        return jsonify({"error": "O campo 'prompt' é obrigatório."}), 400
    
    prompt = data.get('prompt')
    model = data.get('model', 'gemini-2.5-pro') # Default para o modelo usado no teste
    
    response_text = gemini_service.generate_response(prompt, model=model)
    
    if response_text:
        return jsonify({"response": response_text}), 200
    else:
        return jsonify({"error": "Falha ao gerar conteúdo com o Gemini."}), 500