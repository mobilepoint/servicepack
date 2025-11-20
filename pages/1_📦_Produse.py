# pages/1_📦_Produse.py
import re
import uuid
import json
from datetime import datetime
from decimal import Decimal
import pandas as pd
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from woocommerce import API
import time

# =========================
#   CONFIG
# =========================
st.set_page_config(page_title="Produse WooCommerce", layout="wide")
st.title("📦 Import Produse WooCommerce")

# =========================
#   CONNECTIONS
# =========================
@st.cache_resource
def get_pg_connection_string():
    """PostgreSQL connection"""
    try:
        return st.secrets["connections"]["postgresql"]["url"]
    except KeyError:
        st.error("❌ Credențiale PostgreSQL lipsă")
        st.stop()

@st.cache_resource
def init_woocommerce():
    """WooCommerce API connection"""
    try:
        return API(
            url=st.secrets["connections"]["woocommerce"]["WOO_URL"],
            consumer_key=st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_KEY"],
            consumer_secret=st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_SECRET"],
            version="wc/v3",
            timeout=30
        )
    except KeyError:
        st.error("❌ Credențiale WooCommerce lipsă")
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
if "last_processed_page" not in st.session_state:
    st.session_state["last_processed_page"] = 0
if "quick_sync_running" not in st.session_state:
    st.session_state["quick_sync_running"] = False

# =========================
#   HELPER FUNCTIONS
# =========================
def get_db_connection():
    """Get PostgreSQL connection"""
    return psycopg2.connect(get_pg_connection_string())

def compose_variation_name(parent_name: str, attributes: list) -> str:
    """Compune numele variației din nume parent + atribute"""
    if not attributes:
        return parent_name
    
    attr_parts = []
    for attr in attributes:
        value = attr.get('option', '')
        if value:
            attr_parts.append(value)
    
    if attr_parts:
        return f"{parent_name} - {' - '.join(attr_parts)}"
    else:
        return parent_name

def clear_staging_tables(session_id: str = None):
    """Șterge datele din staging"""
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
        st.error(f"Eroare ștergere staging: {e}")
        return False

