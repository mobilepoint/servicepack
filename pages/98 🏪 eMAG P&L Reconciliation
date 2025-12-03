import streamlit as st
import pandas as pd
from datetime import datetime
from supabase import create_client, Client

# ═══════════════════════════════════════════════════════
# CONFIGURARE PAGINĂ
# ═══════════════════════════════════════════════════════

st.set_page_config(
    page_title="🏪 eMAG P&L Reconciliation",
    page_icon="🏪",
    layout="wide"
)

# ═══════════════════════════════════════════════════════
# CONEXIUNE SUPABASE
# ═══════════════════════════════════════════════════════

@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_supabase()

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
    
    # ═══════════════════════════════════════
    # FUNCȚII HELPER PENTRU UPLOAD
    # ═══════════════════════════════════════
    
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
        df_clean['data'] = pd.to_datetime(df_clean['data'], format='%d/%m/%Y', errors='coerce')
        
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
        
        df_clean = df_clean.where(pd.notnull(df_clean), None)
        return df_clean
    
    
    def upsert_order_line(row, batch_id, file_name):
        """Insert sau Update o linie în DB"""
        data_dict = {
            'data': row['data'].strftime('%Y-%m-%d') if pd.notnull(row['data']) else None,
            'seller': row.get('seller'),
            'id_comanda': int(row['id_comanda']) if pd.notnull(row['id_comanda']) else None,
            'id_produs': int(row['id_produs']) if pd.notnull(row['id_produs']) else None,
            'ean': str(row.get('ean')) if pd.notnull(row.get('ean')) else None,
            'sku': str(row.get('sku')) if pd.notnull(row.get('sku')) else None,
            'pnk': row.get('pnk'),
            'brand': row.get('brand'),
            'produs': row.get('produs'),
            'tip_desfasurator': row.get('tip_desfasurator'),
            'cantitate': int(row['cantitate']) if pd.notnull(row['cantitate']) else 0,
            'vanzari': float(row['vanzari']) if pd.notnull(row['vanzari']) else 0.0,
            'taxa_livrare': float(row['taxa_livrare']) if pd.notnull(row['taxa_livrare']) else 0.0,
            'taxa_retur': float(row['taxa_retur']) if pd.notnull(row['taxa_retur']) else 0.0,
            'valoare_retinuta': float(row['valoare_retinuta']) if pd.notnull(row['valoare_retinuta']) else 0.0,
            'comision': float(row['comision']) if pd.notnull(row['comision']) else 0.0,
            'comision_anulate': float(row['comision_anulate']) if pd.notnull(row['comision_anulate']) else 0.0,
            'comision_taxa_livrare': float(row['comision_taxa_livrare']) if pd.notnull(row['comision_taxa_livrare']) else 0.0,
            'depozitare_fbe': float(row['depozitare_fbe']) if pd.notnull(row['depozitare_fbe']) else 0.0,
            'operatiuni_fbe': float(row['operatiuni_fbe']) if pd.notnull(row['operatiuni_fbe']) else 0.0,
            'cost_livrare': float(row['cost_livrare']) if pd.notnull(row['cost_livrare']) else 0.0,
            'cost_retur': float(row['cost_retur']) if pd.notnull(row['cost_retur']) else 0.0,
            'vanzari_nete': float(row['vanzari_nete']) if pd.notnull(row['vanzari_nete']) else 0.0,
            'profit_net': float(row['vanzari_nete']) if pd.notnull(row['vanzari_nete']) else 0.0,
            'upload_batch_id': batch_id,
            'excel_source_file': file_name
        }
        
        response = supabase.table('emag_order_lines').upsert(
            data_dict,
            on_conflict='id_comanda,tip_desfasurator'
        ).execute()
        
        return response
    
    # ═══════════════════════════════════════
    # UI UPLOAD
    # ═══════════════════════════════════════
    
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
                        upsert_order_line(row, batch_id, uploaded_file.name)
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
                
            except Exception as e:
                st.error(f"❌ Eroare la procesare: {e}")
                st.exception(e)

# ═══════════════════════════════════════════════════════
# TAB 2: DASHBOARD
# ═══════════════════════════════════════════════════════

with tab2:
    st.header("📊 Dashboard Comenzi")
    st.info("🚧 **În dezvoltare** - Aici vei vedea toate comenzile și profitul")
    
    # Placeholder pentru viitor
    try:
        result = supabase.table('emag_order_lines').select('*', count='exact').limit(10).execute()
        
        if result.data:
            st.success(f"✅ Găsite {len(result.data)} linii în DB")
            st.dataframe(result.data)
        else:
            st.warning("⚠️ Nu există date în baza de date. Upload un fișier P&L mai întâi.")
    except Exception as e:
        st.error(f"❌ Eroare: {e}")

# ═══════════════════════════════════════════════════════
# TAB 3: RECONCILIERE
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("💰 Reconciliere Avize Plată")
    st.info("🚧 **În dezvoltare** - Aici vei face matching cu avizele de plată")

# ═══════════════════════════════════════════════════════
# TAB 4: RAPOARTE
# ═══════════════════════════════════════════════════════

with tab4:
    st.header("📈 Rapoarte Profit")
    st.info("🚧 **În dezvoltare** - Aici vei genera rapoarte financiare")
