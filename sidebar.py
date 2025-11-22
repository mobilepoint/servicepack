import streamlit as st
import psycopg2
from datetime import datetime

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
        from woocommerce import API
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
        from requests.auth import HTTPBasicAuth
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
    """Verifică cel mai recent timestamp din fiecare tabel Supabase"""
    table_status = {
        "woo_stock": {"name": "🛒 Stoc WooCommerce", "timestamp": None, "status": "⏳", "column": "last_sync"},
        "woo_preturi": {"name": "💰 Prețuri WooCommerce", "timestamp": None, "status": "⏳", "column": "last_sync"},
        "smartbill_stock": {"name": "📦 Stoc SmartBill", "timestamp": None, "status": "⏳", "column": "last_sync"},
        "smartbill_pret_intrare": {"name": "💵 Preț Intrare SmartBill", "timestamp": None, "status": "⏳", "column": "last_sync"}
    }
    
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        for table_key, table_info in table_status.items():
            try:
                timestamp_column = table_info["column"]
                
                # Verifică dacă coloana există în tabel
                cursor.execute(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = '{table_key}' 
                    AND column_name = '{timestamp_column}';
                """)
                
                column_exists = cursor.fetchone()
                
                if column_exists:
                    # Obține cel mai recent timestamp (MAX = ultimul update)
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
                        table_status[table_key]["timestamp"] = "Fără date"
                else:
                    table_status[table_key]["status"] = "❓"
                    table_status[table_key]["timestamp"] = "Coloană lipsă"
                    
            except Exception as e:
                table_status[table_key]["status"] = "✗"
                table_status[table_key]["timestamp"] = f"Eroare"
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        # Dacă conexiunea eșuează, marcăm toate ca eroare
        for table_key in table_status.keys():
            table_status[table_key]["status"] = "✗"
            table_status[table_key]["timestamp"] = "DB offline"
    
    return table_status


# ===== FUNCȚIE PENTRU AFIȘARE SIDEBAR =====
def render_sidebar():
    """Afișează sidebar-ul cu statusuri - apelează în toate paginile"""
    with st.sidebar:
        st.markdown("### 🔌 Conexiuni")
        
        connection_status = check_all_connections()
        
        # Status compact într-un singur rând pentru fiecare conexiune
        cols = st.columns([3, 1])
        with cols[0]:
            st.caption("PostgreSQL")
        with cols[1]:
            if connection_status["postgresql"]["status"]:
                st.markdown("✅")
            else:
                st.markdown("❌")
        
        cols = st.columns([3, 1])
        with cols[0]:
            st.caption("WooCommerce")
        with cols[1]:
            if connection_status["woocommerce"]["status"]:
                st.markdown("✅")
            else:
                st.markdown("❌")
        
        cols = st.columns([3, 1])
        with cols[0]:
            st.caption("SmartBill")
        with cols[1]:
            if connection_status["smartbill"]["status"]:
                st.markdown("✅")
            else:
                st.markdown("❌")
        
        st.divider()
        
        # ===== STATUS TABELE SUPABASE =====
        st.markdown("### 📊 Ultima Actualizare")
        
        table_timestamps = check_table_timestamps()
        
        for table_key, table_info in table_timestamps.items():
            with st.container():
                cols = st.columns([1, 5])
                with cols[0]:
                    st.markdown(f"{table_info['status']}")
                with cols[1]:
                    st.caption(f"**{table_info['name']}**")
                    if table_info['timestamp'] and table_info['status'] == "✓":
                        if isinstance(table_info['timestamp'], datetime):
                            st.caption(f"🕐 {table_info['timestamp'].strftime('%d.%m.%y %H:%M')}")
                        else:
                            # Dacă e string, încearcă să-l parsezi
                            try:
                                ts = datetime.fromisoformat(str(table_info['timestamp']).replace('Z', '+00:00'))
                                st.caption(f"🕐 {ts.strftime('%d.%m.%y %H:%M')}")
                            except:
                                st.caption(f"🕐 {table_info['timestamp']}")
                    else:
                        st.caption(f"🕐 {table_info['timestamp'] if table_info['timestamp'] else 'N/A'}")
        
        st.divider()
        
        # Buton refresh compact
        if st.button("🔄 Actualizează", use_container_width=True, type="secondary"):
            st.cache_resource.clear()
            st.cache_data.clear()
            st.rerun()
        
        st.caption(f"Verificat: {datetime.now().strftime('%H:%M:%S')}")
