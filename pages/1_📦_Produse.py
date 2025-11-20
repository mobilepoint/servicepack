# pages/1_📦_Produse.py
import uuid
import json
from decimal import Decimal, InvalidOperation
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from woocommerce import API
import time

st.set_page_config(page_title="Produse WooCommerce", layout="wide")
st.title("📦 Import Produse WooCommerce")

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

wcapi = init_woocommerce()

# =========================
#   SESSION & HELPERS
# =========================
if "import_session_id" not in st.session_state:
    st.session_state["import_session_id"] = None
if "import_phase" not in st.session_state:
    st.session_state["import_phase"] = None
if "import_stats" not in st.session_state:
    st.session_state["import_stats"] = {}

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
#   QUICK REFRESH
# =========================
def quick_refresh_prices_and_stock():
    with st.container():
        st.markdown("### ⚡ Quick Refresh")
        
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
#   PHASE 1: EXTRACT
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
#   PHASE 2: TRANSFORM + AUTO-CREATE
# =========================
def run_sku_matching_and_autocreate(session_id: str):
    """FAZA 2: Matching + Auto-create"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("🔍 Matching SKU...")
        
        # DEBUG: Verifică câte SKU-uri avem în staging
        cursor.execute("SELECT COUNT(*) FROM woo_staging_raw WHERE import_session_id = %s AND sku IS NOT NULL", (session_id,))
        sku_count = cursor.fetchone()[0]
        st.write(f"🔎 DEBUG: {sku_count} SKU-uri în staging_raw")
        
        # Rulează matching
        cursor.execute("SELECT * FROM match_skus_for_session(%s)", (session_id,))
        matches = cursor.fetchall()
        
        st.write(f"🔎 DEBUG: Funcția SQL a returnat {len(matches)} match-uri")
        
        if not matches:
            st.warning("⚠️ Nu am găsit match-uri din funcția SQL!")
            
            # DEBUG: Vezi de ce nu returnează
            cursor.execute("""
                SELECT sr.sku, 
                       (SELECT COUNT(*) FROM product_sku ps WHERE ps.sku = sr.sku) as count_in_product_sku
                FROM woo_staging_raw sr
                WHERE sr.import_session_id = %s AND sr.sku IS NOT NULL
                LIMIT 10
            """, (session_id,))
            debug_skus = cursor.fetchall()
            st.write("🔎 DEBUG: Sample SKU-uri:", debug_skus)
            
            cursor.close()
            conn.close()
            return {}
        
        # Procesează match-urile
        match_data = []
        created_products = 0
        
        for match in matches:
            staging_raw_id, sku, product_id, match_type, requires_action = match
            
            st.write(f"🔎 DEBUG Match: SKU={sku}, type={match_type}, product_id={product_id}, requires_action={requires_action}")
            
            # UNKNOWN = Creează automat
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
                
                st.write(f"🆕 Creat produs: {product_name} (SKU: {sku})")
            
            match_data.append((
                session_id, staging_raw_id, sku, product_id, match_type, requires_action
            ))
        
        # Insert match-uri
        if match_data:
            st.write(f"💾 Inserez {len(match_data)} match-uri în woo_staging_matched...")
            
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
        
        # Statistici
        cursor.execute("SELECT * FROM v_import_status WHERE import_session_id = %s", (session_id,))
        stats = cursor.fetchone()
        
        cursor.close()
        conn.close()
        
        if stats:
            st.write(f"📊 Stats din v_import_status: {stats}")
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
#   PHASE 3: RECONCILIATION
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
#   PHASE 4: FINALIZE
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
#   MAIN UI
# =========================
st.markdown("### 🔄 Import din WooCommerce")

if st.session_state["import_phase"]:
    phase_labels = {
        'extracting': '📥 FAZA 1: Extragere',
        'matching': '🔍 FAZA 2: Matching',
        'reconciling': '🤔 FAZA 3: Confirmă aliasuri',
        'finalizing': '📦 FAZA 4: Finalizare',
        'done': '✅ Complet'
    }
    st.info(f"**Status:** {phase_labels.get(st.session_state['import_phase'])}")

# BUTOANE
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
    # BUTON NOU - Rulează doar matching pe datele existente
    if st.button("🔄 Rulează Matching", use_container_width=True):
        latest_session = get_latest_session_id()
        if latest_session:
            st.session_state["import_session_id"] = latest_session
            st.session_state["import_phase"] = 'matching'
            st.session_state["import_stats"] = {}
            st.rerun()
        else:
            st.error("Nu există date în staging! Rulează Full Import mai întâi.")

with col3:
    if st.button("⚡ Quick Refresh", use_container_width=True):
        quick_refresh_prices_and_stock()

st.caption("**Full Import:** Tot procesul | **Matching:** Doar matching pe datele existente | **Quick Refresh:** Stoc + prețuri")

st.divider()

# =========================
#   WORKFLOW
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
    st.markdown("### 🔍 FAZA 2: Matching + Auto-create")
    
    with st.spinner("Matching..."):
        match_stats = run_sku_matching_and_autocreate(st.session_state["import_session_id"])
        st.session_state["import_stats"]['matching'] = match_stats
        
        if match_stats:
            st.session_state["import_phase"] = 'reconciling'
        else:
            st.error("❌ Matching a eșuat! Vezi debug info mai sus.")
    
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

st.divider()
st.caption("💡 Primary=automat | Secondary=confirmă | Necunoscut=creează automat")
# =========================
#   SMARTBILL INTEGRATION - FIX COMPLET
# =========================
def get_smartbill_stocks(email, token, cif):
    """Preia stocuri din SmartBill API"""
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
            st.error(f"SmartBill API error: {r.status_code}")
            st.code(r.text)
            return None
    except Exception as e:
        st.error(f"Error fetching SmartBill: {e}")
        return None

def process_smartbill_data(data):
    """Procesează răspunsul SmartBill în dict cu SKU-uri"""
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
    """Citește toate deciziile salvate din DB"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT smartbill_sku, decision_type, product_id FROM smartbill_sku_mapping_decisions")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        memory = {}
        for row in rows:
            memory[row[0]] = {
                "decision": row[1],
                "product_id": row[2]
            }
        return memory
    except Exception as e:
        st.error(f"Eroare citire decizii SmartBill: {e}")
        import traceback
        st.code(traceback.format_exc())
        return {}

