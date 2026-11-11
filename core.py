"""Scraping engine: URL safety, HTML fetch, SEO/tech analysis, offline ZIP
packaging and full-site crawling. Pure synchronous code (runs in job threads).
"""

import ipaddress
import os
import posixpath
import re
import socket
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

import config

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}


class UnsafeURL(ValueError):
    """URL points to a local / non-public destination or is malformed."""


class ResponseTooLarge(ValueError):
    """Fetched page or asset exceeds the safety limit."""


# ═══════════════════════ URL SAFETY ═══════════════════════
URL_PATTERN = re.compile(
    r"^https?://"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}"
    r"(?::\d{1,5})?"
    r"(?:/[^\s]*)?$"
)


def is_valid_url(text: str) -> bool:
    if not text:
        return False
    text = text.strip()
    if " " in text or "\n" in text or not URL_PATTERN.match(text):
        return False
    try:
        parsed = urlparse(text)
        return bool(parsed.scheme and parsed.netloc and "." in parsed.netloc)
    except Exception:
        return False


def _validated_public_url(url: str) -> str:
    """Allow only public HTTP(S) hosts; reject credentials and private IPs."""
    try:
        parsed = urlparse(str(url).strip())
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https") or not parsed.hostname:
            raise UnsafeURL("Only public HTTP/HTTPS URLs are supported.")
        if parsed.username or parsed.password:
            raise UnsafeURL("URLs containing embedded credentials are not allowed.")
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        default_port = 443 if scheme == "https" else 80
        port = parsed.port or default_port
        if port != default_port:
            raise UnsafeURL("Only standard HTTP/HTTPS ports are supported.")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".localhost", ".local", ".internal", ".lan")
        ):
            raise UnsafeURL("Local and private network hosts are not allowed.")
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = [
                ipaddress.ip_address(rec[4][0].split("%", 1)[0]) for rec in records
            ]
        if not addresses or any(not a.is_global for a in addresses):
            raise UnsafeURL("Only publicly routable hosts are allowed.")
        netloc = f"[{host}]" if ":" in host else host
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((scheme, netloc, parsed.path or "/", parsed.params, parsed.query, ""))
    except UnsafeURL:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise UnsafeURL("That URL is invalid or could not be resolved safely.") from exc


def safe_get(url, headers=None, timeout=20, max_bytes=None):
    """Fetch a public URL, validating every redirect and limiting body size."""
    max_bytes = max_bytes or config.MAX_PAGE_RESPONSE_BYTES
    session = requests.Session()
    session.trust_env = False
    current = url
    try:
        for _ in range(config.MAX_SAFE_REDIRECTS + 1):
            current = _validated_public_url(current)
            response = session.get(
                current, headers=headers or HEADERS, timeout=timeout,
                allow_redirects=False, stream=True,
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise requests.RequestException("Incomplete redirect response.")
                current = urljoin(current, location)
                continue
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                response.close()
                raise ResponseTooLarge("The page or asset exceeds the safe download limit.")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > max_bytes:
                    response.close()
                    raise ResponseTooLarge("The page or asset exceeds the safe download limit.")
            response._content = bytes(body)
            response._content_consumed = True
            response.url = current
            response.close()
            return response
        raise requests.TooManyRedirects("The site redirected too many times.")
    finally:
        session.close()


def detect_blocked_page(html: str, status_code: int) -> str:
    """Reason string if the page looks like a bot-block page, else ''."""
    lower = html.lower()
    if status_code in (403, 429, 503):
        return f"Site returned HTTP {status_code} (likely blocking automated requests)."
    if "checking your browser" in lower or "cf-browser-verification" in lower:
        return "Cloudflare bot-protection challenge page detected."
    if "captcha" in lower and len(html) < 5000:
        return "CAPTCHA / verification page detected."
    if "<body" not in lower and "<html" in lower and len(html) < 1000:
        return "Page returned almost no content (likely JS-only rendering)."
    return ""


# ═══════════════════════ ANALYSIS ═══════════════════════
def extract_title(html_content: str) -> str:
    title = "page"
    try:
        lower = html_content.lower()
        if "<title>" in lower:
            start_idx = lower.find("<title>") + 7
            end_idx = lower.find("</title>")
            raw = html_content[start_idx:end_idx].strip()
            cleaned = "".join(c for c in raw if c.isalnum() or c in (" ", "_", "-")).strip()[:40]
            if cleaned:
                title = cleaned
    except Exception:
        pass
    return title


def get_real_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else "Not found"


def get_meta_description(html: str) -> str:
    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
        html, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
            html, re.IGNORECASE,
        )
    return m.group(1).strip()[:200] if m else "Not found"


