# stock_recommender.py
import requests
import snowflake.connector
import pandas as pd
from bs4 import BeautifulSoup
from flask import Flask, render_template, request
import plotly.graph_objs as go
import json
import os
from dotenv import load_dotenv
import re
import time
from typing import Dict, List, Tuple, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# ------------------- Configuration -------------------
SCREENER_URL = "https://www.screener.in/company/{}/consolidated/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Cookie": os.getenv("SCREENER_COOKIE", "")
}
STOCKS = {
    "Large Cap": ["RELIANCE", "TCS", "ITC"],
    "Mid Cap": ["PIDILITIND", "CUMMINSIND"],
    "Small Cap": ["HATSUN", "BALAMINES"]
}

# ------------------- To Categorise Metrics -------------------
METRIC_CATEGORIES = {
    "Income Statement": [
        "Sales", "Operating Profit", "Net Profit +", "Other Income +", "EBITDA", "Interest", "Tax %"
    ],
    "Balance Sheet": [
        "Total Assets", "Total Liabilities", "Reserves", "Equity Capital", "Investments", "Other Assets +"
    ],
    "Cash Flow": [
        "Cash from Operating Activity", "Cash from Investing Activity", "Cash from Financing Activity", "Net Cash Flow"
    ],
    "Financial Ratios": [
        "ROE", "ROCE %", "EPS", "Debt to Equity", "Current Ratio", "Inventory Turnover", "Interest Coverage Ratio"
    ]
}

# ------------------- Batch Loader -------------------
def load_all_data():
    """Load all stock data with improved error handling and batch processing"""
    logger.info("🔄 Loading all data")
    
    try:
        create_snowflake_table()
        conn = snowflake_connect()
        
        total_stocks = sum(len(stocks) for stocks in STOCKS.values())
        current_stock = 0
        
        for category, stocks in STOCKS.items():
            for stock in stocks:
                current_stock += 1
                logger.info(f"Processing {stock} ({current_stock}/{total_stocks})")
                
                try:
                    data, quarters, stock_category, industry = get_financial_data(stock)
                    if data and quarters:
                        insert_quarterly_to_snowflake(conn, stock, data, quarters, stock_category, industry)
                        logger.info(f"✅ Successfully loaded {stock}")
                    else:
                        logger.warning(f"⚠️ No data found for {stock}")
                except Exception as e:
                    logger.error(f"❌ Error processing {stock}: {e}")
                    continue
                
                # Add delay to avoid overwhelming the server
                time.sleep(1)
        
        conn.close()
        logger.info("✅ All data loaded successfully")
        
    except Exception as e:
        logger.error(f"❌ Error during data loading: {e}")
        raise

# ------------------- Flask App -------------------
app = Flask(__name__)

@app.route("/quarterly/<stock>")
def quarterly_view(stock):
    try:
        conn = snowflake_connect()
        cur = conn.cursor()

        cur.execute("""
            SELECT METRIC, QUARTER, VALUE
            FROM FINANCIALS_QUARTERLY
            WHERE STOCK_CODE=%s
            ORDER BY QUARTER
        """, (stock,))
        rows = cur.fetchall()

        if not rows:
            return f"<h2>No data found for {stock}</h2>"

        financial_data = {}
        quarters = []

        for metric, quarter, value in rows:
            if metric not in financial_data:
                financial_data[metric] = {}
            financial_data[metric][quarter] = value
            if quarter not in quarters:
                quarters.append(quarter)

        # Sort quarters chronologically
        quarters.sort()

        # Convert row format
        formatted = {
            metric: [financial_data[metric].get(q, "") for q in quarters]
            for metric in financial_data
        }

        conn.close()
        return render_template("quarterly.html",
                               stock=stock,
                               quarters=quarters,
                               financial_data=json.dumps(formatted),
                               metric_categories=METRIC_CATEGORIES)
    
    except Exception as e:
        logger.error(f"Error in quarterly view for {stock}: {e}")
        return f"<h2>Error loading data for {stock}</h2>"

@app.route("/sector/<sector>")
def sector_view(sector):
    try:
        conn = snowflake_connect()
        cur = conn.cursor()

        cur.execute("""
            SELECT STOCK_CODE, METRIC, QUARTER, VALUE
            FROM FINANCIALS_QUARTERLY
            WHERE CATEGORY=%s
        """, (sector,))
        rows = cur.fetchall()

        if not rows:
            return f"<h2>No data found for sector {sector}</h2>"

        sector_data = {}
        quarters = set()
        for stock, metric, quarter, value in rows:
            quarters.add(quarter)
            key = (stock, metric)
            if key not in sector_data:
                sector_data[key] = {}
            sector_data[key][quarter] = value

        quarters = sorted(quarters)

        formatted_data = {
            f"{stock} - {metric}": [sector_data[(stock, metric)].get(q, "") for q in quarters]
            for (stock, metric) in sector_data
        }

        conn.close()
        return render_template("sector.html",
                               sector=sector,
                               quarters=quarters,
                               financial_data=json.dumps(formatted_data))
    
    except Exception as e:
        logger.error(f"Error in sector view for {sector}: {e}")
        return f"<h2>Error loading data for sector {sector}</h2>"

