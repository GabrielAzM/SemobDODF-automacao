from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import smtplib
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


SEMOB_TERMS = (
    "SEMOB",
    "SECRETARIA DE ESTADO DE TRANSPORTE E MOBILIDADE",
    "TRANSPORTE E MOBILIDADE",
)

DEFAULT_DODF_KEYWORDS = (
    "SEMOB",
    "SECRETARIA DE ESTADO DE TRANSPORTE E MOBILIDADE",
    "TRANSPORTE E MOBILIDADE",
    "MOBILIDADE",
    "METRO-DF",
    "METRO/DF",
    "METRÔ-DF",
    "METRÔ/DF",
    "COMPANHIA DO METROPOLITANO DO DISTRITO FEDERAL",
    "DFTRANS",
    "DER-DF",
    "DER/DF",
    "DEPARTAMENTO DE ESTRADAS DE RODAGEM",
    "DETRAN-DF",
    "DETRAN/DF",
    "DEPARTAMENTO DE TRÂNSITO",
    "TRANSPORTE PÚBLICO",
    "SISTEMA DE TRANSPORTE PÚBLICO COLETIVO",
    "STPC/DF",
    "BILHETAGEM",
    "TARIFAS E CONTROLE DE BILHETAGEM",
    "TRANSPORTES URBANOS",
    "TÉCNICO DE TRANSPORTES URBANOS",
    "SOCIEDADE DE TRANSPORTES COLETIVOS DE BRASÍLIA",
    "TCB",
)

GMAIL_API_SCOPES = ("https://www.googleapis.com/auth/gmail.send",)


@dataclass(frozen=True)
class Config:
    email_delivery: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    mail_from: str
    mail_to: tuple[str, ...]
    gmail_credentials_file: str
    gmail_token_file: str
    attach_pdf: bool
    max_attachment_mb: int
    send_empty_report: bool
    dodf_base_url: str
    timezone: str
    http_timeout_seconds: int
    max_retries: int
    retry_delay_seconds: int
    scan_full_diario: bool
    dodf_keywords: tuple[str, ...]
    relevant_snippets_only: bool
    relevant_context_lines: int

    @property
    def max_attachment_bytes(self) -> int:
        return self.max_attachment_mb * 1024 * 1024


@dataclass(frozen=True)
class PdfInfo:
    name: str
    url: str


@dataclass(frozen=True)
class PdfAttachment:
    filename: str
    content: bytes


@dataclass(frozen=True)
class PdfAttachmentResult:
    status: str
    attachment: PdfAttachment | None


@dataclass(frozen=True)
class Materia:
    code: str
    slug: str
    title: str
    section: str
    kind: str
    agency: str
    url: str
    full_text: str
    match_reason: str = ""
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiarioInfo:
    published_date: date | None
    timestamp: int | None
    pdfs: tuple[PdfInfo, ...]
    demandantes: dict[str, Any]


@dataclass(frozen=True)
class Report:
    diario: DiarioInfo
    materias: tuple[Materia, ...]
    pdf_attachment_result: PdfAttachmentResult | None


class DodfError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = html.unescape(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()
    return text.upper()


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "s", "on"}


def parse_recipients(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"[,;]", raw or "") if part.strip())


