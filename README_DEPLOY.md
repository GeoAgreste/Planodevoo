# GeoAgreste DroneTM — planejamento de voo com PostgreSQL

Esta versão mantém o frontend de planejamento e remove o processamento fotogramétrico. Usuários, projetos, parâmetros de voo, geometrias/waypoints e AROs (armazenados no conteúdo dos projetos) passam a ser persistidos no PostgreSQL do Render.

## Arquivos
- `index.html`: interface e chamadas à API; não guarda os projetos como fonte principal no navegador.
- `app.py`: API Flask, autenticação, autorização, persistência e auditoria.
- `requirements.txt`: dependências Flask + PostgreSQL.

## Render — Web Service
- Runtime: Python
- Build: `pip install -r requirements.txt`
- Start: `gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:app`

## Environment Variables
Configure no serviço Web:
- `DATABASE_URL`: Internal Database URL do PostgreSQL do Render (não use a External URL se Web Service e banco estiverem no Render).
- `SECRET_KEY`: chave aleatória longa e estável; não altere a cada deploy.
- `CORS_ORIGINS`: `*` se frontend e API forem servidos pelo mesmo domínio; se hospedados separadamente, informe a origem exata do frontend.
- `COOKIE_SECURE`: `true` em produção.
- `ADMIN_EMAIL`: e-mail da conta administradora inicial.
- `ADMIN_PASSWORD`: senha forte para a conta administradora inicial. A senha só é usada na criação; se a conta já existir, o deploy promove o papel para admin, sem substituir a senha existente.
- `ADMIN_NAME`: nome exibido para o administrador (opcional).

O banco precisa existir antes do deploy. A aplicação cria as tabelas `users`, `projects` e `audit_logs` na inicialização.

## Primeiro acesso
1. Configure todas as variáveis acima, principalmente `DATABASE_URL`, `SECRET_KEY`, `ADMIN_EMAIL` e `ADMIN_PASSWORD`.
2. Faça deploy.
3. Entre com `ADMIN_EMAIL` e `ADMIN_PASSWORD`.
4. Operadores podem criar contas pelo formulário de cadastro; as senhas são armazenadas com hash, nunca em texto puro.

## Persistência e auditoria
- Projetos são gravados no PostgreSQL, incluindo os parâmetros, polígonos, waypoints e dados da ARO contidos no objeto do projeto.
- A tabela `audit_logs` registra cadastro, login, logout, sincronização de projetos e exclusões.
- Exclusões de projetos são executadas no banco e registradas.
- O endpoint `/api/health` verifica a conexão com o banco.
- O endpoint `/api/admin/audit` disponibiliza os últimos 500 registros para administradores.

## Migração de projetos antigos do navegador
No primeiro login de cada operador, o frontend tenta importar os projetos legados encontrados em `localStorage` que tenham `ownerEmail` igual ao e-mail autenticado. Mantenha os dados do navegador antigo até confirmar que os projetos aparecem na plataforma conectada ao banco. Contas e senhas antigas que existiam apenas no `localStorage` não são migradas; cadastre novamente os operadores e defina a conta administradora pelas variáveis de ambiente.

## Segurança e limitações atuais
- A sessão usa cookie HttpOnly e assinatura com `SECRET_KEY`.
- A API aplica autorização por proprietário e administrador.
- Login Google é apenas um botão legado e permanece desabilitado até configurar OAuth real.
- Recuperação automática de senha não está habilitada; requer fluxo de e-mail/transação seguro.
- Faça backup periódico do PostgreSQL pelo painel do Render.
