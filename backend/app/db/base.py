"""所有 ORM 模型共用的 SQLAlchemy 声明基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 数据模型的共同基类，Alembic 从它读取目标数据库结构。"""
