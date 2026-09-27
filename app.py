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
            columns=["id", "date", "description", "category", "amount", "type", "unique_hash"]
        )

    transactions = pd.DataFrame(response.data)
    transactions["date"] = pd.to_datetime(transactions["date"], errors="coerce")
    transactions["amount"] = pd.to_numeric(transactions["amount"], errors="coerce")
    return transactions.dropna(subset=["date", "amount"])


def fetch_split_config() -> tuple[float, float]:
    response = supabase.table("split_config").select("*").eq("id", 1).execute()
    if response.data:
        return float(response.data[0]["diego_pct"]), float(response.data[0]["daniela_pct"])
    return 50.0, 50.0


def generate_hash(row: pd.Series) -> str:
    columns = ["date", "description", "category", "amount", "type"]
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
    transactions["category"] = transactions["category"].fillna("").astype(str).str.strip()
    transactions["description"] = transactions["description"].fillna("").astype(str).str.strip()

    invalid_rows = transactions[transactions["date"].isna() | transactions["amount"].isna()]
    if not invalid_rows.empty:
        return None, [f"{len(invalid_rows)} row(s) have an invalid Date or Amount value"]

    transactions["date"] = transactions["date"].dt.strftime("%Y-%m-%d")
    transactions["unique_hash"] = transactions.apply(generate_hash, axis=1)
    return transactions, []


def upload_transactions(transactions: pd.DataFrame) -> tuple[int, int]:
    inserted_count = 0
    skipped_count = 0
    database_columns = ["date", "description", "category", "amount", "type", "unique_hash"]

    for _, row in transactions.iterrows():
        record = {column: row[column] for column in database_columns}
        try:
            supabase.table("transactions").insert(record).execute()
            inserted_count += 1
        except Exception:
            skipped_count += 1

    return inserted_count, skipped_count


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
            inserted_count, skipped_count = upload_transactions(parsed_transactions)
            st.success(
                f"Added {inserted_count} transaction(s). "
                f"Skipped {skipped_count} duplicate or invalid transaction(s)."
            )
            st.rerun()


def render_monthly_dashboard() -> None:
    st.title("📊 Monthly Dashboard")
    render_uploader()
    transactions = fetch_transactions()

    if transactions.empty:
        st.info("No transaction data available. Upload a CSV above to get started.")
        return

    transactions["month_year"] = transactions["date"].dt.to_period("M").astype(str)
    expenses = transactions[transactions["type"] == "Expense"]
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Category Spending Over Time")
        if expenses.empty:
            st.info("No expense data available.")
        else:
            category_totals = expenses.groupby(
                ["month_year", "category"], as_index=False
            )["amount"].sum()
            chart = px.bar(
                category_totals,
                x="month_year",
                y="amount",
                color="category",
                barmode="stack",
                labels={"month_year": "Month", "amount": "Amount"},
            )
            st.plotly_chart(chart, use_container_width=True)

    with col2:
        st.subheader("Net Wealth")
        totals = transactions.groupby(["month_year", "type"])["amount"].sum().unstack(fill_value=0)
        totals["Income"] = totals.get("Income", 0)
        totals["Expense"] = totals.get("Expense", 0)
        totals["Net Wealth"] = totals["Income"] - totals["Expense"]
        chart = px.line(
            totals.reset_index(),
            x="month_year",
            y="Net Wealth",
            markers=True,
            labels={"month_year": "Month", "Net Wealth": "Net Surplus"},
        )
        st.plotly_chart(chart, use_container_width=True)

    summary = transactions.groupby(["month_year", "type"])["amount"].sum().unstack(fill_value=0)
    summary["Income"] = summary.get("Income", 0)
    summary["Expense"] = summary.get("Expense", 0)
    summary["Net Savings"] = summary["Income"] - summary["Expense"]
    st.subheader("📋 Month-to-Month Summary")
    st.dataframe(
        summary.reset_index().sort_values("month_year", ascending=False),
        use_container_width=True,
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
