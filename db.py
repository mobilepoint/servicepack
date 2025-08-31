from __future__ import annotations
from typing import Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime

DB_PATH = "sqlite:///servicepack.db"

Base = declarative_base()
engine = create_engine(DB_PATH, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    code = Column(String, index=True)
    name = Column(Text)
    purchase_price = Column(Float)     # Pret achizitie
    sale_price = Column(Float)         # Pret vanzare
    profit_abs = Column(Float)         # Profit absolut din fisier, daca exista
    sale_price_minus20 = Column(Float)
    profit_minus20 = Column(Float)
    competitor_price = Column(Float)   # Pret concurenta (manual / import separat)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("code", name="uq_products_code"),)

class StockMove(Base):
    __tablename__ = "stock_moves"
    id = Column(Integer, primary_key=True)
    product_code = Column(String, index=True)   # legam pe cod
    date = Column(Date, index=True)
    qty = Column(Float)                         # pozitiv intrari, negativ iesiri
    source = Column(String)                     # ex: SmartBill, manual

class Inventory(Base):
    __tablename__ = "inventory"
    id = Column(Integer, primary_key=True)
    product_code = Column(String, index=True)
    on_hand = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(engine)
    return engine

def ensure_db():
    init_db()
    return SessionLocal()