import streamlit as st
import pandas as pd
import numpy as np
from db import ensure_db, Product, StockMove, Inventory
from sqlalchemy import select, func, delete
from utils import normalize_columns, map_product_columns, normalize_stock_moves
from datetime import date, timedelta
from io import BytesIO

st.set_page_config(page_title="ServicePack Inventory & Profitability", layout="wide")

st.title("ServicePack – Inventar, Profitabilitate & Comenzi")
st.caption("MVP – import produse, mișcări, analize profit și recomandări de comandă")

session = ensure_db()

tabs = st.tabs(["📦 Produse", "📈 Profitabilitate", "🔁 Mișcări & Comenzi", "⚙️ Admin"])

with tabs[0]:
    st.subheader("Import produse")
    up = st.file_uploader("Încarcă XLSX/CSV (ex: exportul existent – foaia *Samsung*)", type=["xlsx","csv"])
    if up is not None:
        if up.name.endswith(".xlsx"):
            df = pd.read_excel(up)
        else:
            df = pd.read_csv(up)
        raw_rows = len(df)
        df = normalize_columns(df)
        norm = map_product_columns(df)
        st.write("Previzualizare coloană normalizată:")
        st.dataframe(norm.head(30), use_container_width=True)

        if st.button("Salvează / Actualizează produse în DB"):
            # upsert by code
            from db import SessionLocal
            s = SessionLocal()
            inserted, updated = 0, 0
            for _, r in norm.iterrows():
                prod = s.query(Product).filter(Product.code==r["code"]).one_or_none()
                if prod is None:
                    prod = Product(code=r["code"])
                    inserted += 1
                else:
                    updated += 1
                prod.name = r.get("name")
                prod.purchase_price = r.get("purchase_price")
                prod.sale_price = r.get("sale_price")
                prod.profit_abs = r.get("profit_abs")
                prod.sale_price_minus20 = r.get("sale_price_minus20")
                prod.profit_minus20 = r.get("profit_minus20")
                # keep competitor_price if provided
                if not pd.isna(r.get("competitor_price")):
                    prod.competitor_price = r.get("competitor_price")
                s.merge(prod)
            s.commit()
            s.close()
            st.success(f"Import finalizat. Inserate: {inserted}, actualizate: {updated} (din {raw_rows} rânduri brute).")

    st.divider()
    st.subheader("Editează preț concurență (manual)")
    from db import SessionLocal
    s = SessionLocal()
    prods = s.query(Product).order_by(Product.code).limit(1000).all()
    if prods:
        sel = st.selectbox("Alege produs", options=[(p.code, p.name) for p in prods], format_func=lambda x: f"{x[0]} – {x[1]}" if x else "")
        if sel:
            code = sel[0]
            p = s.query(Product).filter(Product.code==code).first()
            new_price = st.number_input("Preț concurență", value=float(p.competitor_price or 0), step=0.01)
            if st.button("Salvează preț concurență"):
                p.competitor_price = new_price
                s.commit()
                st.success("Salvat.")
    s.close()

with tabs[1]:
    st.subheader("Analiză profitabilitate")
    from db import SessionLocal
    s = SessionLocal()
    rows = s.query(Product).all()
    s.close()
    if rows:
        dfp = pd.DataFrame([{
            "code": r.code,
            "name": r.name,
            "purchase_price": r.purchase_price,
            "sale_price": r.sale_price,
            "competitor_price": r.competitor_price,
            "gross_margin_pct": ((r.sale_price - r.purchase_price) / r.sale_price * 100) if (r.sale_price and r.purchase_price is not None and r.sale_price>0) else np.nan,
            "markup_pct": ((r.sale_price - r.purchase_price) / r.purchase_price * 100) if (r.sale_price and r.purchase_price and r.purchase_price>0) else np.nan,
            "price_vs_competition": (r.sale_price - r.competitor_price) if (r.sale_price and r.competitor_price) else np.nan
        } for r in rows])
        st.dataframe(dfp, use_container_width=True)
        st.caption("• gross_margin_pct = (sale - purchase) / sale; • markup_pct = (sale - purchase) / purchase; • price_vs_competition = sale - competitor.")
        top_profit = dfp.sort_values("gross_margin_pct", ascending=False).head(20)
        st.markdown("**Top 20 după marjă brută (%)**")
        st.dataframe(top_profit[["code","name","gross_margin_pct","sale_price","purchase_price"]], use_container_width=True)

        # Export
        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as xl:
            dfp.to_excel(xl, sheet_name="profitabilitate", index=False)
            top_profit.to_excel(xl, sheet_name="top_margins", index=False)
        st.download_button("Descarcă Excel (profitabilitate)", data=out.getvalue(), file_name="profitabilitate.xlsx")

