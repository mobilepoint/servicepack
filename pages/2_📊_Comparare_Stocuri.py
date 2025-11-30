# pages/2_📊_Comparare_Stocuri.py

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
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM get_stock_comparison_stats()")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return dict(result) if result else None
    except Exception as e:
        st.error(f"Eroare citire statistici: {e}")
        return None

@st.cache_data(ttl=60)
def get_products_to_remove():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM v_stock_to_remove ORDER BY sku")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(row) for row in results]
    except Exception as e:
        st.error(f"Eroare: {e}")
        return []

@st.cache_data(ttl=60)
def get_products_to_add():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM v_stock_to_add ORDER BY sku")
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(row) for row in results]
    except Exception as e:
        st.error(f"Eroare: {e}")
        return []

@st.cache_data(ttl=60)
def get_ignored_products():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = '''
            SELECT sci.*, p.name as product_name_db
            FROM stock_comparison_ignored sci
            LEFT JOIN product_sku ps ON ps.sku = sci.sku
            LEFT JOIN product p ON p.id = ps.product_id
            ORDER BY sci.ignored_at DESC
        '''
        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return [dict(row) for row in results]
    except Exception as e:
        st.error(f"Eroare: {e}")
        return []

def add_to_ignored(sku, product_name, reason=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = '''
            INSERT INTO stock_comparison_ignored (sku, product_name, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (sku) DO NOTHING
        '''
        cursor.execute(query, (sku, product_name, reason))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare adaugare: {e}")
        return False

def remove_from_ignored(sku):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM stock_comparison_ignored WHERE sku = %s", (sku,))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare stergere: {e}")
        return False

# =========================
# VERIFICARE SINCRONIZARE
# =========================

st.markdown("## 🔍 Verificare Sincronizare Date")

stats = get_stock_comparison_stats()

if stats:
    woo_sync = stats.get('woo_last_sync')
    sb_sync = stats.get('smartbill_last_sync')
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        if woo_sync:
            woo_display = woo_sync.strftime('%d.%m.%Y %H:%M') if isinstance(woo_sync, datetime) else str(woo_sync)
            st.metric("🛒 WooCommerce ultima sync", woo_display)
        else:
            st.metric("🛒 WooCommerce", "Fara date")
    
    with col2:
        if sb_sync:
            sb_display = sb_sync.strftime('%d.%m.%Y %H:%M') if isinstance(sb_sync, datetime) else str(sb_sync)
            st.metric("📦 SmartBill ultima sync", sb_display)
        else:
            st.metric("📦 SmartBill", "Fara date")
    
    with col3:
        if woo_sync and sb_sync:
            if isinstance(woo_sync, datetime) and isinstance(sb_sync, datetime):
                time_diff = abs((woo_sync - sb_sync).total_seconds() / 3600)
                
                if time_diff < 24:
                    st.success(f"Date sincronizate ({time_diff:.1f}h diferenta)")
                elif time_diff < 72:
                    st.warning(f"Date partial dezactualizate ({time_diff:.1f}h diferenta)")
                else:
                    st.error(f"Date dezactualizate ({time_diff/24:.1f} zile diferenta)")
            else:
                st.info("Verificati manual")
        else:
            st.warning("Lipsesc date")
    
    if woo_sync and sb_sync:
        if isinstance(woo_sync, datetime) and isinstance(sb_sync, datetime):
            time_diff = abs((woo_sync - sb_sync).total_seconds() / 3600)
            if time_diff >= 24:
                st.warning("Atentie: Datele nu sunt la zi! Actualizati mai intai WooCommerce si SmartBill")
                
                if st.button("🔄 Reimprospateaza verificarea", type="primary"):
                    st.cache_data.clear()
                    st.rerun()

st.divider()

# =========================
# BUTOANE ACTIUNI
# =========================

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🔄 Actualizeaza Raportul", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

with col2:
    if st.button("📋 Gestioneaza Lista Ignore", use_container_width=True):
        st.session_state['show_ignore_management'] = not st.session_state.get('show_ignore_management', False)
        st.rerun()

with col3:
    if stats:
        st.metric("Produse ignorate", stats.get('ignored_products', 0))

st.divider()

# =========================
# GESTIONARE LISTA IGNORE
# =========================

