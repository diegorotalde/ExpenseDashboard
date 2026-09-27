import hashlib
import pandas as pd
import plotly.express as px
import streamlit as st
from supabase import create_client, Client

# ------------------------------------------------------------------------------
# 1. PAGE SETUP & SUPABASE CONNECTION
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="Financial Dashboard", page_icon="📊", layout="wide"
)

# Initialize Supabase client
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ------------------------------------------------------------------------------
# 2. EMAIL & PASSWORD AUTHENTICATION GATEWAY
# ------------------------------------------------------------------------------
if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title("🔒 Financial Dashboard")

    tab_login, tab_signup = st.tabs(["🔑 Log In", "📝 Sign Up"])

    # --- Login Tab ---
    with tab_login:
        st.subheader("Log in to your account")
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")

        if st.button("Log In", type="primary"):
            if not email or not password:
                st.error("Please fill in all fields.")
            else:
                try:
                    response = supabase.auth.sign_in_with_password(
                        {"email": email, "password": password}
                    )
                    st.session_state.user = response.user
                    st.success("Successfully logged in!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Login failed: {e}")

    # --- Sign Up Tab ---
    with tab_signup:
        st.subheader("Create a new account")
        new_email = st.text_input("Email", key="signup_email")
        new_password = st.text_input("Password", type="password", key="signup_password")

        if st.button("Sign Up"):
            if not new_email or not new_password:
                st.error("Please fill in all fields.")
            else:
                try:
                    response = supabase.auth.sign_up(
                        {"email": new_email, "password": new_password}
                    )
                    if response.user:
                        st.session_state.user = response.user
                        st.success("Account created!")
                        st.rerun()
                except Exception as e:
                    st.error(f"Sign up failed: {e}")

    # Prevent rest of app from running when not logged in
    st.stop()

# ------------------------------------------------------------------------------
# 3. HELPER FUNCTIONS
# ------------------------------------------------------------------------------
def fetch_transactions():
    """Fetch all transaction records from Supabase."""
    res = supabase.table("transactions").select("*").execute()
    if res.data:
        df = pd.DataFrame(res.data)
        df["date"] = pd.to_datetime(df["date"])
        df["amount"] = pd.to_numeric(df["amount"])
        return df
    return pd.DataFrame(columns=["id", "date", "description", "category", "amount", "type", "unique_hash"])

def fetch_split_config():
    """Fetch split configuration or default to 50/50."""
    res = supabase.table("split_config").select("*").eq("id", 1).execute()
    if res.data:
        return float(res.data[0]["diego_pct"]), float(res.data[0]["daniela_pct"])
    return 50.0, 50.0

def generate_hash(row):
    """Generate a unique hash for each row to prevent duplicates."""
    raw_str = f"{row['date']}_{row['description']}_{row['category']}_{row['amount']}_{row['type']}"
    return hashlib.md5(raw_str.encode("utf-8")).hexdigest()

# ------------------------------------------------------------------------------
# 4. SIDEBAR NAVIGATION & LOGOUT (Only reached if logged in)
# ------------------------------------------------------------------------------
# Safely extract email attribute whether user is an object or a dict
user_obj = st.session_state.user
user_email = getattr(user_obj, "email", None) if user_obj else None
if not user_email and isinstance(user_obj, dict):
    user_email = user_obj.get("email", "User")

st.sidebar.title("📌 Navigation")
st.sidebar.write(f"Logged in as: **{user_email or 'User'}**")

page = st.sidebar.radio(
    "Go to",
    ["Monthly Dashboard", "Shared Expenses (Julio 2026 style)", "Configuration"]
)

if st.sidebar.button("Logout"):
    supabase.auth.sign_out()
    st.session_state.user = None
    st.rerun()

