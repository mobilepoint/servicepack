# pages/98_🏪_eMAG_PL_Reconciliation.py

import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

from sidebar import rendersidebar
from authsimple import checkpassword


st.set_page_config(
    page_title="eMAG P&L Reconciliation",
    page_icon="🏪",
    layout="wide"
)

if not checkpassword():
    st.stop()

rendersidebar()


# ─────────────────────────────────────────────
# Conexiune DB
# ─────────────────────────────────────────────
def get_db_connection():
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        return conn
    except Exception as e:
        st.error(f"Nu mă pot conecta la baza de date: {e}")
        return None


st.title("🏪 eMAG P&L Reconciliation")
tab1, tab2, tab3 = st.tabs(["📤 Upload P&L", "📊 Dashboard", "💰 Reconciliere Avize"])


# ─────────────────────────────────────────────
# TAB 1: doar placeholder (poți păstra ce aveai)
# ─────────────────────────────────────────────
with tab1:
    st.write("Tab 1 – upload P&L (păstrează aici codul tău care mergea deja).")


# ─────────────────────────────────────────────
# TAB 2: Dashboard simplu bazat pe tabela emag_order_lines
# ─────────────────────────────────────────────
with tab2:
    st.header("📊 Dashboard eMAG (simplu)")

    conn = get_db_connection()
    if not conn:
        st.stop()

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT
                COUNT(*) AS total_linii,
                COUNT(DISTINCT id_comanda) AS comenzi_unice,
                ROUND(SUM(vanzari), 2)        AS total_vanzari,
                ROUND(SUM(comision), 2)       AS total_comision,
                ROUND(SUM(vanzari_nete), 2)   AS total_vanzari_nete,
                ROUND(SUM(profit_net), 2)     AS total_profit
            FROM emag_order_lines
        """)
        stats = cursor.fetchone()

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("💰 Total vânzări", f"{stats['total_vanzari'] or 0:,.2f} RON")
        col2.metric("💸 Comisioane", f"{stats['total_comision'] or 0:,.2f} RON")
        col3.metric("📦 Vânzări nete", f"{stats['total_vanzari_nete'] or 0:,.2f} RON")
        col4.metric("✨ Profit net", f"{stats['total_profit'] or 0:,.2f} RON")

        st.divider()

        cursor.execute("""
            SELECT
                data,
                id_comanda,
                sku,
                produs,
                cantitate,
                ROUND(vanzari, 2)      AS vanzari,
                ROUND(comision, 2)     AS comision,
                ROUND(vanzari_nete, 2) AS vanzari_nete,
                ROUND(profit_net, 2)   AS profit_net
            FROM emag_order_lines
            ORDER BY data DESC
            LIMIT 200
        """)
        rows = cursor.fetchall()
        df = pd.DataFrame(rows)
        if not df.empty:
            df["data"] = pd.to_datetime(df["data"]).dt.strftime("%d/%m/%Y")
            st.dataframe(df, use_container_width=True, height=600)
        else:
            st.info("Nu există linii în emag_order_lines încă.")

        cursor.close()
        conn.close()
    except Exception as e:
        st.error(f"Eroare la încărcarea dashboard-ului: {e}")


# ─────────────────────────────────────────────
# TAB 3: deocamdată doar info (fără cod nou)
# ─────────────────────────────────────────────
with tab3:
    st.header("💰 Reconciliere Avize")
    st.info(
        "Versiune simplificată. Avem tabelele `emag_payout_notices` și "
        "`emag_invoices` în DB, dar logica de reconciliere automată prin API "
        "o adăugăm după ce clarificăm exact ce rapoarte oferă eMAG."
    )
