import pytest
from app.utils.adk_resumo_service import AdkResumoService


def test_normalize_payload_handles_unpadronized_presentation():
    raw_payload = {
        "resumo_processo": {
            "medicamento_solicitado": "Adalimumabe 80mg injetável",
            "medicamentos_detalhados": [
                {
                    "medicamento": "Adalimumabe",
                    "apresentacao": "solução injetável 80mg",
                    "posologia": "1 aplicação a cada 14 dias",
                    "origem_documental": "Laudo LME fl. 4",
                    "apresentacao_padronizada_sus": False,
                    "status_dispensacao": "Componente Especializado - Aprovado",  # Model inadvertently approved
                    "justificativa_item": "",
                }
            ],
            "cid_informado": "K50.0",
        },
        "confronto_documentacao_suporte": {
            "cid_validado": True,
            "medicamento_contemplado_para_o_cid": "sim",
            "observacoes": ["Doença de Crohn moderada a grave."],
        },
    }

    normalized = AdkResumoService._normalize_payload(raw_payload)

    confronto = normalized["confronto_documentacao_suporte"]
    resumo = normalized["resumo_processo"]

    # Deve marcar apresentacao_padronizada como False
    assert confronto["apresentacao_padronizada"] is False

    # Deve sincronizar itens_avaliados
    itens = confronto["itens_avaliados"]
    assert len(itens) == 1
    item = itens[0]
    assert item["medicamento"] == "Adalimumabe"
    assert item["apresentacao"] == "solução injetável 80mg"
    assert item["posologia"] == "1 aplicação a cada 14 dias"
    assert item["apresentacao_padronizada_sus"] is False
    assert item["status_dispensacao"] == "Não Dispensado"
    assert "A apresentação e dosagem solicitadas não estão padronizadas para fornecimento." in item["justificativa_item"]

    # Deve adicionar frase padrao nas observacoes do confronto
    assert any("A apresentação e dosagem solicitadas não estão padronizadas para fornecimento." in obs for obs in confronto["observacoes"])

    # Deve manter resumo_processo["medicamentos_detalhados"] consistente
    assert len(resumo["medicamentos_detalhados"]) == 1
    assert resumo["medicamentos_detalhados"][0]["status_dispensacao"] == "Não Dispensado"


def test_normalize_payload_handles_all_padronized_presentations():
    raw_payload = {
        "resumo_processo": {
            "medicamentos_detalhados": [
                {
                    "medicamento": "Adalimumabe",
                    "apresentacao": "solução injetável 40mg/0,8mL",
                    "posologia": "1 aplicação a cada 14 dias",
                    "origem_documental": "Receituário fl. 2",
                    "apresentacao_padronizada_sus": True,
                    "status_dispensacao": "Componente Especializado - Aprovado",
                    "justificativa_item": "Apresentação padronizada no SUS para Doença de Crohn.",
                }
            ]
        },
        "confronto_documentacao_suporte": {
            "cid_validado": True,
            "medicamento_contemplado_para_o_cid": "sim",
            "observacoes": ["Protocolo atendido."],
        },
    }

    normalized = AdkResumoService._normalize_payload(raw_payload)
    confronto = normalized["confronto_documentacao_suporte"]

    assert confronto["apresentacao_padronizada"] is True
    assert len(confronto["itens_avaliados"]) == 1
    assert confronto["itens_avaliados"][0]["apresentacao_padronizada_sus"] is True
    assert confronto["itens_avaliados"][0]["status_dispensacao"] == "Componente Especializado - Aprovado"


def test_normalize_payload_handles_mixed_presentations():
    raw_payload = {
        "confronto_documentacao_suporte": {
            "itens_avaliados": [
                {
                    "medicamento": "Adalimumabe",
                    "apresentacao": "solução injetável 40mg/0,8mL",
                    "apresentacao_padronizada_sus": True,
                },
                {
                    "medicamento": "Infliximabe",
                    "apresentacao": "frasco-ampola 500mg",  # Não padronizada (padrão é 100mg)
                    "apresentacao_padronizada_sus": False,
                },
            ]
        }
    }

    normalized = AdkResumoService._normalize_payload(raw_payload)
    confronto = normalized["confronto_documentacao_suporte"]
    resumo = normalized["resumo_processo"]

    # Como um item não é padronizado, o conjunto não é padronizado
    assert confronto["apresentacao_padronizada"] is False
    assert len(confronto["itens_avaliados"]) == 2
    assert len(resumo["medicamentos_detalhados"]) == 2

    inflix = next(it for it in confronto["itens_avaliados"] if it["medicamento"] == "Infliximabe")
    assert inflix["status_dispensacao"] == "Não Dispensado"
    assert "A apresentação e dosagem solicitadas não estão padronizadas para fornecimento." in inflix["justificativa_item"]


