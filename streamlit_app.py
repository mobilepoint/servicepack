import streamlit as st
from woocommerce import API
import psycopg2
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime

# ===== CONFIGURARE PAGINĂ =====
st.set_page_config(
    page_title="Magazin Manager",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== FUNCȚIE VERIFICARE CONEXIUNI =====
@st.cache_resource
def check_all_connections():
    """Verifică toate conexiunile la pornirea aplicației - se rulează o singură dată"""
    results = {
        "postgresql": {"status": False, "message": "", "details": ""},
        "woocommerce": {"status": False, "message": "", "details": ""},
        "smartbill": {"status": False, "message": "", "details": ""}
    }
    
    # 1. VERIFICARE POSTGRESQL DIRECT
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        results["postgresql"]["status"] = True
        results["postgresql"]["message"] = "Conectat"
        version_part = db_version.split()[1] if len(db_version.split()) > 1 else "unknown"
        results["postgresql"]["details"] = f"PostgreSQL {version_part}"
    except KeyError:
        results["postgresql"]["message"] = "URL lipsă"
    except psycopg2.OperationalError as e:
        results["postgresql"]["message"] = "Eroare conexiune"
        results["postgresql"]["details"] = str(e)[:80]
    except Exception as e:
        results["postgresql"]["message"] = "Eroare necunoscută"
        results["postgresql"]["details"] = str(e)[:80]
    
    # 2. VERIFICARE WOOCOMMERCE API
    try:
        woo_url = st.secrets["connections"]["woocommerce"]["WOO_URL"]
        woo_key = st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_KEY"]
        woo_secret = st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_SECRET"]
        
        wcapi = API(
            url=woo_url,
            consumer_key=woo_key,
            consumer_secret=woo_secret,
            version="wc/v3",
            timeout=15
        )
        
        try:
            response = wcapi.get("")
            
            if response.status_code in range(200, 300) or response.status_code == 401:
                results["woocommerce"]["status"] = True
                results["woocommerce"]["message"] = "Conectat"
                results["woocommerce"]["details"] = f"Store: {woo_url}"
            else:
                results["woocommerce"]["message"] = f"Cod HTTP {response.status_code}"
                results["woocommerce"]["details"] = "Verifică credențialele"
        except Exception as req_error:
            try:
                response2 = wcapi.get("products", params={"per_page": 1})
                if response2.status_code in range(200, 300):
                    results["woocommerce"]["status"] = True
                    results["woocommerce"]["message"] = "Conectat"
                    results["woocommerce"]["details"] = f"Store: {woo_url}"
                else:
                    results["woocommerce"]["message"] = f"Cod {response2.status_code}"
            except:
                results["woocommerce"]["message"] = "Eroare conexiune"
                results["woocommerce"]["details"] = str(req_error)[:80]
                
    except KeyError:
        results["woocommerce"]["message"] = "Credențiale lipsă"
    except Exception as e:
        results["woocommerce"]["message"] = "Eroare conexiune"
        results["woocommerce"]["details"] = str(e)[:80]
    
    # 3. VERIFICARE SMARTBILL API
    try:
        sb_email = st.secrets["connections"]["smartbill"]["EMAIL"]
        sb_token = st.secrets["connections"]["smartbill"]["TOKEN"]
        sb_cif = st.secrets["connections"]["smartbill"]["CIF"]
        
        # Endpoint corect pentru SmartBill - verificare liste (taxe)
        url = "https://ws.smartbill.ro/SBORO/api/tax"
        auth = HTTPBasicAuth(sb_email, sb_token)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            url, 
            auth=auth, 
            headers=headers,
            params={"cif": sb_cif},
            timeout=10
        )
        
        if response.status_code == 200:
            results["smartbill"]["status"] = True
            results["smartbill"]["message"] = "Conectat"
            results["smartbill"]["details"] = f"CIF: {sb_cif}"
        elif response.status_code == 401:
            results["smartbill"]["message"] = "Autentificare eșuată"
            results["smartbill"]["details"] = "Verifică email/token"
        elif response.status_code == 403:
            results["smartbill"]["message"] = "Acces interzis"
            results["smartbill"]["details"] = "Verifică abonamentul (Platinum)"
        else:
            results["smartbill"]["message"] = f"Cod HTTP {response.status_code}"
            results["smartbill"]["details"] = response.text[:80] if response.text else ""
            
    except KeyError:
        results["smartbill"]["message"] = "Credențiale lipsă"
    except requests.exceptions.Timeout:
        results["smartbill"]["message"] = "Timeout conexiune"
    except Exception as e:
        results["smartbill"]["message"] = "Eroare conexiune"
        results["smartbill"]["details"] = str(e)[:80]
    
    results["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return results

# ===== SIDEBAR - AFIȘARE STATUS AUTOMAT =====
with st.sidebar:
    st.title("🔌 Status Conexiuni")
    st.caption("Verificare automată la pornire")
    st.divider()
    
    connection_status = check_all_connections()
    
    # 1. PostgreSQL Direct
    st.write("### 🗄️ PostgreSQL")
    if connection_status["postgresql"]["status"]:
        st.success(f"✅ {connection_status['postgresql']['message']}")
        if connection_status["postgresql"]["details"]:
            st.caption(connection_status["postgresql"]["details"])
    else:
        st.error(f"❌ {connection_status['postgresql']['message']}")
        if connection_status["postgresql"]["details"]:
            st.caption(connection_status["postgresql"]["details"])
    
    # 2. WooCommerce API
    st.write("### 🛒 WooCommerce")
    if connection_status["woocommerce"]["status"]:
        st.success(f"✅ {connection_status['woocommerce']['message']}")
        if connection_status["woocommerce"]["details"]:
            st.caption(connection_status["woocommerce"]["details"])
    else:
        st.error(f"❌ {connection_status['woocommerce']['message']}")
        if connection_status["woocommerce"]["details"]:
            st.caption(connection_status["woocommerce"]["details"])
    
    # 3. SmartBill API
    st.write("### 🧾 SmartBill")
    if connection_status["smartbill"]["status"]:
        st.success(f"✅ {connection_status['smartbill']['message']}")
        if connection_status["smartbill"]["details"]:
            st.caption(connection_status["smartbill"]["details"])
    else:
        st.error(f"❌ {connection_status['smartbill']['message']}")
        if connection_status["smartbill"]["details"]:
            st.caption(connection_status["smartbill"]["details"])
    
    st.divider()
    st.caption(f"🕐 Verificat: {connection_status['timestamp']}")
    
    if st.button("🔄 Re-verifică", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()
    
    st.divider()
    
    with st.expander("🔗 Link-uri utile"):
        st.markdown("""
        - [WooCommerce Admin](https://servicepack.ro/wp-admin)
        - [SmartBill Dashboard](https://www.smartbill.ro)
        - [SmartBill API Docs](https://api.smartbill.ro)
        """)

# ===== PAGINA PRINCIPALĂ =====
st.title("🛒 Aplicație Management Magazin WooCommerce")

st.divider()

# Afișare metrici generale
col1, col2, col3 = st.columns(3)

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
st.caption("💡 **Tip**: Folosește butonul 'Re-verifică' din sidebar pentru a testa din nou conexiunile după modificări.")
