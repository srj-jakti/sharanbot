#!/usr/bin/env python3
"""
Show OpenBuild MVP in Tandem Browser
"""

import sys
import base64
import os

# Fix unicode output on Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

sys.path.insert(0, r'C:\Users\shara\Downloads\SharanBot')

from sharanbot.tools.tandem_browser import TandemBrowserTool, TandemBrowserError

def main():
    print("Connecting to Tandem Browser...")
    
    try:
        # Create browser instance
        browser = TandemBrowserTool()
        print("Connected to Tandem Browser!")
        
        # Navigate to OpenBuild MVP
        print("\nNavigating to OpenBuild Network MVP...")
        result = browser.navigate("http://localhost:3000")
        print(f"Navigation result: {result}")
        
        # Wait for page to load
        print("Waiting for page to load...")
        browser.wait_for_load(timeout=5000)
        print("Page loaded!")
        
        # Get page info
        title = browser.get_title()
        url = browser.get_current_url()
        print(f"\nPage Title: {title}")
        print(f"URL: {url}")
        
        # Take screenshot
        print("\nTaking screenshot...")
        screenshot = browser.screenshot(full_page=True)
        
        # Save screenshot
        output_path = "openbuild_mvp_screenshot.png"
        with open(output_path, 'wb') as f:
            f.write(base64.b64decode(screenshot))
        print(f"Screenshot saved to: {output_path}")
        
        # Get page content
        print("\nExtracting page content...")
        content = browser.get_page_content()
        print(f"Title: {content.get('title')}")
        print(f"Text length: {len(content.get('text', ''))} characters")
        
        # Get links
        print("\nExtracting links...")
        links = browser.get_links()
        print(f"Found {len(links)} links on the page")
        
        print("\n" + "="*60)
        print("SUCCESS! OpenBuild MVP is now displayed in Tandem Browser!")
        print("="*60)
        print(f"\nYou can see it at: http://localhost:3000")
        print(f"Screenshot saved: {output_path}")
        
        return 0
        
    except TandemBrowserError as e:
        print(f"\nError: {e}")
        print("\nTo fix this:")
        print("1. Make sure Tandem Browser is running:")
        print("   cd C:\\Users\\shara\\.sharanbot\\workspace\\tandem-browser")
        print("   npm start")
        print("\n2. Wait for Tandem Browser window to fully load")
        print("\n3. Try again")
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
