import streamlit as st
from woocommerce import API
import psycopg2
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
        "postgresql": {"status": False, "message": ""},
        "woocommerce": {"status": False, "message": ""},
        "smartbill": {"status": False, "message": ""}
    }
    
    # 1. VERIFICARE POSTGRESQL DIRECT
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        cursor.close()
        conn.close()
        results["postgresql"]["status"] = True
        results["postgresql"]["message"] = "✓"
    except:
        results["postgresql"]["message"] = "✗"
    
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
                results["woocommerce"]["message"] = "✓"
            else:
                results["woocommerce"]["message"] = "✗"
        except:
            try:
                response2 = wcapi.get("products", params={"per_page": 1})
                if response2.status_code in range(200, 300):
                    results["woocommerce"]["status"] = True
                    results["woocommerce"]["message"] = "✓"
                else:
                    results["woocommerce"]["message"] = "✗"
            except:
                results["woocommerce"]["message"] = "✗"
    except:
        results["woocommerce"]["message"] = "✗"
    
    # 3. VERIFICARE SMARTBILL API
    try:
        import requests
        sb_email = st.secrets["connections"]["smartbill"]["EMAIL"]
        sb_token = st.secrets["connections"]["smartbill"]["TOKEN"]
        sb_cif = st.secrets["connections"]["smartbill"]["CIF"]
        
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
            results["smartbill"]["message"] = "✓"
        else:
            results["smartbill"]["message"] = "✗"
    except:
        results["smartbill"]["message"] = "✗"
    
    results["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return results


# ===== FUNCȚIE VERIFICARE TIMESTAMP-URI TABELE =====
@st.cache_data(ttl=60)  # Cache pentru 60 secunde
def check_table_timestamps():
    """Verifică cel mai vechi timestamp din fiecare tabel Supabase"""
    table_status = {
        "woo_stock": {"name": "🛒 Stoc WooCommerce", "timestamp": None, "status": "⏳"},
        "woo_preturi": {"name": "💰 Prețuri WooCommerce", "timestamp": None, "status": "⏳"},
        "smartbill_stock": {"name": "📦 Stoc SmartBill", "timestamp": None, "status": "⏳"},
        "smartbill_pret_intrare": {"name": "💵 Preț Intrare SmartBill", "timestamp": None, "status": "⏳"}
    }
    
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        for table_key in table_status.keys():
            try:
                # Caută coloana de timestamp (poate fi updated_at, timestamp, last_update, etc.)
                cursor.execute(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = '{table_key}' 
                    AND (column_name LIKE '%updated%' OR column_name LIKE '%timestamp%' OR column_name LIKE '%date%')
                    ORDER BY ordinal_position
                    LIMIT 1;
                """)
                
                result = cursor.fetchone()
                if result:
                    timestamp_column = result[0]
                    
                    # Obține cel mai vechi timestamp (ultimul update)
                    cursor.execute(f"""
                        SELECT MAX({timestamp_column}) 
                        FROM {table_key};
                    """)
                    
                    max_timestamp = cursor.fetchone()[0]
                    
                    if max_timestamp:
                        table_status[table_key]["timestamp"] = max_timestamp
                        table_status[table_key]["status"] = "✓"
                    else:
                        table_status[table_key]["status"] = "⚠️"
                else:
                    table_status[table_key]["status"] = "❓"
                    
            except Exception as e:
                table_status[table_key]["status"] = "✗"
                table_status[table_key]["timestamp"] = str(e)[:30]
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        # Dacă conexiunea eșuează, marcăm toate ca eroare
        for table_key in table_status.keys():
            table_status[table_key]["status"] = "✗"
    
    return table_status


# ===== SIDEBAR - AFIȘARE STATUS COMPACT =====
with st.sidebar:
    st.markdown("### 🔌 Conexiuni")
    
    connection_status = check_all_connections()
    
    # Status compact într-un singur rând pentru fiecare conexiune
    cols = st.columns([3, 1])
    with cols[0]:
        st.caption("PostgreSQL")
    with cols[1]:
        if connection_status["postgresql"]["status"]:
            st.success("✓", icon="✅")
        else:
            st.error("✗", icon="❌")
    
    cols = st.columns([3, 1])
    with cols[0]:
        st.caption("WooCommerce")
    with cols[1]:
        if connection_status["woocommerce"]["status"]:
            st.success("✓", icon="✅")
        else:
            st.error("✗", icon="❌")
    
    cols = st.columns([3, 1])
    with cols[0]:
        st.caption("SmartBill")
    with cols[1]:
        if connection_status["smartbill"]["status"]:
            st.success("✓", icon="✅")
        else:
            st.error("✗", icon="❌")
    
    st.divider()
    
    # ===== STATUS TABELE SUPABASE =====
    st.markdown("### 📊 Ultima Actualizare Date")
    
    table_timestamps = check_table_timestamps()
    
    for table_key, table_info in table_timestamps.items():
        cols = st.columns([1, 4])
        with cols[0]:
            st.markdown(f"{table_info['status']}")
        with cols[1]:
            st.caption(f"**{table_info['name']}**")
            if table_info['timestamp']:
                if isinstance(table_info['timestamp'], datetime):
                    st.caption(f"🕐 {table_info['timestamp'].strftime('%d.%m %H:%M')}")
                else:
                    st.caption(f"🕐 {table_info['timestamp']}")
            else:
                st.caption("🕐 N/A")
    
    st.divider()
    
    # Buton refresh compact
    if st.button("🔄 Actualizează", use_container_width=True, type="secondary"):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()
    
    st.caption(f"Verificat: {datetime.now().strftime('%H:%M:%S')}")

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
st.caption("💡 **Tip**: Folosește butonul 'Actualizează' din sidebar pentru a verifica din nou conexiunile și statusul datelor.")
