import streamlit as st
import psycopg2
from psycopg2.extras import execute_values
import pandas as pd
from datetime import datetime, timedelta
import requests
import time
import json

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
    cost_ron = foneday_price_eur * EUR_RON_RATE
    selling_price_without_vat = woo_price_ron / TVA_RATE
    ratio = cost_ron / selling_price_without_vat
    profit_margin = (1 - ratio) * 100
    return round(profit_margin, 2)

def is_profitable(foneday_price_eur: float, woo_price_ron: float) -> bool:
    """Verifică dacă produsul e profitabil"""
    cost_ron = foneday_price_eur * EUR_RON_RATE
    selling_price_without_vat = woo_price_ron / TVA_RATE
    ratio = cost_ron / selling_price_without_vat
    return ratio < MIN_PROFIT_MARGIN

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
    """PASUL 2: Mapare SKU-uri - compară TOATE SKU-urile tale (inclusiv sinonime) cu artcodes Foneday"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step2_start", "PASUL 2: Începe mapare SKU → Foneday", status="info")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0
        
        cursor = conn.cursor()
        
        status_container.info("📂 PASUL 2: Citesc TOATE SKU-urile din catalogul tău (inclusiv sinonime)...")
        
        # Citește TOATE SKU-urile din product_sku (nu doar primare!)
        cursor.execute("""
            SELECT ps.sku, ps.product_id, ps.is_primary
            FROM product_sku ps
            WHERE ps.sku IS NOT NULL AND ps.sku != ''
        """)
        all_my_skus = cursor.fetchall()
        
        if not all_my_skus:
            st.warning("Nu există SKU-uri în catalogul tău")
            log_event("step2_warning", "Nu există SKU-uri de mapat", status="warning")
            cursor.close()
            return 0
        
        # Numără câte sunt primare vs secundare
        primary_count = sum(1 for _, _, is_prim in all_my_skus if is_prim)
        secondary_count = len(all_my_skus) - primary_count
        
        status_container.success(f"✅ Total {len(all_my_skus)} SKU-uri: {primary_count} primare + {secondary_count} sinonime")
        log_event("step2_process", f"Procesez {len(all_my_skus)} SKU-uri", status="info")
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
        
        status_container.success(f"✅ Total {len(all_artcodes)} artcode-uri din Foneday")
        log_event("step2_process", f"Procesez {len(all_artcodes)} artcodes", status="info")
        progress_bar.progress(0.6)
        
        status_container.info("🔗 Compar și creez mapări...")
        
        # Creează dicționar pentru mapare rapidă: artcode -> lista de foneday_sku
        artcode_dict = {}
        for foneday_sku, artcode in all_artcodes:
            if artcode not in artcode_dict:
                artcode_dict[artcode] = []
            artcode_dict[artcode].append(foneday_sku)
        
        # Compară TOATE SKU-urile tale (inclusiv sinonime) cu artcodes Foneday
        batch_mappings = []
        matches_count = 0
        primary_matches = 0
        secondary_matches = 0
        
        for my_sku, product_id, is_primary in all_my_skus:
            # Verifică dacă SKU-ul tău apare în artcodes Foneday
            if my_sku in artcode_dict:
                matches_count += 1
                if is_primary:
                    primary_matches += 1
                else:
                    secondary_matches += 1
                
                # Poate exista mai mult de un produs Foneday cu același artcode
                for foneday_sku in artcode_dict[my_sku]:
                    batch_mappings.append((
                        my_sku,           # SKU-ul tău (poate fi primar sau sinonim)
                        my_sku,           # foneday_artcode (același cu SKU-ul tău)
                        foneday_sku,      # SKU-ul produsului în Foneday
                        product_id,       # ID-ul produsului tău (UUID)
                        100,              # mapping_score (100 = match exact)
                        datetime.now()
                    ))
        
        status_container.success(f"""
        ✅ Găsite {matches_count} SKU-uri cu match în Foneday:
        - {primary_matches} SKU-uri primare
        - {secondary_matches} sinonime
        - {len(batch_mappings)} mapări totale (cu duplicate Foneday)
        """)
        log_event("step2_process", f"Create {len(batch_mappings)} mapări din {len(all_my_skus)} SKU-uri", status="info")
        progress_bar.progress(0.8)
        
        if not batch_mappings:
            st.warning("⚠️ Nu s-au găsit match-uri între SKU-urile tale și Foneday!")
            st.info("""
            **Posibile cauze:**
            - SKU-urile tale nu apar în câmpul `artcode` din produsele Foneday
            - Rulează PASUL 1 pentru a actualiza catalogul Foneday
            """)
            log_event("step2_warning", "Nu s-au găsit match-uri", status="warning")
            cursor.close()
            return 0
        
        status_container.info("💾 Salvez mapări în baza de date...")
        
        # Șterge mapările vechi (optional - sau comentează dacă vrei să păstrezi istoric)
        # cursor.execute("DELETE FROM public.sku_artcode_mapping")
        # conn.commit()
        
        # Salvează mapările în batch-uri
        batch_size = 500
        total_saved = 0
        errors = 0
        
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
                errors += 1
                log_event("step2_error", f"Eroare salvare batch {i//batch_size + 1}: {str(e)}", status="error")
                st.error(f"⚠️ Eroare salvare batch {i//batch_size + 1}: {str(e)}")
                conn.rollback()
                
                # Încearcă să salveze una câte una pentru debugging
                if errors <= 3:  # Încearcă maximum 3 batch-uri cu erori
                    st.warning(f"Încerc salvare individuală pentru batch-ul {i//batch_size + 1}...")
                    for mapping in batch:
                        try:
                            cursor.execute("""
                                INSERT INTO public.sku_artcode_mapping 
                                (my_sku, foneday_artcode, foneday_sku, product_id, mapping_score, last_verified_at)
                                VALUES (%s, %s, %s, %s, %s, %s)
                                ON CONFLICT (my_sku, foneday_artcode) DO UPDATE SET
                                    foneday_sku = EXCLUDED.foneday_sku,
                                    product_id = EXCLUDED.product_id,
                                    mapping_score = EXCLUDED.mapping_score,
                                    last_verified_at = EXCLUDED.last_verified_at
                            """, mapping)
                            conn.commit()
                            total_saved += 1
                        except Exception as e2:
                            st.error(f"Eroare SKU {mapping[0]}: {str(e2)}")
                            conn.rollback()
                            continue
        
        # Numără total mapări în DB
        cursor.execute("SELECT COUNT(*) FROM public.sku_artcode_mapping")
        total_in_db = cursor.fetchone()[0]
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 2 complet: {total_saved} mapări salvate, {total_in_db} total în DB"
        log_event("step2_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 2 FINALIZAT:**
        - 🔗 **{matches_count} SKU-uri** din catalogul tău au corespondent în Foneday
          - {primary_matches} SKU-uri primare
          - {secondary_matches} sinonime
        - 💾 **{total_saved} mapări** procesate și salvate
        - 📊 **{total_in_db} mapări** totale în baza de date
        - 💡 Acum poți căuta produse disponibile în Foneday (PASUL 3)
        """)
        
        if errors > 0:
            st.warning(f"⚠️ Au fost {errors} erori la salvare. Verifică log-urile pentru detalii.")
        
        return total_in_db
    except Exception as e:
        error_msg = f"Eroare PASUL 2: {e}"
        st.error(f"❌ {error_msg}")
        log_event("step2_error", error_msg, status="error")
        
        # Afișează traceback pentru debugging
        import traceback
        st.code(traceback.format_exc())
        
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
            
            # Obține prețul de vânzare din WooCommerce
            cursor.execute("""
                SELECT regular_price FROM v_woo_prices WHERE sku = %s
            """, (my_sku,))
            price_result = cursor.fetchone()
            
            if not price_result or not price_result[0]:
                missing_price += 1
                continue
            
            woo_price = float(price_result[0])
            
            if woo_price <= 0 or foneday_price <= 0:
                continue
            
            # Calculează profitabilitate
            # Cost (RON) = Preț Foneday (EUR) × Curs
            # Preț vânzare fără TVA = Preț WooCommerce / 1.21
            # Profitabil dacă: Cost / Preț vânzare < MIN_PROFIT_MARGIN (0.88 = 12% profit)
            
            if is_profitable(foneday_price, woo_price):
                profit_margin = calculate_profit_margin(foneday_price, woo_price)
                
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
                            foneday_price,
                            woo_price,
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
        - ❌ **{not_profitable} produse** neprofitabile (marjă < 12%)
        - ⚠️ **{missing_price} produse** fără preț WooCommerce (actualizează din Pagina 1)
        - 💡 Parametri profit: EUR/RON = {EUR_RON_RATE}, TVA = {TVA_RATE}, Marjă min = {(1-MIN_PROFIT_MARGIN)*100:.0f}%
        """)
        
        return added_to_cart, not_profitable
    except Exception as e:
        error_msg = f"Eroare PASUL 4: {e}"
        st.error(f"❌ {error_msg}")
        log_event("step4_error", error_msg, status="error")
        return 0, 0
    finally:
        if conn:
            conn.close()

# ============ FUNCȚIE: Căutare Oportunități Profit ============
def find_high_profit_opportunities(min_profit_percent: float):
    """Caută produse cu marjă de profit mare (DOAR cu stoc ≥ 1)"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    status_container.info("💰 Caut oportunități de profit mare (produse CU stoc)...")
    log_event("opportunities_start", f"Căutare oportunități profit ≥{min_profit_percent}%", status="info")
    
    opportunities = []
    conn = None
    
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return []
        
        cursor = conn.cursor()
        
        # Găsește toate mapările
        cursor.execute("""
            SELECT m.my_sku, m.foneday_sku, m.product_id
            FROM public.sku_artcode_mapping m
        """)
        mappings = cursor.fetchall()
        
        if not mappings:
            st.warning("Nu există mapări. Rulează mai întâi PASUL 2.")
            cursor.close()
            return []
        
        total_mappings = len(mappings)
        
        for idx, (my_sku, foneday_sku, product_id) in enumerate(mappings):
            status_container.info(f"💰 Verific {idx+1}/{total_mappings}: {my_sku}")
            progress_bar.progress((idx + 1) / total_mappings)
            
            # Verifică stoc WooCommerce
            cursor.execute("""
                SELECT stock_quantity FROM v_woo_stock WHERE sku = %s
            """, (my_sku,))
            stock_result = cursor.fetchone()
            
            if not stock_result:
                continue
            
            current_stock = stock_result[0] if stock_result[0] is not None else 0
            
            # Doar produse CU stoc (nu căutăm reaprovizionare, căutăm oportunități)
            if current_stock <= 0:
                continue
            
            # Verifică preț WooCommerce
            cursor.execute("""
                SELECT regular_price FROM v_woo_prices WHERE sku = %s
            """, (my_sku,))
            price_result = cursor.fetchone()
            
            if not price_result:
                continue
            
            woo_price = float(price_result[0]) if price_result[0] else 0
            
            if woo_price <= 0:
                continue
            
            # Verifică disponibilitate și preț Foneday LIVE
            foneday_product = get_foneday_product_by_sku(foneday_sku)
            
            if foneday_product and foneday_product.get("instock") == "Y":
                foneday_price = float(foneday_product.get("price", 0))
                
                if foneday_price > 0:
                    profit_margin = calculate_profit_margin(foneday_price, woo_price)
                    
                    # Dacă marja >= marja cerută
                    if profit_margin >= min_profit_percent:
                        # Obține nume produs
                        cursor.execute("""
                            SELECT name FROM v_product WHERE id = %s
                        """, (product_id,))
                        product_result = cursor.fetchone()
                        product_name = product_result[0] if product_result else my_sku
                        
                        opportunities.append({
                            "sku": my_sku,
                            "product_name": product_name,
                            "foneday_sku": foneday_sku,
                            "woo_price_ron": woo_price,
                            "foneday_price_eur": foneday_price,
                            "profit_margin": profit_margin,
                            "current_stock": current_stock,
                            "foneday_title": foneday_product.get("title"),
                            "quality": foneday_product.get("quality")
                        })
                        
                        log_event("opportunity_found", 
                                f"Oportunitate: {my_sku} - Stoc: {current_stock} - Profit: {profit_margin}%", 
                                sku=my_sku, status="success")
            
            if idx % 10 == 0:
                time.sleep(0.2)
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        log_event("opportunities_complete", 
                f"Găsite {len(opportunities)} oportunități cu profit ≥{min_profit_percent}%", 
                status="success")
        
        return opportunities
    except Exception as e:
        st.error(f"❌ Eroare căutare oportunități: {e}")
        log_event("opportunities_error", f"Eroare: {e}", status="error")
        return []
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
