from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from app.config import Config
from app.models import db, bcrypt, User, Role

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    bcrypt.init_app(app)
    jwt = JWTManager(app)
    migrate = Migrate(app, db)

    from app.routes.auth import auth_bp
    from app.routes.users import users_bp
    from app.routes.gemini import gemini_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(gemini_bp)

    CORS(app)

    # Criação inicial de perfis e um usuário admin se não existirem
    with app.app_context():
        db.create_all() # Cria as tabelas se não existirem

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

    return app
