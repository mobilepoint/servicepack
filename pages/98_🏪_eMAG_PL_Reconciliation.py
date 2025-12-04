"""
eMAG P&L Reconciliation + Payout PDF Parser + Breakdown Parser

Toate funcționalitățile eMAG într-un singur loc - cu autentificare
"""

import streamlit as st
import pandas as pd
import pdfplumber
import re
import hashlib
import uuid
from datetime import datetime
from io import BytesIO
from sqlalchemy import text

# Setări pagină
st.set_page_config(
    page_title="eMAG Business Intelligence",
    page_icon="🏪",
    layout="wide"
)

# ═══════════════════════════════════════════════════════
# AUTENTIFICARE
# ═══════════════════════════════════════════════════════

def check_password():
    """Verifică dacă parola este corectă."""
    def password_entered():
        """Verifică parola introdusă."""
        if st.session_state["password"] == st.secrets["password"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input(
            "🔒 Parolă",
            type="password",
            on_change=password_entered,
            key="password"
        )
        st.stop()
    elif not st.session_state["password_correct"]:
        st.text_input(
            "🔒 Parolă",
            type="password",
            on_change=password_entered,
            key="password"
        )
        st.error("😕 Parolă incorectă")
        st.stop()

# Verificare parolă
check_password()

# ═══════════════════════════════════════════════════════
# CONEXIUNE POSTGRESQL
# ═══════════════════════════════════════════════════════

@st.cache_resource
def init_connection():
    """Inițializează conexiunea PostgreSQL Direct."""
    try:
        return st.connection("postgresql", type="sql")
    except Exception as e:
        st.error(f"⚠️ Eroare conexiune PostgreSQL: {e}")
        return None

conn = init_connection()

# ═══════════════════════════════════════════════════════
# HELPER FUNCTIONS - FILTRARE DATE
# ═══════════════════════════════════════════════════════

def detect_date_column(df):
    """Detectează automat coloana cu date."""
    for col in df.columns:
        col_lower = col.lower()
        if any(keyword in col_lower for keyword in ['date', 'data', 'time', 'timp', 'data_']):
            return col

    date_cols = df.select_dtypes(include=['datetime64']).columns.tolist()
    if date_cols:
        return date_cols[0]

    if len(df.columns) > 0:
        return df.columns[0]

    return None

def filter_year_2025_and_above(df, date_column):
    """Filtrează DataFrame-ul pentru a păstra doar înregistrările din 2025 sau după."""
    initial_count = len(df)

    try:
        df_work = df.copy()
        df_work[date_column] = pd.to_datetime(
            df_work[date_column],
            dayfirst=True,
            errors='coerce'
        )

        nat_count = df_work[date_column].isna().sum()
        if nat_count > 0:
            st.warning(f"⚠️ {nat_count} rânduri au date invalide și vor fi ignorate")

        df_filtered = df_work[
            (df_work[date_column].dt.year >= 2025) &
            (df_work[date_column].notna())
        ].copy()

        removed_count = initial_count - len(df_filtered)
        kept_count = len(df_filtered)

        return df_filtered, removed_count, kept_count
    except Exception as e:
        st.error(f"❌ Eroare la filtrare: {e}")
        return df, 0, len(df)

# ═══════════════════════════════════════════════════════
# HELPER FUNCTIONS - DATABASE OPERATIONS
# ═══════════════════════════════════════════════════════

def upload_pl_to_db(df, conn):
    """Uploadează datele P&L în PostgreSQL cu INSERT doar pentru rânduri noi."""
    stats = {
        'total_rows': len(df),
        'inserted': 0,
        'skipped': 0,
        'errors': 0,
        'error_details': []
    }
    batch_id = str(uuid.uuid4())

    try:
        df_prepared = df.copy()
        column_mapping = {
            'Data': 'data', 'Seller': 'seller', 'ID comanda': 'order_id',
            'ID produs': 'product_id', 'EAN': 'ean', 'Cod produs (PN)': 'cod_produs_pn',
            'PNK': 'pnk', 'Brand': 'brand', 'Produs': 'produs',
            'Tip desfasurator': 'tip_desfasurator', 'Cantitate': 'cantitate',
            'Vanzari': 'vanzari', 'Taxa livrare': 'taxa_livrare',
            'Taxa retur': 'taxa_retur', 'Valoare retinuta': 'valoare_retinuta',
            'Comision': 'comision', 'Comision anulate': 'comision_anulate',
            'Comision taxa livrare': 'comision_taxa_livrare', 'Depozitare FBE': 'depozitare_fbe',
            'Operatiuni FBE': 'operatiuni_fbe', 'Cost livrare': 'cost_livrare',
            'Cost retur': 'cost_retur', 'Vanzari nete': 'vanzari_nete'
        }
        df_prepared.rename(columns=column_mapping, inplace=True)
        df_prepared['data'] = pd.to_datetime(df_prepared['data'], dayfirst=True).dt.date
        df_prepared['upload_batch_id'] = batch_id
        df_prepared = df_prepared.where(pd.notna(df_prepared), None)

        insert_query = text("""
            INSERT INTO emag_order_lines (
                order_id, product_id, tip_desfasurator, data, seller, ean, cod_produs_pn, pnk, brand, produs,
                cantitate, vanzari, taxa_livrare, taxa_retur, valoare_retinuta, comision, comision_anulate,
                comision_taxa_livrare, depozitare_fbe, operatiuni_fbe, cost_livrare, cost_retur, vanzari_nete,
                upload_batch_id
            ) VALUES (
                :order_id, :product_id, :tip_desfasurator, :data, :seller, :ean, :cod_produs_pn, :pnk, :brand, :produs,
            :cantitate, :vanzari, :taxa_livrare, :taxa_retur, :valoare_retinuta, :comision, :comision_anulate,
                :comision_taxa_livrare, :depozitare_fbe, :operatiuni_fbe, :cost_livrare, :cost_retur, :vanzari_nete,
                :upload_batch_id
            )
            ON CONFLICT (order_id, product_id, tip_desfasurator) DO NOTHING;
        """)

        with conn.session as session:
            for idx, row in df_prepared.iterrows():
                try:
                    result = session.execute(insert_query, row.to_dict())
                    if result.rowcount > 0:
                        stats['inserted'] += 1
                    else:
                        stats['skipped'] += 1
                except Exception as e:
                    stats['errors'] += 1
                    stats['error_details'].append({'row': idx, 'order_id': row.get('order_id'), 'error': str(e)})
            session.commit()
        return stats
    except Exception as e:
        stats['errors'] = stats['total_rows']
        stats['error_details'].append({'global_error': str(e)})
        return stats

def get_db_stats(conn):
    """Returnează statistici despre datele din emag_order_lines."""
    try:
        query = """
            SELECT
                COALESCE(COUNT(*), 0) as total_rows,
                COALESCE(COUNT(DISTINCT order_id), 0) as total_orders,
                MIN(data) as min_date,
                MAX(data) as max_date,
                COALESCE(SUM(CASE WHEN tip_desfasurator = 'finalizata' THEN 1 ELSE 0 END), 0) as finalizate,
                COALESCE(SUM(CASE WHEN tip_desfasurator = 'stornata' THEN 1 ELSE 0 END), 0) as stornate,
                COALESCE(SUM(vanzari_nete), 0) as total_vanzari_nete
            FROM emag_order_lines;
        """
        result = conn.query(query)
        if len(result) > 0:
            stats = result.iloc[0].to_dict()
            for key in ['total_rows', 'total_orders', 'finalizate', 'stornate', 'total_vanzari_nete']:
                if stats.get(key) is None:
                    stats[key] = 0
            return stats
        else:
            return {
                'total_rows': 0, 'total_orders': 0, 'finalizate': 0, 'stornate': 0,
                'total_vanzari_nete': 0, 'min_date': None, 'max_date': None
            }
    except Exception as e:
        return {'error': str(e)}

# ═══════════════════════════════════════════════════════
# HELPER FUNCTIONS - PDF PARSING
# ═══════════════════════════════════════════════════════

def calculate_pdf_hash(pdf_bytes: bytes) -> str:
    """Calculează SHA256 hash pentru PDF."""
    return hashlib.sha256(pdf_bytes).hexdigest()

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrage tot textul din PDF."""
    full_text = ""
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
    except Exception as e:
        st.error(f"Eroare la extragere text: {e}")
    return full_text

def extract_total_amount(text: str) -> float:
    """Extrage suma totală de plată din PDF."""
    patterns = [
        r'Total\s+de\s+plata[:\s]+([0-9.,]+)\s*RON',
        r'Total\s+amount[:\s]+([0-9.,]+)\s*RON',
        r'TOTAL[:\s]+([0-9.,]+)\s*RON',
        r'Total[:\s]+([0-9.,]+)\s*RON',
        r'Suma\s+totala[:\s]+([0-9.,]+)\s*RON',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1).replace('.', '').replace(',', '.')
            try:
                return float(amount_str)
            except ValueError:
                continue
    return None

def extract_invoices(text: str) -> list:
    """Extrage toate numerele de facturi din PDF."""
    pattern = r'([A-Z]{1,4})-MKTP-(\d+)'
    invoices = []
    seen = set()
    lines = text.split('\n')
    
    for idx, line in enumerate(lines):
        matches = re.finditer(pattern, line)
        for match in matches:
            invoice_number = match.group(0)
            if invoice_number in seen:
                continue
            seen.add(invoice_number)
            
            invoice_type = match.group(1)
            amount = None
            
            # Îmbunătățit: caută suma în mai multe formate
            amount_patterns = [
                r'(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})\s*RON',  # 1.234,56 RON sau 1,234.56 RON
                r'RON\s*(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})',  # RON 1.234,56
                r'(\d+[.,]\d{2})\s*RON',                      # 123,45 RON simplu
                r'(\d+[.,]\d{2})$',                           # 123,45 la sfârșitul liniei
            ]
            
            for amt_pattern in amount_patterns:
                amount_match = re.search(amt_pattern, line)
                if amount_match:
                    amount_str = amount_match.group(1)
                    # Normalizează: elimină separatorii de mii, înlocuiește virgula cu punct
                    if ',' in amount_str and '.' in amount_str:
                        # Format european: 1.234,56
                        amount_str = amount_str.replace('.', '').replace(',', '.')
                    elif ',' in amount_str:
                        # Doar virgulă: 1234,56
                        amount_str = amount_str.replace(',', '.')
                    try:
                        amount = float(amount_str)
                        break
                    except ValueError:
                        continue
            
            invoices.append({
                'invoice_number': invoice_number,
                'invoice_type': invoice_type,
                'invoice_amount': amount,
                'position_in_pdf': idx + 1,
                'raw_line': line.strip()
            })
    
    return invoices


def extract_payout_info(text: str, filename: str) -> dict:
    """Extrage informații despre payout (ID, date)."""
    info = {
        'payout_id': None, 'payout_date': None,
        'reference_period_start': None, 'reference_period_end': None
    }
    payout_id_title = re.search(r'(\d{4}-\d{10})\s+(?:din|from)', text, re.IGNORECASE)
    if payout_id_title:
        info['payout_id'] = payout_id_title.group(1)
    if not info['payout_id']:
        filename_match = re.search(r'_(\d{10,})\.pdf', filename)
        if filename_match:
            info['payout_id'] = filename_match.group(1)
    if not info['payout_id']:
        payout_id_match = re.search(r'Payout\s+ID[:\s]+(\d+)', text, re.IGNORECASE)
        if payout_id_match:
            info['payout_id'] = payout_id_match.group(1)
    date_patterns = [
        r'(?:from|din)\s+(\d{2}\.\d{2}\.\d{4})',
        r'Data\s+platii?[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})',
        r'Payout\s+date[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            for date_format in ['%d.%m.%Y', '%d-%m-%Y', '%d/%m/%Y']:
                try:
                    info['payout_date'] = datetime.strptime(date_str, date_format).date()
                    break
                except ValueError:
                    continue
            if info['payout_date']:
                break
    return info

def parse_payout_pdf(pdf_bytes: bytes, filename: str) -> dict:
    """Parser principal pentru PDF payout."""
    pdf_hash = calculate_pdf_hash(pdf_bytes)
    text = extract_text_from_pdf(pdf_bytes)
    payout_info = extract_payout_info(text, filename)
    total_amount = extract_total_amount(text)
    invoices = extract_invoices(text)
    pages_count = 0
    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            pages_count = len(pdf.pages)
    except:
        pass
    return {
        'pdf_hash': pdf_hash, 'filename': filename, 'pages_count': pages_count,
        'payout_info': payout_info, 'total_amount': total_amount,
        'invoices': invoices, 'invoices_count': len(invoices)
    }

def save_payout_to_db(result, conn):
    """Salvează în DB un payout parsat."""
    stats = {"inserted_header": 0, "inserted_invoices": 0, "skipped_existing": False, "error": None}
    
    try:
        with conn.session as session:
            # Folosește pdf_hash (cu underscore) - consistent cu dicționarul result
            check_q = text("SELECT id FROM emag_payout_header WHERE pdf_hash = :pdf_hash")
            existing = session.execute(check_q, {"pdf_hash": result["pdf_hash"]}).fetchone()
            
            if existing:
                stats["skipped_existing"] = True
                return stats
            
            insert_header = text("""
                INSERT INTO emag_payout_header 
                (payout_id, payout_date, total_amount, pages_count, filename, pdf_hash)
                VALUES (:payout_id, :payout_date, :total_amount, :pages_count, :filename, :pdf_hash)
                RETURNING id;
            """)
            
            payout_info = result["payout_info"]
            header_id = session.execute(insert_header, {
                "payout_id": payout_info.get("payout_id"),
                "payout_date": payout_info.get("payout_date"),
                "total_amount": result.get("total_amount"),
                "pages_count": result.get("pages_count"),
                "filename": result.get("filename"),
                "pdf_hash": result.get("pdf_hash"),  # cu underscore!
            }).scalar()
            
            stats["inserted_header"] = 1
            
            invoices = result.get("invoices", []) or []
            if invoices:
                insert_inv = text("""
                    INSERT INTO emag_payout_invoices (
                        header_id, invoice_number, invoice_type, invoice_amount,
                        position_in_pdf, raw_line, invoice_label
                    )
                    VALUES (
                        :header_id, :invoice_number, :invoice_type, :invoice_amount,
                        :position_in_pdf, :raw_line, :invoice_label
                    );
                """)
                for inv in invoices:
                    session.execute(insert_inv, {
                        "header_id": header_id,
                        "invoice_number": inv.get("invoice_number"),
                        "invoice_type": inv.get("invoice_type"),
                        "invoice_amount": inv.get("invoice_amount"),
                        "position_in_pdf": inv.get("position_in_pdf"),
                        "raw_line": inv.get("raw_line"),
                         "invoice_label": inv.get("invoice_label"),
                    })
                stats["inserted_invoices"] = len(invoices)
            
            session.commit()
            return stats
            
    except Exception as e:
        stats["error"] = str(e)
        return stats


# ═══════════════════════════════════════════════════════
# MAIN APP
# ═══════════════════════════════════════════════════════

st.title("🏪 eMAG Business Intelligence")
st.markdown("**Central hub pentru toate operațiunile eMAG**")

if conn:
    st.success("✅ Conectat la PostgreSQL")
else:
    st.warning("⚠️ PostgreSQL nu este disponibil")

st.divider()

tab1, tab2, tab3 = st.tabs(["📊 Upload P&L", "📄 Payout PDF Parser", "📑 Breakdown Excel Parser"])

# ═══════════════════════════════════════════════════════
# TAB 1: UPLOAD P&L
# ═══════════════════════════════════════════════════════
with tab1:
    st.header("📊 Upload Profit & Loss")
    st.markdown("Uploadează fișierul Excel cu datele P&L de la eMAG")
    st.info("📌 **Notă**: Toate datele anterioare anului 2025 vor fi ignorate automat (format: zz/ll/aaaa)")
    uploaded_file = st.file_uploader("Selectează fișier Excel", type=['xlsx', 'xls'], key="pl_uploader")

    if uploaded_file:
        try:
            df = pd.read_excel(uploaded_file)
            st.success(f"✅ Fișier încărcat: {uploaded_file.name}")
            st.info(f"📋 Total rânduri inițiale: **{len(df)}**")
            date_column = detect_date_column(df)

            if date_column:
                st.info(f"🔍 Coloană date detectată: **{date_column}**")
                with st.expander("🔎 Vezi primele 3 date din fișier"):
                    st.write(df[date_column].head(3).tolist())
                df_filtered, removed, kept = filter_year_2025_and_above(df, date_column)
                if removed > 0:
                    st.warning(f"🗑️ **{removed} rânduri eliminate** (< 2025 sau invalide)  |  ✅ **{kept} rânduri păstrate** (2025+)")
                else:
                    st.success(f"✅ Toate {kept} rândurile sunt din 2025+")
                df = df_filtered
            else:
                st.warning("⚠️ Nu am găsit coloană cu date. Selectează manual:")
                selected_col = st.selectbox("Selectează coloana cu date:", df.columns.tolist())
                if selected_col:
                    df, removed, kept = filter_year_2025_and_above(df, selected_col)
                    if removed > 0:
                        st.warning(f"🗑️ {removed} eliminate | ✅ {kept} păstrate")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("📋 Rânduri (după filtrare)", len(df))
            with col2:
                st.metric("📊 Coloane", len(df.columns))
            with col3:
                st.metric("💾 Dimensiune", f"{uploaded_file.size / 1024:.1f} KB")

            st.divider()
            st.subheader("👀 Preview Date (primele 10 rânduri)")
            st.dataframe(df.head(10), width='stretch')
            st.divider()
            numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
            if numeric_cols:
                st.subheader("📊 Statistici coloane numerice")
                st.dataframe(df[numeric_cols].describe(), width='stretch')

            st.divider()
            st.subheader("📊 Statistici bază de date")
            if conn:
                db_stats = get_db_stats(conn)
                if 'error' not in db_stats:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("📋 Total rânduri în DB", f"{int(db_stats.get('total_rows', 0)):,}")
                    with col2:
                        st.metric("🛒 Total comenzi", f"{int(db_stats.get('total_orders', 0)):,}")
                    with col3:
                        st.metric("✅ Finalizate", f"{int(db_stats.get('finalizate', 0)):,}")
                    with col4:
                        st.metric("🔄 Stornate", f"{int(db_stats.get('stornate', 0)):,}")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if db_stats.get('min_date'):
                            st.metric("📅 Prima comandă", str(db_stats['min_date']))
                    with col2:
                        if db_stats.get('max_date'):
                            st.metric("📅 Ultima comandă", str(db_stats['max_date']))
                    with col3:
                        vanzari = float(db_stats.get('total_vanzari_nete', 0))
                        if vanzari != 0:
                            st.metric("💰 Total vânzări nete", f"{vanzari:,.2f} RON")
                else:
                    st.warning(f"⚠️ Eroare statistici: {db_stats.get('error')}")
            else:
                st.info("ℹ️ Conectează la DB pentru statistici")

            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                if st.button("💾 Salvează în DB", type="primary", key="save_pl"):
                    if conn:
                        with st.spinner("💾 Salvare în PostgreSQL..."):
                            upload_stats = upload_pl_to_db(df, conn)
                            if upload_stats['errors'] == 0:
                                st.success(f"✅ **Upload finalizat cu succes!**\n\n"
                                           f"📊 **{upload_stats['inserted']}** rânduri noi inserate\n"
                                           f"⏭️ **{upload_stats['skipped']}** rânduri ignorate (duplicate)\n"
                                           f"📋 **{upload_stats['total_rows']}** total procesate")
                                st.balloons()
                                st.rerun()
                            else:
                                st.warning(f"⚠️ **Upload completat cu erori**\n\n"
                                           f"✅ **{upload_stats['inserted']}** inserate\n"
                                           f"⏭️ **{upload_stats['skipped']}** ignorate\n"
                                           f"❌ **{upload_stats['errors']}** erori")
                                if upload_stats['error_details']:
                                    with st.expander("📋 Detalii erori"):
                                        for err in upload_stats['error_details'][:10]:
                                            st.error(f"Row {err.get('row')}: {err.get('error')}")
                    else:
                        st.error("❌ DB nu este conectat")
            with col2:
                if st.button("📊 Generează raport", key="report_pl"):
                    st.info("🚧 Funcționalitate în dezvoltare")

            st.divider()
            st.subheader("💾 Exportare date filtrate")
            csv_buffer = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Descarcă CSV (filtrat 2025+)",
                data=csv_buffer,
                file_name=f"PL_filtrat_2025_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
        except Exception as e:
            st.error(f"❌ Eroare la citire: {str(e)}")
            with st.expander("📋 Detalii eroare"):
                import traceback
                st.code(traceback.format_exc())
    else:
        st.info("👆 Uploadează un fișier Excel pentru a începe")

# ═══════════════════════════════════════════════════════
# TAB 2: PAYOUT PDF PARSER
# ═══════════════════════════════════════════════════════
with tab2:
    st.header("📄 Payout PDF Parser")
    st.markdown("""
    Uploadează PDF-ul de payout de la eMAG pentru a extrage:
    - 💰 **Suma totală** de plată
    - 📋 **Lista facturilor** (C-MKTP, V-MKTP, etc.)
    - 📅 **Date și perioade** de referință
    """)
    st.divider()
    uploaded_pdf = st.file_uploader(
        "Selectează PDF payout", type=['pdf'], key="pdf_uploader",
        help="Uploadează avizul de plată (payout notice) de la eMAG"
    )

    if uploaded_pdf:
        pdf_bytes = uploaded_pdf.read()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📁 Fișier", uploaded_pdf.name)
        with col2:
            st.metric("📊 Dimensiune", f"{len(pdf_bytes) / 1024:.1f} KB")
        with col3:
            file_hash = calculate_pdf_hash(pdf_bytes)
            st.metric("🔑 Hash", file_hash[:12] + "...")
        st.divider()

        with st.spinner("🔍 Parsez PDF-ul..."):
            try:
                result = parse_payout_pdf(pdf_bytes, uploaded_pdf.name)
                st.success("✅ PDF parsat cu succes!")
                st.subheader("📊 Informații Payout")

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    payout_id = result['payout_info'].get('payout_id')
                    if payout_id:
                        st.metric("🆔 Payout ID", payout_id)
                    else:
                        st.warning("❌ Payout ID nu a fost găsit")
                with col2:
                    payout_date = result['payout_info'].get('payout_date')
                    if payout_date:
                        st.metric("📅 Data plății", payout_date.strftime("%d.%m.%Y"))
                    else:
                        st.warning("❌ Data nu a fost găsită")
                with col3:
                    total = result.get('total_amount')
                    if total:
                        st.metric("💰 Total", f"{total:,.2f} RON")
                    else:
                        st.warning("❌ Total nu a fost găsit")
                with col4:
                    st.metric("📄 Facturi", result['invoices_count'])

                st.divider()
                st.subheader(f"📋 Facturi Găsite ({result['invoices_count']})")
                if result['invoices']:
                    invoice_types = {}
                    for inv in result['invoices']:
                        inv_type = inv['invoice_type']
                        if inv_type not in invoice_types:
                            invoice_types[inv_type] = []
                        invoice_types[inv_type].append(inv)

                    type_labels = {'C': '💼 Comisioane', 'V': '🎟️ Vouchere', 'Y': '🔄 Retururi', 'A': '📢 Ads', 'D': '📦 Diverse'}
                    tabs = st.tabs([f"{type_labels.get(t, t)} ({len(invoices)})" for t, invoices in invoice_types.items()])

                    for idx, (inv_type, invoices) in enumerate(invoice_types.items()):
                        with tabs[idx]:
                            df_inv = pd.DataFrame([{
                                'Număr factură': inv['invoice_number'],
                                'Sumă (RON)': f"{inv['invoice_amount']:,.2f}" if inv['invoice_amount'] else 'N/A',
                                'Poziție': inv['position_in_pdf'],
                                'Linia din PDF': inv['raw_line'][:80] + '...' if len(inv['raw_line']) > 80 else inv['raw_line']
                            } for inv in invoices])
                            st.dataframe(df_inv, width='stretch', hide_index=True)
                else:
                    st.warning("⚠️ Nu am găsit facturi în PDF.")

                st.divider()
                with st.expander("🔧 Debug Info"):
                    st.json({
                        'pdf_hash': result['pdf_hash'],
                        'pages_count': result['pages_count'],
                        'payout_info': {
                            'payout_id': result['payout_info'].get('payout_id'),
                            'payout_date': str(result['payout_info'].get('payout_date'))
                        },
                        'total_amount': result['total_amount'],
                        'invoices_count': result['invoices_count'],
                        'invoices': [
                            {
                                'invoice_number': inv['invoice_number'],
                                'invoice_type': inv['invoice_type'],
                                'invoice_amount': inv['invoice_amount'],
                                'label': inv.get('invoice_label')
                            } for inv in result['invoices']
                        ]
                    })


                st.divider()
                st.subheader("⚡ Acțiuni")
                col1, col2, col3 = st.columns(3)

                with col1:
                    if st.button("💾 Salvează payout în DB", type="primary", disabled=not conn, key="save_payout_db"):
                        if conn:
                            with st.spinner("💾 Salvez payout-ul în PostgreSQL..."):
                                save_stats = save_payout_to_db(result, conn)
                                if save_stats.get("error"):
                                    st.error(f"❌ Eroare la salvare: {save_stats['error']}")
                                elif save_stats.get("skipped_existing"):
                                    st.info("ℹ️ Acest payout există deja în DB (identificat după PDF hash). Nu am inserat din nou.")
                                else:
                                    st.success(f"✅ Payout salvat cu succes!\n\n📄 1 header inserat\n📋 {save_stats['inserted_invoices']} facturi inserate")
                        else:
                            st.error("❌ DB nu este conectat")
                with col2:
                    if st.button("🔍 Reconciliază cu Excel", disabled=True, key="reconcile_pdf"):
                        st.info("🚧 Funcționalitate în dezvoltare")
                with col3:
                    if st.button("📊 Raport complet", disabled=True, key="report_pdf"):
                        st.info("🚧 Funcționalitate în dezvoltare")

            except Exception as e:
                st.error(f"❌ Eroare la parsare: {str(e)}")
                with st.expander("📋 Detalii eroare"):
                    import traceback
                    st.code(traceback.format_exc())
    else:
        st.info("👆 Uploadează un PDF pentru a începe parsarea")

# ═══════════════════════════════════════════════════════
# HELPER FUNCTIONS - BREAKDOWN PARSING
# ═══════════════════════════════════════════════════════

def detect_breakdown_type_from_payout(invoice_number: str,
                                      invoice_type: str | None,
                                      invoice_label: str | None) -> str:
    """
    Determină tipul desfășurătorului (DC/DV/DP/DY/DHDR/DED/CO/COD)
    pe baza numărului de factură, a tipului și a descrierii din payout.
    """
    num = (invoice_number or "").upper()
    t = (invoice_type or "").upper()
    label = (invoice_label or "").lower()

    # 1. Reguli clasice după prefix factură
    if num.startswith("C-MKTP") or t == "C":
        return "DC"          # comisioane
    if num.startswith("V-MKTP") or t == "V":
        return "DV"          # vouchere
    if num.startswith("Y-MKTP") or t == "Y":
        return "DY"          # retururi
    if num.startswith("H-MKTP") or t == "H":
        return "DHDR"        # compensări / daune
    if num.startswith("E-MKTP") or t == "E":
        return "DP"          # încasări payout standard
    if "STORNO" in label or "stornare" in label:
        return "DCS"         # stornări comision
    if "livrare" in label or "delivery" in label or "expediere" in label:
        return "DED"         # delivery

    # 2. Facturi fără prefix, doar descriere (încasări ramburs / card etc.)
    if "ramburs" in label or "cash on delivery" in label or "cod" in label:
        return "DP_COD"      # încasări ramburs – legăm de fișierele DP COD
    if "card" in label or "online card" in label or "incasari card" in label:
        return "DP_CARD"     # încasări card – legăm de fișierele DP CO
#     if 'co ' in filename_lower or 'online_card' in filename_lower:
#     return 'DP_CARD'
#     if 'cod ' in filename_lower or 'cash_on_delivery' in filename_lower:
#     return 'DP_COD'

    # 3. Fallback
    return "UNKNOWN"



def detect_breakdown_type(filename: str) -> str:
    """Detectează tipul fișierului din nume (ex: DC, DV, DP, DY, DP_CARD, DP_COD)."""
    filename_lower = filename.lower()
    
    # Logic retrieved from misplaced code
    if 'co ' in filename_lower or 'online_card' in filename_lower:
        return 'DP_CARD'
    if 'cod ' in filename_lower or 'cash_on_delivery' in filename_lower:
        return 'DP_COD'
        
    # Standard types based on prefixes/substrings
    if 'dc' in filename_lower: return 'DC'
    if 'dv' in filename_lower: return 'DV'
    if 'dy' in filename_lower: return 'DY'
    if 'dhdr' in filename_lower: return 'DHDR'
    if 'ded' in filename_lower: return 'DED'
    if 'dp' in filename_lower: return 'DP'
    
    return 'UNKNOWN'

def parse_breakdown_excel(file_bytes, filename: str, breakdown_type: str) -> pd.DataFrame:
    """Parsează un fișier Excel desfășurător și returnează DataFrame normalizat."""
    try:
        df = pd.read_excel(file_bytes)
        
        # Elimină rânduri goale sau header duplicat
        df = df[df.iloc[:, 0].notna()]
        
        # Mapare coloane comune
        df_normalized = pd.DataFrame()
        
        # Coloane comune pentru toate tipurile
        if 'ID comanda' in df.columns:
            df_normalized['order_id'] = df['ID comanda'].astype(str)
        elif 'Order ID' in df.columns:
            df_normalized['order_id'] = df['Order ID'].astype(str)
        
        if 'OLID' in df.columns:
            df_normalized['olid'] = df['OLID'].astype(str)
        elif 'OFID' in df.columns:
            df_normalized['olid'] = df['OFID'].astype(str)
        
        if 'PNK' in df.columns:
            df_normalized['pnk'] = df['PNK']
        
        if 'Part number' in df.columns:
            df_normalized['part_number'] = df['Part number']
        elif 'Part Number' in df.columns:
            df_normalized['part_number'] = df['Part Number']
        
        if 'Brand' in df.columns:
            df_normalized['brand'] = df['Brand']
        
        if 'Nume produs' in df.columns:
            df_normalized['nume_produs'] = df['Nume produs']
        
        if 'Data comanda' in df.columns:
            df_normalized['data_comanda'] = pd.to_datetime(df['Data comanda'], errors='coerce')
        elif 'Order date' in df.columns:
            df_normalized['data_comanda'] = pd.to_datetime(df['Order date'], errors='coerce')
        
        if 'Data finalizare comanda' in df.columns:
            df_normalized['data_finalizare'] = pd.to_datetime(df['Data finalizare comanda'], errors='coerce')
        elif 'Order finalization date' in df.columns:
            df_normalized['data_finalizare'] = pd.to_datetime(df['Order finalization date'], errors='coerce')
        
        if 'Cantitate' in df.columns:
            df_normalized['cantitate'] = pd.to_numeric(df['Cantitate'], errors='coerce')
        
        if 'Mod plata' in df.columns:
            df_normalized['mod_plata'] = df['Mod plata']
        elif 'Payment method' in df.columns:
            df_normalized['mod_plata'] = df['Payment method']
        
        # Coloane specifice tipului
        if breakdown_type in ['DC', 'DCS']:
            if 'Valoare produse' in df.columns:
                df_normalized['valoare_produse'] = pd.to_numeric(df['Valoare produse'], errors='coerce')
            if 'Comision Net' in df.columns:
                df_normalized['comision_net'] = pd.to_numeric(df['Comision Net'], errors='coerce')
            if 'Valoare vouchere' in df.columns:
                df_normalized['valoare_vouchere'] = pd.to_numeric(df['Valoare vouchere'], errors='coerce')
        
        elif breakdown_type == 'DV':
            if 'Valoare vouchere' in df.columns:
                df_normalized['valoare_vouchere'] = pd.to_numeric(df['Valoare vouchere'], errors='coerce')
        
        elif breakdown_type == 'DP':
            if 'Fraction value' in df.columns:
                df_normalized['valoare_plata'] = pd.to_numeric(df['Fraction value'], errors='coerce')
        
        elif breakdown_type == 'DY':
            if 'Valoare produse' in df.columns:
                df_normalized['valoare_produse'] = pd.to_numeric(df['Valoare produse'], errors='coerce')
            if 'Valoare vouchere' in df.columns:
                df_normalized['valoare_vouchere'] = pd.to_numeric(df['Valoare vouchere'], errors='coerce')
        
        elif breakdown_type == 'DHDR':
            if 'Valoare compensata' in df.columns:
                df_normalized['valoare_plata'] = pd.to_numeric(df['Valoare compensata'], errors='coerce')
        
        elif breakdown_type == 'DED':
            if 'Valoare produs' in df.columns:
                df_normalized['valoare_produse'] = pd.to_numeric(df['Valoare produs'], errors='coerce')
        
        df_normalized['breakdown_type'] = breakdown_type
        df_normalized['filename'] = filename
        
        return df_normalized
        
    except Exception as e:
        st.error(f"Eroare la parsare {filename}: {str(e)}")
        return pd.DataFrame()

def save_breakdown_to_db(df: pd.DataFrame, payout_id: str, invoice_number: str, conn) -> dict:
    """Salvează desfășurătorul în DB."""
    stats = {'inserted': 0, 'errors': 0, 'error_details': []}
    
    try:
        df['payout_id'] = payout_id
        df['invoice_number'] = invoice_number
        
        insert_query = text("""
            INSERT INTO emag_breakdown_lines (
                payout_id, invoice_number, breakdown_type, filename,
                order_id, olid, pnk, part_number, brand, nume_produs,
                data_comanda, data_finalizare, cantitate,
                valoare_produse, comision_net, valoare_vouchere, valoare_plata, mod_plata
            ) VALUES (
                :payout_id, :invoice_number, :breakdown_type, :filename,
                :order_id, :olid, :pnk, :part_number, :brand, :nume_produs,
                :data_comanda, :data_finalizare, :cantitate,
                :valoare_produse, :comision_net, :valoare_vouchere, :valoare_plata, :mod_plata
            );
        """)
        
        with conn.session as session:
            for idx, row in df.iterrows():
                try:
                    session.execute(insert_query, {
                        'payout_id': row.get('payout_id'),
                        'invoice_number': row.get('invoice_number'),
                        'breakdown_type': row.get('breakdown_type'),
                        'filename': row.get('filename'),
                        'order_id': row.get('order_id'),
                        'olid': row.get('olid'),
                        'pnk': row.get('pnk'),
                        'part_number': row.get('part_number'),
                        'brand': row.get('brand'),
                        'nume_produs': row.get('nume_produs'),
                        'data_comanda': row.get('data_comanda'),
                        'data_finalizare': row.get('data_finalizare'),
                        'cantitate': row.get('cantitate'),
                        'valoare_produse': row.get('valoare_produse'),
                        'comision_net': row.get('comision_net'),
                        'valoare_vouchere': row.get('valoare_vouchere'),
                        'valoare_plata': row.get('valoare_plata'),
                        'mod_plata': row.get('mod_plata')
                    })
                    stats['inserted'] += 1
                except Exception as e:
                    stats['errors'] += 1
                    stats['error_details'].append({'row': idx, 'error': str(e)})
            
            session.commit()
        
        return stats
        
    except Exception as e:
        stats['errors'] = len(df)
        stats['error_details'].append({'global_error': str(e)})
        return stats

def get_payout_list(conn):
    """Returnează lista de payout-uri din DB."""
    try:
        query = """
            SELECT payout_id, payout_date, total_amount, filename
            FROM emag_payout_header
            ORDER BY payout_date DESC, payout_id DESC;
        """
        result = conn.query(query)
        return result
    except Exception as e:
        st.error(f"Eroare la listare payout-uri: {str(e)}")
        return pd.DataFrame()

def get_payout_invoices(payout_id: str, conn):
    """Returnează facturile pentru un payout."""
    try:
        query = text("""
            SELECT 
                i.invoice_number,
                i.invoice_type,
                i.invoice_amount,
                i.invoice_label
            FROM emag_payout_invoices i
            JOIN emag_payout_header h ON h.id = i.header_id
            WHERE h.payout_id = :payout_id
            ORDER BY i.invoice_type NULLS LAST, i.invoice_number;
        """)
        with conn.session as session:
            result = session.execute(query, {'payout_id': payout_id})
            return pd.DataFrame(result.fetchall(), 
                columns=['invoice_number', 'invoice_type', 'invoice_amount', 'invoice_label'])
    except Exception as e:
        st.error(f"Eroare la citire facturi: {str(e)}")
        return pd.DataFrame()

# ═══════════════════════════════════════════════════════
# TAB 3: BREAKDOWN EXCEL PARSER
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("📑 Breakdown Excel Parser")
    st.markdown("""
        **Workflow reconciliere:**
        1. Selectează payout-ul din listă
        2. Uploadează desfășurătoarele Excel pentru fiecare factură
        3. Sistemul verifică automat reconcilierea
        4. Vezi raportul de profit final
    """)
    
    st.divider()
    
    if not conn:
        st.warning("⚠️ PostgreSQL nu este disponibil")
        st.stop()
    
    # STEP 1: Selectează payout
    st.subheader("1️⃣ Selectează Payout")
    
    payouts = get_payout_list(conn)
    
    if len(payouts) == 0:
        st.info("📭 Nu există payout-uri în DB. Uploadează mai întâi un PDF în Tab 2.")
        st.stop()
    
    # Creează opțiuni pentru selectbox
    payout_options = [
        f"{row['payout_id']} - {row['payout_date']} ({row['total_amount']:.2f} RON)"
        for _, row in payouts.iterrows()
    ]
    
    selected_payout_str = st.selectbox(
        "Alege payout-ul:",
        options=payout_options,
        key="selected_payout"
    )
    
    if selected_payout_str:
        selected_payout_id = selected_payout_str.split(' - ')[0]
        
        # Afișează facturile pentru payout-ul selectat
        st.subheader(f"📋 Facturi pentru payout {selected_payout_id}")
        
        invoices_df = get_payout_invoices(selected_payout_id, conn)
        
        if len(invoices_df) > 0:
            # Mapare tipuri facturi
            type_mapping = {
                'C': '💼 Comisioane',
                'V': '🎟️ Vouchere',
                'E': '💰 Încasări',
                'Y': '🔄 Retururi',
                'H': '⚖️ Compensări',
                'D': '📦 Diverse'
            }
            
            invoices_df['Tip'] = invoices_df['invoice_type'].map(type_mapping)
            invoices_df['Sumă'] = invoices_df['invoice_amount'].apply(
                lambda x: f"{x:,.2f} RON" if pd.notna(x) else 'N/A'
            )
            
            invoices_df['Label'] = invoices_df['invoice_label'].fillna('').str.slice(0, 60)
            st.dataframe(
                invoices_df[['invoice_number', 'Tip', 'Sumă', 'Label']],
                width='stretch',
                hide_index=True
            )
            
            st.divider()
            
            # STEP 2: Upload desfășurătoare
            st.subheader("2️⃣ Uploadează Desfășurătoare")
            
            st.info(f"""
                📌 **Ghid upload:**
                - Uploadează câte un fișier Excel pentru fiecare factură
                - Sistemul detectează automat tipul din nume (dc, dv, dp, dy, etc.)
                - Poți uploada toate desfășurătoarele simultan
            """)
            
            uploaded_breakdowns = st.file_uploader(
                "Selectează fișiere Excel (poți selecta multiple)",
                type=['xlsx', 'xls'],
                accept_multiple_files=True,
                key="breakdown_uploader"
            )
            
            if uploaded_breakdowns:
                st.success(f"✅ {len(uploaded_breakdowns)} fișiere încărcate")
                
                # Preview fișiere
                breakdown_info = []
                for file in uploaded_breakdowns:
                    # tip din nume fișier
                    bd_type_filename = detect_breakdown_type(file.name)

                    # dacă user-ul alege deja o factură, poți calcula și tipul sugerat din payout:
                    # (exemplu simplu: folosim prima factură ca hint; după ce legi pe factură reală, poți recalcula)
                    suggested_type = bd_type_filename  # fallback
                    breakdown_info.append({
                        'Fișier': file.name,
                        'Tip fișier': bd_type_filename,
                        'Tip sugerat payout': suggested_type,
                        'Dimensiune': f"{file.size / 1024:.1f} KB"
                    })

                
                st.dataframe(pd.DataFrame(breakdown_info), width='stretch', hide_index=True)
                
                st.divider()
                
                # STEP 3: Asociere facturi
                st.subheader("3️⃣ Asociere Facturi")
                
                # Pentru fiecare fișier, lasă user-ul să selecteze factura
                file_invoice_mapping = {}
                
                for file in uploaded_breakdowns:
                    bd_type = detect_breakdown_type(file.name)
                    
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        st.text(f"📄 {file.name} ({bd_type})")
                    
                    with col2:
                        selected_invoice = st.selectbox(
                            "Factură:",
                            options=invoices_df['invoice_number'].tolist(),
                            key=f"invoice_{file.name}",
                            label_visibility="collapsed"
                        )
                        file_invoice_mapping[file.name] = selected_invoice
                        inv_row = invoices_df[invoices_df['invoice_number'] == selected_invoice].iloc[0]
                        suggested_type = detect_breakdown_type_from_payout(
                            invoice_number=inv_row['invoice_number'],
                            invoice_type=inv_row['invoice_type'],
                            invoice_label=inv_row.get('invoice_label')
                        )
                        st.caption(f"Tip sugerat din payout: **{suggested_type}**")
                
                st.divider()
                
                # STEP 4: Salvare
                if st.button("💾 Salvează toate desfășurătoarele", type="primary", key="save_breakdowns"):
                    with st.spinner("💾 Procesez și salvez desfășurătoarele..."):
                        total_stats = {'inserted': 0, 'errors': 0, 'files_processed': 0}
                        
                        progress_bar = st.progress(0)
                        
                        for idx, file in enumerate(uploaded_breakdowns):
                            bd_type = detect_breakdown_type(file.name)
                            invoice_num = file_invoice_mapping[file.name]
                            
                            # Parsează Excel
                            df_parsed = parse_breakdown_excel(file, file.name, bd_type)
                            
                            if len(df_parsed) > 0:
                                # Salvează în DB
                                stats = save_breakdown_to_db(
                                    df_parsed,
                                    selected_payout_id,
                                    invoice_num,
                                    conn
                                )
                                
                                total_stats['inserted'] += stats['inserted']
                                total_stats['errors'] += stats['errors']
                                total_stats['files_processed'] += 1
                            
                            progress_bar.progress((idx + 1) / len(uploaded_breakdowns))
                        
                        progress_bar.empty()
                        
                        if total_stats['errors'] == 0:
                            st.success(f"""
                                ✅ **Upload finalizat cu succes!**
                                
                                📊 **{total_stats['files_processed']}** fișiere procesate
                                📝 **{total_stats['inserted']}** linii inserate în DB
                            """)
                            st.balloons()
                        else:
                            st.warning(f"""
                                ⚠️ **Upload completat cu erori**
                                
                                ✅ **{total_stats['inserted']}** inserate
                                ❌ **{total_stats['errors']}** erori
                            """)
                
                st.divider()
                
                # STEP 5: Reconciliere & Raport
                st.subheader("4️⃣ Reconciliere & Raport Profit")
                
                if st.button("📊 Generează raport reconciliere", key="generate_report"):
                    with st.spinner("📊 Calculez reconcilierea..."):
                        
                        # Query reconciliere
                        reconcile_query = text("""
                            SELECT 
                                i.invoice_number,
                                i.invoice_type,
                                i.invoice_amount as invoice_total,
                                COALESCE(SUM(
                                    COALESCE(b.comision_net, 0) + 
                                    COALESCE(b.valoare_vouchere, 0) + 
                                    COALESCE(b.valoare_plata, 0) +
                                    COALESCE(b.valoare_produse, 0)
                                ), 0) as breakdown_total,
                                COUNT(DISTINCT b.order_id) as nr_comenzi,
                                COUNT(b.id) as nr_linii
                            FROM emag_payout_invoices i
                            JOIN emag_payout_header h ON h.id = i.header_id
                            LEFT JOIN emag_breakdown_lines b ON b.invoice_number = i.invoice_number
                            WHERE h.payout_id = :payout_id
                            GROUP BY i.invoice_number, i.invoice_type, i.invoice_amount
                            ORDER BY i.invoice_type, i.invoice_number;
                        """)
                        
                        with conn.session as session:
                            result = session.execute(reconcile_query, {'payout_id': selected_payout_id})
                            reconcile_df = pd.DataFrame(result.fetchall(), 
                                columns=['invoice_number', 'invoice_type', 'invoice_total', 
                                        'breakdown_total', 'nr_comenzi', 'nr_linii'])
                        
                        if len(reconcile_df) > 0:
                            reconcile_df['Diferență'] = reconcile_df['invoice_total'] - reconcile_df['breakdown_total']
                            reconcile_df['Status'] = reconcile_df['Diferență'].apply(
                                lambda x: '✅ OK' if abs(x) < 0.01 else '⚠️ Diferență'
                            )
                            
                            st.dataframe(reconcile_df, width='stretch', hide_index=True)
                            
                            # Profit query
                            st.subheader("💰 Raport Profit")
                            
                            profit_query = """
                                SELECT 
                                    order_id,
                                    pnk,
                                    produs,
                                    cantitate,
                                    vanzari,
                                    comision,
                                    vanzari_nete,
                                    cost_achizitie_unitar,
                                    cost_achizitie_total,
                                    profit
                                FROM v_order_profit
                                ORDER BY data DESC
                                LIMIT 100;
                            """
                            
                            profit_df = conn.query(profit_query)
                            
                            if len(profit_df) > 0:
                                total_profit = profit_df['profit'].sum()
                                total_vanzari = profit_df['vanzari_nete'].sum()
                                
                                col1, col2, col3 = st.columns(3)
                                with col1:
                                    st.metric("💰 Total Vânzări Nete", f"{total_vanzari:,.2f} RON")
                                with col2:
                                    st.metric("📈 Profit Total", f"{total_profit:,.2f} RON")
                                with col3:
                                    margin = (total_profit / total_vanzari * 100) if total_vanzari > 0 else 0
                                    st.metric("📊 Marjă Profit", f"{margin:.1f}%")
                                
                                st.dataframe(profit_df.head(50), width='stretch', hide_index=True)
                        else:
                            st.info("Nu există date de reconciliere pentru acest payout.")
        else:
            st.warning("⚠️ Payout-ul selectat nu are facturi asociate.")

# ═══════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════
st.divider()
st.caption("🏪 eMAG Business Intelligence v2.3 COMPLETE FIXED | Mobile Point")
