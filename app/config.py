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
    SEI_USER = os.environ.get('SEI_USER')
    SEI_PASS = os.environ.get('SEI_PASS')
    SEI_ORGAO = os.environ.get('SEI_ORGAO')
    SEI_URL_LOGIN = os.environ.get('SEI_URL_LOGIN')
    SEI_TIMEOUT_MS = int(os.environ.get('SEI_TIMEOUT_MS', 60000))
    SEI_MAX_TENTATIVAS = int(os.environ.get('SEI_MAX_TENTATIVAS', 2))
    HEADLESS = os.environ.get('HEADLESS', 'true').lower() in {'1', 'true', 'yes', 'y'}
