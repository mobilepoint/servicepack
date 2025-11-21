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
# CONNECTIONS
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
# SESSION STATE
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
if "smartbill_selections" not in st.session_state:
    st.session_state["smartbill_selections"] = {}

# =========================
# HELPER FUNCTIONS
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
# WOOCOMMERCE FUNCTIONS
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
                
                # ȘTERGEM DATELE VECHI ÎNAINTE DE A INSERA DATELE NOI
                cursor.execute("DELETE FROM woo_preturi")
                cursor.execute("DELETE FROM woo_stoc")
                cursor.execute("DELETE FROM woo_variation_attributes")
                
                if prices_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_preturi
                        (product_id, sku, woo_product_id, woo_variation_id, regular_price, sale_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, prices_data, page_size=500)
                
                if stock_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_stoc
                        (product_id, sku, woo_product_id, woo_variation_id, stock_quantity)
                        VALUES (%s, %s, %s, %s, %s)
                    """, stock_data, page_size=500)
                
                if attributes_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_variation_attributes
                        (product_id, woo_product_id, woo_variation_id, attribute_name, attribute_value)
                        VALUES (%s, %s, %s, %s, %s)
                    """, attributes_data, page_size=500)
                
                conn.commit()
                cursor.close()
                conn.close()
                
                st.success(f"✅ Complet!")
                
                c1, c2, c3 = st.columns(3)
                c1.metric("💰 Prețuri", len(prices_data))
                c2.metric("📦 Stocuri", len(stock_data))
                c3.metric("✅ Match-uri", matched_count)
            except Exception as e:
                st.error(f"Eroare: {e}")
        else:
            st.warning("Nu am găsit date")

def fetch_and_stage_products_bulk(session_id: str):
    stats = {'total_products_fetched': 0, 'simple_products': 0, 'variations_inserted': 0, 'errors': 0, 'duration': 0}
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        status_text.info("🚀 Fetch BULK...")
        start_time = time.time()
        
        response = wcapi.get("products/export-full")
        if response.status_code != 200:
            st.error(f"❌ Eroare API: {response.status_code}")
            return stats
        
        data = response.json()
        if not data.get('success'):
            st.error(f"❌ Export eșuat: {data.get('message')}")
            return stats
        
        woo_products = data.get('products', [])
        stats['total_products_fetched'] = len(woo_products)
        status_text.success(f"✅ {len(woo_products)} produse în {time.time() - start_time:.2f}s!")
        
        if not woo_products:
            return stats
        
        status_text.info("💾 Scriu în staging...")
        conn = get_db_connection()
        cursor = conn.cursor()
        
        total_items = len(woo_products)
        processed = 0
        
        for product in woo_products:
            processed += 1
            progress_bar.progress(processed / total_items)
            try:
                product_type = product.get('product_type', 'simple')
                woo_product_id = product.get('woo_product_id')
                woo_variation_id = product.get('woo_variation_id')
                name = product.get('name', 'Produs fără nume')
                sku = product.get('sku', '').strip()
                
                regular_price = safe_decimal(product.get('regular_price'), 0)
                sale_price_raw = product.get('sale_price')
                sale_price = safe_decimal(sale_price_raw) if sale_price_raw else None
                stock_qty = safe_int(product.get('stock_quantity'), 0)
                
                if product_type == 'variation':
                    parent_name = product.get('parent_name', '')
                    attributes = product.get('attributes', [])
                    full_name = compose_variation_name(parent_name, attributes)
                    stats['variations_inserted'] += 1
                else:
                    full_name = name
                    stats['simple_products'] += 1
                
                parent_id_for_conflict = product.get('parent_id') if product_type == 'variation' else woo_product_id
                
                cursor.execute("""
                    INSERT INTO woo_staging_raw
                    (import_session_id, woo_product_id, woo_variation_id, product_type,
                     parent_name, sku, regular_price, sale_price, stock_quantity, attributes, raw_data)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    ON CONFLICT (import_session_id, woo_product_id, woo_variation_id) DO UPDATE
                    SET parent_name = EXCLUDED.parent_name, sku = EXCLUDED.sku,
                        regular_price = EXCLUDED.regular_price, sale_price = EXCLUDED.sale_price,
                        stock_quantity = EXCLUDED.stock_quantity
                """, (session_id, parent_id_for_conflict, woo_variation_id, product_type,
                      full_name, sku if sku else None, regular_price, sale_price, stock_qty,
                      json.dumps(product.get('attributes', [])), json.dumps(product)))
                
                if processed % 100 == 0:
                    conn.commit()
                    status_text.info(f"💾 {processed}/{total_items}...")
            except:
                stats['errors'] += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        stats['duration'] = time.time() - start_time
        progress_bar.empty()
        status_text.success(f"✅ Extract: {stats['variations_inserted'] + stats['simple_products']} în {stats['duration']:.2f}s")
    except Exception as e:
        st.error(f"❌ Eroare FAZA 1: {e}")
        stats['errors'] += 1
    
    return stats

