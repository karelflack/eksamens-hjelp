# Test infrastructure: percent-encode null bytes in httpx URLs before validation.
# httpx 0.27+ raises InvalidURL for literal \x00 in URL strings; the correct
# representation is %00.  This patch lets the ASGI test client receive those
# requests so security tests can assert on the response status code rather than
# catching a client-side exception.
#
# urlparse is imported by-value in httpx._urls, so we must patch that module's
# reference, not the source module httpx._urlparse.
import httpx._urls as _httpx_urls

if not getattr(_httpx_urls, "_null_byte_patch_applied", False):
    _original_urlparse = _httpx_urls.urlparse

    def _null_safe_urlparse(url="", **kwargs):
        if isinstance(url, str) and "\x00" in url:
            url = url.replace("\x00", "%00")
        return _original_urlparse(url, **kwargs)

    _httpx_urls.urlparse = _null_safe_urlparse
    _httpx_urls._null_byte_patch_applied = True
