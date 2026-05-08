import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    import uvicorn
    print("SUCCESS: uvicorn imported")

    # Try to import the app
    import main
    print("SUCCESS: main imported")

    # Check if app has the expected attributes
    print(f"main.app: {main.app}")
    print(f"main.app.title: {getattr(main.app, 'title', 'no title')}")

    # Try to start uvicorn programmatically (but don't actually start the server)
    print("SUCCESS: ready to start uvicorn")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
