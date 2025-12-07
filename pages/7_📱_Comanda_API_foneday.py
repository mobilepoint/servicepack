import streamlit as st
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from datetime import datetime, timedelta
import requests
import time
import json

# AUTENTIFICARE
if not check_password():
    st.stop()

# SIDEBAR
render_sidebar()

# ===== CONFIGURARE PAGINĂ =====
st.set_page_config(
    page_title="📱 Comanda API Foneday",
    page_icon="📱",
    layout="wide"
)

# ===== ÎNCĂRCARE CONFIGURAȚIE DIN SECRETS =====
try:
    # PostgreSQL
    PG_URL = st.secrets["connections"]["postgresql"]["url"]
    
    # WooCommerce
    WOO_URL = st.secrets["connections"]["woocommerce"]["WOO_URL"]
    WOO_CONSUMER_KEY = st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_KEY"]
    WOO_CONSUMER_SECRET = st.secrets["connections"]["woocommerce"]["WOO_CONSUMER_SECRET"]
    
    # Foneday
    FONEDAY_API_URL = st.secrets["connections"]["foneday"]["API_URL"]
    FONEDAY_API_TOKEN = st.secrets["connections"]["foneday"]["API_TOKEN"]
    
    # Parametri calcul profit (cu valori default)
    EUR_RON_RATE = float(st.secrets.get("EUR_RON_RATE", 5.1))
    MIN_PROFIT_MARGIN = float(st.secrets.get("MIN_PROFIT_MARGIN", 0.88))
    TVA_RATE = float(st.secrets.get("TVA_RATE", 1.21))
    
except Exception as e:
    st.error(f"⚠️ Eroare la încărcarea configurației: {e}")
    st.info("Asigură-te că ai completat toate secretele în Streamlit Cloud Settings.")
    st.stop()

# ===== FUNCȚII DATABASE =====
def get_db_connection():
    """Conexiune la PostgreSQL"""
    try:
        conn = psycopg2.connect(PG_URL, connect_timeout=10)
        return conn
    except Exception as e:
        st.error(f"❌ Eroare conexiune DB: {e}")
        return None

def log_event(event_type: str, message: str, sku: str = None, 
              product_id: str = None, status: str = "info"):
    """Salvează evenimente în log"""
    conn = None
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO public.sync_logs (event_type, sku, product_id, message, status)
                VALUES (%s, %s, %s, %s, %s)
            """, (event_type, sku, product_id, message, status))
            conn.commit()
            cursor.close()
    except Exception as e:
        print(f"Error logging: {e}")
    finally:
        if conn:
            conn.close()

# ===== FUNCȚII CALCUL PROFIT =====
def calculate_profit_margin(foneday_price_eur: float, woo_price_ron: float) -> float:
    """Calculează marja de profit în procente"""
    try:
        # Conversie explicit la float
        foneday_price_eur = float(foneday_price_eur)
        woo_price_ron = float(woo_price_ron)
        
        cost_ron = foneday_price_eur * EUR_RON_RATE
        selling_price_without_vat = woo_price_ron / TVA_RATE
        ratio = cost_ron / selling_price_without_vat
        profit_margin = (1 - ratio) * 100
        return round(profit_margin, 2)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0.0

def is_profitable(foneday_price_eur: float, woo_price_ron: float) -> bool:
    """Verifică dacă produsul e profitabil"""
    try:
        # Conversie explicit la float
        foneday_price_eur = float(foneday_price_eur)
        woo_price_ron = float(woo_price_ron)
        
        cost_ron = foneday_price_eur * EUR_RON_RATE
        selling_price_without_vat = woo_price_ron / TVA_RATE
        ratio = cost_ron / selling_price_without_vat
        return ratio < MIN_PROFIT_MARGIN
    except (ValueError, TypeError, ZeroDivisionError):
        return False


# ===== FUNCȚII API FONEDAY =====
@st.cache_data(ttl=300)
def get_foneday_product_by_sku(foneday_sku: str):
    """Obține produs din Foneday după SKU-ul lor"""
    try:
        headers = {
            "Authorization": f"Bearer {FONEDAY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            f"{FONEDAY_API_URL}/product/{foneday_sku}",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return data.get("product")
        return None
    except Exception as e:
        return None

def add_to_foneday_cart(foneday_sku: str, quantity: int, note: str = None):
    """Adaugă produs în coșul Foneday folosind SKU-ul lor"""
    try:
        headers = {
            "Authorization": f"Bearer {FONEDAY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "articles": [{
                "sku": foneday_sku,
                "quantity": quantity,
                "note": note
            }]
        }
        
        response = requests.post(
            f"{FONEDAY_API_URL}/shopping-cart-add-items",
            headers=headers,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        return None

# ===== FUNCȚII HELPERS =====
def get_product_info_from_catalog(sku: str):
    """Obține informații produs din catalog"""
    conn = None
    try:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT product_id, is_primary
                FROM v_product_sku
                WHERE sku = %s AND is_primary = TRUE
                LIMIT 1
            """, (sku,))
            result = cursor.fetchone()
            
            if result:
                product_id = result[0]
                cursor.execute("""
                    SELECT name FROM v_product WHERE id = %s LIMIT 1
                """, (product_id,))
                product_result = cursor.fetchone()
                
                cursor.close()
                
                if product_result:
                    return {"product_id": product_id, "name": product_result[0]}
                return {"product_id": product_id, "name": sku}
            
            cursor.close()
        return None
    except Exception as e:
        print(f"Error in get_product_info: {e}")
        return None
    finally:
        if conn:
            conn.close()

