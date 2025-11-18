import argparse
import datetime as dt
import sys
from typing import Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


HEADERS = {
    "User-Agent": "PutnamTextExtractor/1.0 (+https://github.com/)"
}


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def remove_unwanted_nodes(soup: BeautifulSoup) -> None:
    for selector in ["script", "style", "noscript", "header", "footer", "nav", "form"]:
        for node in soup.select(selector):
            node.decompose()


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
            return node
    body = soup.body
    if not body:
        raise ValueError("No <body> found in HTML.")
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
    lines: List[str] = []
    for child in content.children:
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
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    remove_unwanted_nodes(soup)
    content = find_main_content(soup)
    markdown = content_to_markdown(content, url)
    title_node = soup.find("title")
    title = title_node.get_text().strip() if title_node else None
    return format_output(url, markdown, title)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract clean text from a web article.")
    parser.add_argument("url", help="URL of the page to extract")
    parser.add_argument("-o", "--output", help="Write results to a file instead of stdout")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    try:
        output = extract(args.url)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fp:
            fp.write(output)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
