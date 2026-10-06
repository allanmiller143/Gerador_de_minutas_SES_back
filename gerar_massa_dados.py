import os
import random
from datetime import datetime, timedelta
from app import db, create_app
from app.models import ProcessoSEI

os.environ["GEMINI_API_KEY"] = "chave_falsa_apenas_para_gerar_massa"

app = create_app()

with app.app_context():
    db.drop_all()   #Apaga o banco antigo
    db.create_all() #Cria o novo banco com a estrutura atualizada

    assuntos = [
        "Fornecimento de medicamento oncológico", 
        "Vaga em UTI", 
        "Cirurgia bariátrica", 
        "Cadeira de rodas",
        "Solicitação de Órtese/Prótese",
        "Transferência interhospitalar"
    ]
    status_lista = ["Pré-análise", "Em revisão", "Concluído"]
    analistas = ["Ana Silva", "Carlos Souza", "Mariana Lima", "Roberto Alves"]


    orgaos_controle = [
        "TCE-SP - Tribunal de Contas",
        "MPSP - Ministério Público",
        "TJSP - Tribunal de Justiça",
        "Controladoria Geral do Estado (CGE)"
    ]
    
    orgaos_internos = [
        "Secretaria de Estado da Saúde",
        "Gabinete do Secretário",
        "Departamento de Suprimentos",
        "Coordenadoria de Regulação",
        "Assessoria Técnica - SES"
    ]

    for i in range(50):
        num_fake = f"000{random.randint(1000, 9999)}-{random.randint(10, 99)}.2026.8.26.0053"

        if db.session.query(ProcessoSEI).filter_by(numero=num_fake).first():
            continue

        status_atual = random.choice(status_lista)

        prioridades_possiveis = ["Baixa", "Média", "Alta", "Máxima"]
        prioridade_sorteada = random.choice(prioridades_possiveis)
        
        if random.random() < 0.20: #20% de chance de ser Órgão de Controle, 80% Órgão Interno
            remetente_sorteado = random.choice(orgaos_controle)
        else:
            remetente_sorteado = random.choice(orgaos_internos)

        prazo_dias = random.choice([2, 5, 10, 15, 30])
        dias_restantes = random.choice([-1, 0, 1, 2, 3, 5, 8, 12, 20, 25])
        
        hoje = datetime.now()
        data_vencimento_alvo = hoje + timedelta(days=dias_restantes)
        data_emissao = data_vencimento_alvo - timedelta(days=prazo_dias)
        data_recebimento = data_emissao + timedelta(hours=random.randint(2, 24))

        data_pre_analise = data_recebimento + timedelta(hours=random.randint(1, 4))

        analista_atribuido = None
        data_revisao_atribuida = None

        if status_atual in ["Em revisão", "Concluído"]:
            analista_atribuido = random.choice(analistas)
            if status_atual == "Concluído":
                data_revisao_atribuida = data_pre_analise + timedelta(days=random.randint(1, 3))

        mock_jurisprudencias = [
            {"id": 1, "titulo": "Súmula 45-TJ", "relevancia": "Alta"},
            {"id": 2, "titulo": "Acórdão RE 123.456", "relevancia": "Média"}
        ] if random.random() > 0.3 else []

        novo_processo = ProcessoSEI(
            numero=num_fake,
            assunto=random.choice(assuntos),
            status=status_atual,
            prioridade=prioridade_sorteada,  
            prioridade_original=prioridade_sorteada,
            dataRecebimento=data_recebimento,
            dataPreAnalise=data_pre_analise,
            dataRevisao=data_revisao_atribuida,
            iaConfidence=round(random.uniform(0.65, 0.99), 2),
            analista=analista_atribuido,
            iaSugestao="Com base nos laudos anexados, o parecer técnico sugere o deferimento do pedido...",
            jurisprudenciasSugeridas=mock_jurisprudencias,
            remetente=remetente_sorteado,
            data_emissao_documento=data_emissao,
            prazo_legal_dias=prazo_dias
        )

        novo_processo.atualizar_prioridade()

        db.session.add(novo_processo)

    db.session.commit()

    print(f"{db.session.query(ProcessoSEI).count()} processos inseridos com sucesso.")