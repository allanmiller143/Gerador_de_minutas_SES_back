import hashlib
import json
import math
import random
import re
import time
import unicodedata

from google.cloud import storage
from google.genai import types
import fitz

class IncrementalRAG:
    """RAG incremental: cria um JSON no storage do GCP para cada PDF usado e mantém o manifesto dinâmico."""

    _manifest_cache: dict | None = None
    _manifest_cache_time: float = 0.0
    _MANIFEST_TTL: float = 900.0  # 15 minutos de TTL em memória

    def __init__(
        self,
        genai_client,
        bucket_name,
        knowledge_prefix="base_conhecimento/",
        index_prefix="rag-index/",
        embedding_model="gemini-embedding-001",
        dimensions=768,
    ):
        self.ai = genai_client
        self.bucket = storage.Client().bucket(bucket_name)
        self.knowledge_prefix = knowledge_prefix.rstrip("/") + "/"
        self.index_prefix = index_prefix.rstrip("/") + "/"
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

    @staticmethod
    def _normalize_text(text: str) -> str:
        if not text:
            return ""
        n = unicodedata.normalize("NFKD", text)
        return "".join(c for c in n if not unicodedata.combining(c)).lower().strip()

    def get_or_build_manifest(self, force_refresh: bool = False) -> dict:
        """Obtém o manifesto clínico dinâmico do GCS (ou constrói e salva se não existir)."""
        now = time.time()
        if (
            not force_refresh
            and IncrementalRAG._manifest_cache is not None
            and (now - IncrementalRAG._manifest_cache_time) < IncrementalRAG._MANIFEST_TTL
        ):
            return IncrementalRAG._manifest_cache

        manifest_path = f"{self.index_prefix.rstrip('/')}/knowledge_manifest.json"
        try:
            manifest_blob = self.bucket.blob(manifest_path)
            if not force_refresh and manifest_blob.exists():
                data = json.loads(manifest_blob.download_as_text())
                IncrementalRAG._manifest_cache = data
                IncrementalRAG._manifest_cache_time = now
                return data
        except Exception as exc:
            print(f"Aviso ao consultar knowledge_manifest.json no GCS: {exc}")

        # Se não existe no bucket ou force_refresh foi solicitado, constrói e salva
        try:
            manifest = self._build_knowledge_manifest()
            try:
                manifest_blob = self.bucket.blob(manifest_path)
                manifest_blob.upload_from_string(
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    content_type="application/json",
                )
            except Exception as up_exc:
                print(f"Aviso ao persistir knowledge_manifest.json no GCS: {up_exc}")
            IncrementalRAG._manifest_cache = manifest
            IncrementalRAG._manifest_cache_time = now
            return manifest
        except Exception as build_exc:
            print(f"Erro ao construir knowledge_manifest dinâmico: {build_exc}")
            return {}

    def _build_knowledge_manifest(self) -> dict:
        """Extrai fármacos, apresentações, dosagens, CIDs e PCDTs a partir do REESME e dos PDFs no bucket."""
        # 1. Lista PDFs na base de conhecimento
        blobs = list(self.bucket.list_blobs(prefix=self.knowledge_prefix))
        pdf_files = [b.name for b in blobs if b.name.endswith(".pdf")]

        files_by_stem = {}
        for f in pdf_files:
            stem = f.split("/")[-1].replace(".pdf", "")
            files_by_stem[self._normalize_text(stem)] = f

        def find_files_for_title(title: str) -> list[str]:
            nt = self._normalize_text(title)
            words = [w for w in nt.split() if len(w) > 2 and w not in ["para", "com", "nao", "dos", "das", "tipo", "grau", "fase"]]
            if not words:
                return []
            matched = []
            for stem, f in files_by_stem.items():
                if all(w[:5] in stem for w in words):
                    matched.append(f)
            return matched

        medication_to_indications: dict[str, list[dict]] = {}
        medication_to_presentations: dict[str, list[str]] = {}
        cid_to_pathology: dict[str, str] = {}
        cid_to_files: dict[str, list[str]] = {}
        medication_to_files: dict[str, list[str]] = {}

        # 2. Extrai dados do REESME-2025 se disponível
        reesme_blob = None
        for b_name in pdf_files:
            if "reesme" in b_name.lower():
                reesme_blob = self.bucket.get_blob(b_name)
                break

        if reesme_blob:
            try:
                doc = fitz.open(stream=reesme_blob.download_as_bytes(), filetype="pdf")
                current_drug = None

                for page_idx in range(len(doc)):
                    p = doc[page_idx]
                    tabs = p.find_tables()
                    for tab in tabs:
                        for r in tab.extract():
                            cleaned = [str(x).strip() if x else "" for x in r]
                            if any("DENOMINA" in c for c in cleaned):
                                continue
                            first_val = cleaned[0] if cleaned[0] else (cleaned[1] if len(cleaned) > 1 and cleaned[1] else "")
                            if (
                                first_val
                                and len(first_val) > 3
                                and not first_val[0].isdigit()
                                and not first_val.startswith("PCDT")
                                and not any(u in first_val for u in ["mg", "mcg", "UI", "g/"])
                            ):
                                current_drug = self._normalize_text(first_val)

                            if not current_drug:
                                continue

                            medication_to_indications.setdefault(current_drug, [])
                            medication_to_presentations.setdefault(current_drug, [])
                            medication_to_files.setdefault(current_drug, [])

                            # Dosagem e forma farmacêutica
                            dosage_val = next((c for c in cleaned if any(u in c for u in ["mg", "mcg", "UI", "g/", "mL"])), "")
                            forma_val = next(
                                (
                                    c
                                    for c in cleaned
                                    if any(
                                        f in c.upper()
                                        for f in [
                                            "COMPRIMIDO", "CÁPSULA", "SOLUÇÃO", "SUSPENSÃO",
                                            "PÓ", "INJETÁVEL", "FRASCO", "AMPOLA",
                                            "ADESIVO", "GEL", "POMADA", "COLÍRIO", "IMPLANTE",
                                        ]
                                    )
                                ),
                                "",
                            )
                            if dosage_val or forma_val:
                                pres_str = f"{dosage_val} {forma_val}".strip()
                                if pres_str and pres_str not in medication_to_presentations[current_drug]:
                                    medication_to_presentations[current_drug].append(pres_str)

                            pcdt_val = next((c for c in reversed(cleaned) if "PCDT" in c or "Norma" in c or "CID" in c), "")
                            if pcdt_val:
                                clean_pcdt = re.sub(r"\s+", " ", pcdt_val).strip()
                                parts = [p.strip() for p in re.split(r";|\.(?=\s*(?:PCDT|Norma|\b[A-Z]))", clean_pcdt) if p.strip()]
                                for part in parts:
                                    cids = re.findall(r"\b[A-Z]\d{2}(?:\.\d{1,2})?\b", part)
                                    title_m = re.search(r"^(?:PCDT\s+|Norma\s+Técnica[^\(]*?\s+)?(.*?)(?:\(CID|\bCID|$)", part, re.IGNORECASE)
                                    title = title_m.group(1).strip(" :;,. ") if title_m else ""
                                    if cids and title:
                                        prefixes = sorted(list(set(c[:3] for c in cids)))
                                        cid_summary = "/".join(prefixes) if len(prefixes) <= 2 else prefixes[0]
                                        matched_files = find_files_for_title(title)

                                        ind_obj = {
                                            "cid": cid_summary,
                                            "patologia": title,
                                            "cids_detalhados": cids,
                                            "arquivos": matched_files,
                                        }

                                        if not any(x["patologia"].lower() == title.lower() for x in medication_to_indications[current_drug]):
                                            medication_to_indications[current_drug].append(ind_obj)

                                        for f in matched_files:
                                            if f not in medication_to_files[current_drug]:
                                                medication_to_files[current_drug].append(f)

                                        for pfx in prefixes:
                                            if pfx not in cid_to_pathology:
                                                cid_to_pathology[pfx] = title
                                            cid_to_files.setdefault(pfx, [])
                                            for f in matched_files:
                                                if f not in cid_to_files[pfx]:
                                                    cid_to_files[pfx].append(f)
                doc.close()
            except Exception as exc:
                print(f"Aviso ao processar REESME no bucket: {exc}")

        # Mapeia arquivos de protocolos_clinicos para prefixos de CID conhecidos
        for f in pdf_files:
            if "protocolos_clinicos" in f or "normas_tecnicas" in f:
                title = f.split("/")[-1].replace(".pdf", "")
                ntitle = self._normalize_text(title)
                for pfx, pathol in cid_to_pathology.items():
                    if self._normalize_text(pathol)[:5] in ntitle:
                        if f not in cid_to_files.setdefault(pfx, []):
                            cid_to_files[pfx].append(f)

        return {
            "version": "1.0",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_drugs": len(medication_to_indications),
            "total_cid_prefixes": len(cid_to_files),
            "medication_to_indications": medication_to_indications,
            "medication_to_presentations": medication_to_presentations,
            "cid_to_files": cid_to_files,
            "cid_to_pathology": cid_to_pathology,
            "medication_to_files": medication_to_files,
        }

    def get_indications_for_medication(self, medication: str) -> list[dict]:
        """Consulta as indicações do SUS para determinado fármaco a partir do manifesto dinâmico."""
        if not medication:
            return []
        manifest = self.get_or_build_manifest()
        med_norm = self._normalize_text(medication)
        med_map = manifest.get("medication_to_indications", {})

        for med_key, cids_list in med_map.items():
            if med_key in med_norm or med_norm in med_key:
                return cids_list
        return []