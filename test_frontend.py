import asyncio
from playwright.async_api import async_playwright
import uuid
import time
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

async def run_tests():
    async with async_playwright() as p:
        # Use headless mode for CI, but we want to simulate a real browser
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("\n--- Test 12: Auto-Redirect to Google OAuth ---")
        try:
            # Go to localhost
            print("1. Opening http://localhost:5173/auth")
            await page.goto("http://localhost:5173/auth")
            
            # Switch to Sign Up
            await page.get_by_text("Sign Up").click()
            await page.wait_for_timeout(500)

            email = f"playwright_{uuid.uuid4().hex[:8]}@example.com"
            print(f"2. Signing up with {email}")
            
            # Fill the form
            await page.locator("input[placeholder='Full Name']").fill("Test User")
            await page.locator("input[placeholder='Email address']").fill(email)
            await page.locator("input[placeholder='Password']").fill("StrongPass123!")
            await page.locator("input[placeholder='Confirm Password']").fill("StrongPass123!")
            
            # Click submit
            print("3. Submitting form...")
            await page.locator("button", has_text="Create Account").click()

            # Wait for the URL to change to Google's OAuth
            print("4. Waiting for redirect...")
            await page.wait_for_url("**/accounts.google.com/**", timeout=10000)
            print(f"Success! Redirected to: {page.url[:100]}...")
            
            # This confirms Test 11 (localhost domain logic) and Test 12 (Auto Redirect)
            print("Test 12: PASSED. Auto-redirect works flawlessly.")
        except Exception as e:
            print(f"Test 12 Failed: {e}")

        # Test 13: Manual Workspace Button
        print("\n--- Test 13: Manual Connect Button in Workspace ---")
        try:
            # We need to sign in normally (bypassing Google for a second) to reach Workspace
            # Wait, if we are at Google, we can't just bypass. 
            # We can create a new context, use the backend API to create a user and get a token,
            # then set the token in localStorage and navigate to /workspace directly.
            print("1. Creating a new session directly to /workspace")
            context2 = await browser.new_context()
            page2 = await context2.new_page()
            
            # Go to auth just to set origin
            await page2.goto("http://localhost:5173/auth")
            
            # Register user via API to get token
            import requests
            email2 = f"playwright_manual_{uuid.uuid4().hex[:8]}@example.com"
            res = requests.post("http://localhost:5000/api/v1/auth/local/signup", 
                                json={"name": "Manual", "email": email2, "password": "StrongPass123!"})
            token = res.json()["access_token"]
            
            # Set token in localStorage
            await page2.evaluate(f"localStorage.setItem('jwt_token', '{token}')")
            
            # Navigate to workspace
            print("2. Navigating to http://localhost:5173/workspace")
            await page2.goto("http://localhost:5173/workspace")
            await page2.wait_for_selector("text=Connect Google", timeout=5000)
            
            # Click Connect Google
            print("3. Clicking 'Connect Google' button...")
            await page2.get_by_text("Connect Google").click()
            
            print("4. Waiting for Google OAuth popup/redirect...")
            await page2.wait_for_url("**/accounts.google.com/**", timeout=5000)
            print(f"Success! Manual button redirected to: {page2.url[:100]}...")
            print("Test 13: PASSED. Manual connect button works flawlessly.")
            
        except Exception as e:
            print(f"Test 13 Failed: {e}")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_tests())
