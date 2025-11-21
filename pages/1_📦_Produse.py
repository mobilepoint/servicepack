# pages/1_📦_Produse.py
import uuid
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from woocommerce import API
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd
import time

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
            'email': sbcfg["EMAIL"],
            'token': sbcfg["TOKEN"],
            'cif': sbcfg["CIF"]
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
if "smartbill_page" not in st.session_state:
    st.session_state["smartbill_page"] = 1

# =========================
#   HELPER FUNCTIONS
# =========================
def get_db_connection():
    return psycopg2.connect(get_pg_connection_string())

def safe_decimal(value, default=0):
    if value is None or value == '' or value == 'null':
        return Decimal(default)
    try:
        cleaned = str(value).strip().replace(',', '.')
        if cleaned == '' or cleaned == '.':
            return Decimal(default)
        return Decimal(cleaned)
    except (ValueError, TypeError, InvalidOperation):
        return Decimal(default)

def safe_int(value, default=0):
    if value is None or value == '' or value == 'null':
        return default
    try:
        return int(float(str(value)))
    except (ValueError, TypeError):
        return default

def compose_variation_name(parent_name: str, attributes: list) -> str:
    if not attributes:
        return parent_name
    attr_parts = []
    for attr in attributes:
        value = attr.get('option', '')
        if value:
            attr_parts.append(value)
    if attr_parts:
        return f"{parent_name} - {' - '.join(attr_parts)}"
    return parent_name

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
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result else None
    except:
        return None