# ============ PASUL 1: Import Foneday ============
def step1_import_foneday_all_products():
    """PASUL 1: Import toate produsele din Foneday + normalizare artcode"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step1_start", "PASUL 1: Începe import complet Foneday", status="info")
    status_container.info("🌐 PASUL 1: Citesc TOATE produsele din Foneday...")
    
    conn = None
    try:
        headers = {
            "Authorization": f"Bearer {FONEDAY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            f"{FONEDAY_API_URL}/products",
            headers=headers,
            timeout=60
        )
        
        if response.status_code != 200:
            error_msg = f"Eroare API Foneday: {response.status_code}"
            st.error(f"❌ {error_msg}")
            log_event("step1_error", error_msg, status="error")
            return 0
        
        data = response.json()
        products = data.get("products", [])
        
        if not products:
            st.warning("⚠️ Nu s-au găsit produse în Foneday")
            log_event("step1_warning", "Nu s-au găsit produse în Foneday", status="warning")
            return 0
        
        status_container.success(f"✅ Găsite {len(products)} produse în Foneday")
        log_event("step1_process", f"Procesez {len(products)} produse Foneday", status="info")
        time.sleep(1)
        
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0
        
        cursor = conn.cursor()
        batch_size = 100
        total_saved = 0
        total_artcodes_normalized = 0
        
        for i in range(0, len(products), batch_size):
            batch = products[i:i+batch_size]
            batch_data = []
            batch_artcodes = []
            
            for product in batch:
                try:
                    foneday_sku = product.get("sku")
                    artcode_raw = product.get("artcode")
                    
                    # Salvează artcode-ul RAW în foneday_products (pentru referință)
                    artcode_for_db = None
                    if artcode_raw:
                        if isinstance(artcode_raw, (list, dict)):
                            artcode_for_db = json.dumps(artcode_raw)
                        else:
                            artcode_for_db = str(artcode_raw)
                    
                    batch_data.append((
                        foneday_sku,
                        artcode_for_db,
                        product.get("ean"),
                        product.get("title"),
                        product.get("instock"),
                        product.get("suitable_for"),
                        product.get("category"),
                        product.get("product_brand"),
                        product.get("quality"),
                        product.get("model_brand"),
                        product.get("model_codes"),
                        float(product.get("price", 0)) if product.get("price") else None,
                        datetime.now()
                    ))
                    
                    # NORMALIZARE ARTCODES - extrage fiecare artcode individual
                    if artcode_raw:
                        artcodes_list = []
                        
                        # Cazul 1: artcode_raw este deja o listă Python
                        if isinstance(artcode_raw, list):
                            artcodes_list = artcode_raw
                        # Cazul 2: artcode_raw este un string care poate fi JSON
                        elif isinstance(artcode_raw, str):
                            # Încearcă să parseze ca JSON
                            try:
                                parsed = json.loads(artcode_raw)
                                if isinstance(parsed, list):
                                    artcodes_list = parsed
                                else:
                                    artcodes_list = [str(parsed)]
                            except (json.JSONDecodeError, ValueError):
                                # Nu e JSON valid, tratează-l ca string simplu
                                artcodes_list = [artcode_raw.strip()]
                        # Cazul 3: altceva (număr, dict, etc.)
                        else:
                            artcodes_list = [str(artcode_raw)]
                        
                        # Curăță și adaugă fiecare artcode
                        for artcode_value in artcodes_list:
                            # Convertește la string și curăță
                            artcode_clean = str(artcode_value).strip()
                            # Elimină ghilimele din exterior (dacă există)
                            artcode_clean = artcode_clean.strip('"').strip("'")
                            
                            # Adaugă doar dacă nu e gol
                            if artcode_clean and artcode_clean != "null" and artcode_clean != "None":
                                batch_artcodes.append((foneday_sku, artcode_clean))
                    
                except Exception as e:
                    log_event("step1_error", f"Eroare procesare produs {foneday_sku}: {e}", status="error")
                    continue
            
            # Salvează produsele în batch
            if batch_data:
                try:
                    execute_values(cursor, """
                        INSERT INTO public.foneday_products 
                        (foneday_sku, artcode, ean, title, instock, suitable_for, category, 
                         product_brand, quality, model_brand, model_codes, price_eur, last_sync_at)
                        VALUES %s
                        ON CONFLICT (foneday_sku) DO UPDATE SET
                            artcode = EXCLUDED.artcode,
                            ean = EXCLUDED.ean,
                            title = EXCLUDED.title,
                            instock = EXCLUDED.instock,
                            suitable_for = EXCLUDED.suitable_for,
                            category = EXCLUDED.category,
                            product_brand = EXCLUDED.product_brand,
                            quality = EXCLUDED.quality,
                            model_brand = EXCLUDED.model_brand,
                            model_codes = EXCLUDED.model_codes,
                            price_eur = EXCLUDED.price_eur,
                            last_sync_at = EXCLUDED.last_sync_at
                    """, batch_data)
                    conn.commit()
                    total_saved += len(batch_data)
                except Exception as e:
                    log_event("step1_error", f"Eroare salvare produse: {e}", status="error")
                    conn.rollback()
            
            # Salvează artcodes-urile normalizate în batch
            if batch_artcodes:
                try:
                    execute_values(cursor, """
                        INSERT INTO public.foneday_artcodes_normalized (foneday_sku, artcode)
                        VALUES %s
                        ON CONFLICT (foneday_sku, artcode) DO NOTHING
                    """, batch_artcodes)
                    conn.commit()
                    total_artcodes_normalized += len(batch_artcodes)
                except Exception as e:
                    log_event("step1_error", f"Eroare salvare artcodes: {e}", status="error")
                    conn.rollback()
            
            status_container.info(f"💾 Salvate {total_saved}/{len(products)} produse, {total_artcodes_normalized} artcodes...")
            progress_bar.progress(min(total_saved / len(products), 0.99))
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 1 complet: {total_saved} produse, {total_artcodes_normalized} artcodes normalizate"
        log_event("step1_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 1 FINALIZAT:**
        - 📦 **{total_saved} produse Foneday** salvate în `foneday_products`
        - 🔗 **{total_artcodes_normalized} artcodes** extrase și normalizate în `foneday_artcodes_normalized`
        - 💡 Artcode = SKU-ul tău din catalogul Foneday
        """)
        
        return total_saved
    except Exception as e:
        error_msg = f"Eroare PASUL 1: {e}"
        st.error(f"❌ {error_msg}")
        log_event("step1_error", error_msg, status="error")
        return 0
    finally:
        if conn:
            conn.close()

