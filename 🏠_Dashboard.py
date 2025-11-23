import streamlit as st
from datetime import datetime
from sidebar import render_sidebar, check_all_connections, check_table_timestamps
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
st.title("🏠 Dashboard")
st.divider()

# Afișare metrici generale
col1, col2, col3, col4 = st.columns(4)

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
    tables = check_table_timestamps()
    active_tables = sum(1 for t in tables.values() if t["status"] == "✓")
    st.metric(
        label="📊 Tabele Sincronizate",
        value=f"{active_tables}/4"
    )

with col4:
    st.metric(
        label="🕐 Ultima Verificare",
        value=datetime.now().strftime("%H:%M:%S")
    )

st.divider()

# === STATUS DETALIAT CONEXIUNI ===
st.markdown("### 🔍 Status Sistem")

if total_connections == 4:
    st.success("✅ **Toate conexiunile sunt active!** Sistemul funcționează optim.")
elif total_connections > 0:
    st.warning("⚠️ **Unele conexiuni au eșuat.** Verifică detaliile în sidebar.")
    
    # Afișează care conexiuni au eșuat
    failed_connections = [name for name, status in connection_status.items() if not status["status"]]
    if failed_connections:
        st.info(f"**Conexiuni inactive:** {', '.join(failed_connections).title()}")
else:
    st.error("❌ **Nicio conexiune activă.** Verifică configurarea secrets.")
    
    with st.expander("📖 Cum configurez secrets?"):
        st.code("""
# În Streamlit Cloud → Settings → Secrets

[connections.postgresql]
url = "postgresql://..."

[connections.woocommerce]
WOO_URL = "https://..."
WOO_CONSUMER_KEY = "ck_..."
WOO_CONSUMER_SECRET = "cs_..."

[connections.smartbill]
EMAIL = "your@email.com"
TOKEN = "your_token"
CIF = "ROxxxxxx"

[connections.foneday]
API_URL = "https://foneday.shop/api/v1"
API_TOKEN = "eyJ0eXAi..."
        """, language="toml")

st.divider()

# === TABELE SINCRONIZATE ===
st.markdown("### 📊 Detalii Tabele")

# Afișează tabelele într-un grid
col1, col2 = st.columns(2)

table_items = list(tables.items())

with col1:
    for i in range(0, len(table_items), 2):
        table_key, table_info = table_items[i]
        with st.container(border=True):
            st.markdown(f"{table_info['status']} **{table_info['name']}**")
            if table_info['ts'] and table_info['status'] == "✓":
                if isinstance(table_info['ts'], datetime):
                    st.caption(f"🕐 {table_info['ts'].strftime('%d.%m.%Y %H:%M')}")
                else:
                    st.caption(f"🕐 {table_info['ts']}")
            else:
                st.caption(f"⚠️ {table_info['ts'] if table_info['ts'] else 'Fără date'}")

with col2:
    for i in range(1, len(table_items), 2):
        if i < len(table_items):
            table_key, table_info = table_items[i]
            with st.container(border=True):
                st.markdown(f"{table_info['status']} **{table_info['name']}**")
                if table_info['ts'] and table_info['status'] == "✓":
                    if isinstance(table_info['ts'], datetime):
                        st.caption(f"🕐 {table_info['ts'].strftime('%d.%m.%Y %H:%M')}")
                    else:
                        st.caption(f"🕐 {table_info['ts']}")
                else:
                    st.caption(f"⚠️ {table_info['ts'] if table_info['ts'] else 'Fără date'}")

st.divider()
st.caption("💡 **Tip**: Folosește butonul '🔄 Actualizează' din sidebar pentru a reîmprospăta datele.")