def run_sku_matching_and_autocreate(session_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("🔍 Matching SKU...")
        cursor.execute("SELECT * FROM match_skus_for_session(%s)", (session_id,))
        matches = cursor.fetchall()
        
        if not matches:
            st.warning("⚠️ Nu am găsit match-uri!")
            cursor.close()
            conn.close()
            return {}
        
        match_data = []
        created_products = 0
        
        for match in matches:
            staging_raw_id, sku, product_id, match_type, requires_action = match
            
            if match_type == 'unknown' and sku:
                cursor.execute("SELECT parent_name FROM woo_staging_raw WHERE id = %s", (staging_raw_id,))
                result = cursor.fetchone()
                product_name = result[0] if result else f"Produs {sku}"
                
                new_product_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO product (id, name) VALUES (%s, %s)", (new_product_id, product_name))
                cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)", (sku, new_product_id))
                
                product_id = new_product_id
                match_type = 'auto_created'
                requires_action = False
                created_products += 1
            
            match_data.append((session_id, staging_raw_id, sku, product_id, match_type, requires_action))
        
        if match_data:
            execute_batch(cursor, """
                INSERT INTO woo_staging_matched
                (import_session_id, staging_raw_id, sku, product_id, match_type, requires_action)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (import_session_id, staging_raw_id) DO UPDATE
                SET sku = EXCLUDED.sku, product_id = EXCLUDED.product_id,
                    match_type = EXCLUDED.match_type, requires_action = EXCLUDED.requires_action
            """, match_data, page_size=1000)
        
        conn.commit()
        st.success(f"✅ Inserate {len(match_data)} match-uri | 🆕 Creeat {created_products} produse")
        
        cursor.execute("SELECT * FROM v_import_status WHERE import_session_id = %s", (session_id,))
        stats = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if stats:
            return {
                'total': stats[1], 'matched_primary': stats[2], 'matched_alias': stats[3],
                'matched_remembered': stats[4], 'unknown': stats[5], 'duplicates': stats[6],
                'errors': stats[7], 'pending_actions': stats[8], 'auto_created': created_products
            }
        return {}
    except Exception as e:
        st.error(f"❌ Eroare FAZA 2: {e}")
        return {}

