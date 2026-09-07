import requests
import time
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
import numpy as np
import io

# 1. Fetch existing Parquet from GitHub Releases
print("Fetching latest release data from GitHub...")
repo_url = "https://api.github.com/repos/JesusASantillanMinila/data_centers_time_series/releases/latest"
response = requests.get(repo_url)

if response.status_code == 200:
    release_data = response.json()
    # Find the parquet file in the release assets
    parquet_asset = next((asset for asset in release_data.get('assets', []) if asset['name'].endswith('.parquet')), None)
    
    if parquet_asset:
        parquet_url = parquet_asset['browser_download_url']
        print(f"Downloading existing parquet from: {parquet_url}")
        existing_df = pd.read_parquet(parquet_url)
        existing_df['snapshot_date'] = pd.to_datetime(existing_df['snapshot_date'])
        
        # Determine start date: max date in the existing data + 1 day
        start_date = existing_df['snapshot_date'].max() + pd.Timedelta(days=1)
        print(f"Found existing data. Starting new queries from {start_date.strftime('%Y-%m-%d')}")
    else:
        print("No parquet file found in the latest release. Starting from default date.")
        existing_df = pd.DataFrame()
        start_date = pd.to_datetime("2023-01-01")
else:
    print(f"Failed to fetch release info (Status {response.status_code}). Starting from default date.")
    existing_df = pd.DataFrame()
    start_date = pd.to_datetime("2023-01-01")

# End date is today. Change frequency to 7 Days (7D)
end_date = pd.Timestamp.today().normalize()
dates = pd.date_range(start=start_date, end=end_date, freq="7D")

if len(dates) == 0:
    print("No new dates to query. Exiting script.")
    exit(0)

failed_queries = []
new_data_frames = []

overpass_urls = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter"
]

headers = {'User-Agent': 'PortfolioProject/1.0 (DataCenterForecasting)'}

lat_min, lon_min, lat_max, lon_max = 24.39, -125.0, 49.38, -66.93
mid_lat = (lat_min + lat_max) / 2
mid_lon = (lon_min + lon_max) / 2

bboxes = [
    (lat_min, lon_min, mid_lat, mid_lon), # Southwest
    (lat_min, mid_lon, mid_lat, lon_max), # Southeast
    (mid_lat, lon_min, lat_max, mid_lon), # Northwest
    (mid_lat, mid_lon, lat_max, lon_max)  # Northeast
]

print("Overpass parameters set.")
print("Downloading US counties shapefile (only needs to run once)...")
counties_url = "https://www2.census.gov/geo/tiger/GENZ2021/shp/cb_2021_us_county_20m.zip"
counties = gpd.read_file(counties_url)[['GEOID', 'NAME', 'STATE_NAME', 'geometry']].to_crs("EPSG:4326")
counties.rename(columns={'NAME': 'COUNTY_NAME'}, inplace=True)

print("Fetching historical snapshots from Overpass (using geographic chunking)...")

