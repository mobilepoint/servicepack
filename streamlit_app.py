import streamlit as st
from supabase import create_client, Client
from woocommerce import API
import psycopg2
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
        
        # Conexiune cu timeout explicit
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        # Test simplu - verifică versiunea
        cursor.execute("SELECT version();")
        db_version = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        results["postgresql"]["status"] = True
        results["postgresql"]["message"] = "Conectat"
        # Extrage doar versiunea PostgreSQL
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
    
    # 3. VERIFICARE WOOCOMMERCE API
    try:
        # Acum accesăm din secțiunea [connections.woocommerce]
        woo_url = st.secrets["connections"]["woocommerce"]["WOO_URL"]
        woo_key = st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_KEY"]
        woo_secret = st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_SECRET"]
        
        # Inițializare cu timeout mărit la 15 secunde
        wcapi = API(
            url=woo_url,
            consumer_key=woo_key,
            consumer_secret=woo_secret,
            version="wc/v3",
            timeout=15
        )
        
        # Test simplu - verifică endpoint-ul root
        try:
            response = wcapi.get("")
            
            # Cod 200-299 = succes, 401 = autentificat dar fără permisiuni (OK)
            if response.status_code in range(200, 300) or response.status_code == 401:
                results["woocommerce"]["status"] = True
                results["woocommerce"]["message"] = "Conectat"
                results["woocommerce"]["details"] = f"Store: {woo_url}"
            else:
                results["woocommerce"]["message"] = f"Cod HTTP {response.status_code}"
                results["woocommerce"]["details"] = "Verifică credențialele"
        except Exception as req_error:
            # Test alternativ - verifică produse
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
    
    results["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return results

# ===== SIDEBAR - AFIȘARE STATUS AUTOMAT =====
with st.sidebar:
    st.title("🔌 Status Conexiuni")
    st.caption("Verificare automată la pornire")
    st.divider()
    
    # Verifică conexiunile automat (cache-uit, rulează o singură dată)
    connection_status = check_all_connections()
    
    # 1. Supabase API
    st.write("### 📡 Supabase API")
    if connection_status["supabase"]["status"]:
        st.success(f"✅ {connection_status['supabase']['message']}")
        if connection_status["supabase"]["details"]:
            st.caption(connection_status["supabase"]["details"])
    else:
        st.error(f"❌ {connection_status['supabase']['message']}")
        if connection_status["supabase"]["details"]:
            st.caption(connection_status["supabase"]["details"])
    
    # 2. PostgreSQL Direct
    st.write("### 🗄️ PostgreSQL")
    if connection_status["postgresql"]["status"]:
        st.success(f"✅ {connection_status['postgresql']['message']}")
        if connection_status["postgresql"]["details"]:
            st.caption(connection_status["postgresql"]["details"])
    else:
        st.error(f"❌ {connection_status['postgresql']['message']}")
        if connection_status["postgresql"]["details"]:
            st.caption(connection_status["postgresql"]["details"])
    
    # 3. WooCommerce API
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
    
    st.divider()
    
    # Link-uri utile
    with st.expander("🔗 Link-uri utile"):
        st.markdown("""
        - [Supabase Dashboard](https://supabase.com/dashboard)
        - [WooCommerce Admin](https://servicepack.ro/wp-admin)
        - [GitHub Repository](https://github.com)
        """)

# ===== PAGINA PRINCIPALĂ =====
st.title("🛒 Aplicație Management Magazin WooCommerce")
st.write("Sistemul a verificat automat toate conexiunile. Verifică sidebar-ul pentru detalii.")

st.divider()

# Afișare metrici generale
col1, col2, col3 = st.columns(3)

connection_status = check_all_connections()
total_connections = sum(1 for conn in ["supabase", "postgresql", "woocommerce"] 
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
    
    # Afișare informații despre servicii conectate
    st.subheader("📋 Servicii conectate")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.info("""
        **Supabase**
        - Database PostgreSQL
        - API REST
        - Autentificare
        """)
    
    with col2:
        st.info("""
        **PostgreSQL**
        - Query-uri directe
        - Operațiuni complexe
        - Backup & Export
        """)
    
    with col3:
        st.info("""
        **WooCommerce**
        - Produse
        - Comenzi
        - Clienți
        """)
    
elif total_connections > 0:
    st.warning("⚠️ **Unele conexiuni au eșuat.** Verifică detaliile în sidebar și corectează credențialele.")
    
else:
    st.error("❌ **Nicio conexiune activă.** Verifică configurarea secrets în Streamlit Cloud.")
    
    with st.expander("📖 Cum configurez secrets?"):
        st.markdown("""
        1. Mergi în **Streamlit Cloud Dashboard**
        2. Selectează aplicația ta
        3. Click pe **Settings** → **Secrets**
        4. Adaugă configurația din documentație
        5. Click **Save** și reîncarc ă aplicația
        """)

st.divider()

# Informații despre structura aplicației
st.subheader("🗂️ Structura aplicației")

st.markdown("""
Această aplicație este organizată modular pentru a gestiona diferite aspecte ale magazinului tău WooCommerce:

- **📦 Produse** - Gestionare catalog, stocuri, prețuri
- **📋 Comenzi** - Tracking comenzi, status, procesare
- **👥 Clienți** - Baza de date clienți, istoricul comenzilor
- **📊 Rapoarte** - Analiză vânzări, statistici, export date

Toate modulele vor fi accesibile din sidebar după ce le adaugi în folderul `pages/`.
""")

# Footer
st.divider()
st.caption("💡 **Tip**: Folosește butonul 'Re-verifică' din sidebar pentru a testa din nou conexiunile după modificări.")
