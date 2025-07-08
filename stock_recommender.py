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
    "Large Cap": ["RELIANCE", "TCS", "ITC","HDFCBANK"],
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
        # Check if we can connect to Snowflake at all
        try:
            conn = snowflake_connect()
            cur = conn.cursor()
            # Test basic connectivity
            cur.execute("SELECT 1")
            cur.fetchone()
        except Exception as db_error:
            logger.error(f"Snowflake connection failed: {db_error}")
            # Use fallback data directly when database is unavailable
            return serve_fallback_quarterly_view(stock)
        
        # Try to ensure the table exists
        try:
            create_snowflake_table()
        except Exception as table_error:
            logger.error(f"Table creation failed: {table_error}")
            conn.close()
            return serve_fallback_quarterly_view(stock)

        # Check if data exists for this stock
        cur.execute("""
            SELECT METRIC, QUARTER, VALUE, METRIC_CATEGORY
            FROM FINANCIALS_QUARTERLY
            WHERE STOCK_CODE=%s
            ORDER BY METRIC_CATEGORY, METRIC, QUARTER
        """, (stock,))
        rows = cur.fetchall()

        # If no data found, try to load it automatically
        if not rows:
            logger.info(f"No data found for {stock}, attempting to load...")
            try:
                # Get financial data (with fallback)
                data, quarters, category, industry = get_financial_data(stock)
                
                if data and quarters:
                    # Insert the data
                    insert_quarterly_to_snowflake(conn, stock, data, quarters, category, industry)
                    logger.info(f"Successfully loaded data for {stock}")
                    
                    # Re-query the database
                    cur.execute("""
                        SELECT METRIC, QUARTER, VALUE, METRIC_CATEGORY
                        FROM FINANCIALS_QUARTERLY
                        WHERE STOCK_CODE=%s
                        ORDER BY METRIC_CATEGORY, METRIC, QUARTER
                    """, (stock,))
                    rows = cur.fetchall()
                else:
                    conn.close()
                    return f"""
                    <div class="container mt-5">
                        <div class="alert alert-warning">
                            <h4>⚠️ No data available for {stock}</h4>
                            <p>Unable to fetch financial data from external sources.</p>
                            <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                            <a href="/test-scraper/{stock}" class="btn btn-secondary" target="_blank">🔧 Test Data Source</a>
                        </div>
                    </div>
                    """
            except Exception as load_error:
                logger.error(f"Failed to load data for {stock}: {load_error}")
                conn.close()
                return f"""
                <div class="container mt-5">
                    <div class="alert alert-danger">
                        <h4>❌ Error loading data for {stock}</h4>
                        <p>Error: {str(load_error)}</p>
                        <div class="mt-3">
                            <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                            <a href="/test-scraper/{stock}" class="btn btn-secondary" target="_blank">🔧 Test Data Source</a>
                            <a href="/debug/{stock}" class="btn btn-info" target="_blank">🔍 Debug Database</a>
                        </div>
                    </div>
                </div>
                """

        # If still no data after attempting to load
        if not rows:
            conn.close()
            return f"""
            <div class="container mt-5">
                <div class="alert alert-info">
                    <h4>📊 No data found for {stock}</h4>
                    <p>This stock may not be available in our data sources.</p>
                    <div class="mt-3">
                        <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                        <button class="btn btn-success" onclick="loadStockData('{stock}')">🔄 Try Loading Data</button>
                        <a href="/test-scraper/{stock}" class="btn btn-secondary" target="_blank">🔧 Test Data Source</a>
                    </div>
                </div>
                <script>
                function loadStockData(stock) {{
                    fetch(`/load-single/${{stock}}`, {{method: 'POST'}})
                    .then(response => response.json())
                    .then(data => {{
                        if (data.status === 'success') {{
                            location.reload();
                        }} else {{
                            alert('Error: ' + data.message);
                        }}
                    }});
                }}
                </script>
            </div>
            """

        # Process the data for display
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
            
            # Clean and filter values for charts
            values = []
            for q in quarters:
                value = quarter_data.get(q, "")
                # Ensure we have valid numeric data
                if value and str(value).strip() and value != "":
                    try:
                        # Try to convert to float to validate
                        float(value)
                        values.append(str(value))
                    except (ValueError, TypeError):
                        values.append("")
                else:
                    values.append("")
            
            categorized_data[category][metric] = values

        conn.close()
        return render_template("quarterly.html",
                               stock=stock,
                               quarters=quarters,
                               financial_data=json.dumps(categorized_data),
                               metric_categories=categorized_data)
    
    except Exception as e:
        logger.error(f"Error in quarterly view for {stock}: {e}")
        return f"""
        <div class="container mt-5">
            <div class="alert alert-danger">
                <h4>❌ Database Error for {stock}</h4>
                <p>Error: {str(e)}</p>
                <div class="mt-3">
                    <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                    <a href="/debug/{stock}" class="btn btn-info" target="_blank">🔍 Debug Database</a>
                </div>
            </div>
        </div>
        """

