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

# ------------------- Comprehensive Metric Categories -------------------
METRIC_CATEGORY_PATTERNS = {
    "Income Statement": [
        # Revenue and Sales
        r".*sales.*", r".*revenue.*", r".*income.*", r".*turnover.*",
        # Profitability
        r".*profit.*", r".*earnings.*", r".*ebit.*", r".*ebitda.*", 
        r".*operating profit.*", r".*net profit.*", r".*gross profit.*",
        r".*pbt.*", r".*pat.*",
        # Expenses
        r".*expense.*", r".*cost.*", r".*depreciation.*", r".*amortization.*",
        r".*interest.*", r".*tax.*", r".*provision.*",
        # Other Income Statement items
        r".*other income.*", r".*exceptional.*", r".*extraordinary.*"
    ],
    "Balance Sheet": [
        # Assets
        r".*assets.*", r".*fixed assets.*", r".*current assets.*", 
        r".*non.current assets.*", r".*tangible assets.*", r".*intangible assets.*",
        r".*investments.*", r".*cash.*", r".*bank.*", r".*inventory.*",
        r".*receivables.*", r".*debtors.*", r".*advances.*",
        # Liabilities
        r".*liabilities.*", r".*current liabilities.*", r".*non.current liabilities.*",
        r".*payables.*", r".*creditors.*", r".*provisions.*", r".*borrowings.*",
        r".*debt.*", r".*loans.*",
        # Equity
        r".*equity.*", r".*capital.*", r".*reserves.*", r".*surplus.*",
        r".*share capital.*", r".*retained earnings.*"
    ],
    "Cash Flow": [
        r".*cash flow.*", r".*operating.*activity.*", r".*investing.*activity.*",
        r".*financing.*activity.*", r".*free cash flow.*", r".*net cash.*",
        r".*cash generated.*", r".*cash used.*"
    ],
    "Financial Ratios": [
        # Profitability Ratios
        r".*roe.*", r".*roi.*", r".*roce.*", r".*roic.*", r".*roa.*",
        r".*margin.*", r".*operating margin.*", r".*net margin.*", 
        r".*gross margin.*", r".*ebitda margin.*",
        # Liquidity Ratios
        r".*current ratio.*", r".*quick ratio.*", r".*cash ratio.*",
        # Leverage Ratios
        r".*debt.*equity.*", r".*debt.*total.*", r".*interest coverage.*",
        r".*debt service coverage.*", r".*financial leverage.*",
        # Efficiency Ratios
        r".*turnover.*", r".*days.*", r".*inventory turnover.*", 
        r".*receivables turnover.*", r".*asset turnover.*",
        # Market Ratios
        r".*pe.*", r".*pb.*", r".*price.*book.*", r".*price.*earnings.*",
        r".*dividend yield.*", r".*dividend payout.*"
    ],
    "Per Share Data": [
        r".*per share.*", r".*eps.*", r".*book value.*share.*", 
        r".*cash.*share.*", r".*dividend.*share.*", r".*sales.*share.*"
    ],
    "Valuation Metrics": [
        r".*market cap.*", r".*enterprise value.*", r".*ev.*", 
        r".*price.*sales.*", r".*price.*cash.*", r".*market.*book.*"
    ],
    "Other Financial Metrics": [
        r".*working capital.*", r".*net worth.*", r".*face value.*",
        r".*book value.*", r".*intrinsic value.*", r".*fair value.*"
    ]
}

# ------------------- Dynamic Metric Categories -------------------
DYNAMIC_METRIC_CATEGORIES = {
    "Income Statement": set(),
    "Balance Sheet": set(),
    "Cash Flow": set(),
    "Financial Ratios": set(),
    "Per Share Data": set(),
    "Valuation Metrics": set(),
    "Other Financial Metrics": set()
}

