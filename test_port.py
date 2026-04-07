#!/usr/bin/env python3
"""Test port extraction from various URLs."""

def test_port_extraction():
    test_cases = [
        ("http://localhost:15580", "15580"),
        ("http://localhost:15580/", "15580"),
        ("http://127.0.0.1:8080", "8080"),
        ("http://example.com:9999", "9999"),
        ("http://localhost:15580/path", "15580/path"),  # BUG: will include path
        ("http://localhost", "localhost"),  # BUG: no port
        ("https://localhost:443", "443"),
    ]
    
    print("Testing port extraction: displayer_url.rsplit(':', 1)[-1].strip('/')")
    print("=" * 70)
    
    for url, expected in test_cases:
        port = url.rsplit(":", 1)[-1].strip("/")
        status = "✓" if port == expected else "✗"
        print(f"{status} {url:40} -> {port:20} (expected: {expected})")


if __name__ == "__main__":
    test_port_extraction()
