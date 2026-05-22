from copy import deepcopy
from pathlib import Path


PDF_FILENAME = "exemplo-processo-2.pdf"
PDF_MIME_TYPE = "application/pdf"


def get_mock_pdf_path() -> Path:
    workspace_dir = Path(__file__).resolve().parents[3]
    return workspace_dir / "Gerador_de_minutas_SES_front" / "docs" / PDF_FILENAME


def get_mock_pdf_metadata(sei_id: str) -> dict:
    return {
        "filename": PDF_FILENAME,
        "mime_type": PDF_MIME_TYPE,
        "url": f"/api/seis/{sei_id}/pdf",
    }


def with_pdf_metadata(sei: dict) -> dict:
    out = deepcopy(sei)
    out["documentoPdf"] = get_mock_pdf_metadata(out["id"])
    return out


def read_mock_pdf_bytes() -> bytes:
    return get_mock_pdf_path().read_bytes()


SEIS = [
    {
        "id": "1",
        "numero": "0001234-56.2024.8.26.0053",
        "assunto": "Fornecimento de medicamento oncológico",
        "dataRecebimento": "20/05/2024",
        "dataPreAnalise": "20/05/2024",
        "prioridade": "Alta",
        "status": "Pré-análise",
        "partes": "João da Silva x Estado de SP",
        "resumo": "Paciente requer fornecimento de medicamento de alto custo não incorporado ao SUS para tratamento oncológico, com apresentação de laudo médico e relatório de insucesso terapêutico.",
        "iaConfidence": 0.92,
        "iaSugestao": "Deferimento parcial. Medicamento possui registro ANVISA e há jurisprudência consolidada do STJ (REsp 1.657.156) quanto à imprescindibilidade.",
        "jurisprudenciasSugeridas": ["j1", "j2", "j4"],
    },
    {
        "id": "2",
        "numero": "0002345-67.2024.8.26.0053",
        "assunto": "Fornecimento de medicamento",
        "dataRecebimento": "19/05/2024",
        "dataPreAnalise": "19/05/2024",
        "prioridade": "Média",
        "status": "Pré-análise",
        "partes": "Maria Souza x Estado de SP",
        "resumo": "Solicitação de medicamento constante na RENAME. Verificar disponibilidade na rede SUS.",
        "iaConfidence": 0.88,
        "iaSugestao": "Orientar protocolo SUS – medicamento disponível na rede. Improcedência por ausência de recusa administrativa.",
        "jurisprudenciasSugeridas": ["j2"],
    },
    {
        "id": "3",
        "numero": "0003456-78.2024.8.26.0053",
        "assunto": "Fornecimento de insumo",
        "dataRecebimento": "18/05/2024",
        "dataPreAnalise": "18/05/2024",
        "prioridade": "Média",
        "status": "Pré-análise",
        "partes": "Carlos Pereira x Estado de SP",
        "resumo": "Requer fitas e insumos para controle glicêmico.",
        "iaConfidence": 0.81,
        "iaSugestao": "Deferimento – insumo incluído em protocolo. Verificar quantitativo mensal.",
        "jurisprudenciasSugeridas": ["j3"],
    },
    {
        "id": "4",
        "numero": "0004567-89.2024.8.26.0053",
        "assunto": "Fornecimento de medicamento",
        "dataRecebimento": "17/05/2024",
        "dataPreAnalise": "17/05/2024",
        "prioridade": "Baixa",
        "status": "Pré-análise",
        "partes": "Ana Lima x Estado de SP",
        "resumo": "Medicamento com similar padronizado disponível.",
        "iaConfidence": 0.76,
        "iaSugestao": "Improcedência – existe alternativa terapêutica padronizada.",
        "jurisprudenciasSugeridas": ["j5"],
    },
    {
        "id": "5",
        "numero": "0005678-90.2024.8.26.0053",
        "assunto": "Medicamento órfão",
        "dataRecebimento": "16/05/2024",
        "dataPreAnalise": "16/05/2024",
        "prioridade": "Alta",
        "status": "Em revisão",
        "analista": "Mariana Costa",
        "partes": "Pedro Alves x Estado de SP",
        "resumo": "Medicamento de alto custo sem alternativa terapêutica.",
        "iaConfidence": 0.69,
        "iaSugestao": "Caso complexo. Recomenda-se análise humana detalhada.",
        "jurisprudenciasSugeridas": ["j1", "j4"],
    },
    {
        "id": "6",
        "numero": "0006789-01.2024.8.26.0053",
        "assunto": "Fornecimento de fórmula nutricional",
        "dataRecebimento": "15/05/2024",
        "dataPreAnalise": "15/05/2024",
        "prioridade": "Média",
        "status": "Em revisão",
        "analista": "Rafael Souza",
        "resumo": "Fórmula prescrita por nutrólogo.",
        "iaConfidence": 0.84,
        "iaSugestao": "Deferimento – prescrição e laudo adequados.",
        "jurisprudenciasSugeridas": ["j2"],
    },
    {
        "id": "7",
        "numero": "0001111-11.2024.8.26.0053",
        "assunto": "Fornecimento de medicamento",
        "dataRecebimento": "10/05/2024",
        "dataPreAnalise": "10/05/2024",
        "dataRevisao": "20/05/2024",
        "prioridade": "Média",
        "status": "Concluído",
        "analista": "Mariana Costa",
        "resumo": "Revisado pela analista, pronto para envio.",
        "iaConfidence": 0.9,
        "iaSugestao": "Deferimento – requisitos atendidos.",
        "jurisprudenciasSugeridas": ["j2", "j4"],
    },
    {
        "id": "8",
        "numero": "0001222-22.2024.8.26.0053",
        "assunto": "Medicamento de alto custo",
        "dataRecebimento": "08/05/2024",
        "dataPreAnalise": "08/05/2024",
        "dataRevisao": "18/05/2024",
        "prioridade": "Alta",
        "status": "Concluído",
        "analista": "Rafael Souza",
        "resumo": "Resposta oficial já encaminhada.",
        "iaConfidence": 0.95,
        "iaSugestao": "Deferimento integral – jurisprudência consolidada.",
        "jurisprudenciasSugeridas": ["j1", "j2"],
    },
    {
        "id": "9",
        "numero": "0001333-33.2024.8.26.0053",
        "assunto": "Insumo para diabetes",
        "dataRecebimento": "05/05/2024",
        "dataPreAnalise": "05/05/2024",
        "dataRevisao": "16/05/2024",
        "prioridade": "Baixa",
        "status": "Concluído",
        "analista": "Mariana Costa",
        "resumo": "Insumo conforme protocolo.",
        "iaConfidence": 0.87,
        "iaSugestao": "Deferimento padrão.",
        "jurisprudenciasSugeridas": ["j3"],
    },
    {
        "id": "10",
        "numero": "0001444-44.2024.8.26.0053",
        "assunto": "Fornecimento de medicamento",
        "dataRecebimento": "03/05/2024",
        "dataPreAnalise": "03/05/2024",
        "dataRevisao": "14/05/2024",
        "prioridade": "Média",
        "status": "Concluído",
        "analista": "Rafael Souza",
        "resumo": "Revisado e aguardando envio.",
        "iaConfidence": 0.82,
        "iaSugestao": "Deferimento parcial.",
        "jurisprudenciasSugeridas": ["j2"],
    },
]

