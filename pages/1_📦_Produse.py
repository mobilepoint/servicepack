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
                # VERIFICĂM DACĂ SKU-ul EXISTĂ DEJA
                cursor.execute("SELECT product_id FROM product_sku WHERE sku = %s", (sku,))
                existing = cursor.fetchone()

                if existing:
                    # SKU-ul există deja, îl tratăm ca matched_primary
                    product_id = existing[0]
                    match_type = 'matched_primary'
                    requires_action = False
                else:
                    # SKU-ul nu există, îl creăm
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
