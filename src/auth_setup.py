import logging
from pathlib import Path
import sys

# Configure basic logging to see output in the terminal
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger('uvicorn.error')

# Add the src directory to path so we can import our modules
src_path = Path(__file__).resolve().parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from stores.gmail.GmailService import GmailService
from stores.calendar.CalendarService import CalendarService

def setup_oauth():
    print("==================================================")
    print("       Google Services OAuth Setup Utility        ")
    print("==================================================")
    print("\nThis script will open your web browser to authenticate with Google.")
    print("Make sure you have your credentials.json file in the correct directory.\n")
    
    # 1. Setup Gmail OAuth
    print("\n[1/2] Setting up Gmail Authentication...")
    gmail_service = GmailService()
    # allow_interactive=True forces the browser to open if refresh fails or token is missing
    gmail_success = gmail_service.authenticate(allow_interactive=True)
    if gmail_success:
        print("✅ Gmail authentication successful!")
    else:
        print("❌ Gmail authentication failed. Check your credentials.json.")

    # 2. Setup Calendar OAuth
    print("\n[2/2] Setting up Google Calendar Authentication...")
    calendar_service = CalendarService()
    calendar_success = calendar_service.authenticate(allow_interactive=True)
    if calendar_success:
        print("✅ Google Calendar authentication successful!")
    else:
        print("❌ Google Calendar authentication failed.")

    print("\n==================================================")
    if gmail_success and calendar_success:
        print("🎉 All services authenticated successfully!")
        print("You can now start your FastAPI server.")
    else:
        print("⚠️ Some authentications failed. Please review the errors above.")
    print("==================================================")

if __name__ == "__main__":
    setup_oauth()