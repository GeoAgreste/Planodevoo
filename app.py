"""GeoAgreste DroneTM bridge: Flask API + NodeODM.

Run as a Render Web Service. Heavy photogrammetry runs on a separate NodeODM
service; configure NODEODM_HOST/NODEODM_PORT to its reachable address.
"""
import os, re, json, math, uuid, shutil, zipfile, logging
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory, abort
from flask_cors import CORS
from werkzeug.utils import secure_filename
from PIL import Image, ExifTags
from pyproj import Transformer
from pyodm import Node, exceptions
try:
    import laspy
except Exception:
    laspy = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('DATA_DIR', str(BASE_DIR / 'data'))).resolve()
UPLOAD_DIR = DATA_DIR / 'uploads'
RESULTS_DIR = DATA_DIR / 'results'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

NODEODM_HOST = os.environ.get('NODEODM_HOST', '').strip()
NODEODM_PORT = int(os.environ.get('NODEODM_PORT', '3000'))
NODEODM_TOKEN = os.environ.get('NODEODM_TOKEN', '').strip()
NODEODM_SCHEME = os.environ.get('NODEODM_SCHEME', 'http').strip()
NODEODM_URL = os.environ.get('NODEODM_URL', '').strip().rstrip('/') or (f'{NODEODM_SCHEME}://{NODEODM_HOST}:{NODEODM_PORT}' if NODEODM_HOST else '')
MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', str(20 * 1024 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff'}

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
CORS(app, resources={r'/api/*': {'origins': os.environ.get('CORS_ORIGINS', '*').split(',')}})
logging.basicConfig(level=os.environ.get('LOG_LEVEL', 'INFO'))
log = logging.getLogger('geoagreste-drone')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def project_dir(project_id: str) -> Path:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(project_id or '')):
        raise ValueError('Identificador de projeto inválido.')
    p = (UPLOAD_DIR / project_id).resolve()
    if UPLOAD_DIR.resolve() not in p.parents:
        raise ValueError('Caminho inválido.')
    return p


def exif_fraction(value):
    try:
        return float(value)
    except Exception:
        return float(value.numerator) / float(value.denominator)


def dms_to_degrees(value):
    return exif_fraction(value[0]) + exif_fraction(value[1]) / 60 + exif_fraction(value[2]) / 3600


def extract_exif_gps(path: Path):
    try:
        with Image.open(path) as im:
            width, height = im.size
            exif = im.getexif()
            gps_raw = exif.get(34853)
            if not gps_raw:
                return {'lat': None, 'lon': None, 'alt': None, 'width': width, 'height': height}
            gps = {}
            for k, v in gps_raw.items():
                gps[ExifTags.GPSTAGS.get(k, k)] = v
            lat = dms_to_degrees(gps['GPSLatitude'])
            lon = dms_to_degrees(gps['GPSLongitude'])
            if gps.get('GPSLatitudeRef') in ('S', b'S'):
                lat = -lat
            if gps.get('GPSLongitudeRef') in ('W', b'W'):
                lon = -lon
            alt = exif_fraction(gps['GPSAltitude']) if gps.get('GPSAltitude') is not None else None
            return {'lat': lat, 'lon': lon, 'alt': alt, 'width': width, 'height': height}
    except Exception as exc:
        log.info('EXIF não disponível em %s: %s', path.name, exc)
        return {'lat': None, 'lon': None, 'alt': None, 'width': 0, 'height': 0}


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.atan2(math.sqrt(a), math.sqrt(max(0, 1-a)))


def node():
    if not NODEODM_HOST and not NODEODM_URL:
        raise RuntimeError('NodeODM não configurado. Defina NODEODM_HOST (ou NODEODM_URL) nas variáveis de ambiente.')
    if NODEODM_URL:
        # pyodm Node accepts host and port; URL path/proxy is not supported here.
        from urllib.parse import urlparse
        u = urlparse(NODEODM_URL)
        if u.path not in ('', '/'):
            raise RuntimeError('NODEODM_URL deve apontar diretamente para a raiz do NodeODM, sem caminho adicional.')
        return Node(u.hostname, u.port or (443 if u.scheme == 'https' else 80), NODEODM_TOKEN)
    return Node(NODEODM_HOST, NODEODM_PORT, NODEODM_TOKEN)


def epsg_code(value):
    s = str(value or 'EPSG:31984').upper().replace(' ', '')
    if s.isdigit():
        s = 'EPSG:' + s
    if not re.fullmatch(r'EPSG:\d{4,6}', s):
        raise ValueError('EPSG inválido. Informe, por exemplo, EPSG:31984.')
    return s