@app.route("/")
def index():
    return render_template("index.html", categories=STOCKS)

@app.route("/visualize", methods=["POST"])
def visualize():
    stock = request.form['stock']
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM FINANCIALS_QUARTERLY WHERE STOCK_CODE=%s", (stock,))
        rows = cur.fetchall()

        if not rows:
            return f"<h2>No data found in Snowflake for {stock}</h2>"

        financial_data = {}
        years = ["2025", "2024", "2023", "2022", "2021"]  # <- replace generic labels

        for row in rows:
            metric = row[1]
            values = row[2:7]
            financial_data[metric] = values

        conn.close()
        return render_template("visualize.html",
                            stock=stock,
                            years=years,
                            financial_data=json.dumps(financial_data),
                            metric_categories=METRIC_CATEGORIES)
    
    except Exception as e:
        logger.error(f"Error in visualize for {stock}: {e}")
        return f"<h2>Error loading data for {stock}</h2>"

# ------------------- Screener Scraper -------------------
def clean_metric_name(metric_name: str) -> str:
    """Clean metric name while preserving important special characters"""
    # Remove unwanted whitespace and non-breaking spaces
    cleaned = metric_name.strip().replace("\xa0", " ")
    # Remove extra spaces
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned

def clean_value(val: str) -> str:
    """Clean financial values while preserving numbers and percentages"""
    if not val or val == "-":
        return ""
    
    # Remove unwanted characters but preserve important ones
    val = val.strip().replace(",", "").replace("\xa0", "")
    
    # Handle percentage values
    if val.endswith("%"):
        try:
            return str(float(val.strip('%')) / 100)
        except ValueError:
            return ""
    
    # Handle negative numbers in parentheses: (123) => -123
    if val.startswith("(") and val.endswith(")"):
        val = "-" + val[1:-1]
    
    # Remove + sign from values only (not from metric names)
    val = val.replace("+", "")
    
    # Validate numeric values
    if re.match(r"^-?\d+(\.\d+)?$", val):
        return val
    
    return ""

def get_financial_data(stock_code: str) -> Tuple[Dict, List, str, str]:
    """
    Fetch financial data from screener.in with improved error handling
    Returns: (data_dict, quarters_list, category, industry)
    """
    url = SCREENER_URL.format(stock_code)
    logger.info(f"🔎 Fetching {stock_code} from: {url}")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        
        if res.status_code != 200:
            logger.error(f"❌ Failed to fetch {stock_code}: HTTP {res.status_code}")
            return {}, [], "", ""

        soup = BeautifulSoup(res.content, "html.parser")

        # Extract industry and sector/category info
        category, industry = extract_company_info(soup)
        
        # Extract quarterly data
        data, quarters = extract_quarterly_data(soup, stock_code)
        
        return data, quarters, category, industry
        
    except requests.RequestException as e:
        logger.error(f"❌ Request failed for {stock_code}: {e}")
        return {}, [], "", ""
    except Exception as e:
        logger.error(f"❌ Unexpected error for {stock_code}: {e}")
        return {}, [], "", ""

def extract_company_info(soup: BeautifulSoup) -> Tuple[str, str]:
    """Extract company category and industry from breadcrumb"""
    try:
        breadcrumb = soup.select_one(".breadcrumb")
        if breadcrumb:
            breadcrumb_text = breadcrumb.get_text().strip()
            parts = breadcrumb_text.split('›')
            if len(parts) >= 3:
                category = parts[1].strip()
                industry = parts[2].strip()
                return category, industry
    except Exception as e:
        logger.warning(f"Could not extract company info: {e}")
    
    return "", ""

def extract_quarterly_data(soup: BeautifulSoup, stock_code: str) -> Tuple[Dict, List]:
    """Extract quarterly financial data from the page"""
    quarterly_table = soup.find("section", id="quarters")
    if not quarterly_table:
        logger.warning(f"⚠️ Quarterly data not found for {stock_code}")
        return {}, []

    try:
        # Extract quarters from header
        header_cells = quarterly_table.select("thead tr th")[1:]  # skip first col
        quarters = [th.get_text().strip() for th in header_cells]
        
        if not quarters:
            logger.warning(f"⚠️ No quarters found for {stock_code}")
            return {}, []

        # Extract data rows
        data = {}
        for row in quarterly_table.select("tbody tr"):
            cols = row.find_all("td")
            if len(cols) < len(quarters) + 1:
                continue
            
            # Clean metric name while preserving special characters
            metric = clean_metric_name(cols[0].get_text())
            
            # Clean values
            values = [clean_value(td.get_text()) for td in cols[1:len(quarters)+1]]
            
            # Only add if we have valid data
            if metric and any(v for v in values):
                data[metric] = values

        logger.info(f"📊 Extracted {len(data)} metrics for {stock_code}")
        return data, quarters
        
    except Exception as e:
        logger.error(f"Error extracting quarterly data for {stock_code}: {e}")
        return {}, []

