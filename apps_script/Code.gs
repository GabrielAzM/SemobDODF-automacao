const CONFIG = {
  BASE_URL: 'https://dodf.df.gov.br',
  MAIL_TO: 'thaysdiasr@gmail.com',
  MAIL_FROM_LABEL: 'DODF SEMOB',
  ATTACH_PDF: true,
  MAX_ATTACHMENT_MB: 20,
  SEND_EMPTY_REPORT: true,
  TIMEZONE: 'America/Sao_Paulo',
  SEMOB_TERMS: [
    'SEMOB',
    'SECRETARIA DE ESTADO DE TRANSPORTE E MOBILIDADE',
    'TRANSPORTE E MOBILIDADE',
  ],
};

function sendDodfSemobReport() {
  console.log('Carregando Diario do Dia no DODF...');
  const diario = loadDiario();
  const semobCodes = collectSemobCodes(diario.demandantes);
  console.log(`Codigos SEMOB encontrados: ${semobCodes.join(', ') || 'nenhum'}`);

  const rawMaterias = diario.timestamp && semobCodes.length
    ? fetchFilteredMaterias(diario.timestamp, semobCodes).filter(materiaMatchesSemob)
    : [];
  console.log(`Publicacoes SEMOB encontradas: ${rawMaterias.length}`);

  const materias = rawMaterias.map((item) => {
    console.log(`Extraindo texto completo da materia ${item.coMateria || item.co_materia}...`);
    let fullText = '';
    try {
      fullText = fetchFullText(item);
    } catch (err) {
      fullText = `${cleanText(item.texto || '')}\n\n[Aviso: nao foi possivel abrir a pagina completa da materia: ${err}]`;
    }
    return toMateria(item, fullText);
  });

  if (!materias.length && !CONFIG.SEND_EMPTY_REPORT) {
    console.log('Nenhuma publicacao SEMOB encontrada e SEND_EMPTY_REPORT=false. Email nao enviado.');
    return;
  }

  const report = { diario, materias };
  const pdfAttachment = getPdfAttachment(diario);
  const subject = `DODF SEMOB - ${formatReportDate(diario)} - ${materias.length} ${pluralPublicacoes(materias.length)}`;

  MailApp.sendEmail({
    to: CONFIG.MAIL_TO,
    subject,
    name: CONFIG.MAIL_FROM_LABEL,
    body: buildPlainBody(report, pdfAttachment.status),
    htmlBody: buildHtmlBody(report, pdfAttachment.status),
    attachments: pdfAttachment.blob ? [pdfAttachment.blob] : [],
  });

  console.log(`Email enviado para ${CONFIG.MAIL_TO}.`);
}

function setupDailyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter((trigger) => trigger.getHandlerFunction() === 'sendDodfSemobReport')
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));

  ScriptApp.newTrigger('sendDodfSemobReport')
    .timeBased()
    .atHour(6)
    .nearMinute(30)
    .everyDays(1)
    .inTimezone(CONFIG.TIMEZONE)
    .create();

  console.log('Trigger diario criado para aproximadamente 06:30 em America/Sao_Paulo.');
}

function loadDiario() {
  const html = fetchText(`${CONFIG.BASE_URL}/dodf/jornal/diario`);
  const data = extractJsJson(html, 'data', []);
  const lstLinkPdf = extractJsJson(html, 'lstLinkPdf', {});
  const demandantes = extractJsJson(html, 'listaDemandantes', {});
  const parsedDate = parseDodfDate(data);

  return {
    publishedDate: parsedDate.publishedDate,
    timestamp: parsedDate.timestamp,
    pdfs: collectPdfInfos(lstLinkPdf),
    demandantes,
  };
}

function fetchFilteredMaterias(timestamp, codes) {
  const first = postDiario(timestamp, 1, codes);
  const totalPages = Number(first.totalPaginas || 0);
  let items = first.lstMaterias || [];

  for (let page = 2; page <= totalPages; page += 1) {
    const result = postDiario(timestamp, page, codes);
    items = items.concat(result.lstMaterias || []);
  }

  return dedupeMaterias(items);
}