@app.errorhandler(413)
def too_large(_):
    return jsonify(error='Upload excede o limite MAX_CONTENT_LENGTH configurado no servidor.'), 413


@app.get('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')


@app.get('/api/health')
def health():
    node_ok = False
    node_error = None
    if NODEODM_HOST or NODEODM_URL:
        try:
            base = NODEODM_URL or f'{NODEODM_SCHEME}://{NODEODM_HOST}:{NODEODM_PORT}'
            headers = {'Authorization': NODEODM_TOKEN} if NODEODM_TOKEN else {}
            response = requests.get(f'{base}/info', headers=headers, timeout=5)
            response.raise_for_status()
            node_ok = True
        except Exception as e:
            node_error = str(e)
    return jsonify(status='ok', service='GeoAgreste DroneTM API', timestamp=utc_now(), nodeodm_configured=bool(NODEODM_HOST or NODEODM_URL), nodeodm_client_ready=node_ok, nodeodm_error=node_error, data_dir=str(DATA_DIR))


@app.post('/api/projects/<project_id>/photos')
def upload_photos(project_id):
    try:
        folder = project_dir(project_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    files = request.files.getlist('photos')
    if not files:
        return jsonify(error='Nenhuma fotografia foi enviada no campo photos.'), 400
    folder.mkdir(parents=True, exist_ok=True)
    output, errors = [], []
    for file in files:
        if not file.filename:
            continue
        original = Path(file.filename).name
        ext = Path(original).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            errors.append({'filename': original, 'error': 'Formato não permitido.'})
            continue
        safe = secure_filename(original)
        if not safe:
            safe = f'{uuid.uuid4().hex}{ext}'
        # Avoid silently overwriting a previous image with the same name.
        target = folder / safe
        if target.exists():
            stem, suffix = target.stem, target.suffix
            safe = f'{stem}_{uuid.uuid4().hex[:8]}{suffix}'
            target = folder / safe
        file.save(target)
        meta = extract_exif_gps(target)
        output.append({'filename': safe, 'url': f'/uploads/{quote(project_id)}/{quote(safe)}', **meta})
    return jsonify(message=f'{len(output)} fotografia(s) armazenada(s).', photos=output, errors=errors), 201


@app.get('/uploads/<project_id>/<path:filename>')
def serve_photo(project_id, filename):
    try:
        folder = project_dir(project_id)
    except ValueError:
        abort(404)
    return send_from_directory(folder, filename, as_attachment=False, max_age=3600)


@app.post('/api/projects/<project_id>/filter-gcp-photos')
def filter_gcp_photos(project_id):
    data = request.get_json(silent=True) or {}
    try:
        radius = float(data.get('radius', 80))
        if data.get('x') is not None and data.get('y') is not None:
            src = epsg_code(data.get('epsg', 'EPSG:31984'))
            transformer = Transformer.from_crs(src, 'EPSG:4326', always_xy=True)
            lon, lat = transformer.transform(float(data['x']), float(data['y']))
        else:
            lat, lon = float(data['lat']), float(data['lon'])
    except (KeyError, TypeError, ValueError) as e:
        return jsonify(error=f'Informe coordenadas válidas (X/Y + EPSG ou lat/lon): {e}'), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < radius <= 5000):
        return jsonify(error='Coordenadas ou raio fora dos limites.'), 400
    folder = project_dir(project_id)
    matched = []
    if folder.exists():
        for f in folder.iterdir():
            if f.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            m = extract_exif_gps(f)
            if m['lat'] is None or m['lon'] is None:
                continue
            dist = haversine_m(lat, lon, m['lat'], m['lon'])
            if dist <= radius:
                matched.append({'filename': f.name, 'url': f'/uploads/{quote(project_id)}/{quote(f.name)}', **m, 'distance_m': round(dist, 2)})
    matched.sort(key=lambda x: x['distance_m'])
    return jsonify(matched_photos=matched, count=len(matched), note='A distância considera a coordenada EXIF da câmera; a posição do alvo precisa ser marcada manualmente na imagem.')