# ============ PASUL 2: Mapare SKU ============
def step2_map_sku_to_artcode():
    """PASUL 2: Mapare la nivel de PRODUS (UUID) - evită duplicate pentru sinonime"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step2_start", "PASUL 2: Începe mapare produse → Foneday", status="info")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0
        
        cursor = conn.cursor()
        
        status_container.info("📂 PASUL 2: Citesc produsele și SKU-urile din catalog...")
        
        # Citește TOATE SKU-urile cu product_id
        cursor.execute("""
            SELECT ps.sku, ps.product_id, ps.is_primary
            FROM product_sku ps
            WHERE ps.sku IS NOT NULL AND ps.sku != ''
        """)
        all_skus = cursor.fetchall()
        
        if not all_skus:
            st.warning("Nu există SKU-uri în catalogul tău")
            log_event("step2_warning", "Nu există SKU-uri de mapat", status="warning")
            cursor.close()
            return 0
        
        status_container.success(f"✅ Citite {len(all_skus)} SKU-uri din catalog")
        log_event("step2_process", f"Procesez {len(all_skus)} SKU-uri", status="info")
        progress_bar.progress(0.3)
        
        status_container.info("📂 Citesc artcode-urile din Foneday...")
        
        # Citește toate artcode-urile normalizate din Foneday
        cursor.execute("""
            SELECT foneday_sku, artcode 
            FROM public.foneday_artcodes_normalized
        """)
        all_artcodes = cursor.fetchall()
        
        if not all_artcodes:
            st.warning("Nu există artcode-uri Foneday. Rulează mai întâi PASUL 1!")
            log_event("step2_warning", "Nu există artcode-uri Foneday", status="warning")
            cursor.close()
            return 0
        
        status_container.success(f"✅ Citite {len(all_artcodes)} artcode-uri din Foneday")
        log_event("step2_process", f"Procesez {len(all_artcodes)} artcodes", status="info")
        progress_bar.progress(0.6)
        
        status_container.info("🔗 Mapare la nivel de PRODUS (UUID)...")
        
        # Creează dicționar: artcode -> lista de foneday_sku
        artcode_to_foneday = {}
        for foneday_sku, artcode in all_artcodes:
            if artcode not in artcode_to_foneday:
                artcode_to_foneday[artcode] = []
            artcode_to_foneday[artcode].append(foneday_sku)
        
        # Grupează SKU-uri pe PRODUS (product_id = UUID)
        products_dict = {}
        
        for sku, product_id, is_primary in all_skus:
            if product_id not in products_dict:
                products_dict[product_id] = {
                    "primary_sku": None,
                    "all_skus": [],
                    "matching_skus": []
                }
            
            products_dict[product_id]["all_skus"].append(sku)
            
            if is_primary:
                products_dict[product_id]["primary_sku"] = sku
            
            if sku in artcode_to_foneday:
                products_dict[product_id]["matching_skus"].append({
                    "sku": sku,
                    "is_primary": is_primary,
                    "foneday_skus": artcode_to_foneday[sku]
                })
        
        # Creează mapări la nivel de PRODUS
        # Folosește DICT cu cheia unică (my_sku, foneday_artcode) pentru a evita duplicate
        mappings_dict = {}
        products_mapped = 0
        
        for product_id, product_data in products_dict.items():
            matching_skus = product_data["matching_skus"]
            
            if not matching_skus:
                continue
            
            # Alege SKU-ul de folosit pentru mapare
            primary_match = next((m for m in matching_skus if m["is_primary"]), None)
            
            if primary_match:
                selected_sku = primary_match["sku"]
                selected_foneday_skus = primary_match["foneday_skus"]
            else:
                selected_sku = matching_skus[0]["sku"]
                selected_foneday_skus = matching_skus[0]["foneday_skus"]
            
            # IMPORTANT: Cheia unică în tabel este (my_sku, foneday_artcode)
            # Dacă același SKU match-uiește cu mai multe foneday_sku, alege PRIMUL
            key = (selected_sku, selected_sku)  # (my_sku, foneday_artcode)
            
            if key not in mappings_dict:
                # Ia primul foneday_sku din listă
                mappings_dict[key] = {
                    "foneday_sku": selected_foneday_skus[0],  # PRIMUL match
                    "product_id": product_id
                }
                products_mapped += 1
        
        # Convertește dict în listă de tuple pentru batch insert
        batch_mappings = []
        for (my_sku, foneday_artcode), data in mappings_dict.items():
            batch_mappings.append((
                my_sku,
                foneday_artcode,
                data["foneday_sku"],
                data["product_id"],
                100,
                datetime.now()
            ))
        
        status_container.success(f"""
        ✅ Mapare completă la nivel de PRODUS:
        - {products_mapped} produse UNICE mapate
        - {len(batch_mappings)} mapări create (fără duplicate)
        - {len(products_dict) - products_mapped} produse fără match în Foneday
        """)
        log_event("step2_process", f"{products_mapped} produse mapate", status="info")
        progress_bar.progress(0.8)
        
        if not batch_mappings:
            st.warning("⚠️ Nu s-au găsit match-uri între produsele tale și Foneday!")
            log_event("step2_warning", "Nu s-au găsit match-uri", status="warning")
            cursor.close()
            return 0
        
        status_container.info("💾 Salvez mapări în baza de date...")
        
        # Salvează mapările în batch-uri
        batch_size = 500
        total_saved = 0
        
        for i in range(0, len(batch_mappings), batch_size):
            batch = batch_mappings[i:i+batch_size]
            try:
                execute_values(cursor, """
                    INSERT INTO public.sku_artcode_mapping 
                    (my_sku, foneday_artcode, foneday_sku, product_id, mapping_score, last_verified_at)
                    VALUES %s
                    ON CONFLICT (my_sku, foneday_artcode) DO UPDATE SET
                        foneday_sku = EXCLUDED.foneday_sku,
                        product_id = EXCLUDED.product_id,
                        mapping_score = EXCLUDED.mapping_score,
                        last_verified_at = EXCLUDED.last_verified_at
                """, batch)
                conn.commit()
                total_saved += len(batch)
                status_container.info(f"💾 Salvate {total_saved}/{len(batch_mappings)} mapări...")
            except Exception as e:
                log_event("step2_error", f"Eroare salvare batch: {str(e)}", status="error")
                st.error(f"⚠️ Eroare salvare: {str(e)}")
                conn.rollback()
                continue
        
        # Numără total mapări și produse unice în DB
        cursor.execute("SELECT COUNT(*) FROM public.sku_artcode_mapping")
        total_mappings_db = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT product_id) FROM public.sku_artcode_mapping WHERE product_id IS NOT NULL")
        unique_products_db = cursor.fetchone()[0]
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 2 complet: {products_mapped} produse, {total_saved} mapări"
        log_event("step2_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 2 FINALIZAT - Mapare la nivel de PRODUS:**
        
        - 🎯 **{products_mapped} produse UNICE** au corespondent în Foneday
        - 🔗 **{total_saved} mapări** salvate cu succes
        - 📊 **{unique_products_db} produse unice** în baza de date
        - 💡 **Fără duplicate** - fiecare produs e mapat o singură dată
        
        **Detalii:**
        - Prioritate SKU primar când există match
        - Un produs = o singură mapare în tabel
        - Dacă un SKU match-uiește cu mai multe produse Foneday, se alege primul
        - Gata pentru PASUL 3 (verificare stoc zero)
        """)
        
        return unique_products_db
    except Exception as e:
        error_msg = f"Eroare PASUL 2: {e}"
        st.error(f"❌ {error_msg}")
        log_event("step2_error", error_msg, status="error")
        return 0
    finally:
        if conn:
            conn.close()




