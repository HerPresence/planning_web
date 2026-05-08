import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

try:
    import main
    print("SUCCESS: main imported successfully")
    print(f"main.app type: {type(main.app)}")
    print(f"main.app title: {getattr(main.app, 'title', 'no title')}")
except Exception as e:
    print(f"ERROR importing main: {e}")
    import traceback
    traceback.print_exc()
