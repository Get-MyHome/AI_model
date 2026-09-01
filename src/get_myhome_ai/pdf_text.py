from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from get_myhome_ai.errors import (
    InvalidPdfError,
    InvalidPdfUrlError,
    PdfDownloadError,
    PdfTextExtractionError,
    PdfTextTimeoutError,
    PdfTextToolUnavailableError,
    PdfTooLargeError,
)
from get_myhome_ai.settings import Settings


@dataclass(frozen=True)
class DownloadedPdf:
    content: bytes
    sha256: str


@dataclass(frozen=True)
class PdfPage:
    number: int
    text: str


def _host_is_allowed(host: str, allowed_hosts: set[str]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(
        normalized == allowed.lstrip(".") or normalized.endswith(f".{allowed.lstrip('.')}")
        for allowed in allowed_hosts
    )


def _is_public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_public_host(host: str) -> bool:
    try:
        return _is_public_ip(host)
    except ValueError:
        pass

    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    resolved = {entry[4][0] for entry in addresses}
    return bool(resolved) and all(_is_public_ip(address) for address in resolved)


async def _validate_remote_url(url: str, settings: Settings) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise InvalidPdfUrlError("PDF URL은 공개 HTTPS 주소여야 합니다.")

    if settings.allowed_pdf_hosts:
        if not _host_is_allowed(parsed.hostname, settings.allowed_pdf_hosts):
            raise InvalidPdfUrlError("허용되지 않은 PDF 호스트입니다.")
        return

    if not await asyncio.to_thread(_resolve_public_host, parsed.hostname):
        raise InvalidPdfUrlError("사설망 또는 확인할 수 없는 PDF 호스트입니다.")


async def load_pdf_from_url(url: str, settings: Settings) -> DownloadedPdf:
    """Receive a crawler-provided pre-signed PDF URL.

    This function never discovers or scrapes announcement pages. The crawler owns that job.
    """
    current_url = url
    timeout = httpx.Timeout(settings.pdf_download_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for redirect_count in range(4):
            await _validate_remote_url(current_url, settings)
            try:
                async with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or redirect_count == 3:
                            raise PdfDownloadError("PDF 리디렉션을 완료하지 못했습니다.")
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code in {401, 403}:
                        raise PdfDownloadError("PDF 링크가 만료되었거나 접근이 거부되었습니다.")
                    response.raise_for_status()

                    declared_length = response.headers.get("content-length")
                    if declared_length and int(declared_length) > settings.pdf_max_bytes:
                        raise PdfTooLargeError("PDF가 허용된 최대 크기를 초과했습니다.")

                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > settings.pdf_max_bytes:
                            raise PdfTooLargeError("PDF가 허용된 최대 크기를 초과했습니다.")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content.startswith(b"%PDF-"):
                        raise InvalidPdfError("다운로드한 파일이 PDF 형식이 아닙니다.")
                    return DownloadedPdf(
                        content=content,
                        sha256=hashlib.sha256(content).hexdigest(),
                    )
            except (PdfTooLargeError, InvalidPdfError, PdfDownloadError):
                raise
            except (httpx.HTTPError, ValueError) as exc:
                raise PdfDownloadError("PDF를 다운로드하지 못했습니다.") from exc

    raise PdfDownloadError("PDF를 다운로드하지 못했습니다.")


def load_pdf_from_path(path: str, settings: Settings) -> DownloadedPdf:
    """Load a local PDF for batch development and golden-set evaluation only."""

    try:
        with open(path, "rb") as source:
            content = source.read(settings.pdf_max_bytes + 1)
    except OSError as exc:
        raise PdfDownloadError("로컬 PDF 파일을 읽지 못했습니다.") from exc
    if len(content) > settings.pdf_max_bytes:
        raise PdfTooLargeError("PDF가 허용된 최대 크기를 초과했습니다.")
    if not content.startswith(b"%PDF-"):
        raise InvalidPdfError("로컬 파일이 PDF 형식이 아닙니다.")
    return DownloadedPdf(content=content, sha256=hashlib.sha256(content).hexdigest())


def extract_pdf_pages(content: bytes, settings: Settings) -> list[PdfPage]:
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(content)
            source.flush()
            completed = subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", source.name, "-"],
                check=False,
                capture_output=True,
                timeout=settings.pdf_text_timeout_seconds,
            )
    except FileNotFoundError as exc:
        raise PdfTextToolUnavailableError(
            "서버에 pdftotext가 설치되어 있지 않습니다."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PdfTextTimeoutError("PDF 텍스트 추출 시간이 초과되었습니다.") from exc

    if completed.returncode != 0:
        raise PdfTextExtractionError("PDF 텍스트를 추출하지 못했습니다.")

    text = completed.stdout.decode("utf-8", errors="replace")
    raw_pages = text.split("\f")
    if raw_pages and not raw_pages[-1].strip():
        raw_pages.pop()
    if not raw_pages:
        raise PdfTextExtractionError("PDF에서 페이지 텍스트를 찾지 못했습니다.")
    if len(raw_pages) > settings.max_pdf_pages:
        raise PdfTextExtractionError("PDF 페이지 수가 허용 범위를 초과했습니다.")

    return [PdfPage(number=index, text=page) for index, page in enumerate(raw_pages, start=1)]
