# Loras Indexer 🔍

[![Version](https://img.shields.io/badge/version-1.0-blue.svg)]()
[![Python](https://img.shields.io/badge/Python-3.7+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Author](https://img.shields.io/badge/author-AnniCodex-purple.svg)]()

**Loras Indexer** is a Python tool that scans a folder full of `.safetensors` files (LoRAs, checkpoints, etc.), identifies the AI model each file belongs to, fetches metadata from Civitai and CivArchive, and generates a beautiful HTML gallery with previews, descriptions, trigger words and direct links to the original model pages.

> **v1.0 by AnniCodex**

### Quick start and recommended settings
1) Copy Loras_Indexer.py and Loras_Indexer.config.example inside your lora folder, rename Loras_Indexer.config.example to Loras_Indexer.config.
2) Open Loras_Indexer.config inside your favorite text editor and I recommend you set "save_previews": true, this will save previews locally so that the html gallery will work completely offline without downloading images everytime. You can leave the other settings as they are.
3) If you have all your loras in the current folder just double click Loras_Indexer and enjoy, if you have subfolders like lora\minimas, lora\zit etc you can open a command prompt inside lora\ and run Loras_Indexer.py -propagate.

Read below for detailed infos about each feature.

## ✨ Features

- 🔍 **Automatic model detection** using Civitai API, filename patterns, internal safetensors metadata, and folder names
- 🌐 **Civitai API integration** — fetches name, description, stats, trigger words, previews and more
- 📚 **CivArchive fallback** — automatically searches archived models when they are no longer on Civitai
- 🎬 **Video support** — converts `.mp4` previews to `.jpeg` thumbnails and adds a link to the video page
- 💾 **Persistent JSON cache** — one JSON per LoRA, never re-downloaded, and protected from accidental overwrite
- 🖼️ **Local preview caching** — optionally save previews to disk for offline viewing and faster reruns
- 🔞 **NSFW filtering** — hide NSFW models *and* NSFW previews of SFW models with a single flag
- 📐 **Customizable grid** — 1 to 3 columns in the HTML report
- 🔎 **Live search** — filter the HTML gallery in real-time with multi-term AND logic
- 🍪 **Optional Firefox cookie support** — for NSFW descriptions behind Cloudflare (opt-in, privacy-safe)
- 🔄 **Smart refresh modes** — control how often hashes are recalculated
- 🚀 **Propagate mode** — process an entire tree of subfolders in one command

## 🚀 Installation

### Prerequisites

- Python 3.7 or higher
- `pip` package manager

### Required library

```bash
pip install requests
```

### Optional: curl_cffi (for NSFW descriptions behind Cloudflare)

```bash
pip install curl_cffi
```

Without `curl_cffi`, the tool works fine — NSFW pages protected by Cloudflare will simply have empty descriptions (unless the description is available via the public API, which is often the case).

## 📖 Usage

### Basic usage

Navigate to the folder containing your `.safetensors` files and run:

```bash
python Loras_Indexer.py
```

The tool will:
1. Scan all `.safetensors` files in the current directory
2. Calculate AutoV3 and SHA256 hashes
3. Query Civitai API to identify each model
4. Fall back to CivArchive when needed
5. Generate an HTML report with all findings

### Command line options

```bash
# Exclude NSFW models (and NSFW previews of SFW models)
python Loras_Indexer.py --nsfw off

# Use 2 columns in the grid
python Loras_Indexer.py --rows 2

# Save previews locally (recommended)
python Loras_Indexer.py --savepreviews on

# Enable Firefox cookies for NSFW descriptions behind Cloudflare
python Loras_Indexer.py --usefirefoxcookies yes

# Force hash recalculation once, then set to 'never'
python Loras_Indexer.py --refresh yes

# Clean corrupted preview files
python Loras_Indexer.py --cleanpreviews yes

# Process all subfolders in one command (see "Propagate mode" below)
python Loras_Indexer.py -propagate

# Combine options
python Loras_Indexer.py --nsfw off --rows 2 --savepreviews on --usefirefoxcookies yes
```

### Refresh modes

The `--refresh` option controls hash recalculation:

| Value | Description |
|-------|-------------|
| `ask` | (Default) Prompts you at startup. Default answer is **No**. |
| `never` | Always uses cached hashes. Fastest option. |
| `yes` | Forces recalculation **once**, then automatically sets config to `never` for future runs. |
| `always` | Recalculates hashes **every** run. ⚠️ **Not recommended** — reduces SSD lifespan. |

**Note**: Refresh modes only apply when a hash cache already exists. On the first run in a new folder, hashes are always calculated.

### Propagate mode 🚀

If your LoRAs are organized in **subfolders**, `-propagate` saves you from running the script manually in each one.

```bash
python Loras_Indexer.py -propagate
```

What it does:
1. Processes the current folder as usual (generates its HTML report)
2. Scans all subfolders (recursively) that contain `.safetensors` files
3. Shows the list of subfolders and asks for confirmation
4. Copies the script and config into each subfolder
5. Runs the script in each subfolder (without `-propagate`, so it doesn't recurse further)
6. Prints a final summary of successful/failed runs

**Example structure:**

```
D:\AI\Models\
├── LoRA_MiniMax\
│   ├── minimax_h3_turbo.safetensors
│   └── minimax_h3_ref2v.safetensors
├── LoRA_ZImage\
│   └── zit_style.safetensors
└── LoRA_Flux\
    └── flux_klein_v2.safetensors
```

After `python Loras_Indexer.py -propagate`, each subfolder gets its own `Loras_Indexer_report_*.html`, `Loras_Indexer_Jsons/`, and `Loras_Indexer_Previews/`.

## ⚙️ Configuration file

The tool saves settings to `Loras_Indexer.config` for persistence:

```json
{
  "nsfw": true,
  "rows": 3,
  "save_previews": false,
  "refresh": "ask",
  "clean_previews": false,
  "use_firefox_cookies": false
}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `nsfw` | boolean | `true` | Include NSFW models and previews |
| `rows` | integer | `3` | Grid columns (1-3) |
| `save_previews` | boolean | `false` | Save previews locally |
| `refresh` | string | `"ask"` | Hash refresh mode (`ask`/`never`/`yes`/`always`) |
| `clean_previews` | boolean | `false` | Clean corrupted previews on startup |
| `use_firefox_cookies` | boolean | `false` | Extract cookies from Firefox for NSFW descriptions |

## 📁 Generated files

| File/Directory | Description |
|----------------|-------------|
| `Loras_Indexer_report_YYYYMMDD_HHMMSS.html` | Main HTML report |
| `Loras_Indexer_Cache.json` | Hash cache (avoids recalculation) |
| `Loras_Indexer.config` | User preferences |
| `Loras_Indexer_Jsons/` | Individual JSON data per LoRA |
| `Loras_Indexer_Previews/` | Locally saved preview images |

**Note**: `Loras_Indexer_Jsons/` and `Loras_Indexer_Previews/` are only created when there are `.safetensors` files to analyze. In an empty folder, only the `Loras_Indexer.config` file is created/updated.

## 🎯 Supported models

The tool automatically detects and classifies:

- **Z-Image** (Base & Turbo)
- **Flux** (Standard & Klein)
- **Wan 2.2**
- **LTX Video 2.3**
- **MiniMax H3** (aka Hailuo H3)
- **Krea 2**
- **SDXL**
- **Stable Diffusion 1.5 / 2.1**
- **Pony Diffusion**
- **PixArt-α**

Detection priority:

1. **Civitai / CivArchive data** (most reliable)
2. **Filename patterns** (e.g., `minimax_h3_...`, `krea2_...`)
3. **Internal safetensors metadata** (`ss_base_model_version`, `ss_sd_model_name`, etc.)
4. **Parent folder name** (last resort, ignoring generic names like `lora`, `models`, `downloads`)

## 🔞 NSFW handling

The tool has three levels of NSFW filtering:

1. **Model-level** (`--nsfw off`): hides entire cards for NSFW models
2. **Preview-level** (`--nsfw off`): skips previews with `nsfwLevel > 2` even on SFW models
3. **Description-level** (only when needed): uses Firefox cookies to fetch NSFW descriptions behind Cloudflare

### Firefox cookies (optional)

By default, the tool uses only the public Civitai API and public HTML pages. NSFW descriptions on `civitai.red` protected by Cloudflare will be empty.

To enable NSFW description fetching behind Cloudflare:

```bash
pip install curl_cffi
python Loras_Indexer.py --usefirefoxcookies yes
```

**What this does:**

- Extracts cookies for `.civitai.com` and `.civitai.red` from your local Firefox profile
- Uses them **only** for requests to `civitai.com` / `civitai.red`
- Never logs, saves, or transmits these cookies to any third party
- Requires you to be logged in to `civitai.red` in Firefox

**What this does NOT do:**

- Never reads cookies for other domains
- Never uploads cookies to any third-party service
- Never requires a Civitai API key or account credentials

The setting is **persistent**: once enabled, it stays enabled until you disable it with `--usefirefoxcookies no`.

## 🔍 Description fetching strategy

For each model, the tool tries to fetch the description in this order:

1. **Local JSON cache** (`_fetched_description` key) — no network request
2. **Civitai API** (`data['description']` → `data['model']['description']`)
3. **Public HTML page** (no cookies, no TLS impersonation)
4. **curl_cffi + Firefox cookies** (only if `use_firefox_cookies=yes`)

When a description is fetched from HTML, it is **persisted into the local JSON cache** so that future runs don't re-download the same page. This dramatically reduces network requests after the first run.

## 🔎 Live search in the HTML report

The generated HTML report includes a search bar at the top of the grid. Typing in it filters the visible cards in real time.

- **Single term**: `minimax` → shows all cards containing "minimax"
- **Multiple terms (AND)**: `flux klein` → shows only cards containing **both** "flux" and "klein"
- **Escape key**: clears the search and shows all cards
- **Counter**: shows "X / Y shown" while a search is active

The search matches against: filename, model display name, model name, and base model.

## 🛡️ Data preservation

The tool **never** overwrites a valid JSON cache with an empty or error marker. If a model is deleted from Civitai:

- The local JSON is preserved
- The local previews are preserved
- The HTML report continues to show all known metadata

Only corrupted files (e.g., non-image previews) are removed and re-downloaded.

## 🧪 How it works

### Hashing

- **AutoV3**: SHA256 of the safetensors file (skipping the header), truncated to 12 hex chars
- **SHA256**: full file SHA256, used for CivArchive fallback

### API endpoints

- `https://civitai.com/api/v1/model-versions/by-hash/{autov3}` — model lookup
- `https://civarchive.com/sha256/{sha256}` — archived model lookup

### Description parsing

Descriptions are extracted from the Next.js `__NEXT_DATA__` embedded JSON in the HTML page:

```regex
"id":<number>,"name":"...","description":"<p>...</p>"
```

This is more reliable than regex over the entire page, which can match meta tags or unrelated content.

### Preview validation

Before saving a preview, the tool validates:
- The HTTP status code (must be 200)
- The `Content-Type` header (must start with `image/`)
- The file magic bytes (JPEG, PNG, WebP, GIF, BMP)
- The file size (minimum 100 bytes)

Older versions of the script saved PNG files with a `.jpg` extension. The current version detects this case by inspecting the actual content, not the file extension.

## 🛠️ Troubleshooting

**"No .safetensors files found"**
- Make sure you're in the correct directory
- Check that files have the `.safetensors` extension

**"The 'requests' library is required"**
- Install it: `pip install requests`

**Descriptions are empty for NSFW models**
- The public API may not include the description
- Install `curl_cffi` and use `--usefirefoxcookies yes` to fetch from the NSFW page
- Make sure you're logged in to `civitai.red` in Firefox

**"Cloudflare challenge" errors**
- Make sure `curl_cffi` is installed
- Make sure your Firefox cookies are still valid (re-login if needed)

**Previews are missing or corrupted**
- Run with `--cleanpreviews yes` to remove and re-download corrupted files
- Check your internet connection and the Civitai/CDN availability

**Model identified as "Unknown"**
- The tool tries Civitai, CivArchive, filename, metadata, and folder name
- If all fail, the model is genuinely unidentifiable without opening the file
- You can inspect its metadata with: `python -c "from safetensors import safe_open; f = safe_open('model.safetensors', framework='pt'); print(f.metadata())"`

## 🐛 Reporting bugs / Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [Civitai](https://civitai.com) — for their excellent API and model database
- [CivArchive](https://civarchive.com) — for preserving deleted models
- [curl_cffi](https://github.com/yifeikong/curl_cffi) — for TLS impersonation
- All the amazing model creators in the community

---

**Made with ❤️ by AnniCodex and DeepSeek**