# ============ PASUL 3: Verifică stoc ============
def step3_check_stock_and_prices():
    """PASUL 3: Verifică disponibilitate Foneday pentru produsele cu stoc zero"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step3_start", "PASUL 3: Verificare disponibilitate Foneday", status="info")
    status_container.info("🔍 PASUL 3: Găsesc produse cu stoc zero în WooCommerce...")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0, 0
        
        cursor = conn.cursor()
        
        # Găsește produse cu stoc zero din WooCommerce
        cursor.execute("""
            SELECT sku, product_id, woo_product_id
            FROM v_woo_stock
            WHERE stock_quantity <= 0
        """)
        zero_stock_products = cursor.fetchall()
        
        if not zero_stock_products:
            status_container.success("✅ Nu există produse cu stoc zero în WooCommerce!")
            log_event("step3_complete", "Nu există produse cu stoc zero", status="success")
            cursor.close()
            return 0, 0
        
        status_container.info(f"📦 Găsite {len(zero_stock_products)} produse cu stoc zero")
        log_event("step3_process", f"Verificare {len(zero_stock_products)} produse", status="info")
        
        total_checked = 0
        total_available = 0
        products_without_mapping = 0
        
        for idx, (my_sku, product_id, woo_product_id) in enumerate(zero_stock_products):
            status_container.info(f"🔍 PASUL 3: Verific {idx+1}/{len(zero_stock_products)}: {my_sku}")
            progress_bar.progress((idx + 1) / len(zero_stock_products))
            
            # Găsește maparea către Foneday (din PASUL 2)
            cursor.execute("""
                SELECT foneday_sku FROM public.sku_artcode_mapping
                WHERE my_sku = %s
            """, (my_sku,))
            mappings = cursor.fetchall()
            
            if not mappings:
                products_without_mapping += 1
                continue
            
            # Verifică fiecare mapping (poate exista mai mult de unul)
            for (foneday_sku,) in mappings:
                if not foneday_sku:
                    continue
                
                # Verifică LIVE disponibilitate la Foneday prin API
                foneday_product = get_foneday_product_by_sku(foneday_sku)
                
                if foneday_product:
                    total_checked += 1
                    
                    # Dacă e în stoc la Foneday, salvează în inventar
                    if foneday_product.get("instock") == "Y":
                        total_available += 1
                        
                        try:
                            execute_values(cursor, """
                                INSERT INTO public.foneday_inventory 
                                (product_id, sku, foneday_sku, price_eur, instock, title, quality, last_checked_at)
                                VALUES %s
                                ON CONFLICT (sku, foneday_sku) DO UPDATE SET
                                    price_eur = EXCLUDED.price_eur,
                                    instock = EXCLUDED.instock,
                                    title = EXCLUDED.title,
                                    quality = EXCLUDED.quality,
                                    last_checked_at = EXCLUDED.last_checked_at
                            """, [(
                                product_id,
                                my_sku,
                                foneday_sku,
                                float(foneday_product.get("price", 0)),
                                True,
                                foneday_product.get("title"),
                                foneday_product.get("quality"),
                                datetime.now()
                            )])
                            conn.commit()
                        except Exception as e:
                            conn.rollback()
                            log_event("step3_error", f"Eroare salvare inventar {my_sku}: {e}", status="error")
                
                time.sleep(0.2)  # Rate limiting API
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 3: {total_checked} verificate, {total_available} disponibile, {products_without_mapping} fără mapping"
        log_event("step3_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 3 FINALIZAT:**
        - 🔍 **{total_checked} produse** verificate LIVE în API Foneday
        - ✅ **{total_available} produse** disponibile pentru comandă
        - ⚠️ **{products_without_mapping} produse** fără mapping (rulează PASUL 2 dacă lipsesc)
        - 💾 Produsele disponibile sunt salvate în `foneday_inventory`
        """)
        
        return total_checked, total_available
    except Exception as e:
        error_msg = f"Eroare PASUL 3: {e}"
        st.error(f"❌ {error_msg}")
        log_event("step3_error", error_msg, status="error")
        return 0, 0
    finally:
        if conn:
            conn.close()

