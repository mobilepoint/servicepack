"""
eMAG Payout PDF Parser - Streamlit Interface
Upload și parsare PDF-uri de payout eMAG
"""

import streamlit as st
import sys
import os
from io import BytesIO

# Setări pagină
st.set_page_config(
    page_title="eMAG Payout PDF Parser",
    page_icon="📄",
    layout="wide"
)

# Import parser (asigură-te că modulul e în path)
try:
    from emag_payout_pdf_parser import parse_payout_pdf, calculate_pdf_hash
except ImportError:
    st.error("⚠️ Nu am găsit modulul `emag_payout_pdf_parser.py`. Asigură-te că e în directorul principal!")
    st.stop()

# ═══════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════

st.title("📄 eMAG Payout PDF Parser")
st.markdown("""
Uploadează PDF-ul de payout de la eMAG pentru a extrage:
- 💰 **Suma totală** de plată
- 📋 **Lista facturilor** (C-MKTP, V-MKTP, etc.)
- 📅 **Date și perioade** de referință
""")

st.divider()

# ═══════════════════════════════════════════════════════
# UPLOAD ZONE
# ═══════════════════════════════════════════════════════

st.subheader("📤 Upload PDF Payout")

uploaded_file = st.file_uploader(
    "Selectează PDF-ul de payout",
    type=['pdf'],
    help="Uploadează avizul de plată (payout notice) de la eMAG"
)

if uploaded_file is not None:
    
    # Citește bytes
    pdf_bytes = uploaded_file.read()
    
    # Afișează info fișier
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("📁 Fișier", uploaded_file.name)
    with col2:
        st.metric("📊 Dimensiune", f"{len(pdf_bytes) / 1024:.1f} KB")
    with col3:
        file_hash = calculate_pdf_hash(pdf_bytes)
        st.metric("🔑 Hash", file_hash[:12] + "...")
    
    st.divider()
    
    # ═══════════════════════════════════════════════════════
    # PARSARE
    # ═══════════════════════════════════════════════════════
    
    with st.spinner("🔍 Parsez PDF-ul..."):
        try:
            result = parse_payout_pdf(
                pdf_bytes=BytesIO(pdf_bytes),
                filename=uploaded_file.name,
                conn=None  # Deocamdată fără salvare în DB
            )
            
            st.success("✅ PDF parsat cu succes!")
            
            # ═══════════════════════════════════════════════════════
            # REZULTATE
            # ═══════════════════════════════════════════════════════
            
            # Row 1: Informații generale
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
            
            # Row 2: Perioada de referință
            if result['payout_info'].get('reference_period_start') or result['payout_info'].get('reference_period_end'):
                st.info(f"📆 **Perioada:** {result['payout_info'].get('reference_period_start')} → {result['payout_info'].get('reference_period_end')}")
            
            st.divider()
            
            # ═══════════════════════════════════════════════════════
            # LISTA FACTURI
            # ═══════════════════════════════════════════════════════
            
            st.subheader(f"📋 Facturi Găsite ({result['invoices_count']})")
            
            if result['invoices']:
                
                # Grupare pe tip
                invoice_types = {}
                for inv in result['invoices']:
                    inv_type = inv['invoice_type']
                    if inv_type not in invoice_types:
                        invoice_types[inv_type] = []
                    invoice_types[inv_type].append(inv)
                
                # Afișare pe tip
                type_labels = {
                    'C': '💼 Comisioane',
                    'V': '🎟️ Vouchere',
                    'Y': '🔄 Retururi',
                    'A': '📢 Ads',
                    'D': '📦 Diverse'
                }
                
                tabs = st.tabs([f"{type_labels.get(t, t)} ({len(invoices)})" for t, invoices in invoice_types.items()])
                
                for idx, (inv_type, invoices) in enumerate(invoice_types.items()):
                    with tabs[idx]:
                        
                        # Tabel cu facturi
                        import pandas as pd
                        
                        df = pd.DataFrame([{
                            'Număr factură': inv['invoice_number'],
                            'Sumă (RON)': f"{inv['invoice_amount']:,.2f}" if inv['invoice_amount'] else 'N/A',
                            'Poziție': inv['position_in_pdf'],
                            'Linia din PDF': inv['raw_line'][:80] + '...' if len(inv['raw_line']) > 80 else inv['raw_line']
                        } for inv in invoices])
                        
                        st.dataframe(
                            df,
                            use_container_width=True,
                            hide_index=True
                        )
            else:
                st.warning("⚠️ Nu am găsit facturi în PDF. Verifică formatul documentului.")
            
            st.divider()
            
            # ═══════════════════════════════════════════════════════
            # DEBUG INFO (expandable)
            # ═══════════════════════════════════════════════════════
            
            with st.expander("🔧 Debug Info"):
                st.json({
                    'pdf_hash': result['pdf_hash'],
                    'pages_count': result['pages_count'],
                    'payout_info': {
                        'payout_id': result['payout_info'].get('payout_id'),
                        'payout_date': str(result['payout_info'].get('payout_date')),
                        'reference_period_start': str(result['payout_info'].get('reference_period_start')),
                        'reference_period_end': str(result['payout_info'].get('reference_period_end'))
                    },
                    'total_amount': result['total_amount'],
                    'invoices_count': result['invoices_count']
                })
            
            # ═══════════════════════════════════════════════════════
            # ACȚIUNI (pentru viitor)
            # ═══════════════════════════════════════════════════════
            
            st.divider()
            st.subheader("⚡ Acțiuni")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                if st.button("💾 Salvează în DB", type="primary", disabled=True):
                    st.info("🚧 Funcționalitate în dezvoltare")
            
            with col2:
                if st.button("🔍 Reconciliază cu Excel", disabled=True):
                    st.info("🚧 Funcționalitate în dezvoltare")
            
            with col3:
                if st.button("📊 Raport complet", disabled=True):
                    st.info("🚧 Funcționalitate în dezvoltare")
            
        except Exception as e:
            st.error(f"❌ Eroare la parsare: {str(e)}")
            
            with st.expander("📋 Detalii eroare"):
                import traceback
                st.code(traceback.format_exc())

else:
    st.info("👆 Uploadează un PDF pentru a începe parsarea")

# ═══════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════

st.divider()
st.caption("📄 eMAG Payout PDF Parser v1.0 | Mobile Point")
