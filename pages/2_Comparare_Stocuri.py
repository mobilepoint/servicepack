# pages/2_Comparare_Stocuri.py

import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from sidebar import render_sidebar
from auth_simple import check_password

# AUTENTIFICARE
if not check_password():
    st.stop()

# SIDEBAR
render_sidebar()

st.set_page_config(page_title="Comparare Stocuri WooCommerce & SmartBill", layout="wide")
st.title("📊 Comparare Stocuri: WooCommerce ↔ SmartBill")

# =========================
# HELPER FUNCTIONS
# =========================

@st.cache_resource
def get_pg_connection_string():
    return st.secrets["connections"]["postgresql"]["url"]

def get_db_connection():
    return psycopg2.connect(get_pg_connection_string())

@st.cache_data(ttl=60)
def get_stock_comparison_stats():
    """Obține statistici despre compararea stocurilor"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM get_stock_comparison_stats()")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(result) if result else None
    except Exception as e:
        st.error(f"❌ Eroare citire statistici: {e}")
        return None

@st.cache_data(ttl=60)
def get_products_to_remove():
    """Produse în WooCommerce dar NU în SmartBill"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM v_stock_to_remove ORDER BY sku")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(row) for row in results]
    except Exception as e:
        st.error(f"❌ Eroare: {e}")
        return []

@st.cache_data(ttl=60)
def get_products_to_add():
    """Produse în SmartBill dar NU în WooCommerce (exclud ignorate)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM v_st