function postDiario(timestamp, pagina, codes) {
  const payload = {
    data: String(timestamp),
    pagina: String(pagina),
    tpDemandante: codes.join(','),
  };
  const headers = defaultHeaders();
  headers['X-Requested-With'] = 'XMLHttpRequest';

  const response = UrlFetchApp.fetch(`${CONFIG.BASE_URL}/dodf/jornal/diario`, {
    method: 'post',
    payload,
    muteHttpExceptions: true,
    headers,
  });

  if (response.getResponseCode() >= 400) {
    throw new Error(`Falha no POST do DODF: HTTP ${response.getResponseCode()}`);
  }

  return JSON.parse(response.getContentText());
}

function fetchFullText(item) {
  const html = fetchText(materiaUrl(item));
  const match = html.match(/<div class="row conteudo-materia">([\s\S]*?)<p class="d-flex justify-content-center box-visualizar-jornal">/i);
  if (!match) return cleanExtractedText(item.texto || '');
  return cleanExtractedText(stripTags(match[1]));
}

function getPdfAttachment(diario) {
  if (!CONFIG.ATTACH_PDF || !diario.pdfs.length) {
    return { status: 'sem tentativa de anexo', blob: null };
  }

  const pdf = diario.pdfs[0];
  try {
    console.log('Baixando PDF do DODF para anexo...');
    const response = UrlFetchApp.fetch(pdf.url, {
      muteHttpExceptions: true,
      headers: defaultHeaders(),
    });
    if (response.getResponseCode() >= 400) {
      return { status: `PDF nao anexado: HTTP ${response.getResponseCode()}`, blob: null };
    }

    const blob = response.getBlob().setName(parsePdfFilename(pdf.url, pdf.name));
    const sizeMb = blob.getBytes().length / (1024 * 1024);
    if (sizeMb > CONFIG.MAX_ATTACHMENT_MB) {
      return {
        status: `PDF nao anexado: ${sizeMb.toFixed(1)} MB acima do limite de ${CONFIG.MAX_ATTACHMENT_MB} MB.`,
        blob: null,
      };
    }
    return { status: `PDF anexado: ${blob.getName()} (${sizeMb.toFixed(1)} MB).`, blob };
  } catch (err) {
    return { status: `PDF nao anexado: falha ao baixar o arquivo (${err}).`, blob: null };
  }
}

function collectSemobCodes(demandantes) {
  const codes = [];

  function nodeMatches(node) {
    const values = [node.ds_nome || ''];
    if (Array.isArray(node.rastreio)) values.push(...node.rastreio);
    const joined = normalizeText(values.join(' '));
    return CONFIG.SEMOB_TERMS.some((term) => joined.indexOf(term) !== -1);
  }

  function walk(items, inheritedMatch) {
    Object.keys(items || {}).forEach((code) => {
      const node = items[code];
      if (!node || typeof node !== 'object') return;
      const currentMatch = inheritedMatch || nodeMatches(node);
      if (currentMatch && codes.indexOf(String(code)) === -1) codes.push(String(code));
      if (node.filhos && typeof node.filhos === 'object') walk(node.filhos, currentMatch);
    });
  }

  walk(demandantes, false);
  return codes;
}

function materiaMatchesSemob(item) {
  const poder = Array.isArray(item.poder) ? item.poder.join(' ') : String(item.poder || '');
  const normalized = normalizeText(poder);
  return CONFIG.SEMOB_TERMS.some((term) => normalized.indexOf(term) !== -1);
}

function toMateria(item, fullText) {
  const poder = Array.isArray(item.poder) ? item.poder.join(' > ') : String(item.poder || '');
  return {
    code: String(item.coMateria || item.co_materia || ''),
    slug: String(item.slug || ''),
    title: String(item.titulo || item.ds_titulo || 'Materia sem titulo'),
    section: String(item.secao || item.ds_secao || ''),
    kind: String(item.tipo || item.ds_materia_tipo || ''),
    agency: poder,
    url: materiaUrl(item),
    fullText: fullText || cleanText(item.texto || ''),
  };
}

