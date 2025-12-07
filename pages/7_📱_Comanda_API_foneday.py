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
    """Conexiune la PostgreSQL (fără cache pentru a evita 'connection closed')"""
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
                    
                    batch_data.append((
                        foneday_sku,
                        json.dumps(artcode_raw) if isinstance(artcode_raw, (list, dict)) else str(artcode_raw) if artcode_raw else None,
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
                    
                    # Normalizează artcodes
                    if artcode_raw:
                        artcodes_list = []
                        if isinstance(artcode_raw, str):
                            try:
                                artcodes_list = json.loads(artcode_raw)
                            except:
                                artcodes_list = [artcode_raw.strip()]
                        elif isinstance(artcode_raw, list):
                            artcodes_list = artcode_raw
                        else:
                            artcodes_list = [str(artcode_raw)]
                        
                        for artcode_value in artcodes_list:
                            artcode_clean = str(artcode_value).strip().strip('"').strip("'")
                            if artcode_clean:
                                batch_artcodes.append((foneday_sku, artcode_clean))
                except Exception as e:
                    log_event("step1_error", f"Eroare procesare produs Foneday: {e}", status="error")
                    continue
            
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
            progress_bar.progress(total_saved / len(products))
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 1 complet: {total_saved} produse, {total_artcodes_normalized} artcodes normalizate"
        log_event("step1_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 1 FINALIZAT:**
        - 📦 {total_saved} produse Foneday salvate
        - 🔗 {total_artcodes_normalized} artcodes normalizate
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
    """PASUL 2: Mapare SKU-uri optimizată"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step2_start", "PASUL 2: Începe mapare SKU → artcode", status="info")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0
        
        cursor = conn.cursor()
        
        status_container.info("📂 PASUL 2: Citesc toate SKU-urile din catalog...")
        
        # Citește toate SKU-urile primare
        cursor.execute("""
            SELECT sku, product_id 
            FROM v_product_sku 
            WHERE is_primary = TRUE
        """)
        all_my_skus = cursor.fetchall()
        
        if not all_my_skus:
            st.warning("Nu există SKU-uri de mapat")
            log_event("step2_warning", "Nu există SKU-uri de mapat", status="warning")
            cursor.close()
            return 0
        
        status_container.success(f"✅ Total {len(all_my_skus)} SKU-uri în catalog")
        log_event("step2_process", f"Procesez {len(all_my_skus)} SKU-uri", status="info")
        progress_bar.progress(0.3)
        
        status_container.info("📂 Citesc toate artcode-urile Foneday...")
        
        # Citește toate artcode-urile
        cursor.execute("""
            SELECT foneday_sku, artcode 
            FROM public.foneday_artcodes_normalized
        """)
        all_artcodes = cursor.fetchall()
        
        if not all_artcodes:
            st.warning("Nu există artcode-uri Foneday")
            log_event("step2_warning", "Nu există artcode-uri Foneday", status="warning")
            cursor.close()
            return 0
        
        status_container.success(f"✅ Total {len(all_artcodes)} artcode-uri Foneday")
        log_event("step2_process", f"Procesez {len(all_artcodes)} artcodes", status="info")
        progress_bar.progress(0.6)
        
        status_container.info("🔗 Creez mapări în memorie...")
        
        # Creează dicționar pentru mapare rapidă
        artcode_dict = {}
        for foneday_sku, artcode in all_artcodes:
            if artcode not in artcode_dict:
                artcode_dict[artcode] = []
            artcode_dict[artcode].append(foneday_sku)
        
        batch_mappings = []
        for my_sku, product_id in all_my_skus:
            if my_sku in artcode_dict:
                for foneday_sku in artcode_dict[my_sku]:
                    batch_mappings.append((
                        my_sku,
                        my_sku,  # foneday_artcode = my_sku
                        foneday_sku,
                        product_id,
                        100,  # mapping_score
                        datetime.now()
                    ))
        
        status_container.success(f"✅ Create {len(batch_mappings)} mapări în memorie")
        log_event("step2_process", f"Create {len(batch_mappings)} mapări", status="info")
        progress_bar.progress(0.8)
        
        if not batch_mappings:
            st.warning("Nu s-au găsit match-uri între SKU-uri și Foneday")
            log_event("step2_warning", "Nu s-au găsit match-uri", status="warning")
            cursor.close()
            return 0
        
        status_container.info("💾 Salvez mapări...")
        
        # Salvează batch-uri de mapări
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
                log_event("step2_error", f"Eroare salvare mapări: {e}", status="error")
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
        - 🔗 {total_saved} mapări procesate
        - 📊 {total_in_db} mapări totale în DB
        """)
        
        return total_in_db
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
    """PASUL 3: Verifică stoc și prețuri - produse cu stoc zero"""
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    log_event("step3_start", "PASUL 3: Verificare stoc și prețuri Foneday", status="info")
    status_container.info("🔍 PASUL 3: Găsesc produse cu stoc zero...")
    
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            st.error("❌ Nu pot conecta la baza de date")
            return 0, 0
        
        cursor = conn.cursor()
        
        # Găsește produse cu stoc zero
        cursor.execute("""
            SELECT sku, product_id, woo_product_id
            FROM v_woo_stock
            WHERE stock_quantity <= 0
        """)
        zero_stock_products = cursor.fetchall()
        
        if not zero_stock_products:
            status_container.success("✅ Nu există produse cu stoc zero!")
            log_event("step3_complete", "Nu există produse cu stoc zero", status="success")
            cursor.close()
            return 0, 0
        
        log_event("step3_process", f"Verificare {len(zero_stock_products)} produse cu stoc zero", status="info")
        
        total_checked = 0
        total_available = 0
        
        for idx, (my_sku, product_id, woo_product_id) in enumerate(zero_stock_products):
            status_container.info(f"🔍 PASUL 3: Verific {idx+1}/{len(zero_stock_products)}: {my_sku}")
            progress_bar.progress((idx + 1) / len(zero_stock_products))
            
            # Găsește maparea Foneday
            cursor.execute("""
                SELECT foneday_sku FROM public.sku_artcode_mapping
                WHERE my_sku = %s
            """, (my_sku,))
            mappings = cursor.fetchall()
            
            if not mappings:
                continue
            
            for (foneday_sku,) in mappings:
                if not foneday_sku:
                    continue
                
                # Verifică disponibilitate la Foneday
                foneday_product = get_foneday_product_by_sku(foneday_sku)
                
                if foneday_product:
                    total_checked += 1
                    
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
                        except:
                            conn.rollback()
                            pass
                
                time.sleep(0.2)
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 3: {total_checked} verificate, {total_available} disponibile"
        log_event("step3_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 3 FINALIZAT:**
        - 🔍 {total_checked} produse verificate la Foneday
        - ✅ {total_available} disponibile pentru comandă
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
    """PASUL 4: Adaugă în coș Foneday produsele profitabile (2 bucăți)"""
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
        
        # Găsește produse disponibile la Foneday
        cursor.execute("""
            SELECT product_id, sku, foneday_sku, price_eur
            FROM public.foneday_inventory
            WHERE instock = TRUE
        """)
        available_products = cursor.fetchall()
        
        if not available_products:
            status_container.info("Nu există produse disponibile la Foneday")
            log_event("step4_complete", "Nu există produse disponibile", status="info")
            cursor.close()
            return 0, 0
        
        log_event("step4_process", f"Procesez {len(available_products)} produse disponibile", status="info")
        
        added_to_cart = 0
        not_profitable = 0
        
        for idx, (product_id, my_sku, foneday_sku, foneday_price) in enumerate(available_products):
            status_container.info(f"🛒 PASUL 4: Verific {idx+1}/{len(available_products)}: {my_sku}")
            progress_bar.progress((idx + 1) / len(available_products))
            
            # Obține prețul WooCommerce
            cursor.execute("""
                SELECT regular_price FROM v_woo_prices WHERE sku = %s
            """, (my_sku,))
            price_result = cursor.fetchone()
            
            if not price_result:
                continue
            
            woo_price = float(price_result[0]) if price_result[0] else 0
            
            if woo_price <= 0 or foneday_price <= 0:
                continue
            
            # Verifică profitabilitate
            if is_profitable(foneday_price, woo_price):
                profit_margin = calculate_profit_margin(foneday_price, woo_price)
                
                # Adaugă în coș Foneday
                cart_result = add_to_foneday_cart(foneday_sku, 2, f"Auto-import - {my_sku}")
                
                if cart_result:
                    try:
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
                    except:
                        conn.rollback()
                        pass
            else:
                not_profitable += 1
            
            time.sleep(0.1)
        
        cursor.close()
        
        progress_bar.progress(1.0)
        status_container.empty()
        
        success_msg = f"PASUL 4 complet: {added_to_cart} adăugate, {not_profitable} neprofitabile"
        log_event("step4_complete", success_msg, status="success")
        
        st.success(f"""
        ✅ **PASUL 4 FINALIZAT:**
        - 🛒 {added_to_cart} produse adăugate în coș
        - ❌ {not_profitable} produse neprofitabile (excluse)
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
    
    status_container.info("💰 Caut oportunități de profit mare (DOAR produse cu stoc)...")
    log_event("opportunities_start", f"Căutare oportunități profit ≥{min_profit_percent}% (stoc ≥1)", status="info")
    
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
            
            # Verifică stoc
            cursor.execute("""
                SELECT stock_quantity FROM v_woo_stock WHERE sku = %s
            """, (my_sku,))
            stock_result = cursor.fetchone()
            
            if not stock_result:
                continue
            
            current_stock = stock_result[0] if stock_result[0] is not None else 0
            
            # Doar produse cu stoc
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
            
            # Verifică disponibilitate Foneday
            foneday_product = get_foneday_product_by_sku(foneday_sku)
            
            if foneday_product and foneday_product.get("instock") == "Y":
                foneday_price = float(foneday_product.get("price", 0))
                
                if foneday_price > 0:
                    profit_margin = calculate_profit_margin(foneday_price, woo_price)
                    
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
                f"Găsite {len(opportunities)} oportunități (stoc ≥1) cu profit ≥{min_profit_percent}%", 
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
Asigură-te că ai rulat **Pagina 1** (Produse) pentru a actualiza prețurile și stocurile WooCommerce!
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
        st.markdown("""
        ### **Pasul 1: 🌐 Import Complet Catalog Foneday**
        **Ce face:**
        - Accesează `GET /products` din API Foneday
        - Descarcă **TOATE produsele** disponibile
        - Salvează în `foneday_products` și `foneday_artcodes_normalized`
        
        **Când:** **Săptămânal** (catalogul nu se schimbă zilnic)
        
        ---
        
        ### **Pasul 2: 🗺️ Mapare SKU-uri**
        **Ce face:**
        - Ia fiecare SKU din catalogul tău
        - Caută în Foneday unde `artcode` = SKU-ul tău
        - Creează legătura în `sku_artcode_mapping`
        
        **Când:** După Pasul 1, sau când adaugi produse noi
        
        ---
        
        ### **Pasul 3: 🔍 Verificare Stoc (Produse cu stoc zero)**
        **Ce face:**
        - Găsește produsele tale cu stoc zero (din woo_stoc)
        - Verifică prin API Foneday dacă sunt disponibile
        - Salvează în `foneday_inventory`
        
        **Când:** **ZILNIC** pentru reaprovizionare
        
        ---
        
        ### **Pasul 4: 🛒 Adăugare Automată în Coș**
        **Ce face:**
        - Ia produsele disponibile la Foneday
        - Calculează marja de profit
        - Dacă profitabil → adaugă 2 bucăți în coș
        
        **Când:** După Pasul 3, când vrei să comanzi automat
        """)
    
    st.markdown("---")
    
    tab1, tab2, tab3, tab4 = st.tabs(["1️⃣ Foneday", "2️⃣ Mapare", "3️⃣ Stoc", "4️⃣ Coș"])
    
    with tab1:
        st.markdown("## 🌐 PASUL 1: Import Catalog Foneday")
        if st.button("▶️ Rulează Pasul 1", type="primary", use_container_width=True):
            step1_import_foneday_all_products()
    
    with tab2:
        st.markdown("## 🗺️ PASUL 2: Mapare SKU-uri")
        if st.button("▶️ Rulează Pasul 2", type="primary", use_container_width=True):
            step2_map_sku_to_artcode()
    
    with tab3:
        st.markdown("## 🔍 PASUL 3: Verificare Stoc")
        if st.button("▶️ Rulează Pasul 3", type="primary", use_container_width=True):
            step3_check_stock_and_prices()
    
    with tab4:
        st.markdown("## 🛒 PASUL 4: Adăugare în Coș")
        if st.button("▶️ Rulează Pasul 4", type="primary", use_container_width=True):
            step4_add_to_cart()

elif page == "💰 Oportunități Profit":
    st.title("💰 Oportunități de Profit")
    
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
    st.title("📊 Stocuri Critice")
    
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
                st.info(f"📦 Găsite {len(df)} produse cu stoc zero")
                st.dataframe(df, use_container_width=True)
            else:
                st.success("✅ Nu există produse cu stoc zero!")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")

elif page == "🛒 Coș Foneday":
    st.title("🛒 Coș de Cumpărături Foneday")
    
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
                st.info("Coșul este gol")
            
            conn.close()
    except Exception as e:
        st.error(f"Eroare: {e}")

elif page == "🗺️ Mapări":
    st.title("🗺️ Mapări SKU")
    
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
                st.info("Nu există mapări. Rulează PASUL 2.")
            
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
