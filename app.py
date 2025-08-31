import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
import re
from datetime import date
from sqlalchemy import create_engine, text

st.set_page_config(page_title="ServicePack – DB (v3.1 FIX3)", layout="wide")
st.title("ServicePack – Bază de date produse & rapoarte (v3.1 FIX3)")

st.caption("Persistență în Postgres (Neon/Supabase). Auto-migrations ON, CRUD manual, importuri, mapare, rapoarte.")


# ---------------- Helpers ----------------
def get_engine():
    db_url = st.secrets.get("DB_URL") or st.session_state.get("DB_URL")
    if not db_url:
        st.warning("Nu ai setat DB_URL în Secrets. Poți seta temporar mai jos.")
        db_url = st.text_input("DB_URL (temporar, sesiunea curentă)", type="password")
        if db_url:
            st.session_state["DB_URL"] = db_url
    if not db_url:
        return None
    try:
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as e:
        st.error(f"Conexiune DB eșuată: {e}")
        return None


def run_migrations(engine):
    ddl = '''
    CREATE TABLE IF NOT EXISTS products (
        code TEXT PRIMARY KEY,
        name TEXT,
        name_key TEXT,
        grup_sku TEXT,
        purchase_price_no_vat NUMERIC,
        purchase_price_with_vat NUMERIC,
        sale_price_no_vat NUMERIC,
        sale_price_with_vat NUMERIC,
        sale_price_site_109 NUMERIC,
        profit_lei NUMERIC,
        profit_pct NUMERIC,
        competitor_gsmnet NUMERIC,
        competitor_moka NUMERIC,
        competitor_sep NUMERIC,
        competitor_square NUMERIC,
        competitor_ecranegsm NUMERIC,
        competitor_distrizone NUMERIC,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS stock_moves (
        id BIGSERIAL PRIMARY KEY,
        code TEXT REFERENCES products(code) ON DELETE SET NULL,
        product_name TEXT,
        stoc_initial NUMERIC,
        intrari NUMERIC,
        iesiri NUMERIC,
        stoc_final NUMERIC,
        period_start DATE,
        period_end DATE,
        source_tag TEXT,
        uploaded_at TIMESTAMPTZ DEFAULT now()
    );

    CREATE INDEX IF NOT EXISTS idx_moves_code ON stock_moves(code);
    CREATE INDEX IF NOT EXISTS idx_moves_period ON stock_moves(period_start, period_end);
    '''
    with engine.begin() as conn:
        for stmt in ddl.split(';'):
            s = stmt.strip()
            if s:
                conn.execute(text(s))


def norm_name_value(x: str) -> str:
    if x is None:
        return ""
    x = str(x).strip().lower()
    x = re.sub(r"\s+", " ", x)
    return x


def to_num_or_none(x):
    if x is None or str(x).strip() == "":
        return None
    try:
        return float(x)
    except Exception:
        return None


def to_num(s):
    return pd.to_numeric(s, errors="coerce")


def norm_name_series(s: pd.Series) -> pd.Series:
    return s.fillna("").map(norm_name_value)


# ---------------- DB boot ----------------
st.sidebar.header("Bază de date")
engine = get_engine()
if engine:
    # Auto-migrations on startup
    try:
        run_migrations(engine)
        st.sidebar.success("Conexiune OK • Tabele verificate.")
    except Exception as e:
        st.sidebar.error(f"Eroare migrații: {e}")


tabs = st.tabs([
    "✏️ Produse (add/edit)",
    "📦 Import produse în DB",
    "🔁 Import mișcări în DB",
    "🧩 Mapare grup_sku",
    "📊 Rapoarte & Recomandări",
])