# =========================
#   WOOCOMMERCE FUNCTIONS (păstrează tot ce ai deja aici - quick_refresh, fetch_and_stage, etc)
# =========================
def quick_refresh_prices_and_stock():
    with st.container():
        st.markdown("### ⚡ Quick Refresh WooCommerce")
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT ps.sku, ps.product_id FROM product_sku ps")
            known_skus = cursor.fetchall()
            cursor.close()
            conn.close()
            if not known_skus:
                st.warning("Nu am găsit SKU-uri!")
                return
            st.success(f"✅ {len(known_skus)} SKU-uri")
            sku_to_product = {row['sku']: row['product_id'] for row in known_skus}
        except Exception as e:
            st.error(f"Eroare: {e}")
            return
        
        st.info("🚀 Fetch din WooCommerce...")
        try:
            start_time = time.time()
            response = wcapi.get("products/export-full")
            if response.status_code != 200:
                st.error(f"Eroare API: {response.status_code}")
                return
            data = response.json()
            if not data.get('success'):
                st.error(f"Export eșuat: {data.get('message')}")
                return
            woo_products = data.get('products', [])
            st.success(f"✅ {len(woo_products)} produse în {time.time() - start_time:.2f}s")
        except Exception as e:
            st.error(f"Eroare fetch: {e}")
            return
        
        st.info("🔄 Procesez...")
        prices_data = []
        stock_data = []
        attributes_data = []
        matched_count = 0
        progress_bar = st.progress(0)
        
        for idx, woo_product in enumerate(woo_products):
            progress_bar.progress((idx + 1) / len(woo_products))
            try:
                sku = woo_product.get('sku', '').strip()
                if not sku or sku not in sku_to_product:
                    continue
                product_id = sku_to_product[sku]
                matched_count += 1
                product_type = woo_product.get('product_type', 'simple')
                woo_product_id = woo_product.get('woo_product_id')
                woo_variation_id = woo_product.get('woo_variation_id')
                parent_id = woo_product.get('parent_id')
                regular_price = safe_decimal(woo_product.get('regular_price'), 0)
                sale_price_raw = woo_product.get('sale_price')
                sale_price = safe_decimal(sale_price_raw) if sale_price_raw else None
                stock_qty = safe_int(woo_product.get('stock_quantity'), 0)
                
                prices_data.append((
                    product_id, sku,
                    parent_id if product_type == 'variation' else woo_product_id,
                    woo_variation_id if product_type == 'variation' else None,
                    regular_price, sale_price
                ))
                stock_data.append((
                    product_id, sku,
                    parent_id if product_type == 'variation' else woo_product_id,
                    woo_variation_id if product_type == 'variation' else None,
                    stock_qty
                ))
                if product_type == 'variation' and woo_product.get('attributes'):
                    for attr in woo_product.get('attributes', []):
                        attr_name = attr.get('name', '')
                        attr_value = attr.get('option', '')
                        if attr_name and attr_value:
                            attributes_data.append((
                                product_id, parent_id, woo_variation_id,
                                attr_name, attr_value
                            ))
            except:
                pass
        progress_bar.empty()
        
        if prices_data or stock_data:
            st.info("💾 Salvez...")
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                if prices_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_preturi 
                        (product_id, sku, woo_product_id, woo_variation_id, regular_price, sale_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE
                        SET product_id = EXCLUDED.product_id, sku = EXCLUDED.sku,
                            regular_price = EXCLUDED.regular_price, sale_price = EXCLUDED.sale_price,
                            last_sync = NOW()
                    """, prices_data, page_size=500)
                if stock_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_stoc 
                        (product_id, sku, woo_product_id, woo_variation_id, stock_quantity)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE
                        SET product_id = EXCLUDED.product_id, sku = EXCLUDED.sku,
                            stock_quantity = EXCLUDED.stock_quantity, last_sync = NOW()
                    """, stock_data, page_size=500)
                if attributes_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_variation_attributes 
                        (product_id, woo_product_id, woo_variation_id, attribute_name, attribute_value)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (woo_product_id, woo_variation_id, attribute_name) DO UPDATE
                        SET product_id = EXCLUDED.product_id, attribute_value = EXCLUDED.attribute_value
                    """, attributes_data, page_size=500)
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Complet!")
                st.balloons()
                c1, c2, c3 = st.columns(3)
                c1.metric("💰 Prețuri", len(prices_data))
                c2.metric("📦 Stocuri", len(stock_data))
                c3.metric("✅ Match-uri", matched_count)
            except Exception as e:
                st.error(f"Eroare: {e}")
        else:
            st.warning("Nu am găsit date")

# =========================
#   SMARTBILL FUNCTIONS
# =========================
def get_smartbill_stocks(email, token, cif):
    try:
        r = requests.get(
            "https://ws.smartbill.ro/SBORO/api/stocks",
            auth=HTTPBasicAuth(email, token),
            headers={"Accept": "application/json"},
            params={"cif": cif, "date": datetime.now().strftime("%Y-%m-%d")},
            timeout=30
        )
        if r.status_code == 200:
            return r.json()
        else:
            st.error(f"SmartBill API error: {r.status_code} - {r.text}")
            return None
    except Exception as e:
        st.error(f"Error fetching SmartBill stocks: {e}")
        return None

def process_smartbill_data(data):
    sb_dict = {}
    if not data:
        return sb_dict
    products = []
    if isinstance(data, dict) and "list" in data:
        for w in data["list"]:
            if isinstance(w, dict) and "products" in w:
                products.extend(w["products"])
    for p in products:
        if not isinstance(p, dict):
            continue
        code = p.get('productCode', '').strip() or p.get("code", "").strip()
        if not code:
            continue
        sb_dict[code] = {
            'name': p.get('productName', '') or p.get("name", ""),
            'stock': float(p.get('quantity', 0))
        }
    return sb_dict

def get_smartbill_decisions():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT smartbill_sku, decision_type, product_id FROM smartbill_sku_mapping_decisions")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        out = {}
        for row in rows:
            out[row[0]] = {"decision": row[1], "product_id": row[2]}
        return out
    except Exception as e:
        st.error(f"Eroare citire decizii SmartBill: {e}")
        return {}

def save_smartbill_decision(sku, action, name=None, product_id=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Șterge decizia veche
        cursor.execute("DELETE FROM smartbill_sku_mapping_decisions WHERE smartbill_sku = %s", (sku,))
        
        if action == "creaza_nou":
            new_product_id = str(uuid.uuid4())
            cursor.execute("INSERT INTO product (id, name) VALUES (%s, %s)", (new_product_id, name or sku))
            cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)", (sku, new_product_id))
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type, decided_at)
                VALUES (%s, %s, %s, NOW())
            """, (sku, new_product_id, action))
        elif action == "asociaza_la_sku" and product_id:
            # Asociază SKU-ul la un produs existent (fără să creeze produs nou)
            cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, false) ON CONFLICT (sku) DO UPDATE SET product_id = EXCLUDED.product_id", (sku, product_id))
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type, decided_at)
                VALUES (%s, %s, %s, NOW())
            """, (sku, product_id, action))
        else:
            # Pentru ignora/asteapta
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, decision_type, decided_at)
                VALUES (%s, %s, NOW())
            """, (sku, action))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        if conn:
            conn.rollback()
        st.error(f"❌ Eroare salvare decizie pentru {sku}: {e}")
        return False
    finally:
        if conn:
            conn.close()

