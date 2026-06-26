# 🚀 Backend API - Projeto Saúde

Este é o serviço de backend desenvolvido em **Python** utilizando o framework **Flask**. A API gerencia autenticação de usuários, controle de permissões (RBAC) e operações administrativas.

---

## 🛠️ Tecnologias e Dependências

A aplicação utiliza as seguintes tecnologias principais:

| Tecnologia | Descrição |
| :--- | :--- |
| **Flask** | Micro-framework web para Python |
| **Flask-SQLAlchemy** | ORM para interação com banco de dados |
| **Flask-JWT-Extended** | Gerenciamento de autenticação via JSON Web Tokens |
| **Flask-Bcrypt** | Hashing seguro de senhas |
| **Flask-Migrate** | Gerenciamento de migrações de banco de dados |
| **Flask-Cors** | Suporte a requisições de diferentes origens |
| **Pytest** | Framework para testes automatizados |

---

## ⚙️ Pré-requisitos

Antes de começar, você precisará ter instalado em sua máquina:
- [Python 3.8+](https://www.python.org/downloads/)
- [Pip](https://pip.pypa.io/en/stable/installation/)

---

## 🚀 Como Rodar o Projeto

### 1. Clonar e Acessar o Diretório
```bash
# Navegue até a pasta do backend
cd back
```

### 2. Configurar Ambiente Virtual
É altamente recomendado o uso de um ambiente virtual para isolar as dependências:

```bash
# Criar o ambiente virtual
python -m venv venv

# Ativar no Linux/macOS:
source venv/bin/activate

# Ativar no Windows:
venv\Scripts\activate
```

### 3. Instalar Dependências
```bash
pip install -r requirements.txt
```

### 4. Configurar Variáveis de Ambiente
Crie um arquivo chamado `.env` na raiz da pasta `back/` (onde está o arquivo `run.py`):

```env
SECRET_KEY=sua_chave_secreta_aqui
JWT_SECRET_KEY=sua_chave_jwt_aqui
DATABASE_URL=sqlite:///site.db
FLASK_RUN_HOST=127.0.0.1
FLASK_RUN_PORT=5000
GEMINI_API_KEY=chave_api_gemini_aqui
GOOGLE_CLOUD_PROJECT=id_do_projeto_google_cloud
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=True
GCS_BUCKET_NAME=nome_bucket_farmacia
GCS_BUCKET_PATH=processos
GCS_PROJECT_ID=id_projeto_google_cloud
GOOGLE_APPLICATION_CREDENTIALS=caminho_chave_conta_de_servico
GCS_BUCKET_KNOWLEDGE_BASE=base_conhecimento
```

### 5. Iniciar a Aplicação
```bash
python run.py
```
A API estará disponível em `http://127.0.0.1:5000`.

Para permitir acesso de outro dispositivo na mesma rede, execute com:

```bash
FLASK_RUN_HOST=0.0.0.0 FLASK_RUN_PORT=5000 python run.py
```

Nesse modo, a API também fica acessível pelo IP da máquina, por exemplo `http://192.168.68.119:5000`.

---

## 📑 Documentação da API

### Serviço de Resumo Técnico (`/api/resumo`)

A documentação de uso do serviço de resumo técnico, com instruções de execução e exemplos de payloads de entrada e saída, está disponível em:

- [`docs/USO_SERVICO_RESUMO.md`](docs/USO_SERVICO_RESUMO.md)

### Autenticação (`/auth`)
| Método | Endpoint | Descrição |
| :--- | :--- | :--- |
| `POST` | `/auth/register` | Cadastro de novos usuários |
| `POST` | `/auth/login` | Login e geração de tokens JWT |
| `POST` | `/auth/refresh` | Renovação do token de acesso |
| `GET` | `/auth/protected` | Rota de teste para validar token |

### Gerenciamento de Usuários (`/users`)
*Acesso restrito a usuários com perfil `admin`.*

| Método | Endpoint | Descrição |
| :--- | :--- | :--- |
| `GET` | `/users/` | Lista todos os usuários cadastrados |
| `POST` | `/users/create` | Criação manual de usuários por admin |
| `DELETE` | `/users/<id>` | Exclusão de um usuário específico |

---

## 🧪 Executando Testes

O projeto conta com uma suíte de testes automatizados que validam os fluxos de autenticação e permissões.

```bash
# Certifique-se de estar com o venv ativo
pytest
```
*Nota: Os testes utilizam um banco de dados SQLite em memória (`sqlite:///:memory:`) para não interferir nos dados locais.*

---

## 👤 Usuário Inicial (Seed)
Ao rodar a aplicação pela primeira vez, o sistema cria automaticamente:
- **Roles**: `admin` e `analyst`.
- **Admin Padrão**:
  - **Usuário**: `admin`
  - **Senha**: `admin_password`
  - **Email**: `admin@example.com`

> [!IMPORTANT]
> Recomenda-se alterar a senha do usuário administrador logo após o primeiro acesso.
