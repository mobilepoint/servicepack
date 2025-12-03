import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

st.set_page_config(
    page_title="eMAG P&L Reconciliation",
    page_icon="🏪",
    layout="wide"
)

# ═══════════════════════════════════════════════════════
# FUNCȚIE CONEXIUNE DB
# ═══════════════════════════════════════════════════════

def get_db_connection():
    """Conectare la PostgreSQL/Supabase"""
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        return conn
    except Exception as e:
        st.error(f"❌ Nu mă pot conecta la baza de date: {e}")
        return None

# ═══════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════

st.title("🏪 eMAG P&L Reconciliation")
st.caption("Reconcilierea rapoartelor Profit & Loss cu facturile și avizele de plată")

# ═══════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════

tab1, tab2, tab3 = st.tabs([
    "📤 Upload P&L", 
    "📊 Dashboard", 
    "💰 Reconciliere Avize"
])

# ═══════════════════════════════════════════════════════
# TAB 1: UPLOAD P&L
# ═══════════════════════════════════════════════════════

with tab1:
    st.header("📤 Upload raport P&L eMAG")
    
    st.info("""
    **Pași:**
    1. Descarcă raportul P&L (Profit & Loss) din eMAG Marketplace
    2. Upload fișierul Excel aici
    3. Datele vor fi procesate și salvate în baza de date
    """)
    
    uploaded_file = st.file_uploader(
        "Selectează raportul P&L (Excel)",
        type=['xlsx', 'xls'],
        help="Raportul descărcat din eMAG Marketplace > Reports > Profit & Loss"
    )
    
    if uploaded_file:
        st.success(f"✅ Fișier încărcat: **{uploaded_file.name}**")
        
        try:
            # Citește Excel
            df = pd.read_excel(uploaded_file)
            
            st.write(f"**Rânduri găsite:** {len(df)}")
            
            # Afișează preview
            with st.expander("👁️ Preview date (primele 10 rânduri)"):
                st.dataframe(df.head(10))
            
            # Afișează coloanele
            with st.expander("📋 Coloane disponibile"):
                st.write(list(df.columns))
            
            # Buton procesare (doar placeholder pentru moment)
            if st.button("🚀 Procesează și salvează în DB", type="primary"):
                st.warning("⚠️ Funcția de import automată va fi adăugată în următoarea versiune.")
                st.info("Pentru moment, verifică că datele sunt corecte în preview.")
                
        except Exception as e:
            st.error(f"❌ Eroare la citirea fișierului: {e}")

# ═══════════════════════════════════════════════════════
# TAB 2: DASHBOARD
# ═══════════════════════════════════════════════════════

