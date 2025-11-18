import argparse
import datetime as dt
import logging
import sys
from functools import lru_cache
from io import BytesIO
from typing import Iterable, List, Optional
from urllib.parse import urljoin

import coloredlogs
import pytesseract
import requests
from PIL import Image
from bs4 import BeautifulSoup, NavigableString, Tag
from tqdm import tqdm


logger = logging.getLogger("extract_article")


HEADERS = {
    "User-Agent": "PutnamTextExtractor/1.0 (+https://github.com/)"
}


def configure_logging(level: int = logging.INFO) -> None:
    coloredlogs.install(level=level, logger=logger, fmt="%(levelname)s %(name)s - %(message)s")


def fetch_html(url: str) -> str:
    logger.info("Fetching HTML from %s", url)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    logger.info("Fetched %d bytes of HTML", len(response.text))
    return response.text


def remove_unwanted_nodes(soup: BeautifulSoup) -> None:
    for selector in ["script", "style", "noscript", "header", "footer", "nav", "form"]:
        removed = 0
        nodes = soup.select(selector)
        for node in tqdm(nodes, desc=f"Removing <{selector}> elements", leave=False):
            node.decompose()
            removed += 1
        if removed:
            logger.debug("Removed %d <%s> nodes", removed, selector)


CONTENT_SELECTORS = [
    "article",
    "main",
    "div.entry-content",
    "div.post-content",
    "div.article-content",
    "div#content",
]


def find_main_content(soup: BeautifulSoup) -> Tag:
    for selector in CONTENT_SELECTORS:
        node = soup.select_one(selector)
        if node:
            logger.info("Selected main content using selector '%s'", selector)
            return node
    body = soup.body
    if not body:
        raise ValueError("No <body> found in HTML.")
    logger.info("Falling back to <body> as main content container")
    return body


def inline_markdown(node: Tag | NavigableString, base_url: str) -> str:
    if isinstance(node, NavigableString):
        return str(node)

    name = node.name.lower()
    if name in {"em", "i", "cite"}:
        return f"*{''.join(inline_markdown(child, base_url) for child in node.children)}*"
    if name in {"strong", "b"}:
        return f"**{''.join(inline_markdown(child, base_url) for child in node.children)}**"
    if name == "code":
        return f"`{''.join(inline_markdown(child, base_url) for child in node.children)}`"
    if name == "a":
        href = node.get("href") or ""
        text = ''.join(inline_markdown(child, base_url) for child in node.children) or href
        absolute = urljoin(base_url, href) if href else ""
        if absolute:
            return f"[{text}]({absolute})"
        return text
    if name == "br":
        return "\n"

    return ''.join(inline_markdown(child, base_url) for child in node.children)


def fetch_image(url: str) -> Image.Image:
    logger.info("Fetching image from %s", url)
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return Image.open(BytesIO(response.content))


@lru_cache(maxsize=256)
def ocr_image(url: str) -> Optional[str]:
    try:
        with fetch_image(url) as img:
            max_dim = 2000
            if max(img.size) > max_dim:
                img.thumbnail((max_dim, max_dim))
                logger.debug("Resized image %s to fit within %d px", url, max_dim)

            if img.mode != "L":
                img = img.convert("L")
                logger.debug("Converted image %s to grayscale", url)

            text = pytesseract.image_to_string(img, config="--psm 6")
            cleaned = text.strip()
            logger.info("Extracted OCR text from %s", url)
            return cleaned or None
    except Exception:
        logger.warning("Failed to extract OCR text from %s", url, exc_info=True)
        return None


def image_markdown(node: Tag, base_url: str) -> List[str]:
    src = node.get("src") or node.get("data-src") or node.get("data-full-url")
    alt_text = (node.get("alt") or "").strip()
    lines: List[str] = []

    if src:
        absolute = urljoin(base_url, src)
        logger.info("Processing image node with source %s", absolute)
        ocr_text = ocr_image(absolute)
        if ocr_text:
            lines.append(ocr_text + "\n")
        else:
            logger.debug("No OCR text extracted for %s", absolute)

    if alt_text:
        lines.append(alt_text + "\n")

    return lines