def parse_keywords(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None or not raw.strip():
        return default
    return tuple(part.strip() for part in re.split(r"[|;\n]", raw) if part.strip())


def matching_terms(text: Any, keywords: tuple[str, ...]) -> tuple[str, ...]:
    normalized = normalize_text(text)
    matches: list[str] = []
    for keyword in keywords:
        normalized_keyword = normalize_text(keyword)
        if normalized_keyword and normalized_keyword in normalized and keyword not in matches:
            matches.append(keyword)
    return tuple(matches)


def load_config(require_email: bool = True) -> Config:
    load_dotenv()

    email_delivery = os.getenv("EMAIL_DELIVERY", "smtp").strip().lower()
    if email_delivery not in {"smtp", "gmail_api"}:
        raise DodfError("EMAIL_DELIVERY deve ser 'smtp' ou 'gmail_api'.")

    smtp_user = os.getenv("SMTP_USER", "").strip()
    mail_from = os.getenv("MAIL_FROM", "").strip() or smtp_user

    config = Config(
        email_delivery=email_delivery,
        smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_user=smtp_user,
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        mail_from=mail_from,
        mail_to=parse_recipients(os.getenv("MAIL_TO", "")),
        gmail_credentials_file=os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json").strip(),
        gmail_token_file=os.getenv("GMAIL_TOKEN_FILE", "token.json").strip(),
        attach_pdf=parse_bool(os.getenv("ATTACH_PDF"), True),
        max_attachment_mb=int(os.getenv("MAX_ATTACHMENT_MB", "20")),
        send_empty_report=parse_bool(os.getenv("SEND_EMPTY_REPORT"), True),
        dodf_base_url=os.getenv("DODF_BASE_URL", "https://dodf.df.gov.br").rstrip("/"),
        timezone=os.getenv("TIMEZONE", "America/Sao_Paulo"),
        http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
        max_retries=max(1, int(os.getenv("MAX_RETRIES", "6"))),
        retry_delay_seconds=max(0, int(os.getenv("RETRY_DELAY_SECONDS", "300"))),
        scan_full_diario=parse_bool(os.getenv("SCAN_FULL_DIARIO"), True),
        dodf_keywords=parse_keywords(os.getenv("DODF_KEYWORDS"), DEFAULT_DODF_KEYWORDS),
        relevant_snippets_only=parse_bool(os.getenv("RELEVANT_SNIPPETS_ONLY"), True),
        relevant_context_lines=max(0, int(os.getenv("RELEVANT_CONTEXT_LINES", "0"))),
    )

    if require_email:
        missing = []
        if not config.mail_from:
            missing.append("MAIL_FROM")
        if not config.mail_to:
            missing.append("MAIL_TO")
        if config.email_delivery == "smtp":
            if not config.smtp_user:
                missing.append("SMTP_USER")
            if not config.smtp_password:
                missing.append("SMTP_PASSWORD")
        if config.email_delivery == "gmail_api":
            if not config.gmail_credentials_file:
                missing.append("GMAIL_CREDENTIALS_FILE")
            elif not Path(config.gmail_credentials_file).exists():
                missing.append(f"GMAIL_CREDENTIALS_FILE ({config.gmail_credentials_file} não encontrado)")
        if missing:
            raise DodfError(
                "Variáveis de email ausentes: "
                + ", ".join(missing)
                + ". Preencha no Render ou no arquivo .env."
            )

    return config


def extract_js_assignment(html_text: str, variable: str) -> str | None:
    match = re.search(rf"\bvar\s+{re.escape(variable)}\s*=", html_text)
    if not match:
        return None

    index = match.end()
    while index < len(html_text) and html_text[index].isspace():
        index += 1
    if index >= len(html_text):
        return None

    start = index
    opener = html_text[index]
    closer = {"{": "}", "[": "]"}.get(opener)

    if not closer:
        end = html_text.find(";", index)
        return html_text[start:end].strip() if end != -1 else None

    depth = 0
    in_string = False
    quote = ""
    escaped = False

    while index < len(html_text):
        char = html_text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
        else:
            if char in {'"', "'"}:
                in_string = True
                quote = char
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return html_text[start : index + 1].strip()
        index += 1

    return None


def extract_js_json(html_text: str, variable: str, default: Any = None) -> Any:
    raw = extract_js_assignment(html_text, variable)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DodfError(f"Não foi possível ler a variável JS {variable}: {exc}") from exc


def parse_dodf_date(data_value: Any) -> tuple[date | None, int | None]:
    if not isinstance(data_value, list) or len(data_value) < 2:
        return None, None

    raw_date = str(data_value[0])
    published = None
    if re.fullmatch(r"\d{8}", raw_date):
        published = datetime.strptime(raw_date, "%Y%m%d").date()

    timestamp = int(data_value[1]) if str(data_value[1]).isdigit() else None
    return published, timestamp


def build_pdf_url(base_url: str, pdf_link: str) -> str:
    base = base_url.rstrip("/")
    if pdf_link.startswith("http://") or pdf_link.startswith("https://"):
        return pdf_link

    if "|&arquivo=" in pdf_link:
        pasta, arquivo = pdf_link.split("|&arquivo=", 1)
        params = {"pasta": f"{pasta}|", "arquivo": arquivo}
        return f"{base}/dodf/jornal/visualizar-pdf?{urlencode(params)}"

    return f"{base}/dodf/jornal/visualizar-pdf?{urlencode({'pasta': pdf_link})}"


def parse_pdf_filename(pdf_url: str, fallback: str = "dodf.pdf") -> str:
    query = parse_qs(urlparse(pdf_url).query)
    arquivo = query.get("arquivo", [""])[0].strip()
    if arquivo:
        return arquivo
    return fallback or "dodf.pdf"


def collect_pdf_infos(base_url: str, lst_link_pdf: Any) -> tuple[PdfInfo, ...]:
    pdfs: list[PdfInfo] = []
    if not isinstance(lst_link_pdf, dict):
        return tuple(pdfs)

    for entries in lst_link_pdf.values():
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or not item.get("link"):
                continue
            url = build_pdf_url(base_url, str(item["link"]))
            name = str(item.get("nome") or parse_pdf_filename(url))
            pdfs.append(PdfInfo(name=name, url=url))
    return tuple(pdfs)


def collect_semob_codes(demandantes: dict[str, Any]) -> tuple[str, ...]:
    codes: list[str] = []

    def node_matches(node: dict[str, Any]) -> bool:
        values = [node.get("ds_nome", "")]
        rastreio = node.get("rastreio")
        if isinstance(rastreio, list):
            values.extend(rastreio)
        joined = normalize_text(" ".join(str(value) for value in values))
        return bool(matching_terms(joined, SEMOB_TERMS))

    def walk(items: dict[str, Any], inherited_match: bool = False) -> None:
        for code, raw_node in items.items():
            if not isinstance(raw_node, dict):
                continue
            current_match = inherited_match or node_matches(raw_node)
            if current_match and str(code) not in codes:
                codes.append(str(code))

            children = raw_node.get("filhos")
            if isinstance(children, dict):
                walk(children, current_match)

    walk(demandantes)
    return tuple(codes)


def materia_matches_semob(materia: dict[str, Any]) -> bool:
    poder = materia.get("poder", "")
    if isinstance(poder, list):
        text = " ".join(str(part) for part in poder)
    else:
        text = str(poder)
    return bool(matching_terms(text, SEMOB_TERMS))


def materia_search_text(materia: dict[str, Any]) -> str:
    poder = materia.get("poder", "")
    agency = " ".join(str(part) for part in poder) if isinstance(poder, list) else str(poder or "")
    values = [
        materia.get("titulo", ""),
        materia.get("ds_titulo", ""),
        materia.get("secao", ""),
        materia.get("ds_secao", ""),
        materia.get("tipo", ""),
        materia.get("ds_materia_tipo", ""),
        agency,
        materia.get("texto", ""),
    ]
    return "\n".join(str(value or "") for value in values)


def materia_url(base_url: str, materia: dict[str, Any]) -> str:
    code = str(materia.get("coMateria") or materia.get("co_materia") or "")
    slug = str(materia.get("slug") or "")
    path = f"/dodf/materia/visualizar?{urlencode({'co_data': code, 'p': slug})}"
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def clean_extracted_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def extract_full_text(materia_html: str) -> str:
    soup = BeautifulSoup(materia_html, "html.parser")
    content = soup.select_one(".conteudo-materia")
    if not content:
        return ""
    return clean_extracted_text(content.get_text("\n", strip=True))


def extract_relevant_blocks(text: str, keywords: tuple[str, ...], context_lines: int = 0) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    selected_indexes: set[int] = set()
    for index, line in enumerate(lines):
        if matching_terms(line, keywords):
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            selected_indexes.update(range(start, end))

    if not selected_indexes:
        return ""

    chunks: list[str] = []
    current: list[str] = []
    previous_index: int | None = None

    for index in sorted(selected_indexes):
        if previous_index is not None and index != previous_index + 1:
            chunks.append("\n".join(current))
            current = []
        current.append(lines[index])
        previous_index = index

    if current:
        chunks.append("\n".join(current))

    return "\n\n...\n\n".join(chunks)


def decide_pdf_attachment(filename: str, content: bytes, max_bytes: int) -> PdfAttachmentResult:
    if not content:
        return PdfAttachmentResult(status="PDF não foi baixado ou veio vazio.", attachment=None)

    size_mb = len(content) / (1024 * 1024)
    if len(content) > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        return PdfAttachmentResult(
            status=f"PDF não anexado: {size_mb:.1f} MB acima do limite de {limit_mb:.1f} MB.",
            attachment=None,
        )

    return PdfAttachmentResult(
        status=f"PDF anexado: {filename} ({size_mb:.1f} MB).",
        attachment=PdfAttachment(filename=filename, content=content),
    )


def to_materia(
    base_url: str,
    raw: dict[str, Any],
    full_text: str,
    match_reason: str = "",
    matched_terms: tuple[str, ...] = (),
) -> Materia:
    poder = raw.get("poder", "")
    agency = " > ".join(str(part) for part in poder) if isinstance(poder, list) else str(poder or "")
    return Materia(
        code=str(raw.get("coMateria") or raw.get("co_materia") or ""),
        slug=str(raw.get("slug") or ""),
        title=str(raw.get("titulo") or raw.get("ds_titulo") or "Matéria sem título"),
        section=str(raw.get("secao") or raw.get("ds_secao") or ""),
        kind=str(raw.get("tipo") or raw.get("ds_materia_tipo") or ""),
        agency=agency,
        url=materia_url(base_url, raw),
        full_text=full_text or clean_extracted_text(str(raw.get("texto") or "")),
        match_reason=match_reason,
        matched_terms=matched_terms,
    )


class DodfClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; dodf-semob-report/1.0; "
                    "+https://dodf.df.gov.br/)"
                ),
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
            }
        )

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.config.http_timeout_seconds,
                    **kwargs,
                )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                log(
                    f"Tentativa {attempt}/{self.config.max_retries} falhou ao acessar {url}. "
                    f"Nova tentativa em {self.config.retry_delay_seconds}s."
                )
                time.sleep(self.config.retry_delay_seconds)

        raise DodfError(f"Falha ao acessar {url}: {last_error}") from last_error

    def load_diario(self) -> DiarioInfo:
        url = urljoin(f"{self.config.dodf_base_url}/", "dodf/jornal/diario")
        response = self.request("GET", url)
        html_text = response.text

        published_date, timestamp = parse_dodf_date(extract_js_json(html_text, "data", []))
        pdfs = collect_pdf_infos(
            self.config.dodf_base_url,
            extract_js_json(html_text, "lstLinkPdf", {}),
        )

        demandantes = extract_js_json(html_text, "listaDemandantes", {})
        if not demandantes:
            demandantes = self._load_demandantes_from_post(timestamp)

        return DiarioInfo(
            published_date=published_date,
            timestamp=timestamp,
            pdfs=pdfs,
            demandantes=demandantes if isinstance(demandantes, dict) else {},
        )

    def _load_demandantes_from_post(self, timestamp: int | None) -> dict[str, Any]:
        if timestamp is None:
            return {}
        data = self.post_diario(timestamp, pagina=1)
        return (
            data.get("lstDemandantesMateria", {}).get("demandantes", {})
            if isinstance(data, dict)
            else {}
        )

    def post_diario(
        self,
        timestamp: int,
        pagina: int,
        tp_demandante: str = "",
    ) -> dict[str, Any]:
        url = urljoin(f"{self.config.dodf_base_url}/", "dodf/jornal/diario")
        payload = {"data": timestamp, "pagina": pagina}
        if tp_demandante:
            payload["tpDemandante"] = tp_demandante

        response = self.request(
            "POST",
            url,
            data=payload,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        return response.json()

    def fetch_filtered_materias(self, diario: DiarioInfo, codes: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
        if diario.timestamp is None:
            return tuple()

        raw_items: list[dict[str, Any]] = []
        first = self.post_diario(diario.timestamp, pagina=1, tp_demandante=",".join(codes))
        total_pages = int(first.get("totalPaginas") or 0)
        raw_items.extend(first.get("lstMaterias") or [])

        for pagina in range(2, total_pages + 1):
            page = self.post_diario(diario.timestamp, pagina=pagina, tp_demandante=",".join(codes))
            raw_items.extend(page.get("lstMaterias") or [])

        return dedupe_materias(tuple(raw_items))

    def fetch_all_materias(self, diario: DiarioInfo) -> tuple[dict[str, Any], ...]:
        if diario.timestamp is None:
            return tuple()

        raw_items: list[dict[str, Any]] = []
        first = self.post_diario(diario.timestamp, pagina=1)
        total_pages = int(first.get("totalPaginas") or 0)
        raw_items.extend(first.get("lstMaterias") or [])

        for pagina in range(2, total_pages + 1):
            page = self.post_diario(diario.timestamp, pagina=pagina)
            raw_items.extend(page.get("lstMaterias") or [])

        return dedupe_materias(tuple(raw_items))

    def fetch_full_text(self, raw_materia: dict[str, Any]) -> str:
        response = self.request("GET", materia_url(self.config.dodf_base_url, raw_materia))
        return extract_full_text(response.text)

    def download_pdf(self, pdf: PdfInfo) -> bytes:
        response = self.request("GET", pdf.url)
        return response.content

def dedupe_materias(items: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        code = str(item.get("coMateria") or item.get("co_materia") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        deduped.append(item)
    return tuple(deduped)


def build_report(config: Config) -> Report:
    log("Carregando Diário do Dia no DODF...")
    client = DodfClient(config)
    diario = client.load_diario()

    semob_codes = collect_semob_codes(diario.demandantes)
    log(f"Códigos SEMOB encontrados: {', '.join(semob_codes) if semob_codes else 'nenhum'}")
    raw_by_code: dict[str, dict[str, Any]] = {}
    demandante_codes: set[str] = set()

    if semob_codes and diario.timestamp is not None:
        log("Consultando publicações da SEMOB...")
        for item in client.fetch_filtered_materias(diario, semob_codes):
            code = str(item.get("coMateria") or item.get("co_materia") or "")
            if code:
                demandante_codes.add(code)
                raw_by_code[code] = item

    if config.scan_full_diario:
        log("Varrendo todas as matérias do DODF para buscar menções a SEMOB/mobilidade...")
        for item in client.fetch_all_materias(diario):
            code = str(item.get("coMateria") or item.get("co_materia") or "")
            if code and code not in raw_by_code:
                raw_by_code[code] = item

    raw_materias = tuple(raw_by_code.values())
    log(f"Matérias candidatas para análise: {len(raw_materias)}")
    materias: list[Materia] = []
    for index, item in enumerate(raw_materias, start=1):
        code = str(item.get("coMateria") or item.get("co_materia") or "")
        matched_by_demandante = code in demandante_codes
        metadata_terms = matching_terms(materia_search_text(item), config.dodf_keywords)

        if config.scan_full_diario and index == 1:
            log("Abrindo textos completos para conferir termos relacionados...")
        if config.scan_full_diario and index % 25 == 0:
            log(f"Analisadas {index}/{len(raw_materias)} matérias...")

        try:
            log(f"Extraindo texto completo da matéria {item.get('coMateria') or item.get('co_materia')}...")
            full_text = client.fetch_full_text(item)
        except Exception as exc:
            fallback = clean_extracted_text(str(item.get("texto") or ""))
            full_text = (
                fallback
                + "\n\n"
                + f"[Aviso: não foi possível abrir a página completa da matéria: {exc}]"
            ).strip()

        full_terms = matching_terms(materia_search_text(item) + "\n" + full_text, config.dodf_keywords)
        strict_semob_agency = materia_matches_semob(item)
        if not matched_by_demandante and not strict_semob_agency and not metadata_terms and not full_terms:
            continue

        matched_terms = tuple(dict.fromkeys(metadata_terms + full_terms))
        if matched_by_demandante or strict_semob_agency:
            report_text = full_text
            match_reason = "Demandante/órgão SEMOB"
        elif config.relevant_snippets_only:
            relevant_blocks = extract_relevant_blocks(
                full_text,
                config.dodf_keywords,
                context_lines=config.relevant_context_lines,
            )
            report_text = (
                "Trechos relevantes da matéria ampla:\n\n" + relevant_blocks
                if relevant_blocks
                else full_text
            )
            match_reason = "Menção textual no DODF"
        else:
            report_text = full_text
            match_reason = "Menção textual no DODF"

        materias.append(
            to_materia(
                config.dodf_base_url,
                item,
                report_text,
                match_reason=match_reason,
                matched_terms=matched_terms,
            )
        )

    log(f"Publicações relacionadas encontradas: {len(materias)}")

    pdf_attachment_result = None
    if config.attach_pdf and diario.pdfs:
        pdf = diario.pdfs[0]
        try:
            log("Baixando PDF do DODF para anexo...")
            content = client.download_pdf(pdf)
            filename = parse_pdf_filename(pdf.url, pdf.name)
            pdf_attachment_result = decide_pdf_attachment(filename, content, config.max_attachment_bytes)
        except Exception as exc:
            pdf_attachment_result = PdfAttachmentResult(
                status=f"PDF não anexado: falha ao baixar o arquivo ({exc}).",
                attachment=None,
            )

    return Report(
        diario=diario,
        materias=tuple(materias),
        pdf_attachment_result=pdf_attachment_result,
    )


def format_report_date(diario: DiarioInfo) -> str:
    if diario.published_date:
        return diario.published_date.strftime("%d/%m/%Y")
    return "data não identificada"


def plural_publicacoes(count: int) -> str:
    return "publicação" if count == 1 else "publicações"


def build_plain_body(report: Report) -> str:
    count = len(report.materias)
    lines = [
        f"DODF SEMOB - {format_report_date(report.diario)}",
        f"Publicações encontradas: {count} {plural_publicacoes(count)}",
        "",
    ]

    if report.diario.pdfs:
        lines.append(f"PDF do DODF: {report.diario.pdfs[0].url}")
    else:
        lines.append("PDF do DODF: não encontrado.")

    if report.pdf_attachment_result:
        lines.append(f"Anexo: {report.pdf_attachment_result.status}")

    lines.append("")

    if not report.materias:
        lines.append("Não foram encontradas publicações da SEMOB no Diário consultado.")
        return "\n".join(lines)

    for index, materia in enumerate(report.materias, start=1):
        lines.extend(
            [
                f"{index}. {materia.title}",
                f"Seção: {materia.section}",
                f"Tipo: {materia.kind}",
                f"Órgão: {materia.agency}",
                f"Origem do filtro: {materia.match_reason or 'SEMOB/mobilidade'}",
                f"Termos encontrados: {', '.join(materia.matched_terms) if materia.matched_terms else 'n/a'}",
                f"Link oficial: {materia.url}",
                "",
                "Texto:",
                materia.full_text or "Texto não disponível no HTML. Consulte o link oficial.",
                "",
                "-" * 72,
                "",
            ]
        )

    return "\n".join(lines).strip()


def html_paragraphs(text: str) -> str:
    escaped = html.escape(text or "")
    return "<br>".join(escaped.splitlines())


def build_html_body(report: Report) -> str:
    count = len(report.materias)
    date_label = html.escape(format_report_date(report.diario))
    pdf_html = (
        f'<a href="{html.escape(report.diario.pdfs[0].url)}">{html.escape(report.diario.pdfs[0].url)}</a>'
        if report.diario.pdfs
        else "não encontrado"
    )
    attachment_status = (
        html.escape(report.pdf_attachment_result.status)
        if report.pdf_attachment_result
        else "sem tentativa de anexo"
    )

    parts = [
        "<html><body>",
        f"<h2>DODF SEMOB - {date_label}</h2>",
        f"<p><strong>Publicações encontradas:</strong> {count} {plural_publicacoes(count)}</p>",
        f"<p><strong>PDF do DODF:</strong> {pdf_html}</p>",
        f"<p><strong>Anexo:</strong> {attachment_status}</p>",
    ]

    if not report.materias:
        parts.append("<p>Não foram encontradas publicações da SEMOB no Diário consultado.</p>")
    else:
        for index, materia in enumerate(report.materias, start=1):
            parts.extend(
                [
                    "<hr>",
                    f"<h3>{index}. {html.escape(materia.title)}</h3>",
                    "<ul>",
                    f"<li><strong>Seção:</strong> {html.escape(materia.section)}</li>",
                    f"<li><strong>Tipo:</strong> {html.escape(materia.kind)}</li>",
                    f"<li><strong>Órgão:</strong> {html.escape(materia.agency)}</li>",
                    f"<li><strong>Origem do filtro:</strong> {html.escape(materia.match_reason or 'SEMOB/mobilidade')}</li>",
                    f"<li><strong>Termos encontrados:</strong> {html.escape(', '.join(materia.matched_terms) if materia.matched_terms else 'n/a')}</li>",
                    f'<li><strong>Link oficial:</strong> <a href="{html.escape(materia.url)}">{html.escape(materia.url)}</a></li>',
                    "</ul>",
                    "<p><strong>Texto:</strong></p>",
                    (
                        '<div style="white-space: pre-wrap; font-family: Arial, sans-serif; line-height: 1.45;">'
                        + html_paragraphs(
                            materia.full_text
                            or "Texto não disponível no HTML. Consulte o link oficial."
                        )
                        + "</div>"
                    ),
                ]
            )

    parts.append("</body></html>")
    return "\n".join(parts)


def build_email_message(config: Config, report: Report) -> EmailMessage:
    count = len(report.materias)
    subject = (
        f"DODF SEMOB - {format_report_date(report.diario)} - "
        f"{count} {plural_publicacoes(count)}"
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.mail_from
    message["To"] = ", ".join(config.mail_to)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="dodf-semob-report.local")
    message.set_content(build_plain_body(report))
    message.add_alternative(build_html_body(report), subtype="html")

    attachment = report.pdf_attachment_result.attachment if report.pdf_attachment_result else None
    if attachment:
        message.add_attachment(
            attachment.content,
            maintype="application",
            subtype="pdf",
            filename=attachment.filename,
        )

    return message


def get_gmail_api_credentials(config: Config, interactive: bool = True):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise DodfError(
            "Dependências da Gmail API ausentes. Rode: "
            ".\\venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    token_path = Path(config.gmail_token_file)
    credentials_path = Path(config.gmail_credentials_file)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_API_SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not interactive:
            raise DodfError(
                f"Token Gmail API ausente ou inválido: {token_path}. "
                "Rode com --init-gmail-api para autenticar."
            )
        if not credentials_path.exists():
            raise DodfError(
                f"Arquivo {credentials_path} não encontrado. Baixe o OAuth Client JSON "
                "do Google Cloud e salve com esse nome."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_API_SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def init_gmail_api(config: Config) -> None:
    get_gmail_api_credentials(config, interactive=True)


def send_email_gmail_api(config: Config, message: EmailMessage) -> None:
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise DodfError(
            "Dependências da Gmail API ausentes. Rode: "
            ".\\venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from exc

    creds = get_gmail_api_credentials(config, interactive=True)
    try:
        service = build("gmail", "v1", credentials=creds)
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
    except HttpError as exc:
        raise DodfError(f"Falha ao enviar pela Gmail API: {exc}") from exc


def send_email_smtp(config: Config, message: EmailMessage) -> None:
    log(f"Conectando ao SMTP {config.smtp_host}:{config.smtp_port}...")
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.http_timeout_seconds) as smtp:
        log("Iniciando TLS...")
        smtp.starttls()
        log(f"Autenticando SMTP como {config.smtp_user}...")
        smtp.login(config.smtp_user, config.smtp_password)
        log(f"Enviando email para {', '.join(config.mail_to)}...")
        smtp.send_message(message)


def send_email(config: Config, message: EmailMessage) -> None:
    if config.email_delivery == "gmail_api":
        send_email_gmail_api(config, message)
        return
    send_email_smtp(config, message)


def print_dry_run(report: Report) -> None:
    count = len(report.materias)
    print(f"DODF SEMOB - {format_report_date(report.diario)}")
    print(f"Publicações encontradas: {count} {plural_publicacoes(count)}")
    if report.diario.pdfs:
        print(f"PDF: {report.diario.pdfs[0].url}")
    if report.pdf_attachment_result:
        print(f"Anexo: {report.pdf_attachment_result.status}")
    for index, materia in enumerate(report.materias, start=1):
        print()
        print(f"{index}. {materia.title}")
        print(f"   {materia.section} | {materia.kind} | {materia.agency}")
        print(f"   filtro: {materia.match_reason or 'SEMOB/mobilidade'}")
        if materia.matched_terms:
            print(f"   termos: {', '.join(materia.matched_terms)}")
        print(f"   {materia.url}")
        preview = materia.full_text[:500].replace("\n", " ")
        print(f"   {preview}{'...' if len(materia.full_text) > 500 else ''}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Envia relatório diário do DODF sobre publicações da SEMOB.")
    parser.add_argument("--dry-run", action="store_true", help="Coleta os dados e imprime um resumo sem enviar email.")
    parser.add_argument(
        "--init-gmail-api",
        action="store_true",
        help="Abre o login OAuth do Google e salva o token local para envio via Gmail API.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.init_gmail_api:
            config = load_config(require_email=False)
            init_gmail_api(config)
            print(f"Token Gmail API salvo em {config.gmail_token_file}.")
            return 0

        config = load_config(require_email=not args.dry_run)
        report = build_report(config)

        if not report.materias and not config.send_empty_report:
            print("Nenhuma publicação SEMOB encontrada e SEND_EMPTY_REPORT=false. Email não enviado.")
            return 0

        if args.dry_run:
            print_dry_run(report)
            return 0

        message = build_email_message(config, report)
        log(f"Modo de envio: {config.email_delivery}")
        send_email(config, message)
        print(f"Email enviado para {', '.join(config.mail_to)}.")
        return 0
    except Exception as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
