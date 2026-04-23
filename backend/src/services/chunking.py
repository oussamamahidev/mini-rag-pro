"""Text extraction and chunking utilities for uploaded documents."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..logging_config import get_logger

logger = get_logger(__name__)

SUPPORTED_FILE_TYPES = {"pdf", "txt", "docx", "md"}
MIN_CHUNK_CHARS = 50


@dataclass(slots=True)
class ChunkData:
    """Serializable chunk payload produced by the chunking service."""

    text: str
    start_char: int
    end_char: int
    chunk_index: int
    page_number: int | None = None
    section_title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for Celery messages."""
        return asdict(self)


@dataclass(slots=True)
class PageSpan:
    """Character span for one extracted source page."""

    page_number: int
    start_char: int
    end_char: int


@dataclass(slots=True)
class ExtractionMetadata:
    """Metadata discovered during document text extraction."""

    title: str | None = None
    author: str | None = None
    language: str | None = None
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)
    page_spans: list[PageSpan] = field(default_factory=list)
    table_count: int = 0
    ocr_recommended: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable metadata payload."""
        payload = asdict(self)
        payload["page_spans"] = [asdict(span) for span in self.page_spans]
        return payload


@dataclass(slots=True)
class ProcessingResult:
    """Complete output from document processing."""

    text_chunks: list[ChunkData]
    page_count: int
    character_count: int
    chunk_count: int
    metadata: ExtractionMetadata
    warnings: list[str] = field(default_factory=list)

    def chunks_as_dicts(self) -> list[dict[str, Any]]:
        """Return chunk data formatted for Celery task arguments."""
        return [chunk.to_dict() for chunk in self.text_chunks]


class OCRExtractor(Protocol):
    """Extensible OCR interface for scanned PDF fallback implementations."""

    def extract_text(self, file_path: str) -> tuple[str, list[str]]:
        """Extract text from a scanned document and return warnings."""


class TableExtractor(Protocol):
    """Extensible table extraction interface for richer ingestion."""

    def extract_tables(self, file_path: str, file_type: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Extract table structures and return warnings."""


class DisabledOCRExtractor:
    """OCR provider used when no OCR engine is configured."""

    def extract_text(self, file_path: str) -> tuple[str, list[str]]:
        """Return no OCR text and explain that OCR is not enabled."""
        return "", [f"OCR fallback is not configured for {Path(file_path).name}"]


class NoopTableExtractor:
    """Table extractor used until a table-aware parser is configured."""

    def extract_tables(self, file_path: str, file_type: str) -> tuple[list[dict[str, Any]], list[str]]:
        """Return no tables without failing ingestion."""
        return [], []


