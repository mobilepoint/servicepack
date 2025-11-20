# pages/5_🔧_Aliasuri_SKU.py
import re
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor

# =========================
#   CONFIG
# =========================
st.set_page_config(page_title="Admin aliasuri SKU", layout="wide")
st.title("🔧 Admin aliasuri SKU")

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
    """
    with st.spinner("📡 Se încarcă produsele din PostgreSQL..."):
        try:
            pg_url = get_pg_connection_string()
            conn = psycopg2.connect(pg_url, connect_timeout=10)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Query pentru produse cu filtrare opțională
            if q:
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
                query = """
                    SELECT 
                        p.id as product_id,
                        p.name,
                        (SELECT sku FROM product_sku WHERE product_id = p.id AND is_primary = true LIMIT 1) as primary_sku,
                        ARRAY(SELECT sku FROM product_sku WHERE product_id = p.id AND is_primary = false ORDER BY sku) as alias_skus
                    FROM product p
                    ORDER BY p.name
                    LIMIT 500
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
    help="Caută produse pentru a le gestiona aliasurile SKU"
).strip()

# Fetch data
df = fetch_products(search or None)

if df.empty:
    st.info("📭 Nu am găsit produse pentru criteriul de căutare. Încearcă alt termen.")
    st.stop()

# Afișare statistici rapide
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("📦 Produse găsite", len(df))
with col2:
    total_aliases = df["alias_skus"].apply(len).sum()
    st.metric("🔗 Total aliasuri", int(total_aliases))
with col3:
    products_with_aliases = len(df[df["alias_skus"].apply(len) > 0])
    st.metric("✅ Cu aliasuri", products_with_aliases)

st.divider()

# =========================
#   UI - SPLIT LAYOUT
# =========================
left, right = st.columns([2, 3], gap="large")

with left:
    st.subheader("📋 Rezultate căutare")
    
    # Pregătire tabel pentru selecție
    view_df = df[["name", "primary_sku"]].copy()
    view_df.insert(0, "selectează", False)

    # Păstrăm selecția anterioară
    sel_idx_prev = st.session_state.get("selected_row_key")
    if sel_idx_prev in df.index:
        view_df.loc[sel_idx_prev, "selectează"] = True

    # Data editor pentru selecție
    edited = st.data_editor(
        view_df,
        key="results_editor",
        use_container_width=True,
        hide_index=False,
        height=400,
        column_config={
            "selectează": st.column_config.CheckboxColumn(
                required=False,
                help="Bifează un singur produs pentru a-i gestiona aliasurile"
            ),
            "name": st.column_config.TextColumn("Nume produs", width="large"),
            "primary_sku": st.column_config.TextColumn("SKU principal", width="medium"),
        },
        disabled=["name", "primary_sku"],
    )

    # Determinăm rândul selectat (SINGLE select)
    selected_rows = [i for i, v in edited["selectează"].items() if v]
    
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
        st.success(f"✅ Selectat: **{name}**")
    else:
        st.info("👆 Selectează un produs din tabel")

with right:
    st.subheader("⚙️ Detalii & Management")
    
    if product_id is None:
        st.info("👈 Selectează un produs din tabelul din stânga pentru a gestiona aliasurile.")
        st.stop()

    # Informații produs
    with st.container():
        st.markdown(f"**📦 Produs:** {name}")
        st.markdown(f"**🏷️ SKU principal:** `{primary}`")
        st.markdown(f"**🆔 Product ID:** `{product_id}`")

    st.markdown("---")

    # Aliasuri existente
    st.markdown("**🔗 Aliasuri existente:**")
    if aliases:
        st.code(", ".join(aliases), language="text")
        st.caption(f"Total: {len(aliases)} aliasuri")
    else:
        st.info("➕ Nu există aliasuri pentru acest produs. Adaugă mai jos.")

    st.markdown("---")

    # ===== ADĂUGARE ALIASURI =====
    st.markdown("### ➕ Adaugă aliasuri noi")
    
    ta_key = f"add_alias_input_{st.session_state['input_nonce']}"
    raw = st.text_area(
        "SKU-uri de adăugat (separate prin virgulă, punct și virgulă sau pe linii diferite)",
        key=ta_key,
        placeholder="Exemple:\nGH97-18767C\n560610000000, 560610000001\n5.6061E+11",
        help="Poți introduce multiple SKU-uri. Notația științifică (ex: 5.6061E+11) va fi convertită automat.",
        height=100
    )

    add_col1, add_col2 = st.columns([1, 3])
    with add_col1:
        btn_add = st.button("➕ Adaugă", type="primary", use_container_width=True)
    with add_col2:
        st.caption("💡 Aliasurile se marchează automat ca `is_primary = false` în baza de date.")

    if btn_add:
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
                st.warning("⚠️ Nimic de adăugat: toate SKU-urile există deja (alias sau principal).")
            else:
                ok, fail = [], []
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for idx, sku in enumerate(to_add):
                    status_text.text(f"Procesez: {sku}...")
                    result = add_alias(product_id, sku)
                    
                    if result["success"]:
                        ok.append(sku)
                    else:
                        fail.append((sku, result["error"]))
                    
                    progress_bar.progress((idx + 1) / len(to_add))
                
                progress_bar.empty()
                status_text.empty()

                if ok:
                    st.success(f"✅ **Adăugate cu succes:** {', '.join(ok)}")
                    bump_input_nonce()
                    
                if fail:
                    st.error("❌ **Eșecuri:**")
                    for sku, msg in fail:
                        st.write(f"- `{sku}` → {msg}")

                if ok:
                    fetch_products.clear()
                    st.rerun()

    st.markdown("---")

    # ===== ȘTERGERE ALIASURI =====
    st.markdown("### 🗑️ Șterge aliasuri")
    
    if not aliases:
        st.caption("➕ Nu ai aliasuri de șters. Adaugă mai întâi aliasuri.")
    else:
        sel_to_remove = st.multiselect(
            "Alege aliasurile de șters",
            options=aliases,
            placeholder="Selectează unul sau mai multe SKU-uri",
            help="Poți selecta multiple aliasuri pentru ștergere în bloc"
        )
        
        danger = st.checkbox(
            "✅ Confirm că știu ce fac (nu pot șterge SKU principal)",
            value=False,
            help="Această acțiune va șterge definitiv aliasurile selectate"
        )
        
        colr1, colr2 = st.columns([1, 3])
        with colr1:
            btn_remove = st.button(
                "🗑️ Șterge selectate",
                disabled=not danger,
                type="secondary",
                use_container_width=True
            )
        with colr2:
            st.caption("⚠️ Operația afectează doar aliasurile, nu SKU-ul principal.")

        if btn_remove:
            if not sel_to_remove:
                st.warning("⚠️ Selectează măcar un alias pentru ștergere.")
            else:
                ok, fail = [], []
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                for idx, sku in enumerate(sel_to_remove):
                    status_text.text(f"Șterg: {sku}...")
                    result = remove_alias(product_id, sku)
                    
                    if result["success"]:
                        ok.append(sku)
                    else:
                        fail.append((sku, result["error"]))
                    
                    progress_bar.progress((idx + 1) / len(sel_to_remove))
                
                progress_bar.empty()
                status_text.empty()

                if ok:
                    st.success(f"✅ **Șterse cu succes:** {', '.join(ok)}")
                    
                if fail:
                    st.error("❌ **Eșecuri:**")
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
st.caption("🔌 **Conexiune:** PostgreSQL direct (bypass Supabase PostgREST)")