def get_pending_aliases(session_id: str, limit: int = 50):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT sm.id as match_id, sm.sku, sm.product_id, sr.parent_name, p.name as existing_product_name
            FROM woo_staging_matched sm
            JOIN woo_staging_raw sr ON sr.id = sm.staging_raw_id
            LEFT JOIN product p ON p.id = sm.product_id
            WHERE sm.import_session_id = %s AND sm.match_type = 'alias' AND sm.requires_action = true AND sm.action_taken IS NULL
            ORDER BY sr.parent_name LIMIT %s
        """, (session_id, limit))
        items = cursor.fetchall()
        cursor.close()
        conn.close()
        return items
    except Exception as e:
        st.error(f"❌ Eroare: {e}")
        return []

def mark_alias_action(match_id: str, action: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE woo_staging_matched SET action_taken = %s, requires_action = false WHERE id = %s", (action, match_id))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ Eroare: {e}")
        return False

def finalize_import(session_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("📦 Finalizare...")
        
        # ȘTERGEM DATELE VECHI ÎNAINTE DE FINALIZARE
        cursor.execute("DELETE FROM woo_variation_attributes")
        cursor.execute("DELETE FROM woo_stoc")
        cursor.execute("DELETE FROM woo_preturi")
        
        cursor.execute("SELECT * FROM finalize_import(%s)", (session_id,))
        result = cursor.fetchone()
        
        conn.commit()
        cursor.close()
        conn.close()
        
        if result:
            return {'prices_inserted': result[0], 'stock_inserted': result[1], 'attributes_inserted': result[2]}
        return {}
    except Exception as e:
        st.error(f"❌ Eroare FAZA 4: {e}")
        return {}

# =========================
# SMARTBILL FUNCTIONS
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
        st.error(f"Error fetching SmartBill: {e}")
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
        memory = {}
        for row in rows:
            memory[row[0]] = {"decision": row[1], "product_id": row[2]}
        return memory
    except Exception as e:
        st.error(f"Eroare citire decizii SmartBill: {e}")
        return {}

def save_smartbill_decisions_batch(decisions_list):
    """Salvează un batch de decizii SmartBill"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        saved_count = 0
        for decision in decisions_list:
            sku = decision['sku']
            action = decision['action']
            name = decision.get('name')
            product_id = decision.get('product_id')
            
            if action == "creaza_nou":
                new_product_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO product (id, name) VALUES (%s, %s)", (new_product_id, name or sku))
                cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)", (sku, new_product_id))
                cursor.execute("""
                    INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type, decided_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (smartbill_sku) DO UPDATE SET product_id = EXCLUDED.product_id, decision_type = EXCLUDED.decision_type, decided_at = NOW()
                """, (sku, new_product_id, action))
                saved_count += 1
            elif action == "mapeaza" and product_id:
                # Asociem SKU-ul SmartBill la produsul existent
                cursor.execute("""
                    INSERT INTO product_sku (sku, product_id, is_primary) 
                    VALUES (%s, %s, false) 
                    ON CONFLICT (sku) DO UPDATE SET product_id = EXCLUDED.product_id
                """, (sku, product_id))
                cursor.execute("""
                    INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type, decided_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (smartbill_sku) DO UPDATE SET product_id = EXCLUDED.product_id, decision_type = EXCLUDED.decision_type, decided_at = NOW()
                """, (sku, product_id, action))
                saved_count += 1
            elif action == "ignora":
                cursor.execute("""
                    INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type, decided_at)
                    VALUES (%s, NULL, %s, NOW())
                    ON CONFLICT (smartbill_sku) DO UPDATE SET decision_type = EXCLUDED.decision_type, decided_at = NOW()
                """, (sku, action))
                saved_count += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        return saved_count
    except Exception as e:
        if conn: conn.rollback()
        st.error(f"❌ Eroare salvare decizii: {e}")
        return 0
    finally:
        if conn: conn.close()

