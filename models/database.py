"""
Database models for the Telegram SSH Bot
"""
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from cryptography.fernet import Fernet
import os
import base64
import hashlib

Base = declarative_base()


def get_encryption_key():
    secret = os.getenv("SECRET_KEY", "default_secret_key_change_me_now!")
    key = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_data(data: str) -> str:
    if not data:
        return data
    f = Fernet(get_encryption_key())
    return f.encrypt(data.encode()).decode()


def decrypt_data(data: str) -> str:
    if not data:
        return data
    try:
        f = Fernet(get_encryption_key())
        return f.decrypt(data.encode()).decode()
    except Exception:
        return data


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String(100))
    first_name = Column(String(100))
    is_banned = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now(), onupdate=func.now())

    servers = relationship("SavedServer", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("SSHSession", back_populates="user", cascade="all, delete-orphan")


class SavedServer(Base):
    __tablename__ = "saved_servers"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    label = Column(String(100), nullable=False)
    host = Column(String(255), nullable=False)
    port = Column(Integer, default=22)
    ssh_username = Column(String(100), nullable=False)
    auth_type = Column(String(20), default="password")  # password, key, key_passphrase
    _password = Column("password", String(500))
    _private_key = Column("private_key", Text)
    _key_passphrase = Column("key_passphrase", String(500))
    keep_alive = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="servers")

    @property
    def password(self):
        return decrypt_data(self._password)

    @password.setter
    def password(self, value):
        self._password = encrypt_data(value)

    @property
    def private_key(self):
        return decrypt_data(self._private_key)

    @private_key.setter
    def private_key(self, value):
        self._private_key = encrypt_data(value)

    @property
    def key_passphrase(self):
        return decrypt_data(self._key_passphrase)

    @key_passphrase.setter
    def key_passphrase(self, value):
        self._key_passphrase = encrypt_data(value)


class SSHSession(Base):
    __tablename__ = "ssh_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    server_id = Column(Integer, ForeignKey("saved_servers.id"), nullable=True)
    host = Column(String(255))
    ssh_username = Column(String(100))
    port = Column(Integer, default=22)
    connected_at = Column(DateTime, server_default=func.now())
    disconnected_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    commands_count = Column(Integer, default=0)
    disconnect_reason = Column(String(255), nullable=True)

    user = relationship("User", back_populates="sessions")


def get_engine():
    db_url = os.getenv("DATABASE_URL", "sqlite:///data/bot.db")
    return create_engine(db_url, connect_args={"check_same_thread": False} if "sqlite" in db_url else {})


def get_session():
    engine = get_engine()
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def init_db():
    engine = get_engine()
    Base.metadata.create_all(engine)
