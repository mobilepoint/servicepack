import streamlit as st
from sidebar import render_sidebar  # Import funcția de sidebar

# ===== CONFIGURARE PAGINĂ =====
st.set_page_config(
    page_title="Magazin Manager",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== AFIȘEAZĂ SIDEBAR (VA FI VIZIBIL ÎN TOATE PAGINILE) =====
render_sidebar()

# ===== PAGINA PRINCIPALĂ =====
st.title("🛒 Aplicație Management Magazin WooCommerce")
st.divider()

# Afișare metrici generale
col1, col2, col3 = st.columns(3)

from sidebar import check_all_connections
connection_status = check_all_connections()
total_connections = sum(1 for conn in ["postgresql", "woocommerce", "smartbill"] 
                       if connection_status[conn]["status"])

with col1:
    st.metric(
        label="🔌 Conexiuni active",
        value=f"{total_connections}/3",
        delta="Toate funcționale" if total_connections == 3 else f"{3-total_connections} inactive"
    )

with col2:
    status_emoji = "✅ Activ" if total_connections == 3 else "⚠️ Parțial" if total_connections > 0 else "❌ Inactiv"
    st.metric(
        label="📊 Status general",
        value=status_emoji
    )

with col3:
    st.metric(
        label="📦 Module disponibile",
        value="4",
        delta="Comenzi, Produse, Clienți, Rapoarte"
    )

st.divider()

# Status detaliat bazat pe conexiuni
if total_connections == 3:
    st.success("✅ **Toate conexiunile sunt active!** Poți începe să lucrezi cu aplicația.")
elif total_connections > 0:
    st.warning("⚠️ **Unele conexiuni au eșuat.** Verifică detaliile în sidebar și corectează credențialele.")
else:
    st.error("❌ **Nicio conexiune activă.** Verifică configurarea secrets în Streamlit Cloud.")
    
    with st.expander("📖 Cum configurez secrets?"):
        st.markdown("""
        1. Mergi în **Streamlit Cloud Dashboard**
        2. Selectează aplicația ta
        3. Click pe **Settings** → **Secrets**
        4. Adaugă configurația necesară
        5. Click **Save** și reîncarcă aplicația
        """)

st.divider()
st.caption("💡 **Tip**: Folosește butonul 'Actualizează' din sidebar pentru a verifica din nou conexiunile și statusul datelor.")
