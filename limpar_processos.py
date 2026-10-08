#!/usr/bin/env python3
"""
Script para limpeza e reinicialização da base de dados de processos.

Este script remove com segurança todos os dados operacionais de processos:
  - processos_sei (ProcessoSEI)
  - resumo_tecnico_versions (ResumoTecnicoVersion)
  - resumo_reexecution_requests (ResumoReexecutionRequest)
  - resumo_batch_runs (ResumoBatchRun) [opcionalmente preservável com --manter-runs]

E PRESERVA integralmente as configurações e cadastros essenciais do sistema:
  - users & roles (usuários e perfis de acesso)
  - remetentes (órgãos e níveis de prioridade)
  - textos_padroes & categorias_textos_padroes (textos padrão de minutas)
  - prompt_configs (configurações de prompts de IA)
  - knowledge_documents (documentos da base de conhecimento RAG)
  - resumo_batch_schedules (configurações do agendador automático)
  - alembic_version (versionamento de migrações)

Uso:
  python limpar_processos.py                  # Pede confirmação interativa
  python limpar_processos.py --force          # Limpa sem confirmação
  python limpar_processos.py --seed           # Limpa e gera massa de dados de teste atualizada
  python limpar_processos.py --seed --qtd 40  # Limpa e gera 40 processos de teste
"""

import os
import sys
import argparse
import random
from datetime import datetime, timedelta

# Garante que chave fictícia exista caso não esteja no .env para carregar a app
if "GEMINI_API_KEY" not in os.environ:
    os.environ["GEMINI_API_KEY"] = "dummy_key_for_db_maintenance"

from app import create_app, db
from app.models import (
    ProcessoSEI,
    ResumoTecnicoVersion,
    ResumoReexecutionRequest,
    ResumoBatchRun,
    Remetente,
    User,
    Role,
    TextoPadrao,
    TextoCategoria,
    KnowledgeDocument,
    PromptConfig,
)


def obter_estatisticas():
    """Retorna a contagem atual das tabelas do sistema."""
    return {
        "processos": ProcessoSEI.query.count(),
        "resumos_versoes": ResumoTecnicoVersion.query.count(),
        "reexecucoes": ResumoReexecutionRequest.query.count(),
        "batch_runs": ResumoBatchRun.query.count(),
        "usuarios": User.query.count(),
        "remetentes": Remetente.query.count(),
        "textos_padroes": TextoPadrao.query.count(),
        "knowledge_docs": KnowledgeDocument.query.count(),
    }


def resetar_sequencias_postgres():
    """Reinicia as sequences de auto-incremento no PostgreSQL para começarem de 1."""
    if db.engine.dialect.name == "postgresql":
        sequencias = [
            "processos_sei_id_seq",
            "resumo_tecnico_versions_id_seq",
            "resumo_reexecution_requests_id_seq",
            "resumo_batch_runs_id_seq",
        ]
        with db.engine.connect() as conn:
            for seq in sequencias:
                try:
                    conn.execute(db.text(f"ALTER SEQUENCE {seq} RESTART WITH 1;"))
                except Exception:
                    pass
            conn.commit()


def limpar_base_processos(manter_runs=False):
    """Executa a limpeza das tabelas de processos e seus históricos."""
    print("\nExecutando limpeza...")

    total_versoes = ResumoTecnicoVersion.query.delete()
    total_reexec = ResumoReexecutionRequest.query.delete()

    total_runs = 0
    if not manter_runs:
        total_runs = ResumoBatchRun.query.delete()

    total_processos = ProcessoSEI.query.delete()

    db.session.commit()

    # Reinicia sequences de IDs
    resetar_sequencias_postgres()

    print("Limpeza concluída com sucesso:")
    print(f"  - {total_processos} processos excluídos")
    print(f"  - {total_versoes} versões de resumos técnicos excluídas")
    print(f"  - {total_reexec} solicitações de reexecução excluídas")
    if not manter_runs:
        print(f"  - {total_runs} execuções em lote excluídas")


