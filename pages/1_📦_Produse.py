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

def safe_int(value, default=0):
    if value is None or value == "" or value == "null":
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
        value = attr.get("option", "")
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
        r = cursor.fetchone()
        cursor.close()
        conn.close()
        return r[0] if r else None
    except:
        return None

# =========================
#   WOO – QUICK REFRESH
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
            sku_to_product = {r["sku"]: r["product_id"] for r in known_skus}
            st.success(f"✅ {len(known_skus)} SKU-uri încărcate")
        except Exception as e:
            st.error(f"Eroare DB: {e}")
            return

        st.info("🚀 Fetch din WooCommerce...")
        try:
            start = time.time()
            resp = wcapi.get("products/export-full")
            if resp.status_code != 200:
                st.error(f"Eroare API: {resp.status_code}")
                st.code(resp.text)
                return
            data = resp.json()
            if not data.get("success"):
                st.error(f"Export eșuat: {data.get('message')}")
                return
            woo_products = data.get("products", [])
            st.success(f"✅ {len(woo_products)} produse în {time.time()-start:.2f}s")
        except Exception as e:
            st.error(f"Eroare fetch: {e}")
            return

        prices_data, stock_data, attrs = [], [], []
        matched = 0
        pb = st.progress(0)

        for idx, p in enumerate(woo_products):
            pb.progress((idx+1)/len(woo_products))
            try:
                sku = p.get("sku", "").strip()
                if not sku or sku not in sku_to_product:
                    continue
                product_id = sku_to_product[sku]
                matched += 1
                pt = p.get("product_type", "simple")
                wpid = p.get("woo_product_id")
                wvid = p.get("woo_variation_id")
                parent = p.get("parent_id")
                rp = safe_decimal(p.get("regular_price"), 0)
                sp_raw = p.get("sale_price")
                sp = safe_decimal(sp_raw) if sp_raw else None
                sq = safe_int(p.get("stock_quantity"), 0)

                prices_data.append((
                    product_id, sku,
                    parent if pt == "variation" else wpid,
                    wvid if pt == "variation" else None,
                    rp, sp
                ))
                stock_data.append((
                    product_id, sku,
                    parent if pt == "variation" else wpid,
                    wvid if pt == "variation" else None,
                    sq
                ))
                if pt == "variation" and p.get("attributes"):
                    for a in p["attributes"]:
                        an = a.get("name", "")
                        av = a.get("option", "")
                        if an and av:
                            attrs.append((product_id, parent, wvid, an, av))
            except Exception:
                pass
        pb.empty()

        if not (prices_data or stock_data):
            st.warning("Nu am găsit date de salvat")
            return

        st.info("💾 Salvez în DB...")
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            if prices_data:
                execute_batch(cursor, """
                    INSERT INTO woo_preturi 
                    (product_id, sku, woo_product_id, woo_variation_id, regular_price, sale_price)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE
                    SET product_id = EXCLUDED.product_id,
                        sku = EXCLUDED.sku,
                        regular_price = EXCLUDED.regular_price,
                        sale_price = EXCLUDED.sale_price,
                        last_sync = NOW()
                """, prices_data, page_size=500)
            if stock_data:
                execute_batch(cursor, """
                    INSERT INTO woo_stoc 
                    (product_id, sku, woo_product_id, woo_variation_id, stock_quantity)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (woo_product_id, woo_variation_id) DO UPDATE
                    SET product_id = EXCLUDED.product_id,
                        sku = EXCLUDED.sku,
                        stock_quantity = EXCLUDED.stock_quantity,
                        last_sync = NOW()
                """, stock_data, page_size=500)
            if attrs:
                execute_batch(cursor, """
                    INSERT INTO woo_variation_attributes 
                    (product_id, woo_product_id, woo_variation_id, attribute_name, attribute_value)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (woo_product_id, woo_variation_id, attribute_name) DO UPDATE
                    SET product_id = EXCLUDED.product_id,
                        attribute_value = EXCLUDED.attribute_value
                """, attrs, page_size=500)
            conn.commit()
            cursor.close()
            conn.close()
            st.success("✅ Quick Refresh complet")
            c1, c2, c3 = st.columns(3)
            c1.metric("💰 Prețuri", len(prices_data))
            c2.metric("📦 Stocuri", len(stock_data))
            c3.metric("✅ Match-uri", matched)
        except Exception as e:
            st.error(f"Eroare salvare: {e}")