with tabs[2]:
    st.subheader("Import mișcări stocuri (SmartBill)")
    up2 = st.file_uploader("Încarcă XLSX/CSV cu mișcări", type=["xlsx","csv"], key="moves")
    if up2 is not None:
        if up2.name.endswith(".xlsx"):
            mdf = pd.read_excel(up2)
        else:
            mdf = pd.read_csv(up2)
        st.write("Previzualizare fișier mișcări:")
        st.dataframe(mdf.head(20), use_container_width=True)
        norm_moves = normalize_stock_moves(mdf)
        st.write("Mișcări mapate:")
        st.dataframe(norm_moves.head(30), use_container_width=True)

        if st.button("Salvează mișcări în DB"):
            from db import SessionLocal
            s = SessionLocal()
            for _, r in norm_moves.iterrows():
                mv = StockMove(product_code=r["product_code"], date=r["date"], qty=r["qty"], source="SmartBill")
                s.add(mv)
            s.commit()
            s.close()
            st.success(f"Importat {len(norm_moves)} mișcări.")

    st.divider()
    st.subheader("Recomandări comandă")
    days_target = st.number_input("Zile țintă de acoperire", value=30, step=1, min_value=1)
    lead_time = st.number_input("Lead-time aprovizionare (zile)", value=10, step=1, min_value=0)
    from db import SessionLocal
    s = SessionLocal()

    # Sales velocity: medie zilnică pe ultimul X zile
    lookback_days = st.number_input("Fereastră istoric (zile)", value=90, step=1, min_value=7)
    since = date.today() - timedelta(days=int(lookback_days))
    # Get sales (negative moves) by product
    q = s.query(StockMove.product_code, func.sum(StockMove.qty).label("qty_sum")).filter(StockMove.date>=since).group_by(StockMove.product_code)
    sales = pd.DataFrame(q.all(), columns=["product_code","qty_sum"])
    # negative are sales; daily velocity = -qty_sum / lookback_days
    if not sales.empty:
        sales["daily_velocity"] = (-sales["qty_sum"].clip(upper=0)) / lookback_days
    else:
        sales = pd.DataFrame(columns=["product_code","qty_sum","daily_velocity"])

    # Inventory on hand (optional; if not present, assume 0)
    inv = pd.read_sql_query("SELECT product_code, on_hand FROM inventory", s.bind)
    # Products table (to include names)
    products = pd.read_sql_query("SELECT code as product_code, name FROM products", s.bind)

    dfm = products.merge(sales, on="product_code", how="left").merge(inv, on="product_code", how="left")
    dfm["on_hand"] = dfm["on_hand"].fillna(0)
    dfm["daily_velocity"] = dfm["daily_velocity"].fillna(0)
    dfm["days_of_stock"] = dfm.apply(lambda r: (r["on_hand"] / r["daily_velocity"]) if r["daily_velocity"]>0 else np.inf, axis=1)

    # Reorder quantity = max(0, (days_target + lead_time) * daily_velocity - on_hand)
    dfm["reorder_qty"] = ((days_target + lead_time) * dfm["daily_velocity"] - dfm["on_hand"]).clip(lower=0).round()

    recs = dfm[(dfm["reorder_qty"] > 0) & (dfm["daily_velocity"] > 0)].sort_values("reorder_qty", ascending=False)
    st.dataframe(recs.head(200), use_container_width=True)
    if not recs.empty:
        out2 = BytesIO()
        with pd.ExcelWriter(out2, engine="openpyxl") as xl:
            recs.to_excel(xl, sheet_name="reorder", index=False)
        st.download_button("Descarcă recomandări (Excel)", data=out2.getvalue(), file_name="recomandari_comenzi.xlsx")

    s.close()

with tabs[3]:
    st.subheader("Administrare stoc curent (opțional)")
    st.write("Import inventar curent (product_code, on_hand)")
    inv_up = st.file_uploader("Încarcă CSV/XLSX inventar", type=["csv","xlsx"], key="inv")
    if inv_up is not None:
        if inv_up.name.endswith(".xlsx"):
            idf = pd.read_excel(inv_up)
        else:
            idf = pd.read_csv(inv_up)
        st.dataframe(idf.head(20), use_container_width=True)
        if st.button("Salvează inventar"):
            from db import SessionLocal
            s = SessionLocal()
            # clear table (simple approach for MVP)
            s.execute(delete(Inventory))
            s.commit()
            for _, r in idf.iterrows():
                code_col = [c for c in idf.columns if str(c).lower() in ["product_code","cod","sku","code"]][0]
                on_hand_col = [c for c in idf.columns if "on_hand" in str(c).lower() or "stoc" in str(c).lower() or "qty" in str(c).lower()][0]
                s.add(Inventory(product_code=str(r[code_col]).strip(), on_hand=float(r[on_hand_col])))
            s.commit()
            s.close()
            st.success("Inventar actualizat.")