@app.post('/api/projects/<project_id>/process-odm')
def process_odm(project_id):
    data = request.get_json(silent=True) or {}
    try:
        folder = project_dir(project_id)
        epsg = epsg_code(data.get('epsg', 'EPSG:31984'))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    quality = str(data.get('quality', 'high')).lower()
    if quality not in {'lowest', 'low', 'medium', 'high', 'ultra'}:
        return jsonify(error='Qualidade inválida.'), 400
    names = data.get('photos') or []
    if not isinstance(names, list):
        return jsonify(error='photos deve ser uma lista de nomes de arquivos.'), 400
    paths = []
    for name in names:
        clean = Path(str(name)).name
        p = folder / clean
        if p.suffix.lower() not in ALLOWED_EXTENSIONS or not p.is_file() or p.parent.resolve() != folder.resolve():
            return jsonify(error=f'Fotografia não encontrada ou inválida: {clean}'), 400
        paths.append(str(p))
    if not paths:
        paths = [str(f) for f in folder.iterdir() if f.suffix.lower() in ALLOWED_EXTENSIONS]
    if len(paths) < 3:
        return jsonify(error='São necessárias pelo menos 3 fotografias.'), 400

    gcps = data.get('gcps') or []
    declared_epsgs = {str(g.get('epsg', epsg)).upper().replace(' ', '') for g in gcps}
    if len(declared_epsgs) > 1:
        return jsonify(error='Todos os GCPs devem usar o mesmo sistema de referência/EPSG.'), 400
    if declared_epsgs:
        try:
            epsg = epsg_code(next(iter(declared_epsgs)))
        except ValueError as e:
            return jsonify(error=str(e)), 400
    gcp_path = None
    if gcps:
        lines = [epsg]
        for g in gcps:
            try:
                name = re.sub(r'[^A-Za-z0-9_.-]', '_', str(g.get('name', 'GCP')))
                x, y, z = float(g['x']), float(g['y']), float(g.get('z', 0))
                projections = g.get('projections') or []
                if len(projections) < 2:
                    return jsonify(error=f'O ponto {name} precisa de pelo menos duas marcações em fotografias diferentes.'), 400
                used = set()
                for pr in projections:
                    fn = Path(str(pr.get('filename', ''))).name
                    px, py = float(pr['px']), float(pr['py'])
                    if fn not in {Path(v).name for v in paths}:
                        return jsonify(error=f'A fotografia {fn} marcada no GCP não faz parte do lote enviado.'), 400
                    if fn in used:
                        return jsonify(error=f'O ponto {name} tem duas marcações na mesma fotografia {fn}.'), 400
                    used.add(fn)
                    if px < 0 or py < 0:
                        return jsonify(error='Coordenadas de pixel não podem ser negativas.'), 400
                    lines.append(f'{x:.4f} {y:.4f} {z:.4f} {px:.2f} {py:.2f} {fn} {name}')
            except (KeyError, TypeError, ValueError) as e:
                return jsonify(error=f'Dados inválidos no GCP: {e}'), 400
        gcp_path = folder / 'gcp_list.txt'
        gcp_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        paths.append(str(gcp_path))

    options = {
        'dsm': True,
        'dtm': True,
        'texturing': True,
        'orthophoto-resolution': 2.0,
        'pc-quality': quality,
        'feature-quality': quality,
        'cog': True,
        'skip-report': False,
    }
    try:
        n = node()
        task = n.create_task(paths, options, name=f'GeoAgreste_{project_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
        task_record = {'task_uuid': task.uuid, 'project_id': project_id, 'created_at': utc_now(), 'epsg': epsg, 'quality': quality}
        (RESULTS_DIR / f'{task.uuid}.json').write_text(json.dumps(task_record, ensure_ascii=False), encoding='utf-8')
        return jsonify(status='QUEUED', task_uuid=task.uuid, message='Tarefa enviada ao NodeODM.', options=options), 202
    except exceptions.NodeConnectionError as e:
        log.exception('Falha de conexão NodeODM')
        return jsonify(error='Não foi possível conectar ao NodeODM. Confira NODEODM_HOST, porta, firewall e se o serviço está ativo.', details=str(e)), 503
    except Exception as e:
        log.exception('Erro ao criar tarefa ODM')
        return jsonify(error='Falha ao criar tarefa no NodeODM.', details=str(e)), 500


@app.get('/api/odm/status/<task_uuid>')
def odm_status(task_uuid):
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', task_uuid):
        return jsonify(error='UUID inválido.'), 400
    try:
        task = node().get_task(task_uuid)
        info = task.info()
        status = getattr(info.status, 'name', str(info.status))
        progress = float(getattr(info, 'progress', 0) or 0)
        elapsed = getattr(info, 'processing_time', None)
        response = {'uuid': task_uuid, 'status': status, 'progress': round(progress, 2), 'processing_time': elapsed}
        if status in {'COMPLETED', 'COMPLETED_WITH_WARNING'}:
            _download_task_assets(task_uuid)
        return jsonify(response)
    except exceptions.NodeConnectionError as e:
        return jsonify(error='NodeODM indisponível.', details=str(e)), 503
    except Exception as e:
        log.exception('Erro consultando status')
        return jsonify(error=str(e)), 500


def _download_task_assets(task_uuid):
    """Download all.zip once, when the NodeODM task has completed."""
    dest = RESULTS_DIR / task_uuid
    marker = dest / '.downloaded'
    if marker.exists():
        return
    if not NODEODM_URL:
        base = f'{NODEODM_SCHEME}://{NODEODM_HOST}:{NODEODM_PORT}'
    else:
        base = NODEODM_URL
    headers = {'Authorization': NODEODM_TOKEN} if NODEODM_TOKEN else {}
    url = f'{base}/task/{quote(task_uuid)}/download/all.zip'
    try:
        r = requests.get(url, headers=headers, timeout=(20, 600), stream=True)
        r.raise_for_status()
        tmp = RESULTS_DIR / f'{task_uuid}.zip'
        with tmp.open('wb') as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp) as zf:
            for member in zf.infolist():
                target = (dest / member.filename).resolve()
                if dest.resolve() not in target.parents and target != dest.resolve():
                    raise ValueError('Arquivo compactado contém caminho inválido.')
            zf.extractall(dest)
        tmp.unlink(missing_ok=True)
        marker.write_text(utc_now(), encoding='utf-8')
    except Exception:
        log.exception('Não foi possível baixar produtos do NodeODM para %s', task_uuid)


