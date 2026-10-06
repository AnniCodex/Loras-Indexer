#!/usr/bin/env python3
"""
Loras_Indexer.py - v1.0 - by AnniCodex
Analyzer for .safetensors files with Civitai and CivArchive API integration
"""

import os
import sys
import json
import struct
import hashlib
import zlib
import glob
import sqlite3
import time
import re
import shutil
import subprocess
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from html import unescape

import requests

# Optional: curl_cffi for Cloudflare TLS impersonation
try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False

# Script metadata
VERSION = "1.0"
AUTHOR = "AnniCodex"

# Base configuration
CIVITAI_API_BASE = "https://civitai.com/api/v1"
CIVITAI_MODEL_VERSION_BY_HASH = f"{CIVITAI_API_BASE}/model-versions/by-hash"
CIVARCHIVE_BASE_URL = "https://civarchive.com"
CIVITAI_BASE_URL = "https://civitai.com"
CIVITAI_NSFW_URL = "https://civitai.red"
REQUEST_TIMEOUT = 15
RATE_LIMIT_DELAY = 1
MAX_PREVIEWS = 3
MAX_DESCRIPTION_LENGTH = 500

# Key used to persist HTML-fetched descriptions inside local JSON files,
# so we don't re-download the page on every run.
DESC_CACHE_KEY = '_fetched_description'

# Folder names that are too generic to infer the model from
GENERIC_FOLDER_NAMES = {
    'lora', 'loras', 'model', 'models', 'safetensors',
    'downloads', 'download', 'output', 'outputs',
    'ai', 'ai-models', 'ai_models', 'stable-diffusion',
    'stable_diffusion', 'sd', 'checkpoint', 'checkpoints',
    'new folder', 'newfolder', 'misc', 'temp', 'test',
    'civitai', 'civitai downloads', 'civitai_downloads',
    'recent', 'sorted', 'unsorted', 'archive', 'archives',
    'backup', 'backups', 'old', 'new'
}

# File and directory names (centralized)
SCRIPT_NAME = "Loras_Indexer"
CONFIG_FILE = f"{SCRIPT_NAME}.config"
CACHE_FILE = f"{SCRIPT_NAME}_Cache.json"
JSON_DIR = f"{SCRIPT_NAME}_Jsons"
PREVIEWS_DIR = f"{SCRIPT_NAME}_Previews"

# Cache for Firefox cookies (loaded lazily)
_FIREFOX_COOKIES_CACHE = None


class Config:
    """Configuration management"""

    def __init__(self):
        self.nsfw = True
        self.rows = 3
        self.save_previews = False
        self.refresh = 'ask'
        self.clean_previews = False
        self.use_firefox_cookies = False  # default: don't use cookies (privacy)
        self.load_config()

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.nsfw = data.get('nsfw', self.nsfw)
                    self.rows = data.get('rows', self.rows)
                    self.save_previews = data.get('save_previews', self.save_previews)
                    self.refresh = data.get('refresh', self.refresh)
                    self.clean_previews = data.get('clean_previews', self.clean_previews)
                    self.use_firefox_cookies = data.get('use_firefox_cookies', self.use_firefox_cookies)
                print(f"⚙️ Configuration loaded from {CONFIG_FILE}")
            except Exception as e:
                print(f"⚠️ Error loading config: {e}")

    def save_config(self):
        try:
            data = {
                'nsfw': self.nsfw,
                'rows': self.rows,
                'save_previews': self.save_previews,
                'refresh': self.refresh,
                'clean_previews': self.clean_previews,
                'use_firefox_cookies': self.use_firefox_cookies
            }
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"💾 Configuration saved to {CONFIG_FILE}")
        except Exception as e:
            print(f"⚠️ Error saving config: {e}")

    def parse_args(self):
        parser = argparse.ArgumentParser(
            description=f'Loras Indexer v{VERSION} by {AUTHOR} - Analyze .safetensors files'
        )
        parser.add_argument('--nsfw', type=str, choices=['on', 'off'],
                           help='Include NSFW models (on/off)')
        parser.add_argument('--rows', type=int, choices=[1, 2, 3],
                           help='Number of columns in the grid (1-3)')
        parser.add_argument('--savepreviews', type=str, choices=['on', 'off'],
                           help='Save previews locally (on/off)')
        parser.add_argument('--refresh', type=str, choices=['ask', 'never', 'yes', 'always'],
                           help='Refresh mode: ask (prompt), never (use cache), yes (force once then set to never), always (always recalculate)')
        parser.add_argument('--cleanpreviews', type=str, choices=['yes', 'no'],
                           help='Clean corrupted preview files before processing (yes/no)')
        parser.add_argument('--usefirefoxcookies', type=str, choices=['yes', 'no'],
                           help='Use Firefox cookies to access NSFW content (yes/no, default: no)')
        parser.add_argument('-propagate', action='store_true',
                           help='After processing the current folder, copy the script and config '
                                'into every subfolder and run it there too (creates an HTML index '
                                'for each subfolder in a single run)')

        args = parser.parse_args()

        if args.nsfw:
            self.nsfw = (args.nsfw == 'on')
            print(f"   NSFW: {'✅ included' if self.nsfw else '❌ excluded'}")

        if args.rows:
            self.rows = args.rows
            print(f"   Columns: {self.rows}")

        if args.savepreviews:
            self.save_previews = (args.savepreviews == 'on')
            print(f"   Save previews: {'✅ enabled' if self.save_previews else '❌ disabled'}")

        refresh_from_cli = args.refresh is not None
        if refresh_from_cli:
            self.refresh = args.refresh
            print(f"   Refresh mode: {self.refresh}")

        clean_from_cli = args.cleanpreviews is not None
        if clean_from_cli:
            self.clean_previews = (args.cleanpreviews == 'yes')
            print(f"   Clean previews: {'✅ enabled' if self.clean_previews else '❌ disabled'}")

        if args.usefirefoxcookies:
            self.use_firefox_cookies = (args.usefirefoxcookies == 'yes')
            print(f"   Use Firefox cookies: {'✅ enabled' if self.use_firefox_cookies else '❌ disabled'}")

        self.save_config()
        return refresh_from_cli, clean_from_cli, args.propagate


class HashCache:
    """Cache to avoid recalculating hashes for the same files"""

    def __init__(self, cache_file=CACHE_FILE):
        self.cache_file = cache_file
        self.cache_exists = os.path.exists(cache_file)
        self.cache = self.load_cache()

    def load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                print(f"📦 Hash cache loaded: {len(cache)} entries")
                return cache
            except Exception as e:
                print(f"⚠️ Error loading hash cache: {e}")
        return {}

    def save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
            print(f"💾 Hash cache saved: {len(self.cache)} entries")
        except Exception as e:
            print(f"⚠️ Error saving hash cache: {e}")

    def get(self, filepath):
        key = str(Path(filepath).absolute())
        return self.cache.get(key)

    def set(self, filepath, hashes):
        key = str(Path(filepath).absolute())
        self.cache[key] = {
            'hashes': hashes,
            'timestamp': datetime.now().isoformat(),
            'mtime': os.path.getmtime(filepath)
        }

    def is_fresh(self, filepath):
        key = str(Path(filepath).absolute())
        if key not in self.cache:
            return False
        current_mtime = os.path.getmtime(filepath)
        cached_mtime = self.cache[key].get('mtime', 0)
        return current_mtime == cached_mtime

    def has_cache(self):
        return self.cache_exists


# ---------------------------------------------------------------------------
# Firefox cookies extraction
# ---------------------------------------------------------------------------

def find_firefox_cookie_db():
    """Locate Firefox's cookies.sqlite across common OS paths."""
    patterns = [
        os.path.expanduser("~/AppData/Roaming/Mozilla/Firefox/Profiles/*/cookies.sqlite"),
        os.path.expanduser("~/.mozilla/firefox/*/cookies.sqlite"),
        os.path.expanduser("~/Library/Application Support/Firefox/Profiles/*/cookies.sqlite"),
        os.path.expanduser("~/snap/firefox/common/.mozilla/firefox/*/cookies.sqlite"),
        os.path.expanduser("~/.var/app/org.mozilla.firefox/.mozilla/firefox/*/cookies.sqlite"),
    ]

    candidates = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern))

    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def get_firefox_cookies(domains=None):
    """
    Extract cookies from Firefox's cookies.sqlite.
    Results are cached in memory to avoid repeated DB reads.
    Returns a dict {name: value} of cookies.
    """
    global _FIREFOX_COOKIES_CACHE

    if _FIREFOX_COOKIES_CACHE is not None:
        return _FIREFOX_COOKIES_CACHE

    cookies_dict = {}

    db_path = find_firefox_cookie_db()
    if not db_path:
        _FIREFOX_COOKIES_CACHE = {}
        return cookies_dict

    tmp_db = os.path.join(os.path.expanduser("~"), "ff_cookies_copy.sqlite")
    try:
        shutil.copy2(db_path, tmp_db)
        db_path = tmp_db
    except Exception:
        pass

    try:
        con = sqlite3.connect(db_path)
        cur = con.cursor()

        if domains:
            placeholders = ','.join('?' * len(domains))
            query = f"SELECT host, name, value FROM moz_cookies WHERE host IN ({placeholders})"
            cur.execute(query, domains)
        else:
            cur.execute("SELECT host, name, value FROM moz_cookies")

        rows = cur.fetchall()
        con.close()

        for host, name, value in rows:
            cookies_dict[name] = value

        print(f"    🍪 Loaded {len(cookies_dict)} Firefox cookies")

    except Exception as e:
        print(f"    ⚠️ Error reading Firefox cookies DB: {e}")
        cookies_dict = {}

    _FIREFOX_COOKIES_CACHE = cookies_dict
    return cookies_dict