# ---------------- Tab 0: CRUD ----------------
with tabs[0]:
    st.subheader("✏️ Adaugă / Editează produse în DB")
    if not engine:
        st.info("Configurează mai întâi conexiunea la DB în sidebar.")
    else:
        with st.expander("🔎 Caută produse"):
            q = st.text_input("Căutare după nume sau cod", value="")
            sql = "select code, name, grup_sku, purchase_price_no_vat, sale_price_no_vat from products"
            params = {}
            if q.strip():
                sql += " where lower(name) like :q or lower(code) like :q"
                params["q"] = f"%{q.lower()}%"
            sql += " order by name limit 500"
            try:
                df_list = pd.read_sql(text(sql), engine, params=params)
                st.dataframe(df_list, use_container_width=True)
            except Exception as e:
                st.warning(f"Nu pot lista produse încă: {e}")

        st.markdown("---")
        colA, colB = st.columns(2)

        # Add
        with colA:
            st.markdown("### ➕ Adaugă produs nou")
            with st.form("add_form", clear_on_submit=True):
                code_new = st.text_input("COD (unic)", key="add_code").strip()
                name_new = st.text_input("NUME", key="add_name")
                grup_sku_new = st.text_input("grup_sku (opțional – un cod din SmartBill)", key="add_group")
                pp_no_vat = st.text_input("Preț intrare fără TVA (C)", key="add_pp_no_vat")
                sp_no_vat = st.text_input("Preț vânzare fără TVA (E)", key="add_sp_no_vat")
                with st.expander("Concurență (opțional)"):
                    c_gsmnet = st.text_input("GSMNET", key="add_cgsm")
                    c_moka = st.text_input("MOKA", key="add_cmoka")
                    c_sep = st.text_input("SEP", key="add_csep")
                    c_square = st.text_input("SQUARE", key="add_csq")
                    c_ecranegsm = st.text_input("ECRANEGSM", key="add_ceg")
                    c_distrizone = st.text_input("DISTRIZONE", key="add_cdz")
                submitted = st.form_submit_button("Adaugă")
                if submitted:
                    if not code_new:
                        st.error("COD este obligatoriu.")
                    else:
                        with engine.begin() as conn:
                            conn.execute(text(
                                "INSERT INTO products(code, name, name_key, grup_sku, "
                                "purchase_price_no_vat, sale_price_no_vat, "
                                "competitor_gsmnet, competitor_moka, competitor_sep, "
                                "competitor_square, competitor_ecranegsm, competitor_distrizone, updated_at) "
                                "VALUES (:code, :name, :nk, nullif(:g,''), :pp, :sp, :cg, :cm, :cs, :csq, :ceg, :cdz, now()) "
                                "ON CONFLICT (code) DO UPDATE SET "
                                "name=EXCLUDED.name, "
                                "name_key=EXCLUDED.name_key, "
                                "grup_sku=COALESCE(NULLIF(EXCLUDED.grup_sku,''), products.grup_sku), "
                                "purchase_price_no_vat=COALESCE(EXCLUDED.purchase_price_no_vat, products.purchase_price_no_vat), "
                                "sale_price_no_vat=COALESCE(EXCLUDED.sale_price_no_vat, products.sale_price_no_vat), "
                                "competitor_gsmnet=COALESCE(EXCLUDED.competitor_gsmnet, products.competitor_gsmnet), "
                                "competitor_moka=COALESCE(EXCLUDED.competitor_moka, products.competitor_moka), "
                                "competitor_sep=COALESCE(EXCLUDED.competitor_sep, products.competitor_sep), "
                                "competitor_square=COALESCE(EXCLUDED.competitor_square, products.competitor_square), "
                                "competitor_ecranegsm=COALESCE(EXCLUDED.competitor_ecranegsm, products.competitor_ecranegsm), "
                                "competitor_distrizone=COALESCE(EXCLUDED.competitor_distrizone, products.competitor_distrizone), "
                                "updated_at=now()"
                            ), {
                                "code": code_new,
                                "name": name_new,
                                "nk": norm_name_value(name_new),
                                "g": grup_sku_new,
                                "pp": to_num_or_none(pp_no_vat),
                                "sp": to_num_or_none(sp_no_vat),
                                "cg": to_num_or_none(c_gsmnet),
                                "cm": to_num_or_none(c_moka),
                                "cs": to_num_or_none(c_sep),
                                "csq": to_num_or_none(c_square),
                                "ceg": to_num_or_none(c_ecranegsm),
                                "cdz": to_num_or_none(c_distrizone),
                            })
                        st.success(f"Produsul {code_new} a fost adăugat/actualizat.")

        # Edit
        with colB:
            st.markdown("### ✏️ Editează produs existent")
            code_sel = st.text_input("COD de editat", key="edit_code")
            if st.button("Încarcă produs"):
                if not code_sel.strip():
                    st.error("Introdu un COD.")
                else:
                    st.session_state["__editing_code__"] = code_sel.strip()

            code_target = st.session_state.get("__editing_code__")
            if code_target:
                try:
                    row = pd.read_sql(text("select * from products where code=:c"), engine, params={"c": code_target})
                except Exception as e:
                    st.error(f"Nu pot citi produsul: {e}")
                    row = pd.DataFrame()
                if row.empty:
                    st.warning(f"Nu am găsit COD={code_target}")
                else:
                    r = row.iloc[0]
                    with st.form("edit_form"):
                        name = st.text_input("NUME", value=r.get("name") or "")
                        grup = st.text_input("grup_sku", value=r.get("grup_sku") or "")
                        pp = st.text_input("Preț intrare fără TVA (C)", value=str(r.get("purchase_price_no_vat") or ""))
                        sp = st.text_input("Preț vânzare fără TVA (E)", value=str(r.get("sale_price_no_vat") or ""))
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            gsm = st.text_input("GSMNET", value=str(r.get("competitor_gsmnet") or ""))
                            moka = st.text_input("MOKA", value=str(r.get("competitor_moka") or ""))
                        with c2:
                            sep = st.text_input("SEP", value=str(r.get("competitor_sep") or ""))
                            square = st.text_input("SQUARE", value=str(r.get("competitor_square") or ""))
                        with c3:
                            egsm = st.text_input("ECRANEGSM", value=str(r.get("competitor_ecranegsm") or ""))
                            dz = st.text_input("DISTRIZONE", value=str(r.get("competitor_distrizone") or ""))
                        save = st.form_submit_button("💾 Salvează")
                        if save:
                            with engine.begin() as conn:
                                conn.execute(text(
                                    "update products set "
                                    "name=:name, name_key=:nk, grup_sku=nullif(:g,''), "
                                    "purchase_price_no_vat=:pp, sale_price_no_vat=:sp, "
                                    "competitor_gsmnet=:cg, competitor_moka=:cm, competitor_sep=:cs, "
                                    "competitor_square=:csq, competitor_ecranegsm=:ceg, competitor_distrizone=:cdz, "
                                    "updated_at=now() where code=:code"
                                ), {
                                    "name": name, "nk": norm_name_value(name), "g": grup,
                                    "pp": to_num_or_none(pp), "sp": to_num_or_none(sp),
                                    "cg": to_num_or_none(gsm), "cm": to_num_or_none(moka), "cs": to_num_or_none(sep),
                                    "csq": to_num_or_none(square), "ceg": to_num_or_none(egsm), "cdz": to_num_or_none(dz),
                                    "code": code_target
                                })
                            st.success("Salvat.")
                    with st.expander("🗑 Șterge produs (atenție!)"):
                        if st.button("Șterge", type="primary"):
                            with engine.begin() as conn:
                                conn.execute(text("delete from products where code=:c"), {"c": code_target})
                            st.success(f"Șters {code_target}")
                            st.session_state.pop("__editing_code__", None)