def parse_smartbill_xlsx(uploaded_file):
    import pandas as pd
    try:
        # Header e mereu pe linia 10 (index 9), restul sunt date
        df = pd.read_excel(uploaded_file, sheet_name=0, header=9)
        df.columns = df.columns.str.strip()  # Curăță spațiile
        
        # Folosește exact denumirile din sheet
        col_sku = None
        col_stoc_final = None
        col_cost_unitar = None
        for col in df.columns:
            cl = col.strip().lower()
            if cl == "cod":
                col_sku = col
            elif "stoc final" in cl:
                col_stoc_final = col
            elif "cost unitar" in cl:
                col_cost_unitar = col
        
        if not (col_sku and col_stoc_final and col_cost_unitar):
            st.error("Nu am găsit coloanele esențiale: Cod, Stoc final, Cost unitar.")
            st.info(f"Coloane găsite: {list(df.columns)}")
            return None

        entries = []
        skipped = 0
        for _, row in df.iterrows():
            try:
                sku = str(row[col_sku]).strip()
                if not sku or sku == 'nan' or sku == '' or sku.lower() == 'none':
                    skipped += 1
                    continue
                stoc_final = safe_decimal(row.get(col_stoc_final, 0))
                cost_unitar = safe_decimal(row.get(col_cost_unitar, 0))
                if stoc_final > 0:
                    entries.append({
                        'sku': sku,
                        'cantitate': stoc_final,
                        'pret_unitar': cost_unitar,
                        'data_document': datetime.now().date()
                    })
            except Exception:
                skipped += 1
                continue
        if entries:
            st.success(f"✅ Procesate {len(entries)} rânduri (sărite: {skipped})")
        else:
            st.error("❌ Niciun rând valid găsit")
        return entries
    except Exception as e:
        st.error(f"❌ Eroare citire XLS: {e}")
        return None


        
        # Recitește fișierul cu acel header
        uploaded_file.seek(0)
        df = pd.read_excel(uploaded_file, sheet_name=0, header=header_row)
        df.columns = df.columns.str.strip()
        
        col_map = {}
        for col in df.columns:
            cl = col.lower()
            if cl == "cod":
                col_map['sku'] = col
            elif "cant" in cl:
                col_map['cantitate'] = col
            elif "valo" in cl:  # Poate aveți și "Valori"
                col_map['pret_unitar'] = col
        if 'sku' not in col_map:
            st.error("❌ Nu am găsit coloana 'Cod' pentru SKU")
            st.info(f"Coloane detectate: {list(df.columns)}")
            return None
        
        entries = []
        skipped = 0
        for idx, row in df.iterrows():
            try:
                sku = str(row[col_map['sku']]).strip()
                if not sku or sku == 'nan' or sku == '' or sku.lower() == 'none':
                    skipped += 1
                    continue
                cantitate = safe_decimal(row.get(col_map.get('cantitate', ''), 0))
                pret_unitar = safe_decimal(row.get(col_map.get('pret_unitar', ''), 0))
                data_doc = datetime.now().date()
                if cantitate > 0:
                    entries.append({
                        'sku': sku,
                        'cantitate': cantitate,
                        'pret_unitar': pret_unitar,
                        'data_document': data_doc
                    })
            except Exception as row_err:
                skipped += 1
                continue
        if entries:
            st.success(f"✅ Procesate {len(entries)} rânduri (sărite: {skipped})")
        else:
            st.error("❌ Niciun rând valid găsit")
        return entries
    except Exception as e:
        st.error(f"❌ Eroare citire XLS: {e}")
        return None

        
        # Găsim rândul cu antetul
        header_row = None
        for idx, row in df.iterrows():
            row_str = ' '.join(str(v).lower() for v in row.values)
            if 'cod' in row_str and ('produs' in row_str or 'sku' in row_str):
                header_row = idx
                break
        
        if header_row is None:
            st.error("❌ Nu am găsit antetul (căutam 'Cod produs' sau 'SKU')")
            return None
        
        # Re-citim cu header-ul corect
        uploaded_file.seek(0)
        try:
            df = pd.read_excel(uploaded_file, sheet_name=0, header=header_row)
        except:
            uploaded_file.seek(0)
            df = pd.read_excel(uploaded_file, sheet_name=0, header=header_row, engine='xlrd')
        
        df.columns = df.columns.str.strip()
        
        # Mapăm coloanele
        col_map = {}
        for col in df.columns:
            cl = col.lower()
            if 'cod' in cl and ('produs' in cl or 'sku' in cl):
                col_map['sku'] = col
            elif 'cantitate' in cl or 'cant' in cl or 'qty' in cl:
                col_map['cantitate'] = col
            elif 'pret' in cl and ('unitar' in cl or 'unit' in cl):
                col_map['pret_unitar'] = col
            elif 'data' in cl or 'date' in cl:
                col_map['data'] = col
        
        if 'sku' not in col_map:
            st.error("❌ Nu am găsit coloana COD PRODUS/SKU")
            st.info(f"Coloane disponibile: {list(df.columns)}")
            return None
        
        # Extragem datele
        entries = []
        skipped = 0
        
        for idx, row in df.iterrows():
            try:
                sku = str(row[col_map['sku']]).strip()
                if not sku or sku == 'nan' or sku == '' or sku.lower() == 'none':
                    skipped += 1
                    continue
                
                cantitate = safe_decimal(row.get(col_map.get('cantitate', ''), 0))
                pret_unitar = safe_decimal(row.get(col_map.get('pret_unitar', ''), 0))
                data_doc = row.get(col_map.get('data')) if 'data' in col_map else datetime.now().date()
                
                if cantitate > 0 and pret_unitar > 0:
                    entries.append({
                        'sku': sku,
                        'cantitate': cantitate,
                        'pret_unitar': pret_unitar,
                        'data_document': data_doc
                    })
            except Exception as row_err:
                skipped += 1
                continue
        
        if entries:
            st.success(f"✅ Procesate {len(entries)} rânduri (sărite: {skipped})")
        else:
            st.error("❌ Niciun rând valid (cantitate > 0 și preț > 0)")
        
        return entries
        
    except Exception as e:
        st.error(f"❌ Eroare parsare: {e}")
        return None