def clear_production_tables():
    """Șterge datele din tabele production"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM woo_variation_attributes")
        cursor.execute("DELETE FROM woo_stoc")
        cursor.execute("DELETE FROM woo_preturi")
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare ștergere date production: {e}")
        return False

def get_staging_progress(session_id: str):
    """Verifică progresul import în staging"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT woo_product_id) as products_staged,
                COUNT(*) as total_rows
            FROM woo_staging_raw
            WHERE import_session_id = %s
        """, (session_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        return {'products_staged': result[0] if result else 0, 'total_rows': result[1] if result else 0}
    except Exception as e:
        return {'products_staged': 0, 'total_rows': 0}

# =========================
#   QUICK SYNC FUNCTION
# =========================
def quick_sync_prices_and_stock():
    """Sincronizare rapidă DOAR pentru produse cunoscute"""
    st.session_state["quick_sync_running"] = True
    
    sync_container = st.container()
    
    with sync_container:
        st.markdown("### ⚡ Quick Sync - Stoc și Prețuri")
        
        # Step 1: Citește SKU-uri cunoscute
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            st.info("📋 Citesc SKU-urile din baza de date...")
            cursor.execute("""
                SELECT ps.sku, ps.product_id, p.name as product_name
                FROM product_sku ps
                JOIN product p ON p.id = ps.product_id
                ORDER BY ps.sku
            """)
            
            known_skus = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if not known_skus:
                st.warning("Nu am găsit SKU-uri în baza de date!")
                st.session_state["quick_sync_running"] = False
                return
            
            st.success(f"✅ Am găsit {len(known_skus)} SKU-uri")
            
            sku_to_product = {row['sku']: row['product_id'] for row in known_skus}
            
        except Exception as e:
            st.error(f"Eroare citire SKU-uri: {e}")
            st.session_state["quick_sync_running"] = False
            return
        
        # Step 2: Fetch din WooCommerce
        st.info("🔍 Fetch produse din WooCommerce...")
        
        woo_products = []
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        page = 1
        per_page = 100
        
        while True:
            status_text.info(f"📥 Fetch pagina {page} din WooCommerce...")
            
            try:
                response = wcapi.get("products", params={"per_page": per_page, "page": page})
                
                if response.status_code != 200:
                    break
                
                products = response.json()
                
                if not products:
                    break
                
                woo_products.extend(products)
                page += 1
                
                progress_bar.progress(min(page / 30, 1.0))
                time.sleep(0.2)
                
            except Exception as e:
                st.error(f"Eroare fetch: {e}")
                break
        
        progress_bar.empty()
        status_text.empty()
        
        st.success(f"✅ Am găsit {len(woo_products)} produse în WooCommerce")
        
        # Step 3: Procesează
        st.info("🔄 Procesez și actualizez...")
        
        prices_data = []
        stock_data = []
        attributes_data = []
        matched_count = 0
        not_matched = []
        
        progress_bar = st.progress(0)
        
        for idx, woo_product in enumerate(woo_products):
            progress_bar.progress((idx + 1) / len(woo_products))
            
            try:
                product_type = woo_product.get('type', 'simple')
                woo_id = woo_product.get('id')
                parent_name = woo_product.get('name', '')
                
                if product_type == 'simple':
                    sku = woo_product.get('sku', '').strip()
                    
                    if sku and sku in sku_to_product:
                        product_id = sku_to_product[sku]
                        matched_count += 1
                        
                        prices_data.append((
                            product_id, sku, woo_id, None,
                            Decimal(woo_product.get('regular_price') or 0),
                            Decimal(woo_product.get('sale_price') or 0) if woo_product.get('sale_price') else None
                        ))
                        
                        stock_data.append((
                            product_id, sku, woo_id, None,
                            woo_product.get('stock_quantity') or 0
                        ))
                    elif sku:
                        not_matched.append(sku)
                
                elif product_type == 'variable':
                    try:
                        var_response = wcapi.get(f"products/{woo_id}/variations", params={"per_page": 100})
                        if var_response.status_code == 200:
                            variations = var_response.json()
                            
                            for var in variations:
                                var_sku = var.get('sku', '').strip()
                                
                                if var_sku and var_sku in sku_to_product:
                                    var_product_id = sku_to_product[var_sku]
                                    matched_count += 1
                                    
                                    prices_data.append((
                                        var_product_id, var_sku, woo_id, var.get('id'),
                                        Decimal(var.get('regular_price') or 0),
                                        Decimal(var.get('sale_price') or 0) if var.get('sale_price') else None
                                    ))
                                    
                                    stock_data.append((
                                        var_product_id, var_sku, woo_id, var.get('id'),
                                        var.get('stock_quantity') or 0
                                    ))
                                    
                                    for attr in var.get('attributes', []):
                                        attributes_data.append((
                                            var_product_id, woo_id, var.get('id'),
                                            attr.get('name', ''), attr.get('option', '')
                                        ))
                                elif var_sku:
                                    not_matched.append(var_sku)
                    except:
                        pass
            except:
                pass
        
        progress_bar.empty()
        
        # Step 4: Bulk insert
        if prices_data or stock_data:
            st.info("💾 Salvez în baza de date...")
            
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                if prices_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_preturi 
                        (product_id, sku, woo_product_id, woo_variation_id, regular_price, sale_price)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE
                        SET product_id = EXCLUDED.product_id,
                            sku = EXCLUDED.sku,
                            regular_price = EXCLUDED.regular_price,
                            sale_price = EXCLUDED.sale_price,
                            last_sync = NOW()
                    """, prices_data)
                
                if stock_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_stoc 
                        (product_id, sku, woo_product_id, woo_variation_id, stock_quantity)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE
                        SET product_id = EXCLUDED.product_id,
                            sku = EXCLUDED.sku,
                            stock_quantity = EXCLUDED.stock_quantity,
                            last_sync = NOW()
                    """, stock_data)
                
                if attributes_data:
                    execute_batch(cursor, """
                        INSERT INTO woo_variation_attributes 
                        (product_id, woo_product_id, woo_variation_id, attribute_name, attribute_value)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (woo_product_id, woo_variation_id, attribute_name) DO UPDATE
                        SET product_id = EXCLUDED.product_id,
                            attribute_value = EXCLUDED.attribute_value
                    """, attributes_data)
                
                conn.commit()
                cursor.close()
                conn.close()
                
                st.success("✅ Quick Sync complet!")
                st.balloons()
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("💰 Prețuri", len(prices_data))
                with col2:
                    st.metric("📦 Stocuri", len(stock_data))
                with col3:
                    st.metric("✅ Match-uri", matched_count)
                with col4:
                    st.metric("⚠️ Nematchate", len(set(not_matched)))
                
            except Exception as e:
                st.error(f"Eroare salvare: {e}")
        else:
            st.warning("Nu am găsit date de sincronizat")
    
    st.session_state["quick_sync_running"] = False

# =========================
#   PHASE 1: EXTRACT
# =========================
def fetch_and_stage_products(session_id: str):
    """FAZA 1: Extract cu resume capability"""
    stats = {
        'total_products_fetched': 0,
        'total_variations_fetched': 0,
        'simple_products': 0,
        'variable_products': 0,
        'variations_inserted': 0,
        'errors': 0
    }
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        # Check progress existent
        existing_progress = get_staging_progress(session_id)
        start_page = st.session_state.get("last_processed_page", 0) + 1
        
        if existing_progress['products_staged'] > 0:
            st.info(f"📌 Reluare import: {existing_progress['products_staged']} produse deja în staging")
        
        status_text.info("📥 Citesc lista produselor din WooCommerce...")
        
        all_products = []
        page = start_page
        per_page = 100
        
        while True:
            status_text.info(f"📥 Fetch produse - pagina {page}...")
            
            try:
                response = wcapi.get("products", params={"per_page": per_page, "page": page})
                
                if response.status_code != 200:
                    st.error(f"Eroare API WooCommerce: {response.status_code}")
                    break
                
                products = response.json()
                
                if not products:
                    break
                
                all_products.extend(products)
                st.session_state["last_processed_page"] = page
                page += 1
                
                time.sleep(0.2)
                
            except Exception as e:
                st.error(f"Eroare fetch produse: {e}")
                stats['errors'] += 1
                break
        
        stats['total_products_fetched'] = len(all_products)
        status_text.success(f"✅ Am găsit {len(all_products)} produse pe paginile procesate")
        
        if not all_products:
            return stats
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        total_items = len(all_products)
        processed = 0
        
        for product in all_products:
            processed += 1
            progress_bar.progress(processed / total_items)
            
            try:
                product_type = product.get('type', '')
                product_id_woo = product.get('id')
                parent_name = product.get('name', 'Produs fără nume')
                
                if product_type == 'variable':
                    stats['variable_products'] += 1
                    status_text.info(f"🔄 Procesez variații: {parent_name}...")
                    
                    var_page = 1
                    while True:
                        try:
                            var_response = wcapi.get(
                                f"products/{product_id_woo}/variations",
                                params={"per_page": 100, "page": var_page}
                            )
                            
                            if var_response.status_code != 200:
                                break
                            
                            variations = var_response.json()
                            
                            if not variations:
                                break
                            
                            stats['total_variations_fetched'] += len(variations)
                            
                            for variation in variations:
                                var_sku = variation.get('sku', '').strip()
                                var_name = compose_variation_name(parent_name, variation.get('attributes', []))
                                
                                # INSERT cu ON CONFLICT pentru a preveni duplicate
                                cursor.execute("""
                                    INSERT INTO woo_staging_raw 
                                    (import_session_id, woo_product_id, woo_variation_id, product_type, 
                                     parent_name, sku, regular_price, sale_price, stock_quantity, attributes, raw_data)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                                    ON CONFLICT (import_session_id, woo_product_id, woo_variation_id) DO UPDATE
                                    SET parent_name = EXCLUDED.parent_name,
                                        sku = EXCLUDED.sku,
                                        regular_price = EXCLUDED.regular_price,
                                        sale_price = EXCLUDED.sale_price,
                                        stock_quantity = EXCLUDED.stock_quantity,
                                        attributes = EXCLUDED.attributes,
                                        raw_data = EXCLUDED.raw_data
                                """, (
                                    session_id, product_id_woo, variation.get('id'), 'variation',
                                    var_name, var_sku if var_sku else None,
                                    Decimal(variation.get('regular_price') or 0),
                                    Decimal(variation.get('sale_price') or 0) if variation.get('sale_price') else None,
                                    variation.get('stock_quantity') or 0,
                                    json.dumps(variation.get('attributes', [])),
                                    json.dumps(variation)
                                ))
                                stats['variations_inserted'] += 1
                            
                            var_page += 1
                            
                        except Exception as e:
                            st.warning(f"Eroare fetch variații {product_id_woo}: {e}")
                            stats['errors'] += 1
                            break
                
                elif product_type == 'simple':
                    stats['simple_products'] += 1
                    sku = product.get('sku', '').strip()
                    
                    cursor.execute("""
                        INSERT INTO woo_staging_raw 
                        (import_session_id, woo_product_id, woo_variation_id, product_type, 
                         parent_name, sku, regular_price, sale_price, stock_quantity, attributes, raw_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (import_session_id, woo_product_id, woo_variation_id) DO UPDATE
                        SET parent_name = EXCLUDED.parent_name,
                            sku = EXCLUDED.sku,
                            regular_price = EXCLUDED.regular_price,
                            sale_price = EXCLUDED.sale_price,
                            stock_quantity = EXCLUDED.stock_quantity,
                            raw_data = EXCLUDED.raw_data
                    """, (
                        session_id, product_id_woo, None, 'simple',
                        parent_name, sku if sku else None,
                        Decimal(product.get('regular_price') or 0),
                        Decimal(product.get('sale_price') or 0) if product.get('sale_price') else None,
                        product.get('stock_quantity') or 0,
                        json.dumps([]),
                        json.dumps(product)
                    ))
                
                # Commit la fiecare 20 produse (batch mai mic pentru safety)
                if processed % 20 == 0:
                    conn.commit()
                    status_text.info(f"💾 Salvat batch {processed}/{total_items}...")
            
            except Exception as e:
                st.warning(f"Eroare procesare produs {product.get('id')}: {e}")
                stats['errors'] += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        progress_bar.empty()
        status_text.success(f"✅ Extract complet: {stats['variations_inserted'] + stats['simple_products']} produse")
        
    except Exception as e:
        st.error(f"Eroare FAZA 1: {e}")
        stats['errors'] += 1
    
    return stats

# =========================
#   PHASE 2: TRANSFORM
# =========================
def run_sku_matching(session_id: str):
    """FAZA 2: Matching"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("🔍 Rulează matching SKU...")
        
        cursor.execute("SELECT * FROM match_skus_for_session(%s)", (session_id,))
        matches = cursor.fetchall()
        
        for match in matches:
            staging_raw_id, sku, product_id, match_type, requires_action = match
            
            cursor.execute("""
                INSERT INTO woo_staging_matched 
                (import_session_id, staging_raw_id, sku, product_id, match_type, requires_action)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (import_session_id, staging_raw_id) DO UPDATE
                SET product_id = EXCLUDED.product_id,
                    match_type = EXCLUDED.match_type,
                    requires_action = EXCLUDED.requires_action
            """, (session_id, staging_raw_id, sku, product_id, match_type, requires_action))
        
        conn.commit()
        
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
                'pending_actions': stats[8]
            }
        return {}
        
    except Exception as e:
        st.error(f"Eroare FAZA 2: {e}")
        return {}

