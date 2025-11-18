# Putnam Text Extractor

A small utility to extract clean, markdown-formatted text from web articles. The script fetches the HTML, removes boilerplate elements, and converts the main content into readable text.

## Setup

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python extract_article.py https://www.peterputnam.org/outline-of-a-functional-model-of-the-nervous-system-putnam/fuller-1964 -o output.md
```

- The `-o/--output` flag is optional; without it, the markdown is printed to stdout.
- Output includes the page title, source URL, and retrieval timestamp in UTC.