def sync_smartbill_data():
    if "smartbill_data" not in st.session_state:
        st.session_state.smartbill_data = None
    
    config = init_smartbill()
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📊 Preia stocuri SmartBill", type="primary", use_container_width=True):
            with st.spinner("Fetching..."):
                data = get_smartbill_stocks(config['email'], config['token'], config['cif'])
                if data:
                    st.session_state.smartbill_data = process_smartbill_data(data)
                    st.session_state.smartbill_selections = {}  # Reset selections
                    if st.session_state.smartbill_data:
                        total_stock = sum(p['stock'] for p in st.session_state.smartbill_data.values())
                        st.success(f"✅ {len(st.session_state.smartbill_data)} produse (stoc total: {total_stock:.0f})")
                    else:
                        st.error("❌ Nu s-au putut procesa datele")
                else:
                    st.error("❌ Eroare fetch API")
    
    with col2:
        uploaded_file = st.file_uploader("📄 Upload Excel prețuri intrare", type=['xls', 'xlsx'], key="xlsx_upload")
        if uploaded_file and st.button("💾 Procesează Excel", use_container_width=True):
            with st.spinner("Procesez..."):
                entries = parse_smartbill_xlsx(uploaded_file)
                if entries:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("SELECT sku, product_id FROM product_sku")
                        sku_map = {r[0]: r[1] for r in cursor.fetchall()}
                        
                        to_insert = [(sku_map[e['sku']], e['sku'], e['data_document'], e['cantitate'], e['pret_unitar']) 
                                     for e in entries if e['sku'] in sku_map]
                        
                        not_found = [e['sku'] for e in entries if e['sku'] not in sku_map]
                        
                        if to_insert:
                            cursor.execute("DELETE FROM smartbill_pret_intrare")
                            execute_batch(cursor, 
                                "INSERT INTO smartbill_pret_intrare (product_id, sku, data_document, cantitate, pret_unitar) VALUES (%s, %s, %s, %s, %s)", 
                                to_insert, page_size=500)
                            conn.commit()
                            st.success(f"✅ Salvate {len(to_insert)} prețuri intrare")
                        
                        if not_found:
                            st.warning(f"⚠️ {len(not_found)} SKU-uri negăsite în DB")
                            with st.expander("Vezi SKU-uri"):
                                st.write(not_found[:20])
                        
                        cursor.close()
                        conn.close()
                    except Exception as e:
                        st.error(f"❌ Eroare DB: {e}")

    if not st.session_state.smartbill_data:
        return
    
    sb_products = st.session_state.smartbill_data
    decisions = get_smartbill_decisions()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku, product_id FROM product_sku WHERE is_primary = true")
        sku_to_product = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Preluăm toate produsele pentru dropdown-ul de mapare
        cursor.execute("SELECT p.id, p.name, ps.sku FROM product p JOIN product_sku ps ON p.id = ps.product_id WHERE ps.is_primary = true ORDER BY p.name")
        all_products = cursor.fetchall()
        product_options = {f"{row[1]} ({row[2]})": row[0] for row in all_products}
        
        cursor.close()
        conn.close()
    except Exception as e:
        st.error(f"Eroare citire produse: {e}")
        return

    stock_data = []
    unmatched = []
    
    for sku, info in sb_products.items():
        dec_info = decisions.get(sku, {})
        dec = dec_info.get("decision")
        
        if sku in sku_to_product:
            stock_data.append((sku_to_product[sku], sku, Decimal(info['stock'])))
        elif dec == "ignora":
            continue
        elif dec == "creaza_nou" and dec_info.get("product_id"):
            stock_data.append((dec_info["product_id"], sku, Decimal(info['stock'])))
        elif dec == "mapeaza" and dec_info.get("product_id"):
            stock_data.append((dec_info["product_id"], sku, Decimal(info['stock'])))
        else:
            unmatched.append({'sku': sku, 'name': info['name'], 'stock': info['stock']})
    
    if unmatched:
        st.warning(f"⚠️ {len(unmatched)} SKU-uri necunoscute")
        page_size = 10
        total_pages = (len(unmatched) + page_size - 1) // page_size
        page = st.number_input("Pagina", 1, total_pages, 1) if total_pages > 1 else 1
        start_idx = (page - 1) * page_size
        
        with st.expander("📋 SKU-uri necunoscute", expanded=True):
            for item in unmatched[start_idx:start_idx+page_size]:
                c1, c2, c3, c4 = st.columns([2, 3, 1, 3])
                c1.write(f"**{item['sku']}**")
                c2.write(item['name'])
                c3.write(item['stock'])
                
                # Dropdown pentru acțiune (nu salvează direct)
                action = c4.selectbox(
                    "Acțiune", 
                    ["Alege...", "Creează nou", "Mapeaza", "Ignoră"], 
                    key=f"act_{item['sku']}", 
                    label_visibility="collapsed"
                )
                
                # Salvăm selecția în session_state
                if action != "Alege...":
                    if item['sku'] not in st.session_state.smartbill_selections:
                        st.session_state.smartbill_selections[item['sku']] = {}
                    st.session_state.smartbill_selections[item['sku']]['action'] = action
                    st.session_state.smartbill_selections[item['sku']]['name'] = item['name']
                
                # Dacă a ales "Mapeaza", afișăm dropdown pentru selectare produs
                if action == "Mapeaza":
                    selected_product = st.selectbox(
                        "Selectează produs existent",
                        [""] + list(product_options.keys()),
                        key=f"prod_{item['sku']}"
                    )
                    if selected_product:
                        st.session_state.smartbill_selections[item['sku']]['product_id'] = product_options[selected_product]
        
        # BUTON UNIC LA FINAL PENTRU SALVARE
        if st.button("💾 Salvează opțiunile selectate", type="primary", use_container_width=True):
            decisions_to_save = []
            
            for sku, selection in st.session_state.smartbill_selections.items():
                action = selection.get('action')
                if not action:
                    continue
                
                action_map = {"Creează nou": "creaza_nou", "Mapeaza": "mapeaza", "Ignoră": "ignora"}
                mapped_action = action_map.get(action)
                
                if mapped_action:
                    decision = {
                        'sku': sku,
                        'action': mapped_action,
                        'name': selection.get('name')
                    }
                    
                    # Dacă e mapare, adăugăm product_id
                    if mapped_action == "mapeaza":
                        product_id = selection.get('product_id')
                        if product_id:
                            decision['product_id'] = product_id
                        else:
                            st.warning(f"⚠️ SKU {sku}: Nu ați selectat un produs pentru mapare")
                            continue
                    
                    decisions_to_save.append(decision)
            
            if decisions_to_save:
                saved_count = save_smartbill_decisions_batch(decisions_to_save)
                if saved_count > 0:
                    st.success(f"✅ Salvate {saved_count} decizii")
                    st.session_state.smartbill_selections = {}  # Reset selections
                    time.sleep(1)
                    st.rerun()
            else:
                st.warning("⚠️ Nicio selecție validă de salvat")
    
    if stock_data:
        st.info(f"📦 {len(stock_data)} produse match")
        if st.button("💾 Salvează stocuri în DB", type="primary", use_container_width=True):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM smartbill_stoc")
                from collections import defaultdict
                product_stoc = defaultdict(Decimal)
                for pid, sku, stoc in stock_data:
                    product_stoc[pid] += Decimal(stoc)
                stock_data_agg = [(pid, suma) for pid, suma in product_stoc.items()]
                execute_batch(cursor, "INSERT INTO smartbill_stoc (product_id, sku, stock_quantity) VALUES (%s, %s, %s)", stock_data, page_size=500)
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Salvate {len(stock_data)} stocuri!")
            except Exception as e:
                st.error(f"Eroare: {e}")

