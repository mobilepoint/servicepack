"""
eMAG P&L Reconciliation + Payout PDF Parser + Breakdown Parser
Toate funcționalitățile eMAG într-un singur loc - cu autentificare
"""

import streamlit as st
import pandas as pd
import pdfplumber
import re
import hashlib
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
        # Prima rulare, arată input pentru parolă
        st.text_input(
            "🔒 Parolă",
            type="password",
            on_change=password_entered,
            key="password"
        )
        st.stop()
    elif not st.session_state["password_correct"]:
        # Parolă incorectă, arată input + eroare
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
# INIȚIALIZARE SUPABASE
# ═══════════════════════════════════════════════════════

@st.cache_resource
def init_supabase():
    """Inițializează conexiunea Supabase."""
    try:
        from supabase import create_client
        return create_client(
            st.secrets["SUPABASE_URL"],
            st.secrets["SUPABASE_KEY"]
        )
    except Exception as e:
        st.error(f"⚠️ Eroare conexiune Supabase: {e}")
        return None

supabase = init_supabase()


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
            
            # Încearcă să extragă suma
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
    
    # Payout ID din nume sau text
    filename_match = re.search(r'_(\d{8,})\.pdf', filename)
    if filename_match:
        info['payout_id'] = int(filename_match.group(1))
    
    payout_id_match = re.search(r'Payout\s+ID[:\s]+(\d+)', text, re.IGNORECASE)
    if payout_id_match:
        info['payout_id'] = int(payout_id_match.group(1))
    
    # Date
    date_patterns = [
        r'Data\s+platii?[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})',
        r'Payout\s+date[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            for date_format in ['%d-%m-%Y', '%d.%m.%Y', '%d/%m/%Y']:
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

# Status conexiune Supabase
if supabase:
    st.success("✅ Conectat la Supabase")
else:
    st.warning("⚠️ Supabase nu este disponibil")

st.divider()

# Tab-uri principale
tab1, tab2, tab3 = st.tabs([
    "📊 Upload P&L",
    "📄 Payout PDF Parser",
    "📑 Breakdown Excel Parser"
])


# ═══════════════════════════════════════════════════════
# TAB 1: UPLOAD P&L
# ═══════════════════════════════════════════════════════

with tab1:
    st.header("📊 Upload Profit & Loss")
    st.markdown("Uploadează fișierul Excel cu datele P&L de la eMAG")
    
    uploaded_file = st.file_uploader(
        "Selectează fișier Excel",
        type=['xlsx', 'xls'],
        key="pl_uploader"
    )
    
    if uploaded_file:
        try:
            df = pd.read_excel(uploaded_file)
            
            st.success(f"✅ Fișier încărcat: {uploaded_file.name}")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("📋 Rânduri", len(df))
            with col2:
                st.metric("📊 Coloane", len(df.columns))
            with col3:
                st.metric("💾 Dimensiune", f"{uploaded_file.size / 1024:.1f} KB")
            
            st.divider()
            
            # Preview date
            st.subheader("👀 Preview Date")
            st.dataframe(df.head(10), use_container_width=True)
            
            # Acțiuni
            st.divider()
            col1, col2 = st.columns(2)
            
            with col1:
                if st.button("💾 Salvează în Supabase", type="primary", key="save_pl"):
                    if supabase:
                        with st.spinner("Salvare în curs..."):
                            # TODO: Implementare salvare în DB
                            st.info("🚧 Funcționalitate în dezvoltare")
                    else:
                        st.error("❌ Supabase nu este conectat")
            
            with col2:
                if st.button("📊 Generează raport", key="report_pl"):
                    st.info("🚧 Funcționalitate în dezvoltare")
                    
        except Exception as e:
            st.error(f"❌ Eroare la citire: {str(e)}")
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
        
        # Info fișier
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📁 Fișier", uploaded_pdf.name)
        with col2:
            st.metric("📊 Dimensiune", f"{len(pdf_bytes) / 1024:.1f} KB")
        with col3:
            file_hash = calculate_pdf_hash(pdf_bytes)
            st.metric("🔑 Hash", file_hash[:12] + "...")
        
        st.divider()
        
        # Parsare
        with st.spinner("🔍 Parsez PDF-ul..."):
            try:
                result = parse_payout_pdf(pdf_bytes, uploaded_pdf.name)
                
                st.success("✅ PDF parsat cu succes!")
                
                # Rezultate
                st.subheader("📊 Informații Payout")
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    payout_id = result['payout_info'].get('payout_id')
                    if payout_id:
                        st.metric("🆔 Payout ID", f"{payout_id:,}")
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
                
                # Lista facturi
                st.subheader(f"📋 Facturi Găsite ({result['invoices_count']})")
                
                if result['invoices']:
                    # Grupare pe tip
                    invoice_types = {}
                    for inv in result['invoices']:
                        inv_type = inv['invoice_type']
                        if inv_type not in invoice_types:
                            invoice_types[inv_type] = []
                        invoice_types[inv_type].append(inv)
                    
                    # Labels
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
                
                # Debug info
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
                
                # Acțiuni
                st.divider()
                st.subheader("⚡ Acțiuni")
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    if st.button("💾 Salvează în DB", type="primary", disabled=not supabase, key="save_pdf"):
                        if supabase:
                            st.info("🚧 Funcționalitate în dezvoltare")
                        else:
                            st.error("❌ Supabase nu este conectat")
                
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
# TAB 3: BREAKDOWN EXCEL PARSER (placeholder)
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("📑 Breakdown Excel Parser")
    st.info("🚧 Funcționalitate în dezvoltare - va permite upload desfășurătoare Excel (DC, DV, DP, etc.)")


# ═══════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════

st.divider()
st.caption("🏪 eMAG Business Intelligence v2.0 | Mobile Point")
