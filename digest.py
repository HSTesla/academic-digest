#!/usr/bin/env python3
"""Write a daily OpenAlex paper digest to an Obsidian folder."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import date, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


# Configuration
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY", "")
OPENALEX_EMAIL = os.environ.get("OPENALEX_EMAIL", "")
MAX_PAPERS = 20
WINDOW_DAYS = 7
OUTPUT_DIR = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "Obsidian"
    / "21_簡報"
)
BASE_DIR = Path(__file__).resolve().parent
SEEN_FILE = BASE_DIR / "seen.json"
API = "https://api.openalex.org"
CROSSREF_API = "https://api.crossref.org/works"
CROSSREF_REQUEST_INTERVAL = 1.1
PAGE_SIZE = 200

SUBFIELDS = {
    "3320": "政治學與國際關係",
    "2002": "經濟學",
    "3308": "法學",
    "3312": "社會學",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def request_json(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    request_params = dict(params)
    if OPENALEX_EMAIL:
        request_params["mailto"] = OPENALEX_EMAIL
    if OPENALEX_API_KEY:
        request_params["api_key"] = OPENALEX_API_KEY
    response = requests.get(
        f"{API}/{endpoint}",
        params=request_params,
        timeout=45,
    )
    if response.status_code in (401, 403):
        raise RuntimeError(
            "OpenAlex rejected the request. Set OPENALEX_API_KEY to a valid key "
            "from https://openalex.org/settings/api."
        )
    response.raise_for_status()
    return response.json()


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", value.strip(), flags=re.I)
    return doi.lower() or None


class _AbstractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize_abstract(value: str | None) -> str:
    if not value:
        return ""
    parser = _AbstractParser()
    parser.feed(unescape(value))
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def choose_abstract(openalex_abstract: str, crossref_abstract: str) -> tuple[str, str]:
    if len(crossref_abstract.split()) > len(openalex_abstract.split()):
        return crossref_abstract, "Crossref"
    if openalex_abstract:
        return openalex_abstract, "OpenAlex"
    return crossref_abstract, "Crossref"


def enrich_abstracts_from_crossref(works: list[dict[str, Any]]) -> None:
    """Fetch abstracts for selected works and retain Crossref when it is fuller."""
    for index, work in enumerate(works):
        doi = normalize_doi(work.get("doi"))
        if not doi:
            continue
        if index:
            time.sleep(CROSSREF_REQUEST_INTERVAL)
        headers = {"User-Agent": "academic-digest/1.0"}
        if OPENALEX_EMAIL:
            headers["User-Agent"] += f" (mailto:{OPENALEX_EMAIL})"
        try:
            response = requests.get(
                f"{CROSSREF_API}/{quote(doi, safe='')}",
                headers=headers,
                timeout=30,
            )
            if response.status_code == 404:
                logging.info("Crossref has no record for DOI %s", doi)
                continue
            response.raise_for_status()
            message = response.json().get("message") or {}
            work["crossref_abstract"] = normalize_abstract(message.get("abstract"))
        except requests.RequestException as error:
            logging.warning("Crossref lookup failed for DOI %s: %s", doi, error)


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(f"{SEEN_FILE} must contain a JSON array of DOI strings")
    return {item.lower() for item in data}


def fetch_works(start_date: date, end_date: date) -> list[dict[str, Any]]:
    subfield_ids = "|".join(f"https://openalex.org/subfields/{sid}" for sid in SUBFIELDS)
    filters = ",".join(
        (
            "type:article",
            "primary_location.source.type:journal",
            "doi:!null",
            "language:en|zh",
            f"from_publication_date:{start_date.isoformat()}",
            f"to_publication_date:{end_date.isoformat()}",
            f"primary_topic.subfield.id:{subfield_ids}",
        )
    )
    select = ",".join(
        (
            "id",
            "doi",
            "display_name",
            "publication_date",
            "language",
            "primary_location",
            "authorships",
            "cited_by_count",
            "primary_topic",
            "abstract_inverted_index",
        )
    )

    works: list[dict[str, Any]] = []
    cursor = "*"
    while cursor:
        data = request_json(
            "works",
            {
                "filter": filters,
                "select": select,
                "per-page": PAGE_SIZE,
                "cursor": cursor,
            },
        )
        works.extend(data.get("results", []))
        next_cursor = (data.get("meta") or {}).get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return works


def fetch_journal_metrics(works: list[dict[str, Any]]) -> dict[str, float]:
    source_ids = sorted(
        {
            source["id"].rsplit("/", 1)[-1]
            for work in works
            if (source := (work.get("primary_location") or {}).get("source"))
            and source.get("id")
        }
    )
    metrics: dict[str, float] = {}
    for offset in range(0, len(source_ids), 50):
        batch = source_ids[offset : offset + 50]
        data = request_json(
            "sources",
            {
                "filter": f"ids.openalex:{'|'.join(batch)}",
                "select": "id,summary_stats",
                "per-page": len(batch),
            },
        )
        for source in data.get("results", []):
            source_id = source["id"].rsplit("/", 1)[-1]
            citedness = (source.get("summary_stats") or {}).get("2yr_mean_citedness")
            metrics[source_id] = float(citedness or 0)
    return metrics


def restore_abstract(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions = [position for word_positions in index.values() for position in word_positions]
    words = [""] * (max(positions) + 1)
    for word, word_positions in index.items():
        for position in word_positions:
            words[position] = word
    return " ".join(word for word in words if word).strip()


def primary_subfield_id(work: dict[str, Any]) -> str | None:
    subfield = ((work.get("primary_topic") or {}).get("subfield") or {}).get("id")
    return subfield.rsplit("/", 1)[-1] if subfield else None


def markdown_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"([\\`*_{}\[\]()#+.!|>])", r"\\\1", value)


def render_paper(work: dict[str, Any]) -> str:
    title = markdown_text(work.get("display_name") or "Untitled")
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    journal = markdown_text(source.get("display_name") or "Unknown journal")
    authors = [
        author.get("author", {}).get("display_name")
        for author in work.get("authorships", [])
        if author.get("author", {}).get("display_name")
    ]
    author_text = ", ".join(markdown_text(name) for name in authors[:5])
    if len(authors) > 5:
        author_text += ", et al."
    author_text = author_text or "作者未提供"
    doi = normalize_doi(work.get("doi"))
    doi_link = f"[DOI](https://doi.org/{doi})" if doi else ""
    date_text = work.get("publication_date") or "日期未提供"
    abstract, abstract_source = choose_abstract(
        normalize_abstract(restore_abstract(work.get("abstract_inverted_index"))),
        work.get("crossref_abstract", ""),
    )
    return (
        f"### {title}\n"
        f"*{journal}｜{author_text}｜{date_text}｜{doi_link}*\n\n"
        f"> 摘要來源：{abstract_source}\n\n"
        f"{abstract}\n"
    )


def merge_daily_document(path: Path, day: date, papers: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in SUBFIELDS.values()}
    for work in papers:
        subfield_id = primary_subfield_id(work)
        section = SUBFIELDS.get(subfield_id or "")
        if section:
            grouped[section].append(work)

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        match = re.search(r"(?m)^count:\s*(\d+)\s*$", "\n".join(lines))
        count = int(match.group(1)) if match else 0
    else:
        count = 0
        lines = [
            "---",
            f"date: {day.isoformat()}",
            "tags: [學術簡報, 政治學, 經濟學, 法學, 社會學]",
            "count: 0",
            "---",
            "",
            f"# {day.isoformat()} 學術簡報",
            "",
            "> 摘要採 OpenAlex 與 Crossref 中較完整的版本；依期刊兩年平均被引次數排序，未經人工審閱。",
            "",
        ]

    additions = sum(len(items) for items in grouped.values())
    for section in SUBFIELDS.values():
        section_line = f"## {section}"
        try:
            start = lines.index(section_line)
        except ValueError:
            lines.extend(["", section_line, ""])
            start = len(lines) - 2
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        entries = [render_paper(work) for work in grouped[section]]
        if entries:
            while end > start + 1 and not lines[end - 1].strip():
                end -= 1
            lines[end:end] = [""] + [
                line for entry in entries for line in entry.rstrip().splitlines() + [""]
            ]

    text = "\n".join(lines).rstrip() + "\n"
    text = re.sub(r"(?m)^count:\s*\d+\s*$", f"count: {count + additions}", text, count=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def save_seen(seen: set[str]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=SEEN_FILE.parent, delete=False
    ) as temporary:
        json.dump(sorted(seen), temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(SEEN_FILE)


def main() -> None:
    if not OPENALEX_API_KEY or OPENALEX_API_KEY.startswith("PASTE_"):
        raise RuntimeError(
            "Set the OPENALEX_API_KEY environment variable before running. "
            "Create a free key at https://openalex.org/settings/api."
        )
    end_date = date.today()
    start_date = end_date - timedelta(days=WINDOW_DAYS - 1)
    seen = load_seen()

    logging.info("Querying OpenAlex from %s through %s", start_date, end_date)
    works = fetch_works(start_date, end_date)
    candidates: dict[str, dict[str, Any]] = {}
    for work in works:
        doi = normalize_doi(work.get("doi"))
        if doi and doi not in seen and primary_subfield_id(work) in SUBFIELDS:
            candidates.setdefault(doi, work)

    if not candidates:
        logging.info("No new papers; no digest file created.")
        return

    metrics = fetch_journal_metrics(list(candidates.values()))

    def rank(work: dict[str, Any]) -> tuple[float, int, str, str]:
        doi = normalize_doi(work.get("doi")) or ""
        source = ((work.get("primary_location") or {}).get("source") or {})
        source_id = source.get("id", "").rsplit("/", 1)[-1]
        return (
            metrics.get(source_id, 0),
            int(work.get("cited_by_count") or 0),
            work.get("publication_date") or "",
            doi,
        )

    selected = sorted(candidates.values(), key=rank, reverse=True)[:MAX_PAPERS]
    enrich_abstracts_from_crossref(selected)
    selected = [
        work
        for work in selected
        if choose_abstract(
            normalize_abstract(restore_abstract(work.get("abstract_inverted_index"))),
            work.get("crossref_abstract", ""),
        )[0]
    ]
    if not selected:
        logging.info("No selected papers have an abstract in OpenAlex or Crossref.")
        return

    output_path = OUTPUT_DIR / f"{end_date.isoformat()} 學術簡報.md"
    merge_daily_document(output_path, end_date, selected)
    seen.update(normalize_doi(work.get("doi")) for work in selected)
    save_seen(seen)
    logging.info("Wrote %d papers to %s", len(selected), output_path)


if __name__ == "__main__":
    main()