# =========================
# UI PRINCIPAL
# =========================

st.markdown("## 🛒 WooCommerce Import")

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("🚀 Full Import", type="primary", use_container_width=True):
        st.session_state["import_session_id"] = str(uuid.uuid4())
        st.session_state["import_phase"] = 'extracting'
        st.session_state["import_stats"] = {}
        with st.spinner("Curăț..."):
            clear_staging_tables()
        st.rerun()
with col2:
    if st.button("🔄 Rulează Matching", use_container_width=True):
        ls = get_latest_session_id()
        if ls:
            st.session_state["import_session_id"] = ls
            st.session_state["import_phase"] = 'matching'
            st.session_state["import_stats"] = {}
            st.rerun()
        else:
            st.error("Nu există date!")
with col3:
    if st.button("⚡ Quick Refresh", use_container_width=True):
        quick_refresh_prices_and_stock()

if st.session_state["import_phase"]:
    st.info(f"Status: {st.session_state['import_phase']}")

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'extracting':
    with st.spinner("Extrag..."):
        stats = fetch_and_stage_products_bulk(st.session_state["import_session_id"])
        st.session_state["import_stats"]['extract'] = stats
        st.session_state["import_phase"] = 'matching'
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'matching':
    with st.spinner("Matching..."):
        match_stats = run_sku_matching_and_autocreate(st.session_state["import_session_id"])
        st.session_state["import_stats"]['matching'] = match_stats
        if match_stats:
            st.session_state["import_phase"] = 'reconciling'
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'reconciling':
    match_stats = st.session_state["import_stats"].get('matching', {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Match", match_stats.get('matched_primary', 0))
    c2.metric("Auto", match_stats.get('auto_created', 0))
    c3.metric("Alias", match_stats.get('matched_alias', 0))
    c4.metric("Dup", match_stats.get('duplicates', 0))
    
    pending = match_stats.get('pending_actions', 0)
    if pending == 0:
        if st.button("📦 Finalizează", type="primary"):
            st.session_state["import_phase"] = 'finalizing'
            st.rerun()
    else:
        st.warning(f"⚠️ {pending} aliasuri")
        items = get_pending_aliases(st.session_state["import_session_id"])
        for item in items:
            with st.expander(f"{item['sku']} -> {item['parent_name']}"):
                st.write(f"Produs: {item['existing_product_name']}")
                c1, c2 = st.columns(2)
                if c1.button("Confirmă", key=f"c_{item['match_id']}"):
                    mark_alias_action(item['match_id'], 'confirmed')
                    st.rerun()
                if c2.button("Skip", key=f"s_{item['match_id']}"):
                    mark_alias_action(item['match_id'], 'skipped')
                    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'finalizing':
    with st.spinner("Finalizare..."):
        finalize_import(st.session_state["import_session_id"])
        st.session_state["import_phase"] = 'done'
    st.rerun()

if st.session_state["import_phase"] == 'done':
    st.success("✅ Import Complet")
    if st.button("Reset"):
        st.session_state["import_session_id"] = None
        st.session_state["import_phase"] = None
        st.rerun()

st.divider()
st.markdown("## 📊 SmartBill Sync")
sync_smartbill_data()
