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
    st.header("📊 Dashboard Comenzi")
    
    conn = get_db_connection()
    if conn:
        try:
            # Statistici generale
            stats_db = get_db_stats(conn)
            if stats_db:
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("📦 Total linii", stats_db['total_lines'])
                with col2:
                    st.metric("🛒 Comenzi unice", stats_db['unique_orders'])
                with col3:
                    profit_color = "normal" if stats_db['total_profit'] >= 0 else "inverse"
                    st.metric("💰 Profit total", f"{stats_db['total_profit']:.2f} RON", delta_color=profit_color)
            
            st.divider()
            
            # Tabel cu ultimele 20 linii
            st.subheader("📋 Ultimele 20 comenzi")
            
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT 
                    data, id_comanda, sku, produs, tip_desfasurator, cantitate,
                    vanzari, comision, vanzari_nete, profit_net
                FROM emag_order_lines 
                ORDER BY data DESC, id_comanda DESC 
                LIMIT 20
            """)
            rows = cursor.fetchall()
            cursor.close()
            
            if rows:
                df_display = pd.DataFrame(rows)
                
                # Format date
                df_display['data'] = pd.to_datetime(df_display['data']).dt.strftime('%d/%m/%Y')
                
                # Format numeric
                for col in ['vanzari', 'comision', 'vanzari_nete', 'profit_net']:
                    if col in df_display.columns:
                        df_display[col] = df_display[col].apply(lambda x: f"{x:.2f}" if pd.notnull(x) else "0.00")
                
                st.dataframe(df_display, use_container_width=True, height=600)
            else:
                st.warning("⚠️ Nu există date în baza de date. Upload un fișier P&L mai întâi.")
                
        except Exception as e:
            st.error(f"❌ Eroare: {e}")
        finally:
            conn.close()
    else:
        st.error("❌ Nu pot conecta la baza de date")

# ═══════════════════════════════════════════════════════
# TAB 3: RECONCILIERE
# ═══════════════════════════════════════════════════════

with tab3:
    st.header("💰 Reconciliere Avize Plată")
    st.info("🚧 **În dezvoltare** - Aici vei face matching cu avizele de plată")
    
    st.markdown("""
    ### Funcționalitate viitoare:
    
    1. **Upload aviz plată** (PDF sau Excel)
    2. **Parse automat** linii aviz:
       - Facturi comisioane (C-MKTP)
       - Vouchere (V-MKTP)
       - Încasări COD/Card
    3. **Match cu P&L** consolidat
    4. **Verificare**: Total aviz = Sum(Profit net) din P&L
    5. **Alertă diferențe** dacă nu se potrivește
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
