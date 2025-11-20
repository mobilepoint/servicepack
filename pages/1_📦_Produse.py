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

st.set_page_config(page_title="Produse WooCommerce & SmartBill", layout="wide")
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
        sbcfg = st.secrets["connections"]["smartbill"]
        return {
            "email": sbcfg["EMAIL"],
            "token": sbcfg["TOKEN"],
            "cif": sbcfg["CIF"],
        }
    except KeyError:
        st.error("❌ Credențiale SmartBill lipsă")
        st.stop()

wcapi = init_woocommerce()

# =========================
#   SESSION STATE
# =========================
if "import_session_id" not in st.session_state:
    st.session_state["import_session_id"] = None
if "import_phase" not in st.session_state:
    st.session_state["import_phase"] = None
if "import_stats" not in st.session_state:
    st.session_state["import_stats"] = {}
if "smartbill_data" not in st.session_state:
    st.session_state["smartbill_data"] = None
if "smartbill_entries" not in st.session_state:
    st.session_state["smartbill_entries"] = None
if "smartbill_page" not in st.session_state:
    st.session_state["smartbill_page"] = 1

# =========================
#   HELPERS
# =========================
def get_db_connection():
    return psycopg2.connect(get_pg_connection_string())

def safe_decimal(value, default=0):
    if value is None or value == "" or value == "null":
        return Decimal(default)
    try:
        cleaned = str(value).strip().replace(",", ".")
        if cleaned == "" or cleaned == ".":
            return Decimal(default)
        return Decimal(cleaned)
    except (ValueError, TypeError, InvalidOperation):
        return Decimal(default)

