"""GeoAgreste DroneTM — API Flask com persistência PostgreSQL."""
import os, json
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, session, redirect, url_for, abort
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from authlib.integrations.flask_client import OAuth
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
# Reconhece HTTPS e host encaminhados pelo proxy do Render.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY') or os.urandom(32).hex(),
    SQLALCHEMY_DATABASE_URI=(os.environ.get('DATABASE_URL', '').replace('postgres://', 'postgresql://', 1)),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE', 'true').lower() == 'true',
)
if not app.config['SQLALCHEMY_DATABASE_URI']:
    raise RuntimeError('DATABASE_URL não configurada. Adicione a URL do PostgreSQL do Render.')
cors_origins = [x.strip() for x in os.environ.get('CORS_ORIGINS', '*').split(',') if x.strip()]
# Frontend e API no mesmo domínio não precisam de CORS. Para domínio separado,
# informe a origem exata e habilite credenciais.
if cors_origins and cors_origins != ['*']:
    CORS(app, supports_credentials=True, resources={r'/api/*': {'origins': cors_origins}})
db = SQLAlchemy(app)


def now_iso():
    return datetime.now(timezone.utc).isoformat()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    cpf = db.Column(db.String(30), default='')
    phone = db.Column(db.String(40), default='')
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    google_id = db.Column(db.String(255), unique=True, nullable=True, index=True)
    role = db.Column(db.String(20), nullable=False, default='user')
    created_at = db.Column(db.String(40), default=now_iso)

    def public(self):
        return {'id': self.id, 'name': self.name, 'cpf': self.cpf or '', 'phone': self.phone or '', 'email': self.email, 'role': self.role}

class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.String(100), primary_key=True)
    owner_email = db.Column(db.String(254), nullable=False, index=True)
    payload = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.String(40), default=now_iso)
    updated_at = db.Column(db.String(40), default=now_iso)

class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_email = db.Column(db.String(254), nullable=False, index=True)
    action = db.Column(db.String(60), nullable=False)
    entity = db.Column(db.String(60), nullable=False)
    entity_id = db.Column(db.String(100), default='')
    details = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.String(40), default=now_iso)


def audit(action, entity, entity_id='', details=None):
    db.session.add(AuditLog(user_email=session.get('email', 'system'), action=action, entity=entity,
                            entity_id=str(entity_id or ''), details=details or {}))


def current_user():
    email = session.get('email')
    return User.query.filter_by(email=email).first() if email else None


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user():
            return jsonify(error='Autenticação necessária.'), 401
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        u = current_user()
        if not u:
            return jsonify(error='Autenticação necessária.'), 401
        if u.role != 'admin':
            return jsonify(error='Acesso restrito ao administrador.'), 403
        return fn(*args, **kwargs)
    return wrapped

@app.get('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.get('/api/health')
def health():
    try:
        db.session.execute(db.text('SELECT 1'))
        return jsonify(status='ok', database='connected', service='GeoAgreste DroneTM', mode='planejamento_de_voo')
    except Exception:
        app.logger.exception('Falha ao verificar PostgreSQL')
        return jsonify(status='error', database='disconnected'), 503

# OAuth Google: habilitado quando GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET estiverem definidos.
oauth = OAuth(app)
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name='google',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

@app.get('/auth/google')
def google_login():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return 'Login Google não configurado. Defina GOOGLE_CLIENT_ID e GOOGLE_CLIENT_SECRET no Render.', 503
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI') or url_for('google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri, prompt='select_account')

@app.get('/auth/google/callback')
def google_callback():
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return redirect('/?google_error=not_configured')
    try:
        token = oauth.google.authorize_access_token()
        info = token.get('userinfo')
        if not info:
            info = oauth.google.get('userinfo').json()
        email = str(info.get('email', '')).strip().lower()
        google_id = str(info.get('sub', '')).strip()
        name = str(info.get('name') or info.get('given_name') or email.split('@')[0]).strip()
        if not email or not google_id or info.get('email_verified') is not True:
            app.logger.warning('Google OAuth recusado: e-mail ausente ou não verificado.')
            return redirect('/?google_error=unverified_email')
        u = User.query.filter_by(google_id=google_id).first()
        if not u:
            # Vincula ao cadastro existente pelo e-mail verificado, evitando contas duplicadas.
            u = User.query.filter_by(email=email).first()
            if u:
                u.google_id = google_id
                if not u.name:
                    u.name = name
            else:
                u = User(name=name, email=email, password_hash=generate_password_hash(secrets.token_urlsafe(48)), google_id=google_id, role='user')
                db.session.add(u)
        session.clear()
        session['email'] = u.email
        audit('google_login', 'session', u.id, {'provider': 'google'})
        db.session.commit()
        return redirect('/')
    except Exception:
        db.session.rollback()
        app.logger.exception('Falha no callback de autenticação Google')
        return redirect('/?google_error=oauth_failed')

