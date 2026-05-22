# Documentação de usuário — Serviço de Resumo Técnico de Processo

Este documento explica como executar e consumir o serviço de geração de **resumo técnico preliminar** a partir de um PDF de processo administrativo/assistencial.

O serviço recebe o conteúdo de um PDF em formato de lista de bytes, extrai o texto do documento, consulta o modelo Gemini configurado e retorna um JSON estruturado com resumo do processo, evidências clínicas, confronto com documentação de suporte e insumos para parecer.

> Atenção: a resposta gerada é um apoio técnico preliminar. O próprio serviço orienta que `necessita_revisao_humana` seja mantido como `true`. O resultado não deve ser tratado como decisão institucional final.

---

## 1. Pré-requisitos

- Python 3.8 ou superior.
- Ambiente virtual Python recomendado.
- Dependências instaladas a partir de `requirements.txt`.
- Chave de API do Gemini configurada na variável `GEMINI_API_KEY`.

---

## 2. Configuração do ambiente

Na raiz do backend, crie ou atualize o arquivo `.env`:

```env
SECRET_KEY=sua_chave_secreta
JWT_SECRET_KEY=sua_chave_jwt
DATABASE_URL=sqlite:///site.db
GEMINI_API_KEY=sua_chave_da_api_gemini
```

A variável obrigatória para a geração do resumo é:

```env
GEMINI_API_KEY=sua_chave_da_api_gemini
```

Se ela não estiver configurada, o serviço não conseguirá chamar o Gemini.

---

## 3. Como executar o backend

Acesse a pasta do backend:

```bash
cd /home/helaine-barreiros/Development/ses-workspace/Gerador_de_minutas_SES_back
```

Crie e ative o ambiente virtual, se ainda não existir:

```bash
python -m venv venv
source venv/bin/activate
```

Instale as dependências:

```bash
pip install -r requirements.txt
```

Execute a aplicação:

```bash
python run.py
```

Por padrão, a API ficará disponível em:

```text
http://127.0.0.1:5000
```

---

## 4. Endpoint principal

### Gerar resumo técnico

- Método: `POST`
- URL local: `http://127.0.0.1:5000/api/resumo`
- Content-Type: `application/json`
- Autenticação: atualmente a rota não exige JWT.

---

## 5. Payload de entrada

### Estrutura geral

```json
{
  "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
  "filename": "processo.pdf",
  "model": "gemini-2.5-pro",
  "options": {
    "usar_documentacao_suporte": true,
    "max_trechos_suporte": 12,
    "incluir_minuta_parecer": true
  }
}
```

### Campos

- `pdf_bytes` — obrigatório.
  - Lista de inteiros entre `0` e `255` representando os bytes do PDF.
  - O conteúdo precisa ser um PDF válido e começar com assinatura `%PDF`.

- `filename` — opcional.
  - Nome do arquivo enviado.
  - Se omitido ou vazio, o serviço assume `arquivo.pdf`.

- `model` — opcional.
  - Modelo Gemini que será usado.
  - Valor padrão: `gemini-2.5-pro`.
  - Exemplo alternativo: `gemini-2.5-flash`.

- `options` — opcional.
  - Objeto de configuração da geração.

### Opções disponíveis

```json
{
  "usar_documentacao_suporte": true,
  "max_trechos_suporte": 12,
  "incluir_minuta_parecer": true
}
```

- `usar_documentacao_suporte`
  - Tipo: booleano.
  - Padrão: `true`.
  - Quando `true`, o serviço inclui contexto técnico extraído de documentos de suporte locais.
  - Quando `false`, o Gemini recebe apenas o texto extraído do PDF do processo.

- `max_trechos_suporte`
  - Tipo: inteiro.
  - Padrão: `12`.
  - Faixa permitida: `1` a `30`.
  - Controla o volume de documentação de suporte incluído no prompt.

- `incluir_minuta_parecer`
  - Tipo: booleano.
  - Padrão: `true`.
  - Quando `true`, solicita conclusão farmacêutica preliminar, fundamentos técnicos e pendências.
  - Quando `false`, solicita um insumo mais objetivo, sem minuta expandida.

---

## 6. Como transformar um PDF em `pdf_bytes`

### Opção A — usando Python