def block_markdown(node: Tag, base_url: str, indent: int = 0) -> List[str]:
    lines: List[str] = []
    name = node.name.lower()

    heading_levels = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    if name in heading_levels:
        prefix = "#" * heading_levels[name]
        text = ''.join(inline_markdown(child, base_url) for child in node.children).strip()
        if text:
            lines.append(f"{prefix} {text}\n")
        return lines

    if name == "p":
        text = ''.join(inline_markdown(child, base_url) for child in node.children).strip()
        if text:
            lines.append(f"{text}\n")
        return lines

    if name == "blockquote":
        block_lines: List[str] = []
        for child in node.children:
            if isinstance(child, NavigableString):
                child_text = str(child).strip()
                if child_text:
                    block_lines.append(child_text)
            elif isinstance(child, Tag):
                block_lines.extend(block_markdown(child, base_url, indent))
        if block_lines:
            quoted = "\n".join(f"> {line}" for line in block_lines if line.strip())
            lines.append(f"{quoted}\n")
        return lines

    if name in {"ul", "ol"}:
        is_ordered = name == "ol"
        count = 1
        for child in node.find_all("li", recursive=False):
            bullet = f"{count}. " if is_ordered else "- "
            child_lines = render_list_item(child, base_url, indent, bullet)
            lines.extend(child_lines)
            count += 1
        lines.append("")
        return lines

    if name == "pre":
        code_text = ''.join(child if isinstance(child, NavigableString) else child.get_text() for child in node.children)
        lines.append("```\n" + code_text.strip("\n") + "\n```\n")
        return lines

    if name == "figure":
        inner_lines: List[str] = []
        for child in node.children:
            if isinstance(child, Tag):
                if child.name == "figcaption":
                    caption = child.get_text().strip()
                    if caption:
                        inner_lines.append(f"*{caption}*\n")
                else:
                    inner_lines.extend(block_markdown(child, base_url, indent))
        lines.extend(inner_lines)
        return lines

    if name == "img":
        lines.extend(image_markdown(node, base_url))
        return lines

    # Fallback: process children
    for child in node.children:
        if isinstance(child, NavigableString):
            child_text = str(child).strip()
            if child_text:
                lines.append(child_text + "\n")
        elif isinstance(child, Tag):
            lines.extend(block_markdown(child, base_url, indent))

    return lines


def render_list_item(item: Tag, base_url: str, indent: int, bullet: str) -> List[str]:
    lines: List[str] = []
    prefix = "  " * indent + bullet
    content_lines: List[str] = []
    for child in item.children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                content_lines.append(text)
        elif isinstance(child, Tag):
            if child.name in {"ul", "ol"}:
                nested = block_markdown(child, base_url, indent + 1)
                content_lines.extend(nested)
            else:
                content_lines.append(''.join(inline_markdown(grandchild, base_url) for grandchild in child.children))
    if content_lines:
        lines.append(prefix + content_lines[0].strip())
        for extra in content_lines[1:]:
            lines.append("  " * (indent + 1) + extra.strip())
    return lines


def content_to_markdown(content: Tag, base_url: str) -> str:
    logger.info("Converting main content to markdown")
    lines: List[str] = []
    children = list(content.children)
    for child in tqdm(children, desc="Processing content blocks", leave=False):
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                lines.append(text + "\n")
        elif isinstance(child, Tag):
            lines.extend(block_markdown(child, base_url))
    cleaned = [line.rstrip() for line in lines]
    return "\n".join(cleaned).strip() + "\n"


def format_output(url: str, markdown: str, title: Optional[str]) -> str:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    header = [
        f"Title: {title or 'Untitled'}",
        f"Source: {url}",
        f"Retrieved: {now}",
        "",
    ]
    return "\n".join(header) + markdown


def extract(url: str) -> str:
    logger.info("Starting extraction for %s", url)
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    remove_unwanted_nodes(soup)
    content = find_main_content(soup)
    markdown = content_to_markdown(content, url)
    title_node = soup.find("title")
    title = title_node.get_text().strip() if title_node else None
    logger.info("Extraction complete for %s", url)
    return format_output(url, markdown, title)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract clean text from a web article.")
    parser.add_argument("url", help="URL of the page to extract")
    parser.add_argument("-o", "--output", help="Write results to a file instead of stdout")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    configure_logging()
    args = parse_args(argv)
    try:
        output = extract(args.url)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        logger.info("Writing output to %s", args.output)
        with open(args.output, "w", encoding="utf-8") as fp:
            fp.write(output)
    else:
        logger.info("Writing output to stdout")
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
