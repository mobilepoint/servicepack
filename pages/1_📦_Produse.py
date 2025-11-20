# pages/1_📦_Produse.py
import uuid
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
import time
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from woocommerce import API
import requests
from requests.auth import HTTPBasicAuth

st.set_page_config(page_title="Import Produse", layout="wide")
st.title("📦 Import Produse")

# =========================
#   CONNECTIONS
# =========================
@st.cache_resource
def get_pg_connection_string():
    return st.secrets["connections"]["postgresql"]["url"]

@st.cache_resource
def init_woocommerce():
    return API(
        url=st.secrets["connections"]["woocommerce"]["WOO_URL"],
        consumer_key=st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_KEY"],
        consumer_secret=st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_SECRET"],
        version="wc/v3",
        timeout=60
    )

@st.cache_resource
def init_smartbill():
    try:
        return {
            "email": st.secrets["connections"]["smartbill"]["EMAIL"],
            "token": st.secrets["connections"]["smartbill"]["TOKEN"],
            "cif": st.secrets["connections"]["smartbill"]["CIF"],
        }
    except KeyError:
        st.error("❌ Credențiale SmartBill lipsă")
        st.stop()

wcapi = init_woocommerce()

# =========================
#   SESSION STATE
# =========================
for key in ["import_session_id", "import_phase", "smartbill_data", "smartbill_entries", "smartbill_page"]:
    if key not in st.session_state:
        st.session_state[key] = None if "page" not in key else 1

# =========================
#   HELPERS
# =========================
def get_db_connection():
    return psycopg2.connect(get_pg_connection_string())

def safe_decimal(value, default=0):
    if value is None or value == "" or value == "null":
        return Decimal(default)
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except:
        return Decimal(default)

def safe_int(value, default=0):
    try:
        return int(float(str(value)))
    except:
        return default

def compose_variation_name(parent_name, attributes):
    parts = [a.get("option") for a in attributes if a.get("option")]
    return f"{parent_name} - {' - '.join(parts)}" if parts else parent_name

def get_latest_session_id():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT import_session_id FROM woo_staging_raw ORDER BY created_at DESC LIMIT 1")
        r = cursor.fetchone()
        cursor.close()
        conn.close()
        return r[0] if r else None
    except:
        return None

