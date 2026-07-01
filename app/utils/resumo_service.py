from __future__ import annotations

import json

from app.models import PromptConfig
from app.utils.gemini_service import GeminiService


DEFAULT_MODEL = "gemini-2.5-pro"

# Parte EDITÁVEL (vai pro banco)
_DEFAULT_EDITABLE_PROMPT = (
    "Você é um farmacêutico avaliador da assistência farmacêutica pública.\n"
    "Sua análise é estritamente técnica e preliminar.\n\n"
    "A presença de processos, protocolos ou solicitações vinculadas a RENAME, REESME ou CEAF não implica deferimento automático.\n"
    "Aferir CID/diagnóstico com medicamento e confronto com PCDT/Norma/Guia antes de qualquer sugestão conclusiva.\n\n"
    "Nunca emita decisão final institucional, não defina deferimento/indeferimento e marque necessita_revisao_humana=true."
)

# Parte FIXA (nunca muda, fica só no código)
_FIXED_JSON_SCHEMA = (
    "\n\nRetorne SOMENTE JSON válido com as chaves:\n"
    "- resumo_processo\n"
    "- evidencias_clinicas_do_processo\n"
    "- confronto_documentacao_suporte\n"
    "- insumo_parecer\n"
    "- fontes_consultadas\n\n"
    "Dentro de insumo_parecer, use EXATAMENTE estas chaves:\n"
    "- conclusao_tecnica_sugerida\n"
    "- fundamentos\n"
    "- alternativas_orientaveis\n"
    "- pendencias_documentais\n"
    "- necessita_revisao_humana\n"
    "- nivel_confianca\n\n"
    "Dentro de confronto_documentacao_suporte, use EXATAMENTE:\n"
    "- cid_validado\n"
    "- medicamento_contemplado_para_o_cid\n"
    "- observacoes\n\n"
    "Dentro de resumo_processo, use EXATAMENTE:\n"
    "- tipo_demanda\n"
    "- medicamento_solicitado\n"
    "- cid_informado\n"
    "- diagnostico_informado\n"
    "- objetivo_da_solicitacao\n"
)