class ChunkingService:
    """Extract raw document text and split it into overlapping chunks."""

    def __init__(
        self,
        *,
        ocr_extractor: OCRExtractor | None = None,
        table_extractor: TableExtractor | None = None,
    ) -> None:
        """Create a chunking service with optional extraction extensions."""
        self.ocr_extractor = ocr_extractor or DisabledOCRExtractor()
        self.table_extractor = table_extractor or NoopTableExtractor()

    def extract_text(self, file_path: str, file_type: str) -> tuple[str, int]:
        """
        Extract raw text from a document.

        Returns the full text and page count. Use process_document when metadata,
        extraction warnings, and page mappings are also needed.
        """
        text, metadata = self._extract_text_with_metadata(file_path, file_type)
        return text, metadata.page_count

    def chunk_text(
        self,
        text: str,
        chunk_size: int,
        chunk_overlap: int,
        *,
        page_spans: list[PageSpan] | None = None,
    ) -> list[ChunkData]:
        """Split text into overlapping, sentence-aware chunks."""
        normalized = normalize_text(text)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap cannot be negative")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        if len(normalized.strip()) < MIN_CHUNK_CHARS:
            return []

        sentence_spans = sentence_boundary_spans(normalized)
        if not sentence_spans:
            sentence_spans = [(0, len(normalized))]

        chunks: list[ChunkData] = []
        chunk_start: int | None = None
        chunk_end: int | None = None

        for sentence_start, sentence_end in sentence_spans:
            if sentence_end - sentence_start > chunk_size:
                if chunk_start is not None and chunk_end is not None:
                    self._append_chunk(chunks, normalized, chunk_start, chunk_end, page_spans)
                    chunk_start = None
                    chunk_end = None
                for split_start, split_end in split_long_span(
                    normalized,
                    sentence_start,
                    sentence_end,
                    chunk_size,
                    chunk_overlap,
                ):
                    self._append_chunk(chunks, normalized, split_start, split_end, page_spans)
                continue

            if chunk_start is None:
                chunk_start = sentence_start
                chunk_end = sentence_end
                continue

            candidate_end = sentence_end
            if candidate_end - chunk_start > chunk_size and chunk_end is not None:
                self._append_chunk(chunks, normalized, chunk_start, chunk_end, page_spans)
                chunk_start = max(chunk_end - chunk_overlap, 0) if chunk_overlap else sentence_start

            chunk_end = sentence_end

        if chunk_start is not None and chunk_end is not None:
            self._append_chunk(chunks, normalized, chunk_start, chunk_end, page_spans)

        for index, chunk in enumerate(chunks):
            chunk.chunk_index = index
        return chunks

    def process_document(
        self,
        file_path: str,
        file_type: str,
        chunk_size: int,
        chunk_overlap: int,
    ) -> ProcessingResult:
        """Extract text and chunk it for downstream embedding/indexing."""
        full_text, metadata = self._extract_text_with_metadata(file_path, file_type)
        chunks = self.chunk_text(
            full_text,
            chunk_size,
            chunk_overlap,
            page_spans=metadata.page_spans,
        )
        warnings = list(metadata.warnings)
        if not chunks:
            warnings.append("No usable chunks were produced from the extracted text")

        return ProcessingResult(
            text_chunks=chunks,
            page_count=metadata.page_count,
            character_count=len(full_text),
            chunk_count=len(chunks),
            metadata=metadata,
            warnings=warnings,
        )

    def _extract_text_with_metadata(self, file_path: str, file_type: str) -> tuple[str, ExtractionMetadata]:
        """Extract text, metadata, warnings, and source page spans."""
        normalized_type = file_type.lower().strip(".")
        if normalized_type not in SUPPORTED_FILE_TYPES:
            raise ValueError(f"unsupported file type: {file_type}")

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"file does not exist: {file_path}")

        if normalized_type == "pdf":
            text, metadata = self._extract_pdf(path)
        elif normalized_type == "docx":
            text, metadata = self._extract_docx(path)
        else:
            text, metadata = self._extract_plain_text(path)

        tables, table_warnings = self.table_extractor.extract_tables(str(path), normalized_type)
        metadata.table_count = len(tables)
        metadata.warnings.extend(table_warnings)
        if tables:
            metadata.warnings.append(f"Detected {len(tables)} table(s); table-aware indexing is available through metadata hooks")

        text = normalize_text(text)
        metadata.language = metadata.language or guess_language(text)
        return text, metadata

    def _extract_pdf(self, path: Path) -> tuple[str, ExtractionMetadata]:
        """Extract text and document metadata from a PDF file."""
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        metadata = ExtractionMetadata(page_count=len(reader.pages))
        raw_metadata = reader.metadata or {}
        metadata.title = clean_metadata_value(getattr(raw_metadata, "title", None) or raw_metadata.get("/Title"))
        metadata.author = clean_metadata_value(getattr(raw_metadata, "author", None) or raw_metadata.get("/Author"))

        page_texts: list[str] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                page_text = ""
                metadata.warnings.append(f"Failed to extract text from page {page_number}: {exc}")
            page_texts.append(page_text)

        cleaned_pages, repeated_warnings = remove_repeated_headers_footers(page_texts)
        metadata.warnings.extend(repeated_warnings)
        if cleaned_pages and average_non_space_chars(cleaned_pages) < 30:
            metadata.ocr_recommended = True
            ocr_text, ocr_warnings = self.ocr_extractor.extract_text(str(path))
            metadata.warnings.extend(ocr_warnings)
            if ocr_text.strip():
                cleaned_pages = [ocr_text]
                metadata.page_count = 1
            else:
                metadata.warnings.append("PDF text is sparse; OCR fallback is recommended")

        text, spans = join_pages(cleaned_pages)
        metadata.page_spans = spans
        return text, metadata

    def _extract_docx(self, path: Path) -> tuple[str, ExtractionMetadata]:
        """Extract paragraph text and core properties from a DOCX file."""
        from docx import Document as DocxDocument

        doc = DocxDocument(str(path))
        paragraphs = [paragraph.text for paragraph in doc.paragraphs if paragraph.text.strip()]
        text = "\n\n".join(paragraphs)
        metadata = ExtractionMetadata(page_count=1)
        metadata.title = clean_metadata_value(doc.core_properties.title)
        metadata.author = clean_metadata_value(doc.core_properties.author)
        metadata.page_spans = [PageSpan(page_number=1, start_char=0, end_char=len(text))]
        return text, metadata

    def _extract_plain_text(self, path: Path) -> tuple[str, ExtractionMetadata]:
        """Extract text from TXT and Markdown files."""
        text = path.read_text(encoding="utf-8", errors="replace")
        metadata = ExtractionMetadata(page_count=1)
        metadata.title = first_markdown_heading(text)
        metadata.page_spans = [PageSpan(page_number=1, start_char=0, end_char=len(normalize_text(text)))]
        return text, metadata

    def _append_chunk(
        self,
        chunks: list[ChunkData],
        source_text: str,
        start_char: int,
        end_char: int,
        page_spans: list[PageSpan] | None,
    ) -> None:
        """Append a chunk if it has enough useful text."""
        chunk_text = source_text[start_char:end_char].strip()
        if len(chunk_text) < MIN_CHUNK_CHARS:
            return

        adjusted_start = start_char + leading_whitespace_count(source_text[start_char:end_char])
        adjusted_end = adjusted_start + len(chunk_text)
        page_number = page_for_span(adjusted_start, adjusted_end, page_spans or [])
        section_title = nearest_section_title(source_text, adjusted_start)
        chunks.append(
            ChunkData(
                text=chunk_text,
                start_char=adjusted_start,
                end_char=adjusted_end,
                chunk_index=len(chunks),
                page_number=page_number,
                section_title=section_title,
                metadata={
                    "section_title": section_title,
                    "page_number": page_number,
                },
            )
        )


