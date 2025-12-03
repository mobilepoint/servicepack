import streamlit as st
import requests
import base64
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Test eMAG API",
    page_icon="🔬",
    layout="wide"
)

st.title("🔬 Test eMAG API - Descoperire Rapoarte")

st.info("""
**Obiectiv:** Să vedem EXACT ce rapoarte/facturi putem extrage din eMAG API

**Ce testăm:**
1. Categorii de facturi disponibile (`/invoice/categories`)
2. Lista facturi din ultimele 30 zile (`/invoice/read`)
3. Căutare factură specifică după număr
""")

# ═══════════════════════════════════════════════════════
# TEST 1: CATEGORII FACTURI
# ═══════════════════════════════════════════════════════

st.header("📋 Test 1: Categorii Facturi")

st.write("Endpoint: `POST /api-3/invoice/categories`")

if st.button("🔍 Rulează Test 1", type="primary", key="test1"):
    try:
        emag_username = st.secrets["connections"]["emag"]["USERNAME"]
        emag_password = st.secrets["connections"]["emag"]["PASSWORD"]
        emag_api_url = st.secrets["connections"]["emag"]["API_URL"]
        
        credentials = f"{emag_username}:{emag_password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }
        
        with st.spinner("🔄 Calling API..."):
            response = requests.post(
                f"{emag_api_url}/invoice/categories",
                headers=headers,
                json={},
                timeout=30
            )
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("HTTP Status", response.status_code)
        with col2:
            status_color = "🟢" if response.status_code == 200 else "🔴"
            st.metric("Status", f"{status_color} {'OK' if response.status_code == 200 else 'ERROR'}")
        
        if response.status_code == 200:
            data = response.json()
            
            st.success("✅ API Call Success!")
            
            with st.expander("📄 Răspuns complet JSON"):
                st.json(data)
            
            if data.get('results'):
                st.subheader("📊 Categorii găsite:")
                categories = data['results']
                
                if isinstance(categories, list):
                    df_cat = pd.DataFrame(categories)
                    st.dataframe(df_cat, use_container_width=True)
                else:
                    st.write(categories)
        else:
            st.error(f"❌ API Error: {response.status_code}")
            st.code(response.text)
            
    except Exception as e:
        st.error(f"❌ Exception: {e}")
        st.exception(e)

st.divider()

# ═══════════════════════════════════════════════════════
# TEST 2: LISTA FACTURI (ULTIMELE 30 ZILE)
# ═══════════════════════════════════════════════════════

st.header("📄 Test 2: Lista Facturi")

st.write("Endpoint: `POST /api-3/invoice/read`")

col1, col2 = st.columns(2)

with col1:
    days_back = st.number_input("📅 Zile înapoi", min_value=1, max_value=90, value=30)

with col2:
    category_filter = st.text_input("🏷️ Filtru categorie (opțional)", placeholder="Ex: FC, VC")

if st.button("🔍 Rulează Test 2", type="primary", key="test2"):
    try:
        emag_username = st.secrets["connections"]["emag"]["USERNAME"]
        emag_password = st.secrets["connections"]["emag"]["PASSWORD"]
        emag_api_url = st.secrets["connections"]["emag"]["API_URL"]
        
        credentials = f"{emag_username}:{emag_password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }
        
        # Calculează perioada
        date_end = datetime.now().strftime('%Y-%m-%d')
        date_start = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        
        payload = {
            "datestart": date_start,
            "dateend": date_end
        }
        
        if category_filter.strip():
            payload["category"] = category_filter.strip()
        
        st.info(f"📅 **Perioada:** {date_start} → {date_end}")
        st.code(payload, language="json")
        
        with st.spinner("🔄 Calling API..."):
            response = requests.post(
                f"{emag_api_url}/invoice/read",
                headers=headers,
                json=payload,
                timeout=60
            )
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("HTTP Status", response.status_code)
        with col2:
            status_color = "🟢" if response.status_code == 200 else "🔴"
            st.metric("Status", f"{status_color} {'OK' if response.status_code == 200 else 'ERROR'}")
        
        if response.status_code == 200:
            data = response.json()
            
            st.success("✅ API Call Success!")
            
            st.write(f"**isError:** {data.get('isError')}")
            st.write(f"**Messages:** {data.get('messages')}")
            
            if data.get('results'):
                invoices = data['results'] if isinstance(data['results'], list) else [data['results']]
                
                st.success(f"🎉 **Găsite: {len(invoices)} facturi**")
                
                # Tabel sumar
                st.subheader("📊 Tabel facturi")
                
                df_invoices = pd.DataFrame([
                    {
                        'Număr': inv.get('number'),
                        'Categorie': inv.get('category'),
                        'Nume': inv.get('name'),
                        'Data': inv.get('date'),
                        'Order ID': inv.get('orderid'),
                        'Total cu TVA': inv.get('totalwithvat'),
                        'Total fără TVA': inv.get('totalwithoutvat'),
                        'TVA': inv.get('vatvalue'),
                        'Storno': '✅' if inv.get('isstorno') else ''
                    }
                    for inv in invoices
                ])
                
                st.dataframe(df_invoices, use_container_width=True, height=400)
                
                # Statistici pe categorii
                st.divider()
                st.subheader("📈 Statistici pe categorii")
                
                categories_count = {}
                categories_sum = {}
                
                for inv in invoices:
                    cat = inv.get('category', 'Unknown')
                    name = inv.get('name', 'Unknown')
                    key = f"{cat} - {name}"
                    
                    categories_count[key] = categories_count.get(key, 0) + 1
                    
                    total = float(inv.get('totalwithvat', 0) or 0)
                    categories_sum[key] = categories_sum.get(key, 0) + total
                
                df_stats = pd.DataFrame([
                    {
                        'Categorie': cat,
                        'Număr facturi': count,
                        'Sumă totală': f"{categories_sum[cat]:,.2f} RON"
                    }
                    for cat, count in sorted(categories_count.items(), key=lambda x: x[1], reverse=True)
                ])
                
                st.dataframe(df_stats, use_container_width=True)
                
                # JSON complet (primele 3)
                with st.expander(f"🔍 Vezi JSON complet (primele 3 din {len(invoices)})"):
                    st.json(invoices[:3])
                
            else:
                st.warning("⚠️ Nu există facturi în perioada selectată")
                with st.expander("📄 Răspuns API complet"):
                    st.json(data)
        else:
            st.error(f"❌ API Error: {response.status_code}")
            st.code(response.text)
            
    except Exception as e:
        st.error(f"❌ Exception: {e}")
        st.exception(e)

