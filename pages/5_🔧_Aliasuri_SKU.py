

# pages/5_🔧_Aliasuri_SKU.py
import re
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from sidebar import render_sidebar
from auth_simple import check_password

# =========================
#   CONFIG
# =========================
st.set_page_config(page_title="Admin aliasuri SKU", layout="wide")
st.title("🔧 Admin aliasuri SKU")

# AUTENTIFICARE
if not check_password():
    st.stop()

# SIDEBAR
render_sidebar()
# =========================
#   POSTGRESQL CONNECTION
# =========================
@st.cache_resource
def get_pg_connection_string():
    """Obține connection string-ul PostgreSQL din secrets"""
    try:
        return st.secrets["connections"]["postgresql"]["url"]
    except KeyError:
        st.error("❌ Credențiale PostgreSQL lipsă din secrets. Verifică configurația.")
        st.stop()

# =========================
#   STATE HELPERS
# =========================
if "selected_row_key" not in st.session_state:
    st.session_state["selected_row_key"] = None
if "input_nonce" not in st.session_state:
    st.session_state["input_nonce"] = 0

def bump_input_nonce():
    """Incrementează nonce-ul pentru a reseta input-urile"""
    st.session_state["input_nonce"] += 1

# =========================
#   HELPERS
# =========================
def canon_sku(x: str) -> str:
    """
    Curăță spații, convertește notație științifică (5.6061E+11 -> 560610000000)
    """
    if x is None:
        return ""
    s = str(x).strip().replace(" ", "")
    if s == "":
        return ""
    # Detectează și convertește notația științifică
    if re.match(r"^[0-9]+(\.[0-9]+)?[eE]\+[0-9]+$", s):
        try:
            d = Decimal(s)
            s = format(d, 'f').replace(".", "")
        except InvalidOperation:
            pass
    return s

@st.cache_data(ttl=300, show_spinner=False)
def fetch_products(q: str | None):
    """
    Citește DIRECT din PostgreSQL (bypass PostgREST complet)
    Afișează TOATE produsele când nu e filtru
    """
    with st.spinner("📡 Se încarcă produsele din PostgreSQL..."):
        try:
            pg_url = get_pg_connection_string()
            conn = psycopg2.connect(pg_url, connect_timeout=10)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Query pentru produse cu/fără filtrare
            if q:
                # Cu filtru - limităm la 500
                query = """
                    SELECT 
                        p.id as product_id,
                        p.name,
                        (SELECT sku FROM product_sku WHERE product_id = p.id AND is_primary = true LIMIT 1) as primary_sku,
                        ARRAY(SELECT sku FROM product_sku WHERE product_id = p.id AND is_primary = false ORDER BY sku) as alias_skus
                    FROM product p
                    WHERE p.name ILIKE %s
                    ORDER BY p.name
                    LIMIT 500
                """
                cursor.execute(query, (f"%{q}%",))
            else:
                # Fără filtru - TOATE produsele
                query = """
                    SELECT 
                        p.id as product_id,
                        p.name,
                        (SELECT sku FROM product_sku WHERE product_id = p.id AND is_primary = true LIMIT 1) as primary_sku,
                        ARRAY(SELECT sku FROM product_sku WHERE product_id = p.id AND is_primary = false ORDER BY sku) as alias_skus
                    FROM product p
                    ORDER BY p.name
                """
                cursor.execute(query)
            
            # Obține rezultatele
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if not rows:
                return pd.DataFrame(columns=["product_id", "name", "primary_sku", "alias_skus"])
            
            # Convertește în DataFrame
            df_data = []
            for row in rows:
                df_data.append({
                    "product_id": row["product_id"],
                    "name": row["name"],
                    "primary_sku": row["primary_sku"] or "",
                    "alias_skus": row["alias_skus"] or []
                })
            
            return pd.DataFrame(df_data)
            
        except Exception as e:
            st.error(f"❌ Eroare la citirea din PostgreSQL: {str(e)}")
            import traceback
            st.code(traceback.format_exc())
            return pd.DataFrame(columns=["product_id", "name", "primary_sku", "alias_skus"])