def parse_smartbill_xlsx(uploaded_file):
    """Parsează fișierul XLSX SmartBill Stoc la zi"""
    try:
        df = pd.read_excel(uploaded_file, sheet_name=0)
        
        # Identifică rândul cu antetele coloanelor
        header_row = None
        for idx, row in df.iterrows():
            if 'Cod produs' in str(row.values) or 'productCode' in str(row.values):
                header_row = idx
                break
        
        if header_row is None:
            st.error("Nu am găsit antetul coloanelor în fișier")
            return None
        
        # Re-citește cu header corect
        df = pd.read_excel(uploaded_file, sheet_name=0, header=header_row)
        
        # Curăță numele coloanelor
        df.columns = df.columns.str.strip()
        
        # Mapare coloane (adaptează după nevoie)
        col_map = {}
        for col in df.columns:
            col_lower = col.lower()
            if 'cod' in col_lower and 'produs' in col_lower:
                col_map['sku'] = col
            elif 'cantitate' in col_lower or 'stoc' in col_lower:
                col_map['cantitate'] = col
            elif 'pret' in col_lower and 'unitar' in col_lower:
                col_map['pret_unitar'] = col
            elif 'data' in col_lower:
                col_map['data'] = col
        
        if 'sku' not in col_map:
            st.error("Nu am găsit coloana pentru COD PRODUS")
            return None
        
        # Extrage datele
        entries = []
        for _, row in df.iterrows():
            try:
                sku = str(row[col_map['sku']]).strip()
                if not sku or sku == 'nan' or sku == '':
                    continue
                
                cantitate = safe_decimal(row.get(col_map.get('cantitate'), 0))
                pret_unitar = safe_decimal(row.get(col_map.get('pret_unitar'), 0))
                data_doc = row.get(col_map.get('data')) if 'data' in col_map else datetime.now().date()
                
                if cantitate > 0 and pret_unitar > 0:
                    entries.append({
                        'sku': sku,
                        'cantitate': cantitate,
                        'pret_unitar': pret_unitar,
                        'data_document': data_doc
                    })
            except:
                continue
        
        return entries
    except Exception as e:
        st.error(f"Eroare parsare fișier: {e}")
        return None