# ---------------- Tab 1: Import produse ----------------
with tabs[1]:
    st.subheader("📦 Import produse în DB (bulk din Excel)")
    st.caption("Așteptat: coloanele tale A..R. `grup_sku` se va seta pe tab-ul Mapare.")
    up_prod = st.file_uploader("Excel produse (.xlsx)", type=["xlsx"], key="prodfile_db")
    if engine and up_prod is not None:
        raw = pd.read_excel(up_prod, sheet_name=0)
        raw = raw.loc[:, ~raw.columns.astype(str).str.contains("^Unnamed", case=False)]
        cols = raw.columns.astype(str).str.strip().str.lower().str.replace("\n"," ", regex=True)
        raw.columns = cols

        def find_col(cands):
            for c in cands:
                if c in raw.columns:
                    return c
            return None

        df = pd.DataFrame()
        df["name"] = raw.get(find_col(["nume","name","denumire","produs"]), pd.Series(dtype="object")).astype(str).str.strip()
        df["code"] = raw.get(find_col(["cod","sku","cod.1","product code","id"]), pd.Series(dtype="object")).astype(str).str.strip()
        df["purchase_price_no_vat"] = to_num(raw.get(find_col(["pret intrare fara tva","pret achizitie","pret achiziție fara tva","pret achiziție"]), np.nan))
        df["sale_price_no_vat"] = to_num(raw.get(find_col(["pret vanzare fara tva","pret vânzare fara tva","pret fara tva"]), np.nan))

        df = df[df["code"].str.len() > 0].copy()
        df["name_key"] = norm_name_series(df["name"])
        rows = df.to_dict("records")

        run_migrations(engine)
        upsert_sql = text(
            "INSERT INTO products (code, name, name_key, purchase_price_no_vat, sale_price_no_vat, updated_at) "
            "VALUES (:code, :name, :name_key, :purchase_price_no_vat, :sale_price_no_vat, now()) "
            "ON CONFLICT (code) DO UPDATE SET "
            "name=EXCLUDED.name, name_key=EXCLUDED.name_key, "
            "purchase_price_no_vat=EXCLUDED.purchase_price_no_vat, "
            "sale_price_no_vat=EXCLUDED.sale_price_no_vat, updated_at=now()"
        )
        with engine.begin() as conn:
            conn.execute(upsert_sql, rows)
        st.success(f"Import/actualizare produse: {len(rows)} în DB.")


