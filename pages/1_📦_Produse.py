# pages/2_📦_Produse.py
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
if "unknown_skus" not in st.session_state:
    st.session_state["unknown_skus"] = []
if "alias_confirmations" not in st.session_state:
    st.session_state["alias_confirmations"] = []
if "import_errors" not in st.session_state:
    st.session_state["import_errors"] = []
if "import_stats" not in st.session_state:
    st.session_state["import_stats"] = {}
if "duplicate_skus" not in st.session_state:
    st.session_state["duplicate_skus"] = []

# =========================
#   DATABASE FUNCTIONS
# =========================
def get_sku_mapping(sku: str):
    """
    Verifică dacă SKU există în:
    1. product_sku ca primary -> returnează product_id, 'primary'
    2. product_sku ca alias -> returnează product_id, 'alias'
    3. sku_mapping_decisions -> returnează product_id, 'remembered'
    4. Nu există -> returnează None, 'unknown'
    """
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
        # Check primary
        cursor.execute(
            "SELECT product_id FROM product_sku WHERE sku = %s AND is_primary = true",
            (sku,)
        )
        result = cursor.fetchone()
        if result:
            cursor.close()
            conn.close()
            return result[0], 'primary'
        
        # Check alias
        cursor.execute(
            "SELECT product_id FROM product_sku WHERE sku = %s AND is_primary = false",
            (sku,)
        )
        result = cursor.fetchone()
        if result:
            cursor.close()
            conn.close()
            return result[0], 'alias'
        
        # Check remembered decisions
        cursor.execute(
            "SELECT product_id, decision_type FROM sku_mapping_decisions WHERE woo_sku = %s",
            (sku,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if result:
            return result[0], 'remembered'
        
        return None, 'unknown'
        
    except Exception as e:
        st.error(f"Eroare verificare SKU: {e}")
        return None, 'error'

def check_duplicate_skus(sku: str):
    """Verifică dacă SKU-ul apare de mai multe ori în baza de date"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                ps.product_id,
                p.name,
                ps.is_primary
            FROM product_sku ps
            JOIN product p ON p.id = ps.product_id
            WHERE ps.sku = %s
        """, (sku,))
        
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return results
    except Exception as e:
        st.error(f"Eroare verificare duplicate: {e}")
        return []

def save_mapping_decision(woo_sku: str, product_id: str, decision_type: str):
    """Salvează decizia de asociere pentru viitor"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
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

def create_new_product(name: str, sku: str):
    """Creează produs nou în tabelul product și product_sku"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
        # Creează produs
        product_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO product (id, name) VALUES (%s, %s)",
            (product_id, name)
        )
        
        # Adaugă SKU ca primary
        cursor.execute(
            "INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, true)",
            (sku, product_id)
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return product_id
    except Exception as e:
        st.error(f"Eroare creare produs: {e}")
        return None

def add_sku_as_alias(sku: str, product_id: str):
    """Adaugă SKU ca alias la un produs existent"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, false) ON CONFLICT (sku) DO NOTHING",
            (sku, product_id)
        )
        
        conn.commit()
        affected = cursor.rowcount
        cursor.close()
        conn.close()
        
        return affected > 0
    except Exception as e:
        st.error(f"Eroare adăugare alias: {e}")
        return False

def log_import_error(session_id: str, error_data: dict):
    """Log eroare în tabelul woo_import_errors"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO woo_import_errors 
            (import_session_id, woo_product_id, woo_variation_id, sku, error_type, error_message, product_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        """, (
            session_id,
            error_data.get('woo_product_id'),
            error_data.get('woo_variation_id'),
            error_data.get('sku'),
            error_data.get('error_type'),
            error_data.get('error_message'),
            json.dumps(error_data.get('product_data', {}))
        ))
        
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        st.warning(f"Nu am putut loga eroarea: {e}")

def clear_woo_data():
    """Șterge toate datele din woo_preturi, woo_stoc, woo_variation_attributes (REPLACE ALL strategy)"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM woo_variation_attributes")
        cursor.execute("DELETE FROM woo_stoc")
        cursor.execute("DELETE FROM woo_preturi")
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare ștergere date vechi: {e}")
        return False

def bulk_insert_prices(prices_data: list):
    """Insert batch prețuri"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
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
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare insert prețuri: {e}")
        return False

def bulk_insert_stock(stock_data: list):
    """Insert batch stocuri"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
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
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Eroare insert stocuri: {e}")
        return False

def bulk_insert_attributes(attributes_data: list):
    """Insert batch atribute variații"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url)
        cursor = conn.cursor()
        
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
        return True
    except Exception as e:
        st.error(f"Eroare insert atribute: {e}")
        return False

# =========================
#   WOOCOMMERCE FUNCTIONS
# =========================
def fetch_all_products():
    """Fetch toate produsele din WooCommerce (simple + variații)"""
    all_products = []
    page = 1
    per_page = 100
    
    progress_placeholder = st.empty()
    
    while True:
        progress_placeholder.info(f"📥 Se citesc produsele din WooCommerce... pagina {page}")
        
        try:
            response = wcapi.get("products", params={"per_page": per_page, "page": page})
            
            if response.status_code != 200:
                st.error(f"Eroare API WooCommerce: {response.status_code}")
                break
            
            products = response.json()
            
            if not products:
                break
            
            all_products.extend(products)
            page += 1
            
            time.sleep(0.5)  # Rate limiting
            
        except Exception as e:
            st.error(f"Eroare fetch produse: {e}")
            break
    
    progress_placeholder.empty()
    return all_products

def fetch_variations_for_product(product_id: int):
    """Fetch variații pentru un produs variabil"""
    all_variations = []
    page = 1
    per_page = 100
    
    while True:
        try:
            response = wcapi.get(f"products/{product_id}/variations", params={"per_page": per_page, "page": page})
            
            if response.status_code != 200:
                break
            
            variations = response.json()
            
            if not variations:
                break
            
            all_variations.extend(variations)
            page += 1
            
            time.sleep(0.3)
            
        except Exception as e:
            st.warning(f"Eroare fetch variații pentru product {product_id}: {e}")
            break
    
    return all_variations

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

# =========================
#   IMPORT LOGIC
# =========================
def process_products_batch(products_batch: list, session_id: str):
    """
    Procesează un batch de produse:
    1. Extrage date (simple + variații)
    2. Verifică SKU-uri
    3. Colectează necunoscute/aliasuri pentru confirmare
    4. Inserează în DB
    """
    prices_to_insert = []
    stock_to_insert = []
    attributes_to_insert = []
    unknown_skus = []
    alias_confirmations = []
    duplicate_skus = []
    errors = []
    
    stats = {
        'processed': 0,
        'matched_primary': 0,
        'matched_alias': 0,
        'matched_remembered': 0,
        'unknown': 0,
        'errors': 0,
        'duplicates': 0
    }
    
    for product in products_batch:
        try:
            product_type = product.get('type', '')
            product_id_woo = product.get('id')
            parent_name = product.get('name', 'Produs fără nume')
            
            # SKIP parent-ul produselor variabile
            if product_type == 'variable':
                # Fetch variații
                variations = fetch_variations_for_product(product_id_woo)
                
                for variation in variations:
                    stats['processed'] += 1
                    
                    var_sku = variation.get('sku', '').strip()
                    if not var_sku:
                        errors.append({
                            'woo_product_id': product_id_woo,
                            'woo_variation_id': variation.get('id'),
                            'sku': None,
                            'error_type': 'missing_sku',
                            'error_message': 'Variație fără SKU',
                            'product_data': variation
                        })
                        stats['errors'] += 1
                        continue
                    
                    # Check duplicates
                    duplicates = check_duplicate_skus(var_sku)
                    if len(duplicates) > 1:
                        duplicate_skus.append({
                            'sku': var_sku,
                            'products': duplicates,
                            'woo_data': variation
                        })
                        stats['duplicates'] += 1
                        continue
                    
                    # Check mapping
                    product_id_db, match_type = get_sku_mapping(var_sku)
                    
                    if match_type == 'primary':
                        stats['matched_primary'] += 1
                    elif match_type == 'remembered':
                        stats['matched_remembered'] += 1
                    elif match_type == 'alias':
                        # Cere confirmare
                        alias_confirmations.append({
                            'sku': var_sku,
                            'product_id': product_id_db,
                            'woo_product_id': product_id_woo,
                            'woo_variation_id': variation.get('id'),
                            'name': compose_variation_name(parent_name, variation.get('attributes', [])),
                            'woo_data': variation
                        })
                        stats['matched_alias'] += 1
                        continue
                    elif match_type == 'unknown':
                        unknown_skus.append({
                            'sku': var_sku,
                            'woo_product_id': product_id_woo,
                            'woo_variation_id': variation.get('id'),
                            'name': compose_variation_name(parent_name, variation.get('attributes', [])),
                            'woo_data': variation
                        })
                        stats['unknown'] += 1
                        continue
                    else:
                        errors.append({
                            'woo_product_id': product_id_woo,
                            'woo_variation_id': variation.get('id'),
                            'sku': var_sku,
                            'error_type': 'mapping_error',
                            'error_message': f'Eroare verificare SKU: {match_type}',
                            'product_data': variation
                        })
                        stats['errors'] += 1
                        continue
                    
                    # Prepare data for insert
                    var_name = compose_variation_name(parent_name, variation.get('attributes', []))
                    
                    prices_to_insert.append((
                        product_id_db,
                        var_sku,
                        product_id_woo,
                        variation.get('id'),
                        Decimal(variation.get('regular_price') or 0),
                        Decimal(variation.get('sale_price') or 0) if variation.get('sale_price') else None
                    ))
                    
                    stock_to_insert.append((
                        product_id_db,
                        var_sku,
                        product_id_woo,
                        variation.get('id'),
                        variation.get('stock_quantity') or 0
                    ))
                    
                    # Atribute
                    for attr in variation.get('attributes', []):
                        attributes_to_insert.append((
                            product_id_db,
                            product_id_woo,
                            variation.get('id'),
                            attr.get('name', ''),
                            attr.get('option', '')
                        ))
            
            # Produse SIMPLE
            elif product_type == 'simple':
                stats['processed'] += 1
                
                sku = product.get('sku', '').strip()
                if not sku:
                    errors.append({
                        'woo_product_id': product_id_woo,
                        'woo_variation_id': None,
                        'sku': None,
                        'error_type': 'missing_sku',
                        'error_message': 'Produs simplu fără SKU',
                        'product_data': product
                    })
                    stats['errors'] += 1
                    continue
                
                # Check duplicates
                duplicates = check_duplicate_skus(sku)
                if len(duplicates) > 1:
                    duplicate_skus.append({
                        'sku': sku,
                        'products': duplicates,
                        'woo_data': product
                    })
                    stats['duplicates'] += 1
                    continue
                
                # Check mapping
                product_id_db, match_type = get_sku_mapping(sku)
                
                if match_type == 'primary':
                    stats['matched_primary'] += 1
                elif match_type == 'remembered':
                    stats['matched_remembered'] += 1
                elif match_type == 'alias':
                    alias_confirmations.append({
                        'sku': sku,
                        'product_id': product_id_db,
                        'woo_product_id': product_id_woo,
                        'woo_variation_id': None,
                        'name': parent_name,
                        'woo_data': product
                    })
                    stats['matched_alias'] += 1
                    continue
                elif match_type == 'unknown':
                    unknown_skus.append({
                        'sku': sku,
                        'woo_product_id': product_id_woo,
                        'woo_variation_id': None,
                        'name': parent_name,
                        'woo_data': product
                    })
                    stats['unknown'] += 1
                    continue
                else:
                    errors.append({
                        'woo_product_id': product_id_woo,
                        'woo_variation_id': None,
                        'sku': sku,
                        'error_type': 'mapping_error',
                        'error_message': f'Eroare verificare SKU: {match_type}',
                        'product_data': product
                    })
                    stats['errors'] += 1
                    continue
                
                prices_to_insert.append((
                    product_id_db,
                    sku,
                    product_id_woo,
                    None,
                    Decimal(product.get('regular_price') or 0),
                    Decimal(product.get('sale_price') or 0) if product.get('sale_price') else None
                ))
                
                stock_to_insert.append((
                    product_id_db,
                    sku,
                    product_id_woo,
                    None,
                    product.get('stock_quantity') or 0
                ))
        
        except Exception as e:
            errors.append({
                'woo_product_id': product.get('id'),
                'woo_variation_id': None,
                'sku': None,
                'error_type': 'processing_error',
                'error_message': str(e),
                'product_data': product
            })
            stats['errors'] += 1
    
    # Insert în DB
    if prices_to_insert:
        bulk_insert_prices(prices_to_insert)
    if stock_to_insert:
        bulk_insert_stock(stock_to_insert)
    if attributes_to_insert:
        bulk_insert_attributes(attributes_to_insert)
    
    # Log errors
    for error in errors:
        log_import_error(session_id, error)
    
    return {
        'stats': stats,
        'unknown_skus': unknown_skus,
        'alias_confirmations': alias_confirmations,
        'duplicate_skus': duplicate_skus,
        'errors': errors
    }

# =========================
#   UI
# =========================
st.markdown("### 🔄 Import complet din WooCommerce")
st.info("Acest proces importă **toate produsele simple** și **toate variațiile** (fără parent-uri variabile) din WooCommerce.")

col1, col2 = st.columns([3, 1])

with col1:
    st.markdown("**Strategie:** REPLACE ALL (șterge datele vechi și reimportă tot)")
    st.markdown("**Batch size:** 80 produse pe batch")

with col2:
    if st.button("🚀 Start Import", type="primary", use_container_width=True):
        # Clear previous session
        st.session_state["import_session_id"] = str(uuid.uuid4())
        st.session_state["unknown_skus"] = []
        st.session_state["alias_confirmations"] = []
        st.session_state["import_errors"] = []
        st.session_state["duplicate_skus"] = []
        st.session_state["import_stats"] = {
            'processed': 0,
            'matched_primary': 0,
            'matched_alias': 0,
            'matched_remembered': 0,
            'unknown': 0,
            'errors': 0,
            'duplicates': 0
        }
        
        # Clear old data
        with st.spinner("🗑️ Șterg datele vechi..."):
            clear_woo_data()
        
        st.success("✅ Date vechi șterse. Încep import...")
        
        # Fetch products
        all_products = fetch_all_products()
        
        if not all_products:
            st.error("Nu am găsit produse în WooCommerce!")
            st.stop()
        
        st.info(f"📦 Am găsit {len(all_products)} produse în WooCommerce. Încep procesarea...")
        
        # Process în batch-uri de 80
        batch_size = 80
        total_batches = (len(all_products) + batch_size - 1) // batch_size
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for i in range(0, len(all_products), batch_size):
            batch = all_products[i:i+batch_size]
            batch_num = i // batch_size + 1
            
            status_text.text(f"📊 Procesez batch {batch_num}/{total_batches}...")
            
            result = process_products_batch(batch, st.session_state["import_session_id"])
            
            # Update stats
            for key in st.session_state["import_stats"]:
                st.session_state["import_stats"][key] += result['stats'][key]
            
            st.session_state["unknown_skus"].extend(result['unknown_skus'])
            st.session_state["alias_confirmations"].extend(result['alias_confirmations'])
            st.session_state["duplicate_skus"].extend(result['duplicate_skus'])
            st.session_state["import_errors"].extend(result['errors'])
            
            progress_bar.progress((batch_num) / total_batches)
        
        progress_bar.empty()
        status_text.empty()
        
        st.success("✅ Import complet!")
        st.balloons()

# =========================
#   STATISTICS
# =========================
if st.session_state["import_stats"]:
    st.divider()
    st.markdown("### 📊 Statistici import")
    
    stats = st.session_state["import_stats"]
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📦 Procesate", stats['processed'])
    with col2:
        st.metric("✅ Match automat", stats['matched_primary'] + stats['matched_remembered'])
    with col3:
        st.metric("⚠️ Necesită confirmare", stats['matched_alias'] + stats['unknown'])
    with col4:
        st.metric("❌ Erori", stats['errors'] + stats['duplicates'])

# =========================
#   DUPLICATE SKUS
# =========================
if st.session_state["duplicate_skus"]:
    st.divider()
    st.markdown("### ⚠️ SKU-uri duplicate detectate")
    st.error(f"Am găsit {len(st.session_state['duplicate_skus'])} SKU-uri care apar în multiple produse!")
    
    for dup in st.session_state["duplicate_skus"]:
        with st.expander(f"🔴 SKU: {dup['sku']} - {len(dup['products'])} produse"):
            st.write("**Produse din baza de date:**")
            for prod_id, prod_name, is_primary in dup['products']:
                st.write(f"- {prod_name} ({prod_id}) - {'PRIMARY' if is_primary else 'ALIAS'}")
            
            st.json(dup['woo_data'])

# =========================
#   ALIAS CONFIRMATIONS
# =========================
if st.session_state["alias_confirmations"]:
    st.divider()
    st.markdown("### 🤔 Confirmă asocieri SKU-uri alias")
    st.warning(f"{len(st.session_state['alias_confirmations'])} SKU-uri sunt aliasuri existente. Confirmă asocierea?")
    
    for idx, alias_item in enumerate(st.session_state["alias_confirmations"]):
        with st.expander(f"SKU: {alias_item['sku']} → {alias_item['name']}"):
            st.info(f"Acest SKU există ca **ALIAS** pentru produsul cu ID: `{alias_item['product_id']}`")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button(f"✅ Confirmă și memorează", key=f"confirm_alias_{idx}"):
                    # Salvează decizie
                    save_mapping_decision(alias_item['sku'], alias_item['product_id'], 'confirmed_alias')
                    
                    # Insert date
                    bulk_insert_prices([(
                        alias_item['product_id'],
                        alias_item['sku'],
                        alias_item['woo_product_id'],
                        alias_item['woo_variation_id'],
                        Decimal(alias_item['woo_data'].get('regular_price') or 0),
                        Decimal(alias_item['woo_data'].get('sale_price') or 0) if alias_item['woo_data'].get('sale_price') else None
                    )])
                    
                    bulk_insert_stock([(
                        alias_item['product_id'],
                        alias_item['sku'],
                        alias_item['woo_product_id'],
                        alias_item['woo_variation_id'],
                        alias_item['woo_data'].get('stock_quantity') or 0
                    )])
                    
                    st.success("Asociere confirmată!")
                    st.session_state["alias_confirmations"].pop(idx)
                    st.rerun()
            
            with col2:
                if st.button(f"❌ Skip", key=f"skip_alias_{idx}"):
                    st.session_state["alias_confirmations"].pop(idx)
                    st.rerun()

# =========================
#   UNKNOWN SKUS RECONCILIATION
# =========================
if st.session_state["unknown_skus"]:
    st.divider()
    st.markdown("### 🆕 SKU-uri necunoscute")
    st.info(f"{len(st.session_state['unknown_skus'])} SKU-uri nu există în baza de date. Alege acțiunea:")
    
    for idx, unknown in enumerate(st.session_state["unknown_skus"]):
        with st.expander(f"SKU: {unknown['sku']} - {unknown['name']}"):
            st.json(unknown['woo_data'])
            
            action = st.radio(
                "Acțiune:",
                ["Creează produs NOU", "Asociază la produs existent"],
                key=f"action_{idx}"
            )
            
            if action == "Creează produs NOU":
                if st.button(f"✅ Creează și memorează", key=f"create_new_{idx}"):
                    product_id = create_new_product(unknown['name'], unknown['sku'])
                    
                    if product_id:
                        save_mapping_decision(unknown['sku'], product_id, 'new_product')
                        
                        # Insert date
                        bulk_insert_prices([(
                            product_id,
                            unknown['sku'],
                            unknown['woo_product_id'],
                            unknown['woo_variation_id'],
                            Decimal(unknown['woo_data'].get('regular_price') or 0),
                            Decimal(unknown['woo_data'].get('sale_price') or 0) if unknown['woo_data'].get('sale_price') else None
                        )])
                        
                        bulk_insert_stock([(
                            product_id,
                            unknown['sku'],
                            unknown['woo_product_id'],
                            unknown['woo_variation_id'],
                            unknown['woo_data'].get('stock_quantity') or 0
                        )])
                        
                        st.success(f"Produs creat cu ID: {product_id}")
                        st.session_state["unknown_skus"].pop(idx)
                        st.rerun()
            
            else:
                # TODO: Add search/select existing product
                st.text_input("Caută produs:", key=f"search_product_{idx}")
                st.caption("(Funcționalitate de căutare în dezvoltare)")

# =========================
#   ERRORS
# =========================
if st.session_state["import_errors"]:
    st.divider()
    st.markdown("### ❌ Erori la import")
    st.error(f"{len(st.session_state['import_errors'])} produse cu erori")
    
    with st.expander(f"Vezi toate erorile ({len(st.session_state['import_errors'])})"):
        for error in st.session_state["import_errors"]:
            st.write(f"**{error['error_type']}:** {error['error_message']}")
            st.write(f"SKU: {error.get('sku', 'N/A')} | WooCommerce ID: {error.get('woo_product_id', 'N/A')}")
            st.json(error.get('product_data', {}))
            st.divider()

# =========================
#   FOOTER
# =========================
st.divider()
st.caption("💡 **Strategie import:** REPLACE ALL - toate datele vechi sunt șterse și reimportate.")
st.caption("🔌 **Conexiune:** PostgreSQL direct + WooCommerce REST API")