def sync_smartbill_data():
    config = init_smartbill()
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Fetch stocuri din SmartBill API", use_container_width=True, type="primary"):
            with st.spinner("Se preiau datele..."):
                data = get_smartbill_stocks(config['email'], config['token'], config['cif'])
                if data:
                    st.session_state.smartbill_data = process_smartbill_data(data)
                    st.session_state.smartbill_page = 1
                    st.success(f"✅ {len(st.session_state.smartbill_data)} produse citite")
                else:
                    st.error("❌ Eroare fetch SmartBill")
    
    with col2:
        uploaded_file = st.file_uploader("📄 Upload raport XLSX (prețuri intrare)", type=['xls', 'xlsx'])
        if uploaded_file and st.button("💾 Procesează XLSX", use_container_width=True):
            with st.spinner("Procesez XLSX..."):
                entries = parse_smartbill_xlsx(uploaded_file)
                if entries:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        
                        # Citește SKU-uri cunoscute
                        cursor.execute("SELECT sku, product_id FROM product_sku")
                        sku_map = {r[0]: r[1] for r in cursor.fetchall()}
                        
                        # Filtrează doar SKU-uri cunoscute
                        to_insert = []
                        for entry in entries:
                            if entry['sku'] in sku_map:
                                to_insert.append((
                                    sku_map[entry['sku']],
                                    entry['sku'],
                                    entry['data_document'],
                                    entry['cantitate'],
                                    entry['pret_unitar']
                                ))
                        
                        if to_insert:
                            cursor.execute("DELETE FROM smartbill_pret_intrare")
                            execute_batch(cursor, """
                                INSERT INTO smartbill_pret_intrare 
                                (product_id, sku, data_document, cantitate, pret_unitar)
                                VALUES (%s, %s, %s, %s, %s)
                            """, to_insert, page_size=500)
                            conn.commit()
                            st.success(f"✅ Salvate {len(to_insert)} intrări de preț")
                        else:
                            st.warning("⚠️ Niciun SKU din XLSX nu a fost găsit în baza de date")
                        
                        cursor.close()
                        conn.close()
                    except Exception as e:
                        st.error(f"Eroare salvare XLSX: {e}")

    if not st.session_state.get("smartbill_data"):
        return

    sb_products = st.session_state.smartbill_data
    decisions = get_smartbill_decisions()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku, id, name FROM product ORDER BY name")
        all_prods = cursor.fetchall()
        sku_to_product = {p[0]: p[1] for p in all_prods}
        product_options = {f'{p[2]} ({p[0]})': p[1] for p in all_prods}
        cursor.close()
        conn.close()
    except Exception as e:
        st.error(f"Eroare DB: {e}")
        return

    stock_data = []
    unmatched = []
    
    for sku, info in sb_products.items():
        dec_info = decisions.get(sku, {})
        dec = dec_info.get("decision")
        
        if sku in sku_to_product:
            stock_data.append((sku_to_product[sku], sku, Decimal(info["stock"])))
        elif dec == "ignora":
            continue
        elif dec in ["creaza_nou", "asociaza_la_sku"] and dec_info.get("product_id"):
            stock_data.append((dec_info["product_id"], sku, Decimal(info["stock"])))
        else:
            unmatched.append({"sku": sku, "name": info["name"], "stock": info["stock"]})

    if unmatched:
        st.warning(f"⚠️ {len(unmatched)} SKU-uri nemapate")
        
        page_size = 10
        total_pages = max(1, (len(unmatched) + page_size - 1) // page_size)
        
        col_page, _ = st.columns([1, 5])
        page = col_page.number_input("Pagina", 1, total_pages, st.session_state.smartbill_page)
        st.session_state.smartbill_page = page
        
        start_idx = (page - 1) * page_size
        page_items = unmatched[start_idx : start_idx + page_size]

        action_map = {
            "Creează nou": "creaza_nou",
            "Asociază la SKU": "asociaza_la_sku",
            "Ignoră": "ignora"
        }
        
        with st.expander("📋 SKU-uri necunoscute", expanded=True):
            for item in page_items:
                sku = item["sku"]
                c1, c2, c3, c4 = st.columns([2, 4, 1, 3])
                c1.write(f"**{sku}**")
                c2.write(item["name"])
                c3.write(item["stock"])
                
                action = c4.selectbox(
                    "Acțiune",
                    ["Alege..."] + list(action_map.keys()),
                    key=f"act_{sku}",
                    label_visibility="collapsed"
                )
                
                if action == "Asociază la SKU":
                    st.selectbox(
                        "Selectează produsul existent",
                        [""] + list(product_options.keys()),
                        key=f"prod_{sku}"
                    )

            if st.button("💾 Salvează deciziile paginii", use_container_width=True):
                for item in page_items:
                    sku = item["sku"]
                    action = st.session_state.get(f"act_{sku}", "Alege...")
                    
                    if action in action_map:
                        code = action_map[action]
                        pid = None
                        
                        if code == "asociaza_la_sku":
                            sel_key = st.session_state.get(f"prod_{sku}")
                            if sel_key:
                                pid = product_options[sel_key]
                        
                        save_smartbill_decision(sku, code, item["name"], pid)
                
                st.success("✅ Decizii salvate!")
                time.sleep(0.5)
                st.rerun()

    if not unmatched and stock_data:
        st.success("✅ Toate SKU-urile sunt mapate!")
        if st.button("💾 Salvează stocurile în DB", type="primary", use_container_width=True):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM smartbill_stoc")
                execute_batch(cursor, """
                    INSERT INTO smartbill_stoc (product_id, sku, stock_quantity)
                    VALUES (%s, %s, %s)
                """, stock_data, page_size=500)
                conn.commit()
                cursor.close()
                conn.close()
                st.success("✅ Stocuri salvate!")
                st.session_state.smartbill_data = None
                st.balloons()
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"Eroare salvare: {e}")

# =========================
#   UI PRINCIPAL
# =========================
st.markdown("## 🛒 WooCommerce Import")

col1, col2, col3 = st.columns(3)
with col3:
    if st.button("⚡ Quick Refresh", use_container_width=True):
        quick_refresh_prices_and_stock()

st.divider()

st.markdown("## 📊 SmartBill Sync")
sync_smartbill_data()
