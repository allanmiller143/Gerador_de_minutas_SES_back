from app.utils.resumo_service import ResumoService


def test_build_prompt_is_farmacological_and_forces_human_review():
    prompt = ResumoService().build_prompt(
        process_text="texto do processo",
        support_context="contexto de suporte",
        include_minuta=True,
    )

    assert "farmacêutico avaliador" in prompt
    assert "não defina deferimento/indeferimento" in prompt
    assert "RENAME, REESME ou CEAF não implica deferimento automático" in prompt
    assert "CID/diagnóstico com medicamento e confronto com PCDT/Norma/Guia" in prompt
    assert "necessita_revisao_humana=true" in prompt
    assert "conclusão farmacêutica preliminar" in prompt


def test_build_prompt_without_minuta_uses_objective_instruction():
    prompt = ResumoService().build_prompt(
        process_text="texto do processo",
        support_context="",
        include_minuta=False,
    )

    assert "sem minuta expandida de parecer" in prompt
    assert "DOCUMENTOS DE SUPORTE:\nN/A" in prompt