def clear_staging_tables(session_id: str = None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if session_id:
            cursor.execute("DELETE FROM woo_staging_matched WHERE import_session_id = %s", (session_id,))
            cursor.execute("DELETE FROM woo_staging_raw WHERE import_session_id = %s", (session_id,))
        else:
            cursor.execute("DELETE FROM woo_staging_matched")
            cursor.execute("DELETE FROM woo_staging_raw")
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ Eroare ștergere staging: {e}")
        return False

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
        
def compose_variation_name(parent_name: str, attributes: list) -> str:
    if not attributes:
        return parent_name
    attr_parts = [attr.get("option", "") for attr in attributes if attr.get("option", "")]
    if attr_parts:
        return f"{parent_name} - {' - '.join(attr_parts)}"
    return parent_name

# =========================
#   WOOCOMMERCE FUNCTIONS
# =========================
def quick_refresh_prices_and_stock():
    with st.container():
        st.markdown("### ⚡ Quick Refresh WooCommerce")
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT ps.sku, ps.product_id FROM product_sku ps")
            known_skus = {row['sku']: row['product_id'] for row in cursor.fetchall()}
            cursor.close()
            conn.close()
        except: return
        st.info("🚀 Fetch din WooCommerce...")
        try:
            resp = wcapi.get("products/export-full")
            if resp.status_code != 200: return
            data = resp.json()
            if not data.get("success"): return
            woo_products = data.get("products", [])
        except: return
        prices, stocks, attrs, matched = [], [], [], 0
        pb = st.progress(0)
        for idx, p in enumerate(woo_products):
            pb.progress((idx+1)/len(woo_products))
            try:
                sku = p.get("sku", "").strip()
                if not sku or sku not in known_skus: continue
                pid = known_skus[sku]
                matched += 1
                pt = p.get("product_type", "simple")
                wpid = p.get("woo_product_id")
                wvid = p.get("woo_variation_id")
                parent = p.get("parent_id")
                rp = safe_decimal(p.get("regular_price"), 0)
                sp = safe_decimal(p.get("sale_price")) if p.get("sale_price") else None
                sq = safe_decimal(p.get("stock_quantity"), 0)
                prices.append((pid, sku, parent if pt=="variation" else wpid, wvid if pt=="variation" else None, rp, sp))
                stocks.append((pid, sku, parent if pt=="variation" else wpid, wvid if pt=="variation" else None, sq))
                if pt == "variation" and p.get("attributes"):
                    for a in p["attributes"]:
                        an, av = a.get("name",""), a.get("option","")
                        if an and av: attrs.append((pid, parent, wvid, an, av))
            except: pass
        pb.empty()
        if not (prices or stocks): return
        st.info("💾 Salvez...")
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if prices: execute_batch(cursor, """INSERT INTO woo_preturi (product_id, sku, woo_product_id, woo_variation_id, regular_price, sale_price) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE SET product_id=EXCLUDED.product_id, sku=EXCLUDED.sku, regular_price=EXCLUDED.regular_price, sale_price=EXCLUDED.sale_price, last_sync=NOW()""", prices)
            if stocks: execute_batch(cursor, """INSERT INTO woo_stoc (product_id, sku, woo_product_id, woo_variation_id, stock_quantity) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE SET product_id=EXCLUDED.product_id, sku=EXCLUDED.sku, stock_quantity=EXCLUDED.stock_quantity, last_sync=NOW()""", stocks)
            if attrs: execute_batch(cursor, """INSERT INTO woo_variation_attributes (product_id, woo_product_id, woo_variation_id, attribute_name, attribute_value) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (woo_product_id, woo_variation_id, attribute_name) DO UPDATE SET product_id=EXCLUDED.product_id, attribute_value=EXCLUDED.attribute_value""", attrs)
            conn.commit()
            cursor.close()
            conn.close()
            st.success("✅ Quick Refresh complet")
        except Exception as e:
            st.error(f"Eroare: {e}")

def fetch_and_stage_products_bulk(session_id: str):
    stats = {"total_products_fetched": 0, "simple_products": 0, "variations_inserted": 0, "errors": 0, "duration": 0}
    pb, status = st.progress(0), st.empty()
    try:
        status.info("🚀 Fetch Woo...")
        start = time.time()
        resp = wcapi.get("products/export-full")
        if resp.status_code != 200: st.error(f"❌ Eroare API: {resp.status_code}"); return stats
        data = resp.json()
        if not data.get("success"): st.error(f"❌ Export eșuat: {data.get('message')}"); return stats
        products = data.get("products", [])
        stats["total_products_fetched"] = len(products)
        status.success(f"✅ {len(products)} produse ({time.time()-start:.2f}s)")
        if not products: return stats
        conn = get_db_connection()
        cursor = conn.cursor()
        total = len(products)
        for idx, p in enumerate(products):
            pb.progress((idx+1)/total)
            try:
                pt, wpid, wvid, name, sku = p.get("product_type","simple"), p.get("woo_product_id"), p.get("woo_variation_id"), p.get("name","Fără nume"), p.get("sku","").strip()
                rp, sp, sq = safe_decimal(p.get("regular_price"),0), safe_decimal(p.get("sale_price")) if p.get("sale_price") else None, safe_int(p.get("stock_quantity"),0)
                full_name = compose_variation_name(p.get("parent_name",""), p.get("attributes",[])) if pt=="variation" else name
                stats[f"{pt}s_inserted" if pt=="simple" else "variations_inserted"] += 1
                parent_conflict = p.get("parent_id") if pt=="variation" else wpid
                cursor.execute("""INSERT INTO woo_staging_raw (import_session_id,woo_product_id,woo_variation_id,product_type,parent_name,sku,regular_price,sale_price,stock_quantity,attributes,raw_data) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT (import_session_id,woo_product_id,woo_variation_id) DO UPDATE SET parent_name=EXCLUDED.parent_name,sku=EXCLUDED.sku,regular_price=EXCLUDED.regular_price,sale_price=EXCLUDED.sale_price,stock_quantity=EXCLUDED.stock_quantity""",(session_id,parent_conflict,wvid,pt,full_name,sku or None,rp,sp,sq,json.dumps(p.get("attributes",[])),json.dumps(p)))
            except: stats["errors"] += 1
        conn.commit()
        cursor.close()
        conn.close()
        stats["duration"] = time.time()-start
        pb.empty()
        status.success(f"✅ Staging complet")
    except Exception as e: st.error(f"❌ Eroare FAZA 1: {e}")
    return stats

def run_sku_matching_and_autocreate(session_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM match_skus_for_session(%s)", (session_id,))
        matches = cursor.fetchall()
        if not matches: return {}
        match_data, created = [], 0
        for m in matches:
            sid, sku, pid, mt, ra = m
            if mt == "unknown" and sku:
                cursor.execute("SELECT parent_name FROM woo_staging_raw WHERE id=%s",(sid,))
                r, name = cursor.fetchone(), f"Produs {sku}"
                if r: name = r[0]
                new_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO product (id,name) VALUES (%s,%s)",(new_id,name))
                cursor.execute("INSERT INTO product_sku (sku,product_id,is_primary) VALUES (%s,%s,true)",(sku,new_id))
                pid, mt, ra, created = new_id, "auto_created", False, created+1
            match_data.append((session_id,sid,sku,pid,mt,ra))
        if match_data:
            execute_batch(cursor,"""INSERT INTO woo_staging_matched (import_session_id,staging_raw_id,sku,product_id,match_type,requires_action) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (import_session_id,staging_raw_id) DO UPDATE SET sku=EXCLUDED.sku,product_id=EXCLUDED.product_id,match_type=EXCLUDED.match_type,requires_action=EXCLUDED.requires_action""",match_data)
            conn.commit()
        cursor.execute("SELECT * FROM v_import_status WHERE import_session_id=%s",(session_id,))
        s = cursor.fetchone()
        cursor.close()
        conn.close()
        if s: return {"total":s[1],"matched_primary":s[2],"matched_alias":s[3],"matched_remembered":s[4],"unknown":s[5],"duplicates":s[6],"errors":s[7],"pending_actions":s[8],"auto_created":created}
        return {}
    except: return {}

def get_pending_aliases(session_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""SELECT sm.id as match_id, sm.sku, sr.parent_name, p.name as existing_product_name FROM woo_staging_matched sm JOIN woo_staging_raw sr ON sr.id = sm.staging_raw_id LEFT JOIN product p ON p.id = sm.product_id WHERE sm.import_session_id=%s AND sm.match_type='alias' AND sm.requires_action=true AND sm.action_taken IS NULL ORDER BY sr.parent_name""", (session_id,))
        return cursor.fetchall()
    except: return []

def mark_alias_action(match_id, action):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE woo_staging_matched SET action_taken=%s, requires_action=false WHERE id=%s", (action,match_id))
        conn.commit()
        return True
    except: return False

def finalize_import(session_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM woo_variation_attributes; DELETE FROM woo_stoc; DELETE FROM woo_preturi;")
        cursor.execute("SELECT * FROM finalize_import(%s)", (session_id,))
        r = cursor.fetchone()
        conn.commit()
        if r: return {"prices_inserted":r[0],"stock_inserted":r[1],"attributes_inserted":r[2]}
    except: return {}

# =========================
#   SMARTBILL FUNCTIONS
# =========================
def get_smartbill_stocks(email, token, cif):
    try:
        r = requests.get("https://ws.smartbill.ro/SBORO/api/stocks",auth=HTTPBasicAuth(email,token),headers={"Accept":"application/json"},params={"cif":cif},timeout=30)
        return r.json() if r.status_code == 200 else None
    except: return None

def get_smartbill_entries(email, token, cif):
    try:
        r = requests.get("https://ws.smartbill.ro/SBORO/api/documents",auth=HTTPBasicAuth(email,token),headers={"Accept":"application/json"},params={"cif":cif,"type":"nir"},timeout=60)
        return r.json() if r.status_code == 200 else None
    except: return None

def process_smartbill_data(data):
    sb = {}
    if not data or "list" not in data: return sb
    for w in data["list"]:
        for p in w.get("products",[]):
            code = p.get("productCode","").strip() or p.get("code","").strip()
            if code: sb[code] = {"name": p.get("productName","") or p.get("name",""), "stock":float(p.get("quantity",0))}
    return sb

def get_smartbill_decisions():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT smartbill_sku, decision_type, product_id FROM smartbill_sku_mapping_decisions")
        return {r[0]:{"decision":r[1],"product_id":r[2]} for r in cursor.fetchall()}
    except: return {}

def save_smartbill_decision(sku, action, name=None, product_id=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM smartbill_sku_mapping_decisions WHERE smartbill_sku=%s",(sku,))
        if action == "creaza_nou":
            nid = str(uuid.uuid4())
            cursor.execute("INSERT INTO product (id,name) VALUES (%s,%s)",(nid,name or sku))
            cursor.execute("INSERT INTO product_sku (sku,product_id,is_primary) VALUES (%s,%s,true)",(sku,nid))
            cursor.execute("INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku,product_id,decision_type) VALUES (%s,%s,%s)",(sku,nid,action))
        elif action=="adauga_la_sku_existent" and product_id:
            cursor.execute("INSERT INTO product_sku (sku,product_id,is_primary) VALUES (%s,%s,false)",(sku,product_id))
            cursor.execute("INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku,product_id,decision_type) VALUES (%s,%s,%s)",(sku,product_id,action))
        else:
            cursor.execute("INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku,decision_type) VALUES (%s,%s)",(sku,action))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"Eroare: {e}")
        return False

def sync_smartbill_data():
    config = init_smartbill()
    if st.button("📥 Fetch stocuri și intrări din SmartBill", use_container_width=True):
        with st.spinner("Se preiau datele..."):
            stock_data = get_smartbill_stocks(config["email"],config["token"],config["cif"])
            entries_data = get_smartbill_entries(config["email"],config["token"],config["cif"])
            if stock_data: st.session_state.smartbill_data = process_smartbill_data(stock_data)
            if entries_data: st.session_state.smartbill_entries = entries_data.get("list",[])
            st.session_state.smartbill_page = 1
    
    if not st.session_state.get("smartbill_data"): return
    sb_products, decisions = st.session_state.smartbill_data, get_smartbill_decisions()
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku,id,name FROM product")
        all_prods = cursor.fetchall()
        sku_to_product = {r[0]:r[1] for r in all_prods}
        product_options = {f"{r[2]} ({r[0]})":r[1] for r in all_prods}
        conn.close()
    except: return
    stocks, unmatched = [], []
    for sku, info in sb_products.items():
        dec_info, dec = decisions.get(sku,{}), decisions.get(sku,{}).get("decision")
        if sku in sku_to_product: stocks.append((sku_to_product[sku],sku,Decimal(info["stock"])))
        elif dec in ["ignora","asteapta"]: continue
        elif dec in ["creaza_nou", "adauga_la_sku_existent"] and dec_info.get("product_id"):
            stocks.append((dec_info["product_id"],sku,Decimal(info["stock"])))
        else: unmatched.append({"sku":sku,"name":info["name"],"stock":info["stock"]})
    
    if unmatched:
        st.warning(f"⚠️ {len(unmatched)} SKU-uri nemapate")
        page_size=10
        total_pages = max(1,(len(unmatched)+page_size-1)//page_size)
        c1,c2 = st.columns([1,5])
        st.session_state.smartbill_page = c1.number_input("Pagina",1,total_pages,st.session_state.smartbill_page)
        page_items = unmatched[(st.session_state.smartbill_page-1)*page_size:(st.session_state.smartbill_page-1)*page_size+page_size]
        action_map = {"Creează nou":"creaza_nou","Adaugă la SKU existent":"adauga_la_sku_existent","Ignoră":"ignora"}
        with st.expander("SKU-uri necunoscute",expanded=True):
            for item in page_items:
                c1,c2,c3,c4 = st.columns([2,4,2,3])
                c1.write(f"**{item['sku']}**"); c2.write(item['name']); c3.write(item['stock'])
                action = c4.selectbox("Acțiune",["Alege..."]+list(action_map.keys()),key=f"act_{item['sku']}",label_visibility="collapsed")
                if action == "Adaugă la SKU existent": st.selectbox("Selectează produs",[""]+list(product_options.keys()),key=f"prod_{item['sku']}")
            if st.button("💾 Salvează deciziile paginii", use_container_width=True):
                for item in page_items:
                    action = st.session_state.get(f"act_{item['sku']}")
                    if action in action_map:
                        pid = st.session_state.get(f"prod_{item['sku']}") if action=="Adaugă la SKU existent" else None
                        save_smartbill_decision(item['sku'],action_map[action],item['name'],pid)
                st.rerun()

    if st.button("💾 Salvează stocuri și prețuri în DB", type="primary", use_container_width=True):
        entries = []
        if st.session_state.get("smartbill_entries"):
            for e in st.session_state.smartbill_entries:
                for p in e.get("products",[]):
                    sku = p.get("code","").strip()
                    if sku in sku_to_product: entries.append((sku_to_product[sku],sku,e.get("date"),safe_decimal(p.get("quantity")),safe_decimal(p.get("price")),e.get("number"),e.get("supplier",{}).get("name")))
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if stocks:
                cursor.execute("DELETE FROM smartbill_stoc")
                execute_batch(cursor,"INSERT INTO smartbill_stoc (product_id,sku,stock_quantity) VALUES (%s,%s,%s)",stocks)
            if entries:
                cursor.execute("DELETE FROM smartbill_pret_intrare")
                execute_batch(cursor,"INSERT INTO smartbill_pret_intrare (product_id,sku,data_intrare,cantitate,pret_unitar,nr_document,furnizor) VALUES (%s,%s,%s,%s,%s,%s,%s)",entries)
            conn.commit()
            st.session_state.smartbill_data, st.session_state.smartbill_entries = None, None
            st.rerun()
        except: st.error("Eroare DB")

# =========================
#   UI
# =========================
st.markdown("## 🛒 WooCommerce Import")
c1,c2,c3 = st.columns(3)
with c1:
    if st.button("🚀 Full Import",type="primary",use_container_width=True):
        st.session_state.import_session_id = str(uuid.uuid4())
        st.session_state.import_phase = "extracting"
        st.rerun()
with c2:
    if st.button("🔄 Rulează Matching",use_container_width=True):
        sid = get_latest_session_id()
        if sid: st.session_state.import_session_id,st.session_state.import_phase = sid,"matching"; st.rerun()
with c3:
    if st.button("⚡ Quick Refresh",use_container_width=True):
        quick_refresh_prices_and_stock()

if st.session_state.get("import_phase"):
    phase=st.session_state.import_phase
    st.info(f"Status: {phase}")
    if phase=="extracting":
        with st.spinner("Extrag..."):
            st.session_state.import_phase = "matching"; st.rerun()
    elif phase=="matching":
        with st.spinner("Matching..."):
            st.session_state.import_phase = "reconciling"; st.rerun()
    elif phase=="reconciling":
        stats = run_sku_matching_and_autocreate(st.session_state.import_session_id)
        if stats.get("pending_actions",0)>0:
            for item in get_pending_aliases(st.session_state.import_session_id):
                with st.expander(f"{item['sku']}"):
                    if st.button("Confirmă",key=f"c_{item['match_id']}"): mark_alias_action(item["match_id"],"confirmed"); st.rerun()
        else:
            if st.button("Finalizează"): st.session_state.import_phase="finalizing"; st.rerun()
    elif phase=="finalizing":
        finalize_import(st.session_state.import_session_id)
        st.session_state.import_phase="done"; st.rerun()

st.markdown("## 📊 SmartBill Sync")
sync_smartbill_data()