# ------------------------------------------------------------------------------
# PAGE 1: MONTHLY DASHBOARD
# ------------------------------------------------------------------------------
if page == "Monthly Dashboard":
    st.title("📊 Monthly Dashboard")

    # --- Excel / CSV Uploader ---
    with st.expander("📥 Upload Excel / CSV Transactions", expanded=False):
        uploaded_file = st.file_uploader("Upload CSV or Excel file", type=["csv", "xlsx", "xls"])
        
        if uploaded_file:
            try:
                if uploaded_file.name.endswith(".csv"):
                    df_upload = pd.read_csv(uploaded_file)
                else:
                    df_upload = pd.read_excel(uploaded_file)

                # Standardize column names (case-insensitive)
                df_upload.columns = [c.lower().strip() for c in df_upload.columns]
                required_cols = {"date", "description", "category", "amount", "type"}

                if not required_cols.issubset(set(df_upload.columns)):
                    st.error(f"File must contain the following columns: {', '.join(required_cols)}")
                else:
                    df_upload["date"] = pd.to_datetime(df_upload["date"]).dt.strftime("%Y-%m-%d")
                    df_upload["type"] = df_upload["type"].str.capitalize()
                    
                    # Generate hashes to check for duplicates
                    records = []
                    for _, row in df_upload.iterrows():
                        u_hash = generate_hash(row)
                        records.append({
                            "date": str(row["date"]),
                            "description": str(row["description"]),
                            "category": str(row["category"]),
                            "amount": float(row["amount"]),
                            "type": str(row["type"]),
                            "unique_hash": u_hash
                        })

                    if st.button("Append Non-Duplicate Rows"):
                        inserted_count = 0
                        skipped_count = 0
                        for record in records:
                            try:
                                supabase.table("transactions").insert(record).execute()
                                inserted_count += 1
                            except Exception:
                                skipped_count += 1
                        
                        st.success(f"Successfully added {inserted_count} new rows! ({skipped_count} duplicates skipped)")
                        st.rerun()

            except Exception as e:
                st.error(f"Error parsing file: {e}")

    # --- Visualizations & Analytics ---
    df = fetch_transactions()

    if df.empty:
        st.info("No transaction data available. Upload a file above to get started!")
    else:
        df["month_year"] = df["date"].dt.to_period("M").astype(str)

        col1, col2 = st.columns(2)

        # Chart 1: Spending per Category over Time
        with col1:
            st.subheader("Category Spending Over Time")
            expense_df = df[df["type"] == "Expense"]
            if not expense_df.empty:
                cat_time = expense_df.groupby(["month_year", "category"])["amount"].sum().reset_index()
                fig_cat = px.bar(
                    cat_time, x="month_year", y="amount", color="category",
                    title="Monthly Expenses by Category", barmode="stack",
                    labels={"month_year": "Month", "amount": "Amount ($)"}
                )
                st.plotly_chart(fig_cat, use_container_width=True)
            else:
                st.write("No expense data available.")

        # Chart 2: Net Wealth (Income - Expenses) over Time
        with col2:
            st.subheader("Net Wealth (Income - Expense)")
            net_df = df.groupby(["month_year", "type"])["amount"].sum().unstack(fill_value=0).reset_index()
            if "Income" not in net_df.columns:
                net_df["Income"] = 0
            if "Expense" not in net_df.columns:
                net_df["Expense"] = 0

            net_df["Net Wealth"] = net_df["Income"] - net_df["Expense"]

            fig_net = px.line(
                net_df, x="month_year", y="Net Wealth", markers=True,
                title="Net Cashflow Over Time",
                labels={"month_year": "Month", "Net Wealth": "Net Surplus ($)"}
            )
            st.plotly_chart(fig_net, use_container_width=True)

        # Chart 3: Detailed Month-to-Month Breakdown Table
        st.subheader("📋 Month-to-Month Summary")
        summary_df = df.groupby(["month_year", "type"])["amount"].sum().unstack(fill_value=0).reset_index()
        if "Income" not in summary_df.columns:
            summary_df["Income"] = 0
        if "Expense" not in summary_df.columns:
            summary_df["Expense"] = 0
        summary_df["Net Savings"] = summary_df["Income"] - summary_df["Expense"]
        
        st.dataframe(
            summary_df.sort_values(by="month_year", ascending=False),
            use_container_width=True
        )

# ------------------------------------------------------------------------------
# PAGE 2: SHARED EXPENSES (JULIO 2026 STYLE)
# ------------------------------------------------------------------------------
elif page == "Shared Expenses (Julio 2026 style)":
    st.title("🤝 Shared Expenses")
    diego_pct, daniela_pct = fetch_split_config()

    df = fetch_transactions()
    
    if df.empty:
        st.info("No transaction data available.")
    else:
        # Filter expenses only
        expenses = df[df["type"] == "Expense"].copy()
        
        # Select Month Filter
        months = sorted(expenses["date"].dt.to_period("M").astype(str).unique(), reverse=True)
        selected_month = st.selectbox("Select Month", options=months, index=0)

        month_expenses = expenses[expenses["date"].dt.to_period("M").astype(str) == selected_month]

        total_shared = month_expenses["amount"].sum()
        diego_share = total_shared * (diego_pct / 100.0)
        daniela_share = total_shared * (daniela_pct / 100.0)

        # Overview Cards
        col1, col2, col3 = st.columns(3)
        col1.metric(f"Total Expenses ({selected_month})", f"${total_shared:,.2f}")
        col2.metric(f"Diego's Share ({diego_pct:.0f}%)", f"${diego_share:,.2f}")
        col3.metric(f"Daniela's Share ({daniela_pct:.0f}%)", f"${daniela_share:,.2f}")

        st.subheader(f"Breakdown for {selected_month}")
        st.dataframe(month_expenses[["date", "description", "category", "amount"]], use_container_width=True)

# ------------------------------------------------------------------------------
# PAGE 3: CONFIGURATION
# ------------------------------------------------------------------------------
elif page == "Configuration":
    st.title("⚙️ Shared Expense Split Configuration")

    current_diego, current_daniela = fetch_split_config()

    st.subheader("Adjust Diego vs Daniela Split")
    
    diego_input = st.number_input(
        "Diego's Percentage (%)", min_value=0.0, max_value=100.0,
        value=current_diego, step=1.0
    )
    
    daniela_input = 100.0 - diego_input
    st.info(f"Daniela's Percentage automatically set to: **{daniela_input:.1f}%**")

    if st.button("Save Split Configuration"):
        supabase.table("split_config").upsert({
            "id": 1,
            "diego_pct": diego_input,
            "daniela_pct": daniela_input
        }).execute()
        st.success("Split percentages updated successfully!")