# 2. Extract Data from Overpass API (Per Date)
for date in dates:
    period_data_centers = []
    date_str = date.strftime("%Y-%m-%dT00:00:00Z")
    
    print(f"\n--- Querying data for {date_str} ---")

    for i, bbox in enumerate(bboxes):
        b_lat_min, b_lon_min, b_lat_max, b_lon_max = bbox
        print(f"  Fetching Region {i+1}/4...")

        query = f"""
        [out:json][timeout:300][date:"{date_str}"];
        (
        nwr["telecom"="data_center"]({b_lat_min},{b_lon_min},{b_lat_max},{b_lon_max});
        nwr["building"="data_center"]({b_lat_min},{b_lon_min},{b_lat_max},{b_lon_max});
        );
        out bb center;
        """

        max_retries = 7
        success = False

        for attempt in range(max_retries):
            current_url = overpass_urls[attempt % len(overpass_urls)]
            try:
                response = requests.post(current_url, data={'data': query}, headers=headers, timeout=350)
                if response.status_code == 200:
                    data = response.json()
                    elements = data.get('elements', [])
                    print(f"    -> Success: Found {len(elements)} data centers in region")

                    for element in elements:
                        tags = element.get('tags', {})
                        lat = element.get('lat') or element.get('center', {}).get('lat')
                        lon = element.get('lon') or element.get('center', {}).get('lon')

                        bounds = element.get('bounds')
                        geom = box(bounds['minlon'], bounds['minlat'], bounds['maxlon'], bounds['maxlat']) if bounds else Point(lon, lat)

                        period_data_centers.append({
                            'snapshot_date': date,
                            'osm_id': element['id'],
                            'lat': lat,
                            'lon': lon,
                            'geometry': geom,
                            'osm_building_levels': tags.get('building:levels'),
                            'osm_power': tags.get('power')
                        })
                    success = True
                    break

                elif response.status_code == 504 or "too busy" in response.text.lower():
                    print(f"    -> Server busy (Attempt {attempt+1}/{max_retries}). Sleeping 2 mins...")
                    time.sleep(120)
                else:
                    print(f"    -> Error {response.status_code}: {response.text[:100]}")
                    break

            except Exception as e:
                print(f"    -> Connection error: {e}. Retrying in 60s...")
                time.sleep(60)

        if not success:
            failed_queries.append({'date': date_str, 'region': i+1})

        time.sleep(60)

    # 3. Create GeoDataFrame and Process for the CURRENT PERIOD
    df = pd.DataFrame(period_data_centers)

    if df.empty:
        print(f"No data was fetched for {date_str}! Skipping processing.")
        continue
    
    df.drop_duplicates(subset=['snapshot_date', 'osm_id'], inplace=True)
    gdf = gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:4326")
    gdf_proj = gdf.to_crs("EPSG:5070")

    gdf['estimated_sq_meters'] = gdf_proj.geometry.area
    gdf['geometry'] = gdf.geometry.centroid

    conditions = [
        (gdf['estimated_sq_meters'] < 5000), 
        (gdf['estimated_sq_meters'] >= 5000) & (gdf['estimated_sq_meters'] < 20000),
        (gdf['estimated_sq_meters'] >= 20000)
    ]
    choices = ['small', 'medium', 'large']
    gdf['size_category'] = np.select(conditions, choices, default='small')

    gdf['small_count'] = (gdf['size_category'] == 'small').astype(int)
    gdf['medium_count'] = (gdf['size_category'] == 'medium').astype(int)
    gdf['large_count'] = (gdf['size_category'] == 'large').astype(int)

    joined = gpd.sjoin(gdf, counties, how="inner", predicate="intersects")

    monthly_county_df = joined.groupby(['snapshot_date', 'GEOID', 'STATE_NAME', 'COUNTY_NAME']).agg(
        total_data_center_count=('osm_id', 'size'),
        small_count=('small_count', 'sum'),
        medium_count=('medium_count', 'sum'),
        large_count=('large_count', 'sum'),
        total_estimated_sqm=('estimated_sq_meters', 'sum')
    ).reset_index()

    pivot_df = monthly_county_df.pivot(
        index='snapshot_date',
        columns=['GEOID', 'STATE_NAME', 'COUNTY_NAME'],
        values=['total_data_center_count', 'small_count', 'medium_count', 'large_count', 'total_estimated_sqm']
    )

    # Updated from 14 days to 7 days
    period_dates = pd.date_range(start=date, periods=7, freq='D', name='snapshot_date')
    daily_pivot = pivot_df.reindex(period_dates).ffill()
    
    final_daily_df = daily_pivot.stack(level=[1, 2, 3]).reset_index()

    count_columns = ['total_data_center_count', 'small_count', 'medium_count', 'large_count']
    for col in count_columns:
        final_daily_df[col] = final_daily_df[col].fillna(0).astype(int)

    final_daily_df['total_estimated_sqm'] = final_daily_df['total_estimated_sqm'].fillna(0.0)
    
    # Store this chunk into our list instead of saving a pickle
    new_data_frames.append(final_daily_df)

# Output failed queries
if failed_queries:
    print("\nWARNING: The following chunks failed to fetch:")
    for fq in failed_queries:
        print(f" - Date: {fq['date']}, Region: {fq['region']}")

# 4. Merge, Deduplicate, and Output Updated Parquet
if new_data_frames:
    print("\nMerging new data with existing data...")
    new_df = pd.concat(new_data_frames, ignore_index=True)
    
    # Combine existing and new data
    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    
    # Deduplicate on county/state/date granularity. Keep the latest scraped version.
    combined_df.drop_duplicates(
        subset=['snapshot_date', 'GEOID', 'STATE_NAME', 'COUNTY_NAME'], 
        keep='last', 
        inplace=True
    )
    
    # Sort the data neatly
    combined_df.sort_values(by=['STATE_NAME', 'COUNTY_NAME', 'snapshot_date'], inplace=True)
    
    output_filename = "data_centers.parquet"
    combined_df.to_parquet(output_filename, index=False)
    print(f"SUCCESS! Updated dataset saved to {output_filename} with {len(combined_df)} rows.")
else:
    print("No new data to append. Script finished.")