# pages/5_🚚_Comanda_APEX.py
"""
Pagina 5: Generator comandă APEX
- Normalizare fișiere APEX (multi-sheet, auto-header)
- Salvare date normalizate în BD (pentru comparații prețuri)
- Mapare pe catalog
- Generare rapoarte
"""

import io
import re
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import pandas as pd
import streamlit as st
from datetime import datetime
import psycopg2
from sidebar import render_sidebar

# =========================
# VERIFICARE AUTENTIFICARE (ca în celelalte pagini)
# =========================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("🔒 Autentificare")
    password = st.text_input("Parolă:", type="password")

    if st.button("Intră", type="primary"):
        if password == st.secrets.get("password", ""):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Parolă incorectă!")
    st.stop()

# =========================
# CONFIG & CONSTANTE
# =========================
st.set_page_config(page_title="🚚 Comandă APEX", page_icon="🚚", layout="wide")

ALLOWED_ROUNDINGS = [1, 3, 5, 10, 20, 50]
EUR_TO_RON = Decimal("5.1")

# =========================
# SIDEBAR (ca în celelalte pagini)
# =========================
render_sidebar()

# =========================
# HEADER
# =========================
st.title("🚚 Generator comenzi APEX")
st.caption("Normalizare APEX → Salvare BD → Mapare catalog → Raport comenzi")

# =========================
# CONEXIUNE DATABASE (ca în celelalte pagini)
# =========================
def get_db_connection():
    """Obține o conexiune NOUĂ la PostgreSQL"""
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        return conn
    except Exception as e:
        st.error(f"❌ Eroare conexiune DB: {e}")
        return None

# =========================
# HELPERS (din apex.py original)
# =========================
def round_to_allowed(value: float) -> int:
    for t in ALLOWED_ROUNDINGS:
        if value <= t:
            return t
    return ALLOWED_ROUNDINGS[-1]

def compute_order(row: pd.Series) -> int:
    iesiri = row.get("iesiri", 0)
    stoc_final = row.get("stoc final", 0)
    if pd.isna(iesiri) or pd.isna(stoc_final):
        return 0
    if iesiri > stoc_final and iesiri > 0:
        return round_to_allowed(iesiri)
    return 0

