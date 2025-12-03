import streamlit as st
import pandas as pd
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

# Import autentificare (dacă există)
try:
    from auth_simple import check_password
    HAS_AUTH = True
except ImportError:
    HAS_AUTH = False

# ═══════════════════════════════════════════════════════
# CONFIGURARE PAGINĂ
# ═══════════════════════════════════════════════════════

st.set_page_config(
    page_title="🏪 eMAG P&L Reconciliation",
    page_icon="🏪",
    layout="wide"
)

# ═══════════════════════════════════════════════════════
# AUTENTIFICARE
# ═══════════════════════════════════════════════════════

if HAS_AUTH:
    if not check_password():
        st.stop()

# ═══════════════════════════════════════════════════════
# CONEXIUNE POSTGRESQL
# ═══════════════════════════════════════════════════════

def get_db_connection():
    """Conexiune la PostgreSQL folosind secrets"""
    try:
        pg_url = st.secrets["connections"]["postgresql"]["url"]
        conn = psycopg2.connect(pg_url, connect_timeout=10)
        return conn
    except Exception as e:
        st.error(f"❌ Eroare conexiune DB: {e}")
        return None

# ═══════════════════════════════════════════════════════
# FUNCȚII HELPER
# ═══════════════════════════════════════════════════════

def parse_excel_pl(df):
    """Parse Excel P&L și pregătește datele pentru insert"""
    column_mapping = {
        'Data': 'data',
        'Seller': 'seller',
        'ID comanda': 'id_comanda',
        'ID produs': 'id_produs',
        'EAN': 'ean',
        'Cod produs (PN)': 'sku',
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
    
    df_clean = df.rename(columns=column_mapping)
    
    # Convertește data (format DD/MM/YYYY)
    df_clean['data'] = pd.to_datetime(df_clean['data'], format='%d/%m/%Y', errors='coerce')
    
    # Convertește numeric
    numeric_cols = [
        'id_comanda', 'id_produs', 'cantitate',
        'vanzari', 'taxa_livrare', 'taxa_retur', 'valoare_retinuta',
        'comision', 'comision_anulate', 'comision_taxa_livrare',
        'depozitare_fbe', 'operatiuni_fbe', 'cost_livrare', 'cost_retur',
        'vanzari_nete'
    ]
    
    for col in numeric_cols:
        if col in df_clean.columns:
            df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')
    
    # Replace NaN cu None (pentru NULL în DB)
    df_clean = df_clean.where(pd.notnull(df_clean), None)
    
    return df_clean


def upsert_order_line(conn, row, batch_id, file_name):
    """Insert sau Update o linie în DB folosind PostgreSQL UPSERT"""
    cursor = conn.cursor()
    
    try:
        sql = """
            INSERT INTO emag_order_lines (
                data, seller, id_comanda, id_produs, ean, sku, pnk, brand, produs,
                tip_desfasurator, cantitate,
                vanzari, taxa_livrare, taxa_retur, valoare_retinuta,
                comision, comision_anulate, comision_taxa_livrare,
                depozitare_fbe, operatiuni_fbe, cost_livrare, cost_retur,
                vanzari_nete, profit_net,
                upload_batch_id, excel_source_file, last_seen_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (id_comanda, tip_desfasurator)
            DO UPDATE SET
                vanzari = EXCLUDED.vanzari,
                comision = EXCLUDED.comision,
                vanzari_nete = EXCLUDED.vanzari_nete,
                profit_net = EXCLUDED.profit_net,
                upload_batch_id = EXCLUDED.upload_batch_id,
                last_seen_at = NOW()
            WHERE 
                emag_order_lines.vanzari IS DISTINCT FROM EXCLUDED.vanzari OR
                emag_order_lines.comision IS DISTINCT FROM EXCLUDED.comision
        """
        
        values = (
            row['data'].strftime('%Y-%m-%d') if pd.notnull(row['data']) else None,
            row.get('seller'),
            int(row['id_comanda']) if pd.notnull(row['id_comanda']) else None,
            int(row['id_produs']) if pd.notnull(row['id_produs']) else None,
            str(row.get('ean')) if pd.notnull(row.get('ean')) else None,
            str(row.get('sku')) if pd.notnull(row.get('sku')) else None,
            row.get('pnk'),
            row.get('brand'),
            row.get('produs'),
            row.get('tip_desfasurator'),
            int(row['cantitate']) if pd.notnull(row['cantitate']) else 0,
            float(row['vanzari']) if pd.notnull(row['vanzari']) else 0.0,
            float(row['taxa_livrare']) if pd.notnull(row['taxa_livrare']) else 0.0,
            float(row['taxa_retur']) if pd.notnull(row['taxa_retur']) else 0.0,
            float(row['valoare_retinuta']) if pd.notnull(row['valoare_retinuta']) else 0.0,
            float(row['comision']) if pd.notnull(row['comision']) else 0.0,
            float(row['comision_anulate']) if pd.notnull(row['comision_anulate']) else 0.0,
            float(row['comision_taxa_livrare']) if pd.notnull(row['comision_taxa_livrare']) else 0.0,
            float(row['depozitare_fbe']) if pd.notnull(row['depozitare_fbe']) else 0.0,
            float(row['operatiuni_fbe']) if pd.notnull(row['operatiuni_fbe']) else 0.0,
            float(row['cost_livrare']) if pd.notnull(row['cost_livrare']) else 0.0,
            float(row['cost_retur']) if pd.notnull(row['cost_retur']) else 0.0,
            float(row['vanzari_nete']) if pd.notnull(row['vanzari_nete']) else 0.0,
            float(row['vanzari_nete']) if pd.notnull(row['vanzari_nete']) else 0.0,  # profit_net = vanzari_nete
            batch_id,
            file_name
        )
        
        cursor.execute(sql, values)
        conn.commit()
        cursor.close()
        return True
        
    except Exception as e:
        conn.rollback()
        cursor.close()
        raise e


def get_db_stats(conn):
    """Obține statistici din baza de date"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Total linii
        cursor.execute("SELECT COUNT(*) as total FROM emag_order_lines")
        total = cursor.fetchone()['total']
        
        # Comenzi unice
        cursor.execute("SELECT COUNT(DISTINCT id_comanda) as unique_orders FROM emag_order_lines")
        unique_orders = cursor.fetchone()['unique_orders']
        
        # Profit total
        cursor.execute("SELECT SUM(profit_net) as total_profit FROM emag_order_lines")
        result = cursor.fetchone()
        total_profit = result['total_profit'] if result['total_profit'] else 0
        
        cursor.close()
        
        return {
            'total_lines': total,
            'unique_orders': unique_orders,
            'total_profit': total_profit
        }
    except:
        cursor.close()
        return None
# ═══════════════════════════════════════════════════════
# FUNCȚII PENTRU RECONCILIERE AVIZE (PAS 2)
# ═══════════════════════════════════════════════════════

import re
import base64
import PyPDF2
from io import BytesIO

def parse_payout_pdf(pdf_file):
    """
    Parse PDF aviz plată și extrage:
    - Număr aviz
    - Perioada
    - Facturi (C-MKTP, V-MKTP, etc.) cu sume
    - Total
    """
    try:
        # Citește PDF
        pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_file.read()))
        full_text = ""
        
        for page in pdf_reader.pages:
            full_text += page.extract_text()
        
        # Reset file pointer
        pdf_file.seek(0)
        
        # Parse număr aviz (ex: 36898183)
        aviz_match = re.search(r'Aviz[^\d]*(\d{8,})', full_text, re.IGNORECASE)
        payout_number = aviz_match.group(1) if aviz_match else None
        
        # Parse perioada (ex: 01.11.2025 - 15.11.2025)
        period_match = re.search(r'(\d{2}[./-]\d{2}[./-]\d{4})\s*[-–]\s*(\d{2}[./-]\d{2}[./-]\d{4})', full_text)
        period_start = None
        period_end = None
        if period_match:
            period_start = period_match.group(1).replace('.', '-').replace('/', '-')
            period_end = period_match.group(2).replace('.', '-').replace('/', '-')
        
        # Parse facturi (C-MKTP, V-MKTP, Y-MKTP, E-MKTP)
        invoice_pattern = r'([CVYE]-MKTP-\d+)\s+([\d.]+[,.]?\d{2})\s*[-]?'
        invoices = []
        
        for match in re.finditer(invoice_pattern, full_text):
            invoice_num = match.group(1)
            amount_str = match.group(2).replace('.', '').replace(',', '.')
            
            # Determină semnul (+ sau -)
            context = full_text[max(0, match.start()-100):match.end()+20]
            is_negative = '-' in context or 'comision' in context.lower() or 'taxa' in context.lower()
            
            amount = float(amount_str) * (-1 if is_negative else 1)
            
            invoices.append({
                'number': invoice_num,
                'amount': amount,
                'category': invoice_num[0]  # C, V, Y, E
            })
        
        # Parse încasări COD
        cod_match = re.search(r'ramburs[^\d]*([\d.,]+)', full_text, re.IGNORECASE)
        collections_cod = float(cod_match.group(1).replace('.', '').replace(',', '.')) if cod_match else 0
        
        # Parse încasări Card
        card_match = re.search(r'card[^\d]*([\d.,]+)', full_text, re.IGNORECASE)
        collections_card = float(card_match.group(1).replace('.', '').replace(',', '.')) if card_match else 0
        
        # Parse total
        total_match = re.search(r'total[^\d]*([-]?[\d.,]+)', full_text, re.IGNORECASE)
        total_amount = float(total_match.group(1).replace('.', '').replace(',', '.')) if total_match else 0
        
        return {
            'success': True,
            'payout_number': payout_number,
            'period_start': period_start,
            'period_end': period_end,
            'invoices': invoices,
            'collections_cod': collections_cod,
            'collections_card': collections_card,
            'total_amount': total_amount,
            'raw_text': full_text[:500]  # Primele 500 caractere pentru debug
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def call_emag_invoice_api(invoice_number):
    """
    Call eMAG Invoice API pentru a obține detalii factură
    """
    try:
        emag_username = st.secrets["connections"]["emag"]["USERNAME"]
        emag_password = st.secrets["connections"]["emag"]["PASSWORD"]
        emag_api_url = st.secrets["connections"]["emag"]["API_URL"]
        
        # Creare Basic Auth header
        credentials = f"{emag_username}:{emag_password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }
        
        # Payload pentru /invoice/read
        payload = {
            "number": invoice_number
        }
        
        import requests
        response = requests.post(
            f"{emag_api_url}/invoice/read",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            
            if not data.get('isError', True) and data.get('results'):
                invoice_data = data['results'][0] if isinstance(data['results'], list) else data['results']
                
                return {
                    'success': True,
                    'invoice': invoice_data,
                    'order_id': invoice_data.get('orderid')
                }
            else:
                return {
                    'success': False,
                    'error': data.get('messages', ['API returned error'])
                }
        else:
            return {
                'success': False,
                'error': f"HTTP {response.status_code}: {response.text}"
            }
            
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def save_payout_notice_to_db(conn, payout_data):
    """
    Salvează avizul de plată în baza de date
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        sql = """
            INSERT INTO emag_payout_notices (
                payout_number, payout_date, period_start, period_end,
                total_amount, collections_cod, collections_card,
                commissions, vouchers, other_fees,
                pdf_file_name, uploaded_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (payout_number) 
            DO UPDATE SET
                total_amount = EXCLUDED.total_amount,
                collections_cod = EXCLUDED.collections_cod,
                collections_card = EXCLUDED.collections_card,
                updated_at = NOW()
            RETURNING id
        """
        
        # Calculează breakdown
        commissions = sum(inv['amount'] for inv in payout_data['invoices'] if inv['category'] == 'C')
        vouchers = sum(inv['amount'] for inv in payout_data['invoices'] if inv['category'] == 'V')
        other_fees = sum(inv['amount'] for inv in payout_data['invoices'] if inv['category'] in ['Y', 'E'])
        
        values = (
            payout_data['payout_number'],
            payout_data.get('payout_date'),
            payout_data.get('period_start'),
            payout_data.get('period_end'),
            payout_data['total_amount'],
            payout_data['collections_cod'],
            payout_data['collections_card'],
            commissions,
            vouchers,
            other_fees,
            payout_data.get('pdf_file_name'),
            'system'  # sau user din session
        )
        
        cursor.execute(sql, values)
        conn.commit()
        
        payout_id = cursor.fetchone()['id']
        cursor.close()
        
        return {'success': True, 'payout_id': payout_id}
        
    except Exception as e:
        conn.rollback()
        cursor.close()
        return {'success': False, 'error': str(e)}


def save_invoice_to_db(conn, invoice_data, payout_id):
    """
    Salvează factura în baza de date
    """
    cursor = conn.cursor()
    
    try:
        sql = """
            INSERT INTO emag_invoices (
                invoice_number, invoice_category, invoice_name, invoice_date,
                order_id, total_without_vat, total_with_vat, vat_value,
                is_storno, payout_notice_id, raw_api_response
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (invoice_number) 
            DO UPDATE SET
                payout_notice_id = EXCLUDED.payout_notice_id,
                updated_at = NOW()
            RETURNING id
        """
        
        values = (
            invoice_data.get('number'),
            invoice_data.get('category'),
            invoice_data.get('name'),
            invoice_data.get('date'),
            invoice_data.get('orderid'),
            invoice_data.get('totalwithoutvat'),
            invoice_data.get('totalwithvat'),
            invoice_data.get('vatvalue'),
            invoice_data.get('isstorno', False),
            payout_id,
            None  # raw_api_response - poate fi adăugat JSON complet
        )
        
        cursor.execute(sql, values)
        conn.commit()
        cursor.close()
        
        return {'success': True}
        
    except Exception as e:
        conn.rollback()
        cursor.close()
        return {'success': False, 'error': str(e)}


# ═══════════════════════════════════════════════════════
# HEADER PRINCIPAL
# ═══════════════════════════════════════════════════════

st.title("🏪 eMAG P&L & Reconciliation")
st.caption("Tracking profit și reconciliere avize plată")
st.divider()

# ═══════════════════════════════════════════════════════
# TABS PRINCIPALE
# ═══════════════════════════════════════════════════════

tab1, tab2, tab3, tab4 = st.tabs([
    "📤 Upload P&L",
    "📊 Dashboard", 
    "💰 Reconciliere Avize",
    "📈 Rapoarte"
])

# ═══════════════════════════════════════════════════════
# TAB 1: UPLOAD P&L
# ═══════════════════════════════════════════════════════

with tab1:
    st.header("📤 Upload Raport P&L eMAG")
    
    st.info("""
    **Instrucțiuni:**
    1. Descarcă raportul P&L din eMAG (Financiar → P&L Comenzi)
    2. Upload fișierul Excel aici
    3. Aplicația va procesa automat și va evita duplicatele
    """)
    
    uploaded_file = st.file_uploader(
        "Selectează fișierul Excel P&L",
        type=['xlsx', 'xls'],
        help="Fișierul trebuie să fie în formatul standard eMAG P&L"
    )
    
    if uploaded_file is not None:
        st.success(f"✅ Fișier încărcat: **{uploaded_file.name}**")
        
        if st.button("🚀 Procesează și Upload în Baza de Date", type="primary"):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Verifică conexiune DB
            conn = get_db_connection()
            if conn is None:
                st.error("❌ Nu pot conecta la baza de date. Verifică secrets!")
                st.stop()
            
            try:
                status_text.text("📖 Citesc fișierul Excel...")
                progress_bar.progress(10)
                
                df = pd.read_excel(uploaded_file)
                total_rows = len(df)
                st.write(f"📊 Total linii în Excel: **{total_rows}**")
                
                status_text.text("🔄 Parse date...")
                progress_bar.progress(20)
                df_clean = parse_excel_pl(df)
                
                required_cols = ['id_comanda', 'tip_desfasurator']
                missing_cols = [col for col in required_cols if col not in df_clean.columns]
                
                if missing_cols:
                    st.error(f"❌ Lipsesc coloane obligatorii: {missing_cols}")
                    st.stop()
                
                status_text.text("💾 Upload în baza de date...")
                batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                stats = {'total': total_rows, 'success': 0, 'errors': 0, 'error_details': []}
                
                for index, row in df_clean.iterrows():
                    try:
                        upsert_order_line(conn, row, batch_id, uploaded_file.name)
                        stats['success'] += 1
                        progress = 20 + int((index / total_rows) * 70)
                        progress_bar.progress(progress)
                        status_text.text(f"💾 Procesez: {index + 1}/{total_rows}")
                    except Exception as e:
                        stats['errors'] += 1
                        stats['error_details'].append({
                            'row': index + 1,
                            'id_comanda': row.get('id_comanda'),
                            'error': str(e)
                        })
                
                progress_bar.progress(100)
                status_text.text("✅ Procesare completă!")
                
                st.divider()
                st.success("🎉 Upload complet!")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Total Linii", stats['total'])
                with col2:
                    st.metric("✅ Succes", stats['success'])
                with col3:
                    st.metric("❌ Erori", stats['errors'])
                
                if stats['errors'] > 0:
                    with st.expander("⚠️ Vezi detalii erori"):
                        for err in stats['error_details']:
                            st.error(f"Rând {err['row']} (ID: {err['id_comanda']}): {err['error']}")
                
                st.info("👉 Mergi la tab-ul **Dashboard** pentru a vedea datele procesate")
                
                # Clear cache pentru a refresh statisticile
                st.cache_data.clear()
                
            except Exception as e:
                st.error(f"❌ Eroare la procesare: {e}")
                st.exception(e)
            finally:
                if conn:
                    conn.close()

# ═══════════════════════════════════════════════════════
# TAB 2: DASHBOARD
# ═══════════════════════════════════════════════════════

with tab2:
    st.header("📊 Dashboard Profit eMAG")
    
    conn = get_db_connection()
    if not conn:
        st.error("❌ Nu pot conecta la baza de date")
        st.stop()
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # ═══════════════════════════════════════════════════════
        # STATISTICI GENERALE
        # ═══════════════════════════════════════════════════════
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_linii,
                COUNT(DISTINCT id_comanda) as comenzi_unice,
                COUNT(CASE WHEN pret_intrare IS NOT NULL THEN 1 END) as cu_pret_intrare,
                COUNT(CASE WHEN pret_intrare IS NULL THEN 1 END) as fara_pret_intrare,
                ROUND(SUM(vanzari), 2) as total_vanzari,
                ROUND(SUM(comision), 2) as total_comision,
                ROUND(SUM(vanzari_nete), 2) as total_vanzari_nete,
                ROUND(SUM(CASE WHEN pret_intrare IS NOT NULL 
                          THEN profit_net ELSE vanzari_nete END), 2) as total_profit,
                ROUND(SUM(CASE WHEN pret_intrare IS NOT NULL 
                          THEN pret_intrare * cantitate ELSE 0 END), 2) as total_costuri
            FROM emag_order_lines
        """)
        stats = cursor.fetchone()
        
        # Metrici principale
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric(
                "💰 Total Vânzări", 
                f"{stats['total_vanzari']:,.2f} RON",
                help="Suma totală încasată (cu taxe)"
            )
        
        with col2:
            st.metric(
                "💸 Comisioane eMAG", 
                f"{stats['total_comision']:,.2f} RON",
                delta=f"-{(stats['total_comision']/stats['total_vanzari']*100):.1f}%",
                delta_color="inverse",
                help="Total comisioane plătite către eMAG"
            )
        
        with col3:
            st.metric(
                "📦 Costuri Produse", 
                f"{stats['total_costuri']:,.2f} RON",
                delta=f"-{(stats['total_costuri']/stats['total_vanzari']*100):.1f}%",
                delta_color="inverse",
                help="Cost de achiziție produse (din SmartBill)"
            )
        
        with col4:
            profit_color = "normal" if stats['total_profit'] >= 0 else "inverse"
            marja = (stats['total_profit'] / stats['total_vanzari'] * 100) if stats['total_vanzari'] > 0 else 0
            st.metric(
                "✨ Profit Net", 
                f"{stats['total_profit']:,.2f} RON",
                delta=f"{marja:.1f}% marjă",
                delta_color=profit_color,
                help="Profit real = Vânzări - Comisioane - Costuri"
            )
        
        st.divider()
        
        # Status match cu SmartBill
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("📦 Total Comenzi", stats['total_linii'])
        
        with col2:
            procent_match = (stats['cu_pret_intrare'] / stats['total_linii'] * 100) if stats['total_linii'] > 0 else 0
            st.metric(
                "✅ Cu Preț Intrare", 
                stats['cu_pret_intrare'],
                delta=f"{procent_match:.1f}%",
                help="Comenzi cu match în SmartBill"
            )
        
        with col3:
            st.metric(
                "⚠️ Fără Preț Intrare", 
                stats['fara_pret_intrare'],
                delta_color="inverse",
                help="Comenzi fără preț în SmartBill"
            )
        
        st.divider()
        
        # ═══════════════════════════════════════════════════════
        # FILTRE
        # ═══════════════════════════════════════════════════════
        
        st.subheader("🔍 Filtre & Vizualizare")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Filtru dată
            cursor.execute("SELECT MIN(data) as min_date, MAX(data) as max_date FROM emag_order_lines")
            date_range = cursor.fetchone()
            
            if date_range['min_date']:
                date_filter = st.date_input(
                    "Perioada",
                    value=(date_range['min_date'], date_range['max_date']),
                    min_value=date_range['min_date'],
                    max_value=date_range['max_date'],
                    format="DD/MM/YYYY"
                )
            else:
                date_filter = None
        
        with col2:
            status_filter = st.selectbox(
                "Status Preț",
                ["Toate", "Cu preț intrare", "Fără preț intrare"]
            )
        
        with col3:
            sort_by = st.selectbox(
                "Sortare după",
                ["Profit (desc)", "Profit (asc)", "Data (desc)", "Data (asc)", "Vânzări (desc)"]
            )
        
        # ═══════════════════════════════════════════════════════
        # CONSTRUIRE QUERY CU FILTRE
        # ═══════════════════════════════════════════════════════
        
        where_clauses = []
        params = []
        
        if date_filter and len(date_filter) == 2:
            where_clauses.append("data BETWEEN %s AND %s")
            params.extend([date_filter[0], date_filter[1]])
        
        if status_filter == "Cu preț intrare":
            where_clauses.append("pret_intrare IS NOT NULL")
        elif status_filter == "Fără preț intrare":
            where_clauses.append("pret_intrare IS NULL")
        
        where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
        
        # Sortare
        sort_mapping = {
            "Profit (desc)": "profit_net DESC NULLS LAST",
            "Profit (asc)": "profit_net ASC NULLS LAST",
            "Data (desc)": "data DESC",
            "Data (asc)": "data ASC",
            "Vânzări (desc)": "vanzari DESC"
        }
        order_sql = sort_mapping.get(sort_by, "data DESC")
        
        # ═══════════════════════════════════════════════════════
        # QUERY COMENZI
        # ═══════════════════════════════════════════════════════
        
        query = f"""
            SELECT 
                data,
                id_comanda,
                sku,
                LEFT(produs, 50) as produs,
                brand,
                tip_desfasurator,
                cantitate,
                ROUND(vanzari, 2) as vanzari,
                ROUND(comision, 2) as comision,
                ROUND(pret_intrare, 2) as pret_intrare,
                ROUND(vanzari_nete, 2) as vanzari_nete,
                ROUND(profit_net, 2) as profit_net,
                CASE 
                    WHEN pret_intrare IS NOT NULL AND vanzari_nete > 0 
                    THEN ROUND((profit_net / vanzari_nete) * 100, 1)
                    ELSE NULL 
                END as marja_procent
            FROM emag_order_lines
            {where_sql}
            ORDER BY {order_sql}
            LIMIT 100
        """
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        if rows:
            df_display = pd.DataFrame(rows)
            
            # Format data
            df_display['data'] = pd.to_datetime(df_display['data']).dt.strftime('%d/%m/%Y')
            
            # Colorare condiționată pentru profit
            def highlight_profit(row):
                if pd.notnull(row['profit_net']):
                    if row['profit_net'] < 0:
                        return ['background-color: #ffe6e6'] * len(row)  # Roșu deschis
                    elif row['profit_net'] > 50:
                        return ['background-color: #e6ffe6'] * len(row)  # Verde deschis
                return [''] * len(row)
            
            st.dataframe(
                df_display,
                use_container_width=True,
                height=600,
                column_config={
                    "data": st.column_config.TextColumn("Data", width="small"),
                    "id_comanda": st.column_config.NumberColumn("ID Comandă", format="%d"),
                    "sku": st.column_config.TextColumn("SKU", width="medium"),
                    "produs": st.column_config.TextColumn("Produs", width="large"),
                    "brand": st.column_config.TextColumn("Brand", width="small"),
                    "tip_desfasurator": st.column_config.TextColumn("Tip", width="small"),
                    "cantitate": st.column_config.NumberColumn("Cant.", format="%d"),
                    "vanzari": st.column_config.NumberColumn("Vânzări", format="%.2f RON"),
                    "comision": st.column_config.NumberColumn("Comision", format="%.2f RON"),
                    "pret_intrare": st.column_config.NumberColumn("Preț Intrare", format="%.2f RON"),
                    "vanzari_nete": st.column_config.NumberColumn("Vânzări Nete", format="%.2f RON"),
                    "profit_net": st.column_config.NumberColumn("Profit Net", format="%.2f RON"),
                    "marja_procent": st.column_config.NumberColumn("Marjă %", format="%.1f%%"),
                }
            )
            
            st.caption(f"📊 Afișate primele 100 comenzi din {len(rows)} găsite")
            
        else:
            st.warning("⚠️ Nu există date pentru filtrele selectate")
        
        cursor.close()
        
    except Exception as e:
        st.error(f"❌ Eroare: {e}")
        st.exception(e)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════
# TAB 3: RECONCILIERE AVIZE
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("💰 Reconciliere Avize Plată eMAG")
    
    st.info("""
    **Cum funcționează:**
    1. Upload avizul de plată (PDF/Excel) primit de la eMAG
    2. Aplicația extrage automat suma totală și perioada
    3. Calculează suma așteptată din P&L pentru aceeași perioadă
    4. Afișează diferențele (dacă există)
    """)
    
    # ═══════════════════════════════════════════════════════
    # UPLOAD AVIZ
    # ═══════════════════════════════════════════════════════
    
    uploaded_aviz = st.file_uploader(
        "📄 Selectează Avizul de Plată",
        type=['pdf', 'xlsx', 'xls', 'csv'],
        help="Acceptăm PDF sau Excel de la eMAG"
    )
    
    if uploaded_aviz:
        st.success(f"✅ Fișier încărcat: **{uploaded_aviz.name}**")
        
        # ═══════════════════════════════════════════════════════
        # INTRODUCERE MANUALĂ DATE AVIZ
        # ═══════════════════════════════════════════════════════
        
        st.divider()
        st.subheader("📋 Detalii Aviz")
        
        col1, col2 = st.columns(2)
        
        with col1:
            aviz_number = st.text_input(
                "Număr Aviz",
                placeholder="Ex: 36898183",
                help="Numărul avizului de plată"
            )
            
            suma_aviz = st.number_input(
                "💰 Suma Totală din Aviz (RON)",
                min_value=-100000.0,
                max_value=100000.0,
                value=0.0,
                step=0.01,
                format="%.2f",
                help="Suma finală din aviz (poate fi pozitivă sau negativă)"
            )
        
        with col2:
            perioada_start = st.date_input(
                "📅 Perioada Start",
                help="Prima zi din perioada avizului"
            )
            
            perioada_end = st.date_input(
                "📅 Perioada End",
                help="Ultima zi din perioada avizului"
            )
        
        st.divider()
        
        # ═══════════════════════════════════════════════════════
        # DETALII LINII AVIZ (OPȚIONAL)
        # ═══════════════════════════════════════════════════════
        
        with st.expander("📝 Detalii Linii Aviz (opțional - pentru analiză detaliată)"):
            st.caption("Dacă vrei verificare detaliată, introdu sumele din aviz:")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                incasari_aviz = st.number_input(
                    "➕ Încasări Comenzi",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.2f"
                )
                
                comisioane_aviz = st.number_input(
                    "➖ Comisioane eMAG",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.2f"
                )
            
            with col2:
                vouchere_aviz = st.number_input(
                    "➖ Vouchere",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.2f"
                )
                
                taxe_livrare_aviz = st.number_input(
                    "➖ Taxe Livrare",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.2f"
                )
            
            with col3:
                taxe_retur_aviz = st.number_input(
                    "➖ Taxe Retur",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.2f"
                )
                
                altele_aviz = st.number_input(
                    "➖ Altele (ajustări)",
                    min_value=0.0,
                    value=0.0,
                    step=0.01,
                    format="%.2f"
                )
        
        # ═══════════════════════════════════════════════════════
        # BUTON RECONCILIERE
        # ═══════════════════════════════════════════════════════
        
        if st.button("🔍 Reconciliază cu P&L", type="primary"):
            
            if not aviz_number or suma_aviz == 0:
                st.warning("⚠️ Te rog completează Număr Aviz și Suma Totală")
                st.stop()
            
            if not perioada_start or not perioada_end:
                st.warning("⚠️ Te rog selectează perioada avizului")
                st.stop()
            
            if perioada_end < perioada_start:
                st.error("❌ Perioada End trebuie să fie după Perioada Start")
                st.stop()
            
            # ═══════════════════════════════════════════════════════
            # CALCUL DIN P&L
            # ═══════════════════════════════════════════════════════
            
            conn = get_db_connection()
            if not conn:
                st.error("❌ Nu pot conecta la baza de date")
                st.stop()
            
            try:
                cursor = conn.cursor(cursor_factory=RealDictCursor)
                
                st.info(f"🔄 Calculez din P&L pentru perioada: {perioada_start.strftime('%d/%m/%Y')} - {perioada_end.strftime('%d/%m/%Y')}...")
                
                cursor.execute("""
                    SELECT 
                        COUNT(*) as nr_comenzi,
                        COUNT(DISTINCT id_comanda) as comenzi_unice,
                        SUM(cantitate) as total_bucati,
                        ROUND(SUM(vanzari), 2) as total_vanzari,
                        ROUND(SUM(taxa_livrare), 2) as total_taxa_livrare,
                        ROUND(SUM(taxa_retur), 2) as total_taxa_retur,
                        ROUND(SUM(valoare_retinuta), 2) as total_valoare_retinuta,
                        ROUND(SUM(comision), 2) as total_comision,
                        ROUND(SUM(comision_anulate), 2) as total_comision_anulate,
                        ROUND(SUM(comision_taxa_livrare), 2) as total_comision_taxa_livrare,
                        ROUND(SUM(depozitare_fbe), 2) as total_depozitare_fbe,
                        ROUND(SUM(operatiuni_fbe), 2) as total_operatiuni_fbe,
                        ROUND(SUM(cost_livrare), 2) as total_cost_livrare,
                        ROUND(SUM(cost_retur), 2) as total_cost_retur,
                        ROUND(SUM(vanzari_nete), 2) as total_vanzari_nete
                    FROM emag_order_lines
                    WHERE data BETWEEN %s AND %s
                """, (perioada_start, perioada_end))
                
                pl_data = cursor.fetchone()
                cursor.close()
                conn.close()
                
                # ═══════════════════════════════════════════════════════
                # AFIȘARE REZULTATE
                # ═══════════════════════════════════════════════════════
                
                st.divider()
                st.success("✅ Reconciliere completă!")
                
                # Comparare suma totală
                diferenta = suma_aviz - pl_data['total_vanzari_nete']
                diferenta_procent = (diferenta / suma_aviz * 100) if suma_aviz != 0 else 0
                
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric(
                        "💰 Suma din Aviz",
                        f"{suma_aviz:,.2f} RON",
                        help="Suma totală din avizul de plată eMAG"
                    )
                
                with col2:
                    st.metric(
                        "📊 Suma din P&L",
                        f"{pl_data['total_vanzari_nete']:,.2f} RON",
                        help="Suma calculată din rapoartele P&L pentru aceeași perioadă"
                    )
                
                with col3:
                    delta_color = "off" if abs(diferenta) < 1 else ("normal" if diferenta >= 0 else "inverse")
                    
                    if abs(diferenta) < 1:
                        st.metric(
                            "✅ Diferență",
                            f"{diferenta:,.2f} RON",
                            delta="MATCH PERFECT! ✓",
                            delta_color=delta_color
                        )
                    elif abs(diferenta) < 10:
                        st.metric(
                            "⚠️ Diferență Mică",
                            f"{diferenta:,.2f} RON",
                            delta=f"{abs(diferenta_procent):.2f}%",
                            delta_color=delta_color,
                            help="Diferență acceptabilă (probabil rotunjiri)"
                        )
                    else:
                        st.metric(
                            "❌ Diferență Mare",
                            f"{diferenta:,.2f} RON",
                            delta=f"{abs(diferenta_procent):.2f}%",
                            delta_color=delta_color,
                            help="Diferență semnificativă - investigare necesară!"
                        )
                
                st.divider()
                
                # ═══════════════════════════════════════════════════════
                # DETALII RECONCILIERE
                # ═══════════════════════════════════════════════════════
                
                st.subheader("📋 Detalii Reconciliere")
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("### 💰 Date din Aviz")
                    st.metric("Număr Aviz", aviz_number)
                    st.metric("Perioada", f"{perioada_start.strftime('%d/%m/%Y')} - {perioada_end.strftime('%d/%m/%Y')}")
                    st.metric("Suma Totală", f"{suma_aviz:,.2f} RON")
                    
                    if incasari_aviz > 0:
                        st.caption(f"➕ Încasări: {incasari_aviz:,.2f} RON")
                        st.caption(f"➖ Comisioane: {comisioane_aviz:,.2f} RON")
                        st.caption(f"➖ Vouchere: {vouchere_aviz:,.2f} RON")
                        st.caption(f"➖ Taxe livrare: {taxe_livrare_aviz:,.2f} RON")
                
                with col2:
                    st.markdown("### 📊 Date din P&L")
                    st.metric("Comenzi în perioadă", f"{pl_data['nr_comenzi']} linii ({pl_data['comenzi_unice']} comenzi)")
                    st.metric("Total Produse", f"{pl_data['total_bucati']} bucăți")
                    st.metric("Vânzări Nete", f"{pl_data['total_vanzari_nete']:,.2f} RON")
                    
                    with st.expander("🔍 Detalii calcul"):
                        st.caption(f"➕ Vânzări: {pl_data['total_vanzari']:,.2f} RON")
                        st.caption(f"➖ Comisioane: {pl_data['total_comision']:,.2f} RON")
                        st.caption(f"➖ Valoare reținută: {pl_data['total_valoare_retinuta']:,.2f} RON")
                        st.caption(f"➖ Taxe livrare: {pl_data['total_taxa_livrare']:,.2f} RON")
                        st.caption(f"➖ Taxe retur: {pl_data['total_taxa_retur']:,.2f} RON")
                        st.caption(f"➖ Depozitare FBE: {pl_data['total_depozitare_fbe']:,.2f} RON")
                        st.caption(f"➖ Operațiuni FBE: {pl_data['total_operatiuni_fbe']:,.2f} RON")
                
                # ═══════════════════════════════════════════════════════
                # ANALIZĂ DIFERENȚĂ
                # ═══════════════════════════════════════════════════════
                
                if abs(diferenta) >= 10:
                    st.divider()
                    st.warning("⚠️ **DIFERENȚĂ SEMNIFICATIVĂ DETECTATĂ!**")
                    
                    st.markdown("""
                    ### Posibile cauze:
                    
                    1. **Retururi procesate după P&L**
                       - eMAG a procesat retururi care nu apar încă în raportul P&L
                       - Soluție: Descarcă un P&L mai recent
                    
                    2. **Facturi comision emise ulterior**
                       - Comisioane suplimentare facturate separat
                       - Verifică dacă ai primit facturi C-MKTP în aviz
                    
                    3. **Comenzi anulate/stornate**
                       - Comenzi care apar în P&L dar au fost stornate
                       - Verifică statusul comenzilor în platformă
                    
                    4. **Ajustări manuale eMAG**
                       - Penalități, bonusuri, corecții
                       - Contactează suportul eMAG pentru detalii
                    
                    5. **Perioada diferită**
                       - Verifică că perioada din P&L coincide exact cu cea din aviz
                       - eMAG folosește timestamp-uri precise
                    """)
                
                elif abs(diferenta) >= 1:
                    st.info("ℹ️ Diferență mică - probabil erori de rotunjire sau comisioane sub 1 RON")
                
                else:
                    st.success("✅ **MATCH PERFECT!** Avizul coincide 100% cu P&L-ul.")
                
            except Exception as e:
                st.error(f"❌ Eroare la reconciliere: {e}")
                st.exception(e)
                if conn:
                    conn.close()
    
    else:
        st.markdown("""
        ### 📖 Cum să folosești Reconcilierea:
        
        1. **Primești aviz de plată** de la eMAG (email sau platformă)
        2. **Download PDF/Excel** al avizului
        3. **Upload aici** și completează datele
        4. **Aplicația calculează automat** din P&L pentru aceeași perioadă
        5. **Vezi diferențele** și cauzele posibile
        
        ### 💡 Tips:
        
        - Asigură-te că ai upload-at **toate rapoartele P&L** pentru perioada avizului
        - Verifică că **perioada** din aviz coincide exact cu cea selectată
        - Diferențe sub **5 RON** sunt normale (rotunjiri)
        - Diferențe peste **10 RON** necesită investigare
        """)


# ═══════════════════════════════════════════════════════
# TAB 4: RAPOARTE
# ═══════════════════════════════════════════════════════

with tab4:
    st.header("📈 Rapoarte Profit")
    st.info("🚧 **În dezvoltare** - Aici vei genera rapoarte financiare")
    
    st.markdown("""
    ### Funcționalitate viitoare:
    
    1. **Raport profit lunar** - Profit per lună
    2. **Raport per produs** - Top produse profitabile
    3. **Raport comenzi stornate** - Impact retururi
    4. **Export PDF/Excel** - Rapoarte descărcabile
    5. **Grafice** - Vizualizare trends
    """)