def count_headings(html: str) -> dict:
    return {
        f"h{lvl}": len(re.findall(rf"<h{lvl}[\s>]", html, re.IGNORECASE))
        for lvl in range(1, 7)
    }


def count_links_and_images(html: str) -> tuple:
    links = len(re.findall(r"<a\s[^>]*href=", html, re.IGNORECASE))
    images = len(re.findall(r"<img\s[^>]*src=", html, re.IGNORECASE))
    return links, images


def detect_tech_stack(html: str, headers) -> list:
    detected = []
    lower = html.lower()
    server_header = headers.get("Server", "")
    powered_by = headers.get("X-Powered-By", "")
    signatures = [
        ("WordPress", ["wp-content", "wp-includes", "/wp-json/"]),
        ("Shopify", ["cdn.shopify.com", "shopify.com/s/"]),
        ("Wix", ["wix.com", "wixstatic.com"]),
        ("Squarespace", ["squarespace.com", "static1.squarespace"]),
        ("Next.js", ["__next", "_next/static"]),
        ("Nuxt.js", ["__nuxt", "_nuxt/"]),
        ("React", ["react-dom", "data-reactroot"]),
        ("Vue.js", ["__vue__", "v-cloak", "vue.js", "vue.min.js"]),
        ("Angular", ["ng-version", "ng-app"]),
        ("Bootstrap", ["bootstrap.min.css", "bootstrap.css"]),
        ("Tailwind CSS", ["tailwind"]),
        ("jQuery", ["jquery.min.js", "jquery.js"]),
        ("Cloudflare", ["cloudflare"]),
        ("Laravel", ["laravel_session", "/vendor/laravel"]),
        ("Django", ["csrfmiddlewaretoken"]),
        ("Magento", ["magento", "mage/cookies"]),
    ]
    for name, keywords in signatures:
        if any(k in lower for k in keywords):
            detected.append(name)
    if "php" in powered_by.lower():
        detected.append(f"PHP ({powered_by})")
    if server_header:
        detected.append(f"Server: {server_header}")
    if not detected:
        detected.append("Could not confidently detect a known stack")
    return list(dict.fromkeys(detected))


def fetch_html(url: str) -> dict:
    resp = safe_get(url, timeout=25)
    html = resp.text
    return {
        "url": url,
        "final_url": resp.url,
        "status": resp.status_code,
        "title": extract_title(html),
        "size_bytes": len(html.encode("utf-8")),
        "blocked": detect_blocked_page(html, resp.status_code),
        "html": html,
    }


def seo_report(url: str) -> dict:
    start = time.time()
    resp = safe_get(url, timeout=20)
    load_time = round(time.time() - start, 2)
    resp.raise_for_status()
    html = resp.text
    links, images = count_links_and_images(html)
    return {
        "url": url,
        "status": resp.status_code,
        "load_time": load_time,
        "size_kb": round(len(html.encode("utf-8")) / 1024, 2),
        "title": get_real_title(html),
        "meta_description": get_meta_description(html),
        "headings": count_headings(html),
        "links": links,
        "images": images,
    }


def tech_report(url: str) -> dict:
    resp = safe_get(url, timeout=20)
    resp.raise_for_status()
    return {"url": url, "detected": detect_tech_stack(resp.text, resp.headers)}


# ═══════════════════════ OFFLINE PACKAGING ═══════════════════════
CSS_URL_RE = re.compile(r'url\(\s*[\'"]?(.*?)[\'"]?\s*\)', re.IGNORECASE)
FONT_EXTS = (".woff", ".woff2", ".ttf", ".otf", ".eot")
_FOLDERS = {"css": "css", "js": "js", "img": "images", "font": "fonts"}
_DEFAULT_EXT = {"css": ".css", "js": ".js", "img": "", "font": ""}
_SKIP_PREFIX = ("data:", "javascript:", "#", "mailto:", "tel:")


def _fetch_asset(full_url):
    try:
        r = safe_get(full_url, timeout=10, max_bytes=config.MAX_ASSET_RESPONSE_BYTES)
        if r.status_code == 200:
            return full_url, r
    except Exception:
        pass
    return full_url, None


