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
import time

st.set_page_config(page_title="Produse WooCommerce & SmartBill", layout="wide")
st.title("📦 Import Produse")

# =========================
#   CONNECTIONS
# =========================
@st.cache_resource
def get_pg_connection_string():
    try:
        return st.secrets["connections"]["postgresql"]["url"]
    except KeyError:
        st.error("❌ Credențiale PostgreSQL lipsă")
        st.stop()

@st.cache_resource
def init_woocommerce():
    try:
        return API(
            url=st.secrets["connections"]["woocommerce"]["WOO_URL"],
            consumer_key=st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_KEY"],
            consumer_secret=st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_SECRET"],
            version="wc/v3",
            timeout=60
        )
    except KeyError:
        st.error("❌ Credențiale WooCommerce lipsă")
        st.stop()

@st.cache_resource
def init_smartbill():
    """SmartBill API config"""
    try:
        return {
            'email': st.secrets["connections"]["smartbill"]["SMARTBILL_EMAIL"],
            'token': st.secrets["connections"]["smartbill"]["SMARTBILL_TOKEN"],
            'cif': st.secrets["connections"]["smartbill"]["SMARTBILL_CIF"],
            'base_url': 'https://ws.smartbill.ro/SBORO/api'
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
    """Obține ultima sesiune de import din staging"""
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
#   WOOCOMMERCE - QUICK REFRESH
# =========================
def quick_refresh_prices_and_stock():
    with st.container():
        st.markdown("### ⚡ Quick Refresh WooCommerce")
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            st.info("📋 Citesc SKU-urile...")
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
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("💰 Prețuri", len(prices_data))
                with col2:
                    st.metric("📦 Stocuri", len(stock_data))
                with col3:
                    st.metric("✅ Match-uri", matched_count)
                
            except Exception as e:
                st.error(f"Eroare: {e}")
        else:
            st.warning("Nu am găsit date")

# =========================
#   WOOCOMMERCE - PHASE 1: EXTRACT
# =========================
def fetch_and_stage_products_bulk(session_id: str):
    stats = {
        'total_products_fetched': 0,
        'simple_products': 0,
        'variations_inserted': 0,
        'errors': 0,
        'duration': 0
    }
    
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
                """, (
                    session_id, parent_id_for_conflict, woo_variation_id, product_type,
                    full_name, sku if sku else None,
                    regular_price, sale_price, stock_qty,
                    json.dumps(product.get('attributes', [])),
                    json.dumps(product)
                ))
                
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

# =========================
#   WOOCOMMERCE - PHASE 2: TRANSFORM
# =========================
def run_sku_matching_and_autocreate(session_id: str):
    """FAZA 2: Matching + Auto-create"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("🔍 Matching SKU...")
        
        cursor.execute("SELECT COUNT(*) FROM woo_staging_raw WHERE import_session_id = %s AND sku IS NOT NULL", (session_id,))
        sku_count = cursor.fetchone()[0]
        st.write(f"🔎 DEBUG: {sku_count} SKU-uri în staging_raw")
        
        cursor.execute("SELECT * FROM match_skus_for_session(%s)", (session_id,))
        matches = cursor.fetchall()
        
        st.write(f"🔎 DEBUG: Funcția SQL a returnat {len(matches)} match-uri")
        
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
            
            match_data.append((
                session_id, staging_raw_id, sku, product_id, match_type, requires_action
            ))
        
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
                'total': stats[1],
                'matched_primary': stats[2],
                'matched_alias': stats[3],
                'matched_remembered': stats[4],
                'unknown': stats[5],
                'duplicates': stats[6],
                'errors': stats[7],
                'pending_actions': stats[8],
                'auto_created': created_products
            }
        return {}
        
    except Exception as e:
        st.error(f"❌ Eroare FAZA 2: {e}")
        import traceback
        st.code(traceback.format_exc())
        return {}