# ---------------- Tab 2: Import mișcări ----------------
with tabs[2]:
    st.subheader("🔁 Import mișcări SmartBill în DB")
    c1, c2 = st.columns(2)
    with c1:
        up_all = st.file_uploader("Anul în curs (.xlsx)", type=["xlsx"], key="an_db")
        d1 = st.date_input("Perioadă AN – început", date(date.today().year,1,1))
        d2 = st.date_input("Perioadă AN – sfârșit", date.today())
    with c2:
        up_30 = st.file_uploader("Ultimele 30 zile (.xlsx)", type=["xlsx"], key="z30_db")
        d3 = st.date_input("Perioadă 30z – început", date.today().replace(day=1))
        d4 = st.date_input("Perioadă 30z – sfârșit", date.today())

    def read_sb(file):
        df = pd.read_excel(file, sheet_name=0, header=1)
        df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed", case=False)]
        cols = df.columns.astype(str).str.strip().str.lower().str.replace("\n"," ", regex=True)
        df.columns = cols
        c_prod = None
        for n in ["produs","denumire","nume"]:
            if n in df.columns:
                c_prod = n
                break
        c_cod = "cod" if "cod" in df.columns else df.columns[1]

        def find(kws):
            for c in df.columns:
                low = c
                if all(kw in low for kw in kws):
                    return c
            return None

        c_stoci = find(["stoc","initial"]) or (df.columns[2] if len(df.columns) > 2 else None)
        c_intrari = "intrari" if "intrari" in df.columns else (df.columns[3] if len(df.columns) > 3 else None)
        c_iesiri = None
        for key in ["iesiri","ieșiri"]:
            if key in df.columns:
                c_iesiri = key
                break
        if c_iesiri is None and len(df.columns) > 4:
            c_iesiri = df.columns[4]
        c_stocf = find(["stoc","final"])

        out = pd.DataFrame()
        out["code"] = df[c_cod].astype(str).str.strip()
        out["product_name"] = df[c_prod].astype(str).str.strip() if c_prod else ""
        out["stoc_initial"] = pd.to_numeric(df[c_stoci], errors="coerce") if c_stoci else 0
        out["intrari"] = pd.to_numeric(df[c_intrari], errors="coerce") if c_intrari else 0
        out["iesiri"] = pd.to_numeric(df[c_iesiri], errors="coerce") if c_iesiri else 0
        if c_stocf:
            out["stoc_final"] = pd.to_numeric(df[c_stocf], errors="coerce")
        else:
            out["stoc_final"] = (out["stoc_initial"] + out["intrari"] - out["iesiri"]).clip(lower=0)
        return out

    def ingest(df, start, end, tag):
        if df is None or df.empty or engine is None:
            return 0
        rows = df.to_dict("records")
        sql = text(
            "INSERT INTO stock_moves (code, product_name, stoc_initial, intrari, iesiri, stoc_final, period_start, period_end, source_tag) "
            "VALUES (:code, :product_name, :stoc_initial, :intrari, :iesiri, :stoc_final, :ps, :pe, :tag)"
        )
        with engine.begin() as conn:
            conn.execute(sql, [{
                "code": r["code"],
                "product_name": r["product_name"],
                "stoc_initial": r["stoc_initial"],
                "intrari": r["intrari"],
                "iesiri": r["iesiri"],
                "stoc_final": r["stoc_final"],
                "ps": start,
                "pe": end,
                "tag": tag
            } for r in rows])
        return len(rows)

    if engine and (up_all or up_30):
        total = 0
        if up_all:
            df_a = read_sb(up_all); total += ingest(df_a, d1, d2, "an")
        if up_30:
            df_b = read_sb(up_30); total += ingest(df_b, d3, d4, "30z")
        if total:
            st.success(f"Importate {total} rânduri în stock_moves.")