# =========================
#   WOO – FULL IMPORT FLOW
# =========================
def fetch_and_stage_products_bulk(session_id: str):
    stats = {"total_products_fetched": 0, "simple_products": 0,
             "variations_inserted": 0, "errors": 0, "duration": 0}
    pb = st.progress(0)
    status = st.empty()
    try:
        status.info("🚀 Fetch BULK Woo...")
        start = time.time()
        resp = wcapi.get("products/export-full")
        if resp.status_code != 200:
            st.error(f"❌ Eroare API: {resp.status_code}")
            return stats
        data = resp.json()
        if not data.get("success"):
            st.error(f"❌ Export eșuat: {data.get('message')}")
            return stats
        products = data.get("products", [])
        stats["total_products_fetched"] = len(products)
        status.success(f"✅ {len(products)} produse ({time.time()-start:.2f}s)")
        if not products:
            return stats

        conn = get_db_connection()
        cursor = conn.cursor()
        total = len(products)
        for idx, p in enumerate(products):
            pb.progress((idx+1)/total)
            try:
                pt = p.get("product_type", "simple")
                wpid = p.get("woo_product_id")
                wvid = p.get("woo_variation_id")
                name = p.get("name", "Produs fără nume")
                sku = p.get("sku", "").strip()
                rp = safe_decimal(p.get("regular_price"), 0)
                sp_raw = p.get("sale_price")
                sp = safe_decimal(sp_raw) if sp_raw else None
                sq = safe_int(p.get("stock_quantity"), 0)
                if pt == "variation":
                    parent_name = p.get("parent_name", "")
                    attrs = p.get("attributes", [])
                    full_name = compose_variation_name(parent_name, attrs)
                    stats["variations_inserted"] += 1
                else:
                    full_name = name
                    stats["simple_products"] += 1
                parent_conflict = p.get("parent_id") if pt == "variation" else wpid
                cursor.execute("""
                    INSERT INTO woo_staging_raw
                    (import_session_id, woo_product_id, woo_variation_id, product_type,
                     parent_name, sku, regular_price, sale_price, stock_quantity, attributes, raw_data)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                    ON CONFLICT (import_session_id, woo_product_id, woo_variation_id) DO UPDATE
                    SET parent_name = EXCLUDED.parent_name,
                        sku = EXCLUDED.sku,
                        regular_price = EXCLUDED.regular_price,
                        sale_price = EXCLUDED.sale_price,
                        stock_quantity = EXCLUDED.stock_quantity
                """, (session_id, parent_conflict, wvid, pt,
                      full_name, sku if sku else None,
                      rp, sp, sq,
                      json.dumps(p.get("attributes", [])),
                      json.dumps(p)))
                if (idx+1) % 100 == 0:
                    conn.commit()
                    status.info(f"💾 {idx+1}/{total}")
            except Exception:
                stats["errors"] += 1
        conn.commit()
        cursor.close()
        conn.close()
        stats["duration"] = time.time()-start
        pb.empty()
        status.success(f"✅ Staging complet ({stats['variations_inserted']+stats['simple_products']} produse)")
    except Exception as e:
        st.error(f"❌ Eroare FAZA 1: {e}")
    return stats

