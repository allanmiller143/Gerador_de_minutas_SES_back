from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime

db = SQLAlchemy()
bcrypt = Bcrypt()

user_roles = db.Table(
    'user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('roles.id'), primary_key=True)
)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    roles = db.relationship('Role', secondary=user_roles, lazy='subquery',
                            backref=db.backref('users', lazy=True))

    def __init__(self, username, email, password, roles=None):
        self.username = username
        self.email = email
        self.password = bcrypt.generate_password_hash(password).decode('utf-8')
        if roles is not None:
            self.roles = roles

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password, password)

    def has_role(self, role_name):
        return any(role.name == role_name for role in self.roles)

    def __repr__(self):
        return f'<User {self.username}>'

class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f'<Role {self.name}>'

class ProcessoSEI(db.Model):
    # Representa um processo SEI recebido pela farmácia
    __tablename__ = 'processo_sei'

    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50), unique=True, nullable=False)
    assunto = db.Column(db.String(255), nullable=False)
    partes = db.Column(db.String(255))
    resumo = db.Column(db.Text)

    status = db.Column(
        db.Enum('Pré-análise', 'Em revisão', 'Concluído', name='status_processo'),
        nullable=False,
        default='Pré-análise'
    )
    prioridade = db.Column(
        db.Enum('Alta', 'Média', 'Baixa', name='prioridade_processo'),
        nullable=False,
        default='Média'
    )

    data_recebimento = db.Column(db.Date, nullable=False)
    data_pre_analise = db.Column(db.Date)
    data_revisao = db.Column(db.Date)

    # Campos gerados pela IA
    ia_confidence = db.Column(db.Float, default=0.0)
    ia_sugestao = db.Column(db.Text)

    def to_dict(self):
        # Transforma o processo em JSON
        return {
            'id': str(self.id),
            'numero': self.numero,
            'assunto': self.assunto,
            'partes': self.partes,
            'resumo': self.resumo,
            'status': self.status,
            'prioridade': self.prioridade,
            'dataRecebimento': self.data_recebimento.strftime('%d/%m/%Y') if self.data_recebimento else None,
            'dataPreAnalise': self.data_pre_analise.strftime('%d/%m/%Y') if self.data_pre_analise else None,
            'dataRevisao': self.data_revisao.strftime('%d/%m/%Y') if self.data_revisao else None,
            'iaConfidence': self.ia_confidence,
            'iaSugestao': self.ia_sugestao or '',
        }

    def __repr__(self):
        return f'<ProcessoSEI {self.numero}>'