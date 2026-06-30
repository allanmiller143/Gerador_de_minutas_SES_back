from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime, date

db = SQLAlchemy()
bcrypt = Bcrypt()

# Tabela de associação para User-Role (muitos-para-muitos)
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
    __tablename__ = 'processos_sei'
    
    id = db.Column(db.Integer, primary_key=True) 
    numero = db.Column(db.String(50), unique=True, nullable=False)
    assunto = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), nullable=False) 
    dataRecebimento = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    prioridade = db.Column(db.String(50), nullable=False)
    resumo = db.Column(db.Text, nullable=True)
    partes = db.Column(db.Text, nullable=True)
    arquivoPdf = db.Column(db.String(255), nullable=True)
    iaConfidence = db.Column(db.Float, nullable=False, default=0.0)
    analista = db.Column(db.String(100), nullable=True) 
    dataRevisao = db.Column(db.DateTime, nullable=True) 
    dataPreAnalise = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    iaSugestao = db.Column(db.Text, nullable=True)
    jurisprudenciasSugeridas = db.Column(db.JSON, nullable=False, default=list)

    @staticmethod
    def _normalize_datetime(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if isinstance(value, str):
            formats = [
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y"
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(value, fmt)
                except ValueError:
                    continue
            raise ValueError(
                "Invalid datetime string format for ProcessoSEI field: %r" % value
            )
        raise TypeError(
            "Invalid type for datetime field, expected datetime/date/str, got %s" % type(value)
        )

    def __init__(
        self,
        numero,
        assunto,
        status,
        prioridade,
        analista=None,
        iaSugestao=None,
        jurisprudenciasSugeridas=None,
        resumo=None,
        partes=None,
        iaConfidence=None,
        dataRecebimento=None,
        dataPreAnalise=None,
        dataRevisao=None,
        arquivoPdf=None,
    ):
        self.numero = numero
        self.assunto = assunto
        self.status = status
        self.prioridade = prioridade
        self.analista = analista
        self.iaSugestao = iaSugestao
        self.jurisprudenciasSugeridas = jurisprudenciasSugeridas if jurisprudenciasSugeridas is not None else []
        self.resumo = resumo
        self.partes = partes
        self.arquivoPdf = arquivoPdf
        if iaConfidence is not None:
            self.iaConfidence = iaConfidence
        if dataRecebimento is not None:
            self.dataRecebimento = self._normalize_datetime(dataRecebimento)
        if dataPreAnalise is not None:
            self.dataPreAnalise = self._normalize_datetime(dataPreAnalise)
        if dataRevisao is not None:
            self.dataRevisao = self._normalize_datetime(dataRevisao)

    def __repr__(self):
        return f'<ProcessoSEI {self.numero}>'