def _download_assets(seen, downloaded, failed, register_ref, pool, budget):
    """Download every registered asset in parallel. CSS files are rewritten so
    their url() references point at local copies (which may register more
    assets, hence two passes). budget: one-element list = bytes still allowed."""
    for _ in range(2):
        pending = [u for u in list(seen) if u not in downloaded and u not in failed]
        if not pending:
            break
        for full_url, resp in list(pool.map(_fetch_asset, pending)):
            if resp is None or budget[0] <= 0:
                failed.add(full_url)
                continue
            content = resp.content
            if seen[full_url][0] == "css":
                text = content.decode(resp.encoding or "utf-8", errors="ignore")

                def _css_sub(m, base=full_url):
                    ref = m.group(1)
                    if ref.startswith("data:"):
                        return m.group(0)
                    target = ref if ref.startswith("http") else urljoin(base, ref)
                    is_font = ref.lower().endswith(FONT_EXTS)
                    local = register_ref(target, "font" if is_font else "img", base)
                    return f"url('../{local}')"

                content = CSS_URL_RE.sub(_css_sub, text).encode("utf-8")
            budget[0] -= len(content)
            downloaded[full_url] = content


def _make_registrar(seen, max_assets):
    def register(u, category, current_page_url):
        if not u or u.startswith(_SKIP_PREFIX):
            return u
        full = urljoin(current_page_url, u.split("#")[0])
        if full in seen:
            return seen[full][1]
        if len(seen) >= max_assets:
            return u
        raw_name = urlparse(full).path.split("/")[-1] or "file"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)[:80]
        if "." not in name and _DEFAULT_EXT[category]:
            name += _DEFAULT_EXT[category]
        local_path = f"{_FOLDERS[category]}/{len(seen)}_{name or 'file'}"
        seen[full] = (category, local_path)
        return local_path
    return register


def _rewrite_assets(soup, reg):
    """Rewrite link/script/img/srcset/style references through reg(u, category)."""
    for tag in soup.find_all("link", href=True):
        rel = " ".join(tag.get("rel", [])).lower()
        if "stylesheet" in rel:
            tag["href"] = reg(tag["href"], "css")
        elif "icon" in rel:
            tag["href"] = reg(tag["href"], "img")
    for tag in soup.find_all("script", src=True):
        tag["src"] = reg(tag["src"], "js")
    for tag in soup.find_all("img"):
        if tag.get("src"):
            tag["src"] = reg(tag["src"], "img")
        if tag.get("srcset"):
            parts = []
            for entry in tag["srcset"].split(","):
                bits = entry.strip().split(" ")
                if bits and bits[0]:
                    bits[0] = reg(bits[0], "img")
                parts.append(" ".join(bits))
            tag["srcset"] = ", ".join(parts)
    for tag in soup.find_all(style=True):
        tag["style"] = CSS_URL_RE.sub(lambda m: f"url('{reg(m.group(1), 'img')}')", tag["style"])
    for tag in soup.find_all("style"):
        if tag.string:
            tag.string = CSS_URL_RE.sub(lambda m: f"url('{reg(m.group(1), 'img')}')", tag.string)


def build_offline_zip(html: str, base_url: str, out_path: str) -> int:
    """Single page + its assets → ZIP at out_path. Returns bundled asset count."""
    soup = BeautifulSoup(html, "html.parser")
    seen, downloaded, failed = {}, {}, set()
    register = _make_registrar(seen, config.MAX_ASSETS_SINGLE)

    _rewrite_assets(soup, lambda u, c: register(u, c, base_url))

    budget = [config.MAX_TOTAL_DOWNLOAD_BYTES]
    with ThreadPoolExecutor(max_workers=config.ASSET_CONCURRENCY) as pool:
        _download_assets(seen, downloaded, failed, register, pool, budget)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", str(soup))
        for full_url, (_cat, local_path) in seen.items():
            content = downloaded.get(full_url)
            if content is not None:
                zf.writestr(local_path, content)
    return len(downloaded)


