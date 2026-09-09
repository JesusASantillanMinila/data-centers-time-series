import streamlit as st
import pandas as pd
import requests
import datetime
import plotly.express as px

# Configure page layout and style
st.set_page_config(
    page_title="Data Center Time Series", 
    page_icon="🏢", 
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Optional Custom CSS for a cleaner look
st.markdown("""
    <style>
    .block-container { padding-top: 2rem; }
    </style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=3600)
def load_data():
    """Fetch the latest release parquet file from the GitHub API."""
    repo_url = "https://api.github.com/repos/JesusASantillanMinila/data_centers_time_series/releases/latest"
    try:
        response = requests.get(repo_url)
        response.raise_for_status()
        data = response.json()
        
        # Locate the parquet asset URL
        download_url = next(
            (asset["browser_download_url"] for asset in data.get("assets", []) 
             if asset["name"].endswith(".parquet")), 
            None
        )
        
        if not download_url:
            st.error("No parquet file found in the latest release.")
            st.stop()
            
        df = pd.read_parquet(download_url)
        df['snapshot_date'] = pd.to_datetime(df['snapshot_date'])
        df = df[df['snapshot_date'] <= pd.Timestamp.today().normalize()]


        # Create a combined Location Label for the new single filter
        df['location_label'] = df['STATE_NAME'] + " - " + df['COUNTY_NAME']
        
        return df
        
    except Exception as e:
        st.error(f"Failed to load data: {e}")
        st.stop()

df = load_data()

# Dashboard Header
st.markdown("<h1 style='text-align: center; color: #2E86C1;'>🏢 Data Centers Time Series Dashboard</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #5D6D7E; font-size: 1.1em;'>Track and analyze data center growth across different regions over time.</p>", unsafe_allow_html=True)
st.divider()

# --- Filters in a Dropdown (Expander) ---
with st.expander("🔎 Filter Options", expanded=True):
    col1, col2 = st.columns([2, 1]) # Give the location selector more room
    
    with col1:
        location_list = sorted(df['location_label'].dropna().unique())
        default_location = "Illinois - Cook"
        
        # Ensure default exists in the list to prevent errors
        default_selection = [default_location] if default_location in location_list else [location_list[0]] if location_list else []
        
        selected_locations = st.multiselect(
            "📍 Select Locations (State - County)", 
            options=location_list, 
            default=default_selection,
            help="You can select multiple locations to compare them on the chart."
        )

    with col2:
        min_date = df['snapshot_date'].min().date()
        max_date = df['snapshot_date'].max().date()
        default_start_date = datetime.date(2025, 1, 1)
        
        # Ensure default start date is within valid bounds
        safe_start_date = max(min_date, min(default_start_date, max_date))
        
        selected_dates = st.date_input(
            "📅 Select Date Range", 
            value=(safe_start_date, max_date),
            min_value=min_date,
            max_value=max_date
        )

# Handle single vs. range date selections
if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    start_dt, end_dt = selected_dates
elif isinstance(selected_dates, tuple) and len(selected_dates) == 1:
    start_dt, end_dt = selected_dates[0], max_date
else:
    start_dt, end_dt = selected_dates, max_date

# --- Data Filtering ---
if not selected_locations:
    st.info("👋 Please select at least one location from the filter menu to view the data.")
    st.stop()

mask = (
    (df['location_label'].isin(selected_locations)) &
    (df['snapshot_date'].dt.date >= start_dt) &
    (df['snapshot_date'].dt.date <= end_dt)
)
filtered_df = df[mask]

# --- Visualization ---
if not filtered_df.empty:
    plot_df = filtered_df.sort_values('snapshot_date')
    
    # Plotly Line Chart Updates
    fig = px.line(
        plot_df, 
        x='snapshot_date', 
        y='total_data_center_count',
        color='location_label', # Enables multi-line plotting for multiple selections
        title="📈 Total Data Centers Over Time",
        markers=True,
        template="plotly_white", # Cleaner UI template
        labels={
            'snapshot_date': 'Snapshot Date', 
            'total_data_center_count': 'Total Data Centers',
            'location_label': 'Location'
        }
    )
    
    # UI improvements for Plotly
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), # Moves legend above chart
        hovermode="x unified", # Shows data for all lines on a single vertical hover
        title_font_size=22,
        margin=dict(l=20, r=20, t=60, b=20)
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # --- Summary Metrics (Visually appealing add-on) ---
    st.markdown("### 📊 Latest Snapshot Summary")
    latest_date = plot_df['snapshot_date'].max()
    latest_df = plot_df[plot_df['snapshot_date'] == latest_date]
    
    if not latest_df.empty:
        # Dynamically create columns based on the number of locations selected (max 4 per row for aesthetic reasons)
        metric_cols = st.columns(min(len(selected_locations), 4)) 
        
        for idx, row in latest_df.iterrows():
            col_idx = selected_locations.index(row['location_label']) % 4
            with metric_cols[col_idx]:
                st.metric(
                    label=f"{row['location_label']}",
                    value=f"{int(row['total_data_center_count'])}",
                    delta=f"As of {latest_date.strftime('%b %d, %Y')}",
                    delta_color="off" # Grey delta since it's just a date
                )
                
else:
    st.warning("⚠️ No data found for the selected filter combination.")