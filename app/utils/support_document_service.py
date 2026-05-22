from __future__ import annotations

from pathlib import Path


class SupportDocumentService:
    def __init__(self, support_root: str | None = None):
        if support_root:
            self.support_root = Path(support_root)
        else:
            self.support_root = (
                Path(__file__).resolve().parents[3] / "ARQUIVOS SUPORTE IA"
            )

    def build_context(self, max_trechos_suporte: int = 12) -> str:
        analysis_path = self.support_root / "extracted" / "analise_documentacao_suporte_llm_farmacia.md"
        if not analysis_path.exists():
            return ""

        content = analysis_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            return ""

        # Limita tamanho para controlar prompt.
        return content[: max(4000, max_trechos_suporte * 800)]