@app.route("/sector/<sector>")
def sector_view(sector):
    try:
        # First try database approach
        
                       
        try:
            conn = snowflake_connect()
            cur = conn.cursor()

            # First, get all available sectors/categories
            cur.execute("SELECT DISTINCT CATEGORY FROM FINANCIALS_QUARTERLY WHERE CATEGORY IS NOT NULL")
            available_categories = [row[0] for row in cur.fetchall()]
            
            # Try to find the sector, case-insensitive
            matched_category = None
            for cat in available_categories:
                if cat.lower() == sector.lower():
                    matched_category = cat
                    break
            
            if matched_category:
                cur.execute("""
                    SELECT STOCK_CODE, METRIC, QUARTER, VALUE, METRIC_CATEGORY
                    FROM FINANCIALS_QUARTERLY
                    WHERE CATEGORY=%s
                    ORDER BY METRIC_CATEGORY, STOCK_CODE, METRIC
                """, (matched_category,))
                
                rows = cur.fetchall()

                if rows:
                    # Process database data
                    
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
                    logger.warning(f"Returning Data for render {matched_category}: {matched_category}")
                    return render_template("sector.html",
                                           sector=sector,
                                           quarters=quarters,
                                           financial_data=(categorized_data),
                                           financial_json=json.dumps(categorized_data))
                    
            conn.close()
        except Exception as db_error:
            logger.warning(f"Database approach failed for sector {sector}: {db_error}")
        
        # Fallback to using fallback data
        return serve_fallback_sector_view(sector)
    
    except Exception as e:
        logger.error(f"Error in sector view for {sector}: {e}")
        return serve_fallback_sector_view(sector)

def serve_fallback_sector_view(sector: str):
    """Serve sector comparison using fallback data"""
    try:
        # Normalize sector name
        sector_normalized = sector.replace("%20", " ").replace("+", " ").strip()
        
        # Find stocks in this sector using fallback data
        sector_stocks = []
        for stock_code, stock_info in FALLBACK_FINANCIAL_DATA.items():
            if stock_info["category"].lower() == sector_normalized.lower():
                sector_stocks.append(stock_code)
        
        if not sector_stocks:
            available_sectors = list(set([info["category"] for info in FALLBACK_FINANCIAL_DATA.values()]))
            return f"""
            <div class="container mt-5">
                <div class="alert alert-warning">
                    <h4>⚠️ Sector '{sector_normalized}' not found</h4>
                    <p><strong>Available sectors:</strong> {', '.join(available_sectors)}</p>
                    <div class="mt-3">
                        <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                        {' '.join([f'<a href="/sector/{cat}" class="btn btn-outline-primary">{cat}</a>' for cat in available_sectors])}
                    </div>
                </div>
            </div>
            """
        
        # Get common quarters (should be same for all stocks)
        quarters = FALLBACK_FINANCIAL_DATA[sector_stocks[0]]["quarters"]
        
        # Organize data by metric category
        categorized_data = {}
        
        for stock_code in sector_stocks:
            stock_data = FALLBACK_FINANCIAL_DATA[stock_code]["data"]
            
            for metric, values in stock_data.items():
                # Categorize the metric
                metric_category = categorize_metric(metric)
                
                if metric_category not in categorized_data:
                    categorized_data[metric_category] = {}
                
                # Create display key
                display_key = f"{stock_code} - {metric}"
                categorized_data[metric_category][display_key] = values
        
        # Create HTML for sector comparison
        html = f"""
        <div class="container mt-4">
            <div class="alert alert-info mb-4">
                <h4>📊 {sector_normalized} Sector Comparison</h4>
                <p><strong>Note:</strong> Using sample data for demonstration. Stocks: {', '.join(sector_stocks)}</p>
                <p><strong>Quarters:</strong> {', '.join(quarters)}</p>
            </div>
            
            <h2><i class="fas fa-industry"></i> {sector_normalized} Sector Analysis</h2>
        """
        
        # Add categorized tables
        for category, metrics in categorized_data.items():
            html += f"""
            <div class="card mt-4">
                <div class="card-header">
                    <h4>{category}</h4>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-striped table-hover">
                            <thead class="table-dark">
                                <tr>
                                    <th>Stock - Metric</th>
            """
            
            for quarter in quarters:
                html += f'<th class="text-center">{quarter}</th>'
            
            html += """
                                </tr>
                            </thead>
                            <tbody>
            """
            
            for display_key, values in metrics.items():
                html += f'<tr><td class="fw-bold">{display_key}</td>'
                for value in values:
                    # Clean up value display
                    clean_value = str(value).replace(",", "")
                    try:
                        # Try to format as number if possible
                        float_val = float(clean_value)
                        if float_val > 1000:
                            formatted_value = f"{float_val:,.0f}"
                        else:
                            formatted_value = f"{float_val:.1f}"
                    except:
                        formatted_value = str(value)
                    
                    html += f'<td class="text-center">{formatted_value}</td>'
                html += '</tr>'
            
            html += """
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            """
        
        html += f"""
            <div class="mt-4">
                <a href="/" class="btn btn-primary">
                    <i class="fas fa-arrow-left"></i> Back to Dashboard
                </a>
                <a href="/sector/Large%20Cap" class="btn btn-outline-primary">Large Cap</a>
                <a href="/sector/Mid%20Cap" class="btn btn-outline-success">Mid Cap</a>
                <a href="/sector/Small%20Cap" class="btn btn-outline-warning">Small Cap</a>
            </div>
        </div>
        
        <style>
            .card-header h4 {{
                margin: 0;
                color: #495057;
            }}
            .table th {{
                background-color: #343a40 !important;
                color: white !important;
                border: none !important;
            }}
            .table-striped tbody tr:nth-of-type(odd) {{
                background-color: rgba(0,123,255,.05);
            }}
            .fw-bold {{
                font-weight: 600 !important;
            }}
        </style>
        """
        
        return html
        
    except Exception as e:
        logger.error(f"Error in fallback sector view for {sector}: {e}")
        return f"""
        <div class="container mt-5">
            <div class="alert alert-danger">
                <h4>❌ Error in Sector Comparison</h4>
                <p>Error: {str(e)}</p>
                <a href="/" class="btn btn-primary">← Back to Dashboard</a>
            </div>
        </div>
        """