def normalize_str_series(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip()

def canon_sku(x: str) -> str:
    """Curăță spații, texte din paranteze și notație științifică."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).replace("\xa0", " ").strip()
    s = re.sub(r"\(.*?\)", "", s).strip()
    s = s.replace(" ", "")
    if s == "":
        return ""
    if re.match(r"^[0-9]+(\.[0-9]+)?[eE]\+[0-9]+$", s):
        try:
            d = Decimal(s)
            s = format(d, "f").rstrip("0").rstrip(".")
        except InvalidOperation:
            pass
    return s

def split_and_expand_codes(raw_code: str) -> list:
    """Împarte coduri multiple pe '/' și aplică reguli de prefix."""
    s = canon_sku(raw_code)
    if s == "":
        return []
    parts = [p for p in s.split("/") if p != ""]
    if not parts:
        return []
    first = parts[0]
    prefix = first[: first.find("-") + 1] if "-" in first else ""
    out = []
    for i, p in enumerate(parts):
        p = p.strip()
        if i > 0 and prefix and "-" not in p:
            p = prefix + p
        out.append(canon_sku(p))
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            uniq.append(c)
            seen.add(c)
    return uniq

def parse_decimal_maybe(val) -> Decimal:
    """Extrage număr din text cu EUR/LEI."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return Decimal("0")
    txt = str(val).replace("\xa0", " ").strip()
    txt = re.sub(r"[^\d,.\-]", "", txt)
    if txt == "" or txt in {".", ",", "-"}:
        return Decimal("0")
    if "," in txt and "." in txt:
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")
        else:
            txt = txt.replace(",", "")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        return Decimal(txt)
    except InvalidOperation:
        return Decimal("0")

# --------- Soft matching pe headere + auto-promote header row ----------
HEADER_VARIANTS = {
    "code": ["product code", "product_code", "code", "cod", "code no", "prod code", "productcode"],
    "name": ["product name", "product_name", "name", "nume", "denumire", "description", "descriere"],
    "qty": ["quantity", "qty", "quant", "q-ty", "qnty"],
    "eur": ["euro price", "euro pri", "eur price", "euro", "eur", "price(€)", "price €", "€ price", "price eur"],
    "order": ["order", "ord", "order qty", "order_qty", "comanda", "orderhint", "order hint"],
}

def _clean_header_cell(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).replace("\xa0", " ")
    s = re.sub(r"[\r\n]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()

def _find_by_variants(headers_clean: list, keys: list):
    for i, h in enumerate(headers_clean):
        for k in keys:
            k0 = k.lower()
            if h == k0 or h.startswith(k0):
                return i
    return None

def _dedupe_columns(labels):
    seen = {}
    out = []
    for lbl in [str(x) for x in labels]:
        base = lbl
        if base not in seen:
            seen[base] = 1
            out.append(base)
        else:
            seen[base] += 1
            out.append(f"{base}.{seen[base]}")
    return out

def _promote_header_row(df: pd.DataFrame):
    """Găsește și promovează rândul de header."""
    max_scan = min(50, len(df))
    for r in range(max_scan):
        row = df.iloc[r].tolist()
        clean = [_clean_header_cell(x) for x in row]
        if any("product" in c and "code" in c for c in clean) and any("product" in c and "name" in c for c in clean):
            new_headers = [str(x) for x in df.iloc[r].tolist()]
            df2 = df.iloc[r+1:].copy()
            df2.columns = new_headers
            # Elimină coloanele complet goale
            empty_pos = []
            for j in range(df2.shape[1]):
                col_txt = df2.iloc[:, j].astype(str)
                col_txt = col_txt.replace({"nan": "", "None": ""})
                if col_txt.str.strip().eq("").all():
                    empty_pos.append(j)
            if empty_pos:
                keep_pos = [j for j in range(df2.shape[1]) if j not in set(empty_pos)]
                df2 = df2.iloc[:, keep_pos]
            df2.columns = _dedupe_columns(df2.columns)
            return df2
    return df

@st.cache_data(ttl=600, show_spinner=False)
def load_sku_mapping_from_db() -> pd.DataFrame:
    """Încarcă view-ul v_sku_mapping din PostgreSQL."""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame(columns=["sku_any", "primary_sku", "denumire_db"])
    
    try:
        query = "SELECT sku_any, primary_sku, denumire_db FROM v_sku_mapping;"
        df = pd.read_sql(query, conn)
        df = df.drop_duplicates(subset=["sku_any"])
        return df[["sku_any", "primary_sku", "denumire_db"]].copy()
    except Exception as e:
        st.error(f"❌ Eroare la citire v_sku_mapping: {e}")
        return pd.DataFrame(columns=["sku_any", "primary_sku", "denumire_db"])
    finally:
        conn.close()


def read_any_apex(file) -> pd.DataFrame:
    """Citește APEX (xlsx/xls/csv). Dacă e Excel, procesează TOATE foile."""
    name = (file.name or "").lower()
    frames = []
    if name.endswith(".csv"):
        df = pd.read_csv(file, dtype=str)
        frames.append(df)
    else:
        wb = pd.read_excel(file, dtype=str, sheet_name=None)
        for sheet, df in wb.items():
            if df is None or df.empty:
                continue
            df_fixed = _promote_header_row(df)
            frames.append(df_fixed)
    if not frames:
        return pd.DataFrame()
    df_all = pd.concat(frames, ignore_index=True)
    return df_all
@st.cache_data(ttl=600, show_spinner=False)
def load_apex_exclude_codes() -> set[str]:
    """Încarcă lista de coduri excluse din tabelul apex_exclude."""
    conn = get_db_connection()
    if not conn:
        return set()
    
    try:
        df = pd.read_sql("SELECT cod FROM apex_exclude;", conn)
        return set(df["cod"].astype(str).str.strip())
    except Exception as e:
        st.error(f"❌ Eroare la citire apex_exclude: {e}")
        return set()
    finally:
        conn.close()


def save_apex_exclude_codes(codes: list[str]) -> bool:
    """Salvează codurile selectate în apex_exclude (batch insert)."""
    if not codes:
        st.warning("Nu ai selectat niciun produs pentru excludere.")
        return False
    
    conn = get_db_connection()
    if not conn:
        st.error("❌ Nu pot salva excluderile - lipsește conexiunea DB")
        return False
    
    try:
        cursor = conn.cursor()
        insert_sql = """
            INSERT INTO apex_exclude (cod)
            VALUES (%s)
            ON CONFLICT (cod) DO NOTHING;
        """
        cursor.executemany(insert_sql, [(str(c),) for c in codes])
        conn.commit()
        cursor.close()
        conn.close()
        
        st.success(f"✅ Salvate {len(set(codes))} coduri în lista de excluderi.")
        load_apex_exclude_codes.clear()  # Invalidează cache
        return True
    except Exception as e:
        st.error(f"❌ Eroare la salvare excluderi: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False

def delete_apex_exclude_codes(codes: list[str]) -> bool:
    """Șterge codurile selectate din apex_exclude."""
    if not codes:
        st.warning("Nu ai selectat niciun cod pentru ștergere.")
        return False

    conn = get_db_connection()
    if not conn:
        st.error("❌ Nu pot șterge excluderile - lipsește conexiunea DB")
        return False

    try:
        cursor = conn.cursor()
        delete_sql = "DELETE FROM apex_exclude WHERE cod = %s;"
        cursor.executemany(delete_sql, [(str(c),) for c in codes])
        deleted_count = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        st.success(f"✅ Șterse {deleted_count} coduri din lista de excluderi.")
        load_apex_exclude_codes.clear()  # Invalidează cache
        return True
    except Exception as e:
        st.error(f"❌ Eroare la ștergere excluderi: {e}")
        if conn:
            conn.rollback()
            conn.close()
        return False


def read_smartbill_file(file) -> pd.DataFrame:
    """
    Citește fișierul SmartBill cu structură fixă:
    - Header pe rândul 10 (index 9)
    - Coloana 3 (C): Cod produs
    - Coloana 5 (E): Stoc initial
    - Coloana 6 (F): Intrari
    - Coloana 7 (G): Iesiri
    - Coloana 8 (H): Stoc final
    """
    try:
        # Citim fișierul cu header pe rândul 10 (index 9)
        df = pd.read_excel(file, header=9)

        # Extragem doar coloanele necesare prin poziție (0-indexed)
        # Coloana C (cod) = index 2
        # Coloana E (stoc initial) = index 4
        # Coloana F (intrari) = index 5
        # Coloana G (iesiri) = index 6
        # Coloana H (stoc final) = index 7

        if df.shape[1] < 8:
            st.error(f"Fișierul SmartBill nu are suficiente coloane (găsite {df.shape[1]}, necesare minim 8)")
            return pd.DataFrame()

        # Selectăm coloanele prin poziție
        result = pd.DataFrame({
            'cod': df.iloc[:, 2],  # Coloana C (index 2)
            'stoc initial': df.iloc[:, 4],  # Coloana E (index 4)
            'intrari': df.iloc[:, 5],  # Coloana F (index 5)
            'iesiri': df.iloc[:, 6],  # Coloana G (index 6)
            'stoc final': df.iloc[:, 7]  # Coloana H (index 7)
        })

        # Curățăm codul
        result['cod'] = result['cod'].astype(str).str.strip()
        result = result[result['cod'].str.strip() != '']
        result = result[result['cod'] != 'nan']
        result = result[result['cod'] != 'None']

        # Convertim valorile numerice
        for col in ['stoc initial', 'intrari', 'iesiri', 'stoc final']:
            result[col] = pd.to_numeric(result[col], errors='coerce').fillna(0)

        st.info(f"📊 SmartBill: încărcate {len(result)} produse")
        return result

    except Exception as e:
        st.error(f"❌ Eroare la citire SmartBill: {e}")
        import traceback
        st.error(f"Detalii: {traceback.format_exc()}")
        return pd.DataFrame()



# =========================
# SALVARE ÎN BD (CORECTATĂ)
# =========================

def save_apex_normalized_to_db(df: pd.DataFrame) -> bool:
    """
    Salvează datele APEX normalizate în tabelul apex_normalized.
    Șterge toate datele existente înainte de salvare.
    """
    conn = get_db_connection()
    if not conn:
        st.error("❌ Nu pot salva - lipsește conexiunea DB")
        return False

    try:
        cursor = conn.cursor()

        # 1. Șterge datele existente
        cursor.execute("DELETE FROM apex_normalized;")
        deleted_count = cursor.rowcount
        conn.commit()
        st.info(f"🗑️ Șterse {deleted_count} înregistrări vechi din apex_normalized")

        # 2. Insert datele noi cu progress indicator
        insert_query = """
            INSERT INTO apex_normalized
            (cod_raw, cod, nume_apex, cantitate, pret_eur, pret_lei, order_hint, import_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        # Pregătim datele - IMPORTANT: folosim valorile deja calculate din DataFrame
        insert_data = []
        total_rows = len(df)

        # Progress bar pentru pregătire date
        progress_bar = st.progress(0, text="Pregătire date pentru salvare...")

        for idx, row in df.iterrows():
            # Extragem valorile direct din DataFrame (deja normalizate)
            pret_eur_val = row.get("pret_eur", "0")
            pret_lei_val = row.get("pret_lei", "0")

            # Convertim la float doar la final
            try:
                pret_eur_float = float(parse_decimal_maybe(pret_eur_val))
            except:
                pret_eur_float = 0.0

            try:
                pret_lei_float = float(parse_decimal_maybe(pret_lei_val))
            except:
                pret_lei_float = 0.0

            insert_data.append((
                str(row.get("cod_raw", "")),
                str(row.get("cod", "")),
                str(row.get("nume_apex", "")),
                str(row.get("cantitate", "")),
                pret_eur_float,
                pret_lei_float,
                str(row.get("order_hint", "")),
                datetime.now()
            ))

            # Update progress la fiecare 10% sau la ultimul rând
            if (idx + 1) % max(1, total_rows // 10) == 0 or idx == total_rows - 1:
                progress_pct = int((idx + 1) / total_rows * 100)
                progress_bar.progress((idx + 1) / total_rows, 
                                     text=f"Pregătire date: {idx + 1}/{total_rows} ({progress_pct}%)")

        progress_bar.progress(1.0, text="Date pregătite! ✓")

        # Scriere în batch-uri cu progress indicator
        st.info("💾 Scriere în baza de date...")
        write_progress = st.progress(0, text="Salvare în BD...")

        batch_size = 1000  # Scriem câte 1000 de înregistrări odată
        total_batches = (len(insert_data) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(insert_data), batch_size):
            batch = insert_data[batch_idx:batch_idx + batch_size]
            cursor.executemany(insert_query, batch)
            conn.commit()

            # Update progress
            current_batch = (batch_idx // batch_size) + 1
            progress_pct = int(current_batch / total_batches * 100)
            write_progress.progress(min(current_batch / total_batches, 1.0), 
                                   text=f"Salvat batch {current_batch}/{total_batches} ({progress_pct}%)")

        write_progress.progress(1.0, text="Salvare completă! ✓")
        st.success(f"✅ Salvate {len(insert_data)} înregistrări noi în apex_normalized")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        st.error(f"❌ Eroare la salvare în BD: {e}")
        import traceback
        st.error(f"Detalii: {traceback.format_exc()}")
        if conn:
            conn.rollback()
            conn.close()
        return False



def normalize_apex_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Mapează coloanele cheie și elimină rândurile fără cod."""
    if df.empty:
        raise ValueError("Fișierul APEX nu conține date.")
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    head_clean = [_clean_header_cell(c) for c in df.columns]

    idx_code = _find_by_variants(head_clean, HEADER_VARIANTS["code"])
    idx_name = _find_by_variants(head_clean, HEADER_VARIANTS["name"])
    idx_qty = _find_by_variants(head_clean, HEADER_VARIANTS["qty"])
    idx_eur = _find_by_variants(head_clean, HEADER_VARIANTS["eur"])
    idx_order = _find_by_variants(head_clean, HEADER_VARIANTS["order"])

    if idx_code is None:
        df2 = _promote_header_row(df)
        if df2 is not df:
            return normalize_apex_columns(df2)
        raise ValueError("În APEX nu am găsit coloana «Product Code».")

    idxs, names = [], []
    def _add(idx, name):
        if idx is not None:
            idxs.append(idx)
            names.append(name)

    _add(idx_code, "cod_raw")
    _add(idx_name, "nume_apex")
    _add(idx_qty, "cantitate")
    _add(idx_eur, "pret_eur")
    _add(idx_order, "order_hint")

    out = df.iloc[:, idxs].copy()
    out.columns = names
    for c in out.columns:
        out[c] = out[c].astype(str).str.replace("\xa0", " ").str.strip()
    out["cod_raw"] = out["cod_raw"].replace({"nan": "", "None": ""})
    out = out[out["cod_raw"].astype(str).str.strip() != ""].copy()
    return out

def expand_apex_rows(df_norm_cols: pd.DataFrame) -> pd.DataFrame:
    """Duplichează rândurile cu coduri multiple separate pe '/'."""
    rows = []
    for _, r in df_norm_cols.iterrows():
        codes = split_and_expand_codes(r["cod_raw"])
        if not codes:
            continue
        for c in codes:
            new_r = r.copy()
            new_r["cod"] = c
            rows.append(new_r)
    if not rows:
        return pd.DataFrame(columns=list(df_norm_cols.columns) + ["cod"])
    out = pd.DataFrame(rows)
    out["cod"] = out["cod"].astype(str).str.replace(" ", "", regex=False).str.strip()

    # Calculează preț LEI
    if "pret_eur" in out.columns:
        eur_num = out["pret_eur"].apply(parse_decimal_maybe)
        out["pret_lei"] = eur_num.apply(lambda x: (x * EUR_TO_RON).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)).astype(str)
    else:
        out["pret_lei"] = ""

    out = out.drop_duplicates().reset_index(drop=True)
    return out



# =========================
# UI INPUT FILES
# =========================
st.subheader("📁 Fișiere de intrare")

c1, c2 = st.columns(2)
with c1:
    apex_file = st.file_uploader("Fișier APEX original (.xlsx / .xls / .csv)", type=["xlsx", "xls", "csv"], key="apex_raw")
with c2:
    smartbill_file = st.file_uploader("Fișier SmartBill (.xlsx sau .xls)", type=["xlsx", "xls"], key="smartbill")

# =========================
# LOGICĂ PRINCIPALĂ
# =========================
apex_df_normalized = None

apex_df_normalized = None

if apex_file:
    st.markdown("---")
    st.markdown("### 📊 Pas 1 — Normalizare APEX")
    
    try:
        apex_raw = read_any_apex(apex_file)
        apex_trim = normalize_apex_columns(apex_raw)
        apex_df_normalized_raw = expand_apex_rows(apex_trim)
    except Exception as e:
        st.error(f"Eroare la normalizare APEX: {e}")
        st.stop()
    
    # ============================================
    # APLICĂ EXCLUDERILE - ELIMINĂ CODURILE IGNORATE
    # ============================================
    exclude_codes = load_apex_exclude_codes()
    apex_df_normalized_raw["cod_canon"] = apex_df_normalized_raw["cod"].map(canon_sku)
    mask_not_excluded = ~apex_df_normalized_raw["cod_canon"].isin(exclude_codes)
    
    # Datele finale = doar produsele neexcluse
    apex_df_normalized = apex_df_normalized_raw[mask_not_excluded].reset_index(drop=True)
    
    # Produsele excluse (pentru preview opțional)
    apex_excluded = apex_df_normalized_raw[~mask_not_excluded].reset_index(drop=True)
    
    if not apex_excluded.empty:
        st.info(f"🚫 {len(apex_excluded)} produse excluse (ignorate) din import")
    
    st.success(f"✅ APEX normalizat: {len(apex_df_normalized)} produse active (fără excluderi)")
    
    cols_show_norm = [c for c in ["cod", "nume_apex", "cantitate", "pret_eur", "pret_lei", "order_hint"] 
                      if c in apex_df_normalized.columns]
    st.dataframe(apex_df_normalized[cols_show_norm].fillna(""), use_container_width=True)
    
    # ============================================
    # UI PENTRU GESTIONARE EXCLUDERI
    # ============================================
    st.markdown("---")
    st.markdown("#### 🧹 Gestionare excluderi permanente")
    
    if "apex_exclude_selected" not in st.session_state:
        st.session_state.apex_exclude_selected = set()
    
    all_codes_current = apex_df_normalized["cod_canon"].tolist()
    
    col_sel1, col_sel2, col_sel3 = st.columns([1, 1, 2])
    
    with col_sel1:
        select_all = st.checkbox("Selectează toate produsele vizibile", key="apex_exclude_select_all")
    
    with col_sel2:
        if st.button("Resetează selecția"):
            st.session_state.apex_exclude_selected = set()
            st.rerun()
    
    # Aplică select all (în memorie, nu în DB)
    if select_all:
        st.session_state.apex_exclude_selected = set(all_codes_current)
    
    # Multiselect pentru alegere individuală
    current_selected_list = [c for c in all_codes_current if c in st.session_state.apex_exclude_selected]
    new_selected = st.multiselect(
        "Selectează produse de exclus permanent (nu vor mai apărea la următoarele importuri):",
        options=all_codes_current,
        default=current_selected_list,
        key="apex_exclude_multiselect",
        help="Produsele selectate vor fi salvate în lista de ignorare și eliminate din toate procesările viitoare"
    )
    
    # Sincronizează selecția în session_state
    st.session_state.apex_exclude_selected = set(new_selected)
    
    col_action1, col_action2 = st.columns([2, 2])
    
    with col_action1:
        if st.button("💾 Salvează în lista de excluderi", type="primary", use_container_width=True):
            to_save = sorted(st.session_state.apex_exclude_selected)
            if save_apex_exclude_codes(to_save):
                st.session_state.apex_exclude_selected = set()
                st.rerun()
    
    with col_action2:
        # Buton pentru a vizualiza produsele deja excluse
        if st.button("👁️ Vezi toate excluderile active", use_container_width=True):
            st.session_state.show_excluded = not st.session_state.get("show_excluded", False)
    
    if st.session_state.get("show_excluded", False) and len(exclude_codes) > 0:
        st.markdown("##### 🚫 Coduri excluse permanent:")
        excluded_df = pd.DataFrame({"cod": sorted(list(exclude_codes))})
        st.dataframe(excluded_df, use_container_width=True)

        # UI pentru ștergere selectivă sau completă
        st.markdown("**Opțiuni de ștergere:**")
        col_del1, col_del2 = st.columns(2)

        with col_del1:
            # Multiselect pentru ștergere selectivă
            codes_to_delete = st.multiselect(
                "Selectează coduri de șters din excluderi:",
                options=sorted(list(exclude_codes)),
                key="delete_exclude_multiselect",
                help="Selectează codurile pe care vrei să le scoți din lista de excluderi"
            )

            if st.button("🗑️ Șterge codurile selectate", use_container_width=True):
                if codes_to_delete:
                    if delete_apex_exclude_codes(codes_to_delete):
                        st.rerun()
                else:
                    st.warning("Selectează cel puțin un cod pentru ștergere")

        with col_del2:
            st.write("")  # Spacing
            st.write("")  # Spacing
            # Buton pentru ștergere totală
            if st.button("🗑️ Șterge TOATE excluderile", type="secondary", use_container_width=True):
                conn = get_db_connection()
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM apex_exclude;")
                        conn.commit()
                        st.success("✅ Toate excluderile au fost șterse")
                        load_apex_exclude_codes.clear()
                        cursor.close()
                        conn.close()
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Eroare la ștergere: {e}")
                        if conn:
                            conn.rollback()
                            conn.close()
    
    # ============================================
    # BUTOANE ACȚIUNI PRINCIPALE
    # ============================================
    st.markdown("---")
    col_a, col_b = st.columns(2)
    
    with col_a:
        csv_buf = io.StringIO()
        apex_df_normalized.to_csv(csv_buf, index=False, quoting=csv.QUOTE_MINIMAL)
        st.download_button(
            "⬇️ Descarcă CSV (produse active)", 
            data=csv_buf.getvalue(), 
            file_name="apex_normalizat.csv", 
            mime="text/csv"
        )
    
    with col_b:
        if st.button("💾 Salvează în BD (apex_normalized)", type="primary"):
            # Salvează DOAR produsele neexcluse
            save_apex_normalized_to_db(apex_df_normalized)

if apex_df_normalized is not None and smartbill_file:
    st.markdown("---")
    st.markdown("### 🔄 Pas 2 — Mapare pe catalog + raport")

    # 0) Mapping din DB
    try:
        df_map = load_sku_mapping_from_db()
    except Exception as e:
        st.error(f"Nu am putut citi v_sku_mapping: {e}")
        st.stop()

    alt_to_principal = dict(zip(df_map["sku_any"].astype(str), df_map["primary_sku"].astype(str)))
    prim_to_name = dict(zip(
        df_map.drop_duplicates(subset=["primary_sku"])["primary_sku"].astype(str),
        df_map.drop_duplicates(subset=["primary_sku"])["denumire_db"].astype(str)
    ))

    # 1) APEX
    apex_df = apex_df_normalized.copy()
    apex_df["cod_canon"] = apex_df["cod"].map(canon_sku)
    name_col_apex = "nume_apex" if "nume_apex" in apex_df.columns else None

    # 2) SmartBill
    try:
        smart_df = read_smartbill_file(smartbill_file)
        if smart_df.empty:
            st.error("Fișierul SmartBill este gol sau invalid")
            st.stop()
    except Exception as e:
        st.error(f"Eroare SmartBill: {e}")
        st.stop()

    smart_df.columns = smart_df.columns.str.strip().str.lower()
    if "cod" not in smart_df.columns:
        st.error("În SmartBill lipsește coloana 'cod'.")
        st.stop()

    smart_df["cod"] = normalize_str_series(smart_df["cod"])
    for col in ["iesiri", "stoc final"]:
        if col not in smart_df.columns:
            smart_df[col] = 0
        smart_df[col] = pd.to_numeric(smart_df[col], errors="coerce").fillna(0)

    # 3) Canonizare + mapare
    smart_df["cod_canon"] = smart_df["cod"].map(canon_sku)
    apex_df["cod_match"] = apex_df["cod_canon"].map(alt_to_principal).fillna(apex_df["cod_canon"])
    smart_df["cod_match"] = smart_df["cod_canon"].map(alt_to_principal).fillna(smart_df["cod_canon"])

    # 4) Agregare SmartBill
    smart_grouped = smart_df.groupby("cod_match", as_index=False)[["iesiri", "stoc final"]].sum()

    # 5) Merge + comandă
    merged = apex_df.merge(smart_grouped, on="cod_match", how="left")
    for col in ["iesiri", "stoc final"]:
        if col not in merged.columns:
            merged[col] = 0
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)
    merged["comanda"] = merged.apply(compute_order, axis=1)

    # 6) Nume DB
    merged = merged.rename(columns={"cod_match": "SKU_principal"})
    merged["Produs_DB"] = merged["SKU_principal"].map(prim_to_name)

    # 7) Afișare
    st.subheader("📦 Rezultat comandă (agregat pe SKU principal)")
    show_cols = ["cod", "SKU_principal", "Produs_DB", "iesiri", "stoc final", "comanda"]
    if name_col_apex:
        show_cols.insert(1, name_col_apex)
    for extra in ["pret_eur", "pret_lei"]:
        if extra in merged.columns and extra not in show_cols:
            show_cols.append(extra)
    show_cols = [c for c in show_cols if c in merged.columns]
    st.dataframe(merged[show_cols], use_container_width=True)

    # 8) Export CSV
    out_csv = io.StringIO()
    merged.to_csv(out_csv, index=False, quoting=csv.QUOTE_MINIMAL)
    st.download_button("⬇️ Descarcă comandă furnizor (CSV)", data=out_csv.getvalue(), file_name="apex_comanda.csv", mime="text/csv")

    # 9) Raport discrepanțe
    st.subheader("⚠️ Raport discrepanțe APEX vs SmartBill")
    smart_canon_set = set(smart_grouped["cod_match"].unique())
    apex_canon_set = set(apex_df["cod_canon"].map(lambda x: alt_to_principal.get(x, x)).unique())

    in_apex_not_in_smart = apex_df.loc[~apex_df["cod_match"].isin(smart_canon_set), ["cod", "cod_match"]].copy()
    in_apex_not_in_smart["categorie"] = "APEX: lipsește în SmartBill"
    if name_col_apex:
        in_apex_not_in_smart = in_apex_not_in_smart.merge(
            apex_df[["cod", name_col_apex]], on="cod", how="left"
        ).rename(columns={name_col_apex: "nume_apex"})
    in_apex_not_in_smart["iesiri"] = ""
    in_apex_not_in_smart["stoc final"] = ""

    sb_zero = smart_grouped[(smart_grouped["stoc final"] == 0) & (smart_grouped["iesiri"] == 0)].copy()
    sb_zero_in_apex = sb_zero[sb_zero["cod_match"].isin(apex_canon_set)].copy()
    sb_zero_in_apex["categorie"] = "SB: 0 stoc & 0 mișcări"
    if name_col_apex:
        apex_name_by_canon = apex_df.drop_duplicates(subset=["cod_match"])[["cod_match", name_col_apex]].rename(columns={name_col_apex: "nume_apex"})
        sb_zero_in_apex = sb_zero_in_apex.merge(apex_name_by_canon, on="cod_match", how="left")
    apex_rep = apex_df.drop_duplicates(subset=["cod_match"])[["cod_match", "cod"]]
    sb_zero_in_apex = sb_zero_in_apex.merge(apex_rep, on="cod_match", how="left")

    discrepante_cols = ["categorie", "cod", "cod_match", "nume_apex", "iesiri", "stoc final"]
    discrepante = pd.concat([
        in_apex_not_in_smart.reindex(columns=discrepante_cols, fill_value=""),
        sb_zero_in_apex.reindex(columns=discrepante_cols, fill_value=""),
    ], ignore_index=True).sort_values(["categorie", "cod_match", "cod"], kind="stable")

    st.dataframe(discrepante, use_container_width=True)
    disc_buffer = io.StringIO()
    discrepante.to_csv(disc_buffer, index=False, quoting=csv.QUOTE_MINIMAL)
    st.download_button("⬇️ Descarcă discrepanțe (CSV)", data=disc_buffer.getvalue(), file_name="apex_smartbill_discrepante.csv", mime="text/csv")

else:
    if not apex_file:
        st.info("👆 Încarcă fișierul APEX pentru a începe")
