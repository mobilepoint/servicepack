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
            timeout=60
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

# =========================
#   PHASE 1: EXTRACT (EXPORT BULK)
# =========================
def fetch_and_stage_products_bulk(session_id: str):
    """
    FAZA 1: Extract prin WooCommerce Export Bulk (1 singur request!)
    """
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
        status_text.info("🚀 Fetch BULK din WooCommerce Export...")
        start_time = time.time()
        
        # 1 SINGUR REQUEST la endpoint-ul custom!
        response = wcapi.get("products/export-full")
        
        if response.status_code != 200:
            st.error(f"❌ Eroare API WooCommerce: {response.status_code}")
            st.error(f"Response: {response.text}")
            return stats
        
        data = response.json()
        
        if not data.get('success'):
            st.error(f"❌ Export eșuat: {data.get('message', 'Eroare necunoscută')}")
            return stats
        
        woo_products = data.get('products', [])
        
        fetch_elapsed = time.time() - start_time
        stats['total_products_fetched'] = len(woo_products)
        
        status_text.success(f"✅ Am primit {len(woo_products)} produse în {fetch_elapsed:.2f} secunde!")
        
        if not woo_products:
            return stats
        
        # Scrie în staging
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
                
                # Compune numele pentru variații
                if product_type == 'variation':
                    parent_name = product.get('parent_name', '')
                    attributes = product.get('attributes', [])
                    full_name = compose_variation_name(parent_name, attributes)
                    stats['variations_inserted'] += 1
                else:
                    full_name = name
                    stats['simple_products'] += 1
                
                # Parent ID pentru CONFLICT
                parent_id_for_conflict = product.get('parent_id') if product_type == 'variation' else woo_product_id
                
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
                    session_id,
                    parent_id_for_conflict,
                    woo_variation_id,
                    product_type,
                    full_name,
                    sku if sku else None,
                    Decimal(product.get('regular_price') or 0),
                    Decimal(product.get('sale_price') or 0) if product.get('sale_price') else None,
                    product.get('stock_quantity') or 0,
                    json.dumps(product.get('attributes', [])),
                    json.dumps(product)
                ))
                
                # Commit la fiecare 100 produse
                if processed % 100 == 0:
                    conn.commit()
                    status_text.info(f"💾 Salvat {processed}/{total_items}...")
            
            except Exception as e:
                st.warning(f"⚠️ Eroare procesare produs {product.get('woo_product_id')}: {e}")
                stats['errors'] += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        stats['duration'] = time.time() - start_time
        
        progress_bar.empty()
        status_text.success(f"✅ Extract complet: {stats['variations_inserted'] + stats['simple_products']} produse în {stats['duration']:.2f}s")
        
    except Exception as e:
        st.error(f"❌ Eroare FAZA 1: {e}")
        import traceback
        st.code(traceback.format_exc())
        stats['errors'] += 1
    
    return stats

# =========================
#   PHASE 2: TRANSFORM (MATCHING)
# =========================
def run_sku_matching(session_id: str):
    """FAZA 2: Rulează matching-ul SKU în PostgreSQL"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("🔍 Rulează matching SKU în PostgreSQL...")
        
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
        st.error(f"❌ Eroare FAZA 2 - Matching: {e}")
        return {}

# =========================
#   PHASE 3: RECONCILIATION
# =========================
def get_pending_items(session_id: str, match_type: str, limit: int = 50):
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
            LIMIT %s
        """, (session_id, match_type, limit))
        
        items = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return items
        
    except Exception as e:
        st.error(f"❌ Eroare get pending items: {e}")
        return []

def mark_action_taken(match_id: str, action: str):
    """Marchează că s-a luat o acțiune"""
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
        st.error(f"❌ Eroare mark action: {e}")
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
        st.error(f"❌ Eroare creare produs: {e}")
        return None