@app.route("/")
def index():
    return render_template("index.html", categories=STOCKS)

@app.route("/visualize", methods=["POST"])
def visualize():
    stock = request.form['stock']
    try:
        # Ensure table exists
        create_snowflake_table()
        
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

        # If no data found, try to load it automatically
        if not rows:
            logger.info(f"No data found for {stock}, attempting to load...")
            try:
                # Get financial data (with fallback)
                data, quarters, category, industry = get_financial_data(stock)
                
                if data and quarters:
                    # Insert the data
                    insert_quarterly_to_snowflake(conn, stock, data, quarters, category, industry)
                    logger.info(f"Successfully loaded data for {stock}")
                    
                    # Re-query the database
                    cur.execute("""
                        SELECT STOCK_CODE, METRIC, QUARTER, VALUE, INDUSTRY, CATEGORY, METRIC_CATEGORY
                        FROM FINANCIALS_QUARTERLY 
                        WHERE STOCK_CODE=%s
                        ORDER BY METRIC_CATEGORY, METRIC, QUARTER
                    """, (stock,))
                    rows = cur.fetchall()
                else:
                    conn.close()
                    return f"""
                    <div class="container mt-5">
                        <div class="alert alert-warning">
                            <h4>⚠️ No data available for {stock}</h4>
                            <p>Unable to fetch financial data from external sources.</p>
                            <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                            <a href="/test-scraper/{stock}" class="btn btn-secondary" target="_blank">🔧 Test Data Source</a>
                        </div>
                    </div>
                    """
            except Exception as load_error:
                logger.error(f"Failed to load data for {stock}: {load_error}")
                conn.close()
                return f"""
                <div class="container mt-5">
                    <div class="alert alert-danger">
                        <h4>❌ Error loading data for {stock}</h4>
                        <p>Error: {str(load_error)}</p>
                        <div class="mt-3">
                            <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                            <a href="/test-scraper/{stock}" class="btn btn-secondary" target="_blank">🔧 Test Data Source</a>
                        </div>
                    </div>
                </div>
                """

        # If still no data
        if not rows:
            conn.close()
            return f"""
            <div class="container mt-5">
                <div class="alert alert-info">
                    <h4>📊 No data found for {stock}</h4>
                    <p>This stock may not be available in our data sources.</p>
                    <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                </div>
            </div>
            """

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
        return f"""
        <div class="container mt-5">
            <div class="alert alert-danger">
                <h4>❌ Database Error for {stock}</h4>
                <p>Error: {str(e)}</p>
                <a href="/" class="btn btn-primary">← Back to Dashboard</a>
            </div>
        </div>
        """

@app.route("/debug/<stock>")
def debug_data(stock):
    """Debug route to see raw data structure"""
    try:
        conn = snowflake_connect()
        cur = conn.cursor()

        # First check if table exists and has data
        cur.execute("SELECT COUNT(*) FROM FINANCIALS_QUARTERLY")
        total_count = cur.fetchone()[0]
        
        # Check for specific stock
        cur.execute("SELECT COUNT(*) FROM FINANCIALS_QUARTERLY WHERE STOCK_CODE=%s", (stock,))
        stock_count = cur.fetchone()[0]

        cur.execute("""
            SELECT METRIC, QUARTER, VALUE, METRIC_CATEGORY
            FROM FINANCIALS_QUARTERLY
            WHERE STOCK_CODE=%s
            ORDER BY METRIC_CATEGORY, METRIC, QUARTER
            LIMIT 20
        """, (stock,))
        rows = cur.fetchall()

        debug_info = {
            "table_total_rows": total_count,
            "stock_rows": stock_count,
            "sample_rows": rows,
            "unique_categories": list(set([row[3] for row in rows])) if rows else [],
            "unique_quarters": list(set([row[1] for row in rows])) if rows else [],
            "value_types": [type(row[2]).__name__ for row in rows[:5]] if rows else [],
            "data_structure_test": {
                "stock": stock,
                "has_data": len(rows) > 0,
                "first_metric": rows[0] if rows else None
            }
        }
        
        conn.close()
        return f"<pre>{json.dumps(debug_info, indent=2, default=str)}</pre>"
        
    except Exception as e:
        return f"<pre>Error: {str(e)}</pre>"

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
            # Try fallback data
            return use_fallback_data(stock_code)

        soup = BeautifulSoup(res.content, "html.parser")

        # Extract industry and sector/category info
        category, industry = extract_company_info(soup)
        
        # Extract ALL financial data from multiple sections
        all_data, quarters = extract_all_financial_data(soup, stock_code)
        
        # If no data extracted, try fallback
        if not all_data or not quarters:
            logger.warning(f"No data extracted from scraping for {stock_code}, trying fallback")
            return use_fallback_data(stock_code)
        
        return all_data, quarters, category, industry
        
    except requests.RequestException as e:
        logger.error(f"❌ Request failed for {stock_code}: {e}")
        # Try fallback data
        return use_fallback_data(stock_code)
    except Exception as e:
        logger.error(f"❌ Unexpected error for {stock_code}: {e}")
        # Try fallback data
        return use_fallback_data(stock_code)

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
    
    # Try multiple selectors for quarterly data
    quarterly_table = None
    
    # Try different possible selectors
    selectors = [
        "section#quarters",
        "section[id*='quarter']",
        "div[class*='quarter']",
        "table[class*='quarter']",
        ".table-responsive table"
    ]
    
    for selector in selectors:
        quarterly_table = soup.select_one(selector)
        if quarterly_table:
            logger.info(f"Found quarterly table using selector: {selector}")
            break
    
    # If still not found, try to find any table with quarterly data
    if not quarterly_table:
        all_tables = soup.find_all("table")
        for table in all_tables:
            # Check if table headers contain quarterly periods
            headers = table.select("thead tr th")
            if headers and len(headers) > 3:
                header_text = " ".join([h.get_text().strip() for h in headers])
                if any(pattern in header_text.lower() for pattern in ["mar", "jun", "sep", "dec", "q1", "q2", "q3", "q4"]):
                    quarterly_table = table
                    logger.info(f"Found quarterly table by pattern matching")
                    break
    
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