def gerar_processos_teste(qtd=25):
    """Gera massa de dados de teste atualizada com suporte às novas funcionalidades."""
    print(f"\nGerando {qtd} processos de teste com as novas funcionalidades...")

    # Obtém remetentes cadastrados no banco ou cria referências locais
    remetentes_db = Remetente.query.all()
    if not remetentes_db:
        # Se não houver remetentes, cadastra alguns exemplos com prioridades distintas
        exemplos_remetentes = [
            ("TCE-SP", "Tribunal de Contas do Estado", "Máxima", "#dc2626"),
            ("MPSP", "Ministério Público de São Paulo", "Alta", "#ea580c"),
            ("TJSP", "Tribunal de Justiça de SP", "Alta", "#ea580c"),
            ("GAB", "Gabinete do Secretário de Saúde", "Média", "#2563eb"),
            ("SES-SUP", "Departamento de Suprimentos SES", "Baixa", "#16a34a"),
        ]
        for sigla, nome, prio, cor in exemplos_remetentes:
            rem = Remetente(sigla=sigla, nome_completo=nome, prioridade=prio, cor=cor)
            db.session.add(rem)
        db.session.commit()
        remetentes_db = Remetente.query.all()

    assuntos = [
        "Fornecimento de medicamento oncológico (Trastuzumabe)",
        "Tratamento de Diabetes Mellitus - Insulina Glargina",
        "Artrite Reumatoide - Adalimumabe 40mg",
        "Solicitação de Cadeira de Rodas Motorizada",
        "Vaga de UTI Neonatal com Urgência",
        "Cirurgia bariátrica por videolaparoscopia",
        "Fornecimento de fórmula infantil para APLV (Neocate)",
        "Transferência inter-hospitalar de urgência",
    ]

    partes_exemplos = [
        "Maria da Silva Santos x Estado de SP",
        "João Pedro Oliveira x Secretaria de Saúde",
        "Ana Clara Fernandes x Fazenda Pública Estadual",
        "Lucas Gabriel Ribeiro x Estado de São Paulo",
        "Beatriz Lima Souza x Estado de SP",
    ]

    analistas = ["Ana Silva", "Carlos Souza", "Mariana Lima", "Roberto Alves"]
    status_lista = ["Pré-análise", "Em revisão", "Concluído"]

    for i in range(qtd):
        num_fake = f"000{random.randint(1000, 9999)}-{random.randint(10, 99)}.2026.8.26.0053"

        if ProcessoSEI.query.filter_by(numero=num_fake).first():
            continue

        status_atual = random.choice(status_lista)
        remetente_obj = random.choice(remetentes_db)
        remetente_nome = remetente_obj.nome_completo
        prioridade_remetente = remetente_obj.prioridade

        # Simula prazos legais e vencimentos
        prazo_dias = random.choice([2, 5, 10, 15, 30])
        # Alguns já vencidos (-1), outros no limite (0, 1, 2) e outros com folga (5, 15)
        dias_restantes = random.choice([-2, 0, 1, 3, 5, 8, 15, 25])

        hoje = datetime.now()
        data_vencimento = hoje + timedelta(days=dias_restantes)
        data_emissao = data_vencimento - timedelta(days=prazo_dias)
        data_recebimento = data_emissao + timedelta(hours=random.randint(1, 12))
        data_pre_analise = data_recebimento + timedelta(hours=random.randint(1, 3))

        analista_atribuido = None
        data_revisao_atribuida = None
        if status_atual in ["Em revisão", "Concluído"]:
            analista_atribuido = random.choice(analistas)
            if status_atual == "Concluído":
                data_revisao_atribuida = data_pre_analise + timedelta(days=random.randint(1, 2))

        # Novo recurso: Alerta de OCR (50% de chance de conter imagens digitalizadas)
        alerta_ocr = random.choice([True, False])

        # Complexidade
        complexidade_opcao = random.choice(["Simples", "Média", "Alta"])
        justificativa_comp = (
            f"Processo classificado como {complexidade_opcao} devido ao perfil da medicação e histórico clínico."
        )

        novo_processo = ProcessoSEI(
            numero=num_fake,
            assunto=random.choice(assuntos),
            status=status_atual,
            status_processamento="Concluído",
            prioridade=prioridade_remetente,
            prioridade_original=prioridade_remetente,
            remetente=remetente_nome,
            dataRecebimento=data_recebimento,
            dataPreAnalise=data_pre_analise,
            dataRevisao=data_revisao_atribuida,
            prazo_legal_dias=prazo_dias,
            data_emissao_documento=data_emissao,
            analista=analista_atribuido,
            iaConfidence=round(random.uniform(0.72, 0.98), 2),
            iaSugestao=(
                "Com base nos laudos anexados e nos protocolos clínicos do SUS, o parecer técnico "
                "sugere análise do enquadramento nos critérios do PCDT."
            ),
            partes=random.choice(partes_exemplos),
            resumo="Solicitação judicial de medicamento. Análise de PCDT e alternativas terapêuticas do SUS.",
            jurisprudenciasSugeridas=[
                {"id": 1, "titulo": "Tema 106 - STJ (Requisitos para concessão de medicamentos)", "relevancia": "Alta"},
                {"id": 2, "titulo": "Súmula 29 - TJSP", "relevancia": "Média"},
            ] if random.random() > 0.3 else [],
        )

        # Campos adicionais das novas branches
        novo_processo.alerta_ocr = alerta_ocr
        novo_processo.complexidade = complexidade_opcao
        novo_processo.complexidade_justificativa = justificativa_comp

        # Aplica o cálculo oficial de prioridade cruzando prazo com prioridade do remetente
        novo_processo.atualizar_prioridade()

        db.session.add(novo_processo)

    db.session.commit()
    total_criados = ProcessoSEI.query.count()
    print(f"{total_criados} processos prontos no banco para testes.")


