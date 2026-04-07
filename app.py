from functools import wraps
from flask import Flask, render_template, redirect, request, flash, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os

PASSWORD_HASH_METHOD = 'pbkdf2:sha256'

def hash_password(pw):
    return generate_password_hash(pw, method=PASSWORD_HASH_METHOD)

app = Flask(__name__)

app.config['SECRET_KEY'] = 'secret123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///auction.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads'

app.config['ADMIN_EMAIL'] = os.environ.get('ADMIN_EMAIL', '')
app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', '')


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100))
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(200))
    role = db.Column(db.String(20), default='user')
    contact_number = db.Column(db.String(20))
    address = db.Column(db.String(200))


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200))
    description = db.Column(db.Text)
    starting_price = db.Column(db.Float)
    current_price = db.Column(db.Float)
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='active')
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    winner_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    image = db.Column(db.String(200))
    winner_login_notified = db.Column(db.Boolean, default=False)


class Bid(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'))
    timestamp = db.Column(db.DateTime, default=datetime.now)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))



def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if current_user.role != 'admin':
            flash('Admin only access')
            return redirect(url_for('dashboard'))
        return view(*args, **kwargs)
    return wrapped


def finalize_item_if_due(item):
    if item.status != 'active':
        return
    if item.end_time > datetime.now():
        return

    top = Bid.query.filter_by(item_id=item.id).order_by(Bid.amount.desc()).first()

    if top:
        item.winner_id = top.user_id
        item.current_price = top.amount
        item.status = 'sold'
    else:
        item.status = 'ended'


def process_all_due_items():
    items = Item.query.filter(Item.status == 'active').all()
    for item in items:
        finalize_item_if_due(item)
    db.session.commit()



def flash_winner_login_notifications(user):
    wins = Item.query.filter_by(
        winner_id=user.id,
        status='sold',
        winner_login_notified=False
    ).all()

    for item in wins:
        flash(f'🎉 You won {item.name} for ₹{item.current_price}', 'success')
        item.winner_login_notified = True

    db.session.commit()



def ensure_default_admin():
    email = app.config['ADMIN_EMAIL']
    password = app.config['ADMIN_PASSWORD']

    if not email or not password:
        return

    user = User.query.filter_by(email=email).first()

    if user:
        user.role = 'admin'
    else:
        db.session.add(User(
            username='Admin',
            email=email,
            password=hash_password(password),
            role='admin'
        ))

    db.session.commit()


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['email']).first()

        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            flash_winner_login_notifications(user)

            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('dashboard'))

        flash('Invalid credentials')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        db.session.add(User(
            username=request.form['username'],
            email=request.form['email'],
            password=hash_password(request.form['password']),
            role='user'
        ))
        db.session.commit()
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/dashboard')
@login_required
def dashboard():
    process_all_due_items()

    items = Item.query.order_by(Item.end_time.desc()).all()

    winners_map = {}
    bids_data = {}

    for item in items:
        if item.winner_id:
            winners_map[item.id] = User.query.get(item.winner_id)

        bids = Bid.query.filter_by(item_id=item.id).order_by(Bid.amount.desc()).all()

        bids_data[item.id] = []
        for b in bids:
            user = User.query.get(b.user_id)
            bids_data[item.id].append({
                'username': user.username if user else 'Unknown',
                'amount': b.amount,
                'time': b.timestamp
            })

    return render_template(
        'dashboard.html',
        items=items,
        winners=winners_map,
        bids_data=bids_data,
        now=datetime.now()
    )


@app.route('/my_wins')
@login_required
def my_wins():
    wins = Item.query.filter_by(winner_id=current_user.id, status='sold').all()

    data = []
    for item in wins:
        seller = User.query.get(item.seller_id)
        data.append({'item': item, 'seller': seller})

    return render_template('my_wins.html', wins=data)


@app.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        file = request.files.get('image')
        filename = None

        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db.session.add(Item(
            name=request.form['item_name'],
            description=request.form['description'],
            starting_price=float(request.form['base_price']),
            current_price=float(request.form['base_price']),
            end_time=datetime.strptime(request.form['end_time'], "%Y-%m-%dT%H:%M"),
            seller_id=current_user.id,
            image=filename
        ))
        db.session.commit()
        return redirect(url_for('dashboard'))

    return render_template('create.html')


@app.route('/bid/<int:id>', methods=['POST'])
@login_required
def bid(id):
    item = Item.query.get(id)
    amount = float(request.form['bid_amount'])

    if amount > item.current_price:
        item.current_price = amount
        db.session.add(Bid(amount=amount, user_id=current_user.id, item_id=id))
        db.session.commit()

    return redirect(url_for('dashboard'))


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_user.contact_number = request.form.get('contact_number')
        current_user.address = request.form.get('address')
        db.session.commit()
        flash('Profile updated')
        return redirect(url_for('profile'))

    return render_template('profile.html', user=current_user)


@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    process_all_due_items()

    items = Item.query.order_by(Item.end_time.desc()).all()

    stats = {
        'active': Item.query.filter_by(status='active').count(),
        'sold': Item.query.filter_by(status='sold').count(),
        'users': User.query.count(),
        'bids': Bid.query.count()
    }

    return render_template(
        'admin_dashboard.html',
        items=items,
        stats=stats,
        now=datetime.now()
    )


@app.route('/admin/add_item', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_add_item():
    users = User.query.filter(User.role != 'admin').all()

    if request.method == 'POST':
        file = request.files.get('image')
        filename = None

        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        db.session.add(Item(
            name=request.form['item_name'],
            description=request.form['description'],
            starting_price=float(request.form['base_price']),
            current_price=float(request.form['base_price']),
            end_time=datetime.strptime(request.form['end_time'], "%Y-%m-%dT%H:%M"),
            seller_id=int(request.form['seller_id']),
            image=filename
        ))

        db.session.commit()
        flash("Item added by admin")
        return redirect(url_for('admin_dashboard'))

    return render_template('admin_add_item.html', users=users)


@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/bids')
@login_required
@admin_required
def admin_bids():
    bids = Bid.query.order_by(Bid.timestamp.desc()).all()
    return render_template('admin_bids.html', bids=bids)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')




if __name__ == '__main__':
    os.makedirs('static/uploads', exist_ok=True)

    with app.app_context():
        db.create_all()
        ensure_default_admin()

    app.run(debug=True)