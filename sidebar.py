import streamlit as st
import psycopg2
from datetime import datetime
import requests

# ===== FUNCȚIE VERIFICARE CONEXIUNI =====
@st.cache_resource
def check_all_connections():
    """Verifică toate conexiunile la pornirea aplicației"""
    results = {
        "postgresql": {"status": False},
        "woocommerce": {"status": False},
        "smartbill": {"status": False},
        "foneday": {"status": False}
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
        
        response = wcapi.get("products", params={"per_page": 1})
        if response.status_code in range(200, 300):
            results["woocommerce"]["status"] = True
    except:
        pass
    
    # 3. VERIFICARE SMARTBILL API
    try:
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
    
    # 4. VERIFICARE FONEDAY API
    try:
        foneday_api_url = st.secrets["connections"]["foneday"]["API_URL"]
        foneday_api_token = st.secrets["connections"]["foneday"]["API_TOKEN"]
        
        headers = {
            "Authorization": f"Bearer {foneday_api_token}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            f"{foneday_api_url}/products",
            headers=headers,
            timeout=10
        )
        
        if response.status_code in range(200, 300):
            results["foneday"]["status"] = True
    except:
        pass
    
    return results


# ===== FUNCȚIE PENTRU A OBȚINE PRODUS FONEDAY =====
@st.cache_data(ttl=300)
def get_foneday_product_by_sku(foneday_sku: str):
    """Obține produs din FoneDay după SKU"""
    try:
        headers = {
            "Authorization": f"Bearer {st.secrets['connections']['foneday']['API_TOKEN']}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            f"{st.secrets['connections']['foneday']['API_URL']}/product/{foneday_sku}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("product")
        
        return None
    except:
        return None


# ===== FUNCȚIE VERIFICARE TIMESTAMP-URI TABELE =====
@st.cache_data(ttl=60)
def check_table_timestamps():
    """Verifică cel mai recent timestamp din tabelele Supabase"""
    tables = {
        "woo_stoc": {
            "name": "🛒 Stoc WooCommerce", 
            "ts": None, 
            "status": "⏳",
            "column": "last_sync"
        },
        "woo_preturi": {
            "name": "💰 Prețuri WooCommerce", 
            "ts": None, 
            "status": "⏳",
            "column": "last_sync"
        },
        "smartbill_stoc": {
            "name": "📦 Stoc SmartBill", 
            "ts": None, 
            "status": "⏳",
            "column": "last_sync"
        },
        "smartbill_pret_intrare": {
            "name": "💵 Preț Intrare SmartBill", 
            "ts": None, 
            "status": "⏳",
            "column": "updated_at"
        }
    }
    
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        for table_key, table_config in tables.items():
            try:
                timestamp_column = table_config["column"]
                
                cursor.execute(f"""
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns 
                        WHERE table_schema = 'public' 
                        AND table_name = '{table_key}' 
                        AND column_name = '{timestamp_column}'
                    );
                """)
                
                exists = cursor.fetchone()[0]
                
                if exists:
                    cursor.execute(f"""
                        SELECT MAX({timestamp_column}) 
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
                    
            except:
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
        # === CONEXIUNI API ===
        st.markdown("### 🔌 Conexiuni API")
        
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
        
        # FoneDay
        col1, col2 = st.columns([3, 1])
        with col1:
            st.caption("📱 FoneDay")
        with col2:
            st.markdown("✅" if conn_status["foneday"]["status"] else "❌")
        
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
                
                if table_info["ts"] and table_info["status"] == "✓":
                    if isinstance(table_info["ts"], datetime):
                        st.caption(f"🕐 {table_info['ts'].strftime('%d.%m.%y %H:%M')}")
                    else:
                        try:
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
