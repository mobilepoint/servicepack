import streamlit as st
from sidebar import render_sidebar
from auth_simple import check_password

# ===== CONFIGURARE PAGINĂ =====
st.set_page_config(
    page_title="🏠 Dashboard",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== AUTENTIFICARE =====
if not check_password():
    st.stop()

# ===== AFIȘEAZĂ SIDEBAR =====
render_sidebar()

# ===== PAGINA PRINCIPALĂ =====
st.title("🏠 Dashboard Central - Sistem ERP Magazin")
st.markdown("**Centrul tău de comandă pentru management integrat WooCommerce, SmartBill și FoneDay**")
st.divider()

# Afișare metrici generale
col1, col2, col3, col4 = st.columns(4)

from sidebar import check_all_connections
connection_status = check_all_connections()
total_connections = sum(1 for conn in ["postgresql", "woocommerce", "smartbill", "foneday"] 
                       if connection_status[conn]["status"])

with col1:
    st.metric(
        label="🔌 Conexiuni Active",
        value=f"{total_connections}/4",
        delta="Toate funcționale" if total_connections == 4 else f"{4-total_connections} inactive"
    )

with col2:
    status_emoji = "✅ Activ" if total_connections == 4 else "⚠️ Parțial" if total_connections > 0 else "❌ Inactiv"
    st.metric(
        label="📊 Status General",
        value=status_emoji
    )

with col3:
    st.metric(
        label="📦 Module Disponibile",
        value="6",
        delta="Comenzi, Produse, Clienți, FoneDay+"
    )

with col4:
    from sidebar import check_table_timestamps
    tables = check_table_timestamps()
    active_tables = sum(1 for t in tables.values() if t["status"] == "✓")
    st.metric(
        label="📊 Tabele Sincronizate",
        value=f"{active_tables}/4"
    )

st.divider()



# === FOOTER ===
col1, col2 = st.columns([3, 1])
with col1:
    st.caption("💡 **Tip**: Folosește butonul '🔄 Actualizează' din sidebar pentru a reîmprospăta conexiunile.")
with col2:
    st.caption("🏠 Dashboard Central v1.0")