JURISPRUDENCIAS = [
    {
        "id": "j1",
        "tribunal": "TJSP",
        "numero": "Apelação Cível 100XXXX-XX.2023.8.26.0053",
        "tema": "Medicamento de alto custo – imprescindibilidade",
        "resumo": "É dever do Estado o fornecimento de medicamento quando comprovada a imprescindibilidade e a ausência de alternativa terapêutica fornecida pelo SUS.",
        "link": "#",
        "tags": ["alto custo", "imprescindibilidade", "SUS"],
        "preferencial": True,
    },
    {
        "id": "j2",
        "tribunal": "STJ",
        "numero": "REsp 1.657.156 / SP",
        "tema": "Fornecimento obrigatório – requisitos",
        "resumo": "O Estado não pode se eximir do dever de fornecer medicamento de alto custo quando presentes os requisitos da necessidade e da incapacidade financeira.",
        "link": "#",
        "tags": ["STJ", "repetitivo", "alto custo"],
        "preferencial": True,
    },
    {
        "id": "j3",
        "tribunal": "STJ",
        "numero": "AgInt no AREsp 1.234.567 / SP",
        "tema": "Solidariedade entre entes federativos",
        "resumo": "A responsabilidade pelo fornecimento de medicamento é solidária entre os entes federativos, cabendo ao autor eleger o polo passivo.",
        "link": "#",
        "tags": ["solidariedade", "entes federativos"],
    },
    {
        "id": "j4",
        "tribunal": "STF",
        "numero": "RE 855.178 / SE",
        "tema": "Tema 793 – responsabilidade solidária",
        "resumo": "Tratando-se de ações que visem o fornecimento de medicamentos, a responsabilidade dos entes da Federação é solidária.",
        "link": "#",
        "tags": ["STF", "tema 793"],
        "preferencial": True,
    },
    {
        "id": "j5",
        "tribunal": "TJSP",
        "numero": "AI 2XXXXXX-XX.2024.8.26.0000",
        "tema": "Medicamento sem registro ANVISA",
        "resumo": "O fornecimento de medicamento sem registro na ANVISA é excepcional e exige prova robusta da imprescindibilidade.",
        "link": "#",
        "tags": ["ANVISA", "registro", "excepcional"],
    },
]


def get_sei(sei_id: str):
    return next((sei for sei in SEIS if sei["id"] == sei_id), None)


def get_jurisprudencias_for_sei(sei):
    ids = set(sei.get("jurisprudenciasSugeridas", []))
    return [juris for juris in JURISPRUDENCIAS if juris["id"] in ids]