@app.post('/api/auth/register')
def register():
    data = request.get_json(silent=True) or {}
    name, email, password = str(data.get('name', '')).strip(), str(data.get('email', '')).strip().lower(), str(data.get('password', ''))
    if not name or not email or len(password) < 8:
        return jsonify(error='Informe nome, e-mail e senha com pelo menos 8 caracteres.'), 400
    if User.query.filter_by(email=email).first():
        return jsonify(error='Este e-mail já está cadastrado.'), 409
    u = User(name=name, email=email, cpf=str(data.get('cpf', '')).strip(), phone=str(data.get('phone', '')).strip(), password_hash=generate_password_hash(password))
    db.session.add(u); db.session.commit()
    audit('register', 'user', u.id); db.session.commit()
    return jsonify(user=u.public()), 201

@app.post('/api/auth/login')
def login():
    data = request.get_json(silent=True) or {}
    email = str(data.get('email', '')).strip().lower()
    u = User.query.filter_by(email=email).first()
    if not u or not check_password_hash(u.password_hash, str(data.get('password', ''))):
        return jsonify(error='E-mail ou senha inválidos.'), 401
    session.clear(); session['email'] = u.email
    audit('login', 'session', u.id); db.session.commit()
    return jsonify(user=u.public())

@app.get('/api/auth/me')
@login_required
def me():
    return jsonify(user=current_user().public())

@app.post('/api/auth/logout')
@login_required
def logout():
    u = current_user(); audit('logout', 'session', u.id); db.session.commit(); session.clear()
    return jsonify(ok=True)

@app.get('/api/projects')
@login_required
def list_projects():
    u = current_user()
    q = Project.query if u.role == 'admin' else Project.query.filter_by(owner_email=u.email)
    items = [p.payload for p in q.order_by(Project.updated_at.desc()).all()]
    return jsonify(projects=items)

@app.post('/api/projects/sync')
@login_required
def sync_projects():
    """Upsert do conjunto em cache do frontend; nunca apaga registros do banco."""
    u = current_user(); data = request.get_json(silent=True) or {}; items = data.get('projects', [])
    if not isinstance(items, list): return jsonify(error='Lista de projetos inválida.'), 400
    for item in items:
        if not isinstance(item, dict) or not item.get('id'): continue
        pid = str(item['id'])
        owner = str(item.get('ownerEmail') or u.email).strip().lower()
        if u.role != 'admin' and owner != u.email: continue
        item = dict(item); item['ownerEmail'] = owner
        row = db.session.get(Project, pid)
        if row and u.role != 'admin' and row.owner_email != u.email: continue
        if not row:
            row = Project(id=pid, owner_email=owner, payload=item)
            db.session.add(row)
        else:
            row.owner_email = owner; row.payload = item; row.updated_at = now_iso()
    audit('sync', 'projects', details={'received': len(items)}); db.session.commit()
    return jsonify(ok=True)

@app.delete('/api/projects/<string:project_id>')
@login_required
def delete_project(project_id):
    u = current_user(); row = db.session.get(Project, project_id)
    if not row: return jsonify(error='Projeto não encontrado.'), 404
    if u.role != 'admin' and row.owner_email != u.email: return jsonify(error='Sem permissão para excluir este projeto.'), 403
    db.session.delete(row); audit('delete', 'project', project_id); db.session.commit()
    return jsonify(ok=True)

@app.get('/api/admin/audit')
@admin_required
def audit_records():
    rows = AuditLog.query.order_by(AuditLog.id.desc()).limit(500).all()
    return jsonify(records=[{'id':r.id,'user':r.user_email,'action':r.action,'entity':r.entity,'entity_id':r.entity_id,'details':r.details,'created_at':r.created_at} for r in rows])

with app.app_context():
    db.create_all()
    # Migração compatível com bancos já existentes (CREATE TABLE não adiciona colunas).
    try:
        db.session.execute(db.text('ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id VARCHAR(255)'))
        db.session.execute(db.text('CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_id ON users (google_id)'))
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception('Falha ao aplicar migração da coluna google_id')
    # Conta administrativa opcional: criada somente quando as duas variáveis forem definidas.
    admin_email = os.environ.get('ADMIN_EMAIL', '').strip().lower()
    admin_password = os.environ.get('ADMIN_PASSWORD', '')
    if admin_email and admin_password:
        admin_user = User.query.filter_by(email=admin_email).first()
        if not admin_user:
            admin_user = User(name=os.environ.get('ADMIN_NAME', 'Administrador GeoAgreste'), email=admin_email,
                              password_hash=generate_password_hash(admin_password), role='admin')
            db.session.add(admin_user)
        else:
            admin_user.role = 'admin'
        db.session.commit()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')))
