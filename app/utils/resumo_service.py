from __future__ import annotations

import json

from app.utils.gemini_service import GeminiService


DEFAULT_MODEL = "gemini-2.5-pro"


class ResumoService:
    def __init__(self, gemini_service: GeminiService | None = None):
        self.gemini_service = gemini_service or GeminiService()

    def build_prompt(self, process_text: str, support_context: str, include_minuta: bool = True) -> str:
        minuta_instruction = (
            "Preencha insumo_parecer com conclusão farmacêutica preliminar, fundamentos técnicos e pendências."
            if include_minuta
            else "Mantenha insumo_parecer objetivo, sem minuta expandida de parecer."
        )
        return (
            "Você é um farmacêutico avaliador da assistência farmacêutica pública. "
            "Sua análise é estritamente técnica e preliminar. "
            "A presença de processos, protocolos ou solicitações vinculadas a RENAME, REESME ou CEAF não implica deferimento automático. "
            "Aferir CID/diagnóstico com medicamento e confronto com PCDT/Norma/Guia antes de qualquer sugestão conclusiva. "
            "Retorne SOMENTE JSON válido com as chaves: resumo_processo, "
            "evidencias_clinicas_do_processo, confronto_documentacao_suporte, "
            "insumo_parecer, fontes_consultadas. "
            "Nunca emita decisão final institucional, não defina deferimento/indeferimento e marque necessita_revisao_humana=true. "
            f"{minuta_instruction}\n\n"
            f"DOCUMENTOS DE SUPORTE:\n{support_context or 'N/A'}\n\n"
            f"TEXTO EXTRAÍDO DO PROCESSO:\n{process_text}"
        )

    def generate_resumo(self, process_text: str, support_context: str, model: str = DEFAULT_MODEL, include_minuta: bool = True) -> dict | None:
        prompt = self.build_prompt(process_text, support_context, include_minuta=include_minuta)
        response_text = self.gemini_service.generate_response(prompt, model=model)
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

        insumo = normalized["insumo_parecer"]
        if not isinstance(insumo, dict):
            insumo = {}
        insumo.setdefault("necessita_revisao_humana", True)
        normalized["insumo_parecer"] = insumo
        return normalized