class ResumoService:
    def __init__(self, gemini_service: GeminiService | None = None):
        self.gemini_service = gemini_service

    @staticmethod
    def get_default_prompt_text() -> str:
        """Retorna o prompt hardcoded para seed inicial no banco."""
        return f"{_DEFAULT_EDITABLE_PROMPT}\n\n{_FIXED_JSON_SCHEMA}"

    def get_active_prompt(self, key: str = "resumo_default") -> str:
        """Busca o prompt no banco. Se não existir, retorna o hardcoded."""
        try:
            config = PromptConfig.query.filter_by(key=key).first()
            if config and config.system_prompt:
                return config.system_prompt
        except Exception:
            pass
        return self.get_default_prompt_text()
    

    @staticmethod
    def get_fixed_schema() -> str:
        return _FIXED_JSON_SCHEMA
    
    @staticmethod
    def get_default_editable_prompt() -> str:
        return _DEFAULT_EDITABLE_PROMPT
    
    def get_active_editable_prompt(self, key: str = "resumo_default") -> str:
        try:
            config = PromptConfig.query.filter_by(key=key).first()
            if config and config.system_prompt:
                return config.system_prompt
        except Exception:
            pass
        return _DEFAULT_EDITABLE_PROMPT

    def build_prompt(self, process_text: str, support_context: str, include_minuta: bool = True, prompt_key: str = "resumo_default") -> str:
        editable_prompt = self.get_active_editable_prompt(prompt_key)
        minuta_instruction = (
            "Preencha insumo_parecer com conclusão farmacêutica preliminar, fundamentos técnicos e pendências."
            if include_minuta
            else "Mantenha insumo_parecer objetivo, sem minuta expandida de parecer."
        )
        
        final_prompt = (
            f"{editable_prompt}\n\n"
            f"{minuta_instruction}\n\n"
            f"{_FIXED_JSON_SCHEMA}\n\n"
            f"DOCUMENTOS DE SUPORTE:\n{support_context or 'N/A'}\n\n"
            f"TEXTO EXTRAÍDO DO PROCESSO:\n{process_text}"
        )
        
        # DEBUG: printa o prompt no terminal do Flask
        print("=" * 60)
        print("PROMPT ENVIADO AO GEMINI:")
        print(final_prompt)
        print("=" * 60)
        
        return final_prompt

    def generate_resumo(self, process_text: str, support_context: str, model: str = DEFAULT_MODEL, include_minuta: bool = True, prompt_key: str = "resumo_default") -> dict | None:
        prompt = self.build_prompt(process_text, support_context, include_minuta=include_minuta, prompt_key=prompt_key)
        response_text = (self.gemini_service or GeminiService()).generate_response(prompt, model=model)
        if not response_text:
            return None
        return self._normalize_payload(self._safe_parse_json(response_text))

    @staticmethod
    def _safe_parse_json(response_text: str) -> dict:
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {
            "resumo_processo": {"tipo_demanda": "indefinido"},
            "evidencias_clinicas_do_processo": [cleaned],
            "confronto_documentacao_suporte": {
                "cid_validado": False,
                "medicamento_contemplado_para_o_cid": "indeterminado",
                "observacoes": ["Resposta do modelo fora de JSON; revisão humana obrigatória."],
            },
            "insumo_parecer": {
                "conclusao_tecnica_sugerida": "Resposta parcial; consolidar tecnicamente.",
                "fundamentos": [],
                "alternativas_orientaveis": [],
                "pendencias_documentais": [],
                "necessita_revisao_humana": True,
                "nivel_confianca": "baixo",
            },
            "fontes_consultadas": [],
        }

    @staticmethod
    def _normalize_payload(payload: dict) -> dict:
        normalized = dict(payload) if isinstance(payload, dict) else {}
        normalized.setdefault("resumo_processo", {})
        normalized.setdefault("evidencias_clinicas_do_processo", [])
        normalized.setdefault("confronto_documentacao_suporte", {})
        normalized.setdefault("insumo_parecer", {})
        normalized.setdefault("fontes_consultadas", [])

        if not isinstance(normalized["resumo_processo"], dict):
            normalized["resumo_processo"] = {}
        if not isinstance(normalized["evidencias_clinicas_do_processo"], list):
            normalized["evidencias_clinicas_do_processo"] = []
        if not isinstance(normalized["confronto_documentacao_suporte"], dict):
            normalized["confronto_documentacao_suporte"] = {}
        if not isinstance(normalized["fontes_consultadas"], list):
            normalized["fontes_consultadas"] = []

        resumo_processo = normalized["resumo_processo"]
        resumo_processo.setdefault("tipo_demanda", "não informado")
        resumo_processo.setdefault("medicamento_solicitado", "não informado")
        resumo_processo.setdefault("cid_informado", "não informado")
        resumo_processo.setdefault("diagnostico_informado", "não informado")
        resumo_processo.setdefault("objetivo_da_solicitacao", "não informado")

        confronto = normalized["confronto_documentacao_suporte"]
        confronto.setdefault("cid_validado", False)
        confronto.setdefault("medicamento_contemplado_para_o_cid", "indeterminado")
        confronto.setdefault("observacoes", [])
        if not isinstance(confronto["observacoes"], list):
            confronto["observacoes"] = []

        insumo = normalized["insumo_parecer"]
        if not isinstance(insumo, dict):
            insumo = {}
        insumo.setdefault("conclusao_tecnica_sugerida", "Conclusão técnica não informada.")
        insumo.setdefault("fundamentos", [])
        insumo.setdefault("alternativas_orientaveis", [])
        insumo.setdefault("pendencias_documentais", [])
        insumo.setdefault("necessita_revisao_humana", True)
        insumo.setdefault("nivel_confianca", "não informado")
        for key in ("fundamentos", "alternativas_orientaveis", "pendencias_documentais"):
            if not isinstance(insumo[key], list):
                insumo[key] = []
        normalized["insumo_parecer"] = insumo
        return normalized