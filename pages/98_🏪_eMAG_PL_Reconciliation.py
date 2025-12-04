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
import os

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
    """
    Detectează automat coloana cu date.
    """
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
    """
    Filtrează DataFrame-ul pentru a păstra doar înregistrările din 2025 sau după.
    SUPORTĂ format european dd/mm/yyyy.
    """
    initial_count = len(df)

    try:
        df_work = df.copy()

        # Format european: zi/lună/an
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
    """
    Uploadează datele P&L în PostgreSQL cu INSERT doar pentru rânduri noi.
    """
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
            'Data': 'data',
            'Seller': 'seller',
            'ID comanda': 'order_id',
            'ID produs': 'product_id',
            'EAN': 'ean',
            'Cod produs (PN)': 'cod_produs_pn',
            'PNK': 'pnk',
            'Brand': 'brand',
            'Produs': 'produs',
            'Tip desfasurator': 'tip_desfasurator',
            'Cantitate': 'cantitate',
            'Vanzari': 'vanzari',
            'Taxa livrare': 'taxa_livrare',
            'Taxa retur': 'taxa_retur',
            'Valoare retinuta': 'valoare_retinuta',
            'Comision': 'comision',
            'Comision anulate': 'comision_anulate',
            'Comision taxa livrare': 'comision_taxa_livrare',
            'Depozitare FBE': 'depozitare_fbe',
            'Operatiuni FBE': 'operatiuni_fbe',
            'Cost livrare': 'cost_livrare',
            'Cost retur': 'cost_retur',
            'Vanzari nete': 'vanzari_nete'
        }

        df_prepared.rename(columns=column_mapping, inplace=True)
        df_prepared['data'] = pd.to_datetime(df_prepared['data'], dayfirst=True).dt.date
        df_prepared['upload_batch_id'] = batch_id
        df_prepared = df_prepared.where(pd.notna(df_prepared), None)

        insert_query = """
            INSERT INTO emag_order_lines (
                order_id, product_id, tip_desfasurator,
                data, seller,
                ean, cod_produs_pn, pnk, brand, produs,
                cantitate, vanzari, taxa_livrare, taxa_retur,
                valoare_retinuta, comision, comision_anulate,
                comision_taxa_livrare, depozitare_fbe, operatiuni_fbe,
                cost_livrare, cost_retur, vanzari_nete,
                upload_batch_id
            ) VALUES (
                %(order_id)s, %(product_id)s, %(tip_desfasurator)s,
                %(data)s, %(seller)s,
                %(ean)s, %(cod_produs_pn)s, %(pnk)s, %(brand)s, %(produs)s,
                %(cantitate)s, %(vanzari)s, %(taxa_livrare)s, %(taxa_retur)s,
                %(valoare_retinuta)s, %(comision)s, %(comision_anulate)s,
                %(comision_taxa_livrare)s, %(depozitare_fbe)s, %(operatiuni_fbe)s,
                %(cost_livrare)s, %(cost_retur)s, %(vanzari_nete)s,
                %(upload_batch_id)s
            )
            ON CONFLICT (order_id, product_id, tip_desfasurator) 
            DO NOTHING;
        """

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
                    stats['error_details'].append({
                        'row': idx,
                        'order_id': row.get('order_id'),
                        'error': str(e)
                    })

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
                COUNT(*) as total_rows,
                COUNT(DISTINCT order_id) as total_orders,
                MIN(data) as min_date,
                MAX(data) as max_date,
                SUM(CASE WHEN tip_desfasurator = 'finalizata' THEN 1 ELSE 0 END) as finalizate,
                SUM(CASE WHEN tip_desfasurator = 'stornata' THEN 1 ELSE 0 END) as stornate,
                SUM(vanzari_nete) as total_vanzari_nete
            FROM emag_order_lines;
        """

        result = conn.query(query)
        return result.iloc[0].to_dict() if len(result) > 0 else {}

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
            amount_str = match.group(1)
            amount_str = amount_str.replace('.', '').replace(',', '.')
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
            invoice_type = match.group(1)

            if invoice_number in seen:
                continue

            seen.add(invoice_number)

            amount = None
            amount_match = re.search(r'([0-9.,]+)\s*RON', line)
            if amount_match:
                amount_str = amount_match.group(1).replace('.', '').replace(',', '.')
                try:
                    amount = float(amount_str)
                except ValueError:
                    pass

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
        'payout_id': None,
        'payout_date': None,
        'reference_period_start': None,
        'reference_period_end': None
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
        'pdf_hash': pdf_hash,
        'filename': filename,
        'pages_count': pages_count,
        'payout_info': payout_info,
        'total_amount': total_amount,
        'invoices': invoices,
        'invoices_count': len(invoices)
    }

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

tab1, tab2, tab3 = st.tabs([
    "📊 Upload P&L",
    "📄 Payout PDF Parser",
    "📑 Breakdown Excel Parser"
])

# ═══════════════════════════════════════════════════════
# TAB 1: UPLOAD P&L - CU FILTRARE 2025+ ȘI SALVARE DB
# ═══════════════════════════════════════════════════════

with tab1:
    st.header("📊 Upload Profit & Loss")
    st.markdown("Uploadează fișierul Excel cu datele P&L de la eMAG")

    st.info("📌 **Notă**: Toate datele anterioare anului 2025 vor fi ignorate automat (format: zz/ll/aaaa)")

    uploaded_file = st.file_uploader(
        "Selectează fișier Excel",
        type=['xlsx', 'xls'],
        key="pl_uploader"
    )

    if uploaded_file:
        try:
            df = pd.read_excel(uploaded_file)

            st.success(f"✅ Fișier încărcat: {uploaded_file.name}")
            st.info(f"📋 Total rânduri inițiale: **{len(df)}**")

            # Detectare și filtrare 2025+
            date_column = detect_date_column(df)

            if date_column:
                st.info(f"🔍 Coloană date detectată: **{date_column}**")

                with st.expander("🔎 Vezi primele 3 date din fișier"):
                    st.write(df[date_column].head(3).tolist())

                df_filtered, removed, kept = filter_year_2025_and_above(df, date_column)

                if removed > 0:
                    st.warning(
                        f"🗑️ **{removed} rânduri eliminate** (< 2025 sau invalide)  |  "
                        f"✅ **{kept} rânduri păstrate** (2025+)"
                    )
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

            # Preview
            st.subheader("👀 Preview Date (primele 10 rânduri)")
            st.dataframe(df.head(10), use_container_width=True)

            st.divider()

            # Statistici numerice
            numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
            if numeric_cols:
                st.subheader("📊 Statistici coloane numerice")
                st.dataframe(df[numeric_cols].describe(), use_container_width=True)

            # Statistici DB
            st.divider()
            st.subheader("📊 Statistici bază de date")

            if conn:
                db_stats = get_db_stats(conn)

                if 'error' not in db_stats:
                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        st.metric("📋 Total rânduri în DB", f"{db_stats.get('total_rows', 0):,}")
                    with col2:
                        st.metric("🛒 Total comenzi", f"{db_stats.get('total_orders', 0):,}")
                    with col3:
                        st.metric("✅ Finalizate", f"{db_stats.get('finalizate', 0):,}")
                    with col4:
                        st.metric("🔄 Stornate", f"{db_stats.get('stornate', 0):,}")

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        if db_stats.get('min_date'):
                            st.metric("📅 Prima comandă", str(db_stats['min_date']))
                    with col2:
                        if db_stats.get('max_date'):
                            st.metric("📅 Ultima comandă", str(db_stats['max_date']))
                    with col3:
                        vanzari = db_stats.get('total_vanzari_nete', 0)
                        if vanzari:
                            st.metric("💰 Total vânzări nete", f"{vanzari:,.2f} RON")
                else:
                    st.warning(f"⚠️ Eroare statistici: {db_stats.get('error')}")
            else:
                st.info("ℹ️ Conectează la DB pentru statistici")

            # Acțiuni
            st.divider()
            col1, col2 = st.columns(2)

            with col1:
                if st.button("💾 Salvează în DB", type="primary", key="save_pl"):
                    if conn:
                        with st.spinner("💾 Salvare în PostgreSQL..."):
                            upload_stats = upload_pl_to_db(df, conn)

                            if upload_stats['errors'] == 0:
                                st.success(
                                    f"✅ **Upload finalizat cu succes!**\n\n"
                                    f"📊 **{upload_stats['inserted']}** rânduri noi inserate\n"
                                    f"⏭️ **{upload_stats['skipped']}** rânduri ignorate (duplicate)\n"
                                    f"📋 **{upload_stats['total_rows']}** total procesate"
                                )
                                st.balloons()
                            else:
                                st.warning(
                                    f"⚠️ **Upload completat cu erori**\n\n"
                                    f"✅ **{upload_stats['inserted']}** inserate\n"
                                    f"⏭️ **{upload_stats['skipped']}** ignorate\n"
                                    f"❌ **{upload_stats['errors']}** erori"
                                )

                                if upload_stats['error_details']:
                                    with st.expander("📋 Detalii erori"):
                                        for err in upload_stats['error_details'][:10]:
                                            st.error(f"Row {err.get('row')}: {err.get('error')}")
                    else:
                        st.error("❌ DB nu este conectat")

            with col2:
                if st.button("📊 Generează raport", key="report_pl"):
                    st.info("🚧 Funcționalitate în dezvoltare")

            # Export CSV
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
        "Selectează PDF payout",
        type=['pdf'],
        key="pdf_uploader",
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

                    type_labels = {
                        'C': '💼 Comisioane',
                        'V': '🎟️ Vouchere',
                        'Y': '🔄 Retururi',
                        'A': '📢 Ads',
                        'D': '📦 Diverse'
                    }

                    tabs = st.tabs([
                        f"{type_labels.get(t, t)} ({len(invoices)})"
                        for t, invoices in invoice_types.items()
                    ])

                    for idx, (inv_type, invoices) in enumerate(invoice_types.items()):
                        with tabs[idx]:
                            df_inv = pd.DataFrame([{
                                'Număr factură': inv['invoice_number'],
                                'Sumă (RON)': f"{inv['invoice_amount']:,.2f}" if inv['invoice_amount'] else 'N/A',
                                'Poziție': inv['position_in_pdf'],
                                'Linia din PDF': inv['raw_line'][:80] + '...' if len(inv['raw_line']) > 80 else inv['raw_line']
                            } for inv in invoices])

                            st.dataframe(df_inv, use_container_width=True, hide_index=True)

                else:
                    st.warning("⚠️ Nu am găsit facturi în PDF.")

                st.divider()

                with st.expander("🔧 Debug Info"):
                    st.json({
                        'pdf_hash': result['pdf_hash'],
                        'pages_count': result['pages_count'],
                        'payout_info': {
                            'payout_id': result['payout_info'].get('payout_id'),
                            'payout_date': str(result['payout_info'].get('payout_date')),
                        },
                        'total_amount': result['total_amount'],
                        'invoices_count': result['invoices_count']
                    })

                st.divider()
                st.subheader("⚡ Acțiuni")

                col1, col2, col3 = st.columns(3)

                with col1:
                    if st.button("💾 Salvează în DB", type="primary", disabled=not conn, key="save_pdf"):
                        if conn:
                            st.info("🚧 Funcționalitate în dezvoltare")
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
# TAB 3: BREAKDOWN EXCEL PARSER
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("📑 Breakdown Excel Parser")
    st.info("🚧 Funcționalitate în dezvoltare - va permite upload desfășurătoare Excel (DC, DV, DP, etc.)")

# ═══════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════

st.divider()
st.caption("🏪 eMAG Business Intelligence v2.1 FINAL | Mobile Point")