def save_smartbill_decision(sku, action, name=None):
    """
    Salvează decizia pentru un SKU SmartBill
    action: 'creaza_nou', 'ignora', 'asteapta'
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.write(f"🔍 DEBUG: Salvez decizie pentru SKU={sku}, action={action}, name={name}")
        
        if action == "creaza_nou":
            # Creează produs nou
            new_product_id = str(uuid.uuid4())
            st.write(f"🔍 DEBUG: Creez produs nou cu ID={new_product_id}")
            
            cursor.execute("INSERT INTO product (id, name) VALUES (%s, %s)", 
                         (new_product_id, name or sku))
            cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)", 
                         (sku, new_product_id))
            
            # Salvează decizia
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions 
                (smartbill_sku, product_id, decision_type, decided_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (smartbill_sku) 
                DO UPDATE SET 
                    product_id = EXCLUDED.product_id,
                    decision_type = EXCLUDED.decision_type,
                    decided_at = NOW()
            """, (sku, new_product_id, action))
            
        else:
            # Pentru 'ignora' și 'asteapta' - doar salvează decizia fără product_id
            st.write(f"🔍 DEBUG: Salvez decizie {action} pentru {sku}")
            
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions 
                (smartbill_sku, product_id, decision_type, decided_at)
                VALUES (%s, NULL, %s, NOW())
                ON CONFLICT (smartbill_sku) 
                DO UPDATE SET 
                    decision_type = EXCLUDED.decision_type,
                    decided_at = NOW()
            """, (sku, action))
        
        conn.commit()
        st.write(f"✅ DEBUG: Commit reușit pentru {sku}")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        if conn:
            conn.rollback()
        st.error(f"❌ Eroare salvare decizie pentru {sku}: {e}")
        import traceback
        st.code(traceback.format_exc())
        return False
    finally:
        if conn:
            conn.close()

def sync_smartbill_data():
    """Sincronizează SmartBill cu baza de date"""
    config = init_smartbill()
    
    st.markdown("### 📊 Sync SmartBill")
    st.info("📥 Fetch stoc din SmartBill...")
    
    data = get_smartbill_stocks(config['email'], config['token'], config['cif'])
    if not data:
        st.error("❌ Nu am primit date de la SmartBill")
        return
    
    sb_products = process_smartbill_data(data)
    st.success(f"✅ {len(sb_products)} produse citite din SmartBill")
    
    # Citește decizii memorate
    decisions = get_smartbill_decisions()
    st.write(f"🔍 DEBUG: Găsite {len(decisions)} decizii memorate")
    
    # Citește SKU-uri cunoscute din product_sku
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku, product_id FROM product_sku")
        sku_to_product = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        st.write(f"🔍 DEBUG: Găsite {len(sku_to_product)} SKU-uri în product_sku")
    except Exception as e:
        st.error(f"Eroare citire SKU-uri: {e}")
        sku_to_product = {}
    
    # Procesare: împarte în matched vs unmatched
    stock_data = []
    unmatched = []
    matched_count = 0
    
    for sku, info in sb_products.items():
        dec_info = decisions.get(sku, {})
        dec = dec_info.get("decision")
        
        # SKU cunoscut în product_sku
        if sku in sku_to_product:
            product_id = sku_to_product[sku]
            matched_count += 1
            stock_data.append((product_id, sku, Decimal(info['stock'])))
        
        # Decizie "ignora" - skip complet
        elif dec == "ignora":
            continue
        
        # Decizie "creaza_nou" și are product_id
        elif dec == "creaza_nou" and dec_info.get("product_id"):
            product_id = dec_info["product_id"]
            matched_count += 1
            stock_data.append((product_id, sku, Decimal(info['stock'])))
        
        # Decizie "asteapta" - arată în listă dar nu face nimic
        elif dec == "asteapta":
            continue
        
        # Necunoscut - adaugă la lista pentru decizie
        else:
            unmatched.append({
                'sku': sku,
                'name': info['name'],
                'stock': info['stock']
            })
    
    st.write(f"🔍 DEBUG: Matched={matched_count}, Unmatched={len(unmatched)}")
    
    # UI pentru SKU-uri necunoscute
    if unmatched:
        st.warning(f"⚠️ {len(unmatched)} SKU-uri necunoscute necesită atenția ta")
        
        with st.expander("📋 SKU-uri necunoscute", expanded=True):
            for idx, item in enumerate(unmatched[:20]):
                sku = item['sku']
                
                cols = st.columns([2, 3, 2, 2, 1])
                cols[0].write(f"**{sku}**")
                cols[1].write(item['name'])
                cols[2].write(f"Stoc: {item['stock']}")
                
                # Selectbox pentru acțiune
                action_map = {
                    "Așteaptă": "asteapta",
                    "Creează nou": "creaza_nou",
                    "Ignoră": "ignora"
                }
                
                selected = cols[3].selectbox(
                    "Acțiune",
                    list(action_map.keys()),
                    key=f"act_{sku}_{idx}"
                )
                
                # Buton salvare
                if cols[4].button("💾", key=f"save_{sku}_{idx}"):
                    action_code = action_map[selected]
                    
                    with st.spinner(f"Salvez decizie pentru {sku}..."):
                        success = save_smartbill_decision(
                            sku=sku,
                            action=action_code,
                            name=item['name']
                        )
                    
                    if success:
                        st.success(f"✅ Decizie '{selected}' salvată pentru {sku}")
                        time.sleep(1)  # Așteaptă puțin ca să vadă mesajul
                        st.rerun()
                    else:
                        st.error(f"❌ Nu s-a putut salva decizia pentru {sku}")
        
        st.info("💡 După ce salvezi o decizie, lista se va actualiza. Apasă Sync din nou când toate sunt rezolvate.")
        return
    
    # Salvare stoc pentru produsele cu match
    if stock_data:
        st.info("💾 Salvez stocuri...")
        
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
            
            st.success(f"✅ Salvate {len(stock_data)} produse în SmartBill stock")
            st.balloons()
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("📦 Produse sincronizate", len(stock_data))
            with col2:
                st.metric("✅ Match-uri", matched_count)
                
        except Exception as e:
            st.error(f"❌ Eroare salvare stocuri: {e}")
            import traceback
            st.code(traceback.format_exc())
    else:
        st.info("ℹ️ Nu există produse cu match pentru salvat")

# =========================
#   UI SMARTBILL
# =========================
st.markdown("## 📊 Import din SmartBill")

if st.button("📊 Sync SmartBill", use_container_width=True, type="primary"):
    sync_smartbill_data()

st.caption("**Sync SmartBill:** Stoc + decizii persistente pentru SKU-uri necunoscute")
st.divider()