@app.route("/load-data", methods=["POST"])
def load_data_endpoint():
    """API endpoint to trigger data loading"""
    try:
        # This would typically be run in the background
        import threading
        thread = threading.Thread(target=load_all_data)
        thread.start()
        
        return json.dumps({"status": "success", "message": "Data loading initiated"})
        
    except Exception as e:
        logger.error(f"Error initiating data load: {e}")
        return json.dumps({"status": "error", "message": str(e)})

@app.route("/load-single/<stock>", methods=["POST"])
def load_single_stock(stock):
    """Load data for a single stock"""
    try:
        create_snowflake_table()
        conn = snowflake_connect()
        
        stock_code = stock.upper()
        logger.info(f"Loading data for single stock: {stock_code}")
        
        # Get financial data
        data, quarters, category, industry = get_financial_data(stock_code)
        
        if data and quarters:
            insert_quarterly_to_snowflake(conn, stock_code, data, quarters, category, industry)
            conn.close()
            
            result = {
                "status": "success",
                "message": f"Successfully loaded {len(data)} metrics for {stock_code}",
                "metrics_count": len(data),
                "quarters": quarters,
                "category": category,
                "industry": industry
            }
            return json.dumps(result)
        else:
            conn.close()
            return json.dumps({
                "status": "error", 
                "message": f"No data found for {stock_code}. Please check if the stock code is correct."
            })
            
    except Exception as e:
        logger.error(f"Error loading single stock {stock}: {e}")
        return json.dumps({"status": "error", "message": str(e)})

@app.route("/test-scraper/<stock>")
def test_scraper_route(stock):
    """Test web scraping for a single stock"""
    try:
        stock_code = stock.upper()
        url = SCREENER_URL.format(stock_code)
        
        # Test the scraping
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response_status = response.status_code
            scraping_success = response.status_code == 200
        except Exception as e:
            response_status = "Failed"
            scraping_success = False
            
        # Test quarterly data extraction
        data, quarters, category, industry = get_financial_data(stock_code)
        
        result = {
            "stock_code": stock_code,
            "url": url,
            "response_status": response_status,
            "scraping_success": scraping_success,
            "metrics_found": len(data),
            "quarters": quarters,
            "category": category,
            "industry": industry,
            "sample_metrics": list(data.keys())[:10] if data else [],
            "fallback_used": stock_code in FALLBACK_FINANCIAL_DATA,
            "data_source": "fallback" if stock_code in FALLBACK_FINANCIAL_DATA and not scraping_success else "web_scraping"
        }
        
        return f"<pre>{json.dumps(result, indent=2)}</pre>"
        
    except Exception as e:
        return f"<h2>Error testing scraper for {stock}: {e}</h2>"

