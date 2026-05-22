import json
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt

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


def utcnow():
    return datetime.now(timezone.utc)


class ResumoBatchRun(db.Model):
    __tablename__ = 'resumo_batch_runs'

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(30), nullable=False, default="running")
    trigger_type = db.Column(db.String(30), nullable=False, default="manual")
    triggered_by = db.Column(db.String(120), nullable=False, default="sistema")
    started_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    total_seis = db.Column(db.Integer, nullable=False, default=0)
    generated_count = db.Column(db.Integer, nullable=False, default=0)
    failed_count = db.Column(db.Integer, nullable=False, default=0)
    sei_ids_json = db.Column(db.Text, nullable=False, default="[]")
    error_message = db.Column(db.Text, nullable=True)

    versions = db.relationship('ResumoTecnicoVersion', backref='batch_run', lazy=True)

    @property
    def sei_ids(self):
        return json.loads(self.sei_ids_json or "[]")

    @sei_ids.setter
    def sei_ids(self, value):
        self.sei_ids_json = json.dumps(value or [], ensure_ascii=False)

    def finish(self, status="success", error_message=None):
        self.status = status
        self.finished_at = utcnow()
        self.error_message = error_message
        if self.started_at and self.finished_at:
            self.duration_seconds = max(0, int((self.finished_at - self.started_at).total_seconds()))

    def to_dict(self):
        return {
            "id": self.id,
            "status": self.status,
            "trigger_type": self.trigger_type,
            "triggered_by": self.triggered_by,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "total_seis": self.total_seis,
            "generated_count": self.generated_count,
            "failed_count": self.failed_count,
            "sei_ids": self.sei_ids,
            "error_message": self.error_message,
        }


class ResumoTecnicoVersion(db.Model):
    __tablename__ = 'resumo_tecnico_versions'

    id = db.Column(db.Integer, primary_key=True)
    sei_id = db.Column(db.String(40), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    minuta = db.Column(db.Text, nullable=False, default="")
    generated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    generated_by = db.Column(db.String(120), nullable=False, default="sistema")
    source = db.Column(db.String(30), nullable=False, default="manual")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    batch_run_id = db.Column(db.Integer, db.ForeignKey('resumo_batch_runs.id'), nullable=True)

    @property
    def payload(self):
        return json.loads(self.payload_json or "{}")

    @payload.setter
    def payload(self, value):
        self.payload_json = json.dumps(value or {}, ensure_ascii=False)

    @classmethod
    def create_new(cls, sei_id, payload, minuta, generated_by="sistema", source="manual", batch_run_id=None):
        latest = cls.query.filter_by(sei_id=sei_id).order_by(cls.version.desc()).first()
        cls.query.filter_by(sei_id=sei_id, is_active=True).update({"is_active": False})
        item = cls(
            sei_id=str(sei_id),
            version=(latest.version if latest else 0) + 1,
            minuta=minuta or "",
            generated_by=generated_by or "sistema",
            source=source,
            is_active=True,
            batch_run_id=batch_run_id,
        )
        item.payload = payload
        db.session.add(item)
        db.session.flush()
        return item

    def to_dict(self):
        return {
            "id": self.id,
            "sei_id": self.sei_id,
            "version": self.version,
            "resumoTecnico": self.payload,
            "minuta": self.minuta,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "generated_by": self.generated_by,
            "source": self.source,
            "is_active": self.is_active,
            "batch_run_id": self.batch_run_id,
        }


class ResumoReexecutionRequest(db.Model):
    __tablename__ = 'resumo_reexecution_requests'

    id = db.Column(db.Integer, primary_key=True)
    sei_id = db.Column(db.String(40), nullable=False, index=True)
    requested_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    requested_by = db.Column(db.String(120), nullable=False, default="sistema")
    status = db.Column(db.String(30), nullable=False, default="pending")
    fulfilled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "sei_id": self.sei_id,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
            "requested_by": self.requested_by,
            "status": self.status,
            "fulfilled_at": self.fulfilled_at.isoformat() if self.fulfilled_at else None,
        }


class ResumoBatchSchedule(db.Model):
    __tablename__ = 'resumo_batch_schedules'

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    time = db.Column(db.String(5), nullable=False, default="03:00")
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_by = db.Column(db.String(120), nullable=False, default="sistema")
    last_run_date = db.Column(db.String(10), nullable=True)

    @classmethod
    def singleton(cls):
        schedule = cls.query.first()
        if not schedule:
            schedule = cls()
            db.session.add(schedule)
            db.session.flush()
        return schedule

    def to_dict(self):
        return {
            "id": self.id,
            "enabled": self.enabled,
            "time": self.time,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by,
            "last_run_date": self.last_run_date,
        }