def normalize_text(text: str) -> str:
    """Normalize whitespace while preserving paragraph boundaries."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    normalized = re.sub(r"[ \t\v]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def sentence_boundary_spans(text: str) -> list[tuple[int, int]]:
    """Return approximate sentence spans without cutting common abbreviations perfectly."""
    spans: list[tuple[int, int]] = []
    pattern = re.compile(r".+?(?:[.!?]+(?=\s|$)|\n+|$)", re.DOTALL)
    for match in pattern.finditer(text):
        segment = match.group(0)
        if not segment.strip():
            continue
        start = match.start() + leading_whitespace_count(segment)
        end = match.end() - trailing_whitespace_count(segment)
        if end > start:
            spans.append((start, end))
    return spans


def split_long_span(
    text: str,
    start: int,
    end: int,
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[int, int]]:
    """Split a sentence-like span that is larger than chunk_size."""
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + chunk_size, end)
        if window_end < end:
            break_at = text.rfind(" ", cursor + max(1, chunk_size // 2), window_end)
            if break_at > cursor:
                window_end = break_at
        spans.append((cursor, window_end))
        if window_end >= end:
            break
        cursor = max(window_end - chunk_overlap, cursor + 1)
    return spans


def join_pages(page_texts: list[str]) -> tuple[str, list[PageSpan]]:
    """Join page text while recording character spans for citation mapping."""
    chunks: list[str] = []
    spans: list[PageSpan] = []
    cursor = 0
    for page_number, page_text in enumerate(page_texts, start=1):
        if chunks:
            chunks.append("\n\n")
            cursor += 2
        normalized = normalize_text(page_text)
        start = cursor
        chunks.append(normalized)
        cursor += len(normalized)
        spans.append(PageSpan(page_number=page_number, start_char=start, end_char=cursor))
    return "".join(chunks), spans


def remove_repeated_headers_footers(page_texts: list[str]) -> tuple[list[str], list[str]]:
    """Remove first/last lines repeated on many pages as likely headers or footers."""
    if len(page_texts) < 3:
        return page_texts, []

    first_lines: list[str] = []
    last_lines: list[str] = []
    split_pages: list[list[str]] = []
    for page_text in page_texts:
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        split_pages.append(lines)
        if lines:
            first_lines.append(lines[0])
            last_lines.append(lines[-1])

    threshold = max(2, math.ceil(len(page_texts) * 0.5))
    repeated = {
        line
        for line, count in (Counter(first_lines) + Counter(last_lines)).items()
        if count >= threshold and len(line) <= 120
    }
    if not repeated:
        return page_texts, []

    cleaned_pages = []
    for lines in split_pages:
        cleaned_pages.append("\n".join(line for line in lines if line not in repeated))
    return cleaned_pages, [f"Removed {len(repeated)} repeated header/footer line(s)"]


def page_for_span(start_char: int, end_char: int, page_spans: list[PageSpan]) -> int | None:
    """Find the page whose span overlaps the chunk midpoint."""
    if not page_spans:
        return None
    midpoint = start_char + max(0, end_char - start_char) // 2
    for span in page_spans:
        if span.start_char <= midpoint <= span.end_char:
            return span.page_number
    return page_spans[-1].page_number


def nearest_section_title(text: str, start_char: int) -> str | None:
    """Find a nearby heading-like line before a chunk for later citations."""
    prefix = text[:start_char]
    candidate_lines = [line.strip("# ").strip() for line in prefix.splitlines()[-12:]]
    for line in reversed(candidate_lines):
        if is_heading_like(line):
            return line[:160]
    return None


def is_heading_like(line: str) -> bool:
    """Return whether a line looks like a section heading."""
    if not line or len(line) > 120:
        return False
    if line.endswith((".", ",", ";", ":")):
        return False
    words = line.split()
    if len(words) > 12:
        return False
    uppercase_letters = sum(1 for char in line if char.isalpha() and char.isupper())
    letters = sum(1 for char in line if char.isalpha())
    return line.startswith("#") or bool(words and (line.istitle() or uppercase_letters >= max(2, letters // 2)))


def first_markdown_heading(text: str) -> str | None:
    """Return the first Markdown heading as a document title."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def clean_metadata_value(value: Any) -> str | None:
    """Normalize metadata values returned by document parsers."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def guess_language(text: str) -> str:
    """Return a lightweight language guess without external dependencies."""
    sample = text[:5000].lower()
    if not sample.strip():
        return "unknown"
    common_en = sum(sample.count(f" {word} ") for word in ("the", "and", "of", "to", "in", "is"))
    common_fr = sum(sample.count(f" {word} ") for word in ("le", "la", "les", "des", "et", "est"))
    common_es = sum(sample.count(f" {word} ") for word in ("el", "la", "los", "de", "que", "es"))
    if common_en >= common_fr and common_en >= common_es and common_en > 0:
        return "en"
    if common_fr >= common_es and common_fr > 0:
        return "fr"
    if common_es > 0:
        return "es"
    return "unknown"


def average_non_space_chars(page_texts: list[str]) -> float:
    """Return average non-whitespace characters per page."""
    if not page_texts:
        return 0.0
    return sum(len(re.sub(r"\s+", "", text)) for text in page_texts) / len(page_texts)


def leading_whitespace_count(text: str) -> int:
    """Return leading whitespace character count."""
    return len(text) - len(text.lstrip())


def trailing_whitespace_count(text: str) -> int:
    """Return trailing whitespace character count."""
    return len(text) - len(text.rstrip())