function buildPlainBody(report, attachmentStatus) {
  const count = report.materias.length;
  const lines = [
    `DODF SEMOB - ${formatReportDate(report.diario)}`,
    `Publicacoes encontradas: ${count} ${pluralPublicacoes(count)}`,
    '',
    report.diario.pdfs.length ? `PDF do DODF: ${report.diario.pdfs[0].url}` : 'PDF do DODF: nao encontrado.',
    `Anexo: ${attachmentStatus}`,
    '',
  ];

  if (!report.materias.length) {
    lines.push('Nao foram encontradas publicacoes da SEMOB no Diario consultado.');
    return lines.join('\n');
  }

  report.materias.forEach((materia, index) => {
    lines.push(
      `${index + 1}. ${materia.title}`,
      `Secao: ${materia.section}`,
      `Tipo: ${materia.kind}`,
      `Orgao: ${materia.agency}`,
      `Link oficial: ${materia.url}`,
      '',
      'Texto completo:',
      materia.fullText || 'Texto nao disponivel no HTML. Consulte o link oficial.',
      '',
      '------------------------------------------------------------------------',
      ''
    );
  });

  return lines.join('\n').trim();
}

function buildHtmlBody(report, attachmentStatus) {
  const count = report.materias.length;
  const pdfHtml = report.diario.pdfs.length
    ? `<a href="${escapeHtml(report.diario.pdfs[0].url)}">${escapeHtml(report.diario.pdfs[0].url)}</a>`
    : 'nao encontrado';
  const parts = [
    '<html><body>',
    `<h2>DODF SEMOB - ${escapeHtml(formatReportDate(report.diario))}</h2>`,
    `<p><strong>Publicacoes encontradas:</strong> ${count} ${pluralPublicacoes(count)}</p>`,
    `<p><strong>PDF do DODF:</strong> ${pdfHtml}</p>`,
    `<p><strong>Anexo:</strong> ${escapeHtml(attachmentStatus)}</p>`,
  ];

  if (!report.materias.length) {
    parts.push('<p>Nao foram encontradas publicacoes da SEMOB no Diario consultado.</p>');
  } else {
    report.materias.forEach((materia, index) => {
      parts.push(
        '<hr>',
        `<h3>${index + 1}. ${escapeHtml(materia.title)}</h3>`,
        '<ul>',
        `<li><strong>Secao:</strong> ${escapeHtml(materia.section)}</li>`,
        `<li><strong>Tipo:</strong> ${escapeHtml(materia.kind)}</li>`,
        `<li><strong>Orgao:</strong> ${escapeHtml(materia.agency)}</li>`,
        `<li><strong>Link oficial:</strong> <a href="${escapeHtml(materia.url)}">${escapeHtml(materia.url)}</a></li>`,
        '</ul>',
        '<p><strong>Texto completo:</strong></p>',
        `<div style="white-space: pre-wrap; font-family: Arial, sans-serif; line-height: 1.45;">${escapeHtml(materia.fullText || 'Texto nao disponivel no HTML. Consulte o link oficial.')}</div>`
      );
    });
  }

  parts.push('</body></html>');
  return parts.join('\n');
}

function fetchText(url) {
  const response = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: defaultHeaders(),
  });
  if (response.getResponseCode() >= 400) {
    throw new Error(`Falha ao acessar ${url}: HTTP ${response.getResponseCode()}`);
  }
  return response.getContentText();
}

function defaultHeaders() {
  return {
    'User-Agent': 'Mozilla/5.0 (compatible; dodf-semob-report/1.0; Google Apps Script)',
    Accept: 'text/html,application/json;q=0.9,*/*;q=0.8',
  };
}