def categorize_metric(metric_name: str) -> str:
    """Automatically categorize a metric based on its name using pattern matching"""
    metric_lower = metric_name.lower().strip()
    
    # Check each category's patterns
    for category, patterns in METRIC_CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, metric_lower):
                DYNAMIC_METRIC_CATEGORIES[category].add(metric_name)
                return category
    
    # Default category for unmatched metrics
    DYNAMIC_METRIC_CATEGORIES["Other Financial Metrics"].add(metric_name)
    return "Other Financial Metrics"

def get_all_metric_categories() -> Dict:
    """Get all metric categories including dynamically discovered ones"""
    return {k: list(v) for k, v in DYNAMIC_METRIC_CATEGORIES.items() if v}

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
                        logger.info(f"✅ Successfully loaded {stock} with {len(data)} metrics")
                    else:
                        logger.warning(f"⚠️ No data found for {stock}")
                except Exception as e:
                    logger.error(f"❌ Error processing {stock}: {e}")
                    continue
                
                # Add delay to avoid overwhelming the server
                time.sleep(2)
        
        conn.close()
        
        # Log summary of discovered metrics
        total_metrics = sum(len(metrics) for metrics in DYNAMIC_METRIC_CATEGORIES.values())
        logger.info(f"✅ All data loaded successfully! Discovered {total_metrics} unique metrics")
        
        for category, metrics in DYNAMIC_METRIC_CATEGORIES.items():
            if metrics:
                logger.info(f"📊 {category}: {len(metrics)} metrics")
        
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
            SELECT METRIC, QUARTER, VALUE, METRIC_CATEGORY
            FROM FINANCIALS_QUARTERLY
            WHERE STOCK_CODE=%s
            ORDER BY METRIC_CATEGORY, METRIC, QUARTER
        """, (stock,))
        rows = cur.fetchall()

        if not rows:
            return f"<h2>No data found for {stock}</h2>"

        financial_data = {}
        quarters = []
        categories = {}

        for metric, quarter, value, metric_category in rows:
            if metric not in financial_data:
                financial_data[metric] = {}
                categories[metric] = metric_category
            financial_data[metric][quarter] = value
            if quarter not in quarters:
                quarters.append(quarter)

        # Sort quarters chronologically
        quarters.sort()

        # Convert row format and group by category
        categorized_data = {}
        for metric, quarter_data in financial_data.items():
            category = categories[metric]
            if category not in categorized_data:
                categorized_data[category] = {}
            categorized_data[category][metric] = [quarter_data.get(q, "") for q in quarters]

        conn.close()
        return render_template("quarterly.html",
                               stock=stock,
                               quarters=quarters,
                               financial_data=json.dumps(categorized_data),
                               metric_categories=categorized_data)
    
    except Exception as e:
        logger.error(f"Error in quarterly view for {stock}: {e}")
        return f"<h2>Error loading data for {stock}</h2>"

@app.route("/sector/<sector>")
def sector_view(sector):
    try:
        conn = snowflake_connect()
        cur = conn.cursor()

        cur.execute("""
            SELECT STOCK_CODE, METRIC, QUARTER, VALUE, METRIC_CATEGORY
            FROM FINANCIALS_QUARTERLY
            WHERE CATEGORY=%s
            ORDER BY METRIC_CATEGORY, STOCK_CODE, METRIC
        """, (sector,))
        rows = cur.fetchall()

        if not rows:
            return f"<h2>No data found for sector {sector}</h2>"

        sector_data = {}
        quarters = set()
        
        for stock, metric, quarter, value, metric_category in rows:
            quarters.add(quarter)
            key = (stock, metric, metric_category)
            if key not in sector_data:
                sector_data[key] = {}
            sector_data[key][quarter] = value

        quarters = sorted(quarters)

        # Group by metric category
        categorized_data = {}
        for (stock, metric, metric_category), quarter_data in sector_data.items():
            if metric_category not in categorized_data:
                categorized_data[metric_category] = {}
            
            display_key = f"{stock} - {metric}"
            categorized_data[metric_category][display_key] = [
                quarter_data.get(q, "") for q in quarters
            ]

        conn.close()
        return render_template("sector.html",
                               sector=sector,
                               quarters=quarters,
                               financial_data=json.dumps(categorized_data))
    
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
        
        # Select only the columns we need to avoid datetime serialization issues
        cur.execute("""
            SELECT STOCK_CODE, METRIC, QUARTER, VALUE, INDUSTRY, CATEGORY, METRIC_CATEGORY
            FROM FINANCIALS_QUARTERLY 
            WHERE STOCK_CODE=%s
            ORDER BY METRIC_CATEGORY, METRIC, QUARTER
        """, (stock,))
        rows = cur.fetchall()

        if not rows:
            return f"<h2>No data found in Snowflake for {stock}</h2>"

        # Group data by metric category and quarter
        categorized_data = {}
        quarters = set()
        
        for stock_code, metric, quarter, value, industry, category, metric_category in rows:
            quarters.add(quarter)
            
            if metric_category not in categorized_data:
                categorized_data[metric_category] = {}
            if metric not in categorized_data[metric_category]:
                categorized_data[metric_category][metric] = {}
            
            categorized_data[metric_category][metric][quarter] = value

        # Sort quarters chronologically
        quarters = sorted(quarters)

        # Convert to format expected by template
        formatted_data = {}
        for category, metrics in categorized_data.items():
            formatted_data[category] = {}
            for metric, quarter_data in metrics.items():
                formatted_data[category][metric] = [quarter_data.get(q, "") for q in quarters]

        conn.close()
        return render_template("visualize.html",
                            stock=stock,
                            years=quarters,  # Use actual quarters instead of generic years
                            financial_data=json.dumps(formatted_data),
                            metric_categories=formatted_data)
    
    except Exception as e:
        logger.error(f"Error in visualize for {stock}: {e}")
        return f"<h2>Error loading data for {stock}</h2>"

# ------------------- Enhanced Screener Scraper -------------------
def clean_metric_name(metric_name: str) -> str:
    """Clean metric name while preserving important special characters"""
    # Remove unwanted whitespace and non-breaking spaces
    cleaned = metric_name.strip().replace("\xa0", " ")
    # Remove extra spaces but preserve + and other meaningful characters
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned

def clean_value(val: str) -> str:
    """Clean financial values while preserving numbers and percentages"""
    if not val or val == "-" or val.lower() == "n/a":
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
    
    # Handle special cases like "1.5x", "2.3times"
    if re.match(r"^-?\d+(\.\d+)?(x|times)$", val.lower()):
        return re.sub(r'(x|times)$', '', val.lower())
    
    # Validate numeric values
    if re.match(r"^-?\d+(\.\d+)?$", val):
        return val
    
    return ""

def get_financial_data(stock_code: str) -> Tuple[Dict, List, str, str]:
    """
    Fetch ALL financial data from screener.in with comprehensive scraping
    Returns: (data_dict, quarters_list, category, industry)
    """
    url = SCREENER_URL.format(stock_code)
    logger.info(f"🔎 Fetching ALL metrics for {stock_code} from: {url}")
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=30)
        res.raise_for_status()
        
        if res.status_code != 200:
            logger.error(f"❌ Failed to fetch {stock_code}: HTTP {res.status_code}")
            return {}, [], "", ""

        soup = BeautifulSoup(res.content, "html.parser")

        # Extract industry and sector/category info
        category, industry = extract_company_info(soup)
        
        # Extract ALL financial data from multiple sections
        all_data, quarters = extract_all_financial_data(soup, stock_code)
        
        return all_data, quarters, category, industry
        
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

def extract_all_financial_data(soup: BeautifulSoup, stock_code: str) -> Tuple[Dict, List]:
    """Extract ALL financial data from multiple sections of the page"""
    all_data = {}
    quarters = []
    
    try:
        # 1. Extract Quarterly Results (main financial statements)
        quarterly_data, quarterly_quarters = extract_quarterly_data(soup, stock_code)
        if quarterly_data and quarterly_quarters:
            all_data.update(quarterly_data)
            quarters = quarterly_quarters
        
        # 2. Extract Annual Results if available
        annual_data, annual_quarters = extract_annual_data(soup, stock_code)
        if annual_data:
            all_data.update(annual_data)
            if not quarters:
                quarters = annual_quarters
        
        # 3. Extract Ratios section
        ratios_data = extract_ratios_data(soup, stock_code, quarters)
        if ratios_data:
            all_data.update(ratios_data)
        
        # 4. Extract Balance Sheet details
        balance_sheet_data = extract_balance_sheet_data(soup, stock_code, quarters)
        if balance_sheet_data:
            all_data.update(balance_sheet_data)
        
        # 5. Extract Cash Flow details
        cashflow_data = extract_cashflow_data(soup, stock_code, quarters)
        if cashflow_data:
            all_data.update(cashflow_data)
        
        # 6. Extract Per Share data
        per_share_data = extract_per_share_data(soup, stock_code, quarters)
        if per_share_data:
            all_data.update(per_share_data)
        
        logger.info(f"📊 Extracted {len(all_data)} total metrics for {stock_code}")
        return all_data, quarters
        
    except Exception as e:
        logger.error(f"Error extracting all financial data for {stock_code}: {e}")
        return {}, []

def extract_quarterly_data(soup: BeautifulSoup, stock_code: str) -> Tuple[Dict, List]:
    """Extract quarterly financial data from the main quarterly table"""
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

        logger.info(f"📈 Extracted {len(data)} quarterly metrics for {stock_code}")
        return data, quarters
        
    except Exception as e:
        logger.error(f"Error extracting quarterly data for {stock_code}: {e}")
        return {}, []

def extract_annual_data(soup: BeautifulSoup, stock_code: str) -> Tuple[Dict, List]:
    """Extract annual financial data if available"""
    annual_table = soup.find("section", id="profit-loss")
    if not annual_table:
        return {}, []
    
    try:
        # Similar logic to quarterly but for annual data
        header_cells = annual_table.select("thead tr th")[1:]
        years = [th.get_text().strip() for th in header_cells]
        
        data = {}
        for row in annual_table.select("tbody tr"):
            cols = row.find_all("td")
            if len(cols) < len(years) + 1:
                continue
            
            metric = clean_metric_name(cols[0].get_text())
            values = [clean_value(td.get_text()) for td in cols[1:len(years)+1]]
            
            if metric and any(v for v in values):
                # Prefix to distinguish from quarterly
                data[f"Annual {metric}"] = values
        
        logger.info(f"📅 Extracted {len(data)} annual metrics for {stock_code}")
        return data, years
        
    except Exception as e:
        logger.warning(f"Could not extract annual data for {stock_code}: {e}")
        return {}, []

def extract_ratios_data(soup: BeautifulSoup, stock_code: str, quarters: List) -> Dict:
    """Extract financial ratios from ratios section"""
    try:
        # Look for ratios in various possible sections
        ratios_sections = soup.find_all("section", class_=re.compile(r".*ratio.*", re.I))
        if not ratios_sections:
            # Try alternative selectors
            ratios_sections = soup.find_all("div", class_=re.compile(r".*ratio.*", re.I))
        
        data = {}
        for section in ratios_sections:
            table = section.find("table")
            if not table:
                continue
                
            for row in table.select("tbody tr"):
                cols = row.find_all("td")
                if len(cols) >= 2:
                    metric = clean_metric_name(cols[0].get_text())
                    # For ratios, we might have different data structure
                    values = [clean_value(td.get_text()) for td in cols[1:]]
                    
                    if metric and any(v for v in values):
                        # Pad or trim values to match quarters length
                        while len(values) < len(quarters):
                            values.append("")
                        values = values[:len(quarters)]
                        data[metric] = values
        
        if data:
            logger.info(f"📊 Extracted {len(data)} ratio metrics for {stock_code}")
        return data
        
    except Exception as e:
        logger.warning(f"Could not extract ratios for {stock_code}: {e}")
        return {}

def extract_balance_sheet_data(soup: BeautifulSoup, stock_code: str, quarters: List) -> Dict:
    """Extract detailed balance sheet data"""
    try:
        balance_sheet_section = soup.find("section", id="balance-sheet")
        if not balance_sheet_section:
            return {}
        
        data = {}
        for table in balance_sheet_section.find_all("table"):
            for row in table.select("tbody tr"):
                cols = row.find_all("td")
                if len(cols) >= len(quarters) + 1:
                    metric = clean_metric_name(cols[0].get_text())
                    values = [clean_value(td.get_text()) for td in cols[1:len(quarters)+1]]
                    
                    if metric and any(v for v in values):
                        data[metric] = values
        
        if data:
            logger.info(f"🏦 Extracted {len(data)} balance sheet metrics for {stock_code}")
        return data
        
    except Exception as e:
        logger.warning(f"Could not extract balance sheet data for {stock_code}: {e}")
        return {}

def extract_cashflow_data(soup: BeautifulSoup, stock_code: str, quarters: List) -> Dict:
    """Extract cash flow statement data"""
    try:
        cashflow_section = soup.find("section", id="cash-flow")
        if not cashflow_section:
            return {}
        
        data = {}
        for table in cashflow_section.find_all("table"):
            for row in table.select("tbody tr"):
                cols = row.find_all("td")
                if len(cols) >= len(quarters) + 1:
                    metric = clean_metric_name(cols[0].get_text())
                    values = [clean_value(td.get_text()) for td in cols[1:len(quarters)+1]]
                    
                    if metric and any(v for v in values):
                        data[metric] = values
        
        if data:
            logger.info(f"💰 Extracted {len(data)} cash flow metrics for {stock_code}")
        return data
        
    except Exception as e:
        logger.warning(f"Could not extract cash flow data for {stock_code}: {e}")
        return {}

def extract_per_share_data(soup: BeautifulSoup, stock_code: str, quarters: List) -> Dict:
    """Extract per share data and other key metrics"""
    try:
        # Look for per share data in various sections
        data = {}
        
        # Check for per share ratios or metrics
        for section in soup.find_all("section"):
            tables = section.find_all("table")
            for table in tables:
                for row in table.select("tbody tr"):
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        metric = clean_metric_name(cols[0].get_text())
                        
                        # Check if this is a per share metric
                        if any(keyword in metric.lower() for keyword in ['per share', 'eps', 'book value', 'dividend']):
                            if len(cols) >= len(quarters) + 1:
                                values = [clean_value(td.get_text()) for td in cols[1:len(quarters)+1]]
                            else:
                                # Handle single value metrics
                                values = [clean_value(cols[1].get_text())] + [""] * (len(quarters) - 1)
                            
                            if any(v for v in values):
                                data[metric] = values
        
        if data:
            logger.info(f"📈 Extracted {len(data)} per share metrics for {stock_code}")
        return data
        
    except Exception as e:
        logger.warning(f"Could not extract per share data for {stock_code}: {e}")
        return {}

# ------------------- Enhanced Snowflake Integration -------------------
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
    """Create the enhanced financials table if it doesn't exist"""
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        
        logger.info("📋 Creating/checking enhanced Snowflake table...")
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS FINANCIALS_QUARTERLY (
                STOCK_CODE STRING,
                METRIC STRING,
                QUARTER STRING,
                VALUE STRING,
                INDUSTRY STRING,
                CATEGORY STRING,
                METRIC_CATEGORY STRING,
                DATA_SOURCE STRING DEFAULT 'SCREENER',
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
                UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
            )
        """)
        
        # Create indexes for better performance
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_metric_quarter 
            ON FINANCIALS_QUARTERLY (STOCK_CODE, METRIC, QUARTER)
        """)
        
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_metric_category 
            ON FINANCIALS_QUARTERLY (METRIC_CATEGORY)
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Enhanced table created/verified successfully")
        
    except Exception as e:
        logger.error(f"❌ Error creating table: {e}")
        raise