```python
from pathlib import Path
import json

pdf_path = Path("processo.pdf")
payload = {
    "pdf_bytes": list(pdf_path.read_bytes()),
    "filename": pdf_path.name,
    "model": "gemini-2.5-pro",
    "options": {
        "usar_documentacao_suporte": True,
        "max_trechos_suporte": 12,
        "incluir_minuta_parecer": True,
    },
}

print(json.dumps(payload, ensure_ascii=False))
```

### Opção B — usando Python para chamar a API diretamente

```python
from pathlib import Path
import requests

url = "http://127.0.0.1:5000/api/resumo"
pdf_path = Path("processo.pdf")

payload = {
    "pdf_bytes": list(pdf_path.read_bytes()),
    "filename": pdf_path.name,
    "options": {
        "usar_documentacao_suporte": True,
        "max_trechos_suporte": 12,
        "incluir_minuta_parecer": True,
    },
}

response = requests.post(url, json=payload, timeout=180)
print(response.status_code)
print(response.json())
```

---

## 7. Exemplo de chamada com `curl`

Como o payload pode ficar muito grande, é recomendado gerar um arquivo JSON temporário com Python e depois enviá-lo com `curl`.

```bash
python - <<'PY'
from pathlib import Path
import json

pdf_path = Path("processo.pdf")
payload = {
    "pdf_bytes": list(pdf_path.read_bytes()),
    "filename": pdf_path.name,
    "model": "gemini-2.5-pro",
    "options": {
        "usar_documentacao_suporte": True,
        "max_trechos_suporte": 12,
        "incluir_minuta_parecer": True,
    },
}

Path("payload_resumo.json").write_text(
    json.dumps(payload, ensure_ascii=False),
    encoding="utf-8",
)
PY

curl -X POST http://127.0.0.1:5000/api/resumo \
  -H "Content-Type: application/json" \
  --data @payload_resumo.json
```

---

## 8. Exemplo mínimo de payload

Este exemplo mostra a estrutura mínima. Os bytes abaixo representam apenas o início de um PDF e servem para ilustrar o formato; para uso real, envie os bytes completos de um PDF válido com texto extraível.

```json
{
  "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
  "filename": "processo.pdf"
}
```

---

## 9. Exemplo completo de payload

```json
{
  "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
  "filename": "solicitacao_medicamento.pdf",
  "model": "gemini-2.5-pro",
  "options": {
    "usar_documentacao_suporte": true,
    "max_trechos_suporte": 12,
    "incluir_minuta_parecer": true
  }
}
```

Em produção, substitua a lista abreviada de `pdf_bytes` pela lista completa dos bytes do arquivo PDF.

---

## 10. Exemplo de resposta com sucesso

Status HTTP:

```text
200 OK
```

Corpo da resposta:

```json
{
  "resumo": {
    "resumo_processo": {
      "tipo_demanda": "solicitação administrativa de medicamento",
      "medicamento_solicitado": "exemplo: medicamento X 10 mg",
      "cid_informado": "exemplo: M00.0",
      "diagnostico_informado": "exemplo: condição clínica informada no processo",
      "objetivo_da_solicitacao": "síntese objetiva da demanda apresentada"
    },
    "evidencias_clinicas_do_processo": [
      "Documento médico informa diagnóstico e medicamento solicitado.",
      "Foram identificados exames/laudos anexados ao processo.",
      "Há pendências ou inconsistências a serem revisadas pela equipe técnica."
    ],
    "confronto_documentacao_suporte": {
      "cid_validado": false,
      "medicamento_contemplado_para_o_cid": "indeterminado",
      "observacoes": [
        "Necessário confrontar CID, diagnóstico e medicamento com PCDT, RENAME, REESME ou norma aplicável.",
        "A presença em lista oficial não implica deferimento automático."
      ]
    },
    "insumo_parecer": {
      "conclusao_tecnica_sugerida": "Análise preliminar condicionada à revisão humana e à conferência da documentação clínica.",
      "fundamentos": [
        "Texto extraído do processo indica solicitação de tratamento medicamentoso.",
        "É necessário validar compatibilidade entre CID, indicação terapêutica e documentação de suporte."
      ],
      "alternativas_orientaveis": [],
      "pendencias_documentais": [
        "Conferir se há prescrição atualizada, laudo médico e exames pertinentes."
      ],
      "necessita_revisao_humana": true,
      "nivel_confianca": "baixo"
    },
    "fontes_consultadas": [
      "Texto extraído do PDF do processo",
      "Documentação de suporte local, quando disponível"
    ]
  },
  "metadata": {
    "filename": "solicitacao_medicamento.pdf",
    "text_chars": 12345,
    "model": "gemini-2.5-pro"
  }
}
```