def save_mapping_decision(woo_sku: str, product_id: str, decision_type: str):
    """Salvează decizia pentru viitor"""
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
        st.error(f"❌ Eroare salvare decizie: {e}")
        return False

def update_match_product_id(match_id: str, product_id: str):
    """Update product_id pentru un match"""
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
        st.error(f"❌ Eroare update match: {e}")
        return False

# =========================
#   PHASE 4: FINALIZE
# =========================
def finalize_import(session_id: str):
    """FAZA 4: Transfer din staging → production"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        st.info("📦 Finalizare import - transfer staging → production...")
        
        # Clear production tables
        cursor.execute("DELETE FROM woo_variation_attributes")
        cursor.execute("DELETE FROM woo_stoc")
        cursor.execute("DELETE FROM woo_preturi")
        
        # Rulează funcția de finalizare
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
        st.error(f"❌ Eroare FAZA 4 - Finalizare: {e}")
        return {}

# =========================
#   MAIN UI
# =========================
st.markdown("### 🔄 Import din WooCommerce")
st.info("**Import rapid:** Folosește WooCommerce Export Bulk (1 singur request, ~10-30 secunde)")

# Status current
if st.session_state["import_phase"]:
    phase_labels = {
        'extracting': '📥 FAZA 1: Extragere date (Export Bulk)',
        'matching': '🔍 FAZA 2: Matching SKU-uri',
        'reconciling': '🤔 FAZA 3: Reconciliere',
        'finalizing': '📦 FAZA 4: Finalizare',
        'done': '✅ Import complet'
    }
    st.info(f"**Status:** {phase_labels.get(st.session_state['import_phase'], 'Necunoscut')}")

# Start Import button
col1, col2 = st.columns([3, 1])

with col1:
    st.markdown("**Strategie:** REPLACE ALL + Export Bulk WooCommerce")

with col2:
    if st.button("🚀 Start Import", type="primary", use_container_width=True):
        st.session_state["import_session_id"] = str(uuid.uuid4())
        st.session_state["import_phase"] = 'extracting'
        st.session_state["import_stats"] = {}
        
        # Șterge staging
        with st.spinner("🗑️ Curăț staging..."):
            clear_staging_tables()
        
        st.rerun()

st.divider()

# =========================
#   IMPORT WORKFLOW
# =========================

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'extracting':
    st.markdown("### 📥 FAZA 1: Extragere (Export Bulk)")
    
    with st.spinner("🚀 Extrag date prin WooCommerce Export..."):
        stats = fetch_and_stage_products_bulk(st.session_state["import_session_id"])
        st.session_state["import_stats"]['extract'] = stats
        st.session_state["import_phase"] = 'matching'
    
    st.success(f"✅ Extract: {stats.get('variations_inserted', 0) + stats.get('simple_products', 0)} produse în {stats.get('duration', 0):.2f}s")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'matching':
    st.markdown("### 🔍 FAZA 2: Matching SKU-uri")
    
    with st.spinner("🔍 Matching SKU-urilor..."):
        match_stats = run_sku_matching(st.session_state["import_session_id"])
        st.session_state["import_stats"]['matching'] = match_stats
        st.session_state["import_phase"] = 'reconciling'
    
    st.success("✅ Matching complet")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'reconciling':
    st.markdown("### 🤔 FAZA 3: Reconciliere")
    
    match_stats = st.session_state["import_stats"].get('matching', {})
    
    # Display stats
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
        st.success("🎉 Nu sunt acțiuni pendinte! Poți finaliza import-ul.")
        
        if st.button("📦 Finalizează Import", type="primary"):
            st.session_state["import_phase"] = 'finalizing'
            st.rerun()
    
    else:
        st.warning(f"⚠️ {pending} acțiuni necesită atenția ta")
        
        # ALIASURI - confirmări
        alias_items = get_pending_items(st.session_state["import_session_id"], 'alias')
        
        if alias_items:
            st.markdown("#### 🔗 Confirmă aliasuri")
            st.info(f"Acestea SKU-uri există ca aliasuri. Confirmă asocierea pentru a continua.")
            
            for item in alias_items:
                with st.expander(f"SKU: {item['sku']} → {item['parent_name']}"):
                    st.info(f"Acest SKU există ca **ALIAS** pentru produsul cu ID: `{item['product_id']}`")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button(f"✅ Confirmă și memorează", key=f"conf_{item['match_id']}"):
                            save_mapping_decision(item['sku'], item['product_id'], 'confirmed_alias')
                            mark_action_taken(item['match_id'], 'confirmed')
                            st.success("Confirmat!")
                            st.rerun()
                    with col2:
                        if st.button(f"❌ Skip", key=f"skip_{item['match_id']}"):
                            mark_action_taken(item['match_id'], 'skipped')
                            st.rerun()
        
        # UNKNOWN - necunoscute
        unknown_items = get_pending_items(st.session_state["import_session_id"], 'unknown')
        
        if unknown_items:
            st.markdown("#### ❓ SKU-uri necunoscute")
            st.info(f"Acestea SKU-uri nu există în baza ta. Creează produse noi sau asociază manual.")
            
            for item in unknown_items:
                with st.expander(f"SKU: {item['sku']} - {item['parent_name']}"):
                    
                    if st.button(f"✅ Creează produs NOU", key=f"create_{item['match_id']}"):
                        product_id = create_new_product(item['parent_name'], item['sku'])
                        
                        if product_id:
                            save_mapping_decision(item['sku'], product_id, 'new_product')
                            update_match_product_id(item['match_id'], product_id)
                            mark_action_taken(item['match_id'], 'created_new')
                            st.success(f"Produs creat cu ID: {product_id}")
                            st.rerun()
        
        # DUPLICATES
        duplicate_items = get_pending_items(st.session_state["import_session_id"], 'duplicate')
        
        if duplicate_items:
            st.markdown("#### ⚠️ SKU-uri duplicate")
            st.error("Aceste SKU-uri apar în multiple produse! Trebuie rezolvate manual în pagina 'Aliasuri SKU'")
            
            for item in duplicate_items:
                st.write(f"- {item['sku']} - {item['parent_name']}")

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'finalizing':
    st.markdown("### 📦 FAZA 4: Finalizare")
    
    with st.spinner("📦 Transfer staging → production..."):
        final_stats = finalize_import(st.session_state["import_session_id"])
        st.session_state["import_stats"]['finalize'] = final_stats
        st.session_state["import_phase"] = 'done'
    
    st.success("✅ Import finalizat!")
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == 'done':
    st.markdown("### ✅ Import complet!")
    st.balloons()
    
    extract_stats = st.session_state["import_stats"].get('extract', {})
    final_stats = st.session_state["import_stats"].get('finalize', {})
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("⏱️ Timp total", f"{extract_stats.get('duration', 0):.1f}s")
    with col2:
        st.metric("💰 Prețuri", final_stats.get('prices_inserted', 0))
    with col3:
        st.metric("📦 Stocuri", final_stats.get('stock_inserted', 0))
    with col4:
        st.metric("🏷️ Atribute", final_stats.get('attributes_inserted', 0))
    
    if st.button("🧹 Curăță staging și reset", use_container_width=True):
        clear_staging_tables(st.session_state["import_session_id"])
        st.session_state["import_session_id"] = None
        st.session_state["import_phase"] = None
        st.session_state["import_stats"] = {}
        st.rerun()

# =========================
#   FOOTER
# =========================
st.divider()
st.caption("💡 **Arhitectură:** Export Bulk WooCommerce (1 request) → Staging → Match → Reconcile → Load")
st.caption("🔌 **Conexiune:** PostgreSQL direct + WooCommerce Export Bulk API")
st.caption("⚡ **Performanță:** ~10-30 secunde pentru import complet")