st.divider()

# ═══════════════════════════════════════════════════════
# TEST 3: CĂUTARE FACTURĂ SPECIFICĂ
# ═══════════════════════════════════════════════════════

st.header("🔎 Test 3: Căutare Factură Specifică")

st.write("Endpoint: `POST /api-3/invoice/read` (cu filtru `number`)")

invoice_number_test = st.text_input(
    "🔢 Număr factură",
    placeholder="Ex: C-MKTP-4990846",
    help="Introdu un număr de factură din avizul tău"
)

if st.button("🔍 Caută Factură", type="primary", key="test3"):
    if not invoice_number_test.strip():
        st.warning("⚠️ Te rog introdu un număr de factură!")
    else:
        try:
            emag_username = st.secrets["connections"]["emag"]["USERNAME"]
            emag_password = st.secrets["connections"]["emag"]["PASSWORD"]
            emag_api_url = st.secrets["connections"]["emag"]["API_URL"]
            
            credentials = f"{emag_username}:{emag_password}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            
            headers = {
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/json"
            }
            
            payload = {"number": invoice_number_test.strip()}
            
            st.code(payload, language="json")
            
            with st.spinner("🔄 Calling API..."):
                response = requests.post(
                    f"{emag_api_url}/invoice/read",
                    headers=headers,
                    json=payload,
                    timeout=30
                )
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("HTTP Status", response.status_code)
            with col2:
                status_color = "🟢" if response.status_code == 200 else "🔴"
                st.metric("Status", f"{status_color} {'OK' if response.status_code == 200 else 'ERROR'}")
            
            if response.status_code == 200:
                data = response.json()
                st.success("✅ API Call Success!")
                
                if data.get('results'):
                    invoice = data['results'][0] if isinstance(data['results'], list) else data['results']
                    
                    st.subheader("📄 Detalii factură")
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Număr", invoice.get('number'))
                        st.metric("Categorie", invoice.get('category'))
                    with col2:
                        st.metric("Nume", invoice.get('name'))
                        st.metric("Data", invoice.get('date'))
                    with col3:
                        st.metric("Order ID", invoice.get('orderid'))
                        st.metric("Total cu TVA", f"{invoice.get('totalwithvat')} RON")
                    
                    st.divider()
                    
                    with st.expander("📄 JSON complet"):
                        st.json(invoice)
                else:
                    st.warning("⚠️ Factura nu a fost găsită")
                    st.json(data)
            else:
                st.error(f"❌ API Error: {response.status_code}")
                st.code(response.text)
                
        except Exception as e:
            st.error(f"❌ Exception: {e}")
            st.exception(e)

# ═══════════════════════════════════════════════════════
# INSTRUCȚIUNI
# ═══════════════════════════════════════════════════════

st.divider()

with st.expander("💡 Cum să folosești acest tool"):
    st.markdown("""
    ### Pași:
    
    1. **Test 1** - Verifică ce categorii de facturi există în sistem
    2. **Test 2** - Vezi toate facturile din ultimele 30 zile (sau alt interval)
    3. **Test 3** - Caută o factură specifică din avizul tău (ex: C-MKTP-4990846)
    
    ### Ce căutăm:
    
    - **Categorii disponibile** - FC (Commission), VC (Voucher), etc.
    - **Structura răspunsului** - Ce câmpuri returnează API-ul
    - **Link cu comenzile** - Câmpul `orderid` ne permite să facem match cu P&L
    
    ### După teste:
    
    Vom implementa sincronizarea automată a facturilor și reconcilierea cu avizele.
    """)