with tab2:
    st.header("📊 Dashboard eMAG - Vânzări și Profit")
    
    conn = get_db_connection()
    if not conn:
        st.stop()
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Statistici generale
        cursor.execute("""
            SELECT
                COUNT(*) AS total_linii,
                COUNT(DISTINCT id_comanda) AS comenzi_unice,
                ROUND(SUM(vanzari), 2)        AS total_vanzari,
                ROUND(SUM(comision), 2)       AS total_comision,
                ROUND(SUM(vanzari_nete), 2)   AS total_vanzari_nete,
                ROUND(SUM(COALESCE(profit_net, 0)), 2) AS total_profit
            FROM emag_order_lines
        """)
        stats = cursor.fetchone()
        
        # Metrici principale
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "💰 Total vânzări", 
                f"{stats['total_vanzari'] or 0:,.2f} RON"
            )
        
        with col2:
            st.metric(
                "💸 Comisioane eMAG", 
                f"{stats['total_comision'] or 0:,.2f} RON",
                delta=f"-{(stats['total_comision'] or 0) / (stats['total_vanzari'] or 1) * 100:.1f}%",
                delta_color="inverse"
            )
        
        with col3:
            st.metric(
                "📦 Vânzări nete", 
                f"{stats['total_vanzari_nete'] or 0:,.2f} RON"
            )
        
        with col4:
            profit = stats['total_profit'] or 0
            marja = (profit / (stats['total_vanzari'] or 1) * 100) if stats['total_vanzari'] else 0
            st.metric(
                "✨ Profit net", 
                f"{profit:,.2f} RON",
                delta=f"{marja:.1f}% marjă"
            )
        
        st.divider()
        
        # Info suplimentară
        col1, col2 = st.columns(2)
        with col1:
            st.metric("📦 Total comenzi", f"{stats['comenzi_unice'] or 0:,}")
        with col2:
            st.metric("📋 Total linii", f"{stats['total_linii'] or 0:,}")
        
        st.divider()
        
        # Tabel comenzi
        st.subheader("📋 Comenzi recente")
        
        cursor.execute("""
            SELECT
                data,
                id_comanda,
                sku,
                LEFT(produs, 50) AS produs,
                cantitate,
                ROUND(vanzari, 2)      AS vanzari,
                ROUND(comision, 2)     AS comision,
                ROUND(vanzari_nete, 2) AS vanzari_nete,
                ROUND(COALESCE(profit_net, 0), 2) AS profit_net
            FROM emag_order_lines
            ORDER BY data DESC
            LIMIT 200
        """)
        
        rows = cursor.fetchall()
        
        if rows:
            df = pd.DataFrame(rows)
            df['data'] = pd.to_datetime(df['data']).dt.strftime('%d/%m/%Y')
            
            st.dataframe(
                df,
                use_container_width=True,
                height=600,
                column_config={
                    "data": st.column_config.TextColumn("Data", width="small"),
                    "id_comanda": st.column_config.NumberColumn("ID Comandă", format="%d"),
                    "sku": st.column_config.TextColumn("SKU", width="medium"),
                    "produs": st.column_config.TextColumn("Produs", width="large"),
                    "cantitate": st.column_config.NumberColumn("Cant.", format="%d"),
                    "vanzari": st.column_config.NumberColumn("Vânzări", format="%.2f RON"),
                    "comision": st.column_config.NumberColumn("Comision", format="%.2f RON"),
                    "vanzari_nete": st.column_config.NumberColumn("Vânzări Nete", format="%.2f RON"),
                    "profit_net": st.column_config.NumberColumn("Profit Net", format="%.2f RON"),
                }
            )
            
            st.caption(f"📊 Afișate ultimele 200 comenzi din {stats['total_linii']} total")
        else:
            st.info("📭 Nu există date în tabelul `emag_order_lines`.")
            st.caption("Upload un raport P&L în Tab 1 pentru a vedea datele aici.")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        st.error(f"❌ Eroare la încărcarea dashboard-ului: {e}")
        st.exception(e)
        if conn:
            conn.close()

# ═══════════════════════════════════════════════════════
# TAB 3: RECONCILIERE AVIZE
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("💰 Reconciliere Avize de Plată")
    
    st.info("""
    **Status:** În dezvoltare
    
    Funcționalități planificate:
    - ✅ Tabele create în DB (`emag_payout_notices`, `emag_invoices`)
    - 🔄 Sincronizare factури prin eMAG API (în implementare)
    - 🔄 Upload și validare avize PDF
    - 🔄 Reconciliere automată
    
    **Următorii pași:**
    1. Testare eMAG Invoice API pentru a vedea ce rapoarte sunt disponibile
    2. Sincronizare periodică a facturilor
    3. Implementare workflow reconciliere
    """)
    
    # Verifică dacă tabelele există
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Check emag_payout_notices
            cursor.execute("""
                SELECT COUNT(*) AS cnt 
                FROM emag_payout_notices
            """)
            payout_count = cursor.fetchone()['cnt']
            
            # Check emag_invoices
            cursor.execute("""
                SELECT COUNT(*) AS cnt 
                FROM emag_invoices
            """)
            invoice_count = cursor.fetchone()['cnt']
            
            st.divider()
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("📄 Avize salvate", payout_count)
            with col2:
                st.metric("🧾 Facturi salvate", invoice_count)
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            st.warning(f"⚠️ Nu pot verifica tabelele: {e}")
            if conn:
                conn.close()

# ═══════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════

st.divider()
st.caption(f"📅 Ultima actualizare: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
