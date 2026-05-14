import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'uma-chave-secreta-padrao-muito-segura'
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or 'uma-chave-jwt-padrao-muito-segura'
    # Usando SQLite para facilitar o teste no ambiente sandbox. 
    # Para PostgreSQL, altere para: 'postgresql://user:password@localhost:5432/saude_db'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///site.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_ACCESS_TOKEN_EXPIRES = 3600 # 1 hora
    JWT_REFRESH_TOKEN_EXPIRES = 2592000 # 30 dias