@app.get('/api/projects/<project_id>/assets')
def list_assets(project_id):
    try:
        project_dir(project_id)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    # Find tasks registered for this project.
    assets = []
    for meta_file in RESULTS_DIR.glob('*.json'):
        try:
            meta = json.loads(meta_file.read_text(encoding='utf-8'))
            if meta.get('project_id') != project_id:
                continue
            tid = meta.get('task_uuid')
            folder = RESULTS_DIR / tid
            if folder.exists():
                for f in folder.rglob('*'):
                    if f.is_file() and f.name != '.downloaded':
                        rel = f.relative_to(folder).as_posix()
                        assets.append({'name': rel, 'url': f'/api/odm/assets/{quote(tid)}/{quote(rel)}'})
        except Exception:
            log.exception('Metadado de tarefa inválido: %s', meta_file)
    return jsonify(assets=assets)


@app.get('/api/odm/assets/<task_uuid>/<path:filename>')
def serve_asset(task_uuid, filename):
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', task_uuid):
        abort(404)
    folder = (RESULTS_DIR / task_uuid).resolve()
    if RESULTS_DIR.resolve() not in folder.parents:
        abort(404)
    return send_from_directory(folder, filename, as_attachment=False, conditional=True)



@app.get('/api/odm/pointcloud/<task_uuid>')
def pointcloud_preview(task_uuid):
    """Return a capped LAS/LAZ sample for browser WebGL preview."""
    if not re.fullmatch(r'[A-Za-z0-9-]{8,80}', task_uuid):
        return jsonify(error='UUID inválido.'), 400
    if laspy is None:
        return jsonify(error='Pré-visualização LAS/LAZ indisponível: instale laspy[lazrs]. O arquivo original pode ser baixado.'), 501
    folder = (RESULTS_DIR / task_uuid).resolve()
    if RESULTS_DIR.resolve() not in folder.parents or not folder.exists():
        return jsonify(error='Produtos desta tarefa ainda não estão disponíveis.'), 404
    files = list(folder.rglob('*.laz')) + list(folder.rglob('*.las'))
    if not files:
        return jsonify(error='Nenhum LAS/LAZ foi gerado nesta tarefa.'), 404
    try:
        points=[]; limit=150000
        with laspy.open(files[0]) as reader:
            total=int(reader.header.point_count); stride=max(1, math.ceil(total/limit)); mins=reader.header.mins
            for chunk in reader.chunk_iterator(500000):
                xyz=chunk.xyz
                red=getattr(chunk,'red',None)
                for i in range(0,len(xyz),stride):
                    if len(points)>=limit: break
                    points.append([round(float(xyz[i][0]-mins[0]),3),round(float(xyz[i][1]-mins[1]),3),round(float(xyz[i][2]-mins[2]),3),int(red[i]/256) if red is not None else 180])
                if len(points)>=limit: break
        return jsonify(source=files[0].name,total_points=total,displayed_points=len(points),spacing=.05,points=points)
    except Exception as exc:
        log.exception('Falha ao preparar nuvem de pontos')
        return jsonify(error=f'Não foi possível ler a nuvem: {exc}'),500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