if st.session_state.get('show_ignore_management', False):
    st.markdown("## 🚫 Gestionare Produse Ignorate")
    
    ignored = get_ignored_products()
    
    if ignored:
        st.info(f"📋 {len(ignored)} produse ignorate")
        
        for item in ignored:
            col1, col2, col3, col4 = st.columns([2, 3, 3, 1])
            
            with col1:
                st.write(f"**{item['sku']}**")
            
            with col2:
                st.write(item.get('product_name_db') or item.get('product_name', 'N/A'))
            
            with col3:
                ignored_at = item.get('ignored_at')
                if ignored_at:
                    if isinstance(ignored_at, datetime):
                        st.caption(f"Ignorat la: {ignored_at.strftime('%d.%m.%Y %H:%M')}")
                    else:
                        st.caption(f"Ignorat la: {ignored_at}")
            
            with col4:
                if st.button("🗑️", key=f"del_{item['sku']}", help="Sterge din ignore"):
                    if remove_from_ignored(item['sku']):
                        st.success(f"{item['sku']} sters din ignore")
                        st.cache_data.clear()
                        st.rerun()
    else:
        st.info("📭 Nu exista produse ignorate")
    
    st.divider()

# =========================
# RAPOARTE - TABURI
# =========================

st.markdown("## 📊 Rapoarte Comparare Stocuri")

if stats:
    col1, col2 = st.columns(2)
    with col1:
        st.metric("🔴 De retras", stats.get('products_to_remove', 0))
    with col2:
        st.metric("🟢 De adaugat", stats.get('products_to_add', 0))

tab1, tab2 = st.tabs(["🔴 De Retras de la Vanzare", "🟢 De Pus la Vanzare"])

# =========================
# TAB 1: DE RETRAS
# =========================

with tab1:
    st.markdown("### Produse in WooCommerce dar NU in SmartBill")
    st.caption("Acestea ar trebui retrase de la vanzare deoarece nu mai sunt disponibile in stoc.")
    
    products_to_remove = get_products_to_remove()
    
    if products_to_remove:
        st.warning(f"{len(products_to_remove)} produse trebuie retrase")
        
        for idx, product in enumerate(products_to_remove, 1):
            with st.expander(f"{idx}. {product['sku']} - {product.get('product_name', 'N/A')}"):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.write(f"**SKU:** {product['sku']}")
                    st.write(f"**Product ID:** {product.get('product_id', 'N/A')}")
                    st.write(f"**Nume:** {product.get('product_name', 'N/A')}")
                
                with col2:
                    st.write(f"**Stoc WooCommerce:** {product.get('woo_stock', 0)}")
                    woo_sync = product.get('woo_last_sync')
                    if woo_sync:
                        if isinstance(woo_sync, datetime):
                            st.write(f"**Ultima sync:** {woo_sync.strftime('%d.%m.%Y %H:%M')}")
                        else:
                            st.write(f"**Ultima sync:** {woo_sync}")
                
                st.info("Actiune recomandata: Dezactivati produsul in WooCommerce sau setati stocul la 0")
    else:
        st.success("Nu exista produse de retras. Toate produsele din WooCommerce sunt in SmartBill.")

# =========================
# TAB 2: DE ADAUGAT
# =========================

with tab2:
    st.markdown("### Produse in SmartBill dar NU in WooCommerce")
    st.caption("Acestea pot fi adaugate la vanzare sau ignorate daca nu sunt destinate magazinului online.")
    
    products_to_add = get_products_to_add()
    
    if products_to_add:
        st.info(f"📦 {len(products_to_add)} produse pot fi adaugate")
        
        for idx, product in enumerate(products_to_add, 1):
            with st.expander(f"{idx}. {product['sku']} - {product.get('product_name', 'N/A')}"):
                col1, col2, col3 = st.columns([3, 3, 2])
                
                with col1:
                    st.write(f"**SKU:** {product['sku']}")
                    st.write(f"**Product ID:** {product.get('product_id', 'N/A')}")
                    st.write(f"**Nume:** {product.get('product_name', 'N/A')}")
                
                with col2:
                    st.write(f"**Stoc SmartBill:** {product.get('smartbill_stock', 0)}")
                    sb_sync = product.get('smartbill_last_sync')
                    if sb_sync:
                        if isinstance(sb_sync, datetime):
                            st.write(f"**Ultima sync:** {sb_sync.strftime('%d.%m.%Y %H:%M')}")
                        else:
                            st.write(f"**Ultima sync:** {sb_sync}")
                
                with col3:
                    st.write("**Actiuni:**")
                    if st.button("🚫 Ignora", key=f"ignore_{product['sku']}", help="Adauga la lista de ignorate"):
                        if add_to_ignored(product['sku'], product.get('product_name', ''), None):
                            st.success(f"{product['sku']} adaugat la ignore")
                            st.cache_data.clear()
                            st.rerun()
                
                st.info("Actiune recomandata: Adaugati produsul in WooCommerce sau ignorati-l")
    else:
        st.success("Nu exista produse de adaugat. Toate produsele din SmartBill sunt in WooCommerce sau sunt ignorate.")

# =========================
# FOOTER
# =========================

st.divider()
st.caption("Nota: Modificati manual produsele in WooCommerce/SmartBill, apoi rulati din nou raportul.")