def add_alias(product_id: str, new_sku: str):
    """Adaugă alias direct prin PostgreSQL"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        # Verifică dacă SKU-ul există deja
        cursor.execute(
            "SELECT product_id, is_primary FROM product_sku WHERE sku = %s",
            (new_sku.strip(),)
        )
        existing = cursor.fetchone()
        
        if existing:
            cursor.close()
            conn.close()
            return {"success": False, "error": f"SKU {new_sku} există deja pentru un alt produs"}
        
        # Inserează SKU-ul ca alias
        cursor.execute(
            "INSERT INTO product_sku (sku, product_id, is_primary) VALUES (%s, %s, false)",
            (new_sku.strip(), product_id)
        )
        
        conn.commit()
        affected = cursor.rowcount
        cursor.close()
        conn.close()
        
        return {"success": affected > 0, "error": None if affected > 0 else "Insert failed"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def remove_alias(product_id: str, sku: str):
    """Șterge alias direct prin PostgreSQL"""
    try:
        pg_url = get_pg_connection_string()
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        cursor = conn.cursor()
        
        # Verifică dacă este SKU principal
        cursor.execute(
            "SELECT is_primary FROM product_sku WHERE sku = %s AND product_id = %s",
            (sku.strip(), product_id)
        )
        result = cursor.fetchone()
        
        if not result:
            cursor.close()
            conn.close()
            return {"success": False, "error": "SKU nu există pentru acest produs"}
        
        if result[0]:  # is_primary = true
            cursor.close()
            conn.close()
            return {"success": False, "error": "Nu se poate șterge SKU-ul principal"}
        
        # Șterge SKU-ul
        cursor.execute(
            "DELETE FROM product_sku WHERE sku = %s AND product_id = %s AND is_primary = false",
            (sku.strip(), product_id)
        )
        
        conn.commit()
        affected = cursor.rowcount
        cursor.close()
        conn.close()
        
        return {"success": affected > 0, "error": None if affected > 0 else "Delete failed"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}

# =========================
#   UI - SEARCH
# =========================
st.markdown("### 🔍 Căutare produs")
search = st.text_input(
    "Caută după nume produs",
    placeholder="ex: iPhone 11, Samsung S20, Capac Spate, etc.",
    help="Lasă gol pentru a vedea toate produsele sau caută un produs specific"
).strip()

# Fetch data
df = fetch_products(search or None)

if df.empty:
    st.info("📭 Nu am găsit produse pentru criteriul de căutare. Încearcă alt termen.")
    st.stop()

# Afișare statistici rapide
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("📦 Produse", len(df))
with col2:
    total_aliases = df["alias_skus"].apply(len).sum()
    st.metric("🔗 Total aliasuri", int(total_aliases))
with col3:
    products_with_aliases = len(df[df["alias_skus"].apply(len) > 0])
    st.metric("✅ Cu aliasuri", products_with_aliases)
with col4:
    if search:
        st.metric("🔍 Filtru activ", "DA")
    else:
        st.metric("📋 Afișare", "TOATE")

st.divider()

# =========================
#   UI - TABEL PE TOATĂ LĂȚIMEA SUS
# =========================
st.subheader("📋 Lista produse")

# Pregătire tabel pentru selecție
view_df = df[["name", "primary_sku"]].copy()
view_df.insert(0, "✓", False)

# Păstrăm selecția anterioară
sel_idx_prev = st.session_state.get("selected_row_key")
if sel_idx_prev in df.index:
    view_df.loc[sel_idx_prev, "✓"] = True

# Data editor pentru selecție - PE TOATĂ LĂȚIMEA
edited = st.data_editor(
    view_df,
    key="results_editor",
    use_container_width=True,
    hide_index=False,
    height=500,  # Mai înalt pentru vizibilitate
    column_config={
        "✓": st.column_config.CheckboxColumn(
            required=False,
            help="Bifează un singur produs pentru a-i gestiona aliasurile",
            width="small"
        ),
        "name": st.column_config.TextColumn("Nume produs", width="large"),
        "primary_sku": st.column_config.TextColumn("SKU principal", width="medium"),
    },
    disabled=["name", "primary_sku"],
)

# Determinăm rândul selectat (SINGLE select)
selected_rows = [i for i, v in edited["✓"].items() if v]

if len(selected_rows) > 1:
    keep = selected_rows[0]
    st.warning("⚠️ Te rog selectează un singur rând. Folosesc primul bifat.")
    st.session_state["selected_row_key"] = keep
elif len(selected_rows) == 1:
    st.session_state["selected_row_key"] = selected_rows[0]
else:
    st.session_state["selected_row_key"] = None

# Informații despre selecție
chosen_idx = st.session_state["selected_row_key"]
if chosen_idx is not None:
    chosen_row = df.loc[chosen_idx]
    product_id = chosen_row["product_id"]
    name = chosen_row["name"]
    primary = chosen_row["primary_sku"]
    aliases = sorted([s for s in chosen_row["alias_skus"] if s != primary])
else:
    product_id = name = primary = None
    aliases = []

# Status bar
if chosen_idx is not None:
    st.success(f"✅ Produs selectat: **{name}** (SKU principal: `{primary}`)")
else:
    st.info("👆 Selectează un produs din tabelul de mai sus pentru a gestiona aliasurile")

st.divider()

# =========================
#   UI - DETALII & MANAGEMENT JOS
# =========================
if product_id is None:
    st.info("💡 Pentru a gestiona aliasurile, bifează un produs din tabelul de mai sus.")
    st.stop()

# Layout pe 2 coloane pentru management
left_mgmt, right_mgmt = st.columns(2, gap="large")

with left_mgmt:
    st.markdown("### 🔗 Aliasuri existente")
    
    # Info box
    st.info(f"**Produs:** {name}\n\n**SKU principal:** `{primary}`\n\n**Product ID:** `{product_id}`")
    
    if aliases:
        st.markdown(f"**{len(aliases)} aliasuri active:**")
        st.code(", ".join(aliases), language="text")
    else:
        st.warning("➕ Nu există aliasuri pentru acest produs.")

with right_mgmt:
    st.markdown("### ⚙️ Management aliasuri")
    
    # ===== ADĂUGARE ALIASURI =====
    with st.expander("➕ Adaugă aliasuri noi", expanded=True):
        ta_key = f"add_alias_input_{st.session_state['input_nonce']}"
        raw = st.text_area(
            "SKU-uri de adăugat",
            key=ta_key,
            placeholder="Exemple:\nGH97-18767C\n560610000000, 560610000001\n5.6061E+11",
            help="Separate prin virgulă, punct și virgulă sau pe linii diferite. Notația științifică e suportată.",
            height=100
        )

        if st.button("➕ Adaugă aliasurile", type="primary", use_container_width=True):
            raw_text = (raw or "").strip()
            if not raw_text:
                st.warning("⚠️ Introdu cel puțin un cod SKU.")
            else:
                # Parse input
                candidates = []
                for piece in re.split(r"[,;\n]+", raw_text):
                    s = canon_sku(piece)
                    if s:
                        candidates.append(s)
                
                # Elimină duplicate și SKU-uri existente
                to_add = sorted(set(candidates) - set(aliases) - {primary})

                if not to_add:
                    st.warning("⚠️ Toate SKU-urile există deja.")
                else:
                    ok, fail = [], []
                    progress_bar = st.progress(0)
                    
                    for idx, sku in enumerate(to_add):
                        result = add_alias(product_id, sku)
                        
                        if result["success"]:
                            ok.append(sku)
                        else:
                            fail.append((sku, result["error"]))
                        
                        progress_bar.progress((idx + 1) / len(to_add))
                    
                    progress_bar.empty()

                    if ok:
                        st.success(f"✅ Adăugate: {', '.join(ok)}")
                        bump_input_nonce()
                        
                    if fail:
                        st.error("❌ Eșecuri:")
                        for sku, msg in fail:
                            st.write(f"- `{sku}` → {msg}")

                    if ok:
                        fetch_products.clear()
                        st.rerun()
    
    # ===== ȘTERGERE ALIASURI =====
    with st.expander("🗑️ Șterge aliasuri", expanded=False):
        if not aliases:
            st.caption("Nu ai aliasuri de șters.")
        else:
            sel_to_remove = st.multiselect(
                "Selectează aliasuri",
                options=aliases,
                help="Poți selecta multiple"
            )
            
            danger = st.checkbox("✅ Confirm ștergerea")
            
            if st.button("🗑️ Șterge selectate", disabled=not danger, type="secondary", use_container_width=True):
                if not sel_to_remove:
                    st.warning("⚠️ Selectează măcar un alias.")
                else:
                    ok, fail = [], []
                    progress_bar = st.progress(0)
                    
                    for idx, sku in enumerate(sel_to_remove):
                        result = remove_alias(product_id, sku)
                        
                        if result["success"]:
                            ok.append(sku)
                        else:
                            fail.append((sku, result["error"]))
                        
                        progress_bar.progress((idx + 1) / len(sel_to_remove))
                    
                    progress_bar.empty()

                    if ok:
                        st.success(f"✅ Șterse: {', '.join(ok)}")
                        
                    if fail:
                        st.error("❌ Eșecuri:")
                        for sku, msg in fail:
                            st.write(f"- `{sku}` → {msg}")

                    if ok:
                        fetch_products.clear()
                        st.rerun()

# =========================
#   FOOTER
# =========================
st.divider()
st.caption("💡 **Sfat:** Aliasurile SKU te ajută să identifici produse la furnizori chiar dacă sunt listate sub coduri diferite.")
st.caption(f"🔌 **Conexiune:** PostgreSQL direct | 📊 **Total produse în DB:** {len(df)}")
