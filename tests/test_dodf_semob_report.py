from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

from dodf_semob_report import (
    Config,
    DEFAULT_DODF_KEYWORDS,
    DiarioInfo,
    PdfInfo,
    Report,
    build_email_message,
    build_pdf_url,
    collect_semob_codes,
    decide_pdf_attachment,
    extract_full_text,
    extract_relevant_blocks,
    mark_report_sent,
    matching_terms,
    report_already_sent,
)


def config() -> Config:
    return Config(
        email_delivery="smtp",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="sender@gmail.com",
        smtp_password="secret",
        mail_from="sender@gmail.com",
        mail_to=("dest@example.com",),
        gmail_credentials_file="credentials.json",
        gmail_token_file="token.json",
        attach_pdf=True,
        max_attachment_mb=20,
        send_empty_report=True,
        dodf_base_url="https://dodf.df.gov.br",
        timezone="America/Sao_Paulo",
        http_timeout_seconds=30,
        max_retries=1,
        retry_delay_seconds=0,
        scan_full_diario=True,
        dodf_keywords=DEFAULT_DODF_KEYWORDS,
        relevant_snippets_only=True,
        relevant_context_lines=0,
        sent_state_file="state/sent_reports.json",
        skip_already_sent=True,
    )


def test_collect_semob_codes_including_children() -> None:
    demandantes = {
        "889": {
            "ds_nome": "Secretaria de Estado de Transporte e Mobilidade",
            "filhos": {
                "1178": {"ds_nome": "Subsecretaria de Administração Geral", "rastreio": ["SEMOB"]},
                "3542": {"ds_nome": "Diretoria de Controle", "rastreio": ["SEMOB", "SUAG"]},
            },
        },
        "100": {
            "ds_nome": "Secretaria de Estado de Saúde",
            "filhos": {"101": {"ds_nome": "Unidade qualquer", "rastreio": ["SES"]}},
        },
    }

    assert collect_semob_codes(demandantes) == ("889", "1178", "3542")


def test_build_pdf_url_from_site_link() -> None:
    url = build_pdf_url(
        "https://dodf.df.gov.br",
        "2026|06_Junho|DODF 099 01-06-2026|&arquivo=DODF 099 01-06-2026 INTEGRA.pdf",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "dodf.df.gov.br"
    assert parsed.path == "/dodf/jornal/visualizar-pdf"
    assert query["pasta"] == ["2026|06_Junho|DODF 099 01-06-2026|"]
    assert query["arquivo"] == ["DODF 099 01-06-2026 INTEGRA.pdf"]


def test_extract_full_text_from_html() -> None:
    html = """
    <main>
      <div class="row conteudo-materia">
        <div class="col">
          <p style="text-align:justify;">Primeiro parágrafo da matéria.</p>
          <p style="text-align:center;">ASSINATURA</p>
        </div>
      </div>
    </main>
    """

    assert extract_full_text(html) == "Primeiro parágrafo da matéria.\nASSINATURA"


def test_text_matching_finds_exoneracao_from_broad_decree() -> None:
    text = """
    EXONERAR, por estar sendo nomeado para outro cargo, RICARDO CARVALHO SILVA
    do Cargo Publico de Natureza Especial, de Chefe, do Gabinete,
    da Secretaria de Estado de Transporte e Mobilidade do Distrito Federal.
    """

    matches = matching_terms(text, DEFAULT_DODF_KEYWORDS)

    assert "SECRETARIA DE ESTADO DE TRANSPORTE E MOBILIDADE" in matches


def test_relevant_blocks_extracts_only_matching_lines() -> None:
    text = "\n".join(
        [
            "EXONERAR pessoa de outro orgao.",
            "EXONERAR pessoa da Secretaria de Estado de Transporte e Mobilidade do Distrito Federal.",
            "NOMEAR pessoa de outro orgao.",
        ]
    )

    result = extract_relevant_blocks(text, DEFAULT_DODF_KEYWORDS)

    assert "Transporte e Mobilidade" in result
    assert "outro orgao" not in result


def test_email_without_results_mentions_empty_report() -> None:
    report = Report(
        diario=DiarioInfo(
            published_date=date(2026, 6, 1),
            timestamp=1780282800,
            pdfs=(PdfInfo(name="INTEGRA.pdf", url="https://dodf.df.gov.br/pdf"),),
            demandantes={},
        ),
        materias=(),
        pdf_attachment_result=None,
    )

    message = build_email_message(config(), report)
    body = message.get_body(preferencelist=("plain",)).get_content()

    assert message["Subject"] == "DODF SEMOB - 01/06/2026 - 0 publicações"
    assert "Não foram encontradas publicações da SEMOB" in body
    assert "https://dodf.df.gov.br/pdf" in body


def test_sent_state_marks_report_as_sent(tmp_path) -> None:
    test_config = config()
    test_config = Config(
        **{
            **test_config.__dict__,
            "sent_state_file": str(tmp_path / "sent_reports.json"),
        }
    )
    report = Report(
        diario=DiarioInfo(
            published_date=date(2026, 6, 2),
            timestamp=1780369200,
            pdfs=(),
            demandantes={},
        ),
        materias=(),
        pdf_attachment_result=None,
    )

    assert not report_already_sent(test_config, report)

    mark_report_sent(test_config, report)

    assert report_already_sent(test_config, report)


def test_pdf_attachment_limit() -> None:
    small = decide_pdf_attachment("dodf.pdf", b"x" * 10, max_bytes=20)
    large = decide_pdf_attachment("dodf.pdf", b"x" * 30, max_bytes=20)

    assert small.attachment is not None
    assert "PDF anexado" in small.status
    assert large.attachment is None
    assert "não anexado" in large.status