# ---------------------------------------------------------------------------
# HTTP helpers with tiered fallback
# ---------------------------------------------------------------------------

def fetch_html_public(url):
    """
    Fetch a URL with plain requests, no cookies, no TLS impersonation.
    This is the first attempt at fetching an HTML page.
    """
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                          'AppleWebKit/537.36 (KHTML, like Gecko) '
                          'Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        })
        response = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if response.status_code == 200:
            return response.text
    except requests.exceptions.RequestException:
        pass
    return ""


def fetch_html_with_cookies(url, cookies):
    """
    Fetch a URL using curl_cffi with Chrome TLS impersonation + Firefox cookies.
    Only used when the user has explicitly enabled use_firefox_cookies.
    """
    if not CURL_CFFI_AVAILABLE:
        return ""

    try:
        session = curl_requests.Session(impersonate="chrome120")
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=".civitai.red")
            session.cookies.set(name, value, domain=".civitai.com")

        response = session.get(url, timeout=REQUEST_TIMEOUT,
                               allow_redirects=True,
                               headers={
                                   'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                                   'Accept-Language': 'en-US,en;q=0.5',
                               })

        if response.status_code == 200:
            return response.text

        cf_mitigated = response.headers.get('Cf-Mitigated', '').lower()
        if response.status_code == 403 and 'challenge' in cf_mitigated:
            print(f"    ⚠️ Cloudflare challenge (curl_cffi + cookies)")
    except Exception as e:
        print(f"    ⚠️ curl_cffi error: {e}")

    return ""


# ---------------------------------------------------------------------------
# Description extraction
# ---------------------------------------------------------------------------

def extract_description_from_html(html_content):
    """
    Extract the 'description' field from the Next.js state JSON in the page.

    Looks for the pattern:
        "id":<number>,"name":"...","description":"<p>...</p>"

    Returns the cleaned description string, or empty string if not found.
    """
    if not html_content:
        return ""

    pattern = (
        r'"id"\s*:\s*\d+\s*,\s*"name"\s*:\s*"(?:[^"\\]|\\.)*"\s*,\s*'
        r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"'
    )
    match = re.search(pattern, html_content)

    if not match:
        return ""

    raw_desc = match.group(1)

    try:
        description = json.loads(f'"{raw_desc}"')
    except json.JSONDecodeError:
        description = raw_desc

    description = re.sub(r'<[^>]+>', ' ', description)
    description = re.sub(r'\s+', ' ', description).strip()

    return description


def fetch_description_from_html(model_url, use_cookies=False):
    """
    Fetch the model page HTML and extract the 'description' field.
    Uses a tiered strategy:

      Tier 1: public HTML request (no cookies)
      Tier 2: if Tier 1 fails AND use_cookies=True AND curl_cffi available,
              retry with Firefox cookies + Chrome TLS impersonation
    """
    if not model_url:
        return ""

    html_content = fetch_html_public(model_url)

    if not html_content and use_cookies:
        if not CURL_CFFI_AVAILABLE:
            print(f"    ⚠️ Firefox cookies requested but curl_cffi not installed")
            print(f"       → Install with: pip install curl_cffi")
        else:
            print(f"    🔐 Retrying with Firefox cookies + TLS impersonation...")
            cookies = get_firefox_cookies(domains=['.civitai.com', '.civitai.red'])
            if cookies:
                html_content = fetch_html_with_cookies(model_url, cookies)
            else:
                print(f"    ⚠️ No cookies extracted from Firefox")

    if not html_content:
        return ""

    return extract_description_from_html(html_content)


def persist_description_in_json(filename, description):
    """
    Inject a fetched description into the local JSON cache under DESC_CACHE_KEY.
    This prevents re-downloading the HTML page on future runs.
    """
    if not filename or not description:
        return

    json_path = get_json_path_for_file(filename)
    if not os.path.exists(json_path):
        return

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            cached_json = json.load(f)

        if cached_json.get(DESC_CACHE_KEY) == description:
            return

        cached_json[DESC_CACHE_KEY] = description
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(cached_json, f, indent=2, ensure_ascii=False)
        print(f"    💾 Description cached to JSON")

    except Exception as e:
        print(f"    ⚠️ Could not cache description: {e}")


# ---------------------------------------------------------------------------
# Filesystem / previews helpers
# ---------------------------------------------------------------------------

def get_json_path_for_file(filename):
    safe_name = Path(filename).stem
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in '._- ')
    return os.path.join(JSON_DIR, f"{safe_name}.json")


def get_preview_paths(filename, preview_index):
    safe_name = Path(filename).stem
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in '._- ')
    preview_filename = f"{safe_name}_preview{preview_index + 1}.jpg"
    return os.path.join(PREVIEWS_DIR, preview_filename)


def ensure_directories():
    os.makedirs(JSON_DIR, exist_ok=True)
    os.makedirs(PREVIEWS_DIR, exist_ok=True)


def is_valid_image(filepath):
    """
    Check if a file is a valid image (JPEG, PNG, WebP, GIF, BMP) by inspecting
    its magic bytes. Does NOT rely on the file extension.
    """
    try:
        if not os.path.exists(filepath):
            return False
        if os.path.getsize(filepath) < 100:
            return False

        with open(filepath, 'rb') as f:
            header = f.read(12)

        if header[:3] == b'\xFF\xD8\xFF':
            return True
        if header[:8] == b'\x89PNG\r\n\x1a\n':
            return True
        if header[:6] in (b'GIF87a', b'GIF89a'):
            return True
        if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
            return True
        if header[:2] == b'BM':
            return True

        return False

    except Exception:
        return False


def save_preview_image(img_url, filepath):
    """Download and save a preview image. Validates Content-Type and magic bytes."""
    try:
        response = requests.get(img_url, timeout=REQUEST_TIMEOUT, allow_redirects=True)

        if response.status_code != 200:
            print(f"        ⚠️ HTTP {response.status_code} for preview")
            return False

        content_type = response.headers.get('Content-Type', '').lower()
        if 'image' not in content_type:
            print(f"        ⚠️ Content-Type is not an image: {content_type}")
            return False

        content = response.content
        if len(content) < 100:
            print(f"        ⚠️ Downloaded content too small ({len(content)} bytes)")
            return False

        is_valid = (
            content.startswith(b'\xFF\xD8\xFF') or
            content.startswith(b'\x89PNG\r\n\x1a\n') or
            content.startswith((b'GIF87a', b'GIF89a')) or
            (content[:4] == b'RIFF' and content[8:12] == b'WEBP') or
            content.startswith(b'BM')
        )

        if not is_valid:
            first_bytes = content[:16].hex()
            print(f"        ⚠️ Not a valid image (magic bytes: {first_bytes})")
            return False

        with open(filepath, 'wb') as f:
            f.write(content)
        return True

    except requests.exceptions.RequestException as e:
        print(f"        ⚠️ Download error: {e}")
        return False
    except Exception as e:
        print(f"        ⚠️ Save error: {e}")
        return False


def clean_corrupted_previews():
    """Remove any non-image file in the previews folder."""
    if not os.path.exists(PREVIEWS_DIR):
        print(f"📁 Previews directory does not exist: {PREVIEWS_DIR}/")
        return 0

    removed = 0
    total_checked = 0

    print(f"\n🧹 Cleaning corrupted previews in {PREVIEWS_DIR}/...")

    try:
        for fname in os.listdir(PREVIEWS_DIR):
            fpath = os.path.join(PREVIEWS_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            if not fname.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp')):
                continue

            total_checked += 1

            if not is_valid_image(fpath):
                try:
                    size = os.path.getsize(fpath)
                    os.remove(fpath)
                    print(f"   🗑️  Removed: {fname} ({size} bytes)")
                    removed += 1
                except Exception as e:
                    print(f"   ⚠️  Could not remove {fname}: {e}")

    except Exception as e:
        print(f"⚠️ Error during cleanup: {e}")

    print(f"🧹 Cleanup complete: {removed} corrupted files removed out of {total_checked} checked\n")
    return removed


# ---------------------------------------------------------------------------
# JSON cache helpers
# ---------------------------------------------------------------------------

def json_has_valid_data(json_data):
    if not json_data:
        return False
    if json_data.get('error') == 'not_found':
        return False
    if json_data.get('modelId') or json_data.get('name'):
        return True
    return False


def load_json_from_cache(filename):
    json_path = get_json_path_for_file(filename)
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not json_has_valid_data(data):
                return None
            print(f"    📦 From JSON cache: {data.get('name', 'N/A')[:50]}...")
            return data
        except Exception as e:
            print(f"    ⚠️ Error reading JSON: {e}")
            return None
    return None


def save_json_cache(filename, json_data):
    """
    Save data to a JSON file.

    Safety: never overwrites a valid JSON file with an empty/error one,
    UNLESS the new data contains additional fields (like _fetched_description)
    that the old one didn't have.
    """
    try:
        json_path = get_json_path_for_file(filename)

        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)

                if json_has_valid_data(existing) and not json_has_valid_data(json_data):
                    has_enrichment = DESC_CACHE_KEY in json_data and json_data[DESC_CACHE_KEY]
                    if not has_enrichment:
                        print(f"    🛡️ Keeping existing valid JSON (new data would be empty)")
                        return json_path
            except Exception:
                pass

        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        return json_path
    except Exception as e:
        print(f"    ⚠️ Error saving JSON: {e}")
        return None