@app.route("/test-full-flow/<stock>")
def test_full_flow(stock):
    """Test complete flow: scrape data, insert to database, retrieve and display"""
    try:
        stock_code = stock.upper()
        
        # Step 1: Create table
        create_snowflake_table()
        
        # Step 2: Get data
        data, quarters, category, industry = get_financial_data(stock_code)
        
        # Step 3: Insert to database
        if data and quarters:
            conn = snowflake_connect()
            insert_quarterly_to_snowflake(conn, stock_code, data, quarters, category, industry)
            conn.close()
        
        # Step 4: Retrieve from database
        conn = snowflake_connect()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM FINANCIALS_QUARTERLY WHERE STOCK_CODE=%s
        """, (stock_code,))
        db_count = cur.fetchone()[0]
        
        cur.execute("""
            SELECT METRIC, QUARTER, VALUE, METRIC_CATEGORY
            FROM FINANCIALS_QUARTERLY
            WHERE STOCK_CODE=%s
            ORDER BY METRIC, QUARTER
            LIMIT 10
        """, (stock_code,))
        sample_rows = cur.fetchall()
        conn.close()
        
        result = {
            "stock_code": stock_code,
            "step1_table_created": "✅ Success",
            "step2_data_extracted": f"✅ {len(data)} metrics, {len(quarters)} quarters",
            "step3_data_inserted": f"✅ Inserted to database",
            "step4_db_verification": f"✅ {db_count} rows in database",
            "sample_data": sample_rows,
            "categories": category,
            "industry": industry,
            "quarters": quarters
        }
        
        return f"<pre>{json.dumps(result, indent=2, default=str)}</pre>"
        
    except Exception as e:
        return f"<h2>Error in full flow test for {stock}: {e}</h2>"

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

@app.route("/simple-quarterly/<stock>")
def simple_quarterly_view(stock):
    """Ultra-simple quarterly view using fallback data to test basic functionality"""
    stock = stock.upper()
    
    # Check if stock exists in fallback data
    if stock not in FALLBACK_FINANCIAL_DATA:
        available_stocks = list(FALLBACK_FINANCIAL_DATA.keys())
        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px;">
            <h2>❌ Stock {stock} not available in simple view</h2>
            <p><strong>Available stocks:</strong> {', '.join(available_stocks)}</p>
            <div style="margin: 20px 0;">
                {' '.join([f'<a href="/simple-quarterly/{s}" style="background: #007bff; color: white; padding: 8px 16px; text-decoration: none; border-radius: 5px; margin: 5px;">{s}</a>' for s in available_stocks])}
            </div>
            <a href="/" style="background: #6c757d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">← Back to Dashboard</a>
        </div>
        """
    
    # Get stock data
    stock_info = FALLBACK_FINANCIAL_DATA[stock]
    data = stock_info["data"]
    quarters = stock_info["quarters"]
    category = stock_info["category"]
    industry = stock_info["industry"]
    
    # Build HTML
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 1200px; margin: 20px auto; padding: 20px;">
        <h1>📊 {stock} - Simple Quarterly View</h1>
        
        <div style="background: #d4edda; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <p><strong>✅ This is working!</strong> Simple fallback data displayed successfully.</p>
            <p><strong>Category:</strong> {category} | <strong>Industry:</strong> {industry}</p>
        </div>
        
        <h3>📅 Quarters: {', '.join(quarters)}</h3>
        
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
            <thead>
                <tr style="background: #f8f9fa;">
                    <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Metric</th>
    """
    
    # Add quarter headers
    for quarter in quarters:
        html += f'<th style="padding: 10px; border: 1px solid #ddd; text-align: center;">{quarter}</th>'
    
    html += """
                </tr>
            </thead>
            <tbody>
    """
    
    # Add data rows
    for metric, values in data.items():
        html += f'<tr><td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">{metric}</td>'
        for value in values:
            # Format the value for display
            display_value = str(value).replace(",", "")
            try:
                float_val = float(display_value)
                if float_val > 1000:
                    formatted_value = f"{float_val:,.0f}"
                else:
                    formatted_value = f"{float_val:.1f}"
            except:
                formatted_value = str(value)
            
            html += f'<td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{formatted_value}</td>'
        html += '</tr>'
    
    html += f"""
            </tbody>
        </table>
        
        <div style="margin: 30px 0;">
            <a href="/" style="background: #6c757d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">← Back to Dashboard</a>
            <a href="/quarterly/{stock}" style="background: #dc3545; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">Try Complex Version</a>
            <a href="/debug-fallback/{stock}" style="background: #17a2b8; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">Debug Data</a>
            <a href="/sector/{category.replace(' ', '%20')}" style="background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-left: 10px;">Compare {category}</a>
        </div>
        
        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <h5>🧪 Test Other Stocks:</h5>
            <div style="margin-top: 10px;">
    """
    
    # Add links to other stocks
    for other_stock in FALLBACK_FINANCIAL_DATA.keys():
        if other_stock != stock:
            color = "#007bff" if other_stock in ["RELIANCE", "TCS", "ITC"] else "#28a745" if other_stock in ["PIDILITIND", "CUMMINSIND"] else "#ffc107"
            text_color = "white" if color != "#ffc107" else "black"
            html += f'<a href="/simple-quarterly/{other_stock}" style="background: {color}; color: {text_color}; padding: 8px 16px; text-decoration: none; border-radius: 5px; margin: 5px; display: inline-block;">{other_stock}</a>'
    
    html += """
            </div>
        </div>
    </div>
    """
    
    return html

@app.route("/debug-fallback/<stock>")
def debug_fallback(stock):
    """Debug fallback data structure"""
    try:
        stock = stock.upper()
        
        debug_info = {
            "stock": stock,
            "available_stocks": list(FALLBACK_FINANCIAL_DATA.keys()),
            "stock_exists": stock in FALLBACK_FINANCIAL_DATA,
        }
        
        if stock in FALLBACK_FINANCIAL_DATA:
            raw_data = FALLBACK_FINANCIAL_DATA[stock]
            debug_info["raw_data_type"] = type(raw_data).__name__
            debug_info["raw_data_keys"] = list(raw_data.keys()) if isinstance(raw_data, dict) else "Not a dict"
            debug_info["raw_data_sample"] = str(raw_data)[:500]
            
            if isinstance(raw_data, dict) and "data" in raw_data:
                data = raw_data["data"]
                debug_info["data_type"] = type(data).__name__
                debug_info["data_keys"] = list(data.keys()) if isinstance(data, dict) else "Not a dict"
                
                if isinstance(data, dict):
                    sample_metric = list(data.keys())[0] if data else None
                    if sample_metric:
                        debug_info["sample_metric"] = sample_metric
                        debug_info["sample_values"] = data[sample_metric]
                        debug_info["sample_values_type"] = type(data[sample_metric]).__name__
        
        # Test the function
        try:
            data, quarters, category, industry = use_fallback_data(stock)
            debug_info["function_test"] = {
                "data_type": type(data).__name__,
                "data_len": len(data) if isinstance(data, dict) else "Not a dict",
                "quarters_type": type(quarters).__name__,
                "quarters_len": len(quarters) if isinstance(quarters, list) else "Not a list",
                "category": category,
                "industry": industry
            }
        except Exception as e:
            debug_info["function_test_error"] = str(e)
        
        return f"<pre>{json.dumps(debug_info, indent=2, default=str)}</pre>"
        
    except Exception as e:
        return f"<pre>Debug error: {str(e)}</pre>"

@app.route("/diagnostic")
def diagnostic():
    """Comprehensive diagnostic route to identify issues"""
    diagnostics = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "environment_check": {},
        "database_check": {},
        "fallback_check": {},
        "import_check": {},
        "overall_status": "Unknown"
    }
    
    # 1. Environment Variables Check
    try:
        required_vars = ["SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_ACCOUNT"]
        for var in required_vars:
            value = os.getenv(var)
            diagnostics["environment_check"][var] = "✅ Set" if value else "❌ Missing"
        
        env_status = all(os.getenv(var) for var in required_vars)
        diagnostics["environment_check"]["status"] = "✅ All Required Variables Set" if env_status else "❌ Missing Variables"
    except Exception as e:
        diagnostics["environment_check"]["error"] = str(e)
    
    # 2. Database Connection Check
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        result = cur.fetchone()
        conn.close()
        
        diagnostics["database_check"]["connection"] = "✅ Successful"
        diagnostics["database_check"]["test_query"] = "✅ Working"
    except Exception as e:
        diagnostics["database_check"]["connection"] = "❌ Failed"
        diagnostics["database_check"]["error"] = str(e)
    
    # 3. Table Check
    try:
        conn = snowflake_connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM FINANCIALS_QUARTERLY")
        count = cur.fetchone()[0]
        conn.close()
        
        diagnostics["database_check"]["table_exists"] = "✅ Yes"
        diagnostics["database_check"]["row_count"] = count
    except Exception as e:
        diagnostics["database_check"]["table_check"] = "❌ Failed"
        diagnostics["database_check"]["table_error"] = str(e)
    
    # 4. Fallback Data Check
    try:
        diagnostics["fallback_check"]["available_stocks"] = list(FALLBACK_FINANCIAL_DATA.keys())
        diagnostics["fallback_check"]["count"] = len(FALLBACK_FINANCIAL_DATA)
        
        # Test one fallback
        data, quarters, category, industry = use_fallback_data("RELIANCE")
        if data and quarters:
            diagnostics["fallback_check"]["test_reliance"] = "✅ Working"
            diagnostics["fallback_check"]["sample_metrics"] = list(data.keys())[:3]
        else:
            diagnostics["fallback_check"]["test_reliance"] = "❌ Failed"
    except Exception as e:
        diagnostics["fallback_check"]["error"] = str(e)
    
    # 5. Import Check
    try:
        import snowflake.connector
        import pandas as pd
        import requests
        from bs4 import BeautifulSoup
        from flask import Flask, render_template, request
        
        diagnostics["import_check"]["snowflake"] = "✅ Available"
        diagnostics["import_check"]["pandas"] = "✅ Available"
        diagnostics["import_check"]["requests"] = "✅ Available"
        diagnostics["import_check"]["beautifulsoup"] = "✅ Available"
        diagnostics["import_check"]["flask"] = "✅ Available"
    except Exception as e:
        diagnostics["import_check"]["error"] = str(e)
    
    # 6. Overall Status
    db_working = diagnostics["database_check"].get("connection") == "✅ Successful"
    fallback_working = diagnostics["fallback_check"].get("test_reliance") == "✅ Working"
    
    if db_working:
        diagnostics["overall_status"] = "✅ Database Available - Full Functionality"
    elif fallback_working:
        diagnostics["overall_status"] = "⚠️ Database Unavailable - Fallback Mode Active"
    else:
        diagnostics["overall_status"] = "❌ Critical Issues - Limited Functionality"
    
    # 7. Recommendations
    recommendations = []
    if not db_working:
        recommendations.append("🔧 Check Snowflake environment variables")
        recommendations.append("🔧 Verify Snowflake credentials")
        recommendations.append("🔧 Test database connectivity")
    
    if fallback_working:
        recommendations.append("✅ Fallback data available for testing")
        recommendations.append("🎯 Try: /quarterly/RELIANCE")
    
    diagnostics["recommendations"] = recommendations
    
    return f"<pre>{json.dumps(diagnostics, indent=2, default=str)}</pre>"

# ------------------- Fallback Data for Testing -------------------
FALLBACK_FINANCIAL_DATA = {
    # Large Cap Stocks
    "RELIANCE": {
        "data": {
            "Sales": ["2,15,000", "2,18,000", "2,20,000", "2,25,000"],
            "Net Profit": ["15,000", "16,000", "17,000", "18,000"],
            "Total Assets": ["7,50,000", "7,65,000", "7,80,000", "7,95,000"],
            "Equity": ["4,50,000", "4,60,000", "4,70,000", "4,80,000"],
            "ROE %": ["15.5", "16.2", "16.8", "17.1"],
            "ROCE %": ["12.5", "13.1", "13.8", "14.2"],
            "Current Ratio": ["1.2", "1.3", "1.4", "1.5"],
            "EPS": ["25.5", "26.8", "28.1", "29.5"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Large Cap",
        "industry": "Oil & Gas"
    },
    "TCS": {
        "data": {
            "Sales": ["55,000", "58,000", "60,000", "62,000"],
            "Net Profit": ["10,500", "11,200", "11,800", "12,400"],
            "Total Assets": ["1,50,000", "1,55,000", "1,60,000", "1,65,000"],
            "Equity": ["1,20,000", "1,25,000", "1,30,000", "1,35,000"],
            "ROE %": ["28.5", "29.1", "29.8", "30.2"],
            "ROCE %": ["32.5", "33.1", "33.8", "34.2"],
            "Current Ratio": ["2.1", "2.2", "2.3", "2.4"],
            "EPS": ["28.5", "30.1", "31.8", "33.2"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Large Cap",
        "industry": "IT Services"
    },
    "ITC": {
        "data": {
            "Sales": ["65,000", "68,000", "70,000", "72,000"],
            "Net Profit": ["18,000", "19,000", "20,000", "21,000"],
            "Total Assets": ["85,000", "88,000", "90,000", "92,000"],
            "Equity": ["65,000", "68,000", "70,000", "72,000"],
            "ROE %": ["24.5", "25.1", "25.8", "26.2"],
            "ROCE %": ["26.5", "27.1", "27.8", "28.2"],
            "Current Ratio": ["1.8", "1.9", "2.0", "2.1"],
            "EPS": ["22.5", "23.8", "25.1", "26.4"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Large Cap",
        "industry": "FMCG"
    },
    
    # Mid Cap Stocks
    "PIDILITIND": {
        "data": {
            "Sales": ["8,500", "8,800", "9,200", "9,600"],
            "Net Profit": ["1,200", "1,350", "1,450", "1,600"],
            "Total Assets": ["12,000", "12,500", "13,000", "13,500"],
            "Equity": ["8,500", "8,800", "9,100", "9,400"],
            "ROE %": ["18.2", "19.1", "19.8", "20.5"],
            "ROCE %": ["22.1", "23.2", "24.1", "25.0"],
            "Current Ratio": ["2.8", "2.9", "3.1", "3.2"],
            "EPS": ["45.2", "48.1", "51.3", "54.8"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Mid Cap",
        "industry": "Chemicals"
    },
    "CUMMINSIND": {
        "data": {
            "Sales": ["18,500", "19,200", "20,100", "21,000"],
            "Net Profit": ["2,100", "2,300", "2,500", "2,700"],
            "Total Assets": ["22,000", "22,800", "23,500", "24,200"],
            "Equity": ["16,500", "17,000", "17,500", "18,000"],
            "ROE %": ["14.8", "15.5", "16.2", "17.0"],
            "ROCE %": ["16.2", "17.1", "18.0", "18.9"],
            "Current Ratio": ["1.9", "2.0", "2.1", "2.2"],
            "EPS": ["65.8", "68.2", "71.5", "75.1"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Mid Cap",
        "industry": "Auto Components"
    },
    
    # Small Cap Stocks
    "HATSUN": {
        "data": {
            "Sales": ["1,850", "1,920", "2,050", "2,180"],
            "Net Profit": ["125", "142", "158", "175"],
            "Total Assets": ["2,200", "2,350", "2,480", "2,620"],
            "Equity": ["1,500", "1,580", "1,650", "1,720"],
            "ROE %": ["12.5", "13.2", "14.1", "15.0"],
            "ROCE %": ["15.8", "16.5", "17.2", "18.1"],
            "Current Ratio": ["1.5", "1.6", "1.7", "1.8"],
            "EPS": ["8.2", "9.1", "10.3", "11.5"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Small Cap",
        "industry": "Food Products"
    },
    "BALAMINES": {
        "data": {
            "Sales": ["2,250", "2,380", "2,520", "2,680"],
            "Net Profit": ["285", "315", "345", "380"],
            "Total Assets": ["3,200", "3,400", "3,600", "3,800"],
            "Equity": ["2,100", "2,200", "2,300", "2,400"],
            "ROE %": ["16.8", "17.5", "18.2", "19.1"],
            "ROCE %": ["19.2", "20.1", "21.0", "22.0"],
            "Current Ratio": ["2.2", "2.3", "2.4", "2.5"],
            "EPS": ["22.8", "25.2", "27.6", "30.4"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Small Cap",
        "industry": "Chemicals"
    }
}

def use_fallback_data(stock_code: str) -> Tuple[Dict, List, str, str]:
    """Use fallback data when web scraping fails"""
    try:
        if stock_code in FALLBACK_FINANCIAL_DATA:
            fallback_entry = FALLBACK_FINANCIAL_DATA[stock_code]
            
            # Validate structure
            if not isinstance(fallback_entry, dict):
                logger.error(f"Fallback entry for {stock_code} is not a dict: {type(fallback_entry)}")
                return {}, [], "", ""
            
            required_keys = ["data", "quarters", "category", "industry"]
            for key in required_keys:
                if key not in fallback_entry:
                    logger.error(f"Missing key '{key}' in fallback data for {stock_code}")
                    return {}, [], "", ""
            
            data = fallback_entry["data"]
            quarters = fallback_entry["quarters"]
            category = fallback_entry["category"]
            industry = fallback_entry["industry"]
            
            # Validate data types
            if not isinstance(data, dict):
                logger.error(f"Data for {stock_code} is not a dict: {type(data)}")
                return {}, [], "", ""
            
            if not isinstance(quarters, list):
                logger.error(f"Quarters for {stock_code} is not a list: {type(quarters)}")
                return {}, [], "", ""
            
            logger.info(f"Using fallback data for {stock_code}: {len(data)} metrics, {len(quarters)} quarters")
            return data, quarters, category, industry
        else:
            logger.warning(f"No fallback data available for {stock_code}")
            return {}, [], "", ""
            
    except Exception as e:
        logger.error(f"Error in use_fallback_data for {stock_code}: {e}")
        return {}, [], "", ""

def serve_fallback_quarterly_view(stock: str):
    """Serve quarterly view using only fallback data (no database required)"""
    try:
        # Get fallback data
        data, quarters, category, industry = use_fallback_data(stock)
        
        if not data or not quarters:
            return f"""
            <div class="container mt-5">
                <div class="alert alert-warning">
                    <h4>⚠️ Database Not Available & No Fallback Data</h4>
                    <p>Unable to connect to database and no fallback data available for {stock}.</p>
                    <p><strong>Available stocks with fallback data:</strong> RELIANCE, TCS, ITC</p>
                    <div class="mt-3">
                        <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                        <a href="/quarterly/RELIANCE" class="btn btn-success">Try RELIANCE</a>
                        <a href="/quarterly/TCS" class="btn btn-info">Try TCS</a>
                        <a href="/quarterly/ITC" class="btn btn-secondary">Try ITC</a>
                    </div>
                </div>
            </div>
            """
        
        # Process fallback data for display
        categorized_data = {}
        
        # Ensure data is a dictionary
        if not isinstance(data, dict):
            logger.error(f"Fallback data for {stock} is not a dictionary: {type(data)}")
            return f"""
            <div class="container mt-5">
                <div class="alert alert-danger">
                    <h4>❌ Data Type Error for {stock}</h4>
                    <p>Expected dictionary but got {type(data).__name__}</p>
                    <p>Raw data: {str(data)[:200]}...</p>
                    <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                </div>
            </div>
            """
        
        for metric, values in data.items():
            # Categorize the metric
            metric_category = categorize_metric(metric)
            
            if metric_category not in categorized_data:
                categorized_data[metric_category] = {}
            
            # Ensure values is a list
            if not isinstance(values, list):
                logger.warning(f"Values for {metric} is not a list: {type(values)}")
                values = [str(values)]  # Convert to list if it's not
            
            # Clean values 
            cleaned_values = []
            for value in values:
                cleaned_value = clean_value(str(value))
                if cleaned_value:
                    cleaned_values.append(cleaned_value)
                else:
                    cleaned_values.append(str(value))  # Keep original if cleaning fails
            
            categorized_data[metric_category][metric] = cleaned_values
        
        # Add notice about fallback data
        fallback_notice = f"""
        <div class="alert alert-info mb-4">
            <h5>📊 Displaying Sample Data for {stock}</h5>
            <p><strong>Note:</strong> Database connection unavailable. Showing sample financial data for demonstration.</p>
            <p><strong>Data Source:</strong> Built-in fallback data | <strong>Category:</strong> {category} | <strong>Industry:</strong> {industry}</p>
        </div>
        """
        
        # Render the template with fallback data
        quarterly_html = render_template("quarterly.html",
                                       stock=stock,
                                       quarters=quarters,
                                       financial_data=json.dumps(categorized_data),
                                       metric_categories=categorized_data)
        
        # Inject the notice into the HTML
        if '<div class="container">' in quarterly_html:
            quarterly_html = quarterly_html.replace(
                '<div class="container">', 
                f'<div class="container">{fallback_notice}', 
                1
            )
        
        return quarterly_html
        
    except Exception as e:
        logger.error(f"Error in fallback quarterly view for {stock}: {e}")
        return f"""
        <div class="container mt-5">
            <div class="alert alert-danger">
                <h4>❌ Fallback Error for {stock}</h4>
                <p>Error: {str(e)}</p>
                <div class="mt-3">
                    <a href="/" class="btn btn-primary">← Back to Dashboard</a>
                </div>
            </div>
        </div>
        """

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