# =========================
#   WOOCOMMERCE - PHASE 3: RECONCILIATION
# =========================
def get_pending_aliases(session_id: str, limit: int = 50):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT 
                sm.id as match_id, sm.sku, sm.product_id,
                sr.parent_name, p.name as existing_product_name
            FROM woo_staging_matched sm
            JOIN woo_staging_raw sr ON sr.id = sm.staging_raw_id
            LEFT JOIN product p ON p.id = sm.product_id
            WHERE sm.import_session_id = %s
            AND sm.match_type = 'alias'
            AND sm.requires_action = true
            AND sm.action_taken IS NULL
            ORDER BY sr.parent_name
            LIMIT %s
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
        
        cursor.execute("""
            UPDATE woo_staging_matched 
            SET action_taken = %s, requires_action = false
            WHERE id = %s
        """, (action, match_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ Eroare: {e}")
        return False

# =========================
#   WOOCOMMERCE - PHASE 4: FINALIZE
# =========================
def finalize_import(session_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("📦 Finalizare...")
        
        cursor.execute("DELETE FROM woo_variation_attributes")
        cursor.execute("DELETE FROM woo_stoc")
        cursor.execute("DELETE FROM woo_preturi")
        
        cursor.execute("SELECT * FROM finalize_import(%s)", (session_id,))
        result = cursor.fetchone()
        
        conn.commit()
        cursor.close()
        conn.close()
        
        if result:
            return {
                'prices_inserted': result[0],
                'stock_inserted': result[1],
                'attributes_inserted': result[2]
            }
        return {}
    except Exception as e:
        st.error(f"❌ Eroare FAZA 4: {e}")
        return {}

# =========================
#   SMARTBILL API
# =========================
def call_smartbill_api(endpoint, method="GET", data=None):
    """Call SmartBill API"""
    config = init_smartbill()
    url = f"{config['base_url']}{endpoint}"
    
    auth = (config['email'], config['token'])
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    
    try:
        if method == "GET":
            response = requests.get(url, auth=auth, headers=headers, timeout=30)
        elif method == "POST":
            response = requests.post(url, auth=auth, headers=headers, json=data, timeout=30)
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Eroare SmartBill API: {e}")
        return None

def get_smartbill_products_and_stock():
    """Obține stoc din SmartBill"""
    config = init_smartbill()
    warehouse = "Eroilor 19 cv"
    
    stocks = call_smartbill_api(
        f"/stocks?cif={config['cif']}&warehouse={warehouse}",
        method="GET"
    )
    
    return stocks

def get_smartbill_stock_entries():
    """Obține intrări stoc (NIR-uri)"""
    config = init_smartbill()
    
    entries = call_smartbill_api(
        f"/inventory/entries?cif={config['cif']}&type=reception",
        method="GET"
    )
    
    return entries

def sync_smartbill_data():
    """Sincronizează SmartBill → PostgreSQL"""
    sync_container = st.container()
    
    with sync_container:
        st.markdown("### 📊 Sync SmartBill")
        
        st.info("📥 Fetch stoc din SmartBill...")
        stocks_data = get_smartbill_products_and_stock()
        
        if not stocks_data:
            st.error("❌ Nu am primit date de stoc!")
            return
        
        st.success(f"✅ Am primit {len(stocks_data.get('list', []))} produse cu stoc")
        
        st.info("📥 Fetch intrări din SmartBill...")
        entries_data = get_smartbill_stock_entries()
        
        if not entries_data:
            st.warning("⚠️ Nu am primit intrări de stoc")
            entries_data = {'list': []}
        
        st.success(f"✅ Am primit {len(entries_data.get('list', []))} intrări")
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT sku, product_id FROM product_sku")
            known_skus = cursor.fetchall()
            sku_to_product = {row['sku']: row['product_id'] for row in known_skus}
            
            cursor.execute("SELECT smartbill_sku, product_id, decision_type FROM smartbill_sku_mapping_decisions")
            remembered_decisions = cursor.fetchall()
            decisions_map = {row['smartbill_sku']: row for row in remembered_decisions}
            
            cursor.close()
            conn.close()
            
            st.info(f"📋 Am găsit {len(known_skus)} SKU-uri în baza de date")
            
        except Exception as e:
            st.error(f"Eroare citire SKU-uri: {e}")
            return
        
        st.info("🔄 Procesez stoc...")
        
        stock_data = []
        entries_prices = []
        unmatched_skus = []
        matched_count = 0
        
        progress_bar = st.progress(0)
        
        for idx, item in enumerate(stocks_data.get('list', [])):
            progress_bar.progress((idx + 1) / len(stocks_data.get('list', [])))
            
            sku = item.get('code', '').strip()
            if not sku:
                continue
            
            if sku in sku_to_product:
                product_id = sku_to_product[sku]
                matched_count += 1
            elif sku in decisions_map:
                decision = decisions_map[sku]
                if decision['decision_type'] == 'ignore':
                    continue
                product_id = decision['product_id']
                matched_count += 1
            else:
                unmatched_skus.append({
                    'sku': sku,
                    'name': item.get('name', 'Produs fără nume'),
                    'quantity': item.get('quantity', 0)
                })
                continue
            
            stock_data.append((
                product_id,
                sku,
                'Eroilor 19 cv',
                safe_decimal(item.get('quantity', 0), 0)
            ))
        
        progress_bar.empty()
        
        st.info("🔄 Procesez intrări...")
        
        for entry in entries_data.get('list', []):
            for item in entry.get('products', []):
                sku = item.get('code', '').strip()
                if not sku:
                    continue
                
                if sku in sku_to_product:
                    product_id = sku_to_product[sku]
                elif sku in decisions_map and decisions_map[sku]['decision_type'] != 'ignore':
                    product_id = decisions_map[sku]['product_id']
                else:
                    continue
                
                entries_prices.append((
                    product_id,
                    sku,
                    entry.get('date'),
                    safe_decimal(item.get('quantity', 0), 0),
                    safe_decimal(item.get('price', 0), 0),
                    entry.get('number'),
                    entry.get('supplier', {}).get('name', ''),
                    'Eroilor 19 cv'
                ))
        
        if unmatched_skus:
            st.warning(f"⚠️ {len(unmatched_skus)} SKU-uri necunoscute necesită atenția ta")
            
            with st.expander(f"📋 SKU-uri necunoscute ({len(unmatched_skus)})"):
                for item in unmatched_skus[:10]:
                    col1, col2, col3, col4 = st.columns([2, 3, 2, 3])
                    
                    with col1:
                        st.write(f"**{item['sku']}**")
                    with col2:
                        st.write(item['name'])
                    with col3:
                        st.write(f"Stoc: {item['quantity']}")
                    with col4:
                        action = st.selectbox(
                            "Acțiune",
                            ["Așteaptă", "Creează nou", "Ignoră"],
                            key=f"sb_{item['sku']}"
                        )
                        
                        if st.button("Salvează", key=f"save_sb_{item['sku']}"):
                            if action == "Creează nou":
                                try:
                                    conn = get_db_connection()
                                    cursor = conn.cursor()
                                    
                                    new_product_id = str(uuid.uuid4())
                                    cursor.execute("INSERT INTO product (id, name) VALUES (%s, %s)", (new_product_id, item['name']))
                                    cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)", (item['sku'], new_product_id))
                                    cursor.execute("""
                                        INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type)
                                        VALUES (%s, %s, 'create_new')
                                    """, (item['sku'], new_product_id))
                                    
                                    conn.commit()
                                    cursor.close()
                                    conn.close()
                                    
                                    st.success(f"✅ Creat produs: {item['name']}")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Eroare: {e}")
                            
                            elif action == "Ignoră":
                                try:
                                    conn = get_db_connection()
                                    cursor = conn.cursor()
                                    
                                    cursor.execute("""
                                        INSERT INTO smartbill_sku_mapping_decisions (smartbill_sku, product_id, decision_type)
                                        VALUES (%s, NULL, 'ignore')
                                    """, (item['sku'],))
                                    
                                    conn.commit()
                                    cursor.close()
                                    conn.close()
                                    
                                    st.success(f"✅ SKU ignorat: {item['sku']}")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Eroare: {e}")
            
            st.info("💡 Rezolvă SKU-urile necunoscute și apasă din nou pe Sync")
            return
        
        if stock_data or entries_prices:
            st.info("💾 Salvez în PostgreSQL...")
            
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                cursor.execute("DELETE FROM smartbill_stoc")
                cursor.execute("DELETE FROM smartbill_pret_intrare")
                
                if stock_data:
                    execute_batch(cursor, """
                        INSERT INTO smartbill_stoc (product_id, sku, gestiune, stock_quantity)
                        VALUES (%s, %s, %s, %s)
                    """, stock_data, page_size=500)
                
                if entries_prices:
                    execute_batch(cursor, """
                        INSERT INTO smartbill_pret_intrare 
                        (product_id, sku, data_intrare, cantitate, pret_unitar, nr_document, furnizor, gestiune)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (sku, nr_document, data_intrare) DO UPDATE
                        SET product_id = EXCLUDED.product_id,
                            cantitate = EXCLUDED.cantitate,
                            pret_unitar = EXCLUDED.pret_unitar,
                            furnizor = EXCLUDED.furnizor
                    """, entries_prices, page_size=500)
                
                conn.commit()
                cursor.close()
                conn.close()
                
                st.success("✅ Sync SmartBill complet!")
                st.balloons()
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("📦 Stocuri", len(stock_data))
                with col2:
                    st.metric("💰 Intrări", len(entries_prices))
                with col3:
                    st.metric("✅ Match-uri", matched_count)
                
            except Exception as e:
                st.error(f"Eroare salvare: {e}")
                import traceback
                st.code(traceback.format_exc())
        else:
            st.warning("Nu am date de salvat")

# =========================
#   MAIN UI
# =========================
st.markdown("## 🛒 Import din WooCommerce")

if st.session_state["import_phase"]:
    phase_labels = {
        'extracting': '📥 FAZA 1: Extragere',
        'matching': '🔍 FAZA 2: Matching',
        'reconciling': '🤔 FAZA 3: Confirmă aliasuri',
        'finalizing': '📦 FAZA 4: Finalizare',
        'done': '✅ Complet'
    }
    st.info(f"**Status:** {phase_labels.get(st.session_state['import_phase'])}")

col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🚀 Full Import", type="primary", use_container_width=True):
        st.session_state["import_session_id"] = str(uuid.uuid4())
        st.session_state["import_phase"] = 'extracting'
        st.session_state["import_stats"] = {}
        with st.spinner("🗑️ Curăț..."):
            clear_staging_tables()
        st.rerun()

with col2:
    if st.button("🔄 Rulează Matching", use_container_width=True):
        latest_session = get_latest_session_id()
        if latest_session:
            st.session_state["import_session_id"] = latest_session
            st.session_state["import_phase"] = 'matching'
            st.session_state["import_stats"] = {}
            st.rerun()
        else:
            st.error("Nu există date în staging!")

with col3:
    if st.button("⚡ Quick Refresh", use_container_width=True):
        quick_refresh_prices_and_stock()

st.caption("**Full Import:** Tot | **Matching:** Doar matching | **Quick Refresh:** Stoc + prețuri")

st.divider()

# =========================
#   WOOCOMMERCE WORKFLOW
# =========================

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'extracting':
    st.markdown("### 📥 FAZA 1")
    
    with st.spinner("Extrag..."):
        stats = fetch_and_stage_products_bulk(st.session_state["import_session_id"])
        st.session_state["import_stats"]['extract'] = stats
        st.session_state["import_phase"] = 'matching'
    
    st.success(f"✅ {stats.get('variations_inserted', 0) + stats.get('simple_products', 0)} produse")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'matching':
    st.markdown("### 🔍 FAZA 2")
    
    with st.spinner("Matching..."):
        match_stats = run_sku_matching_and_autocreate(st.session_state["import_session_id"])
        st.session_state["import_stats"]['matching'] = match_stats
        
        if match_stats:
            st.session_state["import_phase"] = 'reconciling'
        else:
            st.error("❌ Matching a eșuat!")
    
    if st.session_state["import_phase"] == 'reconciling':
        st.success(f"✅ Matching OK")
        st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'reconciling':
    st.markdown("### 🤔 FAZA 3")
    
    match_stats = st.session_state["import_stats"].get('matching', {})
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("✅ Match automat", match_stats.get('matched_primary', 0))
    with col2:
        st.metric("🆕 Creeat automat", match_stats.get('auto_created', 0))
    with col3:
        st.metric("🔗 Aliasuri", match_stats.get('matched_alias', 0))
    with col4:
        st.metric("⚠️ Duplicate", match_stats.get('duplicates', 0))
    
    pending = match_stats.get('pending_actions', 0)
    
    if pending == 0:
        st.success("🎉 Nu sunt acțiuni pendinte!")
        
        if st.button("📦 Finalizează", type="primary"):
            st.session_state["import_phase"] = 'finalizing'
            st.rerun()
    else:
        st.warning(f"⚠️ {pending} aliasuri necesită confirmare")
        
        alias_items = get_pending_aliases(st.session_state["import_session_id"])
        
        if alias_items:
            st.markdown("#### 🔗 Confirmă aliasuri")
            
            for item in alias_items:
                with st.expander(f"{item['sku']} → {item['parent_name']}"):
                    st.write(f"**Produs existent:** {item['existing_product_name']}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button(f"✅ Confirmă", key=f"c_{item['match_id']}"):
                            mark_alias_action(item['match_id'], 'confirmed')
                            st.rerun()
                    with col2:
                        if st.button(f"❌ Skip", key=f"s_{item['match_id']}"):
                            mark_alias_action(item['match_id'], 'skipped')
                            st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'finalizing':
    st.markdown("### 📦 FAZA 4")
    
    with st.spinner("Transfer..."):
        final_stats = finalize_import(st.session_state["import_session_id"])
        st.session_state["import_stats"]['finalize'] = final_stats
        st.session_state["import_phase"] = 'done'
    
    st.success("✅ OK!")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'done':
    st.markdown("### ✅ Complet!")
    st.balloons()
    
    extract_stats = st.session_state["import_stats"].get('extract', {})
    match_stats = st.session_state["import_stats"].get('matching', {})
    final_stats = st.session_state["import_stats"].get('finalize', {})
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("⏱️", f"{extract_stats.get('duration', 0):.1f}s")
    with col2:
        st.metric("💰 Prețuri", final_stats.get('prices_inserted', 0))
    with col3:
        st.metric("📦 Stocuri", final_stats.get('stock_inserted', 0))
    with col4:
        st.metric("🆕 Creeat", match_stats.get('auto_created', 0))
    
    if st.button("🧹 Reset", use_container_width=True):
        clear_staging_tables(st.session_state["import_session_id"])
        st.session_state["import_session_id"] = None
        st.session_state["import_phase"] = None
        st.session_state["import_stats"] = {}
        st.rerun()

# =========================
#   SMARTBILL SECTION
# =========================
st.divider()
st.markdown("## 📊 Import din SmartBill")

if st.button("📊 Sync SmartBill", use_container_width=True, type="primary"):
    sync_smartbill_data()

st.caption("**Sync SmartBill:** Stoc + Prețuri intrare (FIFO) din gestiunea 'Eroilor 19 cv'")

st.divider()
st.caption("💡 **Logică:** Primary=automat | Secondary=confirmă | Necunoscut=creează automat")
