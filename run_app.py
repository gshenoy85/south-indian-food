#!/usr/bin/env python3
"""
Simple script to run the stock recommender application
"""
import os
import sys
from stock_recommender import app

def main():
    """Main function to run the Flask application"""
    print("🚀 Starting Stock Recommender Application")
    print("="*50)
    
    # Check if environment variables are set
    required_vars = ["SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD", "SNOWFLAKE_ACCOUNT"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"⚠️  Missing environment variables: {', '.join(missing_vars)}")
        print("Please set these variables before running the application")
        return
    
    print("✅ Environment variables configured")
    print("🌐 Starting Flask application on http://localhost:5000")
    print("📊 Available endpoints:")
    print("  - /                    : Main dashboard")
    print("  - /quarterly/<stock>   : Quarterly view for stock")
    print("  - /sector/<sector>     : Sector comparison")
    print("  - /test-scraper/<stock> : Test web scraping")
    print("  - /test-full-flow/<stock> : Test complete flow")
    print("  - /debug/<stock>       : Debug database content")
    print("  - /load-single/<stock> : Load single stock data")
    print("="*50)
    
    # Run the Flask app
    try:
        app.run(debug=True, host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        print("\n👋 Application stopped by user")
    except Exception as e:
        print(f"❌ Error running application: {e}")

if __name__ == "__main__":
    main()