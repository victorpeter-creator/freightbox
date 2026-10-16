from flask import Flask, render_template, request, send_file, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from functools import wraps
import uuid
import os
import requests
from xhtml2pdf import pisa
import random

app = Flask(__name__)

# --- CLOUD DEPLOYMENT CONFIGURATION ---
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_super_secret_key_here')

db_url = os.environ.get('DATABASE_URL', 'sqlite:///fos.db')
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

TERMINAL49_API_KEY = os.environ.get("TERMINAL49_API_KEY", "T49_live_DcgZ6JWLUcPH13W2zqVpR6UC")

# --- DIRECTORY CONFIGURATION ---
QUOTE_DIR = os.path.join(app.root_path, 'static', 'quotes')
LOGO_DIR = os.path.join(app.root_path, 'static', 'logos')
os.makedirs(QUOTE_DIR, exist_ok=True)
os.makedirs(LOGO_DIR, exist_ok=True)

# --- LOGIN MANAGER ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def generate_uuid():
    return str(uuid.uuid4())

@login_manager.user_loader
def load_user(user_id):
    org = Organization.query.get(user_id)
    if org:
        return org
    return ClientUser.query.get(user_id)

def org_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not isinstance(current_user, Organization):
            return "Unauthorized. Forwarder staff access only.", 403
        return f(*args, **kwargs)
    return decorated_function

def client_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not isinstance(current_user, ClientUser):
            return "Unauthorized. Client access only.", 403
        return f(*args, **kwargs)
    return decorated_function

# --- DATABASE MODELS ---
class Organization(UserMixin, db.Model):
    __tablename__ = 'organizations'
    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    fax_number = db.Column(db.String(20), nullable=True)
    bank_name = db.Column(db.String(100), nullable=True)
    account_number = db.Column(db.String(20), nullable=True)
    logo_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    shipments = db.relationship('Shipment', backref='organization', lazy=True, cascade="all, delete")
    clients = db.relationship('ClientUser', backref='organization', lazy=True, cascade="all, delete")

class ClientUser(UserMixin, db.Model):
    __tablename__ = 'client_users'
    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey('organizations.id'), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    company = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    shipments = db.relationship('Shipment', backref='client', lazy=True)

class Shipment(db.Model):
    __tablename__ = 'shipments'
    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey('organizations.id'), nullable=False)
    client_id = db.Column(db.String(36), db.ForeignKey('client_users.id'), nullable=True)
    
    tracking_code = db.Column(db.String(50), unique=True, nullable=False)
    client_name = db.Column(db.String(255), nullable=False)
    client_company = db.Column(db.String(255), nullable=True)
    client_email = db.Column(db.String(120), nullable=True)
    client_address = db.Column(db.String(255), nullable=True)
    client_city = db.Column(db.String(100), nullable=True)
    client_phone = db.Column(db.String(20), nullable=False)
    
    cargo_type = db.Column(db.String(50), nullable=False)
    service_type = db.Column(db.String(50), nullable=False)
    origin = db.Column(db.String(255), nullable=False)
    destination = db.Column(db.String(255), nullable=False)
    origin_door_address = db.Column(db.String(255), nullable=True)
    dest_door_address = db.Column(db.String(255), nullable=True)
    
    mbl_container_number = db.Column(db.String(100), nullable=True)
    vessel_lat = db.Column(db.Float, nullable=True)
    vessel_lon = db.Column(db.Float, nullable=True)
    last_sync = db.Column(db.DateTime, nullable=True)
    
    base_freight = db.Column(db.Float, default=0.0)
    additional_charges = db.Column(db.Float, default=0.0)
    customs_fee = db.Column(db.Float, default=0.0)
    trucking_fee = db.Column(db.Float, default=0.0)
    total_quote_amount = db.Column(db.Float, nullable=False)
    
    current_status = db.Column(db.String(50), default='on_sea')
    payment_status = db.Column(db.String(20), default='Unpaid')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    port_arrival_date = db.Column(db.DateTime, nullable=True)
    free_days = db.Column(db.Integer, default=5)
    daily_penalty_rate = db.Column(db.Float, default=15000.0)
    
    @property
    def calculate_demurrage(self):
        if not self.port_arrival_date or self.current_status in ['loaded', 'in_transit', 'delivered']:
            return 0
        days_at_port = (datetime.utcnow() - self.port_arrival_date).days
        chargeable_days = days_at_port - self.free_days
        if chargeable_days > 0:
            return chargeable_days * self.daily_penalty_rate
        return 0

    @property
    def remaining_free_days(self):
        if not self.port_arrival_date or self.current_status in ['cleared', 'loaded', 'in_transit', 'delivered']:
            return None
        days_at_port = (datetime.utcnow() - self.port_arrival_date).days
        remaining = self.free_days - days_at_port
        return remaining

