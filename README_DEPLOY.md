# GeoAgreste DroneTM — implantação

## Conteúdo
- `index.html`: site original preservado, com painel fotogramétrico integrado.
- `app.py`: API Flask, upload/EXIF, filtro espacial de fotos, geração de GCP list, submissão e consulta NodeODM e publicação de produtos.
- `requirements.txt`: dependências Python.
- `render.yaml` / `Procfile`: configuração de deploy.

## Render (API + site)
1. Suba todos os arquivos deste pacote para a raiz do repositório GitHub.
2. Crie um **Web Service** no Render usando esse repositório.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT app:app`
5. Adicione um Persistent Disk com mount path `/var/data` (o `render.yaml` descreve 20 GB; ajuste conforme seu volume).
6. Environment: `DATA_DIR=/var/data`, `NODEODM_HOST=<host alcançável do NodeODM>`, `NODEODM_PORT=3000`, `NODEODM_TOKEN=<token se configurado>`.
7. Verifique `https://SEU-SERVICO.onrender.com/api/health`.

**Importante:** o filesystem do Render sem Persistent Disk é efêmero. Fotos e resultados não podem ficar apenas no disco temporário. A capacidade do disco deve ser dimensionada pelo tamanho dos voos.

## NodeODM (motor fotogramétrico)
O Flask no Render não é o motor ODM. É necessário manter um NodeODM separado, preferencialmente em VPS/servidor com Docker, CPU/RAM/SSD adequados. O NodeODM deve aceitar conexões de saída do Render na porta TCP 3000. Não use `localhost` no Render para apontar a outro serviço: `localhost` seria o próprio container Flask.

Exemplo de execução em VPS com Docker (ajuste memória/CPU e segurança):

```bash
docker run -d --name nodeodm --restart unless-stopped \
  -p 3000:3000 \
  opendronemap/nodeodm
```

Proteja a porta com firewall, permita acesso somente do serviço Flask (ou coloque ambos em rede privada/VPN) e configure token/autenticação se suportado pelo seu ambiente. Não exponha NodeODM aberto à internet.

## Fluxo dos GCPs
1. Envie as fotografias originais JPEG/TIFF/PNG. O servidor lê coordenadas GPS EXIF quando existentes.
2. Cadastre X, Y, Z no sistema de referência projetado correto (por exemplo, SIRGAS 2000 / UTM 24S em Salvador, normalmente EPSG:31984; confirme a zona do projeto).
3. Filtre as fotos candidatas pela posição EXIF da câmera e raio configurado.
4. Selecione o GCP e abra cada fotografia candidata. Clique no centro físico do alvo. O sistema converte o clique para pixel da imagem original, não para o tamanho reduzido da miniatura.
5. Marque o mesmo alvo em pelo menos duas fotografias distintas (recomendado três ou mais e boa geometria de interseção).
6. Envie o projeto ao NodeODM. A API gera `gcp_list.txt` no formato esperado e envia as imagens + GCP.

A filtragem espacial é uma **pré-seleção**: GPS EXIF registra a posição da câmera, não a posição exata do alvo no terreno. A confirmação visual/manual é indispensável. A qualidade final depende da calibração, precisão das coordenadas, distribuição dos GCPs, visibilidade dos alvos e geometria do bloco.

## Limitações que precisam ser resolvidas antes de uso operacional
- O login/cadastro e os projetos do site original continuam no `localStorage` do navegador; esta entrega não transforma o sistema de autenticação em multiusuário seguro nem sincroniza os cadastros entre navegadores. Não use dados pessoais sensíveis nesse login demonstrativo.
- Fotos e produtos são persistidos no disco do Render; para grande escala e alta disponibilidade, migre os arquivos para armazenamento de objetos (S3 compatível) e metadados/projetos para PostgreSQL.
- O site original ainda contém outros módulos de planejamento de voo. A integração fotogramétrica nova funciona ao abrir o detalhe de um projeto.
- A API não implementa autenticação/autorização por usuário. Antes de disponibilizar publicamente, implemente autenticação server-side, controle de acesso por projeto, limites de upload, antivírus/validação de arquivos e política de retenção.
- O custo de processamento depende do número/resolução das imagens, qualidade, RAM, CPU e armazenamento. Não há garantia de que uma instância web comum do Render consiga processar ODM; o motor é externo.

## Testes rápidos
- `GET /api/health`
- Upload: `POST /api/projects/ID/photos` com multipart `photos` (múltiplos arquivos)
- Filtro: `POST /api/projects/ID/filter-gcp-photos` com JSON `{"lat":-12.97,"lon":-38.50,"radius":80}`
- Processamento: `POST /api/projects/ID/process-odm` com JSON contendo `epsg`, `quality`, `photos` e `gcps`.


## Visualizadores pós-processamento
A interface reconhece ortofoto GeoTIFF, MDS, MDT, modelo OBJ e LAS/LAZ. Leaflet + GeoTIFF fazem a pré-visualização dos rasters; Three.js exibe o OBJ e uma amostra WebGL de até 150 mil pontos LAS/LAZ. O arquivo integral continua disponível para download. As bibliotecas do navegador são carregadas por CDN. A pré-visualização de nuvem é para inspeção visual e não substitui ferramentas completas de medição/classificação como Metashape, CloudCompare ou Potree.
