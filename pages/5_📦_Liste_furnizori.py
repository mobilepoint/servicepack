# pages/5_🚚_Liste_Furnizori.py

"""
Pagina 5: Generator comenzi furnizori (APEX + GSMNET)
- Normalizare fișiere furnizori
- Salvare date în BD pentru comparații prețuri
- Mapare pe catalog
- Comparare prețuri și selecție furnizor optim
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
# VERIFICARE AUTENTIFICARE
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
st.set_page_config(page_title="🚚 Liste Furnizori", page_icon="🚚", layout="wide")
ALLOWED_ROUNDINGS = [1, 3, 5, 10, 20, 50]
EUR_TO_RON = Decimal("5.1")

# =========================
# SIDEBAR
# =========================
render_sidebar()

# =========================
# HEADER
# =========================
st.title("🚚 Generator comenzi furnizori")
st.caption("Procesare APEX + GSMNET → Comparație prețuri → Selecție furnizor optim")

# =========================
# CONEXIUNE DATABASE
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
# HELPERS COMUNE
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
    "code": ["product code", "product_code", "code", "cod", "code no", "prod code", "productcode", "partnumber"],
    "name": ["product name", "product_name", "name", "nume", "denumire", "description", "descriere"],
    "qty": ["quantity", "qty", "quant", "q-ty", "qnty", "stock"],
    "eur": ["euro price", "euro pri", "eur price", "euro", "eur", "price(€)", "price €", "€ price", "price eur", "price"],
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

# =========================
# FUNCȚII APEX
# =========================
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
        load_apex_exclude_codes.clear()
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
        load_apex_exclude_codes.clear()
        return True
    except Exception as e:
        st.error(f"❌ Eroare la ștergere excluderi: {e}")
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

def save_apex_normalized_to_db(df: pd.DataFrame) -> bool:
    """Salvează datele APEX normalizate în tabelul apex_normalized."""
    conn = get_db_connection()
    if not conn:
        st.error("❌ Nu pot salva - lipsește conexiunea DB")
        return False

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM apex_normalized;")
        deleted_count = cursor.rowcount
        conn.commit()
        st.info(f"🗑️ Șterse {deleted_count} înregistrări vechi din apex_normalized")

        insert_query = """
        INSERT INTO apex_normalized
        (cod_raw, cod, nume_apex, cantitate, pret_eur, pret_lei, order_hint, import_timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """

        insert_data = []
        total_rows = len(df)
        progress_bar = st.progress(0, text="Pregătire date pentru salvare...")

        for idx, row in df.iterrows():
            pret_eur_val = row.get("pret_eur", "0")
            pret_lei_val = row.get("pret_lei", "0")

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

            if (idx + 1) % max(1, total_rows // 10) == 0 or idx == total_rows - 1:
                progress_pct = int((idx + 1) / total_rows * 100)
                progress_bar.progress((idx + 1) / total_rows,
                    text=f"Pregătire date: {idx + 1}/{total_rows} ({progress_pct}%)")

        progress_bar.progress(1.0, text="Date pregătite! ✓")

        st.info("💾 Scriere în baza de date...")
        write_progress = st.progress(0, text="Salvare în BD...")
        batch_size = 1000
        total_batches = (len(insert_data) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(insert_data), batch_size):
            batch = insert_data[batch_idx:batch_idx + batch_size]
            cursor.executemany(insert_query, batch)
            conn.commit()

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

# =========================
# FUNCȚII GSMNET (NOI)
# =========================
def read_gsmnet_file(file) -> pd.DataFrame:
    """
    Citește fișierul GSMNET cu structură:
    - Header pe rândul 10 (index 9)
    - Coloana 1: Partnumber (SKU)
    - Coloana 3: Name
    - Coloana 4: Stock
    - Coloana 5: Price (EUR)
    """
    try:
        df = pd.read_excel(file, header=9)

        if df.shape[1] < 6:
            st.error(f"Fișierul GSMNET nu are suficiente coloane (găsite {df.shape[1]}, necesare minim 6)")
            return pd.DataFrame()

        # Selectăm coloanele prin poziție
        result = pd.DataFrame({
            'sku': df.iloc[:, 1],      # Coloana Partnumber (index 1)
            'name': df.iloc[:, 3],     # Coloana Name (index 3)
            'stock': df.iloc[:, 4],    # Coloana Stock (index 4)
            'price_eur': df.iloc[:, 5] # Coloana Price (index 5)
        })

        # Curățăm SKU
        result['sku'] = result['sku'].astype(str).str.strip()
        result = result[result['sku'].str.strip() != '']
        result = result[result['sku'] != 'nan']
        result = result[result['sku'] != 'None']

        # Curățăm numele
        result['name'] = result['name'].astype(str).str.strip()

        # Convertim valorile numerice
        result['stock'] = pd.to_numeric(result['stock'], errors='coerce').fillna(0)
        result['price_eur'] = pd.to_numeric(result['price_eur'], errors='coerce').fillna(0)

        # Calculăm prețul în LEI
        result['price_lei'] = (result['price_eur'] * float(EUR_TO_RON)).round(2)

        st.info(f"📊 GSMNET: încărcate {len(result)} produse")
        return result

    except Exception as e:
        st.error(f"❌ Eroare la citire GSMNET: {e}")
        import traceback
        st.error(f"Detalii: {traceback.format_exc()}")
        return pd.DataFrame()

def save_gsmnet_to_db(df: pd.DataFrame) -> bool:
    """Salvează datele GSMNET în tabelul gsmnet_normalized."""
    conn = get_db_connection()
    if not conn:
        st.error("❌ Nu pot salva - lipsește conexiunea DB")
        return False

    try:
        cursor = conn.cursor()

        # Șterge datele existente
        cursor.execute("DELETE FROM gsmnet_normalized;")
        deleted_count = cursor.rowcount
        conn.commit()
        st.info(f"🗑️ Șterse {deleted_count} înregistrări vechi din gsmnet_normalized")

        # Insert datele noi
        insert_query = """
        INSERT INTO gsmnet_normalized
        (sku, name, stock, price_eur, price_lei, import_timestamp)
        VALUES (%s, %s, %s, %s, %s, %s)
        """

        insert_data = []
        total_rows = len(df)
        progress_bar = st.progress(0, text="Pregătire date GSMNET...")

        for idx, row in df.iterrows():
            insert_data.append((
                str(row.get("sku", "")),
                str(row.get("name", "")),
                float(row.get("stock", 0)),
                float(row.get("price_eur", 0)),
                float(row.get("price_lei", 0)),
                datetime.now()
            ))

            if (idx + 1) % max(1, total_rows // 10) == 0 or idx == total_rows - 1:
                progress_pct = int((idx + 1) / total_rows * 100)
                progress_bar.progress((idx + 1) / total_rows,
                    text=f"Pregătire date: {idx + 1}/{total_rows} ({progress_pct}%)")

        progress_bar.progress(1.0, text="Date pregătite! ✓")

        st.info("💾 Scriere GSMNET în baza de date...")
        write_progress = st.progress(0, text="Salvare în BD...")
        batch_size = 1000
        total_batches = (len(insert_data) + batch_size - 1) // batch_size

        for batch_idx in range(0, len(insert_data), batch_size):
            batch = insert_data[batch_idx:batch_idx + batch_size]
            cursor.executemany(insert_query, batch)
            conn.commit()

            current_batch = (batch_idx // batch_size) + 1
            progress_pct = int(current_batch / total_batches * 100)
            write_progress.progress(min(current_batch / total_batches, 1.0),
                text=f"Salvat batch {current_batch}/{total_batches} ({progress_pct}%)")

        write_progress.progress(1.0, text="Salvare completă! ✓")
        st.success(f"✅ Salvate {len(insert_data)} înregistrări noi în gsmnet_normalized")

        cursor.close()
        conn.close()
        return True

    except Exception as e:
        st.error(f"❌ Eroare la salvare GSMNET în BD: {e}")
        import traceback
        st.error(f"Detalii: {traceback.format_exc()}")
        if conn:
            conn.rollback()
            conn.close()
        return False

def normalize_gsmnet_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalizează datele GSMNET (aplicăcanon_sku)."""
    df = df.copy()
    df['sku_canon'] = df['sku'].apply(canon_sku)
    return df

# =========================
# SMARTBILL
# =========================
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
        df = pd.read_excel(file, header=9)

        if df.shape[1] < 8:
            st.error(f"Fișierul SmartBill nu are suficiente coloane (găsite {df.shape[1]}, necesare minim 8)")
            return pd.DataFrame()

        result = pd.DataFrame({
            'cod': df.iloc[:, 2],
            'stoc initial': df.iloc[:, 4],
            'intrari': df.iloc[:, 5],
            'iesiri': df.iloc[:, 6],
            'stoc final': df.iloc[:, 7]
        })

        result['cod'] = result['cod'].astype(str).str.strip()
        result = result[result['cod'].str.strip() != '']
        result = result[result['cod'] != 'nan']
        result = result[result['cod'] != 'None']

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
# UI INPUT FILES
# =========================
st.subheader("📁 Fișiere de intrare")

c1, c2, c3 = st.columns(3)

with c1:
    apex_file = st.file_uploader("Fișier APEX (.xlsx / .xls / .csv)", type=["xlsx", "xls", "csv"], key="apex_raw")

with c2:
    gsmnet_file = st.file_uploader("Fișier GSMNET (.xlsx)", type=["xlsx"], key="gsmnet_raw")

with c3:
    smartbill_file = st.file_uploader("Fișier SmartBill (.xlsx)", type=["xlsx", "xls"], key="smartbill")

# =========================
# PROCESARE APEX
# =========================
apex_df_normalized = None

if apex_file:
    st.markdown("---")
    st.markdown("### 📊 Pas 1A — Normalizare APEX")

    try:
        apex_raw = read_any_apex(apex_file)
        apex_trim = normalize_apex_columns(apex_raw)
        apex_df_normalized_raw = expand_apex_rows(apex_trim)
    except Exception as e:
        st.error(f"Eroare la normalizare APEX: {e}")
        st.stop()

    # Aplică excluderile
    exclude_codes = load_apex_exclude_codes()
    apex_df_normalized_raw["cod_canon"] = apex_df_normalized_raw["cod"].map(canon_sku)
    mask_not_excluded = ~apex_df_normalized_raw["cod_canon"].isin(exclude_codes)

    apex_df_normalized = apex_df_normalized_raw[mask_not_excluded].reset_index(drop=True)
    apex_excluded = apex_df_normalized_raw[~mask_not_excluded].reset_index(drop=True)

    if not apex_excluded.empty:
        st.info(f"🚫 {len(apex_excluded)} produse APEX excluse (ignorate)")

    st.success(f"✅ APEX normalizat: {len(apex_df_normalized)} produse active")

    cols_show_norm = [c for c in ["cod", "nume_apex", "cantitate", "pret_eur", "pret_lei", "order_hint"]
                      if c in apex_df_normalized.columns]
    st.dataframe(apex_df_normalized[cols_show_norm].fillna(""), use_container_width=True)

    # UI pentru excluderi
    st.markdown("---")
    st.markdown("#### 🧹 Gestionare excluderi permanente APEX")

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

    if select_all:
        st.session_state.apex_exclude_selected = set(all_codes_current)

    current_selected_list = [c for c in all_codes_current if c in st.session_state.apex_exclude_selected]

    new_selected = st.multiselect(
        "Selectează produse de exclus permanent:",
        options=all_codes_current,
        default=current_selected_list,
        key="apex_exclude_multiselect"
    )

    st.session_state.apex_exclude_selected = set(new_selected)

    col_action1, col_action2 = st.columns([2, 2])

    with col_action1:
        if st.button("💾 Salvează în lista de excluderi", type="primary", use_container_width=True):
            to_save = sorted(st.session_state.apex_exclude_selected)
            if save_apex_exclude_codes(to_save):
                st.session_state.apex_exclude_selected = set()
                st.rerun()

    with col_action2:
        if st.button("👁️ Vezi toate excluderile active", use_container_width=True):
            st.session_state.show_excluded = not st.session_state.get("show_excluded", False)

    if st.session_state.get("show_excluded", False) and len(exclude_codes) > 0:
        st.markdown("##### 🚫 Coduri excluse permanent:")
        excluded_df = pd.DataFrame({"cod": sorted(list(exclude_codes))})
        st.dataframe(excluded_df, use_container_width=True)

        st.markdown("**Opțiuni de ștergere:**")
        col_del1, col_del2 = st.columns(2)

        with col_del1:
            codes_to_delete = st.multiselect(
                "Selectează coduri de șters:",
                options=sorted(list(exclude_codes)),
                key="delete_exclude_multiselect"
            )

            if st.button("🗑️ Șterge codurile selectate", use_container_width=True):
                if codes_to_delete:
                    if delete_apex_exclude_codes(codes_to_delete):
                        st.rerun()
                else:
                    st.warning("Selectează cel puțin un cod")

        with col_del2:
            st.write("")
            st.write("")
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
                        st.error(f"❌ Eroare: {e}")
                        if conn:
                            conn.rollback()
                            conn.close()

    # Butoane salvare APEX
    st.markdown("---")
    col_a, col_b = st.columns(2)

    with col_a:
        csv_buf = io.StringIO()
        apex_df_normalized.to_csv(csv_buf, index=False, quoting=csv.QUOTE_MINIMAL)
        st.download_button(
            "⬇️ Descarcă CSV APEX",
            data=csv_buf.getvalue(),
            file_name="apex_normalizat.csv",
            mime="text/csv"
        )

    with col_b:
        if st.button("💾 Salvează APEX în BD", type="primary"):
            save_apex_normalized_to_db(apex_df_normalized)

# =========================
# PROCESARE GSMNET
# =========================
gsmnet_df_normalized = None

if gsmnet_file:
    st.markdown("---")
    st.markdown("### 📊 Pas 1B — Normalizare GSMNET")

    try:
        gsmnet_df_raw = read_gsmnet_file(gsmnet_file)
        if not gsmnet_df_raw.empty:
            gsmnet_df_normalized = normalize_gsmnet_data(gsmnet_df_raw)
            st.success(f"✅ GSMNET normalizat: {len(gsmnet_df_normalized)} produse")

            cols_show_gsmnet = ["sku", "name", "stock", "price_eur", "price_lei"]
            st.dataframe(gsmnet_df_normalized[cols_show_gsmnet].fillna(""), use_container_width=True)

            # Butoane salvare GSMNET
            st.markdown("---")
            col_g1, col_g2 = st.columns(2)

            with col_g1:
                csv_buf_gsmnet = io.StringIO()
                gsmnet_df_normalized.to_csv(csv_buf_gsmnet, index=False, quoting=csv.QUOTE_MINIMAL)
                st.download_button(
                    "⬇️ Descarcă CSV GSMNET",
                    data=csv_buf_gsmnet.getvalue(),
                    file_name="gsmnet_normalizat.csv",
                    mime="text/csv"
                )

            with col_g2:
                if st.button("💾 Salvează GSMNET în BD", type="primary"):
                    save_gsmnet_to_db(gsmnet_df_normalized)
        else:
            st.error("Fișierul GSMNET este gol sau invalid")
    except Exception as e:
        st.error(f"Eroare la procesare GSMNET: {e}")
        import traceback
        st.error(f"Detalii: {traceback.format_exc()}")

# =========================
# RAPORT FINAL CU COMPARAȚIE
# =========================
if (apex_df_normalized is not None or gsmnet_df_normalized is not None) and smartbill_file:
    st.markdown("---")
    st.markdown("### 🔄 Pas 2 — Mapare pe catalog + comparație prețuri")

    # Încarcă mapping
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

    # SmartBill
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

    smart_df["cod_canon"] = smart_df["cod"].map(canon_sku)
    smart_df["cod_match"] = smart_df["cod_canon"].map(alt_to_principal).fillna(smart_df["cod_canon"])
    smart_grouped = smart_df.groupby("cod_match", as_index=False)[["iesiri", "stoc final"]].sum()

    # Procesare APEX
    apex_merged = None
    if apex_df_normalized is not None:
        apex_df = apex_df_normalized.copy()
        apex_df["cod_canon"] = apex_df["cod"].map(canon_sku)
        apex_df["cod_match"] = apex_df["cod_canon"].map(alt_to_principal).fillna(apex_df["cod_canon"])

        # Agregare pe SKU principal (luăm primul preț dacă sunt duplicate)
        apex_agg = apex_df.groupby("cod_match", as_index=False).agg({
            "cod": "first",
            "nume_apex": "first",
            "pret_eur": "first",
            "pret_lei": "first"
        }).rename(columns={
            "pret_eur": "apex_pret_eur",
            "pret_lei": "apex_pret_lei"
        })

        apex_merged = smart_grouped.merge(apex_agg, on="cod_match", how="outer")

    # Procesare GSMNET
    gsmnet_merged = None
    if gsmnet_df_normalized is not None:
        gsmnet_df = gsmnet_df_normalized.copy()
        gsmnet_df["cod_match"] = gsmnet_df["sku_canon"].map(alt_to_principal).fillna(gsmnet_df["sku_canon"])

        # Agregare pe SKU principal
        gsmnet_agg = gsmnet_df.groupby("cod_match", as_index=False).agg({
            "sku": "first",
            "name": "first",
            "price_eur": "first",
            "price_lei": "first"
        }).rename(columns={
            "price_eur": "gsmnet_pret_eur",
            "price_lei": "gsmnet_pret_lei"
        })

        gsmnet_merged = smart_grouped.merge(gsmnet_agg, on="cod_match", how="outer")

    # Merge final
    if apex_merged is not None and gsmnet_merged is not None:
        merged = apex_merged.merge(gsmnet_merged, on=["cod_match", "iesiri", "stoc final"], how="outer")
    elif apex_merged is not None:
        merged = apex_merged
    elif gsmnet_merged is not None:
        merged = gsmnet_merged
    else:
        st.error("Nu există date de la niciun furnizor!")
        st.stop()

    # Completare valori lipsă
    for col in ["iesiri", "stoc final"]:
        if col in merged.columns:
            merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)

    # Calculare comandă
    merged["comanda_cantitate"] = merged.apply(compute_order, axis=1)

    # Comparație prețuri și alegere furnizor
    def choose_supplier(row):
        """Alege furnizorul cu prețul cel mai mic."""
        apex_pret = row.get("apex_pret_eur", None)
        gsmnet_pret = row.get("gsmnet_pret_eur", None)

        # Convertim la float pentru comparație
        try:
            apex_val = float(apex_pret) if apex_pret is not None and not pd.isna(apex_pret) else None
        except:
            apex_val = None

        try:
            gsmnet_val = float(gsmnet_pret) if gsmnet_pret is not None and not pd.isna(gsmnet_pret) else None
        except:
            gsmnet_val = None

        if apex_val is not None and gsmnet_val is not None:
            if apex_val <= gsmnet_val:
                return "APEX"
            else:
                return "GSMNET"
        elif apex_val is not None:
            return "APEX"
        elif gsmnet_val is not None:
            return "GSMNET"
        else:
            return ""

    merged["furnizor_optim"] = merged.apply(choose_supplier, axis=1)
    merged["comanda_apex"] = merged.apply(lambda r: r["comanda_cantitate"] if r["furnizor_optim"] == "APEX" else "", axis=1)
    merged["comanda_gsmnet"] = merged.apply(lambda r: r["comanda_cantitate"] if r["furnizor_optim"] == "GSMNET" else "", axis=1)

    # Nume DB
    merged = merged.rename(columns={"cod_match": "SKU_principal"})
    merged["Produs_DB"] = merged["SKU_principal"].map(prim_to_name)

    # Afișare rezultat
    st.subheader("📦 Rezultat comandă cu comparație prețuri")

    show_cols = ["SKU_principal", "Produs_DB", "iesiri", "stoc final"]

    if "cod" in merged.columns:
        show_cols.insert(0, "cod")
    if "sku" in merged.columns and "cod" not in show_cols:
        show_cols.insert(0, "sku")

    if apex_df_normalized is not None:
        show_cols.extend(["apex_pret_eur", "apex_pret_lei", "comanda_apex"])

    if gsmnet_df_normalized is not None:
        show_cols.extend(["gsmnet_pret_eur", "gsmnet_pret_lei", "comanda_gsmnet"])

    show_cols.append("furnizor_optim")

    show_cols = [c for c in show_cols if c in merged.columns]

    st.dataframe(merged[show_cols].fillna(""), use_container_width=True)

    # Export CSV
    out_csv = io.StringIO()
    merged.to_csv(out_csv, index=False, quoting=csv.QUOTE_MINIMAL)
    st.download_button(
        "⬇️ Descarcă comandă comparată (CSV)",
        data=out_csv.getvalue(),
        file_name="comanda_furnizori_comparata.csv",
        mime="text/csv"
    )

    # Statistici
    st.markdown("---")
    st.subheader("📊 Statistici comparație")

    col_stat1, col_stat2, col_stat3 = st.columns(3)

    with col_stat1:
        total_produse = len(merged[merged["comanda_cantitate"] > 0])
        st.metric("Total produse de comandat", total_produse)

    with col_stat2:
        comenzi_apex = len(merged[merged["furnizor_optim"] == "APEX"])
        st.metric("Comenzi APEX", comenzi_apex)

    with col_stat3:
        comenzi_gsmnet = len(merged[merged["furnizor_optim"] == "GSMNET"])
        st.metric("Comenzi GSMNET", comenzi_gsmnet)

else:
    if not apex_file and not gsmnet_file:
        st.info("👆 Încarcă cel puțin un fișier furnizor (APEX sau GSMNET) pentru a începe")
    elif not smartbill_file:
        st.info("👆 Încarcă și fișierul SmartBill pentru a genera raportul de comandă")