# =========================
#   PHASE 3: RECONCILIATION
# =========================
def get_pending_items(session_id: str, match_type: str):
    """Obține itemele care necesită acțiune"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        cursor.execute("""
            SELECT 
                sm.id as match_id, sm.staging_raw_id, sm.sku, sm.product_id, sm.match_type,
                sr.parent_name, sr.woo_product_id, sr.woo_variation_id, sr.raw_data
            FROM woo_staging_matched sm
            JOIN woo_staging_raw sr ON sr.id = sm.staging_raw_id
            WHERE sm.import_session_id = %s
            AND sm.match_type = %s
            AND sm.requires_action = true
            AND sm.action_taken IS NULL
            ORDER BY sr.parent_name
            LIMIT 50
        """, (session_id, match_type))
        
        items = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return items
        
    except Exception as e:
        st.error(f"Eroare get pending: {e}")
        return []

def mark_action_taken(match_id: str, action: str):
    """Marchează acțiune"""
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
        st.error(f"Eroare mark action: {e}")
        return False

def create_new_product(name: str, sku: str):
    """Creează produs nou"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        product_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO product (id, name) VALUES (%s, %s)", (product_id, name))
        cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)", (sku, product_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return product_id
    except Exception as e:
        st.error(f"Eroare creare produs: {e}")
        return None

