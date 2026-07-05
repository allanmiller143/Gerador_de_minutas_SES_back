from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from sqlalchemy import inspect, text
from app.config import Config
from app.models import db, bcrypt, User, Role, ProcessoSEI


# def _ensure_runtime_schema_columns():
#     """Garante colunas novas quando o SQLite local já tinha sido criado via create_all."""
#     inspector = inspect(db.engine)

#     with db.engine.begin() as connection:
#         if inspector.has_table('resumo_batch_runs'):
#             resumo_batch_columns = {column['name'] for column in inspector.get_columns('resumo_batch_runs')}
#             if 'logs_json' not in resumo_batch_columns:
#                 connection.execute(text("ALTER TABLE resumo_batch_runs ADD COLUMN logs_json TEXT NOT NULL DEFAULT '[]'"))

#         if inspector.has_table('processos_sei'):
#             processo_columns = {column['name'] for column in inspector.get_columns('processos_sei')}
#             if 'partes' not in processo_columns:
#                 connection.execute(text("ALTER TABLE processos_sei ADD COLUMN partes VARCHAR(255)"))
#             if 'resumo' not in processo_columns:
#                 connection.execute(text("ALTER TABLE processos_sei ADD COLUMN resumo TEXT"))
#             if 'foi_alterado' not in processo_columns:
#                 connection.execute(text("ALTER TABLE processos_sei ADD COLUMN foi_alterado BOOLEAN NOT NULL DEFAULT 0"))
#             if 'prioridade_original' not in processo_columns:
#                 connection.execute(text("ALTER TABLE processos_sei ADD COLUMN prioridade_original VARCHAR(50)"))

def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    bcrypt.init_app(app)
    jwt = JWTManager(app)
    migrate = Migrate(app, db)
    CORS(app)

    from app.routes.auth import auth_bp
    from app.routes.users import users_bp
    from app.routes.gemini import gemini_bp
    from app.routes.processos import processos_bp
    from app.routes.resumo import resumo_bp
    from app.routes.mock_data import mock_data_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(gemini_bp)
    app.register_blueprint(processos_bp)
    app.register_blueprint(resumo_bp)
    app.register_blueprint(mock_data_bp)

    @app.before_request
    def run_due_resumo_batch():
        if app.config.get("TESTING"):
            return None
        from app.routes.mock_data import execute_due_resumo_batch

        execute_due_resumo_batch()
        return None

    # Criação inicial de perfis e um usuário admin se não existirem
    with app.app_context():
        # Iniciar worker de análise em background
        from app.routes.processos import start_worker_thread
        start_worker_thread()

        db.create_all() # Cria as tabelas se não existirem
        # _ensure_runtime_schema_columns()

        inspector = inspect(db.engine)
        if inspector.has_table('roles') and inspector.has_table('users'):
            if not Role.query.filter_by(name='admin').first():
                admin_role = Role(name='admin')
                db.session.add(admin_role)
                db.session.commit()

            if not Role.query.filter_by(name='analyst').first():
                analyst_role = Role(name='analyst')
                db.session.add(analyst_role)
                db.session.commit()

            # Exemplo: Cria um usuário admin inicial se não existir
            if not User.query.filter_by(username='admin').first():
                admin_role = Role.query.filter_by(name='admin').first()
                if admin_role:
                    admin_user = User(username='admin', email='admin@example.com', password='admin_password', roles=[admin_role])
                    db.session.add(admin_user)
                    db.session.commit()
                    print("Usuário 'admin' criado com senha 'admin_password'. Por favor, altere a senha!")

        # Cria um ProcessoSEI inicial se não existir
        # if not ProcessoSEI.query.filter_by(numero='0002345-67.2024.8.26.0053').first():
        #     processo_inicial = ProcessoSEI(
        #         numero= "0002345-67.2024.8.26.0053",
        #         assunto= "Fornecimento de medicamento",
        #         prioridade= "Média",
        #         status= "Pré-análise",
        #         partes= "Maria Souza x Estado de SP",
        #         resumo= "Solicitação de medicamento constante na RENAME. Verificar disponibilidade na rede SUS.",
        #         iaConfidence= 0.88,
        #         iaSugestao= "Orientar protocolo SUS – medicamento disponível na rede. Improcedência por ausência de recusa administrativa."
        #     )
        #     db.session.add(processo_inicial)
        #     db.session.commit()
        #     print("ProcessoSEI inicial criado no banco de dados!")

    return app
