import streamlit as st
import requests

# ===== CONFIGURARE PAGINĂ =====
st.set_page_config(
    page_title="🔍 IP Finder",
    page_icon="🔍",
    layout="centered"
)

st.title("🔍 Găsește IP-ul aplicației Streamlit Cloud")
st.divider()

st.info("**Această pagină afișează IP-ul PUBLIC al aplicației tale Streamlit Cloud.**")

# === METODA 1: ipify ===
st.markdown("### 📍 IP-ul curent (ipify.org)")
try:
    response = requests.get('https://api.ipify.org?format=json', timeout=10)
    if response.status_code == 200:
        ip_data = response.json()
        current_ip = ip_data.get('ip')

        st.success(f"✅ IP-ul aplicației: **{current_ip}**")

        # Copiază IP
        st.code(current_ip, language="text")

        st.warning("⚠️ **Atenție:** Acest IP poate schimba ORICÂND pe Streamlit Community Cloud!")

        # Instrucțiuni
        with st.expander("📋 Ce fac cu acest IP?"):
            st.markdown(f"""
            **Pași următori:**

            1. **Copiază IP-ul:** `{current_ip}`
            2. **Accesează eMAG Marketplace** → Setări API
            3. **Adaugă IP-ul în whitelist**
            4. **Salvează** și testează conexiunea
            5. **Verifică periodic** - IP-ul se poate schimba!
            """)
    else:
        st.error(f"❌ Eroare la obținerea IP-ului: Status {response.status_code}")
except Exception as e:
    st.error(f"❌ Eroare: {e}")

st.divider()

# === METODA 2: ip-api.com (cu detalii geo) ===
st.markdown("### 🌍 Detalii geografice (ip-api.com)")
try:
    response = requests.get('http://ip-api.com/json/', timeout=10)
    if response.status_code == 200:
        geo_data = response.json()

        col1, col2 = st.columns(2)
        with col1:
            st.metric("IP Address", geo_data.get('query', 'N/A'))
            st.metric("Oraș", geo_data.get('city', 'N/A'))
        with col2:
            st.metric("Țară", geo_data.get('country', 'N/A'))
            st.metric("ISP", geo_data.get('isp', 'N/A'))

        st.caption(f"Coordonate: {geo_data.get('lat', 'N/A')}, {geo_data.get('lon', 'N/A')}")
except Exception as e:
    st.error(f"❌ Eroare la obținerea detaliilor geo: {e}")

st.divider()

# === INFORMAȚII IMPORTANTE ===
st.markdown("### ℹ️ Informații importante")

st.markdown("""
**Streamlit Community Cloud și IP-uri dinamice:**

- 🔄 Streamlit Community Cloud folosește **IP-uri dinamice**
- ⚠️ IP-ul aplicației **poate schimba oricând** fără notificare
- 📝 Verifică periodic IP-ul dacă conexiunea eMAG nu mai funcționează
- 💡 Pentru IP static, consideră deploy pe AWS/GCP/Azure cu Elastic IP

**Documentație oficială:**
- [Status and limitations](https://docs.streamlit.io/deploy/streamlit-community-cloud/status)
""")

st.divider()
st.caption("💡 **Tip:** Salvează această pagină pentru a verifica IP-ul periodic!")
