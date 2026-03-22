#!/usr/bin/env python3
"""
Test script for Tandem Browser integration with SharanBot
"""

import sys
import os

# Add sharanbot to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sharanbot.tools.tandem_browser import TandemBrowserTool, TandemBrowserError


def test_connection():
    """Test basic connection to Tandem Browser"""
    print("🔌 Testing connection to Tandem Browser...")
    
    try:
        browser = TandemBrowserTool()
        print("✅ Successfully connected to Tandem Browser")
        return browser
    except TandemBrowserError as e:
        print(f"❌ Connection failed: {e}")
        print("\nMake sure Tandem Browser is running:")
        print("  cd C:\\Users\\shara\\.sharanbot\\workspace\\tandem-browser")
        print("  npm start")
        return None


def test_navigation(browser):
    """Test navigation"""
    print("\n🌐 Testing navigation...")
    
    try:
        result = browser.navigate("https://example.com")
        print(f"✅ Navigation successful: {result}")
        return True
    except Exception as e:
        print(f"❌ Navigation failed: {e}")
        return False


def test_content_extraction(browser):
    """Test content extraction"""
    print("\n📄 Testing content extraction...")
    
    try:
        content = browser.get_page_content()
        print(f"✅ Title: {content.get('title')}")
        print(f"✅ URL: {content.get('url')}")
        print(f"✅ Text length: {len(content.get('text', ''))} chars")
        return True
    except Exception as e:
        print(f"❌ Content extraction failed: {e}")
        return False


def test_markdown(browser):
    """Test markdown conversion"""
    print("\n📝 Testing markdown conversion...")
    
    try:
        markdown = browser.get_page_markdown()
        print(f"✅ Markdown generated ({len(markdown)} chars)")
        print("\nPreview:")
        print("-" * 40)
        print(markdown[:300])
        print("-" * 40)
        return True
    except Exception as e:
        print(f"❌ Markdown conversion failed: {e}")
        return False


def test_screenshot(browser):
    """Test screenshot"""
    print("\n📸 Testing screenshot...")
    
    try:
        screenshot = browser.screenshot()
        print(f"✅ Screenshot taken ({len(screenshot)} chars base64)")
        
        # Save to file
        import base64
        output_path = "test_screenshot.png"
        with open(output_path, 'wb') as f:
            f.write(base64.b64decode(screenshot))
        print(f"✅ Screenshot saved to: {output_path}")
        return True
    except Exception as e:
        print(f"❌ Screenshot failed: {e}")
        return False


def test_links(browser):
    """Test link extraction"""
    print("\n🔗 Testing link extraction...")
    
    try:
        links = browser.get_links()
        print(f"✅ Found {len(links)} links")
        
        if links:
            print("\nFirst 3 links:")
            for link in links[:3]:
                print(f"  - {link.get('text', 'No text')}: {link.get('href', 'No URL')}")
        return True
    except Exception as e:
        print(f"❌ Link extraction failed: {e}")
        return False


def test_javascript(browser):
    """Test JavaScript execution"""
    print("\n⚡ Testing JavaScript execution...")
    
    try:
        result = browser.execute_javascript("document.title")
        print(f"✅ JavaScript executed, result: {result}")
        return True
    except Exception as e:
        print(f"❌ JavaScript execution failed: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("Tandem Browser Integration Test for SharanBot")
    print("=" * 60)
    
    # Test connection
    browser = test_connection()
    if not browser:
        print("\n❌ Cannot continue without Tandem Browser connection")
        sys.exit(1)
    
    # Run tests
    tests = [
        ("Navigation", test_navigation),
        ("Content Extraction", test_content_extraction),
        ("Markdown Conversion", test_markdown),
        ("Screenshot", test_screenshot),
        ("Link Extraction", test_links),
        ("JavaScript Execution", test_javascript),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func(browser)
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ {name} test crashed: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Tandem Browser is ready to use!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Check errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
