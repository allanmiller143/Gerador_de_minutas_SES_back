import hashlib
import json
import math
import random
import time

from google.cloud import storage
from google.genai import types
import fitz

class IncrementalRAG:
    """RAG incremental: cria um JSON no storage do GCP para cada PDF usado"""

    def __init__(
        self,
        genai_client,
        bucket_name,
        knowledge_prefix="base-conhecimento/",
        index_prefix="rag-index/",
        embedding_model="gemini-embedding-001",
        dimensions=768,
    ):
        self.ai = genai_client
        self.bucket = storage.Client().bucket(bucket_name)
        self.knowledge_prefix = knowledge_prefix
        self.index_prefix = index_prefix
        self.model = embedding_model
        self.dimensions = dimensions

    def rag(self, query, selected_files, top_k=8):
        """Indexa os PDFs selecionados, pesquisa e devolve contexto + resultados"""
        indexes = [self._ensure_indexed(name) for name in selected_files]
        query_vector = self._embed(query, "RETRIEVAL_QUERY")

        hits = []
        for index in indexes:
            for chunk in index["chunks"]:
                score = self._cosine(query_vector, chunk["embedding"])
                hits.append({
                    "score": score,
                    "source": index["source"],
                    "page": chunk["page"],
                    "text": chunk["text"],
                })

        hits.sort(key=lambda item: item["score"], reverse=True)
        hits = hits[:top_k]

        context = "\n\n".join(
            f"[Fonte: {hit['source']}, página {hit['page']}]\n{hit['text']}"
            for hit in hits
        )
        return {"context": context, "hits": hits}

    def _ensure_indexed(self, file_name):
        """Reutiliza o JSON existente; recria se o PDF mudou"""
        source_name = self._source_name(file_name)
        source_blob = self.bucket.get_blob(source_name)
        if not source_blob:
            raise FileNotFoundError(f"Arquivo não encontrado: gs://{self.bucket.name}/{source_name}")

        index_blob = self.bucket.blob(self._index_name(source_name))
        if index_blob.exists():
            index = json.loads(index_blob.download_as_text())
            if str(index.get("generation")) == str(source_blob.generation):
                print('INDEX JA EXISTIA NA BASE')
                return index

        pdf_bytes = source_blob.download_as_bytes()
        document = fitz.open(stream=pdf_bytes, filetype="pdf")

        chunks = []
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            
            for part in self._chunk(text):
                chunks.append({
                    "page": page_number,
                    "text": part,
                    "embedding": self._embed(
                        part,
                        "RETRIEVAL_DOCUMENT",
                        title=source_name.rsplit("/", 1)[-1],
                    ),
                })
        document.close()

        index = {
            "source": source_name,
            "generation": str(source_blob.generation),
            "model": self.model,
            "dimensions": self.dimensions,
            "chunks": chunks,
        }
        index_blob.upload_from_string(
            json.dumps(index, ensure_ascii=False),
            content_type="application/json",
        )
        index_blob.reload()

        print(
            f"Índice criado: gs://{self.bucket.name}/{index_blob.name} "
            f"({index_blob.size} bytes)"
        )
        return index

    def _embed(self, text, task_type, title=None, attempts=7):
        """Retry para 429/503 durante a indexação"""
        config = types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self.dimensions,
            title=title if task_type == "RETRIEVAL_DOCUMENT" else None,
        )

        for attempt in range(attempts):
            try:
                response = self.ai.models.embed_content(
                    model=self.model,
                    contents=text,
                    config=config,
                )
                return response.embeddings[0].values
            except Exception as error:
                retryable = any(code in str(error).lower() for code in ("429", "503", "resource_exhausted"))
                if not retryable or attempt == attempts - 1:
                    raise
                time.sleep(min(60, 2 ** attempt + random.random()))

    def _source_name(self, file_name):
        return file_name if file_name.startswith(self.knowledge_prefix) else self.knowledge_prefix + file_name

    def _index_name(self, source_name):
        # Hash evita problemas com barras e nomes longos.
        key = hashlib.sha256(source_name.encode()).hexdigest()
        return f"{self.index_prefix.rstrip('/')}/{key}.json"

    @staticmethod
    def _chunk(text, size=4000, overlap=400):
        text = " ".join(text.split())
        if not text:
            return []
        return [text[start:start + size] for start in range(0, len(text), size - overlap)]

    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        return dot / (norm_a * norm_b or 1.0)