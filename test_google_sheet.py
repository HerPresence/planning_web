import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CREDS_FILE = r"T:\planning_web\google_credentials.json"

credentials = Credentials.from_service_account_file(
    CREDS_FILE,
    scopes=SCOPES,
)

client = gspread.authorize(credentials)

sheet = client.open("articles_costs")

worksheet = sheet.sheet1

data = worksheet.get_all_records()

print("GOOGLE SHEET CONNECTED")
print(f"ROWS: {len(data)}")

if len(data) > 0:
    print(data[0])