def insert_quarterly_to_snowflake(conn, stock_code: str, financials: Dict, quarters: List, category: str, industry: str):
    """Insert quarterly data with enhanced categorization"""
    if not financials or not quarters:
        logger.warning(f"No data to insert for {stock_code}")
        return
    
    try:
        cur = conn.cursor()
        batch_data = []
        
        # Prepare batch data with automatic categorization
        for metric, values in financials.items():
            metric_category = categorize_metric(metric)
            
            for i, quarter in enumerate(quarters):
                value = values[i] if i < len(values) else ""
                
                if value:  # Only insert non-empty values
                    batch_data.append((
                        stock_code, metric, quarter, value, industry, category, metric_category
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
                    column6 AS CATEGORY,
                    column7 AS METRIC_CATEGORY
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
                    METRIC_CATEGORY = src.METRIC_CATEGORY,
                    UPDATED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN 
                INSERT (STOCK_CODE, METRIC, QUARTER, VALUE, INDUSTRY, CATEGORY, METRIC_CATEGORY)
                VALUES (src.STOCK_CODE, src.METRIC, src.QUARTER, src.VALUE, src.INDUSTRY, src.CATEGORY, src.METRIC_CATEGORY)
        """
        
        # Format data for VALUES clause with proper escaping
        values_list = []
        for d in batch_data:
            escaped_values = [str(val).replace("'", "''") for val in d]
            values_list.append(f"('{escaped_values[0]}', '{escaped_values[1]}', '{escaped_values[2]}', '{escaped_values[3]}', '{escaped_values[4]}', '{escaped_values[5]}', '{escaped_values[6]}')")
        
        values_str = ",".join(values_list)
        final_query = merge_query % values_str
        
        cur.execute(final_query)
        conn.commit()
        
        logger.info(f"✅ Inserted {len(batch_data)} records for {stock_code}")
        
    except Exception as e:
        logger.error(f"❌ Error inserting data for {stock_code}: {e}")
        conn.rollback()
        raise

# ------------------- Additional Analytics Routes -------------------
@app.route("/metrics-summary")
def metrics_summary():
    """Show summary of all discovered metrics by category"""
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT METRIC_CATEGORY, COUNT(DISTINCT METRIC) as METRIC_COUNT,
                   COUNT(DISTINCT STOCK_CODE) as STOCK_COUNT
            FROM FINANCIALS_QUARTERLY
            GROUP BY METRIC_CATEGORY
            ORDER BY METRIC_COUNT DESC
        """)
        
        summary_data = cur.fetchall()
        conn.close()
        
        return render_template("metrics_summary.html", summary_data=summary_data)
        
    except Exception as e:
        logger.error(f"Error in metrics summary: {e}")
        return f"<h2>Error loading metrics summary</h2>"

@app.route("/api/metrics/<category>")
def api_metrics_by_category(category):
    """API endpoint to get metrics by category"""
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT DISTINCT METRIC
            FROM FINANCIALS_QUARTERLY
            WHERE METRIC_CATEGORY = %s
            ORDER BY METRIC
        """, (category,))
        
        metrics = [row[0] for row in cur.fetchall()]
        conn.close()
        
        return json.dumps({"category": category, "metrics": metrics})
        
    except Exception as e:
        logger.error(f"Error in API metrics by category: {e}")
        return json.dumps({"error": str(e)})

# ------------------- Main -------------------
if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced Stock Recommender Application')
    parser.add_argument('--load-data', action='store_true', help='Load all stock data')
    parser.add_argument('--run-app', action='store_true', help='Run Flask application')
    parser.add_argument('--test-single', type=str, help='Test scraping for a single stock')
    
    args = parser.parse_args()
    
    if args.test_single:
        # Test scraping for a single stock
        data, quarters, category, industry = get_financial_data(args.test_single)
        print(f"Found {len(data)} metrics for {args.test_single}")
        for metric in sorted(data.keys()):
            print(f"  - {metric}: {categorize_metric(metric)}")
    elif args.load_data:
        load_all_data()
    elif args.run_app:
        app.run(debug=True, host='0.0.0.0', port=5000)
    else:
        # Default behavior: load data then run app
        load_all_data()
        app.run(debug=True, host='0.0.0.0', port=5000)