def save_mapping_decision(woo_sku: str, product_id: str, decision_type: str):
    """Salvează decizia"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO sku_mapping_decisions (woo_sku, product_id, decision_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (woo_sku) DO UPDATE 
            SET product_id = EXCLUDED.product_id, 
                decision_type = EXCLUDED.decision_type,
                decided_at = NOW()
        """, (woo_sku, product_id, decision_type))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare salvare decizie: {e}")
        return False

def update_match_product_id(match_id: str, product_id: str):
    """Update product_id"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE woo_staging_matched 
            SET product_id = %s
            WHERE id = %s
        """, (product_id, match_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare update match: {e}")
        return False

# =========================
#   PHASE 4: FINALIZE
# =========================
def finalize_import(session_id: str):
    """FAZA 4: Transfer"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("📦 Finalizare import...")
        
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
        st.error(f"Eroare FAZA 4: {e}")
        return {}

# =========================
#   MAIN UI
# =========================
st.markdown("### 🔄 Import din WooCommerce")
st.info("**2 moduri:** Full Import (ETL) sau Quick Sync (stoc + prețuri)")

if st.session_state["import_phase"]:
    phase_labels = {
        'extracting': '📥 FAZA 1: Extragere',
        'matching': '🔍 FAZA 2: Matching',
        'reconciling': '🤔 FAZA 3: Reconciliere',
        'finalizing': '📦 FAZA 4: Finalizare',
        'done': '✅ Import complet'
    }
    st.info(f"**Status:** {phase_labels.get(st.session_state['import_phase'], 'Necunoscut')}")

col1, col2 = st.columns(2)

with col1:
    if st.button("🚀 Start Import NOU", type="primary", use_container_width=True, disabled=st.session_state["quick_sync_running"]):
        st.session_state["import_session_id"] = str(uuid.uuid4())
        st.session_state["import_phase"] = 'extracting'
        st.session_state["import_stats"] = {}
        st.session_state["last_processed_page"] = 0
        
        # ȘTERGE STAGING COMPLET
        with st.spinner("🗑️ Curăț staging..."):
            clear_staging_tables()
        
        st.rerun()

with col2:
    if st.button("⚡ Quick Sync", use_container_width=True, disabled=st.session_state["quick_sync_running"]):
        quick_sync_prices_and_stock()

st.caption("**Full Import:** Prima dată | **Quick Sync:** Daily sync rapid")
st.divider()

# =========================
#   WORKFLOW
# =========================

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'extracting':
    st.markdown("### 📥 FAZA 1: Extragere")
    
    with st.spinner("Extrag date..."):
        stats = fetch_and_stage_products(st.session_state["import_session_id"])
        st.session_state["import_stats"]['extract'] = stats
        st.session_state["import_phase"] = 'matching'
    
    st.success(f"✅ Extract: {stats.get('variations_inserted', 0) + stats.get('simple_products', 0)} produse")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'matching':
    st.markdown("### 🔍 FAZA 2: Matching")
    
    with st.spinner("Matching SKU..."):
        match_stats = run_sku_matching(st.session_state["import_session_id"])
        st.session_state["import_stats"]['matching'] = match_stats
        st.session_state["import_phase"] = 'reconciling'
    
    st.success("✅ Matching complet")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'reconciling':
    st.markdown("### 🤔 FAZA 3: Reconciliere")
    
    match_stats = st.session_state["import_stats"].get('matching', {})
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("✅ Match automat", match_stats.get('matched_primary', 0) + match_stats.get('matched_remembered', 0))
    with col2:
        st.metric("🔗 Aliasuri", match_stats.get('matched_alias', 0))
    with col3:
        st.metric("❓ Necunoscute", match_stats.get('unknown', 0))
    with col4:
        st.metric("⚠️ Duplicate", match_stats.get('duplicates', 0))
    
    pending = match_stats.get('pending_actions', 0)
    
    if pending == 0:
        st.success("🎉 Nu sunt acțiuni pendinte!")
        
        if st.button("📦 Finalizează Import", type="primary"):
            st.session_state["import_phase"] = 'finalizing'
            st.rerun()
    else:
        st.warning(f"⚠️ {pending} acțiuni necesită atenția ta")
        
        alias_items = get_pending_items(st.session_state["import_session_id"], 'alias')
        
        if alias_items:
            st.markdown("#### 🔗 Confirmă aliasuri")
            
            for item in alias_items:
                with st.expander(f"SKU: {item['sku']} → {item['parent_name']}"):
                    st.info(f"Acest SKU e **ALIAS** pentru: `{item['product_id']}`")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button(f"✅ Confirmă", key=f"conf_{item['match_id']}"):
                            save_mapping_decision(item['sku'], item['product_id'], 'confirmed_alias')
                            mark_action_taken(item['match_id'], 'confirmed')
                            st.rerun()
                    with col2:
                        if st.button(f"❌ Skip", key=f"skip_{item['match_id']}"):
                            mark_action_taken(item['match_id'], 'skipped')
                            st.rerun()
        
        unknown_items = get_pending_items(st.session_state["import_session_id"], 'unknown')
        
        if unknown_items:
            st.markdown("#### ❓ SKU-uri necunoscute")
            
            for item in unknown_items:
                with st.expander(f"SKU: {item['sku']} - {item['parent_name']}"):
                    if st.button(f"✅ Creează NOU", key=f"create_{item['match_id']}"):
                        product_id = create_new_product(item['parent_name'], item['sku'])
                        
                        if product_id:
                            save_mapping_decision(item['sku'], product_id, 'new_product')
                            update_match_product_id(item['match_id'], product_id)
                            mark_action_taken(item['match_id'], 'created_new')
                            st.success(f"Creat: {product_id}")
                            st.rerun()
        
        duplicate_items = get_pending_items(st.session_state["import_session_id"], 'duplicate')
        
        if duplicate_items:
            st.markdown("#### ⚠️ SKU-uri duplicate")
            st.error("Rezolvă manual în 'Aliasuri SKU'")
            for item in duplicate_items:
                st.write(f"- {item['sku']} - {item['parent_name']}")

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'finalizing':
    st.markdown("### 📦 FAZA 4: Finalizare")
    
    with st.spinner("Transfer staging → production..."):
        final_stats = finalize_import(st.session_state["import_session_id"])
        st.session_state["import_stats"]['finalize'] = final_stats
        st.session_state["import_phase"] = 'done'
    
    st.success("✅ Import finalizat!")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'done':
    st.markdown("### ✅ Import complet!")
    st.balloons()
    
    final_stats = st.session_state["import_stats"].get('finalize', {})
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("💰 Prețuri", final_stats.get('prices_inserted', 0))
    with col2:
        st.metric("📦 Stocuri", final_stats.get('stock_inserted', 0))
    with col3:
        st.metric("🏷️ Atribute", final_stats.get('attributes_inserted', 0))
    
    if st.button("🧹 Curăță și reset"):
        clear_staging_tables(st.session_state["import_session_id"])
        st.session_state["import_session_id"] = None
        st.session_state["import_phase"] = None
        st.session_state["import_stats"] = {}
        st.session_state["last_processed_page"] = 0
        st.rerun()

st.divider()
st.caption("💡 **Full Import:** ETL complet | **Quick Sync:** Rapid pentru produse cunoscute")
st.caption("🔌 PostgreSQL direct + WooCommerce API")
