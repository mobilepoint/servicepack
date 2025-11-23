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

# === SECȚIUNE QUICK ACTIONS ===
st.markdown("### ⚡ Acțiuni Rapide")

col1, col2, col3 = st.columns(3)

with col1:
    with st.container(border=True):
        st.markdown("#### 🛒 **WooCommerce**")
        st.markdown("Gestionează magazinul online")
        if st.button("📦 Vezi Comenzi", use_container_width=True):
            st.switch_page("pages/1_📦_Comenzi.py")
        if st.button("🏷️ Gestionează Produse", use_container_width=True):
            st.switch_page("pages/2_🏷️_Produse.py")

with col2:
    with st.container(border=True):
        st.markdown("#### 🧾 **SmartBill**")
        st.markdown("Facturare și contabilitate")
        if st.button("📄 Generează Facturi", use_container_width=True):
            st.info("Modul SmartBill în curând...")
        if st.button("📊 Rapoarte Fiscale", use_container_width=True):
            st.info("Modul Rapoarte în curând...")

with col3:
    with st.container(border=True):
        st.markdown("#### 📱 **FoneDay**")
        st.markdown("Gestionare produse telecom")
        if st.button("📞 Catalog FoneDay", use_container_width=True):
            st.info("Modul FoneDay în curând...")
        if st.button("🔄 Sincronizare Stoc", use_container_width=True):
            st.info("Sincronizare în curând...")

st.divider()

# === STATUS DETALIAT CONEXIUNI ===
st.markdown("### 🔍 Status Detaliat Sistem")

if total_connections == 4:
    st.success("✅ **Toate conexiunile sunt active!** Sistemul funcționează optim.")
elif total_connections > 0:
    st.warning("⚠️ **Unele conexiuni au eșuat.** Verifică detaliile în sidebar și corectează credențialele.")
else:
    st.error("❌ **Nicio conexiune activă.** Verifică configurarea secrets în Streamlit Cloud.")
    
    with st.expander("📖 Cum configurez secrets?"):
        st.markdown("""
        **Pași pentru configurare secrets:**
        
        1. Mergi în **Streamlit Cloud Dashboard**
        2. Selectează aplicația ta
        3. Click pe **Settings** → **Secrets**
        4. Adaugă configurația necesară
        5. Click **Save** și reîncarcă aplicația
        
        **Secrets necesare:**
        - `connections.postgresql.url` - Supabase/PostgreSQL
        - `connections.woocommerce.*` - API WooCommerce
        - `connections.smartbill.*` - API SmartBill
        - `FONEDAY_API_URL` și `FONEDAY_API_TOKEN` - API FoneDay
        """)

st.divider()

# === FOOTER ===
col1, col2 = st.columns([3, 1])
with col1:
    st.caption("💡 **Tip**: Folosește butonul '🔄 Actualizează' din sidebar pentru a reîmprospăta conexiunile.")
with col2:
    st.caption("🏠 Dashboard Central v1.0")