def save_raw_next_data(filename, next_data_json):
    try:
        safe_name = Path(filename).stem
        safe_name = "".join(c for c in safe_name if c.isalnum() or c in '._- ')
        json_path = os.path.join(JSON_DIR, f"{safe_name}_nextdata.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(next_data_json, f, indent=2, ensure_ascii=False)
        return json_path
    except Exception as e:
        print(f"    ⚠️ Error saving NEXT_DATA: {e}")
        return None


# ---------------------------------------------------------------------------
# Hashing / metadata
# ---------------------------------------------------------------------------

def calculate_autov3_hash(filepath):
    try:
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            header_size_bytes = f.read(8)
            if len(header_size_bytes) == 8:
                try:
                    header_size = struct.unpack('<Q', header_size_bytes)[0]
                    f.seek(8 + header_size)
                    for block in iter(lambda: f.read(8192), b''):
                        sha256.update(block)
                except Exception:
                    f.seek(0)
                    for block in iter(lambda: f.read(8192), b''):
                        sha256.update(block)
            else:
                f.seek(0)
                for block in iter(lambda: f.read(8192), b''):
                    sha256.update(block)
        return sha256.hexdigest().lower()[:12]
    except Exception as e:
        print(f"    ⚠️ Error calculating AutoV3: {e}")
        return None


def calculate_sha256(filepath):
    try:
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(8192), b''):
                sha256.update(block)
        return sha256.hexdigest().lower()
    except Exception as e:
        print(f"    ⚠️ Error calculating SHA256: {e}")
        return None


def read_safetensors_metadata(filepath):
    try:
        with open(filepath, 'rb') as f:
            header_size_bytes = f.read(8)
            if len(header_size_bytes) < 8:
                return None
            header_size = struct.unpack('<Q', header_size_bytes)[0]
            header_json_bytes = f.read(header_size)
            if len(header_json_bytes) < header_size:
                return None
            header = json.loads(header_json_bytes.decode('utf-8'))
            if '__metadata__' in header:
                return header['__metadata__']
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Civitai API
# ---------------------------------------------------------------------------

