import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    from db import get_connection
    print("SUCCESS: db module imported successfully")

    # Try to get a connection
    conn = get_connection()
    print("SUCCESS: database connection established")

    # Test a simple query
    cur = conn.cursor()
    cur.execute("SELECT 1")
    result = cur.fetchone()
    print(f"SUCCESS: test query result: {result}")

    cur.close()
    conn.close()
    print("SUCCESS: database connection closed")

except Exception as e:
    print(f"ERROR with database: {e}")
    import traceback
    traceback.print_exc()
