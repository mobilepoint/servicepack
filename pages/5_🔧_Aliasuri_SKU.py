# pages/5_🔧_Aliasuri_SKU.py
import re
from decimal import Decimal, InvalidOperation

import pandas as pd
import streamlit as st
from supabase import create_client, Client

# =========================
#   CONFIG
# =========================
st.set_page_config(page_title="Admin aliasuri SKU", layout="wide")
st.title("🔧 Admin aliasuri SKU")

# =========================
#   SUPABASE CONNECTION
# =========================
@st.cache_resource
def init_supabase() -> Client:
    """Inițializează conexiunea Supabase folosind secrets din noua structură"""
    try:
        url = st.secrets["connections"]["supabase"]["SUPABASE_URL"]
        key = st.secrets["connections"]["supabase"]["SUPABASE_KEY"]
        return create_client(url, key)
    except KeyError:
        st.error("❌ Credențiale Supabase lipsă din secrets. Verifică configurația.")
        st.stop()

client = init_supabase()

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
    Citește direct din tabelele product și product_sku (bypass view)
    Construiește manual structura cu primary_sku și alias_skus
    """
    with st.spinner("📡 Se încarcă produsele din Supabase..."):
        try:
            # Obține produsele cu filtrare opțională
            if q:
                products_resp = client.table("product").select("id, name").ilike("name", f"%{q}%").order("name").limit(500).execute()
            else:
                products_resp = client.table("product").select("id, name").order("name").limit(500).execute()
            
            products = products_resp.data or []
            
            if not products:
                return pd.DataFrame(columns=["product_id", "name", "primary_sku", "alias_skus"])
            
            # Obține toate product IDs
            product_ids = [p["id"] for p in products]
            
            # Obține toate SKU-urile pentru aceste produse într-un singur query
            skus_resp = client.table("product_sku").select("sku, product_id, is_primary").in_("product_id", product_ids).execute()
            skus = skus_resp.data or []
            
            # Grupează SKU-urile pe product_id
            sku_map = {}
            for sku_row in skus:
                pid = sku_row["product_id"]
                if pid not in sku_map:
                    sku_map[pid] = {"primary": None, "aliases": []}
                
                if sku_row.get("is_primary"):
                    sku_map[pid]["primary"] = sku_row["sku"]
                else:
                    sku_map[pid]["aliases"].append(sku_row["sku"])
            
            # Construiește DataFrame final
            rows = []
            for product in products:
                pid = product["id"]
                sku_data = sku_map.get(pid, {"primary": None, "aliases": []})
                
                rows.append({
                    "product_id": pid,
                    "name": product["name"],
                    "primary_sku": sku_data["primary"] or "",
                    "alias_skus": sku_data["aliases"]
                })
            
            return pd.DataFrame(rows)
            
        except Exception as e:
            st.error(f"❌ Eroare la citirea din Supabase: {str(e)}")
            return pd.DataFrame(columns=["product_id", "name", "primary_sku", "alias_skus"])

def rpc_add_alias(product_id: str, new_sku: str):
    """Apelează funcția RPC pentru adăugare alias"""
    return client.rpc("add_alias_sku", {"p_product_id": product_id, "p_sku": new_sku}).execute()

def rpc_remove_alias(product_id: str, sku: str):
    """Apelează funcția RPC pentru ștergere alias"""
    return client.rpc("remove_alias_sku", {"p_product_id": product_id, "p_sku": sku}).execute()

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
                    try:
                        resp = rpc_add_alias(product_id, sku)
                        if getattr(resp, "error", None):
                            fail.append((sku, str(resp.error)))
                        elif not getattr(resp, "data", None):
                            fail.append((sku, "RPC a răspuns fără date"))
                        else:
                            ok.append(sku)
                    except Exception as e:
                        fail.append((sku, repr(e)))
                    
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
                    try:
                        resp = rpc_remove_alias(product_id, sku)
                        if getattr(resp, "error", None):
                            fail.append((sku, str(resp.error)))
                        elif not getattr(resp, "data", None):
                            fail.append((sku, "Nu s-a șters niciun rând (poate nu exista)"))
                        else:
                            ok.append(sku)
                    except Exception as e:
                        fail.append((sku, repr(e)))
                    
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
#forteaza redeploy
