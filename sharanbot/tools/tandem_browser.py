"""
Tandem Browser Tool for SharanBot
Provides secure, visual web browsing capabilities with human-in-the-loop controls.
"""

import os
import sys
import json
import base64
import requests
from pathlib import Path
from typing import Optional, Dict, List, Any, Literal
from dataclasses import dataclass
from urllib.parse import urljoin

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class TandemConfig:
    """Configuration for Tandem Browser connection"""
    base_url: str = "http://127.0.0.1:8765"
    token_path: Path = Path.home() / ".tandem" / "api-token"
    timeout: int = 30
    default_viewport: Dict[str, int] = None
    
    def __post_init__(self):
        if self.default_viewport is None:
            self.default_viewport = {"width": 1280, "height": 720}


class TandemBrowserError(Exception):
    """Custom exception for Tandem Browser errors"""
    pass


class TandemBrowserTool:
    """
    SharanBot tool for controlling Tandem Browser.
    
    This tool provides secure web browsing with:
    - Visual screenshots for verification
    - Persistent sessions (cookies, logins)
    - 8-layer security protection
    - Human-in-the-loop controls
    - Page content extraction as markdown
    
    Usage:
        browser = TandemBrowserTool()
        browser.navigate("https://example.com")
        screenshot = browser.screenshot()
        content = browser.get_page_markdown()
    """
    
    name = "tandem_browser"
    description = """
    Control Tandem Browser for secure, visual web browsing.
    
    Use this tool when you need to:
    - Visit websites and see visual results
    - Take screenshots of web pages
    - Extract content from websites
    - Fill forms and interact with pages
    - Perform multi-step web workflows
    - Test web applications visually
    
    The browser provides persistent sessions, security protection,
    and human-in-the-loop controls.
    """
    
    def __init__(self, config: Optional[TandemConfig] = None):
        """Initialize Tandem Browser tool"""
        self.config = config or TandemConfig()
        self.token = self._load_token()
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "SharanBot-TandemBrowser/1.0"
        })
        self._last_screenshot: Optional[str] = None
        self._last_url: Optional[str] = None
        
    def _load_token(self) -> str:
        """Load authentication token from ~/.tandem/api-token"""
        try:
            if self.config.token_path.exists():
                token = self.config.token_path.read_text().strip()
                if len(token) >= 32:
                    return token
        except Exception as e:
            pass
        
        # Try environment variable as fallback
        token = os.environ.get("TANDEM_API_TOKEN")
        if token:
            return token
            
        raise TandemBrowserError(
            "Tandem Browser auth token not found. "
            "Please ensure Tandem Browser is running. "
            "Token should be at: ~/.tandem/api-token"
        )
    
    def _request(
        self, 
        method: Literal["GET", "POST", "PUT", "DELETE"], 
        endpoint: str, 
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        timeout: Optional[int] = None
    ) -> Any:
        """Make authenticated request to Tandem API"""
        url = urljoin(self.config.base_url, endpoint)
        timeout = timeout or self.config.timeout
        
        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=timeout)
            elif method == "POST":
                response = self.session.post(url, json=data, timeout=timeout)
            elif method == "PUT":
                response = self.session.put(url, json=data, timeout=timeout)
            elif method == "DELETE":
                response = self.session.delete(url, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            
            # Handle different response types
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return response.json()
            else:
                return response.text
                
        except requests.exceptions.ConnectionError:
            raise TandemBrowserError(
                f"Cannot connect to Tandem Browser at {self.config.base_url}. "
                "Please ensure Tandem Browser is running."
            )
        except requests.exceptions.Timeout:
            raise TandemBrowserError(
                f"Request to Tandem Browser timed out after {timeout}s"
            )
        except requests.exceptions.HTTPError as e:
            raise TandemBrowserError(
                f"Tandem Browser API error: {e.response.status_code} - {e.response.text}"
            )
        except Exception as e:
            raise TandemBrowserError(f"Tandem Browser request failed: {str(e)}")
    
    # ═══════════════════════════════════════════════════════
    # NAVIGATION
    # ═══════════════════════════════════════════════════════
    
    def navigate(self, url: str, tab_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Navigate to a URL in Tandem Browser.
        
        Args:
            url: The URL to navigate to (must include protocol, e.g., https://)
            tab_id: Optional specific tab ID to use (creates new tab if not provided)
            
        Returns:
            Dict with navigation result including 'ok' and 'url'
            
        Raises:
            TandemBrowserError: If navigation fails
            
        Example:
            >>> browser.navigate("https://github.com")
            {'ok': True, 'url': 'https://github.com'}
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            
        result = self._request("POST", "/navigate", {
            "url": url,
            "tabId": tab_id
        })
        
        self._last_url = url
        return result
    
    def go_back(self) -> Dict[str, Any]:
        """Navigate back in browser history"""
        return self.execute_javascript("window.history.back()")
    
    def go_forward(self) -> Dict[str, Any]:
        """Navigate forward in browser history"""
        return self.execute_javascript("window.history.forward()")
    
    def reload(self, ignore_cache: bool = False) -> Dict[str, Any]:
        """Reload the current page"""
        if ignore_cache:
            return self.execute_javascript("window.location.reload(true)")
        return self.execute_javascript("window.location.reload()")
    
    # ═══════════════════════════════════════════════════════
    # CONTENT EXTRACTION
    # ═══════════════════════════════════════════════════════
    
    def get_page_content(
        self, 
        settle_ms: int = 800, 
        timeout: int = 10000,
        min_length: int = 1000
    ) -> Dict[str, Any]:
        """
        Extract page content as structured data.
        
        Args:
            settle_ms: Time to wait for page to settle before extraction
            timeout: Maximum time to wait for content
            min_length: Minimum content length to consider successful
            
        Returns:
            Dict with:
            - title: Page title
            - url: Current URL
            - description: Meta description
            - text: Body text content
            - length: Character count
            
        Example:
            >>> content = browser.get_page_content()
            >>> print(content['title'])
            'GitHub: Let's build from here'
        """
        params = {
            "settle": settle_ms,
            "timeout": timeout,
            "minLength": min_length
        }
        return self._request("GET", "/page-content", params=params)
    
    def get_page_markdown(self) -> str:
        """
        Get page content formatted as clean markdown.
        
        Returns:
            Markdown formatted string with title, URL, and content
            
        Example:
            >>> md = browser.get_page_markdown()
            >>> print(md[:200])
            '# GitHub: Let's build from here\n\n**URL:** https://github.com\n\n...'
        """
        content = self.get_page_content()
        
        lines = []
        lines.append(f"# {content.get('title', 'Untitled')}")
        lines.append("")
        lines.append(f"**URL:** {content.get('url', '')}")
        lines.append("")
        
        if content.get('description'):
            lines.append(f"> {content['description']}")
            lines.append("")
        
        text = content.get('text', '')
        if text:
            lines.append(text)
        
        return "\n".join(lines)
    
    def get_page_text(self) -> str:
        """Get just the page text content without formatting"""
        content = self.get_page_content()
        return content.get('text', '')
    
    def get_links(self) -> List[Dict[str, Any]]:
        """
        Get all links on the current page.
        
        Returns:
            List of link objects with:
            - text: Link text
            - href: URL
            - visible: Whether element is visible
            
        Example:
            >>> links = browser.get_links()
            >>> for link in links[:5]:
            ...     print(f"{link['text']}: {link['href']}")
        """
        result = self._request("GET", "/links")
        return result.get("links", [])
    
    def get_forms(self) -> List[Dict[str, Any]]:
        """Get all forms on the current page"""
        result = self._request("GET", "/forms")
        return result.get("forms", [])
    
    # ═══════════════════════════════════════════════════════
    # SCREENSHOTS
    # ═══════════════════════════════════════════════════════
    
    def screenshot(
        self, 
        full_page: bool = False, 
        selector: Optional[str] = None,
        quality: int = 90
    ) -> str:
        """
        Take a screenshot of the current page.
        
        Args:
            full_page: Capture full page or just viewport
            selector: Optional CSS selector to screenshot specific element
            quality: JPEG quality (1-100), PNG if not specified
            
        Returns:
            Base64 encoded image string
            
        Example:
            >>> img_base64 = browser.screenshot(full_page=True)
            >>> # Save to file
            >>> import base64
            >>> with open('screenshot.png', 'wb') as f:
            ...     f.write(base64.b64decode(img_base64))
        """
        params = {
            "fullPage": str(full_page).lower(),
            "quality": quality
        }
        if selector:
            params["selector"] = selector
            
        base64_img = self._request("GET", "/screenshot", params=params)
        self._last_screenshot = base64_img
        return base64_img
    
    def save_screenshot(
        self, 
        filepath: str, 
        full_page: bool = False,
        selector: Optional[str] = None
    ) -> str:
        """
        Take a screenshot and save to file.
        
        Args:
            filepath: Path to save screenshot (should end in .png or .jpg)
            full_page: Capture full page
            selector: Optional CSS selector for element
            
        Returns:
            Absolute path to saved file
            
        Example:
            >>> path = browser.save_screenshot("/tmp/page.png", full_page=True)
            >>> print(f"Saved to: {path}")
        """
        base64_img = self.screenshot(full_page=full_page, selector=selector)
        
        # Determine format from extension
        ext = Path(filepath).suffix.lower()
        if ext == '.jpg' or ext == '.jpeg':
            # Remove data URI prefix if present
            if ',' in base64_img:
                base64_img = base64_img.split(',')[1]
        
        img_data = base64.b64decode(base64_img)
        
        # Create directory if needed
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        
        with open(filepath, 'wb') as f:
            f.write(img_data)
        
        return str(Path(filepath).absolute())
    
    # ═══════════════════════════════════════════════════════
    # INTERACTION
    # ═══════════════════════════════════════════════════════
    
    def click(self, selector: str) -> Dict[str, Any]:
        """
        Click an element on the page.
        
        Args:
            selector: CSS selector of element to click
            
        Returns:
            Dict with click result
            
        Example:
            >>> browser.click("button.submit")
            >>> browser.click("a[href='/login']")
        """
        return self._request("POST", "/click", {"selector": selector})
    
    def type_text(
        self, 
        selector: str, 
        text: str, 
        humanize: bool = True,
        delay: int = 20
    ) -> Dict[str, Any]:
        """
        Type text into an input field.
        
        Args:
            selector: CSS selector of input element
            text: Text to type
            humanize: Add realistic typing delays (default: True)
            delay: Delay between keystrokes in ms (default: 20)
            
        Returns:
            Dict with type result
            
        Example:
            >>> browser.type_text("input[name='search']", "openbuild network")
            >>> browser.type_text("#username", "myuser", humanize=True)
        """
        return self._request("POST", "/type", {
            "selector": selector,
            "text": text,
            "humanize": humanize,
            "delay": delay
        })
    
    def clear_input(self, selector: str) -> Dict[str, Any]:
        """Clear an input field"""
        return self.execute_javascript(f"document.querySelector('{selector}').value = ''")
    
    def select_option(self, selector: str, value: str) -> Dict[str, Any]:
        """Select an option from a dropdown"""
        code = f"""
            const select = document.querySelector('{selector}');
            select.value = '{value}';
            select.dispatchEvent(new Event('change'));
        """
        return self.execute_javascript(code)
    
    def scroll_to(self, x: int, y: int) -> Dict[str, Any]:
        """Scroll to specific coordinates"""
        return self.execute_javascript(f"window.scrollTo({x}, {y})")
    
    def scroll_to_element(self, selector: str) -> Dict[str, Any]:
        """Scroll element into view"""
        code = f"""
            const el = document.querySelector('{selector}');
            if (el) el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
        """
        return self.execute_javascript(code)
    
    # ═══════════════════════════════════════════════════════
    # JAVASCRIPT EXECUTION
    # ═══════════════════════════════════════════════════════
    
    def execute_javascript(self, code: str) -> Any:
        """
        Execute JavaScript in the browser context.
        
        Args:
            code: JavaScript code to execute
            
        Returns:
            Result of the JavaScript execution
            
        Example:
            >>> result = browser.execute_javascript("document.title")
            >>> print(result)
            'GitHub'
        """
        return self._request("POST", "/execute-js", {"code": code})
    
    def evaluate(self, expression: str) -> Any:
        """Alias for execute_javascript"""
        return self.execute_javascript(expression)
    
    # ═══════════════════════════════════════════════════════
    # TAB MANAGEMENT
    # ═══════════════════════════════════════════════════════
    
    def list_tabs(self) -> List[Dict[str, Any]]:
        """
        List all open browser tabs.
        
        Returns:
            List of tab objects with id, title, url, etc.
        """
        return self._request("GET", "/tabs")
    
    def new_tab(self, url: Optional[str] = None, active: bool = True) -> Dict[str, Any]:
        """
        Open a new browser tab.
        
        Args:
            url: Optional URL to navigate to
            active: Whether to focus the new tab
        """
        return self._request("POST", "/tabs", {"url": url, "active": active})
    
    def close_tab(self, tab_id: str) -> Dict[str, Any]:
        """Close a specific tab"""
        return self._request("DELETE", f"/tabs/{tab_id}")
    
    def focus_tab(self, tab_id: str) -> Dict[str, Any]:
        """Focus a specific tab"""
        return self._request("POST", f"/tabs/{tab_id}/focus")
    
    def get_active_tab(self) -> Optional[Dict[str, Any]]:
        """Get the currently active tab"""
        tabs = self.list_tabs()
        for tab in tabs:
            if tab.get("active"):
                return tab
        return tabs[0] if tabs else None
    
    # ═══════════════════════════════════════════════════════
    # WAIT CONDITIONS
    # ═══════════════════════════════════════════════════════
    
    def wait_for_load(self, timeout: int = 10000) -> Dict[str, Any]:
        """
        Wait for the page to finish loading.
        
        Args:
            timeout: Maximum wait time in milliseconds
        """
        return self._request("POST", "/wait", {"timeout": timeout})
    
    def wait_for_element(self, selector: str, timeout: int = 10000) -> Dict[str, Any]:
        """Wait for an element to appear on the page"""
        return self._request("POST", "/wait-for-element", {
            "selector": selector,
            "timeout": timeout
        })
    
    def wait_for_text(self, text: str, timeout: int = 10000) -> Dict[str, Any]:
        """Wait for specific text to appear on the page"""
        return self._request("POST", "/wait-for-text", {
            "text": text,
            "timeout": timeout
        })
    
    def sleep(self, milliseconds: int) -> None:
        """Pause execution for specified milliseconds"""
        import time
        time.sleep(milliseconds / 1000)
    
    # ═══════════════════════════════════════════════════════
    # UTILITY
    # ═══════════════════════════════════════════════════════
    
    def get_current_url(self) -> str:
        """Get the current page URL"""
        result = self.execute_javascript("window.location.href")
        return result if isinstance(result, str) else self._last_url or ""
    
    def get_title(self) -> str:
        """Get the current page title"""
        result = self.execute_javascript("document.title")
        return result if isinstance(result, str) else ""
    
    def find_on_page(self, text: str) -> bool:
        """Check if text exists on the page"""
        page_text = self.get_page_text()
        return text.lower() in page_text.lower()
    
    def is_element_present(self, selector: str) -> bool:
        """Check if an element exists on the page"""
        result = self.execute_javascript(f"!!document.querySelector('{selector}')")
        return bool(result)
    
    # ═══════════════════════════════════════════════════════
    # HIGH-LEVEL WORKFLOWS
    # ═══════════════════════════════════════════════════════
    
    def research_page(self, url: str) -> Dict[str, Any]:
        """
        Comprehensive page research workflow.
        
        Navigates to URL, takes screenshot, and extracts content.
        
        Returns:
            Dict with url, title, screenshot, markdown, and links
        """
        self.navigate(url)
        self.wait_for_load()
        
        return {
            "url": self.get_current_url(),
            "title": self.get_title(),
            "screenshot": self.screenshot(),
            "markdown": self.get_page_markdown(),
            "links": self.get_links()[:20]  # First 20 links
        }
    
    def fill_form(self, form_data: Dict[str, str]) -> None:
        """
        Fill a form with multiple fields.
        
        Args:
            form_data: Dict mapping CSS selectors to values
            
        Example:
            >>> browser.fill_form({
            ...     "input[name='username']": "myuser",
            ...     "input[name='password']": "mypass",
            ...     "input[name='email']": "user@example.com"
            ... })
        """
        for selector, value in form_data.items():
            self.type_text(selector, value)
    
    def smart_search(self, query: str, search_selector: str = "input[type='search'], input[name='q'], input[name='search']") -> None:
        """
        Perform a search on the current page.
        
        Finds the search input, types query, and submits.
        """
        # Try common search selectors
        selectors = [
            "input[type='search']",
            "input[name='q']",
            "input[name='search']",
            "input[placeholder*='search' i]",
            "input[placeholder*=' Search' i]",
            "#search",
            ".search-input"
        ]
        
        for selector in selectors:
            if self.is_element_present(selector):
                self.type_text(selector, query)
                # Try to submit (Enter key or search button)
                self.execute_javascript(f"""
                    const input = document.querySelector('{selector}');
                    const form = input.closest('form');
                    if (form) form.submit();
                    else input.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter'}}));
                """)
                return
        
        raise TandemBrowserError("Could not find search input on page")


# ═══════════════════════════════════════════════════════
# SHARANBOT INTEGRATION
# ═══════════════════════════════════════════════════════

def get_tool():
    """Factory function for SharanBot tool registration"""
    return TandemBrowserTool()


# Example usage for testing
if __name__ == "__main__":
    # Test the tool
    try:
        browser = TandemBrowserTool()
        print("✅ Tandem Browser connected")
        
        # Test navigation
        print("\n🌐 Navigating to example.com...")
        result = browser.navigate("https://example.com")
        print(f"Navigation result: {result}")
        
        # Test content extraction
        print("\n📄 Getting page content...")
        content = browser.get_page_content()
        print(f"Title: {content.get('title')}")
        print(f"Text length: {len(content.get('text', ''))}")
        
        # Test screenshot
        print("\n📸 Taking screenshot...")
        screenshot = browser.screenshot()
        print(f"Screenshot size: {len(screenshot)} chars")
        
        print("\n✅ All tests passed!")
        
    except TandemBrowserError as e:
        print(f"❌ Error: {e}")
        print("\nMake sure Tandem Browser is running:")
        print("  cd /path/to/tandem-browser")
        print("  npm start")
