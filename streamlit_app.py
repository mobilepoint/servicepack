import streamlit as st
from supabase import create_client, Client
from woocommerce import API
import psycopg2
from psycopg2 import OperationalError

# Configurare pagină
st.set_page_config(
    page_title="Magazin Manager",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ===== SIDEBAR - VERIFICATOR CONEXIUNI =====
with st.sidebar:
    st.title("🔌 Status Conexiuni")
    st.divider()
    
    # Buton pentru verificare
    if st.button("🔄 Verifică toate conexiunile", use_container_width=True):
        
        # Container pentru status-uri
        status_container = st.container()
        
        with status_container:
            # 1. VERIFICARE SUPABASE API CLIENT
            st.write("### 📡 Supabase API Client")
            try:
                supabase_url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
                supabase_key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
                
                # Testare conexiune
                supabase: Client = create_client(supabase_url, supabase_key)
                
                # Test simplu - încearcă să accesezi o tabelă sau health check
                response = supabase.table("_health").select("*").limit(1).execute()
                
                st.success("✅ Supabase API: Conectat")
                st.caption(f"URL: {supabase_url}")
                
            except KeyError:
                st.error("❌ Supabase API: Credențiale lipsă în secrets")
            except Exception as e:
                st.warning(f"⚠️ Supabase API: Conectat dar test eșuat")
                st.caption(f"Detalii: {str(e)[:100]}")
            
            st.divider()
            
            # 2. VERIFICARE POSTGRESQL DIRECT
            st.write("### 🗄️ PostgreSQL Direct")
            try:
                pg_url = st.secrets["connections"]["postgresql"]["url"]
                
                # Testare conexiune PostgreSQL
                conn = psycopg2.connect(pg_url)
                cursor = conn.cursor()
                cursor.execute("SELECT version();")
                db_version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                
                st.success("✅ PostgreSQL: Conectat")
                st.caption(f"Versiune: {db_version[:50]}...")
                
            except KeyError:
                st.error("❌ PostgreSQL: URL lipsă în secrets")
            except OperationalError as e:
                st.error("❌ PostgreSQL: Eroare de conexiune")
                st.caption(f"Detalii: {str(e)[:100]}")
            except Exception as e:
                st.error(f"❌ PostgreSQL: Eroare necunoscută")
                st.caption(f"Detalii: {str(e)[:100]}")
            
            st.divider()
            
            # 3. VERIFICARE WOOCOMMERCE API
            st.write("### 🛒 WooCommerce API")
            try:
                woo_url = st.secrets["WOO_URL"]
                woo_key = st.secrets["WOO_CONSUMER_KEY"]
                woo_secret = st.secrets["WOO_CONSUMER_SECRET"]
                
                # Inițializare client WooCommerce
                wcapi = API(
                    url=woo_url,
                    consumer_key=woo_key,
                    consumer_secret=woo_secret,
                    version="wc/v3",
                    timeout=10
                )
                
                # Test conexiune - verifică system status
                response = wcapi.get("system_status")
                
                if response.status_code == 200:
                    st.success("✅ WooCommerce: Conectat")
                    st.caption(f"Store: {woo_url}")
                    
                    # Informații adiționale despre magazin
                    data = response.json()
                    if "environment" in data:
                        wc_version = data.get("environment", {}).get("version", "N/A")
                        st.caption(f"WooCommerce v{wc_version}")
                else:
                    st.warning(f"⚠️ WooCommerce: Cod răspuns {response.status_code}")
                    
            except KeyError:
                st.error("❌ WooCommerce: Credențiale lipsă în secrets")
            except Exception as e:
                st.error("❌ WooCommerce: Eroare de conexiune")
                st.caption(f"Detalii: {str(e)[:100]}")
            
            st.divider()
            
            # Timestamp ultimei verificări
            import datetime
            st.caption(f"🕐 Ultima verificare: {datetime.datetime.now().strftime('%H:%M:%S')}")
    
    else:
        # Status implicit când nu s-a apăsat butonul
        st.info("👆 Apasă butonul pentru a verifica conexiunile")
    
    st.divider()
    
    # Link-uri utile
    with st.expander("🔗 Link-uri utile"):
        st.markdown("""
        - [Supabase Dashboard](https://supabase.com/dashboard)
        - [WooCommerce Admin](https://your-store.com/wp-admin)
        - [Documentație Streamlit](https://docs.streamlit.io)
        """)

# ===== PAGINA PRINCIPALĂ =====
st.title("🛒 Aplicație Management Magazin WooCommerce")
st.write("Bine ai venit! Selectează o secțiune din sidebar pentru a începe.")

# Afișare informații generale
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("📦 Module disponibile", "4")
    
with col2:
    st.metric("🔌 Conexiuni", "3")
    
with col3:
    st.metric("📊 Status", "✅ Activ")