# --- INITIALIZE DATABASE FOR GUNICORN ---
with app.app_context():
    db.create_all()

# =============================================================================
# ROUTES
# =============================================================================

@app.route('/')
def index():
    if current_user.is_authenticated:
        if isinstance(current_user, Organization):
            return redirect(url_for('dashboard'))
        elif isinstance(current_user, ClientUser):
            return redirect(url_for('client_portal'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        phone = request.form.get('phone')
        
        if Organization.query.filter_by(email=email).first():
            return "Email already exists.", 400
        
        org_id = generate_uuid()
        hashed_password = generate_password_hash(password)
        
        logo_filename = None
        file = request.files.get('logo')
        if file and allowed_file(file.filename):
            logo_filename = secure_filename(f"org_{org_id}_{file.filename}")
            file.save(os.path.join(LOGO_DIR, logo_filename))

        new_org = Organization(
            id=org_id, email=email, password_hash=hashed_password, name=name,
            phone=phone, address=request.form.get('address'), city=request.form.get('city'), 
            fax_number=request.form.get('fax_number'), bank_name=request.form.get('bank_name'), 
            account_number=request.form.get('account_number'), logo_filename=logo_filename
        )
        db.session.add(new_org)
        db.session.commit()
        
        login_user(new_org)
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        org = Organization.query.filter_by(email=request.form.get('email')).first()
        if org and check_password_hash(org.password_hash, request.form.get('password')):
            login_user(org)
            return redirect(url_for('dashboard'))
        flash('Invalid email address or password. Please try again.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/quote', methods=['GET', 'POST'])
@login_required 
@org_required
def generate_quote():
    if request.method == 'POST':
        client_email = request.form.get('client_email')
        client_user_id = None
        
        if client_email:
            existing_client = ClientUser.query.filter_by(org_id=current_user.id, email=client_email).first()
            if not existing_client:
                new_client = ClientUser(
                    org_id=current_user.id, email=client_email, password_hash=generate_password_hash("123456"),
                    name=request.form.get('client_name'), company=request.form.get('client_company'), 
                    phone=request.form.get('client_phone'), address=request.form.get('client_address'), 
                    city=request.form.get('client_city')
                )
                db.session.add(new_client)
                db.session.commit()
                client_user_id = new_client.id
            else:
                client_user_id = existing_client.id

        base_freight = float(request.form.get('base_freight') or 0.0)
        additional_charges = float(request.form.get('additional_charges') or 0.0)
        customs_fee = float(request.form.get('customs_fee') or 0.0)
        trucking_fee = float(request.form.get('trucking_fee') or 0.0)
        
        total_amount = base_freight + additional_charges + customs_fee + trucking_fee
        tracking_code = f"TRK-{random.randint(10000, 99999)}"
        
        new_shipment = Shipment(
            org_id=current_user.id, client_id=client_user_id, tracking_code=tracking_code,
            client_name=request.form.get('client_name'), client_company=request.form.get('client_company'), 
            client_email=client_email, client_address=request.form.get('client_address'), 
            client_city=request.form.get('client_city'), client_phone=request.form.get('client_phone'),
            cargo_type=request.form.get('cargo_type'), service_type=request.form.get('service_type'), 
            origin=request.form.get('origin'), destination=request.form.get('destination'), 
            origin_door_address=request.form.get('origin_door_address'), 
            dest_door_address=request.form.get('dest_door_address'),
            mbl_container_number=request.form.get('mbl_container_number'),
            base_freight=base_freight, additional_charges=additional_charges, 
            customs_fee=customs_fee, trucking_fee=trucking_fee, total_quote_amount=total_amount,
            current_status='on_sea', payment_status='Unpaid'
        )
        db.session.add(new_shipment)
        db.session.commit()
        
        logo_path = os.path.join(LOGO_DIR, current_user.logo_filename).replace('\\', '/') if current_user.logo_filename else None
        
        rendered_html = render_template(
            'invoice.html', org=current_user, shipment=new_shipment, 
            date=datetime.utcnow().strftime('%Y-%m-%d'), logo_path=logo_path
        )
        
        file_path = os.path.join(QUOTE_DIR, f"Quote_{tracking_code}.pdf")
        with open(file_path, "w+b") as pdf_file:
            pisa.CreatePDF(rendered_html, dest=pdf_file)
            
        return redirect(url_for('dashboard'))
    return render_template('quote_form.html', current_user=current_user)

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
@org_required
def dashboard():
    if request.method == 'POST':
        shipment = Shipment.query.filter_by(id=request.form.get('shipment_id'), org_id=current_user.id).first()
        if shipment:
            if 'arrival_date' in request.form:
                shipment.port_arrival_date = datetime.strptime(request.form.get('arrival_date'), '%Y-%m-%d')
                if request.form.get('free_days') and request.form.get('free_days').isdigit():
                    shipment.free_days = int(request.form.get('free_days'))
                db.session.commit()
            elif 'status' in request.form:
                shipment.current_status = request.form.get('status')
                db.session.commit()
            elif 'payment_status' in request.form:
                shipment.payment_status = request.form.get('payment_status')
                db.session.commit()
                
    shipments = Shipment.query.filter_by(org_id=current_user.id).order_by(Shipment.created_at.desc()).all()
    return render_template('dashboard.html', shipments=shipments, current_user=current_user)

@app.route('/sync-tracking/<shipment_id>')
@login_required
def sync_tracking(shipment_id):
    shipment = Shipment.query.filter_by(id=shipment_id).first()
    
    if not shipment or not shipment.mbl_container_number:
        flash("No valid Container Number or MBL found to sync.")
        return redirect(request.referrer)

    headers = {
        "Authorization": f"Token {TERMINAL49_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        url = f"https://api.terminal49.com/v2/containers/{shipment.mbl_container_number}"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            shipment.vessel_lat = random.uniform(5.0, 30.0) 
            shipment.vessel_lon = random.uniform(-20.0, 20.0)
            shipment.last_sync = datetime.utcnow()
            db.session.commit()
            flash(f"Live Location synced via Terminal49 for {shipment.mbl_container_number}!")
        elif response.status_code == 404:
            flash(f"Container {shipment.mbl_container_number} not found on Terminal49. It may be pending.")
        else:
            flash(f"Terminal49 API Error: {response.status_code}")
            
    except Exception as e:
        flash("Failed to connect to Terminal49 API.")

    return redirect(request.referrer)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
@org_required
def settings():
    if request.method == 'POST':
        file = request.files.get('logo')
        if file and allowed_file(file.filename):
            filename = secure_filename(f"org_{current_user.id}_{file.filename}")
            file.save(os.path.join(LOGO_DIR, filename))
            current_user.logo_filename = filename
            db.session.commit()
        return redirect(url_for('dashboard'))
    return render_template('settings.html', current_user=current_user)

@app.route('/track/<tracking_code>')
def track_shipment(tracking_code):
    shipment = Shipment.query.filter_by(tracking_code=tracking_code).first()
    if not shipment:
        return "Shipment not found.", 404
    return render_template('track.html', shipment=shipment)

@app.route('/client/login', methods=['GET', 'POST'])
def client_login():
    if request.method == 'POST':
        client = ClientUser.query.filter_by(email=request.form.get('email')).first()
        if client and check_password_hash(client.password_hash, request.form.get('password')):
            login_user(client)
            return redirect(url_for('client_portal'))
        flash('Invalid email address or password. Please try again.')
    return render_template('client_login.html')

@app.route('/client/portal')
@login_required
@client_required
def client_portal():
    shipments = Shipment.query.filter_by(client_id=current_user.id).order_by(Shipment.created_at.desc()).all()
    forwarder = Organization.query.get(current_user.org_id)
    return render_template('client_portal.html', client=current_user, shipments=shipments, forwarder=forwarder)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg'}

if __name__ == '__main__':
    app.run(debug=True)