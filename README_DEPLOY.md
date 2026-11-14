# GeoAgreste DroneTM — implantação

## Escopo desta versão
Esta versão mantém a interface e os recursos originais de planejamento de voo, incluindo mapas, delimitação de áreas, parâmetros de missão, cálculo de GSD, geração de linhas/waypoints, estimativas e exportação KMZ, além dos módulos de projetos e ARO.

O backend foi simplificado para servir a aplicação e disponibilizar o endpoint de saúde. Foram removidos do frontend e do backend os recursos de processamento de imagens, upload de fotografias aéreas, leitura EXIF para GCP, cadastro/marcação de GCPs, integração NodeODM, acompanhamento de tarefas, armazenamento/consulta de produtos processados e visualizadores de ortomosaicos, modelos 3D e nuvens de pontos.

## Arquivos
- `index.html`: interface original preservada, sem o módulo de processamento fotogramétrico.
- `app.py`: API Flask mínima para servir a página e verificar a saúde do serviço.
- `requirements.txt`: somente dependências necessárias ao backend leve.
- `render.yaml` / `Procfile`: configuração de deploy.

## Deploy no Render
1. Envie os arquivos deste pacote para a raiz do repositório GitHub.
2. Crie um **Web Service** no Render usando esse repositório.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:app`
5. Não é necessário Persistent Disk para o backend desta versão.
6. Verifique `https://SEU-SERVICO.onrender.com/api/health`.

## Teste rápido
- `GET /` — carrega a plataforma.
- `GET /api/health` — retorna o estado da API.

## Observação
O cadastro de usuários e os projetos continuam seguindo o comportamento já existente no frontend. Esta alteração não reestrutura autenticação, armazenamento de projetos, identidade visual ou funcionalidades de planejamento.
