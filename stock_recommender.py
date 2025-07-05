
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
load_dotenv()

# ------------------- Configuration -------------------
SCREENER_URL = "https://www.screener.in/company/{}/consolidated/"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Cookie": os.getenv("SCREENER_COOKIE")  # Set this in your environment
}
STOCKS = {
    "Large Cap": ["RELIANCE", "TCS", "ITC"],
    "Mid Cap": ["PIDILITIND", "CUMMINSIND"],
    "Small Cap": ["HATSUN", "BALAMINES"]
}

# ------------------- To Cateogrise Metrics -------------------
METRIC_CATEGORIES = {
    "Income Statement": [
        "Sales", "Operating Profit", "Net Profit +", "Other Income +", "EBITDA", "Interest", "Tax %"
    ],
    "Balance Sheet": [
        "Total Assets", "Total Liabilities", "Reserves", "Equity Capital", "Investments","Other Assets +"
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
    print("🔄 Loading all data")
    create_snowflake_table()
    conn = snowflake_connect()
    for category, stocks in STOCKS.items():
        for stock in stocks:
            data, quarters, category, industry = get_financial_data(stock)
            insert_quarterly_to_snowflake(conn, stock, data, quarters, category, industry)


# ------------------- Flask App -------------------
app = Flask(__name__)

@app.route("/quarterly/<stock>")
def quarterly_view(stock):
    conn = snowflake_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT METRIC, QUARTER, VALUE
        FROM FINANCIALS_QUARTERLY
        WHERE STOCK_CODE=%s
        ORDER BY QUARTER
    """, (stock,))
    rows = cur.fetchall()

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

    return render_template("quarterly.html",
                           stock=stock,
                           quarters=quarters,
                           financial_data=json.dumps(formatted),
                           metric_categories=METRIC_CATEGORIES)

@app.route("/sector/<sector>")
def sector_view(sector):
    conn = snowflake_connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT STOCK_CODE, METRIC, QUARTER, VALUE
        FROM FINANCIALS_QUARTERLY
        WHERE CATEGORY=%s
    """, (sector,))
    rows = cur.fetchall()

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

    return render_template("sector.html",
                           sector=sector,
                           quarters=quarters,
                           financial_data=json.dumps(formatted_data))


@app.route("/")
def index():
   return render_template("index.html", categories=STOCKS)

@app.route("/visualize", methods=["POST"])
def visualize():
    stock = request.form['stock']
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

    return render_template("visualize.html",
                        stock=stock,
                        years=years,
                        financial_data=json.dumps(financial_data),
                        metric_categories=METRIC_CATEGORIES) 



# ------------------- Screener Scraper -------------------
import re

def clean_value(val):
    val = val.strip().replace(",", "").replace("+", "").replace("\xa0", "")  # Remove commas, plus signs, etc.

    if val.endswith("%"):
        try:
            return str(float(val.strip('%')) / 100)
        except:
            return ""
    
    # Handle numbers wrapped in parentheses for negatives: (123) => -123
    if val.startswith("(") and val.endswith(")"):
        val = "-" + val[1:-1]

    # Allow values like -123.45 or 678.9
    if re.match(r"^-?\d+(\.\d+)?$", val):
        return val

    return ""


def get_financial_data(stock_code):
    url = f"https://www.screener.in/company/{stock_code}/consolidated/"
    print(f"🔎 Fetching {stock_code} from: {url}")
    res = requests.get(url, headers=HEADERS)
    if res.status_code != 200:
        print(f"❌ Failed to fetch {stock_code}")
        return {}, [], "", ""

    soup = BeautifulSoup(res.content, "html.parser")

    # Extract industry and sector/category info
    try:
        breadcrumb = soup.select_one(".breadcrumb").text.strip()
        category = breadcrumb.split('›')[1].strip()
        industry = breadcrumb.split('›')[2].strip()
    except:
        category, industry = "", ""

    # Extract quarterly data
    quarterly_table = soup.find("section", id="quarters")
    if not quarterly_table:
        print(f"⚠️ Quarterly data not found for {stock_code}")
        return {}, [], category, industry

    header_cells = quarterly_table.select("thead tr th")[1:]  # skip first col
    quarters = [th.text.strip() for th in header_cells]

    data = {}
    for row in quarterly_table.select("tbody tr"):
        cols = row.find_all("td")
        if len(cols) < len(quarters) + 1:
            continue
        metric = cols[0].text.strip()
        values = [clean_value(td.text) for td in cols[1:len(quarters)+1]]
        data[metric] = values

    return data, quarters, category, industry


# ------------------- Snowflake Integration -------------------
def snowflake_connect():
    print("Debugging Environment Variables for Snowflake Connection:")
    print("SNOWFLAKE_USER:", os.getenv("SNOWFLAKE_USER"))
    print("SNOWFLAKE_PASSWORD:", "********" if os.getenv("SNOWFLAKE_PASSWORD") else None)
    print("SNOWFLAKE_ACCOUNT:", os.getenv("SNOWFLAKE_ACCOUNT"))

    return snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse='SNOWFLAKE_LEARNING_WH',
        database='STOCK_DB',
        schema='STOCK_SOURCE'
    )

def create_snowflake_table():
    conn = snowflake_connect()
    print ("I am creating table")
    cur = conn.cursor()
    print ("Created Cursor")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS FINANCIALS_QUARTERLY (
    STOCK_CODE STRING,
    METRIC STRING,
    QUARTER STRING,
    VALUE STRING,
    INDUSTRY STRING,
    CATEGORY STRING
    )
    """)
    print ("executed cursor")
    conn.commit()
   


def is_valid(values):
    return all(re.match(r"^-?\d+(\.\d+)?%?$", v) for v in values)

def insert_quarterly_to_snowflake(conn, stock_code, financials, quarters, category, industry):
    cur = conn.cursor()
    for metric, values in financials.items():
        for i in range(len(quarters)):
            quarter = quarters[i]
            value = values[i] if i < len(values) else None

            if value:
                cur.execute("""
                MERGE INTO FINANCIALS_QUARTERLY AS tgt
                USING (SELECT %s AS STOCK_CODE, %s AS METRIC, %s AS QUARTER) AS src
                ON tgt.STOCK_CODE = src.STOCK_CODE AND tgt.METRIC = src.METRIC AND tgt.QUARTER = src.QUARTER
                WHEN MATCHED THEN UPDATE SET VALUE = %s, INDUSTRY = %s, CATEGORY = %s
                WHEN NOT MATCHED THEN INSERT (
                    STOCK_CODE, METRIC, QUARTER, VALUE, INDUSTRY, CATEGORY
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """, (stock_code, metric, quarter, value, industry, category,
                      stock_code, metric, quarter, value, industry, category))
    conn.commit()


# ------------------- Main -------------------
if __name__ == '__main__':
    #load_all_data()  # <-- call this before Flask runs
    app.run(debug=True)
