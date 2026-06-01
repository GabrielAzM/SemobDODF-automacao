# Google Apps Script - DODF SEMOB

Use esta alternativa quando GitHub Actions ou Render nao conseguirem acessar `https://dodf.df.gov.br`.

## Como instalar

1. Acesse https://script.google.com/.
2. Crie um novo projeto.
3. Apague o conteudo inicial de `Code.gs`.
4. Copie todo o conteudo de `apps_script/Code.gs` deste repositorio e cole no editor.
5. Salve o projeto como `DODF SEMOB`.
6. Rode manualmente a funcao `sendDodfSemobReport`.
7. Autorize os acessos solicitados.
8. Confira se o email chegou em `thaysdiasr@gmail.com`.
9. Depois rode a funcao `setupDailyTrigger` uma vez para criar o agendamento diario.

## Funcoes

- `sendDodfSemobReport`: busca o DODF, filtra SEMOB e envia o email.
- `setupDailyTrigger`: cria um gatilho diario perto de 06:30 no fuso `America/Sao_Paulo`.

## Observacoes

O Apps Script envia o email pela conta Google que autorizou o script. O horario de gatilhos do Apps Script pode ter pequena variacao, conforme politica da propria plataforma.
