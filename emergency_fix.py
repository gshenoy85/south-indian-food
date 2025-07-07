#!/usr/bin/env python3
"""
Emergency fix for quarterly data issues
This script will identify the problem and provide a working solution
"""
import os
import sys

def check_environment():
    """Check if environment variables are set"""
    print("🔍 Checking Environment Variables...")
    
    required_vars = ["SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_ACCOUNT"]
    missing_vars = []
    
    for var in required_vars:
        if os.getenv(var):
            print(f"  ✅ {var}: Set")
        else:
            print(f"  ❌ {var}: Missing")
            missing_vars.append(var)
    
    return len(missing_vars) == 0, missing_vars

def test_imports():
    """Test if all required imports work"""
    print("\n📦 Testing Imports...")
    
    try:
        import requests
        print("  ✅ requests: Available")
    except ImportError:
        print("  ❌ requests: Missing - run: pip install requests")
    
    try:
        from bs4 import BeautifulSoup
        print("  ✅ beautifulsoup4: Available")
    except ImportError:
        print("  ❌ beautifulsoup4: Missing - run: pip install beautifulsoup4")
    
    try:
        from flask import Flask
        print("  ✅ flask: Available")
    except ImportError:
        print("  ❌ flask: Missing - run: pip install flask")
    
    try:
        import snowflake.connector
        print("  ✅ snowflake-connector-python: Available")
    except ImportError:
        print("  ❌ snowflake-connector-python: Missing - run: pip install snowflake-connector-python")

def test_fallback_data():
    """Test if fallback data works"""
    print("\n🔄 Testing Fallback Data...")
    
    try:
        # Import the fallback data directly
        sys.path.append(os.path.dirname(os.path.abspath(__file__)))
        from stock_recommender import FALLBACK_FINANCIAL_DATA, use_fallback_data
        
        print(f"  ✅ Fallback data available for: {list(FALLBACK_FINANCIAL_DATA.keys())}")
        
        # Test RELIANCE fallback
        data, quarters, category, industry = use_fallback_data("RELIANCE")
        if data and quarters:
            print(f"  ✅ RELIANCE fallback: {len(data)} metrics, {len(quarters)} quarters")
            print(f"      Category: {category}, Industry: {industry}")
            print(f"      Sample metrics: {list(data.keys())[:3]}")
            return True
        else:
            print("  ❌ RELIANCE fallback: Failed")
            return False
            
    except Exception as e:
        print(f"  ❌ Fallback test failed: {e}")
        return False

