# ServicePack Inventory & Profitability (Streamlit)

Un MVP web-based (Streamlit) pentru gestionarea produselor, prețurilor, profitabilității, concurenței și generarea de rapoarte din exporturi SmartBill.

## Funcționalități cheie
- Import fișiere XLSX/CSV cu produse (de ex. formatul curent: coloane COD, NUME, PRET ACHIZITIE, PRET VANZARE, PROFIT etc.).
- Normalizare automată a coloanelor (ignoră coloanele `Unnamed`).
- Salvare în SQLite.
- Dashboard profitabilitate (margină %, markup, top profit).
- Concurență: câmpuri de preț competitor per produs.
- Import fișiere de **mișcări stocuri** din SmartBill (vânzări ieșiri) -> calculează viteză de vânzare, zile de stoc, propuneri comenzi.
- Raport "Ce comand azi": în funcție de zile țintă de acoperire și lead-time.
- Export rapoarte în Excel.

## Cum rulezi
1. Instalează dependențe (ideal într-un mediu virtual):
   ```bash
   pip install -r requirements.txt
   ```
2. Rulează aplicația:
   ```bash
   streamlit run app.py
   ```
3. Aplicația pornește local (web). Pentru acces extern, rulează pe un server sau partajează prin Cloudflare Tunnel/Streamlit Cloud.

## Notă
- Modelul de date este minimal și extensibil.
- Pentru SmartBill, exportă mișcările de stoc (XLSX/CSV) și importă-le în pagina "Mișcări & Comenzi".