def query_civitai_by_autov3(autov3_hash):
    """Query Civitai public API using AutoV3 hash."""
    if not autov3_hash:
        return None

    try:
        url = f"{CIVITAI_MODEL_VERSION_BY_HASH}/{autov3_hash}"
        print(f"    🔍 Searching Civitai with AutoV3: {autov3_hash}")

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=headers)

        if response.status_code == 200:
            print(f"    ✅ Found on Civitai!")
            return response.json()
        elif response.status_code == 404:
            print(f"    ❌ Not found on Civitai (404)")
            return None
        else:
            print(f"    ❌ Error {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"    ❌ Connection error: {e}")
        return None


# ---------------------------------------------------------------------------
# CivArchive
# ---------------------------------------------------------------------------

def extract_badge_type(html_content):
    try:
        pattern = r'<div class="inline-flex items-center rounded-md border text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80 px-3 py-1">(.*?)</div>'
        match = re.search(pattern, html_content, re.DOTALL)
        if match:
            badge_text = match.group(1)
            badge_text = re.sub(r'<!--.*?-->', '', badge_text)
            badge_text = badge_text.strip()
            return badge_text
    except Exception:
        pass
    return "Unknown"


def check_nsfw_flag(html_content):
    if '"is_nsfw":true' in html_content:
        return True
    return False


def convert_video_to_jpeg(mp4_url):
    if not mp4_url or not mp4_url.endswith('.mp4'):
        return mp4_url
    try:
        pattern = r'(https://image\.civitai\.com/[^/]+/[^/]+)/original=true/(\d+)\.mp4'
        match = re.search(pattern, mp4_url)
        if match:
            base_path = match.group(1)
            video_id = match.group(2)
            jpeg_url = f"{base_path}/anim=false,transcode=true,width=450,optimized=true/{video_id}.jpeg"
            return jpeg_url
        else:
            return mp4_url
    except Exception:
        return mp4_url


def get_video_page_url(mp4_url):
    if not mp4_url or not mp4_url.endswith('.mp4'):
        return None
    try:
        pattern = r'/original=true/(\d+)\.mp4'
        match = re.search(pattern, mp4_url)
        if match:
            video_id = match.group(1)
            return f"{CIVITAI_NSFW_URL}/images/{video_id}"
    except Exception:
        pass
    return None


def process_preview_image(img_url, prompt_text="", config=None, filename=None, preview_idx=0):
    is_video = img_url.endswith('.mp4')
    display_url = convert_video_to_jpeg(img_url) if is_video else img_url
    video_page_url = get_video_page_url(img_url) if is_video else None

    local_path = None
    if config and config.save_previews and filename is not None:
        preview_path = get_preview_paths(filename, preview_idx)

        if os.path.exists(preview_path):
            if is_valid_image(preview_path):
                local_path = preview_path
            else:
                print(f"        🗑️ Corrupted preview found, re-downloading: {os.path.basename(preview_path)}")
                try:
                    os.remove(preview_path)
                except Exception:
                    pass
                if save_preview_image(display_url, preview_path):
                    local_path = preview_path
                    print(f"        💾 Preview saved: {os.path.basename(preview_path)}")
        else:
            if save_preview_image(display_url, preview_path):
                local_path = preview_path
                print(f"        💾 Preview saved: {os.path.basename(preview_path)}")

        if local_path is None:
            print(f"        ℹ️ Using remote URL as fallback")

    final_url = local_path if local_path else display_url

    return {
        'url': final_url,
        'original_url': img_url,
        'prompt': prompt_text,
        'is_video': is_video,
        'video_page_url': video_page_url,
        'local_path': local_path
    }


def query_civarchive_by_sha256(sha256_hash, use_firefox_cookies=False):
    if not sha256_hash:
        return None

    try:
        search_url = f"{CIVARCHIVE_BASE_URL}/sha256/{sha256_hash}"
        print(f"    🔍 Searching CivArchive with SHA256: {sha256_hash[:16]}...")

        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(search_url, timeout=REQUEST_TIMEOUT, headers=headers)

        if response.status_code != 200:
            print(f"    ❌ Page not found on CivArchive ({response.status_code})")
            return None

        model_link_match = re.search(r'href="(/models/\d+\?modelVersionId=\d+)"', response.text)
        if not model_link_match:
            print(f"    ❌ Model link not found")
            return None

        model_url = model_link_match.group(1)
        full_model_url = f"{CIVARCHIVE_BASE_URL}{model_url}"
        print(f"    🔗 Model link: {full_model_url}")

        model_response = requests.get(full_model_url, timeout=REQUEST_TIMEOUT, headers=headers)
        if model_response.status_code != 200:
            print(f"    ❌ Model page not loadable")
            return None

        html_content = model_response.text
        badge_type = extract_badge_type(html_content)
        is_nsfw = check_nsfw_flag(html_content)

        next_data_match = re.search(r'<script id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
                                    html_content, re.DOTALL)
        if not next_data_match:
            print(f"    ❌ NEXT_DATA not found")
            return None

        try:
            next_data = json.loads(next_data_match.group(1))

            model_data = None
            model_version_data = None

            if 'props' in next_data and 'pageProps' in next_data['props']:
                page_props = next_data['props']['pageProps']

                if 'modelVersion' in page_props:
                    model_version_data = page_props['modelVersion']
                    if 'model' in model_version_data:
                        model_data = model_version_data['model']
                elif 'model' in page_props:
                    model_data = page_props['model']

            if not model_data and not model_version_data:
                print(f"    ❌ Model data not found in JSON")
                return None

            model_name = "Unknown"
            if model_version_data and model_version_data.get('name'):
                model_name = model_version_data.get('name')
            elif model_data and model_data.get('name'):
                model_name = model_data.get('name')

            description = (next_data.get(DESC_CACHE_KEY) or '').strip()

            if not description and model_version_data and model_version_data.get('description'):
                description = model_version_data.get('description')
            if not description and model_data and model_data.get('description'):
                description = model_data.get('description')

            if not description:
                print(f"    📄 Description not in JSON, fetching from HTML...")
                description = fetch_description_from_html(
                    full_model_url,
                    use_cookies=use_firefox_cookies
                )

                if description:
                    next_data[DESC_CACHE_KEY] = description
                    print(f"    💾 Description will be cached in next_data")

            if description:
                description = description[:MAX_DESCRIPTION_LENGTH] + ('...' if len(description) > MAX_DESCRIPTION_LENGTH else '')

            model_id = 0
            model_version_id = 0
            if model_data and model_data.get('id'):
                model_id = model_data.get('id')
            if model_version_data and model_version_data.get('id'):
                model_version_id = model_version_data.get('id')

            images = []
            image_url_pattern = r'"image_url":\s*"([^"]+)"'
            image_matches = re.findall(image_url_pattern, json.dumps(next_data))

            for idx, img_url in enumerate(image_matches[:MAX_PREVIEWS]):
                if img_url:
                    prompt = ""
                    prompt_pattern = rf'"prompt":\s*"([^"]+)"[^}}]*"image_url":\s*"{re.escape(img_url)}"'
                    prompt_match = re.search(prompt_pattern, json.dumps(next_data), re.DOTALL)
                    if not prompt_match:
                        prompt_pattern = rf'"image_url":\s*"{re.escape(img_url)}"[^}}]*"prompt":\s*"([^"]+)"'
                        prompt_match = re.search(prompt_pattern, json.dumps(next_data), re.DOTALL)
                    if prompt_match:
                        prompt = prompt_match.group(1)

                    images.append({'url': img_url, 'prompt': prompt})

            if not images:
                if model_version_data and model_version_data.get('images'):
                    for img in model_version_data.get('images', [])[:MAX_PREVIEWS]:
                        if img.get('url'):
                            prompt = ""
                            if img.get('meta') and isinstance(img.get('meta'), dict):
                                prompt = img.get('meta', {}).get('prompt', '')
                            images.append({'url': img.get('url'), 'prompt': prompt})
                elif model_data and model_data.get('images'):
                    for img in model_data.get('images', [])[:MAX_PREVIEWS]:
                        if img.get('url'):
                            prompt = ""
                            if img.get('meta') and isinstance(img.get('meta'), dict):
                                prompt = img.get('meta', {}).get('prompt', '')
                            images.append({'url': img.get('url'), 'prompt': prompt})

            result = {
                'name': model_name,
                'badge_type': badge_type,
                'description': description,
                'nsfw': is_nsfw,
                'modelId': model_id,
                'modelVersionId': model_version_id,
                'model_url': full_model_url,
                'images': images,
                'next_data': next_data
            }

            print(f"    ✅ Model found: {result['name'][:50]}...")
            return result

        except json.JSONDecodeError as e:
            print(f"    ❌ JSON parsing error: {e}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"    ❌ CivArchive connection error: {e}")
        return None


# ---------------------------------------------------------------------------
# Model info extraction
# ---------------------------------------------------------------------------

def extract_model_info_from_civitai(data, hash_value=None, filename=None, config=None):
    if not data or data.get('error') == 'not_found':
        return None

    model_id = data.get('modelId')
    model_version_id = data.get('id')
    is_nsfw = data.get('model', {}).get('nsfw', False)

    base_domain = CIVITAI_NSFW_URL if is_nsfw else CIVITAI_BASE_URL
    model_url = f"{base_domain}/models/{model_id}"
    model_version_url = f"{base_domain}/models/{model_id}?modelVersionId={model_version_id}"
    fallback_url = f"{CIVITAI_BASE_URL}/models/{model_id}" if is_nsfw else None
    api_url = f"{CIVITAI_MODEL_VERSION_BY_HASH}/{hash_value}" if hash_value else None

    real_name = data.get('model', {}).get('name', 'N/A')
    version_name = data.get('name', 'N/A')

    if real_name != 'N/A' and real_name != version_name:
        final_name = f"{real_name} ({version_name})"
    else:
        final_name = version_name

    base_model = data.get('baseModel', 'N/A')

    model_type_display = base_model
    if 'z-image' in base_model.lower():
        if 'turbo' in version_name.lower() or 'turbo' in base_model.lower():
            model_type_display = 'Z-Image Turbo'
        else:
            model_type_display = 'Z-Image Base'
    elif 'flux' in base_model.lower():
        if 'klein' in version_name.lower():
            model_type_display = 'Flux (Klein)'
        else:
            model_type_display = 'Flux'
    elif 'sdxl' in base_model.lower():
        model_type_display = 'SDXL'
    elif 'sd1.5' in base_model.lower():
        model_type_display = 'SD 1.5'
    elif 'sd2.1' in base_model.lower():
        model_type_display = 'SD 2.1'
    elif 'minimax' in base_model.lower():
        model_type_display = 'MiniMax H3'
    elif 'krea' in base_model.lower():
        model_type_display = 'Krea 2'

    trained_words = data.get('trainedWords', [])

    previews = []
    for img in data.get('images', []):
        if len(previews) >= MAX_PREVIEWS:
            break
        img_url = img.get('url')
        if not img_url:
            continue

        if config and not config.nsfw:
            nsfw_level = img.get('nsfwLevel', 1)
            if nsfw_level > 2:
                continue

        meta = img.get('meta')
        img_prompt = meta.get('prompt', '') if meta and isinstance(meta, dict) else ''
        previews.append(process_preview_image(img_url, img_prompt, config, filename, len(previews)))

    description = (data.get(DESC_CACHE_KEY) or '').strip()

    if not description:
        description = (data.get('description') or '').strip()
    if not description:
        description = (data.get('model', {}).get('description') or '').strip()

    if not description:
        print(f"    📄 Description not in API, fetching from HTML...")
        description = fetch_description_from_html(
            model_url,
            use_cookies=config.use_firefox_cookies if config else False
        )

        if description and filename:
            persist_description_in_json(filename, description)

    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[:MAX_DESCRIPTION_LENGTH] + '...'

    return {
        'nome_modello': final_name,
        'nome_reale': real_name,
        'versione': version_name,
        'modello_base': base_model,
        'modello_display': model_type_display,
        'tipo': data.get('model', {}).get('type', 'N/A'),
        'nsfw': is_nsfw,
        'descrizione': description,
        'download_count': data.get('stats', {}).get('downloadCount', 0),
        'trained_words': trained_words,
        'creazione': data.get('createdAt', ''),
        'model_id': model_id,
        'model_version_id': model_version_id,
        'model_url': model_url,
        'model_version_url': model_version_url,
        'fallback_url': fallback_url,
        'api_url': api_url,
        'hash_usato': hash_value,
        'previews': previews,
        'file_info': [],
        'from_civarchive': False
    }


def extract_model_info_from_civarchive(civarchive_data, sha256_hash=None, filename=None, config=None):
    if not civarchive_data:
        return None

    is_nsfw = civarchive_data.get('nsfw', False)
    model_url = civarchive_data.get('model_url', '')
    badge_type = civarchive_data.get('badge_type', 'Unknown')
    model_type_display = badge_type

    description = civarchive_data.get('description', '')
    if description:
        description = description[:MAX_DESCRIPTION_LENGTH] + ('...' if len(description) > MAX_DESCRIPTION_LENGTH else '')

    previews = []
    for idx, img in enumerate(civarchive_data.get('images', [])[:MAX_PREVIEWS]):
        if img.get('url'):
            processed = process_preview_image(img['url'], img.get('prompt', ''), config, filename, idx)
            previews.append(processed)

    return {
        'nome_modello': civarchive_data.get('name', 'Unknown'),
        'nome_reale': civarchive_data.get('name', 'Unknown'),
        'versione': '',
        'modello_base': badge_type,
        'modello_display': model_type_display,
        'tipo': badge_type,
        'nsfw': is_nsfw,
        'descrizione': description,
        'download_count': 0,
        'trained_words': [],
        'creazione': '',
        'model_id': civarchive_data.get('modelId', 0),
        'model_version_id': civarchive_data.get('modelVersionId', 0),
        'model_url': model_url,
        'model_version_url': model_url,
        'fallback_url': None,
        'api_url': None,
        'hash_usato': sha256_hash,
        'previews': previews,
        'file_info': [],
        'from_civarchive': True,
        'next_data': civarchive_data.get('next_data')
    }


# ---------------------------------------------------------------------------
# Model identification
# ---------------------------------------------------------------------------

_MODEL_PATTERNS = [
    ('minimax',      ['minimax', 'minimax-h3', 'minimax_h3', 'minimaxh3', 'hailuo']),
    ('krea2',        ['krea2', 'krea-2', 'krea_2', 'krea']),
    ('flux_klein',   ['klein', 'flux-klein', 'flux_klein']),
    ('zimage_turbo', ['z-image-turbo', 'zimage_turbo', 'zimage-turbo']),
    ('zimage_base',  ['z-image-base', 'zimage_base', 'z-image', 'zimage']),
    ('pixart',       ['pixart', 'pixart-alpha']),
    ('pony',         ['pony', 'pony-diffusion']),
    ('ltx',          ['ltx-video', 'ltx2.3', 'ltx']),
    ('wan',          ['wan2.2', 'wan2.1', 'wan-video', 'wan_video', 'wanvideo', 'wan']),
    ('flux',         ['flux', 'flux-dev', 'flux-schnell']),
    ('sdxl',         ['sdxl', 'stable-diffusion-xl']),
    ('sd15',         ['sd1.5', 'sd15', 'stable-diffusion-1.5']),
    ('sd21',         ['sd2.1', 'sd21']),
]


def identify_model_from_folder(filepath):
    """Try to infer the AI model from the parent folder name (last resort)."""
    try:
        folder_name = Path(filepath).parent.name.lower().strip()
    except Exception:
        return None

    if not folder_name:
        return None

    if folder_name in GENERIC_FOLDER_NAMES:
        return None

    for model, keywords in _MODEL_PATTERNS:
        for kw in keywords:
            if kw in folder_name:
                return model

    return None


def identify_model_from_metadata_and_civitai(metadata, filename, civitai_data, filepath=None):
    """
    Identify the AI model using a priority cascade:

      1. Civitai / CivArchive data (most reliable)
      2. Filename patterns
      3. Internal safetensors metadata
      4. Parent folder name (last resort)
      5. 'unknown'
    """

    # --- Priority 1: Civitai/CivArchive data ---
    if civitai_data and civitai_data.get('modello_base'):
        base_model = civitai_data['modello_base'].lower()
        version_name = civitai_data.get('versione', '').lower()

        if 'z-image' in base_model:
            if 'turbo' in version_name or 'turbo' in base_model:
                return 'zimage_turbo'
            else:
                return 'zimage_base'

        if 'flux' in base_model:
            if 'klein' in version_name:
                return 'flux_klein'
            return 'flux'
        elif 'sdxl' in base_model:
            return 'sdxl'
        elif 'sd1.5' in base_model:
            return 'sd15'
        elif 'sd2.1' in base_model:
            return 'sd21'
        elif 'wan' in base_model:
            return 'wan'
        elif 'ltx' in base_model:
            return 'ltx'
        elif 'pony' in base_model:
            return 'pony'
        elif 'pixart' in base_model:
            return 'pixart'
        elif 'minimax' in base_model:
            return 'minimax'
        elif 'krea' in base_model:
            return 'krea2'

    # --- Priority 2: Filename patterns ---
    search_text = filename.lower()

    for model, keywords in _MODEL_PATTERNS:
        for kw in keywords:
            if kw in search_text:
                return model

    # --- Priority 3: Internal safetensors metadata ---
    if metadata and isinstance(metadata, dict):
        base_hints = []
        for key in ('ss_base_model_version', 'ss_sd_model_name',
                    'modelspec.sai_model_spec', 'base_model',
                    'ss_network_module', 'ss_network_args'):
            val = metadata.get(key)
            if val:
                base_hints.append(str(val).lower())

        hint_text = ' '.join(base_hints)

        if hint_text:
            for model, keywords in _MODEL_PATTERNS:
                for kw in keywords:
                    if kw in hint_text:
                        return model

            if 'sd_v1' in hint_text or 'v1-5' in hint_text or 'v1_5' in hint_text:
                return 'sd15'

    # --- Priority 4: Parent folder name ---
    if filepath:
        folder_model = identify_model_from_folder(filepath)
        if folder_model:
            return folder_model

    return 'unknown'


def get_model_display_name(model_key):
    display_names = {
        'zimage_turbo': 'Z-Image Turbo',
        'zimage_base': 'Z-Image Base',
        'flux_klein': 'Flux (Klein)',
        'flux': 'Flux',
        'wan': 'Wan 2.2',
        'ltx': 'LTX Video 2.3',
        'sdxl': 'SDXL',
        'sd15': 'Stable Diffusion 1.5',
        'sd21': 'Stable Diffusion 2.1',
        'pony': 'Pony Diffusion',
        'pixart': 'PixArt-α',
        'minimax': 'MiniMax H3',
        'krea2': 'Krea 2',
        'unknown': 'Unknown'
    }
    return display_names.get(model_key, model_key)


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------

def analyze_file(filepath, hash_cache, config, force_refresh=False):
    print(f"  📁 Analyzing: {filepath.name}...")

    result = {
        'filename': filepath.name,
        'size_mb': os.path.getsize(filepath) / (1024 * 1024),
        'metadata_locale': None,
        'hash': {},
        'civitai_data': None,
        'modello_identificato': None,
        'modello_display': None,
        'from_cache': False,
        'from_civarchive': False
    }

    result['metadata_locale'] = read_safetensors_metadata(filepath)

    print(f"    🔑 Retrieving hashes...")
    cached_hashes = hash_cache.get(filepath) if not force_refresh else None

    if cached_hashes and hash_cache.is_fresh(filepath):
        result['hash'] = cached_hashes['hashes']
        print(f"    📦 Hashes from cache")
    else:
        result['hash']['autov3'] = calculate_autov3_hash(filepath)
        result['hash']['sha256'] = calculate_sha256(filepath)
        hash_cache.set(filepath, result['hash'])
        print(f"    📊 Hashes calculated: AutoV3={result['hash']['autov3']}")

    json_data = load_json_from_cache(filepath.name)
    if json_data:
        if json_has_valid_data(json_data):
            if json_data.get('badge_type') or json_data.get('next_data'):
                result['civitai_data'] = extract_model_info_from_civarchive(
                    json_data, result['hash'].get('sha256'), filepath.name, config
                )
                if result['civitai_data']:
                    result['from_cache'] = True
                    result['from_civarchive'] = True
                    print(f"    ✅ From JSON cache (CivArchive): {result['civitai_data']['nome_modello'][:50]}...")
                    result['modello_identificato'] = identify_model_from_metadata_and_civitai(
                        result['metadata_locale'], result['filename'], result['civitai_data'],
                        filepath=filepath
                    )
                    result['modello_display'] = result['civitai_data']['modello_display']
                    return result
            else:
                result['civitai_data'] = extract_model_info_from_civitai(
                    json_data, result['hash'].get('autov3'), filepath.name, config
                )
                if result['civitai_data']:
                    result['from_cache'] = True
                    print(f"    ✅ From JSON cache (Civitai): {result['civitai_data']['nome_modello'][:50]}...")
                    result['modello_identificato'] = identify_model_from_metadata_and_civitai(
                        result['metadata_locale'], result['filename'], result['civitai_data'],
                        filepath=filepath
                    )
                    result['modello_display'] = result['civitai_data']['modello_display']
                    return result

    if not result['civitai_data'] and not force_refresh:
        print(f"    🌐 Searching Civitai...")

        autov3_hash = result['hash'].get('autov3')
        if autov3_hash:
            api_data = query_civitai_by_autov3(autov3_hash)
            if api_data:
                json_path = save_json_cache(filepath.name, api_data)
                if json_path:
                    print(f"    💾 JSON saved: {json_path}")

                result['civitai_data'] = extract_model_info_from_civitai(
                    api_data, autov3_hash, filepath.name, config
                )
                if result['civitai_data']:
                    print(f"    ✅ Found on Civitai! {result['civitai_data']['nome_modello'][:50]}...")

    if not result['civitai_data']:
        print(f"    📚 Fallback: searching CivArchive...")

        sha256_hash = result['hash'].get('sha256')
        if sha256_hash:
            civarchive_data = query_civarchive_by_sha256(
                sha256_hash,
                use_firefox_cookies=config.use_firefox_cookies
            )
            if civarchive_data:
                result['civitai_data'] = extract_model_info_from_civarchive(
                    civarchive_data, sha256_hash, filepath.name, config
                )
                if result['civitai_data']:
                    result['from_civarchive'] = True
                    print(f"    ✅ Found on CivArchive! {result['civitai_data']['nome_modello'][:50]}...")
                    save_json_cache(filepath.name, civarchive_data)
                    if civarchive_data.get('next_data'):
                        save_raw_next_data(filepath.name, civarchive_data['next_data'])

    if not result['civitai_data']:
        print(f"    ❌ Not found on Civitai or CivArchive")
        empty_json = {"error": "not_found", "timestamp": datetime.now().isoformat()}
        save_json_cache(filepath.name, empty_json)

    result['modello_identificato'] = identify_model_from_metadata_and_civitai(
        result['metadata_locale'], result['filename'], result['civitai_data'],
        filepath=filepath
    )

    if result['civitai_data'] and result['civitai_data'].get('modello_display'):
        result['modello_display'] = result['civitai_data']['modello_display']
    else:
        result['modello_display'] = get_model_display_name(result['modello_identificato'])

    return result


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def generate_html_report(results, config):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    filtered_results = results
    if not config.nsfw:
        filtered_results = [r for r in results if not (r.get('civitai_data') and r.get('civitai_data').get('nsfw', False))]
        print(f"\n🔞 NSFW excluded: {len(results) - len(filtered_results)} files hidden")

    total = len(filtered_results)
    found = sum(1 for r in filtered_results if r.get('civitai_data'))
    from_cache = sum(1 for r in filtered_results if r.get('from_cache', False))
    from_archive = sum(1 for r in filtered_results if r.get('from_civarchive', False))

    grid_cols = f"repeat({config.rows}, minmax({300 // config.rows}px, 1fr))"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Loras Indexer - Civitai Integration Report</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        h1 {{ color: #333; margin-bottom: 10px; }}
        .header {{ margin-bottom: 30px; padding-bottom: 20px; border-bottom: 3px solid #667eea; }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            border-radius: 15px;
            margin: 20px 0;
            color: white;
        }}
        .stat-card {{
            text-align: center;
            background: rgba(255,255,255,0.2);
            padding: 15px;
            border-radius: 10px;
            backdrop-filter: blur(10px);
        }}
        .stat-number {{ font-size: 32px; font-weight: bold; }}
        .stat-label {{ font-size: 14px; opacity: 0.9; margin-top: 5px; }}

        .search-container {{
            margin: 20px 0;
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        .search-input {{
            flex: 1;
            padding: 12px 18px;
            font-size: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            outline: none;
            transition: border-color 0.2s, box-shadow 0.2s;
            font-family: inherit;
        }}
        .search-input:focus {{
            border-color: #667eea;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.15);
        }}
        .search-input::placeholder {{
            color: #aaa;
        }}
        .search-count {{
            font-size: 13px;
            color: #666;
            font-weight: 600;
            min-width: 120px;
            text-align: right;
        }}
        .search-count.active {{
            color: #667eea;
        }}
        .card.hidden-by-search {{
            display: none;
        }}

        .grid {{
            display: grid;
            grid-template-columns: {grid_cols};
            gap: 20px;
            margin-top: 20px;
        }}

        .card {{
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            transition: transform 0.2s, box-shadow 0.2s;
            border-left: 4px solid #ddd;
        }}
        .card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 8px 20px rgba(0,0,0,0.15);
        }}
        .card.found {{ border-left-color: #4CAF50; }}
        .card.notfound {{ border-left-color: #f44336; }}
        .card.nsfw {{ border-left-color: #ff9800; }}
        .card.archive {{ border-left-color: #9c27b0; }}

        .card-header {{
            padding: 15px;
            background: #f8f9fa;
            border-bottom: 1px solid #e9ecef;
        }}
        .filename {{
            font-weight: bold;
            font-size: 14px;
            color: #495057;
            word-break: break-all;
            margin-bottom: 5px;
        }}
        .model-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            background: #e9ecef;
            color: #495057;
        }}
        .model-primary {{
            background: #667eea;
            color: white;
            font-size: 13px;
            padding: 4px 12px;
        }}
        .nsfw-badge {{
            background: #ff9800;
            color: white;
        }}
        .archive-badge {{
            background: #9c27b0;
            color: white;
        }}

        .card-body {{ padding: 15px; }}
        .info-row {{ margin: 8px 0; font-size: 13px; }}
        .info-label {{ font-weight: 600; color: #666; width: 100px; display: inline-block; }}
        .info-value {{ color: #333; word-break: break-all; }}

        .description {{
            margin: 8px 0;
            padding: 8px;
            background: #f8f9fa;
            border-radius: 6px;
            font-size: 12px;
            color: #666;
            max-height: 80px;
            overflow-y: auto;
            line-height: 1.4;
        }}

        .button-group {{
            margin-top: 12px;
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .url-link {{
            display: inline-block;
            padding: 6px 12px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-size: 12px;
            transition: background 0.2s;
        }}
        .url-link:hover {{ background: #5a67d8; }}
        .api-link {{
            background: #28a745;
        }}
        .api-link:hover {{ background: #218838; }}
        .fallback-link {{
            background: #ff9800;
        }}
        .fallback-link:hover {{ background: #f57c00; }}
        .archive-link {{
            background: #9c27b0;
        }}
        .archive-link:hover {{ background: #7b1fa2; }}

        .trigger-words {{
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin-top: 8px;
        }}
        .trigger {{
            background: #fff3e0;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            color: #e65100;
            cursor: pointer;
            transition: background 0.2s;
        }}
        .trigger:hover {{
            background: #ffe0b3;
        }}
        .trigger-more {{
            background: #e0e0e0;
            cursor: help;
            position: relative;
        }}
        .trigger-more:hover .tooltip {{
            visibility: visible;
            opacity: 1;
        }}
        .tooltip {{
            visibility: hidden;
            opacity: 0;
            position: absolute;
            bottom: 100%;
            left: 50%;
            transform: translateX(-50%);
            background: #333;
            color: white;
            text-align: center;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 11px;
            white-space: nowrap;
            z-index: 1000;
            transition: opacity 0.3s;
            margin-bottom: 5px;
            pointer-events: none;
        }}
        .tooltip::after {{
            content: "";
            position: absolute;
            top: 100%;
            left: 50%;
            margin-left: -5px;
            border-width: 5px;
            border-style: solid;
            border-color: #333 transparent transparent transparent;
        }}

        .preview {{
            margin-top: 12px;
            display: flex;
            gap: 8px;
            overflow-x: auto;
        }}
        .preview-item {{
            position: relative;
            display: inline-block;
        }}
        .preview img {{
            width: 80px;
            height: 80px;
            object-fit: cover;
            border-radius: 8px;
            cursor: pointer;
            transition: transform 0.2s;
        }}
        .preview img:hover {{ transform: scale(1.05); }}

        .prompt-badge {{
            position: absolute;
            bottom: 4px;
            right: 4px;
            background: #ff9800;
            color: white;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
            z-index: 10;
            transition: background 0.2s;
        }}
        .prompt-badge:hover {{
            background: #f57c00;
        }}

        .video-badge {{
            position: absolute;
            bottom: 4px;
            left: 4px;
            background: #2196F3;
            color: white;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
            z-index: 10;
            transition: background 0.2s;
            text-decoration: none;
        }}
        .video-badge:hover {{
            background: #1976D2;
        }}

        .modal {{
            display: none;
            position: fixed;
            z-index: 10000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.7);
            backdrop-filter: blur(5px);
        }}
        .modal-content {{
            background-color: white;
            margin: 10% auto;
            padding: 0;
            border-radius: 12px;
            width: 80%;
            max-width: 600px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            animation: modalFadeIn 0.3s;
        }}
        @keyframes modalFadeIn {{
            from {{ opacity: 0; transform: translateY(-50px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        .modal-header {{
            padding: 15px 20px;
            background: #ff9800;
            color: white;
            border-radius: 12px 12px 0 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .modal-header h3 {{
            margin: 0;
        }}
        .close {{
            color: white;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            transition: color 0.2s;
        }}
        .close:hover {{
            color: #ddd;
        }}
        .modal-body {{
            padding: 20px;
            max-height: 400px;
            overflow-y: auto;
        }}
        .modal-prompt {{
            background: #f5f5f5;
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 13px;
            white-space: pre-wrap;
            word-break: break-word;
            margin-bottom: 15px;
        }}
        .copy-prompt-btn {{
            background: #4CAF50;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 12px;
            transition: background 0.2s;
        }}
        .copy-prompt-btn:hover {{
            background: #45a049;
        }}

        .hash-info {{
            font-family: monospace;
            font-size: 10px;
            color: #999;
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid #e9ecef;
        }}

        .cache-badge {{
            font-size: 10px;
            color: #4caf50;
            margin-left: 8px;
        }}

        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #e9ecef;
            text-align: center;
            font-size: 12px;
            color: #999;
        }}

        @media (max-width: 768px) {{
            .grid {{ grid-template-columns: 1fr; }}
            .container {{ padding: 15px; }}
            .modal-content {{ width: 95%; margin: 20% auto; }}
        }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>📊 Loras Indexer - Safetensors Analysis Report</h1>
        <p><strong>Date:</strong> {timestamp}</p>
        <p><strong>Folder:</strong> {os.getcwd()}</p>
        <p><strong>Configuration:</strong> NSFW={'✅' if config.nsfw else '❌'} | Columns={config.rows} | Save Previews={'✅' if config.save_previews else '❌'} | Cookies={'✅' if config.use_firefox_cookies else '❌'}</p>
    </div>

    <div class="stats">
        <div class="stat-card">
            <div class="stat-number">{total}</div>
            <div class="stat-label">Files Analyzed</div>
        </div>
        <div class="stat-card">
            <div class="stat-number">{found}</div>
            <div class="stat-label">Found</div>
        </div>
        <div class="stat-card">
            <div class="stat-number">{from_cache}</div>
            <div class="stat-label">From JSON Cache</div>
        </div>
        <div class="stat-card">
            <div class="stat-number">{from_archive}</div>
            <div class="stat-label">From CivArchive</div>
        </div>
    </div>

    <div class="search-container">
        <input type="text" id="searchInput" class="search-input"
               placeholder="🔍 Search by name, model, or filename... (multiple terms = AND)"
               autocomplete="off" spellcheck="false">
        <span id="searchCount" class="search-count"></span>
    </div>

    <div class="grid" id="resultsGrid">
"""

    for r in filtered_results:
        civitai_data = r.get('civitai_data')
        is_nsfw = civitai_data.get('nsfw', False) if civitai_data else False
        from_archive_flag = r.get('from_civarchive', False)

        card_class = "found" if civitai_data else "notfound"
        if is_nsfw:
            card_class += " nsfw"
        if from_archive_flag:
            card_class += " archive"

        display_filename = r['filename']
        if len(display_filename) > 60:
            display_filename = display_filename[:57] + "..."

        search_parts = [r['filename'], r.get('modello_display', '')]
        if civitai_data:
            search_parts.append(civitai_data.get('nome_modello', ''))
            search_parts.append(civitai_data.get('modello_base', ''))
        search_text = ' '.join(p for p in search_parts if p).lower()
        search_text_escaped = search_text.replace('&', '&amp;').replace('"', '&quot;')

        html += f"""
        <div class="card {card_class}" data-search="{search_text_escaped}">
            <div class="card-header">
                <div class="filename">{display_filename}</div>
                <div>
                    <span class="model-badge model-primary">🎯 {r['modello_display']}</span>
                    <span class="model-badge">💾 {r['size_mb']:.1f} MB</span>
"""
        if r.get('from_cache'):
            html += '                    <span class="model-badge cache-badge">📦 Cache JSON</span>\n'
        if is_nsfw:
            html += '                    <span class="model-badge nsfw-badge">🔞 NSFW</span>\n'
        if from_archive_flag:
            html += '                    <span class="model-badge archive-badge">📚 CivArchive</span>\n'

        html += """                </div>
            </div>
            <div class="card-body">
"""

        if civitai_data:
            cd = civitai_data
            model_name = cd['nome_modello'][:60] if len(cd['nome_modello']) > 60 else cd['nome_modello']
            html += f"""
                <div class="info-row">
                    <span class="info-label">📛 Name:</span>
                    <span class="info-value">{model_name}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">🎨 Base model:</span>
                    <span class="info-value">{cd['modello_base']}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">📦 Type:</span>
                    <span class="info-value">{cd['tipo']}</span>
                </div>
"""
            if not from_archive_flag and cd.get('download_count', 0) > 0:
                html += f"""
                <div class="info-row">
                    <span class="info-label">📥 Downloads:</span>
                    <span class="info-value">{cd['download_count']:,}</span>
                </div>
"""

            if cd.get('descrizione'):
                desc_text = cd['descrizione'].replace('<', '&lt;').replace('>', '&gt;')
                html += f"""
                <div class="description">
                    📝 {desc_text}
                </div>
"""

            if cd.get('trained_words') and len(cd['trained_words']) > 0:
                html += '<div class="trigger-words">'
                for word in cd['trained_words'][:3]:
                    js_safe_word = json.dumps(word, ensure_ascii=False)
                    html_safe_word = js_safe_word.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                    html += f'<span class="trigger" data-copy="{html_safe_word}" onclick="copyFromElement(this)">🔑 {word}</span>'
                if len(cd['trained_words']) > 3:
                    remaining_words = cd['trained_words'][3:]
                    remaining_text = ', '.join(remaining_words)
                    js_safe_remaining = json.dumps(remaining_text, ensure_ascii=False)
                    html_safe_remaining = js_safe_remaining.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                    html += f'''
                    <span class="trigger trigger-more" data-copy="{html_safe_remaining}" onclick="copyFromElement(this)">... 
                        <span class="tooltip">Click to copy: {remaining_text}</span>
                    </span>'''
                html += '</div>'

            html += '<div class="button-group">'

            if cd.get('model_url') and cd['model_url']:
                html += f'<a href="{cd["model_url"]}" target="_blank" class="url-link">🔗 Model Page</a>'

            if cd.get('api_url') and not from_archive_flag:
                html += f'<a href="{cd["api_url"]}" target="_blank" class="url-link api-link">📡 API (Hash)</a>'

            if cd.get('fallback_url'):
                html += f' <a href="{cd["fallback_url"]}" target="_blank" class="url-link fallback-link">🌐 Fallback</a>'

            html += '</div>'

            if cd.get('previews') and len(cd['previews']) > 0:
                html += '<div class="preview">'
                for img in cd['previews']:
                    if img.get('url'):
                        prompt_text = img.get('prompt', '')
                        is_video = img.get('is_video', False)
                        video_page_url = img.get('video_page_url')

                        img_src = img.get('url', '')

                        html += f'''
                        <div class="preview-item">
                            <img src="{img_src}" alt="preview" loading="lazy" onclick="window.open(this.src)">'''

                        if prompt_text:
                            json_encoded = json.dumps(prompt_text, ensure_ascii=False)
                            html_safe = json_encoded.replace('&', '&amp;').replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                            html += f'''
                            <div class="prompt-badge" data-prompt="{html_safe}" onclick="showPromptFromElement(this, event)">📝</div>'''

                        if is_video and video_page_url:
                            html += f'''
                            <a href="{video_page_url}" target="_blank" class="video-badge">🔗</a>'''

                        html += '''
                        </div>'''
                html += '</div>'
        else:
            html += """
                <div class="info-row">
                    <span class="info-label">🌐 Search:</span>
                    <span class="info-value">❌ Not found</span>
                </div>
"""
            if r.get('hash', {}).get('autov3'):
                search_url = f"https://civitai.com/search?query={r['hash']['autov3']}"
                html += f'<a href="{search_url}" target="_blank" class="url-link">🔍 Search Civitai</a>'
            if r.get('hash', {}).get('sha256'):
                archive_url = f"https://civarchive.com/sha256/{r['hash']['sha256']}"
                html += f' <a href="{archive_url}" target="_blank" class="url-link archive-link">📚 Search CivArchive</a>'

        if r.get('hash'):
            html += '<div class="hash-info">🔑 '
            hash_parts = []
            for htype, hval in r['hash'].items():
                if hval:
                    if htype == 'autov3':
                        hash_parts.append(f'AutoV3: {hval}')
                    elif htype == 'sha256':
                        hash_parts.append(f'SHA256: {hval[:16]}...')
            html += ' '.join(hash_parts)
            html += '</div>'

        html += """
            </div>
        </div>
"""

    html += f"""
    </div>
    <div class="footer">
        Generated by <strong>Loras Indexer v{VERSION}</strong> by <strong>{AUTHOR}</strong>
    </div>
</div>

<div id="promptModal" class="modal">
    <div class="modal-content">
        <div class="modal-header">
            <h3>📝 Image/Video Prompt</h3>
            <span class="close" onclick="closeModal()">&times;</span>
        </div>
        <div class="modal-body">
            <div class="modal-prompt" id="modalPromptText"></div>
            <button class="copy-prompt-btn" onclick="copyModalPrompt()">📋 Copy Prompt</button>
        </div>
    </div>
</div>

<script>
let currentPrompt = '';
let modal = document.getElementById('promptModal');

// ---- Live search filter (multi-term AND logic) ----
const searchInput = document.getElementById('searchInput');
const searchCount = document.getElementById('searchCount');
const resultsGrid = document.getElementById('resultsGrid');

if (searchInput && resultsGrid) {{
    const allCards = Array.from(resultsGrid.querySelectorAll('.card'));
    const totalCards = allCards.length;

    function applyFilter() {{
        const query = searchInput.value.trim().toLowerCase();
        const terms = query.split(/\\s+/).filter(t => t.length > 0);
        let visibleCount = 0;

        for (const card of allCards) {{
            const haystack = card.getAttribute('data-search') || '';
            const matches = terms.length === 0 || terms.every(t => haystack.indexOf(t) !== -1);

            if (matches) {{
                card.classList.remove('hidden-by-search');
                visibleCount++;
            }} else {{
                card.classList.add('hidden-by-search');
            }}
        }}

        if (query) {{
            searchCount.textContent = visibleCount + ' / ' + totalCards + ' shown';
            searchCount.classList.add('active');
        }} else {{
            searchCount.textContent = '';
            searchCount.classList.remove('active');
        }}
    }}

    searchInput.addEventListener('input', applyFilter);

    searchInput.addEventListener('keydown', function(e) {{
        if (e.key === 'Escape') {{
            searchInput.value = '';
            applyFilter();
        }}
    }});
}}

function showPromptFromElement(element, event) {{
    event.stopPropagation();
    const rawAttr = element.getAttribute('data-prompt');
    if (rawAttr) {{
        let prompt = rawAttr;
        try {{
            prompt = JSON.parse(rawAttr);
        }} catch (e) {{
            console.warn('Could not parse prompt JSON:', e);
        }}
        currentPrompt = prompt;
        document.getElementById('modalPromptText').innerText = prompt;
        modal.style.display = 'block';
    }}
}}

function copyFromElement(element) {{
    const rawAttr = element.getAttribute('data-copy');
    if (rawAttr) {{
        let text = rawAttr;
        try {{
            text = JSON.parse(rawAttr);
        }} catch (e) {{
            console.warn('Could not parse copy JSON:', e);
        }}
        copyToClipboard(text);
    }}
}}

function closeModal() {{
    modal.style.display = 'none';
}}

function copyModalPrompt() {{
    copyToClipboard(currentPrompt);
}}

window.onclick = function(event) {{
    if (event.target == modal) {{
        closeModal();
    }}
}}

function copyToClipboard(text) {{
    text = text.replace(/^[🔑📝]\\s*/, '');
    navigator.clipboard.writeText(text).then(function() {{
        const notification = document.createElement('div');
        notification.textContent = '✓ Copied!';
        notification.style.position = 'fixed';
        notification.style.bottom = '20px';
        notification.style.right = '20px';
        notification.style.backgroundColor = '#4CAF50';
        notification.style.color = 'white';
        notification.style.padding = '10px 20px';
        notification.style.borderRadius = '5px';
        notification.style.zIndex = '10001';
        notification.style.fontSize = '14px';
        document.body.appendChild(notification);
        setTimeout(() => notification.remove(), 2000);
    }}, function(err) {{
        console.error('Copy error: ', err);
        alert('Error copying. Please try manually.');
    }});
}}
</script>
</body>
</html>"""
    return html


def print_summary(results, config):
    print("\n" + "=" * 80)
    print("📋 QUICK SUMMARY")
    print("=" * 80)

    for r in results:
        if not config.nsfw and r.get('civitai_data') and r.get('civitai_data').get('nsfw', False):
            continue

        status = "✅" if r.get('civitai_data') else "❌"
        cache_marker = "📦" if r.get('from_cache') else ("📚" if r.get('from_civarchive') else "🌐")

        civitai_data = r.get('civitai_data')
        is_nsfw = civitai_data.get('nsfw', False) if civitai_data else False
        nsfw_marker = "🔞" if is_nsfw else ""

        print(f"{status} {cache_marker} {nsfw_marker} {r['filename'][:50]:50} [{r['modello_display']}]")

    total = len(results)
    if not config.nsfw:
        total = sum(1 for r in results if not (r.get('civitai_data') and r.get('civitai_data').get('nsfw', False)))

    found = sum(1 for r in results if r.get('civitai_data'))
    if not config.nsfw:
        found = sum(1 for r in results if r.get('civitai_data') and not r.get('civitai_data').get('nsfw', False))

    from_cache = sum(1 for r in results if r.get('from_cache', False))
    from_archive = sum(1 for r in results if r.get('from_civarchive', False))
    print(f"\n🎯 Found: {found}/{total} (of which {from_cache} from cache, {from_archive} from CivArchive)")


# ---------------------------------------------------------------------------
# Propagate mode
# ---------------------------------------------------------------------------

def run_propagate():
    """
    Propagate mode:
    1. Copy the script + config into every subfolder (recursively)
    2. Run the script in each subfolder that contains .safetensors files
       (without -propagate, so it doesn't recurse further)
    """
    script_path = Path(__file__).resolve()
    script_name = script_path.name
    current_dir = Path.cwd()

    print("\n" + "=" * 80)
    print("🚀 PROPAGATE MODE")
    print("=" * 80)
    print(f"Script: {script_path}")
    print(f"Root folder: {current_dir}")
    print()

    # Find all subfolders (recursively) that contain at least one .safetensors file
    subfolders_with_lora = []
    for root, dirs, files in os.walk(current_dir):
        root_path = Path(root)
        if root_path == current_dir:
            continue
        if root_path.name in (JSON_DIR, PREVIEWS_DIR):
            continue

        has_safetensors = any(f.lower().endswith('.safetensors') for f in files)
        if has_safetensors:
            subfolders_with_lora.append(root_path)

    if not subfolders_with_lora:
        print("ℹ️ No subfolders with .safetensors files found. Nothing to propagate.")
        return

    print(f"📂 Found {len(subfolders_with_lora)} subfolder(s) with .safetensors files:\n")
    for folder in subfolders_with_lora:
        try:
            rel = folder.relative_to(current_dir)
            print(f"   • {rel}")
        except ValueError:
            print(f"   • {folder}")
    print()

    confirm = input(f"🚀 Proceed with propagation? [y/N]: ").strip().lower()
    if confirm not in ('y', 'yes'):
        print("❌ Propagation cancelled.")
        return

    print()

    # Copy script + config into each subfolder
    copied_script = 0
    copied_config = 0
    for folder in subfolders_with_lora:
        dest_script = folder / script_name
        try:
            shutil.copy2(script_path, dest_script)
            copied_script += 1
        except Exception as e:
            print(f"   ⚠️ Could not copy script to {folder}: {e}")

        config_src = current_dir / CONFIG_FILE
        if config_src.exists():
            dest_config = folder / CONFIG_FILE
            try:
                shutil.copy2(config_src, dest_config)
                copied_config += 1
            except Exception as e:
                print(f"   ⚠️ Could not copy config to {folder}: {e}")

    print(f"📄 Script copied to {copied_script} folder(s)")
    if copied_config:
        print(f"⚙️ Configuration copied to {copied_config} folder(s)")
    print()

    # Now run the script in each subfolder
    print("=" * 80)
    print("📡 RUNNING SCRIPT IN EACH SUBFOLDER")
    print("=" * 80)

    successful = 0
    failed = 0

    for idx, folder in enumerate(subfolders_with_lora, 1):
        try:
            rel = folder.relative_to(current_dir)
        except ValueError:
            rel = folder

        print(f"\n[{idx}/{len(subfolders_with_lora)}] Processing: {rel}")
        print("-" * 80)

        python_exe = sys.executable or 'python'

        try:
            result = subprocess.run(
                [python_exe, str(folder / script_name)],
                cwd=str(folder),
                capture_output=False,
                text=True
            )
            if result.returncode == 0:
                successful += 1
            else:
                print(f"   ⚠️ Script exited with code {result.returncode}")
                failed += 1
        except Exception as e:
            print(f"   ❌ Error running script: {e}")
            failed += 1

    print()
    print("=" * 80)
    print(f"🏁 PROPAGATION COMPLETE")
    print(f"   ✅ Successful: {successful}")
    print(f"   ❌ Failed: {failed}")
    print("=" * 80)


def main():
    # Print header with version
    print("=" * 80)
    print(f"📊 Loras Indexer v{VERSION} by {AUTHOR}")
    print("=" * 80)

    config = Config()
    refresh_from_cli, clean_from_cli, propagate = config.parse_args()

    current_dir = Path.cwd()
    safetensors_files = list(current_dir.glob("*.safetensors"))

    # Only create working directories if there is something to analyze.
    # The config file is always created/saved by Config.save_config() above.
    if safetensors_files:
        ensure_directories()
    else:
        print("❌ No .safetensors files found in the current folder.")
        if propagate:
            print("   → Propagate mode requested but no local files; running propagation only.")
            run_propagate()
            return
        else:
            return

    print(f"\n🔍 Found {len(safetensors_files)} .safetensors files")
    print(f"📁 JSON cache: {JSON_DIR}/")
    print(f"🖼️ Preview cache: {PREVIEWS_DIR}/")
    print(f"🔑 Hash cache: {CACHE_FILE}")
    print(f"📚 CivArchive fallback enabled")
    print(f"🎬 Video support: .mp4 -> .jpeg conversion for previews")

    if CURL_CFFI_AVAILABLE:
        print(f"🌐 curl_cffi available")
        if config.use_firefox_cookies:
            print(f"🍪 Firefox cookies enabled - NSFW descriptions will be fetched via TLS impersonation")
        else:
            print(f"🔒 Firefox cookies disabled (privacy default)")
            print(f"   → Enable with: --usefirefoxcookies yes")
    else:
        print(f"⚠️ curl_cffi NOT installed")
        if config.use_firefox_cookies:
            print(f"   → Firefox cookies requested but curl_cffi missing")
            print(f"   → Install with: pip install curl_cffi")
    print()

    if config.clean_previews:
        clean_corrupted_previews()

    hash_cache = HashCache()

    force_refresh = False

    if hash_cache.has_cache():
        if config.refresh == 'always':
            force_refresh = True
            print("🔄 Refresh mode: ALWAYS - recalculating all hashes")
        elif config.refresh == 'never':
            force_refresh = False
            print("📦 Refresh mode: NEVER - using cached hashes")
        elif config.refresh == 'yes':
            force_refresh = True
            print("🔄 Refresh mode: YES - recalculating hashes once")
        elif config.refresh == 'ask':
            answer = input("🔄 Force refresh (ignore all caches)? [y/N]: ").strip().lower()
            force_refresh = answer in ('y', 'yes')
            if force_refresh:
                print("   → User chose to refresh")
            else:
                print("   → Using cached hashes")
    else:
        print("📊 No existing cache - calculating hashes for all files")
        force_refresh = False

    print("\n📡 Analyzing...")
    results = []
    for i, filepath in enumerate(safetensors_files, 1):
        print(f"\n[{i}/{len(safetensors_files)}]", end=" ")
        result = analyze_file(filepath, hash_cache, config, force_refresh=force_refresh)
        results.append(result)

    hash_cache.save_cache()

    if refresh_from_cli and config.refresh == 'yes':
        config.refresh = 'never'
        config.save_config()
        print("⚙️ Refresh mode reset to 'never' for future runs")

    if clean_from_cli and config.clean_previews:
        config.clean_previews = False
        config.save_config()
        print("⚙️ Clean previews reset to 'no' for future runs")

    if not config.nsfw:
        original_count = len(results)
        results = [r for r in results if not (r.get('civitai_data') and r.get('civitai_data').get('nsfw', False))]
        print(f"\n🔞 NSFW excluded: {original_count - len(results)} files hidden")

    html_report = generate_html_report(results, config)
    html_filename = f"{SCRIPT_NAME}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    with open(html_filename, 'w', encoding='utf-8') as f:
        f.write(html_report)

    print_summary(results, config)

    print(f"\n✅ HTML report saved: {html_filename}")
    print(f"📁 JSON cache: {JSON_DIR}/")
    print(f"🖼️ Previews: {PREVIEWS_DIR}/")
    print(f"🔑 Hash cache: {CACHE_FILE}")
    print(f"⚙️ Configuration: {CONFIG_FILE}")

    if config.save_previews:
        print("💾 Previews have been saved locally and will be reused in future runs")

    # Propagate mode: after the current folder is done, process subfolders
    if propagate:
        run_propagate()


if __name__ == "__main__":
    try:
        import requests
    except ImportError:
        print("❌ The 'requests' library is required. Install it with: pip install requests")
        exit(1)

    main()