def get_resumo_tecnico_for_sei(sei: dict) -> dict:
    """Resumo mockado no mesmo formato estruturado retornado por POST /api/resumo."""
    if sei["id"] == "1":
        return {
            "resumo_processo": {
                "tipo_demanda": "solicitação administrativa de medicamento",
                "medicamento_solicitado": "medicamento oncológico de alto custo não incorporado ao SUS",
                "cid_informado": "não informado no mock",
                "diagnostico_informado": "tratamento oncológico",
                "objetivo_da_solicitacao": "fornecimento de medicamento para continuidade terapêutica",
            },
            "evidencias_clinicas_do_processo": [
                "Processo informa pedido de medicamento de alto custo para tratamento oncológico.",
                "Há referência a laudo médico e relatório de insucesso terapêutico.",
                "Documentação clínica deve ser conferida antes de qualquer conclusão institucional.",
            ],
            "confronto_documentacao_suporte": {
                "cid_validado": False,
                "medicamento_contemplado_para_o_cid": "indeterminado",
                "observacoes": [
                    "Validar CID, diagnóstico e indicação terapêutica contra PCDT, CEAF, RENAME, REESME ou norma aplicável.",
                    "A existência de registro ou jurisprudência favorável não dispensa análise farmacêutica do caso concreto.",
                ],
            },
            "insumo_parecer": {
                "conclusao_tecnica_sugerida": "Análise preliminar condicionada à revisão farmacêutica e à conferência documental.",
                "fundamentos": [
                    "Demanda envolve medicamento de alto custo não incorporado ao SUS.",
                    "Necessário comprovar imprescindibilidade, ausência de alternativa terapêutica e aderência à documentação técnica aplicável.",
                ],
                "alternativas_orientaveis": [
                    "Verificar alternativa terapêutica padronizada disponível no SUS ou Programa Farmácia.",
                ],
                "pendencias_documentais": [
                    "Conferir prescrição atualizada, laudo médico, exames e histórico terapêutico.",
                    "Confirmar CID e compatibilidade com a indicação do medicamento solicitado.",
                ],
                "necessita_revisao_humana": True,
                "nivel_confianca": "médio",
            },
            "fontes_consultadas": [
                "Texto extraído do PDF do processo",
                "Documentação de suporte local, quando disponível",
            ],
        }

    return {
        "resumo_processo": {
            "tipo_demanda": "solicitação administrativa de medicamento/insumo",
            "medicamento_solicitado": sei["assunto"],
            "cid_informado": "não informado no mock",
            "diagnostico_informado": "não informado no mock",
            "objetivo_da_solicitacao": sei.get("resumo", "síntese não informada"),
        },
        "evidencias_clinicas_do_processo": [sei.get("resumo", "Sem evidências clínicas detalhadas no mock.")],
        "confronto_documentacao_suporte": {
            "cid_validado": False,
            "medicamento_contemplado_para_o_cid": "indeterminado",
            "observacoes": ["Conferir documentação de suporte antes de concluir."],
        },
        "insumo_parecer": {
            "conclusao_tecnica_sugerida": "Análise preliminar condicionada à revisão humana.",
            "fundamentos": [sei.get("iaSugestao", "Fundamentos não informados no mock.")],
            "alternativas_orientaveis": [],
            "pendencias_documentais": ["Conferir documentação clínica e normativa aplicável."],
            "necessita_revisao_humana": True,
            "nivel_confianca": "baixo",
        },
        "fontes_consultadas": ["Dados mockados do processo"],
    }


def gerar_minuta(numero: str, assunto: str) -> str:
    return f"""EXCELENTÍSSIMO(A) SENHOR(A) DOUTOR(A) JUIZ(A) DE DIREITO

Processo SEI: {numero}
Assunto: {assunto}

A SECRETARIA DE ESTADO DA SAÚDE, por meio da Farmácia/Assistência Farmacêutica, vem, respeitosamente, apresentar manifestação nos autos em epígrafe, nos seguintes termos:

1. DO RELATÓRIO
Trata-se de demanda relativa ao fornecimento de medicamento/insumo, na qual a parte autora pleiteia providências junto ao Poder Público.

2. DA ANÁLISE TÉCNICO-FARMACÊUTICA
Após análise da documentação médica apresentada, verifica-se a necessidade de avaliação quanto à imprescindibilidade do tratamento, à existência de alternativas terapêuticas no SUS e à observância dos protocolos clínicos vigentes.

3. DO DIREITO
O entendimento dos tribunais superiores é consolidado no sentido de que o dever do Estado no fornecimento de medicamentos pressupõe a demonstração dos requisitos da imprescindibilidade, inexistência de alternativa fornecida pelo SUS e capacidade financeira, conforme Tema 793/STF e REsp 1.657.156/SP.

4. DA CONCLUSÃO
Ante o exposto, manifesta-se pela observância dos requisitos legais e jurisprudenciais indicados, submetendo-se a presente minuta à revisão da coordenação.

Atenciosamente,
Analista – Farmácia da SES."""
