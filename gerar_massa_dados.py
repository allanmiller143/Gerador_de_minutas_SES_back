import os
import random
from datetime import datetime, timedelta
from app import db, create_app
from app.models import ProcessoSEI

#Inicia o Flask sem exigir a API Key real.
os.environ["GEMINI_API_KEY"] = "chave_falsa_apenas_para_gerar_massa"

app = create_app()


with app.app_context():
    db.drop_all()   #Apaga o banco antigo, se existir.
    # db.create_all() #Cria novo banco.

    #Configuração dos dados dos mocks.
    assuntos = ["Fornecimento de medicamento oncológico", "Vaga em UTI", "Cirurgia bariátrica", "Cadeira de rodas"]
    prioridades = ["Alta", "Média", "Baixa"]
    status_lista = ["Pré-análise", "Em revisão", "Concluído"]
    analistas = ["Ana Silva", "Carlos Souza", "Mariana Lima", "Roberto Alves"]

    #Gera os processos.
    for i in range(30):
        #Cria um número de processo falso.
        num_fake = f"000{random.randint(1000, 9999)}-{random.randint(10, 99)}.2026.8.26.0053"

        #Evita gerar números duplicados.
        if db.session.query(ProcessoSEI).filter_by(numero=num_fake).first():
            continue

        #Sorteia um status para o processo atual.
        status_atual = random.choice(status_lista)

        #Define a data de recebimento (entre 1 e 30 dias atrás).
        data_recebimento = datetime.now() - timedelta(days=random.randint(1, 30))

        #Simula que a IA fez a pré-análise rápida (entre 1 a 4 horas).
        data_pre_analise = data_recebimento + timedelta(hours=random.randint(1, 4))

        # Por padrão, assume que não tem analista nem data de revisão.
        analista_atribuido = None
        data_revisao_atribuida = None

        #Se o processo estiver em 'Em revisão' ou 'Concluído', ganha um analista e data de revisão.
        if status_atual in ["Em revisão", "Concluído"]:
            analista_atribuido = random.choice(analistas)

            #Se já estiver 'Concluído', calcula quando o humano revisou (entre 1 a 3 dias após a IA).
            if status_atual == "Concluído":
                data_revisao_atribuida = data_pre_analise + timedelta(days=random.randint(1, 3))

        #Mock de JSON de jurisprudências.
        mock_jurisprudencias = [
            {"id": 1, "titulo": "Súmula 45-TJ", "relevancia": "Alta"},
            {"id": 2, "titulo": "Acórdão RE 123.456", "relevancia": "Média"}
        ] if random.random() > 0.3 else [] #30% de chance de não ter jurisprudência.

        #Preenche a tabela.
        novo_processo = ProcessoSEI(
            numero=num_fake,
            assunto=random.choice(assuntos),
            status=status_atual,
            prioridade=random.choice(prioridades),
            dataRecebimento=data_recebimento,
            dataPreAnalise=data_pre_analise,
            dataRevisao=data_revisao_atribuida,
            iaConfidence=round(random.uniform(0.65, 0.99), 2), #Sorteia entre 65% e 99%.
            analista=analista_atribuido,
            iaSugestao="Com base nos laudos anexados, o parecer técnico sugere o deferimento do pedido...", #Inicio genérico para o texto da IA.
            jurisprudenciasSugeridas=mock_jurisprudencias
        )

        db.session.add(novo_processo)

    db.session.commit()

    print(f"{db.session.query(ProcessoSEI).count()} processos inseridos.")