function extractJsJson(html, variableName, fallback) {
  const raw = extractJsAssignment(html, variableName);
  return raw ? JSON.parse(raw) : fallback;
}

function extractJsAssignment(html, variableName) {
  const marker = new RegExp(`\\bvar\\s+${variableName}\\s*=`);
  const match = marker.exec(html);
  if (!match) return null;

  let index = match.index + match[0].length;
  while (index < html.length && /\s/.test(html[index])) index += 1;
  const start = index;
  const opener = html[index];
  const closer = opener === '{' ? '}' : opener === '[' ? ']' : null;
  if (!closer) {
    const end = html.indexOf(';', index);
    return end === -1 ? null : html.slice(start, end).trim();
  }

  let depth = 0;
  let inString = false;
  let quote = '';
  let escaped = false;

  while (index < html.length) {
    const char = html[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === quote) inString = false;
    } else if (char === '"' || char === "'") {
      inString = true;
      quote = char;
    } else if (char === opener) {
      depth += 1;
    } else if (char === closer) {
      depth -= 1;
      if (depth === 0) return html.slice(start, index + 1).trim();
    }
    index += 1;
  }
  return null;
}

function parseDodfDate(data) {
  if (!Array.isArray(data) || data.length < 2) return { publishedDate: null, timestamp: null };
  const raw = String(data[0]);
  let publishedDate = null;
  if (/^\d{8}$/.test(raw)) {
    publishedDate = `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`;
  }
  return { publishedDate, timestamp: Number(data[1]) || null };
}

function collectPdfInfos(lstLinkPdf) {
  const pdfs = [];
  Object.keys(lstLinkPdf || {}).forEach((key) => {
    const entries = lstLinkPdf[key] || [];
    entries.forEach((item) => {
      if (!item || !item.link) return;
      const url = buildPdfUrl(item.link);
      pdfs.push({ name: item.nome || parsePdfFilename(url, 'dodf.pdf'), url });
    });
  });
  return pdfs;
}

function buildPdfUrl(link) {
  if (/^https?:\/\//i.test(link)) return link;
  const split = String(link).split('|&arquivo=');
  if (split.length === 2) {
    return `${CONFIG.BASE_URL}/dodf/jornal/visualizar-pdf?pasta=${encodeURIComponent(split[0] + '|')}&arquivo=${encodeURIComponent(split[1])}`;
  }
  return `${CONFIG.BASE_URL}/dodf/jornal/visualizar-pdf?pasta=${encodeURIComponent(link)}`;
}

function parsePdfFilename(pdfUrl, fallback) {
  const match = String(pdfUrl).match(/[?&]arquivo=([^&]+)/);
  return match ? decodeURIComponent(match[1].replace(/\+/g, ' ')) : fallback || 'dodf.pdf';
}

function materiaUrl(item) {
  const code = String(item.coMateria || item.co_materia || '');
  const slug = String(item.slug || '');
  return `${CONFIG.BASE_URL}/dodf/materia/visualizar?co_data=${encodeURIComponent(code)}&p=${encodeURIComponent(slug)}`;
}

function dedupeMaterias(items) {
  const seen = {};
  const result = [];
  items.forEach((item) => {
    const code = String(item.coMateria || item.co_materia || '');
    if (!code || seen[code]) return;
    seen[code] = true;
    result.push(item);
  });
  return result;
}

function normalizeText(value) {
  return cleanText(value).toUpperCase();
}

function cleanText(value) {
  return decodeHtmlEntities(String(value || ''))
    .replace(/\s+/g, ' ')
    .trim();
}

function cleanExtractedText(value) {
  return decodeHtmlEntities(String(value || ''))
    .split(/\r?\n/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n');
}

function stripTags(value) {
  return String(value || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, ' ');
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>');
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatReportDate(diario) {
  if (!diario.publishedDate) return 'data nao identificada';
  const parts = diario.publishedDate.split('-');
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function pluralPublicacoes(count) {
  return count === 1 ? 'publicacao' : 'publicacoes';
}