def clear_staging_tables(sid=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if sid:
            cursor.execute("DELETE FROM woo_staging_matched WHERE import_session_id=%s", (sid,))
            cursor.execute("DELETE FROM woo_staging_raw WHERE import_session_id=%s", (sid,))
        else:
            cursor.execute("DELETE FROM woo_staging_matched; DELETE FROM woo_staging_raw;")
        conn.commit()
        cursor.close()
        conn.close()
    except: pass

# =========================
#   WOO FUNCTIONS
# =========================
def quick_refresh_woo():
    st.markdown("### ⚡ Quick Refresh WooCommerce")
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT sku, product_id FROM product_sku")
        sku_map = {r['sku']: r['product_id'] for r in cursor.fetchall()}
        cursor.close()
        conn.close()
    except: return
    
    st.info("🚀 Fetch...")
    try:
        resp = wcapi.get("products/export-full")
        if resp.status_code != 200: return
        data = resp.json()
        if not data.get("success"): return
        products = data.get("products", [])
    except: return
    
    prices, stocks, attrs, matched = [], [], [], 0
    pb = st.progress(0)
    for i, p in enumerate(products):
        pb.progress((i+1)/len(products))
        sku = p.get("sku", "").strip()
        if not sku or sku not in sku_map: continue
        pid = sku_map[sku]
        matched += 1
        pt = p.get("product_type", "simple")
        wpid, wvid, parent = p.get("woo_product_id"), p.get("woo_variation_id"), p.get("parent_id")
        rp = safe_decimal(p.get("regular_price"))
        sp = safe_decimal(p.get("sale_price")) if p.get("sale_price") else None
        sq = safe_int(p.get("stock_quantity"))
        prices.append((pid, sku, parent if pt=="variation" else wpid, wvid if pt=="variation" else None, rp, sp))
        stocks.append((pid, sku, parent if pt=="variation" else wpid, wvid if pt=="variation" else None, sq))
        if pt == "variation" and p.get("attributes"):
            for a in p["attributes"]:
                if a.get("name") and a.get("option"):
                    attrs.append((pid, parent, wvid, a["name"], a["option"]))
    pb.empty()
    
    if prices or stocks:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if prices: execute_batch(cursor, """INSERT INTO woo_preturi (product_id,sku,woo_product_id,woo_variation_id,regular_price,sale_price) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (woo_product_id,woo_variation_id) DO UPDATE SET product_id=EXCLUDED.product_id,sku=EXCLUDED.sku,regular_price=EXCLUDED.regular_price,sale_price=EXCLUDED.sale_price,last_sync=NOW()""", prices)
            if stocks: execute_batch(cursor, """INSERT INTO woo_stoc (product_id,sku,woo_product_id,woo_variation_id,stock_quantity) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (woo_product_id,woo_variation_id) DO UPDATE SET product_id=EXCLUDED.product_id,sku=EXCLUDED.sku,stock_quantity=EXCLUDED.stock_quantity,last_sync=NOW()""", stocks)
            if attrs: execute_batch(cursor, """INSERT INTO woo_variation_attributes (product_id,woo_product_id,woo_variation_id,attribute_name,attribute_value) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (woo_product_id,woo_variation_id,attribute_name) DO UPDATE SET product_id=EXCLUDED.product_id,attribute_value=EXCLUDED.attribute_value""", attrs)
            conn.commit()
            cursor.close()
            conn.close()
            st.success("✅ Complet")
            st.balloons()
        except Exception as e:
            st.error(f"Eroare: {e}")

# =========================
#   SMARTBILL FUNCTIONS
# =========================
def get_smartbill_stocks(email, token, cif):
    try:
        r = requests.get("https://ws.smartbill.ro/SBORO/api/stocks", auth=HTTPBasicAuth(email, token), headers={"Accept": "application/json"}, params={"cif": cif}, timeout=30)
        if r.status_code == 200:
            return r.json()
        st.error(f"SmartBill API error: {r.status_code}")
        return None
    except Exception as e:
        st.error(f"Eroare SmartBill: {e}")
        return None

def get_smartbill_entries(email, token, cif):
    try:
        r = requests.get("https://ws.smartbill.ro/SBORO/api/documents", auth=HTTPBasicAuth(email, token), headers={"Accept": "application/json"}, params={"cif": cif, "type": "nir"}, timeout=60)
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None

def process_sb_stocks(data):
    sb = {}
    if not data or "list" not in data: return sb
    for w in data["list"]:
        for p in w.get("products", []):
            code = (p.get("productCode") or p.get("code", "")).strip()
            if code:
                sb[code] = {"name": p.get("productName") or p.get("name", ""), "stock": float(p.get("quantity", 0))}
    return sb

def get_sb_decisions():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT smartbill_sku, decision_type, product_id FROM smartbill_sku_mapping_decisions")
        res = {r[0]: {"decision": r[1], "product_id": r[2]} for r in cursor.fetchall()}
        cursor.close()
        conn.close()
        return res
    except:
        return {}

def save_sb_decision(sku, action, name=None, product_id=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if action == "creaza_nou":
            nid = str(uuid.uuid4())
            cursor.execute("INSERT INTO product (id,name) VALUES (%s,%s)", (nid, name or sku))
            cursor.execute("INSERT INTO product_sku (sku,product_id,is_primary) VALUES (%s,%s,true)", (sku, nid))
            cursor.execute("""INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku,product_id,decision_type) VALUES (%s,%s,%s) ON CONFLICT (smartbill_sku) DO UPDATE SET product_id=EXCLUDED.product_id,decision_type=EXCLUDED.decision_type""", (sku, nid, action))
        elif action == "adauga_la_sku_existent" and product_id:
            cursor.execute("INSERT INTO product_sku (sku,product_id,is_primary) VALUES (%s,%s,false)", (sku, product_id))
            cursor.execute("""INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku,product_id,decision_type) VALUES (%s,%s,%s) ON CONFLICT (smartbill_sku) DO UPDATE SET product_id=EXCLUDED.product_id,decision_type=EXCLUDED.decision_type""", (sku, product_id, action))
        else:
            cursor.execute("""INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku,decision_type) VALUES (%s,%s) ON CONFLICT (smartbill_sku) DO UPDATE SET decision_type=EXCLUDED.decision_type""", (sku, action))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare salvare {sku}: {e}")
        return False

def sync_smartbill_ui():
    cfg = init_smartbill()
    
    if st.button("📥 Fetch stocuri și intrări SmartBill", use_container_width=True, type="primary"):
        with st.spinner("Se preiau datele..."):
            sd = get_smartbill_stocks(cfg["email"], cfg["token"], cfg["cif"])
            ed = get_smartbill_entries(cfg["email"], cfg["token"], cfg["cif"])
            st.session_state.smartbill_data = process_sb_stocks(sd) if sd else {}
            st.session_state.smartbill_entries = ed.get("list", []) if ed else []
            st.session_state.smartbill_page = 1
            st.success(f"✅ {len(st.session_state.smartbill_data)} produse | {len(st.session_state.smartbill_entries)} intrări")

    if not st.session_state.get("smartbill_data"):
        return
    
    sb = st.session_state.smartbill_data
    dec = get_sb_decisions()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku,id,name FROM product ORDER BY name")
        all_p = cursor.fetchall()
        sku_map = {p[0]: p[1] for p in all_p}
        prod_opts = {f'{p[2]} ({p[0]})': p[1] for p in all_p}
        cursor.close()
        conn.close()
    except:
        return
    
    stocks, unmatch = [], []
    for sku, info in sb.items():
        d = dec.get(sku, {})
        dt = d.get("decision")
        if sku in sku_map:
            stocks.append((sku_map[sku], sku, Decimal(info["stock"])))
        elif dt == "ignora":
            continue
        elif dt in ["creaza_nou", "adauga_la_sku_existent"] and d.get("product_id"):
            stocks.append((d["product_id"], sku, Decimal(info["stock"])))
        else:
            unmatch.append({"sku": sku, "name": info["name"], "stock": info["stock"]})
    
    if unmatch:
        st.warning(f"⚠️ {len(unmatch)} SKU-uri nemapate")
        pg_size = 10
        tot_pg = max(1, (len(unmatch) + pg_size - 1) // pg_size)
        c1, _ = st.columns([1, 5])
        pg = c1.number_input("Pagina", 1, tot_pg, st.session_state.smartbill_page)
        st.session_state.smartbill_page = pg
        items = unmatch[(pg-1)*pg_size : pg*pg_size]
        act_map = {"Creează nou": "creaza_nou", "Adaugă la SKU existent": "adauga_la_sku_existent", "Ignoră": "ignora"}
        
        with st.expander("📋 SKU-uri necunoscute", expanded=True):
            for it in items:
                c1, c2, c3, c4 = st.columns([2, 4, 1, 3])
                c1.write(f"**{it['sku']}**")
                c2.write(it['name'])
                c3.write(it['stock'])
                act = c4.selectbox("", ["Alege..."] + list(act_map.keys()), key=f"act_{it['sku']}", label_visibility="collapsed")
                if act == "Adaugă la SKU existent":
                    st.selectbox("Produs", [""] + list(prod_opts.keys()), key=f"prod_{it['sku']}")
            
            if st.button("💾 Salvează pagina", use_container_width=True):
                for it in items:
                    act = st.session_state.get(f"act_{it['sku']}")
                    if act in act_map:
                        pid = None
                        if act == "Adaugă la SKU existent":
                            sel = st.session_state.get(f"prod_{it['sku']}")
                            if sel: pid = prod_opts[sel]
                        save_sb_decision(it['sku'], act_map[act], it['name'], pid)
                st.success("✅ Salvat")
                time.sleep(0.5)
                st.rerun()
    
    if not unmatch and (stocks or st.session_state.get("smartbill_entries")):
        st.success("✅ Toate SKU-urile mapate!")
        if st.button("💾 Salvează stocuri și prețuri în DB", type="primary", use_container_width=True):
            entries = []
            if st.session_state.get("smartbill_entries"):
                for e in st.session_state.smartbill_entries:
                    for p in e.get("products", []):
                        sku = p.get("code", "").strip()
                        pid = sku_map.get(sku) or next((d["product_id"] for s, d in dec.items() if s == sku and d.get("product_id")), None)
                        if pid:
                            entries.append((pid, sku, e.get("date"), safe_decimal(p.get("quantity")), safe_decimal(p.get("price")), e.get("number"), e.get("supplier", {}).get("name")))
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                if stocks:
                    cursor.execute("DELETE FROM smartbill_stoc")
                    execute_batch(cursor, "INSERT INTO smartbill_stoc (product_id,sku,stock_quantity) VALUES (%s,%s,%s)", stocks)
                if entries:
                    cursor.execute("DELETE FROM smartbill_pret_intrare")
                    execute_batch(cursor, "INSERT INTO smartbill_pret_intrare (product_id,sku,data_intrare,cantitate,pret_unitar,nr_document,furnizor) VALUES (%s,%s,%s,%s,%s,%s,%s)", entries)
                conn.commit()
                cursor.close()
                conn.close()
                st.success("✅ Salvat!")
                st.session_state.smartbill_data = None
                st.session_state.smartbill_entries = None
                st.balloons()
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Eroare DB: {e}")

# =========================
#   UI PRINCIPAL
# =========================
st.markdown("## 🛒 WooCommerce")
c1, c2, c3 = st.columns(3)
with c3:
    if st.button("⚡ Quick Refresh", use_container_width=True):
        quick_refresh_woo()

st.divider()
st.markdown("## 📊 SmartBill")
sync_smartbill_ui()