def run_sku_matching_and_autocreate(session_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        st.info("🔍 Matching SKU-uri...")
        cursor.execute("SELECT * FROM match_skus_for_session(%s)", (session_id,))
        matches = cursor.fetchall()
        if not matches:
            st.warning("⚠️ match_skus_for_session nu a returnat nimic")
            cursor.close()
            conn.close()
            return {}
        match_data = []
        created = 0
        for m in matches:
            staging_raw_id, sku, product_id, match_type, requires_action = m
            if match_type == "unknown" and sku:
                cursor.execute("SELECT parent_name FROM woo_staging_raw WHERE id=%s", (staging_raw_id,))
                r = cursor.fetchone()
                product_name = r[0] if r else f"Produs {sku}"
                new_id = str(uuid.uuid4())
                cursor.execute("INSERT INTO product (id, name) VALUES (%s,%s)", (new_id, product_name))
                cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s,%s,true)", (sku, new_id))
                product_id = new_id
                match_type = "auto_created"
                requires_action = False
                created += 1
            match_data.append((session_id, staging_raw_id, sku, product_id, match_type, requires_action))
        if match_data:
            execute_batch(cursor, """
                INSERT INTO woo_staging_matched
                (import_session_id, staging_raw_id, sku, product_id, match_type, requires_action)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (import_session_id, staging_raw_id) DO UPDATE
                SET sku=EXCLUDED.sku,
                    product_id=EXCLUDED.product_id,
                    match_type=EXCLUDED.match_type,
                    requires_action=EXCLUDED.requires_action
            """, match_data, page_size=1000)
            conn.commit()
        cursor.execute("SELECT * FROM v_import_status WHERE import_session_id=%s", (session_id,))
        s = cursor.fetchone()
        cursor.close()
        conn.close()
        if s:
            return {
                "total": s[1],
                "matched_primary": s[2],
                "matched_alias": s[3],
                "matched_remembered": s[4],
                "unknown": s[5],
                "duplicates": s[6],
                "errors": s[7],
                "pending_actions": s[8],
                "auto_created": created,
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
            SELECT sm.id as match_id, sm.sku, sm.product_id,
                   sr.parent_name, p.name as existing_product_name
            FROM woo_staging_matched sm
            JOIN woo_staging_raw sr ON sr.id = sm.staging_raw_id
            LEFT JOIN product p ON p.id = sm.product_id
            WHERE sm.import_session_id=%s
              AND sm.match_type='alias'
              AND sm.requires_action=true
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
        cursor.execute("UPDATE woo_staging_matched SET action_taken=%s, requires_action=false WHERE id=%s",
                       (action, match_id))
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
        st.info("📦 Finalizare import Woo...")
        cursor.execute("DELETE FROM woo_variation_attributes")
        cursor.execute("DELETE FROM woo_stoc")
        cursor.execute("DELETE FROM woo_preturi")
        cursor.execute("SELECT * FROM finalize_import(%s)", (session_id,))
        r = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()
        if r:
            return {"prices_inserted": r[0], "stock_inserted": r[1], "attributes_inserted": r[2]}
        return {}
    except Exception as e:
        st.error(f"❌ Eroare FAZA 4: {e}")
        return {}

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
            timeout=30,
        )
        if r.status_code == 200:
            return r.json()
        st.error(f"SmartBill API error: {r.status_code}")
        st.code(r.text)
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
        code = p.get("productCode", "").strip() or p.get("code", "").strip()
        if not code:
            continue
        sb_dict[code] = {
            "name": p.get("productName", "") or p.get("name", ""),
            "stock": float(p.get("quantity", 0)),
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
        for sku, dec, pid in rows:
            out[sku] = {"decision": dec, "product_id": pid}
        return out
    except Exception as e:
        st.error(f"Eroare citire decizii SmartBill: {e}")
        return {}

def save_smartbill_decision(sku, action, name=None):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if action == "creaza_nou":
            new_id = str(uuid.uuid4())
            cursor.execute("INSERT INTO product (id, name) VALUES (%s,%s)", (new_id, name or sku))
            cursor.execute("INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s,%s,true)", (sku, new_id))
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions
                (smartbill_sku, product_id, decision_type, decided_at)
                VALUES (%s,%s,%s,NOW())
                ON CONFLICT (smartbill_sku) DO UPDATE
                SET product_id=EXCLUDED.product_id,
                    decision_type=EXCLUDED.decision_type,
                    decided_at=NOW()
            """, (sku, new_id, action))
        else:
            cursor.execute("""
                INSERT INTO smartbill_sku_mapping_decisions
                (smartbill_sku, product_id, decision_type, decided_at)
                VALUES (%s,NULL,%s,NOW())
                ON CONFLICT (smartbill_sku) DO UPDATE
                SET decision_type=EXCLUDED.decision_type,
                    decided_at=NOW()
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

def sync_smartbill_data():
    """UI + logic pentru SmartBill, cu state persistent și un singur buton de salvare pe pagină."""
    config = init_smartbill()

    # FETCH button
    if st.button("📥 Fetch stoc din SmartBill", use_container_width=True):
        with st.spinner("Fetch SmartBill..."):
            data = get_smartbill_stocks(config["email"], config["token"], config["cif"])
            if data:
                st.session_state.smartbill_data = process_smartbill_data(data)
                st.session_state.smartbill_page = 1
                st.success(f"✅ {len(st.session_state.smartbill_data)} produse citite")
            else:
                st.error("❌ Nu am primit date de la SmartBill")

    if not st.session_state.smartbill_data:
        return

    sb_products = st.session_state.smartbill_data
    decisions = get_smartbill_decisions()

    # SKU-uri din local
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sku, product_id FROM product_sku")
        sku_to_product = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.close()
        conn.close()
    except Exception as e:
        st.error(f"Eroare DB: {e}")
        return

    # Împărțire matched / unmatched
    stock_data = []
    unmatched = []
    for sku, info in sb_products.items():
        dec_info = decisions.get(sku, {})
        dec = dec_info.get("decision")
        if sku in sku_to_product:
            stock_data.append((sku_to_product[sku], sku, Decimal(info["stock"])))
        elif dec == "ignora":
            continue
        elif dec == "creaza_nou" and dec_info.get("product_id"):
            stock_data.append((dec_info["product_id"], sku, Decimal(info["stock"])))
        elif dec == "asteapta":
            continue
        else:
            unmatched.append({"sku": sku, "name": info["name"], "stock": info["stock"]})

    st.warning(f"⚠️ {len(unmatched)} SKU-uri nemapate")

    # Paginare
    page_size = 10
    total_pages = max(1, (len(unmatched) + page_size - 1) // page_size)
    colp1, colp2 = st.columns([1, 5])
    with colp1:
        page = st.number_input("Pagina", 1, total_pages, st.session_state.smartbill_page)
        st.session_state.smartbill_page = page
    with colp2:
        st.write("")

    start = (st.session_state.smartbill_page - 1) * page_size
    end = start + page_size
    page_items = unmatched[start:end]

    action_labels = ["Alege...", "Creează nou", "Ignoră", "Așteaptă"]
    action_map = {"Creează nou": "creaza_nou", "Ignoră": "ignora", "Așteaptă": "asteapta"}

    with st.expander("📋 SKU-uri necunoscute", expanded=True):
        for item in page_items:
            sku = item["sku"]
            name = item["name"]
            stock = item["stock"]
            dec_info = decisions.get(sku, {})
            default_label = "Alege..."
            if dec_info.get("decision") == "creaza_nou":
                default_label = "Creează nou"
            elif dec_info.get("decision") == "ignora":
                default_label = "Ignoră"
            elif dec_info.get("decision") == "asteapta":
                default_label = "Așteaptă"

            c1, c2, c3, c4 = st.columns([2, 4, 2, 2])
            c1.write(f"**{sku}**")
            c2.write(name)
            c3.write(stock)
            c4.selectbox(
                "Acțiune",
                action_labels,
                index=action_labels.index(default_label),
                key=f"sb_action_{sku}",
                label_visibility="collapsed",
            )

        # Buton UNIC pentru salvarea tuturor selecțiilor de pe pagină
        if st.button("💾 Salvează această pagină", use_container_width=True):
            any_saved = False
            for item in page_items:
                sku = item["sku"]
                name = item["name"]
                selected_label = st.session_state.get(f"sb_action_{sku}", "Alege...")
                if selected_label in action_map:
                    code = action_map[selected_label]
                    if save_smartbill_decision(sku, code, name):
                        any_saved = True
            if any_saved:
                st.success("✅ Deciziile pentru pagina curentă au fost salvate")
                time.sleep(0.7)
                st.rerun()
            else:
                st.info("Nu ai selectat nicio acțiune pentru această pagină")

    # Buton separată pentru a scrie stocurile în DB
    if stock_data:
        st.info(f"📦 {len(stock_data)} produse cu match – gata de sync în DB")
        if st.button("💾 Salvează toate stocurile SmartBill în DB", type="primary", use_container_width=True):
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM smartbill_stoc")
                execute_batch(
                    cursor,
                    "INSERT INTO smartbill_stoc (product_id, sku, stock_quantity) VALUES (%s,%s,%s)",
                    stock_data,
                    page_size=500,
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success("✅ Stocuri SmartBill salvate în DB")
            except Exception as e:
                st.error(f"Eroare salvare stocuri: {e}")

# =========================
#   UI WOO
# =========================
st.markdown("## 🛒 WooCommerce Import")

c1, c2, c3 = st.columns(3)
with c1:
    if st.button("🚀 Full Import", type="primary", use_container_width=True):
        st.session_state["import_session_id"] = str(uuid.uuid4())
        st.session_state["import_phase"] = "extracting"
        st.session_state["import_stats"] = {}
        with st.spinner("Curăț staging..."):
            clear_staging_tables()
        st.rerun()
with c2:
    if st.button("🔄 Rulează Matching", use_container_width=True):
        last_session = get_latest_session_id()
        if last_session:
            st.session_state["import_session_id"] = last_session
            st.session_state["import_phase"] = "matching"
            st.session_state["import_stats"] = {}
            st.rerun()
        else:
            st.error("Nu există date în staging!")
with c3:
    if st.button("⚡ Quick Refresh", use_container_width=True):
        quick_refresh_prices_and_stock()

if st.session_state["import_phase"]:
    st.info(f"Status workflow Woo: {st.session_state['import_phase']}")

if st.session_state["import_session_id"] and st.session_state["import_phase"] == "extracting":
    with st.spinner("Extrag produse..."):
        s = fetch_and_stage_products_bulk(st.session_state["import_session_id"])
        st.session_state["import_stats"]["extract"] = s
        st.session_state["import_phase"] = "matching"
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == "matching":
    with st.spinner("Matching SKU-uri..."):
        ms = run_sku_matching_and_autocreate(st.session_state["import_session_id"])
        st.session_state["import_stats"]["matching"] = ms
        if ms:
            st.session_state["import_phase"] = "reconciling"
    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == "reconciling":
    ms = st.session_state["import_stats"].get("matching", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Match automat", ms.get("matched_primary", 0))
    c2.metric("Auto create", ms.get("auto_created", 0))
    c3.metric("Aliasuri", ms.get("matched_alias", 0))
    c4.metric("Duplicate", ms.get("duplicates", 0))
    pending = ms.get("pending_actions", 0)
    if pending == 0:
        if st.button("📦 Finalizează import Woo", type="primary"):
            st.session_state["import_phase"] = "finalizing"
            st.rerun()
    else:
        st.warning(f"{pending} aliasuri de confirmat")
        items = get_pending_aliases(st.session_state["import_session_id"])
        for it in items:
            with st.expander(f"{it['sku']} → {it['parent_name']}"):
                st.write(f"Produs existent: {it['existing_product_name']}")
                c1, c2 = st.columns(2)
                if c1.button("Confirmă", key=f"c_{it['match_id']}"):
                    mark_alias_action(it["match_id"], "confirmed")
                    st.rerun()
                if c2.button("Skip", key=f"s_{it['match_id']}"):
                    mark_alias_action(it["match_id"], "skipped")
                    st.rerun()

if st.session_state["import_session_id"] and st.session_state["import_phase"] == "finalizing":
    with st.spinner("Finalizare import..."):
        finalize_import(st.session_state["import_session_id"])
        st.session_state["import_phase"] = "done"
    st.rerun()

if st.session_state["import_phase"] == "done":
    st.success("✅ Import WooCommerce complet")
    if st.button("Reset workflow Woo"):
        st.session_state["import_session_id"] = None
        st.session_state["import_phase"] = None
        st.session_state["import_stats"] = {}
        st.rerun()

# =========================
#   UI SMARTBILL
# =========================
st.markdown("## 📊 SmartBill Sync")
sync_smartbill_data()