### Observações sobre a resposta

- `resumo` contém o resultado técnico estruturado retornado pelo modelo.
- `metadata.filename` informa o nome do arquivo recebido.
- `metadata.text_chars` informa quantos caracteres de texto foram extraídos do PDF.
- `metadata.model` informa o modelo Gemini usado na geração.
- O conteúdo exato do `resumo` pode variar conforme o PDF e a resposta do modelo.

---

## 11. Respostas de erro

### 400 — `pdf_bytes` ausente, vazio ou inválido

Payload inválido:

```json
{}
```

Resposta:

```json
{
  "error": "O campo 'pdf_bytes' é obrigatório e deve ser uma lista de inteiros entre 0 e 255."
}
```

Também ocorre quando `pdf_bytes` não é lista ou contém valores fora da faixa `0` a `255`.

Exemplo inválido:

```json
{
  "pdf_bytes": [256, 0, 0]
}
```

Resposta:

```json
{
  "error": "O campo 'pdf_bytes' é obrigatório e deve ser uma lista de inteiros entre 0 e 255."
}
```

### 400 — `options` inválido

Payload inválido:

```json
{
  "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
  "options": "x"
}
```

Resposta:

```json
{
  "error": "O campo 'options' deve ser um objeto JSON válido."
}
```

### 400 — `max_trechos_suporte` fora da faixa

Payload inválido:

```json
{
  "pdf_bytes": [37, 80, 68, 70, 45, 49, 46, 55],
  "options": {
    "max_trechos_suporte": 0
  }
}
```

Resposta:

```json
{
  "error": "O campo 'options.max_trechos_suporte' deve ser um inteiro entre 1 e 30."
}
```

### 422 — bytes não correspondem a um PDF válido ou texto não extraível

Payload inválido:

```json
{
  "pdf_bytes": [1, 2, 3, 4]
}
```

Resposta:

```json
{
  "error": "Não foi possível extrair texto do PDF informado."
}
```

Também pode ocorrer quando o arquivo é PDF, mas não possui texto extraível, por exemplo um PDF apenas com imagem escaneada sem OCR.

### 500 — falha na geração pelo modelo

Resposta:

```json
{
  "error": "Falha ao gerar resumo técnico."
}
```

Possíveis causas:

- `GEMINI_API_KEY` ausente ou inválida.
- Indisponibilidade temporária da API do Gemini.
- Modelo informado inexistente ou indisponível.
- Timeout ou erro de comunicação com o provedor.

---

## 12. Documentação de suporte usada pelo serviço

Quando `options.usar_documentacao_suporte` está habilitado, o serviço tenta carregar conteúdo de suporte em:

```text
ARQUIVOS SUPORTE IA/extracted/analise_documentacao_suporte_llm_farmacia.md
```

Se esse arquivo não existir ou estiver vazio, o serviço continua funcionando, mas envia o resumo ao Gemini sem esse contexto adicional.

---

## 13. Recomendações de uso

- Envie PDFs com texto pesquisável/extraível. PDFs escaneados devem passar por OCR antes.
- Use `gemini-2.5-pro` quando priorizar análise mais robusta.
- Use `gemini-2.5-flash` quando priorizar velocidade/custo, se o modelo estiver disponível na conta.
- Mantenha `incluir_minuta_parecer=true` quando o objetivo for obter insumo mais completo para parecer técnico.
- Mantenha revisão humana obrigatória antes de qualquer uso institucional.
- Não envie dados sensíveis para ambientes ou chaves de API não autorizados.

---

## 14. Checklist rápido de operação

1. Configurar `.env` com `GEMINI_API_KEY`.
2. Instalar dependências com `pip install -r requirements.txt`.
3. Subir a API com `python run.py`.
4. Converter o PDF para lista de bytes.
5. Enviar `POST /api/resumo` com JSON.
6. Conferir `metadata.text_chars` para validar se houve extração significativa de texto.
7. Revisar tecnicamente o campo `resumo.insumo_parecer` antes de usar em qualquer fluxo oficial.