def _page_local_path(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "index.html"
    if path.lower().endswith((".html", ".htm")):
        return re.sub(r"[^A-Za-z0-9/_.-]", "_", path)
    return re.sub(r"[^A-Za-z0-9/_-]", "_", path) + "/index.html"


def crawl_full_site(start_url: str, out_path: str, progress_cb=None) -> dict:
    """BFS-crawl every same-host page (bounded by config limits), share one
    asset pool, rewrite internal links, write ZIP to out_path.
    Returns {"pages", "assets", "skipped"}."""
    base_netloc = urlparse(start_url).netloc.lower()
    visited, pages = set(), {}
    queue = deque([(start_url, 0)])
    seen, downloaded, failed = {}, {}, set()
    skipped = 0

    def report(message):
        if progress_cb:
            progress_cb(pages=len(pages), visited=len(visited), message=message)

    def parse_links(soup, page_url, depth):
        links = []
        if depth < config.MAX_SITE_DEPTH:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith(_SKIP_PREFIX):
                    continue
                full = urljoin(page_url, href.split("#")[0])
                if urlparse(full).netloc.lower() == base_netloc:
                    links.append(full)
        return links

    def fetch_page(u):
        try:
            r = safe_get(u, timeout=25, max_bytes=config.MAX_PAGE_RESPONSE_BYTES)
            return r.status_code, r.text
        except Exception:
            return None, None

    with ThreadPoolExecutor(max_workers=max(config.PAGE_CONCURRENCY, config.ASSET_CONCURRENCY)) as pool:
        # ── 1. crawl pages in concurrent batches ──
        while queue and len(visited) < config.MAX_SITE_PAGES:
            batch = []
            while queue and len(batch) < config.PAGE_CONCURRENCY and len(visited) < config.MAX_SITE_PAGES:
                url, depth = queue.popleft()
                norm = url.split("#")[0].rstrip("/")
                if norm in visited:
                    continue
                visited.add(norm)
                batch.append((url, depth, norm))
            if not batch:
                continue
            for (url, depth, norm), (status, html) in zip(batch, pool.map(fetch_page, [b[0] for b in batch])):
                if html is None:
                    skipped += 1
                    report(f"Skipped (fetch failed): {url}")
                    continue
                if status != 200 or detect_blocked_page(html, status):
                    skipped += 1
                    report(f"Skipped (blocked/non-success): {url}")
                    continue
                soup = BeautifulSoup(html, "html.parser")
                pages[norm] = {"soup": soup, "local": _page_local_path(url), "url": url}
                report(url)
                for full in parse_links(soup, url, depth):
                    if full.split("#")[0].rstrip("/") not in visited:
                        queue.append((full, depth + 1))

        if not pages:
            raise requests.RequestException("No accessible pages could be captured from that site.")

        # ── 2. register assets + rewrite every page ──
        report("Rewriting links and collecting assets...")
        register = _make_registrar(seen, config.MAX_SITE_ASSETS)
        for norm, info in pages.items():
            soup, page_url, page_local = info["soup"], info["url"], info["local"]

            def reg_rel(u, category, page_url=page_url, page_local=page_local):
                canonical = register(u, category, page_url)
                if canonical.startswith(("http", "//")) or canonical == u:
                    return canonical
                return posixpath.relpath(canonical, posixpath.dirname(page_local) or ".")

            _rewrite_assets(soup, reg_rel)

            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith(_SKIP_PREFIX):
                    continue
                full = urljoin(page_url, href.split("#")[0])
                target_norm = full.rstrip("/")
                if urlparse(full).netloc.lower() == base_netloc and target_norm in pages:
                    a["href"] = posixpath.relpath(
                        pages[target_norm]["local"], posixpath.dirname(page_local) or "."
                    )

        # ── 3. download assets in parallel ──
        report(f"Downloading {len(seen)} assets...")
        budget = [config.MAX_TOTAL_DOWNLOAD_BYTES]
        _download_assets(seen, downloaded, failed, lambda u, c, base: register(u, c, base), pool, budget)

    # ── 4. write ZIP ──
    report("Packing ZIP...")
    start_norm = start_url.split("#")[0].rstrip("/")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in pages.values():
            zf.writestr(info["local"], str(info["soup"]))
        if "index.html" not in {i["local"] for i in pages.values()}:
            # start URL wasn't the site root: give the ZIP an entry point anyway
            entry = pages.get(start_norm) or next(iter(pages.values()))
            zf.writestr(
                "index.html",
                '<!doctype html><meta charset="utf-8">'
                f'<meta http-equiv="refresh" content="0; url={entry["local"]}">'
                f'<a href="{entry["local"]}">Open the saved site</a>',
            )
        for full_url, (_cat, local_path) in seen.items():
            content = downloaded.get(full_url)
            if content is not None:
                zf.writestr(local_path, content)

    return {"pages": len(pages), "assets": len(downloaded), "skipped": skipped}
