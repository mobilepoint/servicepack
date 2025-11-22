import streamlit as st
import psycopg2
from datetime import datetime

# ===== FUNCȚIE VERIFICARE CONEXIUNI =====
@st.cache_resource
def check_all_connections():
    """Verifică toate conexiunile la pornirea aplicației"""
    results = {
        "postgresql": {"status": False},
        "woocommerce": {"status": False},
        "smartbill": {"status": False}
    }
    
    # 1. VERIFICARE POSTGRESQL
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        cursor.close()
        conn.close()
        results["postgresql"]["status"] = True
    except:
        pass
    
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
            response = wcapi.get("products", params={"per_page": 1})
            if response.status_code in range(200, 300):
                results["woocommerce"]["status"] = True
        except:
            pass
    except:
        pass
    
    # 3. VERIFICARE SMARTBILL API
    try:
        import requests
        from requests.auth import HTTPBasicAuth
        sb_email = st.secrets["connections"]["smartbill"]["EMAIL"]
        sb_token = st.secrets["connections"]["smartbill"]["TOKEN"]
        sb_cif = st.secrets["connections"]["smartbill"]["CIF"]
        
        url = "https://ws.smartbill.ro/SBORO/api/tax"
        auth = HTTPBasicAuth(sb_email, sb_token)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        
        response = requests.get(url, auth=auth, headers=headers, params={"cif": sb_cif}, timeout=10)
        
        if response.status_code == 200:
            results["smartbill"]["status"] = True
    except:
        pass
    
    return results


# ===== FUNCȚIE VERIFICARE TIMESTAMP-URI TABELE =====
@st.cache_data(ttl=60)
def check_table_timestamps():
    """Verifică cel mai recent timestamp din tabelele Supabase"""
    tables = {
        "woo_stoc": {"name": "🛒 Stoc WooCommerce", "ts": None, "status": "⏳"},
        "woo_preturi": {"name": "💰 Prețuri WooCommerce", "ts": None, "status": "⏳"},
        "smartbill_stoc": {"name": "📦 Stoc SmartBill", "ts": None, "status": "⏳"},
        "smartbill_pret_intrare": {"name": "💵 Preț Intrare SmartBill", "ts": None, "status": "⏳"}
    }
    
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        for table_key in tables.keys():
            try:
                # Verifică dacă tabelul există și are coloana last_sync
                cursor.execute(f"""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_schema = 'public' 
                        AND table_name = '{table_key}' 
                        AND column_name = 'last_sync'
                    );
                """)
                
                exists = cursor.fetchone()[0]
                
                if exists:
                    # Obține MAX(last_sync) - cel mai recent update
                    cursor.execute(f"""
                        SELECT MAX(last_sync) 
                        FROM public.{table_key};
                    """)
                    
                    result = cursor.fetchone()
                    max_ts = result[0] if result else None
                    
                    if max_ts:
                        tables[table_key]["ts"] = max_ts
                        tables[table_key]["status"] = "✓"
                    else:
                        tables[table_key]["status"] = "⚠️"
                        tables[table_key]["ts"] = "Fără date"
                else:
                    tables[table_key]["status"] = "❓"
                    tables[table_key]["ts"] = "Coloană lipsă"
                    
            except Exception as e:
                tables[table_key]["status"] = "✗"
                tables[table_key]["ts"] = "Eroare"
        
        cursor.close()
        conn.close()
        
    except:
        for table_key in tables.keys():
            tables[table_key]["status"] = "✗"
            tables[table_key]["ts"] = "DB offline"
    
    return tables


# ===== FUNCȚIE PENTRU AFIȘARE SIDEBAR =====
def render_sidebar():
    """Afișează sidebar-ul compact cu statusuri"""
    with st.sidebar:
        # === CONEXIUNI ===
        st.markdown("### 🔌 Conexiuni")
        
        conn_status = check_all_connections()
        
        # PostgreSQL
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("PostgreSQL")
        with col2:
            st.markdown("✅" if conn_status["postgresql"]["status"] else "❌")
        
        # WooCommerce
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("WooCommerce")
        with col2:
            st.markdown("✅" if conn_status["woocommerce"]["status"] else "❌")
        
        # SmartBill
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("SmartBill")
        with col2:
            st.markdown("✅" if conn_status["smartbill"]["status"] else "❌")
        
        st.divider()
        
        # === ULTIMA ACTUALIZARE TABELE ===
        st.markdown("### 📊 Ultima Actualizare")
        
        tables = check_table_timestamps()
        
        for table_key, table_info in tables.items():
            col1, col2 = st.columns([1, 5])
            with col1:
                st.markdown(table_info["status"])
            with col2:
                st.caption(f"**{table_info['name']}**")
                
                # Formatare timestamp
                if table_info["ts"] and table_info["status"] == "✓":
                    if isinstance(table_info["ts"], datetime):
                        st.caption(f"🕐 {table_info['ts'].strftime('%d.%m.%y %H:%M')}")
                    else:
                        try:
                            # Parse string ISO format
                            ts_str = str(table_info["ts"])
                            if 'T' in ts_str:
                                ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                            else:
                                ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
                            st.caption(f"🕐 {ts.strftime('%d.%m.%y %H:%M')}")
                        except:
                            st.caption(f"🕐 {table_info['ts']}")
                else:
                    st.caption(f"🕐 {table_info['ts'] if table_info['ts'] else 'N/A'}")
        
        st.divider()
        
        # === BUTON REFRESH ===
        if st.button("🔄 Actualizează", use_container_width=True, type="secondary"):
            st.cache_resource.clear()
            st.cache_data.clear()
            st.rerun()
        
        st.caption(f"Verificat: {datetime.now().strftime('%H:%M:%S')}")