def create_standalone_quarterly_app():
    """Create a standalone Flask app that only uses fallback data"""
    print("\n🚀 Creating Standalone Fallback App...")
    
    app_code = '''#!/usr/bin/env python3
"""
Standalone quarterly app using only fallback data (no database required)
"""
from flask import Flask, render_template
import json

app = Flask(__name__)

# Fallback financial data
FALLBACK_DATA = {
    "RELIANCE": {
        "data": {
            "Sales": ["215000", "218000", "220000", "225000"],
            "Net Profit": ["15000", "16000", "17000", "18000"],
            "Total Assets": ["750000", "765000", "780000", "795000"],
            "Equity": ["450000", "460000", "470000", "480000"],
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
            "Sales": ["55000", "58000", "60000", "62000"],
            "Net Profit": ["10500", "11200", "11800", "12400"],
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
            "Sales": ["65000", "68000", "70000", "72000"],
            "Net Profit": ["18000", "19000", "20000", "21000"],
            "ROE %": ["24.5", "25.1", "25.8", "26.2"],
            "ROCE %": ["26.5", "27.1", "27.8", "28.2"],
            "Current Ratio": ["1.8", "1.9", "2.0", "2.1"],
            "EPS": ["22.5", "23.8", "25.1", "26.4"]
        },
        "quarters": ["Mar 2023", "Jun 2023", "Sep 2023", "Dec 2023"],
        "category": "Large Cap",
        "industry": "FMCG"
    }
}

def categorize_metric(metric_name):
    """Simple metric categorization"""
    metric_lower = metric_name.lower()
    
    if any(word in metric_lower for word in ['sales', 'revenue', 'profit', 'income']):
        return "Income Statement"
    elif any(word in metric_lower for word in ['assets', 'equity', 'liabilities']):
        return "Balance Sheet"
    elif any(word in metric_lower for word in ['roe', 'roce', 'ratio']):
        return "Financial Ratios"
    elif any(word in metric_lower for word in ['eps', 'per share']):
        return "Per Share Data"
    else:
        return "Other Financial Metrics"

@app.route("/")
def index():
    return f\"\"\"
    <div style="font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px;">
        <h1>📊 Stock Financial Data (Fallback Mode)</h1>
        <div style="background: #e7f3ff; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <p><strong>Note:</strong> This is a standalone version using built-in sample data.</p>
            <p><strong>Available Stocks:</strong> RELIANCE, TCS, ITC</p>
        </div>
        <div style="display: flex; gap: 15px; flex-wrap: wrap;">
            <a href="/quarterly/RELIANCE" style="background: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">📈 RELIANCE Quarterly</a>
            <a href="/quarterly/TCS" style="background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">📈 TCS Quarterly</a>
            <a href="/quarterly/ITC" style="background: #ffc107; color: black; padding: 10px 20px; text-decoration: none; border-radius: 5px;">📈 ITC Quarterly</a>
        </div>
    </div>
    \"\"\"

@app.route("/quarterly/<stock>")
def quarterly_view(stock):
    stock = stock.upper()
    
    if stock not in FALLBACK_DATA:
        return f\"\"\"
        <div style="font-family: Arial, sans-serif; max-width: 800px; margin: 50px auto; padding: 20px;">
            <h2>❌ Stock {stock} not available</h2>
            <p>Available stocks: RELIANCE, TCS, ITC</p>
            <a href="/">← Back to Home</a>
        </div>
        \"\"\"
    
    stock_data = FALLBACK_DATA[stock]
    data = stock_data["data"]
    quarters = stock_data["quarters"]
    category = stock_data["category"]
    industry = stock_data["industry"]
    
    # Categorize metrics
    categorized_data = {}
    for metric, values in data.items():
        metric_category = categorize_metric(metric)
        if metric_category not in categorized_data:
            categorized_data[metric_category] = {}
        categorized_data[metric_category][metric] = values
    
    # Build HTML
    html = f\"\"\"
    <div style="font-family: Arial, sans-serif; max-width: 1200px; margin: 20px auto; padding: 20px;">
        <h1>📊 {stock} - Quarterly Financial Data</h1>
        
        <div style="background: #e7f3ff; padding: 15px; border-radius: 8px; margin: 20px 0;">
            <p><strong>Company:</strong> {stock} | <strong>Category:</strong> {category} | <strong>Industry:</strong> {industry}</p>
            <p><strong>Note:</strong> Sample data for demonstration</p>
        </div>
        
        <h3>📅 Quarters: {', '.join(quarters)}</h3>
    \"\"\"
    
    # Add categorized tables
    for cat_name, metrics in categorized_data.items():
        html += f\"\"\"
        <div style="margin: 30px 0;">
            <h3 style="background: #f8f9fa; padding: 10px; border-left: 4px solid #007bff;">{cat_name}</h3>
            <table style="width: 100%; border-collapse: collapse; margin: 10px 0;">
                <thead>
                    <tr style="background: #f8f9fa;">
                        <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">Metric</th>
        \"\"\"
        
        for quarter in quarters:
            html += f'<th style="padding: 10px; border: 1px solid #ddd; text-align: center;">{quarter}</th>'
        
        html += """
                    </tr>
                </thead>
                <tbody>
        """
        
        for metric, values in metrics.items():
            html += f'<tr><td style="padding: 10px; border: 1px solid #ddd; font-weight: bold;">{metric}</td>'
            for value in values:
                html += f'<td style="padding: 10px; border: 1px solid #ddd; text-align: center;">{value}</td>'
            html += '</tr>'
        
        html += """
                </tbody>
            </table>
        </div>
        """
    
    html += f\"\"\"
        <div style="margin: 30px 0;">
            <a href="/" style="background: #6c757d; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">← Back to Home</a>
        </div>
    </div>
    \"\"\"
    
    return html

if __name__ == "__main__":
    print("🚀 Starting Standalone Quarterly App...")
    print("📊 Available at: http://localhost:5001")
    print("🎯 Test URLs:")
    print("   - http://localhost:5001/quarterly/RELIANCE")
    print("   - http://localhost:5001/quarterly/TCS")
    print("   - http://localhost:5001/quarterly/ITC")
    app.run(debug=True, host='0.0.0.0', port=5001)
'''
    
    with open('standalone_quarterly_app.py', 'w') as f:
        f.write(app_code)
    
    print("  ✅ Created: standalone_quarterly_app.py")
    print("  🚀 Run with: python standalone_quarterly_app.py")
    print("  🌐 Access at: http://localhost:5001")

def main():
    """Main diagnostic function"""
    print("🏥 EMERGENCY QUARTERLY DATA FIX")
    print("=" * 50)
    
    # Check environment
    env_ok, missing_vars = check_environment()
    
    # Test imports
    test_imports()
    
    # Test fallback data
    fallback_ok = test_fallback_data()
    
    print("\n" + "=" * 50)
    print("📊 DIAGNOSIS SUMMARY")
    print("=" * 50)
    
    if not env_ok:
        print("❌ ISSUE: Missing Snowflake environment variables")
        print(f"   Missing: {', '.join(missing_vars)}")
        print("   This causes database connection failures")
    else:
        print("✅ Environment variables are set")
    
    if fallback_ok:
        print("✅ Fallback data system is working")
    else:
        print("❌ Fallback data system has issues")
    
    print("\n🎯 SOLUTIONS:")
    
    if not env_ok:
        print("1. 📋 Set environment variables:")
        for var in missing_vars:
            print(f"   export {var}='your_value_here'")
        print("   Then restart the application")
    
    if fallback_ok:
        print("2. 🚀 Use standalone app (works without database):")
        create_standalone_quarterly_app()
        print("   Run: python standalone_quarterly_app.py")
        print("   Visit: http://localhost:5001/quarterly/RELIANCE")
    
    print("3. 🔧 Run diagnostic on main app:")
    print("   Visit: http://localhost:5000/diagnostic")
    
    print("4. 🎯 Test specific URLs:")
    print("   http://localhost:5000/simple-quarterly/RELIANCE (guaranteed to work)")
    print("   http://localhost:5000/debug-fallback/RELIANCE (shows data structure)")
    print("   http://localhost:5000/quarterly/RELIANCE (complex version)")
    
    print("\n⚠️ SPECIFIC ERROR FIXES:")
    print("If you see 'str object has no attribute items' error:")
    print("   1. Visit: http://localhost:5000/debug-fallback/RELIANCE")
    print("   2. Check the data structure reported")
    print("   3. Use: http://localhost:5000/simple-quarterly/RELIANCE as working alternative")

if __name__ == "__main__":
    main()