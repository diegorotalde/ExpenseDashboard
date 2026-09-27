import hashlib

import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import Client, create_client


st.set_page_config(page_title="Expense Dashboard", page_icon="📊", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

EXPORT_COLUMN_MAP = {
    "Date": "date",
    "Wallet": "wallet",
    "Type": "type",
    "Category name": "category",
    "Amount": "amount",
    "Currency": "currency",
    "Note": "description",
    "Labels": "labels",
    "Author": "author",
}
REQUIRED_EXPORT_COLUMNS = ["Date", "Wallet", "Type", "Category name", "Amount"]


def fetch_transactions() -> pd.DataFrame:
    response = supabase.table("transactions").select("*").execute()
    if not response.data:
        return pd.DataFrame(
            columns=[
                "id",
                "date",
                "wallet",
                "type",
                "category",
                "amount",
                "currency",
                "description",
                "labels",
                "author",
                "unique_hash",
            ]
        )

    transactions = pd.DataFrame(response.data)
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce")
    if "wallet" not in transactions.columns:
        transactions["wallet"] = "Unknown"
    if "author" not in transactions.columns:
        transactions["author"] = "Unknown"
    transactions["wallet"] = transactions["wallet"].fillna("Unknown").astype(str).str.strip()
    transactions["author"] = transactions["author"].fillna("Unknown").astype(str).str.strip()
    return transactions.dropna(subset=["date", "amount"])


def fetch_split_config() -> tuple[float, float]:
    response = supabase.table("split_config").select("*").eq("id", 1).execute()
    if response.data:
        return float(response.data[0]["diego_pct"]), float(response.data[0]["daniela_pct"])
    return 50.0, 50.0


def generate_hash(row: pd.Series) -> str:
    columns = ["date", "wallet", "description", "category", "amount", "type"]
    values = [row.get(column, "") for column in columns]
    return hashlib.md5("|".join(map(str, values)).encode("utf-8")).hexdigest()


def parse_transactions_csv(uploaded_file) -> tuple[pd.DataFrame | None, list[str]]:
    transactions = pd.read_csv(uploaded_file)
    transactions.columns = transactions.columns.str.strip()

    missing_columns = [
        column for column in REQUIRED_EXPORT_COLUMNS if column not in transactions.columns
    ]
    if missing_columns:
        return None, [f"Missing export columns: {missing_columns}"]

    transactions = transactions.rename(columns=EXPORT_COLUMN_MAP)
    for column in EXPORT_COLUMN_MAP.values():
        if column not in transactions.columns:
            transactions[column] = ""

    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce")
    transactions["type"] = transactions["type"].astype(str).str.strip().str.capitalize()
    transactions["wallet"] = transactions["wallet"].fillna("Unknown").astype(str).str.strip()
    transactions["category"] = transactions["category"].fillna("").astype(str).str.strip()
    transactions["description"] = transactions["description"].fillna("").astype(str).str.strip()
    transactions["currency"] = transactions["currency"].fillna("").astype(str).str.strip()
    transactions["labels"] = transactions["labels"].fillna("").astype(str).str.strip()
    transactions["author"] = transactions["author"].fillna("Unknown").astype(str).str.strip()

    invalid_rows = transactions[transactions["date"].isna() | transactions["amount"].isna()]
    if not invalid_rows.empty:
        return None, [f"{len(invalid_rows)} row(s) have an invalid Date or Amount value"]

    transactions["date"] = transactions["date"].dt.strftime("%Y-%m-%d")
    transactions["unique_hash"] = transactions.apply(generate_hash, axis=1)
    return transactions, []


def upload_transactions(transactions: pd.DataFrame) -> int:
    database_columns = [
        "date",
        "wallet",
        "type",
        "category",
        "amount",
        "currency",
        "description",
        "labels",
        "author",
        "unique_hash",
    ]
    records = transactions[database_columns].to_dict(orient="records")
    for record in records:
        record["date"] = str(record["date"])
        record["wallet"] = str(record["wallet"])
        record["type"] = str(record["type"])
        record["category"] = str(record["category"])
        record["amount"] = float(record["amount"])
        record["currency"] = str(record["currency"])
        record["description"] = str(record["description"])
        record["labels"] = str(record["labels"])
        record["author"] = str(record["author"])
        record["unique_hash"] = str(record["unique_hash"])

    response = supabase.table("transactions").insert(records).execute()
    return len(response.data or [])


def render_uploader() -> None:
    with st.expander("📥 Upload transactions CSV", expanded=False):
        uploaded_file = st.file_uploader(
            "Upload your transactions CSV",
            type=["csv"],
            key="transactions_csv_uploader",
        )
        if uploaded_file is None:
            return

        try:
            parsed_transactions, errors = parse_transactions_csv(uploaded_file)
        except Exception as error:
            st.error(f"Could not read the CSV file: {error}")
            return

        if errors:
            st.error(
                f"File must contain the export columns: {REQUIRED_EXPORT_COLUMNS}. "
                f"Problem: {errors}"
            )
            return

        st.success("File successfully parsed!")
        st.write("Preview of your uploaded data:")
        st.dataframe(parsed_transactions.head(), use_container_width=True)

        if st.button("Add transactions", key="add_transactions"):
            if parsed_transactions.empty:
                st.warning("The CSV does not contain any transaction rows.")
            else:
                try:
                    inserted_count = upload_transactions(parsed_transactions)
                    if inserted_count == len(parsed_transactions):
                        st.success(
                            f"Successfully uploaded {inserted_count} transaction(s) to Supabase."
                        )
                    else:
                        st.warning(
                            f"Supabase accepted {inserted_count} of "
                            f"{len(parsed_transactions)} transaction(s)."
                        )
                except Exception as error:
                    st.error(f"Supabase upload failed: {error}")


def wallet_key(wallet: pd.Series) -> pd.Series:
    return wallet.fillna("").astype(str).str.strip().str.casefold()


def monthly_totals(transactions: pd.DataFrame, year: int, value_column: str) -> pd.DataFrame:
    months = pd.DataFrame({"month_number": range(1, 13)})
    months["month"] = pd.to_datetime(
        {"year": year, "month": months["month_number"], "day": 1}
    ).dt.strftime("%b")
    totals = (
        transactions[transactions["date"].dt.year == year]
        .assign(month_number=lambda frame: frame["date"].dt.month)
        .groupby("month_number", as_index=False)[value_column]
        .sum()
    )
    result = months.merge(totals, on="month_number", how="left").fillna({value_column: 0})
    return result


def render_monthly_dashboard() -> None:
    st.title("📊 Monthly Dashboard")
    render_uploader()
    transactions = fetch_transactions()

    if transactions.empty:
        st.info("No transaction data available. Upload a CSV above to get started.")
        return

    years = sorted(transactions["date"].dt.year.unique().tolist(), reverse=True)
    selected_year = st.selectbox("Year", years, index=0, key="monthly_dashboard_year")
    diego_pct, _ = fetch_split_config()

    transactions["wallet_key"] = wallet_key(transactions["wallet"])
    transactions["type_key"] = transactions["type"].fillna("").astype(str).str.casefold()
    transactions["author_key"] = transactions["author"].fillna("").astype(str).str.casefold()
    transactions["absolute_amount"] = transactions["amount"].abs()

    year_transactions = transactions[transactions["date"].dt.year == selected_year]
    expenses = year_transactions[year_transactions["type_key"] == "expense"].copy()
    expenses = expenses[
        expenses["wallet_key"].str.contains("cuenta sueldo|pagos compartidos", regex=True)
    ]
    expenses["expense_amount"] = expenses["absolute_amount"]

    st.subheader("Total expenses over time")
    expense_wallet = st.selectbox(
        "Wallet",
        [
            "All wallets",
            "Pagos compartidos (full amount)",
            "Cuenta Sueldo",
            "Pagos compartidos (Diego’s amount)",
        ],
        key="total_expenses_wallet",
    )

    if expense_wallet == "Cuenta Sueldo":
        chart_expenses = expenses[expenses["wallet_key"].str.contains("cuenta sueldo")].copy()
    elif expense_wallet.startswith("Pagos compartidos"):
        chart_expenses = expenses[expenses["wallet_key"].str.contains("pagos compartidos")].copy()
        if expense_wallet == "Pagos compartidos (Diego’s amount)":
            chart_expenses["expense_amount"] *= diego_pct / 100
    else:
        chart_expenses = expenses.copy()

    categories = sorted(chart_expenses["category"].dropna().unique().tolist())
    selected_categories = st.multiselect(
        "Expense categories",
        categories,
        default=categories,
        key="total_expenses_categories",
    )
    if selected_categories:
        chart_expenses = chart_expenses[chart_expenses["category"].isin(selected_categories)]
    else:
        chart_expenses = chart_expenses.iloc[0:0]

    expense_chart = monthly_totals(chart_expenses, selected_year, "expense_amount")
    st.bar_chart(expense_chart, x="month", y="expense_amount", height=350)

    st.subheader("Monthly Income")
    income = year_transactions[year_transactions["type_key"] == "income"].copy()
    income["income_amount"] = income["absolute_amount"]
    income_chart = monthly_totals(income, selected_year, "income_amount")
    st.bar_chart(income_chart, x="month", y="income_amount", height=350)

    st.subheader("Pagos compartidos vs. Cuenta Sueldo")
    shared_year = st.selectbox(
        "Year for wallet comparison",
        years,
        index=0,
        key="wallet_comparison_year",
    )
    comparison_expenses = transactions[
        (transactions["date"].dt.year == shared_year)
        & (transactions["type_key"] == "expense")
        & (transactions["author_key"].str.contains("diego rotalde"))
    ].copy()
    comparison_expenses["Cuenta Sueldo"] = comparison_expenses["absolute_amount"].where(
        comparison_expenses["wallet_key"].str.contains("cuenta sueldo"), 0
    )
    comparison_expenses["Pagos Compartidos"] = comparison_expenses["absolute_amount"].where(
        comparison_expenses["wallet_key"].str.contains("pagos compartidos"), 0
    )
    comparison_expenses["Pagos Compartidos"] *= diego_pct / 100
    comparison_months = pd.DataFrame({"month_number": range(1, 13)})
    comparison_months["month"] = pd.to_datetime(
        {"year": shared_year, "month": comparison_months["month_number"], "day": 1}
    ).dt.strftime("%b")
    comparison_totals = (
        comparison_expenses.assign(month_number=comparison_expenses["date"].dt.month)
        .groupby("month_number", as_index=False)[["Cuenta Sueldo", "Pagos Compartidos"]]
        .sum()
    )
    comparison_chart = comparison_months.merge(
        comparison_totals, on="month_number", how="left"
    ).fillna(0)
    st.bar_chart(
        comparison_chart,
        x="month",
        y=["Cuenta Sueldo", "Pagos Compartidos"],
        color=["#2563eb", "#f97316"],
        stack=True,
        height=350,
    )


def render_shared_expenses() -> None:
    st.title("🤝 Shared Expenses")
    transactions = fetch_transactions()
    if transactions.empty:
        st.info("No transaction data available.")
        return

    expenses = transactions[transactions["type"] == "Expense"].copy()
    if expenses.empty:
        st.info("No expense data available.")
        return

    months = sorted(expenses["date"].dt.to_period("M").astype(str).unique(), reverse=True)
    selected_month = st.selectbox("Select Month", months, key="shared_expense_month")
    month_expenses = expenses[expenses["date"].dt.to_period("M").astype(str) == selected_month]
    diego_pct, daniela_pct = fetch_split_config()
    total = month_expenses["amount"].sum()

    col1, col2, col3 = st.columns(3)
    col1.metric(f"Total Expenses ({selected_month})", f"${total:,.2f}")
    col2.metric(f"Diego's Share ({diego_pct:.0f}%)", f"${total * diego_pct / 100:,.2f}")
    col3.metric(f"Daniela's Share ({daniela_pct:.0f}%)", f"${total * daniela_pct / 100:,.2f}")
    st.subheader(f"Breakdown for {selected_month}")
    st.dataframe(
        month_expenses[["date", "description", "category", "amount"]],
        use_container_width=True,
    )


def render_configuration() -> None:
    st.title("⚙️ Shared Expense Split Configuration")
    current_diego, _ = fetch_split_config()
    diego_pct = st.number_input(
        "Diego's Percentage (%)",
        min_value=0.0,
        max_value=100.0,
        value=current_diego,
        step=1.0,
        key="diego_percentage",
    )
    daniela_pct = 100.0 - diego_pct
    st.info(f"Daniela's Percentage: **{daniela_pct:.1f}%**")

    if st.button("Save Split Configuration", key="save_split_configuration"):
        supabase.table("split_config").upsert(
            {"id": 1, "diego_pct": diego_pct, "daniela_pct": daniela_pct}
        ).execute()
        st.success("Split percentages updated successfully.")


if "user" not in st.session_state:
    st.session_state.user = None

if st.session_state.user is None:
    st.title("🔒 Expense Dashboard")
    login_tab, signup_tab = st.tabs(["🔑 Log In", "📝 Sign Up"])

    with login_tab:
        login_email = st.text_input("Email", key="login_email")
        login_password = st.text_input("Password", type="password", key="login_password")
        if st.button("Log In", type="primary", key="login_button"):
            if not login_email or not login_password:
                st.error("Please fill in all fields.")
            else:
                try:
                    response = supabase.auth.sign_in_with_password(
                        {"email": login_email, "password": login_password}
                    )
                    st.session_state.user = response.user
                    st.rerun()
                except Exception as error:
                    st.error(f"Login failed: {error}")

    with signup_tab:
        signup_email = st.text_input("Email", key="signup_email")
        signup_password = st.text_input("Password", type="password", key="signup_password")
        if st.button("Sign Up", key="signup_button"):
            if not signup_email or not signup_password:
                st.error("Please fill in all fields.")
            else:
                try:
                    response = supabase.auth.sign_up(
                        {"email": signup_email, "password": signup_password}
                    )
                    if response.user:
                        st.success("Account created. Check your email if confirmation is enabled.")
                    else:
                        st.error("The account could not be created.")
                except Exception as error:
                    st.error(f"Sign up failed: {error}")

    st.stop()

user = st.session_state.user
user_email = getattr(user, "email", None)
if not user_email and isinstance(user, dict):
    user_email = user.get("email")

st.sidebar.title("📌 Navigation")
st.sidebar.write(f"Logged in as: **{user_email or 'User'}**")
page = st.sidebar.radio(
    "Go to",
    ["Monthly Dashboard", "Shared Expenses", "Configuration"],
    key="sidebar_page",
)

if st.sidebar.button("Logout", key="sidebar_logout"):
    supabase.auth.sign_out()
    st.session_state.user = None
    st.rerun()

if page == "Monthly Dashboard":
    render_monthly_dashboard()
elif page == "Shared Expenses":
    render_shared_expenses()
else:
    render_configuration()