def main():
    parser = argparse.ArgumentParser(
        description="Limpeza da base de dados de processos para testes locais."
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Executa a limpeza imediatamente sem solicitar confirmação.",
    )
    parser.add_argument(
        "-s",
        "--seed",
        action="store_true",
        help="Gera uma massa de dados de teste atualizada imediatamente após a limpeza.",
    )
    parser.add_argument(
        "-q",
        "--qtd",
        type=int,
        default=25,
        help="Quantidade de processos a gerar se --seed for especificado (padrão: 25).",
    )
    parser.add_argument(
        "--manter-runs",
        action="store_true",
        help="Mantém o histórico de execuções em lote (ResumoBatchRun).",
    )

    args = parser.parse_args()

    # Cria app sem inicializar agendador em background desnecessário
    app = create_app(config_overrides={"TESTING": True})

    with app.app_context():
        stats = obter_estatisticas()

        print("\n" + "=" * 60)
        print(" SISTEMA SES FARMÁCIA - LIMPEZA DA BASE DE PROCESSOS")
        print("=" * 60)
        print("\nEstado atual das tabelas de processos:")
        print(f"  • Processos SEI:                     {stats['processos']}")
        print(f"  • Versões de Resumo Técnico:         {stats['resumos_versoes']}")
        print(f"  • Solicitações de Reexecução:        {stats['reexecucoes']}")
        print(f"  • Execuções em Lote (Batch Runs):    {stats['batch_runs']}")

        print("\nTabelas protegidas que serão PRESERVADAS:")
        print(f"  ✓ Usuários ({stats['usuarios']}) e Perfis de Acesso")
        print(f"  ✓ Remetentes cadastrados ({stats['remetentes']})")
        print(f"  ✓ Textos Padrão ({stats['textos_padroes']})")
        print(f"  ✓ Documentos da Base RAG ({stats['knowledge_docs']})")
        print("  ✓ Configurações de Prompts e Migrações (Alembic)")
        print("=" * 60)

        if not args.force:
            print("\nATENÇÃO: Todos os processos e resumos técnicos serão excluídos.")
            confirm = input("Deseja realmente prosseguir? [s/N]: ").strip().lower()
            if confirm not in ["s", "sim", "y", "yes"]:
                print("Operação cancelada pelo usuário.")
                sys.exit(0)

        limpar_base_processos(manter_runs=args.manter_runs)

        if args.seed:
            gerar_processos_teste(qtd=args.qtd)

        print("\nConcluído com sucesso! O ambiente local está pronto para novos testes.\n")


if __name__ == "__main__":
    main()