# ------------------- Snowflake Integration -------------------
def snowflake_connect():
    """Create Snowflake connection with better error handling"""
    try:
        logger.info("Connecting to Snowflake...")
        
        # Validate environment variables
        required_vars = ["SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_ACCOUNT"]
        for var in required_vars:
            if not os.getenv(var):
                raise ValueError(f"Missing required environment variable: {var}")
        
        conn = snowflake.connector.connect(
            user=os.getenv("SNOWFLAKE_USER"),
            password=os.getenv("SNOWFLAKE_PASSWORD"),
            account=os.getenv("SNOWFLAKE_ACCOUNT"),
            warehouse='SNOWFLAKE_LEARNING_WH',
            database='STOCK_DB',
            schema='STOCK_SOURCE',
            client_session_keep_alive=True
        )
        
        logger.info("✅ Snowflake connection established")
        return conn
        
    except Exception as e:
        logger.error(f"❌ Snowflake connection failed: {e}")
        raise

def create_snowflake_table():
    """Create the financials table if it doesn't exist"""
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        
        logger.info("📋 Creating/checking Snowflake table...")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS FINANCIALS_QUARTERLY (
                STOCK_CODE STRING,
                METRIC STRING,
                QUARTER STRING,
                VALUE STRING,
                INDUSTRY STRING,
                CATEGORY STRING,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
                UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
            )
        """)
        
        # Create indexes for better performance
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_metric_quarter 
            ON FINANCIALS_QUARTERLY (STOCK_CODE, METRIC, QUARTER)
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Table created/verified successfully")
        
    except Exception as e:
        logger.error(f"❌ Error creating table: {e}")
        raise

def insert_quarterly_to_snowflake(conn, stock_code: str, financials: Dict, quarters: List, category: str, industry: str):
    """Insert quarterly data with batch processing for better performance"""
    if not financials or not quarters:
        logger.warning(f"No data to insert for {stock_code}")
        return
    
    try:
        cur = conn.cursor()
        batch_data = []
        
        # Prepare batch data
        for metric, values in financials.items():
            for i, quarter in enumerate(quarters):
                value = values[i] if i < len(values) else ""
                
                if value:  # Only insert non-empty values
                    batch_data.append((
                        stock_code, metric, quarter, value, industry, category
                    ))
        
        if not batch_data:
            logger.warning(f"No valid data to insert for {stock_code}")
            return
        
        # Use batch insert with MERGE for better performance
        merge_query = """
            MERGE INTO FINANCIALS_QUARTERLY AS tgt
            USING (
                SELECT 
                    column1 AS STOCK_CODE,
                    column2 AS METRIC,
                    column3 AS QUARTER,
                    column4 AS VALUE,
                    column5 AS INDUSTRY,
                    column6 AS CATEGORY
                FROM VALUES %s
            ) AS src
            ON tgt.STOCK_CODE = src.STOCK_CODE 
               AND tgt.METRIC = src.METRIC 
               AND tgt.QUARTER = src.QUARTER
            WHEN MATCHED THEN 
                UPDATE SET 
                    VALUE = src.VALUE,
                    INDUSTRY = src.INDUSTRY,
                    CATEGORY = src.CATEGORY,
                    UPDATED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN 
                INSERT (STOCK_CODE, METRIC, QUARTER, VALUE, INDUSTRY, CATEGORY)
                VALUES (src.STOCK_CODE, src.METRIC, src.QUARTER, src.VALUE, src.INDUSTRY, src.CATEGORY)
        """
        
        # Format data for VALUES clause
        values_str = ",".join([f"('{d[0]}', '{d[1]}', '{d[2]}', '{d[3]}', '{d[4]}', '{d[5]}')" for d in batch_data])
        final_query = merge_query % values_str
        
        cur.execute(final_query)
        conn.commit()
        
        logger.info(f"✅ Inserted {len(batch_data)} records for {stock_code}")
        
    except Exception as e:
        logger.error(f"❌ Error inserting data for {stock_code}: {e}")
        conn.rollback()
        raise

# ------------------- Main -------------------
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Stock Recommender Application')
    parser.add_argument('--load-data', action='store_true', help='Load all stock data')
    parser.add_argument('--run-app', action='store_true', help='Run Flask application')
    
    args = parser.parse_args()
    
    if args.load_data:
        load_all_data()
    elif args.run_app:
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        # Default behavior: load data then run app
        load_all_data()
        app.run(debug=True, host='0.0.0.0', port=5000)
