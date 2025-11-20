import streamlit as st
from supabase import create_client, Client
from woocommerce import API
import psycopg2
from datetime import datetime

# Configurare pagină
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
        "supabase": {"status": False, "message": "", "details": ""},
        "postgresql": {"status": False, "message": "", "details": ""},
        "woocommerce": {"status": False, "message": "", "details": ""}
    }
    
    # 1. VERIFICARE SUPABASE API CLIENT
    try:
        supabase_url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
        supabase_key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
        supabase = create_client(supabase_url, supabase_key)
        
        results["supabase"]["status"] = True
        results["supabase"]["message"] = "Conectat"
        results["supabase"]["details"] = f"URL: {supabase_url}"
    except KeyError:
        results["supabase"]["message"] = "Credențiale lipsă"
    except Exception as e:
        results["supabase"]["message"] = "Eroare conexiune"
        results["supabase"]["details"] = str(e)[:80]
    
    # 2. VERIFICARE POSTGRESQL DIRECT
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        
        results["postgresql"]["status"] = True
        results["postgresql"]["message"] = "Conectat"
        results["postgresql"]["details"] = f"PostgreSQL {db_version.split()[1]}"
    except KeyError:
        results["postgresql"]["message"] = "URL lipsă"
    except Exception as e:
        results["postgresql"]["message"] = "Eroare conexiune"
        results["postgresql"]["details"] = str(e)[:80]
    
    # 3. VERIFICARE WOOCOMMERCE API
    try:
        woo_url = st.secrets["WOO_URL"]
        woo_key = st.secrets["WOO_CONSUMER_KEY"]
        woo_secret = st.secrets["WOO_CONSUMER_SECRET"]
        
        wcapi = API(
            url=woo_url,
            consumer_key=woo_key,
            consumer_secret=woo_secret,
            version="wc/v3",
            timeout=10
        )
        
        # Test simplu - verifică dacă API-ul răspunde
        response = wcapi.get("products", params={"per_page": 1})
        
        if response.status_code == 200:
            results["woocommerce"]["status"] = True
            results["woocommerce"]["message"] = "Conectat"
            results["woocommerce"]["details"] = f"Store: {woo_url}"
        else:
            results["woocommerce"]["message"] = f"Cod {response.status_code}"
    except KeyError:
        results["woocommerce"]["message"] = "Credențiale lipsă"
    except Exception as e:
        results["woocommerce"]["message"] = "Eroare conexiune"
        results["woocommerce"]["details"] = str(e)[:80]
    
    results["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return results

# ===== SIDEBAR - AFIȘARE STATUS AUTOMAT =====
with st.sidebar:
    st.title("🔌 Status Conexiuni")
    st.caption("Verificare automată la pornire")
    st.divider()
    
    # Verifică conexiunile automat (cache-uit, rulează o singură dată)
    connection_status = check_all_connections()
    
    # 1. Supabase
    st.write("### 📡 Supabase API")
    if connection_status["supabase"]["status"]:
        st.success(f"✅ {connection_status['supabase']['message']}")
        if connection_status["supabase"]["details"]:
            st.caption(connection_status["supabase"]["details"])
    else:
        st.error(f"❌ {connection_status['supabase']['message']}")
        if connection_status["supabase"]["details"]:
            st.caption(connection_status["supabase"]["details"])
    
    # 2. PostgreSQL
    st.write("### 🗄️ PostgreSQL")
    if connection_status["postgresql"]["status"]:
        st.success(f"✅ {connection_status['postgresql']['message']}")
        if connection_status["postgresql"]["details"]:
            st.caption(connection_status["postgresql"]["details"])
    else:
        st.error(f"❌ {connection_status['postgresql']['message']}")
        if connection_status["postgresql"]["details"]:
            st.caption(connection_status["postgresql"]["details"])
    
    # 3. WooCommerce
    st.write("### 🛒 WooCommerce")
    if connection_status["woocommerce"]["status"]:
        st.success(f"✅ {connection_status['woocommerce']['message']}")
        if connection_status["woocommerce"]["details"]:
            st.caption(connection_status["woocommerce"]["details"])
    else:
        st.error(f"❌ {connection_status['woocommerce']['message']}")
        if connection_status["woocommerce"]["details"]:
            st.caption(connection_status["woocommerce"]["details"])
    
    st.divider()
    st.caption(f"🕐 Verificat: {connection_status['timestamp']}")
    
    # Buton pentru re-verificare (opțional)
    if st.button("🔄 Re-verifică", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()

# ===== PAGINA PRINCIPALĂ =====
st.title("🛒 Aplicație Management Magazin WooCommerce")
st.write("Sistemul a verificat automat toate conexiunile. Verifică sidebar-ul pentru detalii.")

# Afișare metrici
col1, col2, col3 = st.columns(3)

connection_status = check_all_connections()
total_connections = sum(1 for conn in ["supabase", "postgresql", "woocommerce"] 
                       if connection_status[conn]["status"])

with col1:
    st.metric("🔌 Conexiuni active", f"{total_connections}/3")
    
with col2:
    status_emoji = "✅" if total_connections == 3 else "⚠️" if total_connections > 0 else "❌"
    st.metric("📊 Status general", status_emoji)
    
with col3:
    st.metric("📦 Module", "4")
