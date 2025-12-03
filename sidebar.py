import streamlit as st
import psycopg2
from datetime import datetime
import requests
import base64

# ===== FUNCȚIE VERIFICARE CONEXIUNI =====
@st.cache_resource
def check_all_connections():
    """Verifică toate conexiunile la pornirea aplicației"""
    results = {
        "postgresql": {"status": False},
        "woocommerce": {"status": False},
        "smartbill": {"status": False},
        "foneday": {"status": False},
        "emag": {"status": False}
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

   
