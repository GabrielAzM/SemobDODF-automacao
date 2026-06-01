# Automação DODF SEMOB

Automação Python para consultar diariamente o Diário Oficial do Distrito Federal, coletar publicações da SEMOB/Secretaria de Estado de Transporte e Mobilidade, extrair o texto completo de cada matéria e enviar por email via SMTP.

O email inclui o texto completo das publicações encontradas, os links oficiais das matérias e o link do PDF do DODF. Quando `ATTACH_PDF=true`, a automação baixa o PDF e anexa se ele estiver dentro de `MAX_ATTACHMENT_MB`.

## Rodar localmente

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Preencha o `.env` com os dados do email:

- `SMTP_USER`: email usado para autenticar.
- `SMTP_PASSWORD`: senha de app do provedor, não a senha normal da conta.
- `MAIL_FROM`: remetente.
- `MAIL_TO`: um ou mais destinatários separados por vírgula.
- `EMAIL_DELIVERY`: use `smtp` ou `gmail_api`.

Se sua rede local bloquear SMTP, use `EMAIL_DELIVERY=gmail_api`. Para isso:

1. No Google Cloud, ative a Gmail API.
2. Crie um OAuth Client do tipo Desktop app.
3. Baixe o JSON do OAuth e salve como `credentials.json` na pasta do projeto.
4. Rode:

```powershell
.\venv\Scripts\python.exe dodf_semob_report.py --init-gmail-api
```

Depois do login no navegador, o script criará `token.json` e o envio local passará pela Gmail API em HTTPS.

Teste a coleta sem enviar email:

```powershell
.\venv\Scripts\python.exe dodf_semob_report.py --dry-run
```

Envie de verdade:

```powershell
.\venv\Scripts\python.exe dodf_semob_report.py
```

## Variáveis de ambiente

- `SMTP_HOST`: padrão `smtp.gmail.com`.
- `SMTP_PORT`: padrão `587`.
- `SMTP_USER`: usuário SMTP.
- `SMTP_PASSWORD`: senha de app ou senha SMTP.
- `MAIL_FROM`: remetente do email.
- `MAIL_TO`: destinatários separados por vírgula ou ponto e vírgula.
- `EMAIL_DELIVERY`: `smtp` ou `gmail_api`.
- `GMAIL_CREDENTIALS_FILE`: arquivo OAuth para Gmail API, padrão `credentials.json`.
- `GMAIL_TOKEN_FILE`: token OAuth local, padrão `token.json`.
- `ATTACH_PDF`: `true` para anexar o PDF quando possível.
- `MAX_ATTACHMENT_MB`: limite máximo do PDF anexado.
- `SEND_EMPTY_REPORT`: `true` para enviar aviso mesmo sem publicações SEMOB.
- `DODF_BASE_URL`: padrão `https://dodf.df.gov.br`.
- `HTTP_TIMEOUT_SECONDS`, `MAX_RETRIES`, `RETRY_DELAY_SECONDS`: controle de rede e tentativas.

## Deploy no Render

1. Suba estes arquivos para um repositório GitHub/GitLab/Bitbucket.
2. No Render, crie um Blueprint usando o `render.yaml` ou crie um Cron Job manualmente.
3. Configure as variáveis marcadas como `sync: false`: `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM` e `MAIL_TO`.
4. O cron está configurado como `30 9 * * *`, pois o Render usa UTC. Isso equivale a `06:30` em `America/Sao_Paulo`.
5. Use `Trigger Run` no Render para testar o envio real.

## Alternativa gratis: Google Apps Script

Se GitHub Actions ou Render nao conseguirem acessar `https://dodf.df.gov.br`, use a versao em `apps_script/`.

1. Acesse https://script.google.com/.
2. Crie um projeto novo.
3. Cole o conteudo de `apps_script/Code.gs` no arquivo `Code.gs`.
4. Rode a funcao `sendDodfSemobReport` para testar o envio.
5. Autorize o script na conta Google que sera o remetente.
6. Rode a funcao `setupDailyTrigger` uma vez para criar o agendamento perto de 06:30.

O Apps Script envia pela conta que autorizou o projeto e usa `UrlFetchApp` para acessar o DODF por HTTPS, sem depender de porta SMTP liberada.

Se aparecer `Exception: Address unavailable`, o Google tambem nao conseguiu acessar o DODF. Nesse caso, use o agendamento local abaixo.

## Agendamento local no Windows

Use este modo quando o DODF so abre pela rede do seu computador, VPN ou rede do orgao.

1. Prepare o Gmail API e o `.env` local:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_local_gmail_api.ps1
```

2. No navegador que abrir, autorize a conta Google remetente.
3. Envie um teste:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_report.ps1
```

4. Se o email chegar, crie a tarefa diaria das 06:30:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

Os logs ficam em `logs/`. O computador precisa estar ligado e com acesso ao DODF no horario do envio.

## Testes

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Os testes cobrem parsing de demandantes SEMOB, montagem de links, extração de texto, email sem resultados e limite do PDF anexado.