def test_normalize_payload_handles_multiple_distinct_medications_with_different_outcomes():
    raw_payload = {
        "resumo_processo": {
            "medicamentos_detalhados": [
                {
                    "medicamento": "Azatioprina",
                    "apresentacao": "comprimido 50mg",
                    "posologia": "2 comprimidos ao dia",
                    "origem_documental": "Receituário fl. 3",
                    "apresentacao_padronizada_sus": True,
                    "status_dispensacao": "Componente Especializado - Aprovado",
                    "justificativa_item": "Item padronizado no PCDT para Doença de Crohn.",
                },
                {
                    "medicamento": "Infliximabe",
                    "apresentacao": "frasco 500mg",
                    "posologia": "5mg/kg a cada 8 semanas",
                    "origem_documental": "Laudo LME fl. 5",
                    "apresentacao_padronizada_sus": False,
                    "status_dispensacao": "Componente Especializado - Aprovado",  # Inadvertidamente preenchido
                    "justificativa_item": "",
                },
                {
                    "medicamento": "Mesalazina",
                    "apresentacao": "comprimido 800mg",
                    "posologia": "3 comprimidos ao dia",
                    "origem_documental": "Receituário fl. 3",
                    "apresentacao_padronizada_sus": True,
                    "status_dispensacao": "Componente Especializado - Aprovado",
                    "justificativa_item": "Item padronizado no PCDT.",
                },
            ]
        }
    }

    normalized = AdkResumoService._normalize_payload(raw_payload)
    itens = normalized["confronto_documentacao_suporte"]["itens_avaliados"]
    assert len(itens) == 3

    # Azatioprina e Mesalazina mantêm aprovação
    aza = next(it for it in itens if it["medicamento"] == "Azatioprina")
    assert aza["apresentacao_padronizada_sus"] is True
    assert aza["status_dispensacao"] == "Componente Especializado - Aprovado"

    mesa = next(it for it in itens if it["medicamento"] == "Mesalazina")
    assert mesa["apresentacao_padronizada_sus"] is True
    assert mesa["status_dispensacao"] == "Componente Especializado - Aprovado"

    # Infliximabe não padronizado vira 'Não Dispensado' com justificativa padrão individual
    inf = next(it for it in itens if it["medicamento"] == "Infliximabe")
    assert inf["apresentacao_padronizada_sus"] is False
    assert inf["status_dispensacao"] == "Não Dispensado"
    assert "A apresentação e dosagem solicitadas não estão padronizadas para fornecimento." in inf["justificativa_item"]


def test_normalize_payload_populates_other_cids_for_uncontemplated_drug():
    raw_payload = {
        "resumo_processo": {
            "medicamento_solicitado": "Adalimumabe 40mg injetável",
            "cid_informado": "K29.7",  # Gastrite - não contemplado para Adalimumabe
            "medicamentos_detalhados": [
                {
                    "medicamento": "Adalimumabe",
                    "apresentacao": "solução injetável 40mg/0,8mL",
                    "posologia": "1 aplicação a cada 14 dias",
                    "apresentacao_padronizada_sus": True,
                    "status_dispensacao": "Não Dispensado",
                    "justificativa_item": "Não há previsão no PCDT para o CID informado.",
                }
            ],
        },
        "confronto_documentacao_suporte": {
            "cid_validado": True,
            "medicamento_contemplado_para_o_cid": "não",
            "observacoes": [],
        },
    }

    normalized = AdkResumoService._normalize_payload(raw_payload)
    confronto = normalized["confronto_documentacao_suporte"]

    # Deve listar outros CIDs onde o Adalimumabe é fornecido no SUS
    outros = confronto["outros_cids_contemplados_para_o_medicamento"]
    assert len(outros) > 0
    assert any("K50" in item for item in outros)
    assert any("M05" in item or "Artrite" in item for item in outros)
    assert any("L40" in item or "Psoríase" in item for item in outros)

    # Deve adicionar às observações a informação sobre as outras patologias atendidas
    assert any("padronizado no sus para outras patologias" in obs.lower() for obs in confronto["observacoes"])


def test_get_cids_sus_info_returns_formatted_sus_indications():
    info = AdkResumoService._get_cids_sus_info("Adalimumabe", "K29.7")
    assert "K50" in info
    assert "M05/M06" in info
    assert "L40" in info
    assert "DIRETRIZ OBRIGATÓRIA DE PARECER" in info