# ---------------- Tab 3: Mapare grup_sku ----------------
with tabs[3]:
    st.subheader("🧩 Mapare `grup_sku` în DB (pe nume normalizat)")
    if engine:
        try:
            prod = pd.read_sql("select * from products", engine)
        except Exception as e:
            st.error(f"Nu pot citi products: {e}")
            prod = pd.DataFrame()

        if not prod.empty:
            prod["name_key"] = prod["name_key"].fillna(prod["name"].map(norm_name_value))
            try:
                sb = pd.read_sql(
                    "select code, product_name, sum(iesiri) as iesiri, max(stoc_final) as stoc_final "
                    "from stock_moves group by code, product_name",
                    engine
                )
            except Exception as e:
                st.error(f"Nu pot citi stock_moves: {e}")
                sb = pd.DataFrame()

            if not sb.empty:
                sb["name_key"] = sb["product_name"].map(norm_name_value)
                # Alegem un SKU SmartBill reprezentativ pe fiecare name_key (cel cu cele mai multe ieșiri)
                top = sb.sort_values(["name_key","iesiri"], ascending=[True, False]).groupby("name_key").first().reset_index()
                map_df = top[["name_key","code"]].rename(columns={"code":"sb_code"})
                prod = prod.merge(map_df, on="name_key", how="left")
                # Setăm grup_sku = SKU din SmartBill (sau propriul cod dacă nu există mișcări)
                prod["grup_sku"] = prod["grup_sku"].fillna(prod["sb_code"]).fillna(prod["code"])

                with engine.begin() as conn:
                    for _, r in prod.iterrows():
                        conn.execute(text(
                            "update products set name_key=:nk, grup_sku=:g, updated_at=now() where code=:code"
                        ), {"nk": r["name_key"], "g": r["grup_sku"], "code": r["code"]})

                st.success("grup_sku setat/actualizat.")
                st.dataframe(pd.read_sql("select code, name, grup_sku from products order by name", engine), use_container_width=True)
            else:
                st.info("Nu există mișcări încă. Încarcă rapoartele SmartBill ca să putem seta grup_sku.")


# ---------------- Tab 4: Rapoarte ----------------
with tabs[4]:
    st.subheader("📊 Rapoarte & Recomandări (din DB)")
    if engine:
        coef_recent = st.number_input("Coeficient 30 zile", value=1.5, step=0.1)
        coef_total = st.number_input("Coeficient anual", value=0.2, step=0.1)
        try:
            sql = text(
                "with moves as ( "
                "  select code, sum(iesiri) filter (where source_tag = '30z') as v30, "
                "         sum(iesiri) as vtot, max(stoc_final) as stocf "
                "  from stock_moves group by code "
                "), "
                "master as ( "
                "  select p.*, coalesce(p.grup_sku, p.code) as gsku from products p "
                "), "
                "by_group as ( "
                "  select mfr.gsku, sum(coalesce(mv.v30,0)) as vanzari_30zile, "
                "         sum(coalesce(mv.vtot,0)) as vanzari_total, max(coalesce(mv.stocf,0)) as stoc_final "
                "  from master mfr left join moves mv on mv.code = mfr.code "
                "  group by mfr.gsku "
                "), "
                "cheapest as ( "
                "  select gsku, code as cheapest_sku, purchase_price_no_vat as cheapest_price from ( "
                "    select gsku, code, purchase_price_no_vat, "
                "           row_number() over (partition by gsku order by purchase_price_no_vat asc nulls last) as rn "
                "    from master "
                "  ) z where rn = 1 "
                "), "
                "skus as ( "
                "  select gsku, string_agg(code, ', ' order by code) as skus_in_group from master group by gsku "
                ") "
                "select g.gsku as grup_sku, "
                "       max(p.name) filter (where p.code = g.gsku) as product_name, "
                "       s.skus_in_group, g.stoc_final, g.vanzari_30zile, g.vanzari_total, "
                "       greatest(0, round(g.vanzari_30zile * :cr + g.vanzari_total * :ct - g.stoc_final)) as recomandat_de_comandat, "
                "       ch.cheapest_sku, ch.cheapest_price "
                "from by_group g "
                "left join cheapest ch on ch.gsku = g.gsku "
                "left join skus s on s.gsku = g.gsku "
                "left join products p on p.code = g.gsku "
                "order by recomandat_de_comandat desc nulls last"
            )
            df = pd.read_sql(sql, engine, params={"cr": coef_recent, "ct": coef_total})
            st.dataframe(df, use_container_width=True)
            out = BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="recomandari")
            st.download_button("📥 Descarcă recomandari_din_DB.xlsx", data=out.getvalue(), file_name="recomandari_din_DB.xlsx")
        except Exception as e:
            st.error(f"Nu pot genera raportul. Verifică dacă ai importat mișcări și produse. Detalii: {e}")