# ============ PASUL 4: Adaugă în coș ============
def step4_add_to_cart():
    """PASUL 4: Calculează profitabilitate și adaugă produse în coșul Foneday"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step4_start", "PASUL 4: Adăugare în coș Foneday", status="info")
    status_container.info("🛒 PASUL 4: Verific produse profitabile...")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0, 0
        
        cursor = conn.cursor()
        
        # Găsește produse disponibile la Foneday (din PASUL 3)
        cursor.execute("""
            SELECT product_id, sku, foneday_sku, price_eur
            FROM public.foneday_inventory
            WHERE instock = TRUE
        """)
        available_products = cursor.fetchall()
        
        if not available_products:
            status_container.info("Nu există produse disponibile la Foneday. Rulează PASUL 3!")
            log_event("step4_complete", "Nu există produse disponibile", status="info")
            cursor.close()
            return 0, 0
        
        status_container.info(f"💰 Analizez profitabilitatea pentru {len(available_products)} produse...")
        log_event("step4_process", f"Procesez {len(available_products)} produse disponibile", status="info")
        
        added_to_cart = 0
        not_profitable = 0
        missing_price = 0
        
        for idx, (product_id, my_sku, foneday_sku, foneday_price) in enumerate(available_products):
            status_container.info(f"🛒 PASUL 4: Verific {idx+1}/{len(available_products)}: {my_sku}")
            progress_bar.progress((idx + 1) / len(available_products))
            
            # CONVERSIE EXPLICIT LA FLOAT
            try:
                foneday_price_float = float(foneday_price) if foneday_price else 0
            except (ValueError, TypeError):
                foneday_price_float = 0
            
            if foneday_price_float <= 0:
                missing_price += 1
                continue
            
            # Obține prețul de vânzare din WooCommerce
            cursor.execute("""
                SELECT regular_price FROM v_woo_prices WHERE sku = %s
            """, (my_sku,))
            price_result = cursor.fetchone()
            
            if not price_result or not price_result[0]:
                missing_price += 1
                continue
            
            # CONVERSIE EXPLICIT LA FLOAT
            try:
                woo_price_float = float(price_result[0])
            except (ValueError, TypeError):
                woo_price_float = 0
            
            if woo_price_float <= 0:
                missing_price += 1
                continue
            
            # Calculează profitabilitate
            if is_profitable(foneday_price_float, woo_price_float):
                profit_margin = calculate_profit_margin(foneday_price_float, woo_price_float)
                
                # Adaugă în coșul Foneday prin API (2 bucăți)
                cart_result = add_to_foneday_cart(foneday_sku, 2, f"Auto-import - {my_sku}")
                
                if cart_result:
                    try:
                        # Salvează în baza de date locală
                        cursor.execute("""
                            INSERT INTO public.foneday_cart 
                            (product_id, sku, foneday_sku, quantity, price_eur, woo_price_ron, 
                             profit_margin, is_profitable, status, note)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            product_id,
                            my_sku,
                            foneday_sku,
                            2,
                            foneday_price_float,
                            woo_price_float,
                            profit_margin,
                            True,
                            'added_to_cart',
                            f"Profit: {profit_margin}% - 2 buc"
                        ))
                        conn.commit()
                        added_to_cart += 1
                        log_event("step4_add", f"Adăugat: {my_sku} - Profit: {profit_margin}%", sku=my_sku, status="success")
                    except Exception as e:
                        conn.rollback()
                        log_event("step4_error", f"Eroare salvare coș {my_sku}: {e}", status="error")
                else:
                    log_event("step4_warning", f"Nu s-a putut adăuga în coș: {my_sku}", sku=my_sku, status="warning")
            else:
                not_profitable += 1
            
            time.sleep(0.1)  # Rate limiting API
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 4: {added_to_cart} adăugate, {not_profitable} neprofitabile, {missing_price} fără preț"
        log_event("step4_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 4 FINALIZAT:**
        - 🛒 **{added_to_cart} produse** adăugate în coșul Foneday (2 buc fiecare)
        - ❌ **{not_profitable} produse** neprofitabile (marjă < {(1-MIN_PROFIT_MARGIN)*100:.0f}%)
        - ⚠️ **{missing_price} produse** fără preț valid (actualizează din Pagina 1)
        - 💡 Parametri profit: EUR/RON = {EUR_RON_RATE}, TVA = {TVA_RATE}, Marjă min = {(1-MIN_PROFIT_MARGIN)*100:.0f}%
        """)
        
        return added_to_cart, not_profitable
    except Exception as e:
        error_msg = f"Eroare PASUL 4: {e}"
        st.error(f"❌ {error_msg}")
        log_event("step4_error", error_msg, status="error")
        
        # Debug traceback
        import traceback
        st.code(traceback.format_exc())
        
        return 0, 0
    finally:
        if conn:
            conn.close()


# ===== SIDEBAR =====
st.sidebar.title("📱 Comanda API Foneday")
st.sidebar.markdown("**Sistem Automat Import Produse**")
st.sidebar.markdown("---")

# Warning pentru prețuri/stocuri
st.sidebar.warning("""
⚠️ **IMPORTANT:**
Asigură-te că ai rulat **Pagina 1** pentru a actualiza prețurile și stocurile WooCommerce!
""")

page = st.sidebar.radio(
    "📋 Navigare",
    [
        "🏠 Dashboard",
        "🔄 Import Individual (Pași)",
        "💰 Oportunități Profit",
        "📊 Stocuri Critice",
        "🛒 Coș Foneday",
        "🗺️ Mapări",
        "📝 Log"
    ]
)

st.sidebar.markdown("---")
st.sidebar.caption(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if st.sidebar.button("🔄 Reîmprospătare"):
    st.cache_data.clear()
    st.rerun()

# ===== PAGINI =====

if page == "🏠 Dashboard":
    st.title("📊 Dashboard Principal")
    st.markdown("### 📈 Statistici Generale")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM v_woo_stock WHERE stock_quantity > 0")
                count = cursor.fetchone()[0]
                st.metric("✅ Cu Stoc", count)
                cursor.close()
                conn.close()
        except:
            st.metric("✅ Cu Stoc", "N/A")
    
    with col2:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM v_woo_stock WHERE stock_quantity <= 0")
                count = cursor.fetchone()[0]
                st.metric("❌ Stoc Zero", count)
                cursor.close()
                conn.close()
        except:
            st.metric("❌ Stoc Zero", "N/A")
    
    with col3:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM public.foneday_products")
                count = cursor.fetchone()[0]
                st.metric("🌐 Produse Foneday", count)
                cursor.close()
                conn.close()
        except:
            st.metric("🌐 Produse Foneday", "N/A")
    
    with col4:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM public.sku_artcode_mapping")
                count = cursor.fetchone()[0]
                st.metric("🗺️ Mapări SKU", count)
                cursor.close()
                conn.close()
        except:
            st.metric("🗺️ Mapări SKU", "N/A")
    
    with col5:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM public.foneday_cart WHERE status = 'added_to_cart'")
                count = cursor.fetchone()[0]
                st.metric("🛒 În Coș", count)
                cursor.close()
                conn.close()
        except:
            st.metric("🛒 În Coș", "N/A")
    
    st.markdown("---")
    st.markdown("### 🕐 Ultimele Sincronizări")
    
    try:
        conn = get_db_connection()
        if conn:
            df = pd.read_sql("""
                SELECT created_at, event_type, message, status
                FROM public.sync_logs
                ORDER BY created_at DESC
                LIMIT 10
            """, conn)
            
            if not df.empty:
                df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M:%S')
                st.dataframe(df, use_container_width=True, height=300)
            else:
                st.info("Nu există log-uri")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")

elif page == "🔄 Import Individual (Pași)":
    st.title("🔄 Import Individual - Alege Pașii")
    
    with st.expander("📚 **CITEȘTE MAI ÎNTÂI - Ce Face Fiecare Pas**", expanded=False):
        st.markdown(f"""
        ## 🔄 Fluxul Complet de Lucru
        
        ### **Pasul 1: 🌐 Import Catalog Foneday**
        **Ce face:**
        1. Accesează API Foneday: `GET /products`
        2. Descarcă **TOATE** produsele disponibile la Foneday (mii de produse)
        3. Salvează fiecare produs în `foneday_products` (SKU Foneday, preț, stoc, etc.)
        4. **NORMALIZARE ARTCODES**: Extrage câmpul `artcode` din fiecare produs
           - `artcode` = SKU-ul TĂU în catalogul Foneday
           - Poate fi: string simplu, array JSON, etc.
           - Fiecare artcode este salvat separat în `foneday_artcodes_normalized`
        
        **Când să rulezi:** **Săptămânal** (catalogul Foneday nu se schimbă zilnic)
        
        **Rezultat:**
        - Tabel `foneday_products`: catalog complet Foneday
        - Tabel `foneday_artcodes_normalized`: fiecare artcode pe rând (pentru mapare rapidă)
        
        ---
        
        ### **Pasul 2: 🗺️ Mapare SKU → Foneday**
        **Ce face:**
        1. Citește toate SKU-urile PRIMARE din catalogul tău (`v_product_sku` WHERE `is_primary = TRUE`)
        2. Citește toate artcodes din `foneday_artcodes_normalized`
        3. **COMPARĂ**: pentru fiecare SKU al tău, verifică dacă apare în artcodes Foneday
        4. Dacă găsește match → creează mapare în `sku_artcode_mapping`:
           - `my_sku` = SKU-ul tău
           - `foneday_artcode` = același cu `my_sku` (câmpul artcode din Foneday)
           - `foneday_sku` = SKU-ul produsului în catalogul Foneday
        
        **Când să rulezi:** După Pasul 1, sau când adaugi produse noi în catalog
        
        **Exemplu:**
        - Tu ai SKU: `ABC123`
        - Foneday are produs cu SKU=`FD-001` și artcode=`["ABC123", "ABC456"]`
        - Rezultat: mapare `ABC123` (tu) → `FD-001` (Foneday)
        
        **Rezultat:**
        - Tabel `sku_artcode_mapping`: legătura dintre SKU-urile tale și Foneday
        
        ---
        
        ### **Pasul 3: 🔍 Verificare Disponibilitate (Stoc Zero)**
        **Ce face:**
        1. Găsește produsele cu **stoc ZERO** în WooCommerce (`v_woo_stock` WHERE `stock_quantity <= 0`)
        2. Pentru fiecare produs:
           - Caută maparea în `sku_artcode_mapping` (din Pasul 2)
           - Verifică **LIVE** prin API Foneday dacă e disponibil: `GET /product/{{foneday_sku}}`
           - Dacă `instock = "Y"` → salvează în `foneday_inventory`
        3. Salvează prețul Foneday (EUR) pentru calculul profitului (Pasul 4)
        
        **Când să rulezi:** **ZILNIC** înainte de reaprovizionare
        
        **Atenție:** Verifică LIVE prin API = poate dura mult pentru multe produse!
        
        **Rezultat:**
        - Tabel `foneday_inventory`: produse cu stoc 0 la tine, dar disponibile la Foneday
        
        ---
        
        ### **Pasul 4: 🛒 Adăugare Automată în Coș**
        **Ce face:**
        1. Ia produsele din `foneday_inventory` (rezultatul Pasului 3)
        2. Pentru fiecare produs:
           - Obține prețul de vânzare din `v_woo_prices` (WooCommerce)
           - **CALCULEAZĂ PROFITABILITATE**:
             - Cost RON = Preț Foneday (EUR) × {EUR_RON_RATE}
             - Preț vânzare fără TVA = Preț WooCommerce / {TVA_RATE}
             - Marjă profit = (1 - Cost/Preț vânzare) × 100%
           - Dacă marjă ≥ {(1-MIN_PROFIT_MARGIN)*100:.0f}% → **PROFITABIL**
        3. Pentru produsele profitabile:
           - Adaugă **2 bucăți** în coșul Foneday prin API: `POST /shopping-cart-add-items`
           - Salvează în `foneday_cart` pentru tracking local
        
        **Când să rulezi:** După Pasul 3, când vrei să comanzi automat
        
        **Parametri profit actuali:**
        - Curs EUR/RON: **{EUR_RON_RATE}**
        - TVA: **{TVA_RATE}** (21%)
        - Marjă minimă: **{(1-MIN_PROFIT_MARGIN)*100:.0f}%**
        
        **Rezultat:**
        - Produsele sunt adăugate în coșul tău Foneday (verifică pe foneday.shop)
        - Tabel `foneday_cart`: tracking local al comenzilor
        
        ---
        
        ## 📊 Rezumat Flux
        
        ```
        PASUL 1: Import Foneday
                ↓
        foneday_products + foneday_artcodes_normalized
                ↓
        PASUL 2: Mapare SKU-uri
                ↓
        sku_artcode_mapping (my_sku ↔ foneday_sku)
                ↓
        PASUL 3: Verifică Stoc Zero
                ↓
        foneday_inventory (disponibile la Foneday)
                ↓
        PASUL 4: Calcul Profit + Adaugă în Coș
                ↓
        foneday_cart + Coș Foneday (pe site)
        ```
        """)
    
    st.markdown("---")
    
    tab1, tab2, tab3, tab4 = st.tabs(["1️⃣ Foneday", "2️⃣ Mapare", "3️⃣ Stoc Zero", "4️⃣ Coș"])
    
    with tab1:
        st.markdown("## 🌐 PASUL 1: Import Catalog Foneday")
        st.info("Descarcă toate produsele Foneday și normalizează artcodes pentru mapare")
        if st.button("▶️ Rulează Pasul 1", type="primary", use_container_width=True):
            step1_import_foneday_all_products()
    
    with tab2:
        st.markdown("## 🗺️ PASUL 2: Mapare SKU-uri")
        st.info("Compară SKU-urile tale cu artcodes Foneday pentru a crea legături")
        if st.button("▶️ Rulează Pasul 2", type="primary", use_container_width=True):
            step2_map_sku_to_artcode()
    
    with tab3:
        st.markdown("## 🔍 PASUL 3: Verificare Stoc Zero")
        st.info("Verifică LIVE în API Foneday care produse cu stoc 0 sunt disponibile")
        if st.button("▶️ Rulează Pasul 3", type="primary", use_container_width=True):
            step3_check_stock_and_prices()
    
    with tab4:
        st.markdown("## 🛒 PASUL 4: Adăugare Automată în Coș")
        st.info("Calculează profitabilitatea și adaugă produsele profitabile în coșul Foneday (2 buc)")
        if st.button("▶️ Rulează Pasul 4", type="primary", use_container_width=True):
            step4_add_to_cart()

elif page == "💰 Oportunități Profit":
    st.title("💰 Oportunități de Profit")
    st.markdown("""
    Caută produse care **AI ÎN STOC** și poți să le cumperi mai ieftin de la Foneday.
    Util pentru a identifica oportunități de revânzare cu marjă mai mare.
    """)
    
    min_profit = st.slider("Marjă minimă de profit (%)", 0, 100, 20, 5)
    
    if st.button("🔍 Caută Oportunități", type="primary", use_container_width=True):
        opportunities = find_high_profit_opportunities(min_profit)
        
        if opportunities:
            df = pd.DataFrame(opportunities)
            st.success(f"✅ Găsite {len(opportunities)} oportunități!")
            st.dataframe(df, use_container_width=True)
        else:
            st.info("Nu s-au găsit oportunități cu aceste criterii")

elif page == "📊 Stocuri Critice":
    st.title("📊 Stocuri Critice (Stoc Zero)")
    st.markdown("Produse care au stoc zero în WooCommerce")
    
    try:
        conn = get_db_connection()
        if conn:
            df = pd.read_sql("""
                SELECT 
                    ws.sku,
                    p.name as product_name,
                    ws.stock_quantity,
                    ws.last_sync
                FROM v_woo_stock ws
                LEFT JOIN product_sku ps ON ws.sku = ps.sku AND ps.is_primary = TRUE
                LEFT JOIN product p ON ps.product_id = p.id
                WHERE ws.stock_quantity <= 0
                ORDER BY ws.last_sync DESC
                LIMIT 100
            """, conn)
            
            if not df.empty:
                st.info(f"📦 Găsite {len(df)} produse cu stoc zero (primele 100)")
                st.dataframe(df, use_container_width=True)
            else:
                st.success("✅ Nu există produse cu stoc zero!")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")

elif page == "🛒 Coș Foneday":
    st.title("🛒 Coș de Cumpărături Foneday")
    st.markdown("Produse adăugate automat în coșul Foneday (din Pasul 4)")
    
    try:
        conn = get_db_connection()
        if conn:
            df = pd.read_sql("""
                SELECT 
                    fc.sku,
                    p.name as product_name,
                    fc.foneday_sku,
                    fc.quantity,
                    fc.price_eur,
                    fc.woo_price_ron,
                    fc.profit_margin,
                    fc.note,
                    fc.created_at
                FROM public.foneday_cart fc
                LEFT JOIN product_sku ps ON fc.sku = ps.sku AND ps.is_primary = TRUE
                LEFT JOIN product p ON ps.product_id = p.id
                WHERE fc.status = 'added_to_cart'
                ORDER BY fc.created_at DESC
            """, conn)
            
            if not df.empty:
                st.info(f"🛒 Găsite {len(df)} produse în coș")
                st.dataframe(df, use_container_width=True)
                
                col1, col2 = st.columns(2)
                with col1:
                    total_eur = (df['price_eur'] * df['quantity']).sum()
                    st.metric("💶 Total EUR", f"€{total_eur:.2f}")
                with col2:
                    avg_profit = df['profit_margin'].mean()
                    st.metric("📊 Marjă Medie", f"{avg_profit:.2f}%")
            else:
                st.info("Coșul este gol. Rulează PASUL 4 pentru a adăuga produse profitabile.")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")

elif page == "🗺️ Mapări":
    st.title("🗺️ Mapări SKU")
    st.markdown("Legături între SKU-urile tale și produsele Foneday (rezultatul Pasului 2)")
    
    try:
        conn = get_db_connection()
        if conn:
            df = pd.read_sql("""
                SELECT 
                    m.my_sku,
                    p.name as product_name,
                    m.foneday_sku,
                    m.mapping_score,
                    m.last_verified_at
                FROM public.sku_artcode_mapping m
                LEFT JOIN product_sku ps ON m.my_sku = ps.sku AND ps.is_primary = TRUE
                LEFT JOIN product p ON ps.product_id = p.id
                ORDER BY m.last_verified_at DESC
                LIMIT 100
            """, conn)
            
            if not df.empty:
                st.info(f"🗺️ Afișez ultimele 100 mapări")
                st.dataframe(df, use_container_width=True)
            else:
                st.info("Nu există mapări. Rulează PASUL 2 pentru a crea mapări.")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")

elif page == "📝 Log":
    st.title("📝 Log Evenimente")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        filter_status = st.multiselect(
            "Filtrează după status",
            ["info", "success", "warning", "error"],
            default=["info", "success", "warning", "error"]
        )
    with col2:
        limit = st.selectbox("Număr înregistrări", [50, 100, 200, 500], index=1)
    
    try:
        conn = get_db_connection()
        if conn:
            placeholders = ','.join(['%s'] * len(filter_status))
            query = f"""
                SELECT created_at, event_type, sku, message, status
                FROM public.sync_logs
                WHERE status IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT %s
            """
            
            df = pd.read_sql(query, conn, params=tuple(filter_status) + (limit,))
            
            if not df.empty:
                df['created_at'] = pd.to_datetime(df['created_at']).dt.strftime('%Y-%m-%d %H:%M:%S')
                st.dataframe(df, use_container_width=True, height=600)
            else:
                st.info("Nu există log-uri")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")
