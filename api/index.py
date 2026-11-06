import os
import io
import csv
import tempfile
from datetime import datetime
import pandas as pd
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

app = Flask(__name__)

# Jinja template helpers
app.jinja_env.globals['hasattr'] = hasattr

@app.template_filter('format_date')
def format_date(value):
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    return str(value)[:10]

def get_database_uri():
    raw_url = os.getenv('DATABASE_URL', '')
    if not raw_url:
        return 'sqlite:///finances.db'

    # Check available drivers
    try:
        import psycopg  # noqa: F401
        has_psycopg3 = True
    except ImportError:
        has_psycopg3 = False

    try:
        import psycopg2  # noqa: F401
        has_psycopg2 = True
    except ImportError:
        has_psycopg2 = False

    # Fix legacy postgres:// dialect prefix
    if raw_url.startswith('postgres://'):
        raw_url = raw_url.replace('postgres://', 'postgresql://', 1)

    # Route to the available driver
    if raw_url.startswith('postgresql+psycopg://') and not has_psycopg3 and has_psycopg2:
        raw_url = raw_url.replace('postgresql+psycopg://', 'postgresql+psycopg2://', 1)
    elif raw_url.startswith('postgresql://'):
        if not has_psycopg3 and has_psycopg2:
            raw_url = raw_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
        elif has_psycopg3 and not has_psycopg2:
            raw_url = raw_url.replace('postgresql://', 'postgresql+psycopg://', 1)

    # For remote PostgreSQL (e.g. Supabase), ensure sslmode=require if not already present
    if 'postgresql' in raw_url and 'sslmode=' not in raw_url:
        separator = '&' if '?' in raw_url else '?'
        raw_url = f"{raw_url}{separator}sslmode=require"

    return raw_url

app.config['SQLALCHEMY_DATABASE_URI'] = get_database_uri()
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# User model
class User(db.Model, UserMixin):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

# Transactions model
class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.now)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# Auto-create tables on startup/cold-start (in serverless, if __name__ == '__main__' is never executed)
with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        app.logger.warning(f"Database initialization notice: {e}")

@app.route('/init-db')
def init_db():
    """Manual endpoint to verify or initialize database tables."""
    try:
        db.create_all()
        return jsonify({
            "status": "success",
            "message": "Database tables ('user', 'transactions') created or verified successfully!"
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

def calc(curr_bal, days_gone, expenses, days_left, save):
    per_day_allowance = (curr_bal - save) / days_left
    projected_expenses = expenses + (expenses / days_gone) * days_left
    return per_day_allowance, projected_expenses

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# Route to register a new user
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        raw_password = request.form['password']
        password = bcrypt.generate_password_hash(raw_password).decode('utf-8')

        new_user = User(username=username, password=password)
        db.session.add(new_user)

        try:
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))

        except IntegrityError as e:
            db.session.rollback()
            err_msg = str(e)
            if 'user_username_key' in err_msg or 'UNIQUE constraint' in err_msg or 'duplicate key' in err_msg:
                flash('That username is already taken. Please choose another.', 'warning')
            else:
                flash('An unexpected database constraint error occurred. Please try again.', 'danger')
            return render_template('register.html', username=username)

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error during registration: {e}")
            flash(f'An error occurred: {str(e)}', 'danger')
            return render_template('register.html', username=username)

    # GET request
    return render_template('register.html')

# Route to log in a user
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        try:
            user = User.query.filter_by(username=username).first()
            if user and bcrypt.check_password_hash(user.password, password):
                login_user(user)
                return redirect(url_for('index'))
            else:
                flash('Incorrect username or password. Please try again.', 'warning')
                return render_template('login.html', username=username)
        except Exception as e:
            app.logger.error(f"Error during login: {e}")
            flash(f'An error occurred: {str(e)}', 'danger')
            return render_template('login.html', username=username)

    # GET request
    return render_template('login.html')

# Route to log out a user
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Route to display transactions and balance
@app.route('/')
@login_required
def index():
    date = datetime.now().strftime('%Y-%m-%d')
    # Compute global balance and total count directly in DB
    totals_query = text("""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END), 0) AS balance,
            COUNT(*) AS total_count
        FROM transactions
        WHERE user_id = :user_id
    """)
    totals_row = db.session.execute(totals_query, {'user_id': current_user.id}).fetchone()
    balance = round(float(totals_row[0]), 2)
    total_count = int(totals_row[1])

    return render_template(
        'index.html',
        date=date,
        balance=balance,
        total_count=total_count,
        username=current_user.username
    )

# Route to add a transaction
@app.route('/add', methods=['POST'])
@login_required
def add_transaction():
    category = request.form['category']
    amount = float(request.form['amount'])
    date_str = request.form.get('date')
    if date_str:
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            date = datetime.now()
    else:
        date = datetime.now()
    type = request.form['type']
    new_transaction = Transaction(category=category, amount=amount, date=date, type=type, user_id=current_user.id)
    db.session.add(new_transaction)
    db.session.commit()
    return redirect(url_for('index'))

# Route to batch add multiple transactions for a specific date
@app.route('/batch_add', methods=['POST'])
@login_required
def batch_add():
    date_str = request.form.get('batch_date') or request.form.get('date')
    if date_str:
        try:
            date = datetime.strptime(date_str, '%Y-%m-%d')
        except (ValueError, TypeError):
            date = datetime.now()
    else:
        date = datetime.now()

    categories = request.form.getlist('category[]')
    if not categories:
        categories = request.form.getlist('category')

    amounts = request.form.getlist('amount[]')
    if not amounts:
        amounts = request.form.getlist('amount')

    types = request.form.getlist('type[]')
    if not types:
        types = request.form.getlist('type')

    added_count = 0
    for cat, amt, typ in zip(categories, amounts, types):
        cat_clean = str(cat).strip()
        if not cat_clean:
            continue
        try:
            amt_val = float(amt)
        except (ValueError, TypeError):
            continue
        if amt_val <= 0:
            continue

        typ_clean = str(typ).strip().lower()
        if typ_clean not in ['income', 'expense']:
            typ_clean = 'expense'

        new_transaction = Transaction(
            category=cat_clean[:50],
            amount=amt_val,
            date=date,
            type=typ_clean,
            user_id=current_user.id
        )
        db.session.add(new_transaction)
        added_count += 1

    if added_count > 0:
        db.session.commit()
        flash(f"Successfully added {added_count} transactions for {date.strftime('%b %d, %Y')}!", 'success')
    else:
        flash("No valid transactions found to add. Please check the entries.", 'warning')

    return redirect(url_for('index'))

# Route to delete a transaction
@app.route('/delete/<int:id>', methods=['GET', 'POST'])
@login_required
def delete_transaction(id):
    transaction = db.session.get(Transaction, id)
    if transaction and transaction.user_id == current_user.id:
        db.session.delete(transaction)
        db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'success': True, 'id': id})
    elif request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'error': 'Transaction not found'}), 404
    return redirect(url_for('index'))

# Route to edit a transaction (including editing the date)
@app.route('/edit/<int:id>', methods=['POST'])
@login_required
def edit_transaction(id):
    transaction = db.session.get(Transaction, id)
    if not transaction or transaction.user_id != current_user.id:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({'error': 'Transaction not found'}), 404
        return redirect(url_for('index'))

    if request.is_json:
        data = request.get_json()
        category = data.get('category', '').strip()
        amount = float(data.get('amount', 0))
        tx_type = data.get('type', 'expense').strip().lower()
        date_str = data.get('date')
    else:
        category = request.form.get('category', '').strip()
        amount = float(request.form.get('amount', 0))
        tx_type = request.form.get('type', 'expense').strip().lower()
        date_str = request.form.get('date')

    transaction.category = category[:50]
    transaction.amount = amount
    transaction.type = tx_type if tx_type in ['income', 'expense'] else 'expense'
    if date_str:
        try:
            transaction.date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            pass
    db.session.commit()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'success': True, 'id': id})
    return redirect(url_for('index'))

# Analysis Route
@app.route('/analysis', endpoint='analysis')
@login_required
def analysis():
    return render_template('analysis.html', username=current_user.username)

# API Route: Analytics Data Endpoint
@app.route('/api/analysis/data', methods=['GET'])
@login_required
def api_analysis_data():
    from api.analytics import get_analytics_data
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    categories = request.args.getlist('categories[]')
    if not categories and request.args.get('categories'):
        categories = [c.strip() for c in request.args.get('categories').split(',') if c.strip()]
    tx_type = request.args.get('type', 'all')
    compare_m1 = request.args.get('compare_m1')
    compare_m2 = request.args.get('compare_m2')

    data = get_analytics_data(
        db=db,
        user_id=current_user.id,
        start_date=start_date,
        end_date=end_date,
        categories=categories,
        tx_type=tx_type,
        compare_m1=compare_m1,
        compare_m2=compare_m2
    )
    return jsonify(data)

# API Route: Analytics Categories
@app.route('/api/analysis/categories', methods=['GET'])
@login_required
def api_analysis_categories():
    from api.analytics import get_analytics_categories
    categories = get_analytics_categories(db, current_user.id)
    return jsonify(categories)

# API Route: Paginated Transactions for Dashboard
@app.route('/api/transactions/paginated', methods=['GET'])
@login_required
def get_paginated_transactions():
    try:
        page = max(int(request.args.get('page', 1)), 1)
    except (ValueError, TypeError):
        page = 1

    try:
        per_page = int(request.args.get('per_page', 20))
        if per_page not in [10, 20, 50, 100]:
            per_page = 20
    except (ValueError, TypeError):
        per_page = 20

    search = request.args.get('search', '').strip()
    tx_type = request.args.get('type', 'all').strip().lower()
    category = request.args.get('category', '').strip()

    conditions = ["user_id = :user_id"]
    params = {'user_id': current_user.id}

    if search:
        conditions.append("LOWER(category) LIKE :search")
        params['search'] = f"%{search.lower()}%"

    if tx_type in ['income', 'expense']:
        conditions.append("type = :tx_type")
        params['tx_type'] = tx_type

    if category:
        conditions.append("category = :category")
        params['category'] = category

    where_sql = " AND ".join(conditions)

    # 1. Total filtered records
    count_query = text(f"SELECT COUNT(*) FROM transactions WHERE {where_sql}")
    total_count = db.session.execute(count_query, params).scalar() or 0

    total_pages = max(1, (total_count + per_page - 1) // per_page)
    if page > total_pages and total_count > 0:
        page = total_pages

    offset = (page - 1) * per_page
    params['limit'] = per_page
    params['offset'] = offset

    # 2. Paginated rows with consistent tie-breaking ordering: date DESC, id DESC
    dialect = db.session.get_bind().dialect.name
    date_fmt = "to_char(date, 'YYYY-MM-DD')" if dialect == 'postgresql' else "strftime('%Y-%m-%d', date)"

    rows_query = text(f"""
        SELECT id, {date_fmt} AS date_str, category, amount, type
        FROM transactions
        WHERE {where_sql}
        ORDER BY date DESC, id DESC
        LIMIT :limit OFFSET :offset
    """)
    rows = db.session.execute(rows_query, params).fetchall()

    tx_list = [{
        'id': r[0],
        'date': str(r[1]),
        'category': r[2],
        'amount': float(r[3]),
        'type': r[4]
    } for r in rows]

    # 3. Global Dashboard Totals (across ALL records for this user)
    global_query = text("""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END), 0) AS balance,
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS total_income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
            COUNT(*) AS total_records
        FROM transactions
        WHERE user_id = :user_id
    """)
    global_row = db.session.execute(global_query, {'user_id': current_user.id}).fetchone()

    return jsonify({
        'transactions': tx_list,
        'page': page,
        'per_page': per_page,
        'total_count': total_count,
        'total_pages': total_pages,
        'global_balance': round(float(global_row[0]), 2),
        'global_income': round(float(global_row[1]), 2),
        'global_expense': round(float(global_row[2]), 2),
        'total_records': int(global_row[3])
    })

# API Route for transactions (backward-compatible)
@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    transactions = db.session.execute(
        text('SELECT * FROM "transactions" WHERE user_id = :user_id ORDER BY date DESC, id DESC'),
        {'user_id': current_user.id}
    ).fetchall()
    transactions_list = [{
        'id': t.id, 'date': t.date.strftime('%Y-%m-%d'), 'category': t.category,
        'amount': t.amount, 'type': t.type
    } for t in transactions]
    return jsonify(transactions_list)

UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'uploads')
ALLOWED_EXTENSIONS = {'xls', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_xlsx(file_source):
    df = pd.read_excel(file_source, header=0)
    df.columns = df.columns.str.strip()
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce', format='%d/%m/%y')
    df = df.dropna(subset=['Date'])

    transactions = []
    for _, row in df.iterrows():
        try:
            date = row['Date']
            category = row['Narration']
            withdrawal = row['Withdrawal Amt.'] if not pd.isna(row['Withdrawal Amt.']) else 0
            deposit = row['Deposit Amt.'] if not pd.isna(row['Deposit Amt.']) else 0

            if withdrawal > 0:
                amount = withdrawal
                txn_type = "expense"
            else:
                amount = deposit
                txn_type = "income"

            transactions.append({
                'date': date,
                'category': category,
                'amount': amount,
                'type': txn_type
            })
        except Exception as e:
            print(f"Error processing row: {row}\nError: {e}")

    return transactions


@app.route('/upload', methods=['POST'])
@login_required
def upload_transactions():
    if 'file' not in request.files:
        flash('No file uploaded', 'error')
        return redirect(request.url)

    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        flash('No selected file or unsupported file type. Please upload .xls or .xlsx.', 'error')
        return redirect(request.url)

    try:
        # Read in memory using BytesIO to remain compatible with Vercel serverless read-only filesystem
        file_bytes = io.BytesIO(file.read())
        df = pd.read_excel(file_bytes)

        # Ensure column names are clean
        df.columns = df.columns.str.strip()

        transactions = []
        for _, row in df.iterrows():
            try:
                date = pd.to_datetime(row['Date'], format='%d/%m/%y', errors='coerce')
                if pd.isna(date):
                    continue

                narration = str(row.get('Narration', '')).strip()
                withdrawal = row.get('Withdrawal Amt.', 0) if not pd.isna(row.get('Withdrawal Amt.', 0)) else 0
                deposit = row.get('Deposit Amt.', 0) if not pd.isna(row.get('Deposit Amt.', 0)) else 0

                amount = deposit if deposit > 0 else withdrawal
                trans_type = "income" if deposit > 0 else "expense"

                transaction = Transaction(
                    date=date,
                    category=narration[:50],
                    amount=float(amount),
                    type=trans_type,
                    user_id=current_user.id
                )

                db.session.add(transaction)
                transactions.append(transaction)

            except Exception as e:
                print(f"Error processing row: {row}\nError: {e}")

        if not transactions:
            flash("No valid transactions found in the file.", "warning")
            return redirect(url_for('index'))

        db.session.commit()
        flash("Transactions uploaded successfully!", "success")
        return redirect(url_for('index'))

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error during upload: {e}")
        flash(f"An error occurred: {e}", "danger")
        return redirect(url_for('index'))

# Route for downloading all transactions (CSV or Excel)
@app.route('/download', methods=['GET','POST'])
@login_required
def download():
    return render_template('download.html', username=current_user.username)

@app.route('/download_transactions', methods=['POST'])
@login_required
def download_transactions():
    filetype = request.form.get('filetype', 'csv').lower()
    transactions = db.session.execute(
        text('SELECT * FROM "transactions" WHERE user_id = :user_id ORDER BY date DESC'),
        {'user_id': current_user.id}
    ).fetchall()

    if filetype in ['excel', 'xlsx']:
        # Prepare data for Excel
        data = [{
            'ID': t.id,
            'Date': t.date.strftime('%Y-%m-%d') if hasattr(t.date, 'strftime') else t.date,
            'Category': t.category,
            'Amount': t.amount,
            'Type': t.type
        } for t in transactions]
        df = pd.DataFrame(data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Transactions')
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment;filename=transactions.xlsx"}
        )
    else:
        # Default: CSV
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Date', 'Category', 'Amount', 'Type'])
        for t in transactions:
            writer.writerow([
                t.id,
                t.date.strftime('%Y-%m-%d') if hasattr(t.date, 'strftime') else t.date,
                t.category,
                t.amount,
                t.type
            ])
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=transactions.csv"}
        )

# Route for calculator page
@app.route('/calculate', methods=['GET', 'POST'])
@login_required
def calculate():
    result = None
    error = None
    days_gone = 1  # default
    days_left = None
    # Get transactions for the current user
    transactions = db.session.execute(
        text('SELECT * FROM "transactions" WHERE user_id = :user_id ORDER BY date DESC'),
        {'user_id': current_user.id}
    ).fetchall()
    balance = sum(t.amount if t.type == 'income' else -t.amount for t in transactions)

    # Filter expenses for only the current month
    now = datetime.now()
    expenses = sum(
        t.amount for t in transactions
        if t.type == 'expense' and t.date.year == now.year and t.date.month == now.month
    )
    if request.method == 'POST':
        try:
            days_gone = datetime.now().date().day
            # Default days_left is 30 - days_gone, unless user provided a value
            days_left = request.form.get('days_left')
            if days_left is None or days_left == '':
                days_left = 30 - days_gone
            else:
                days_left = int(days_left)

            save = float(request.form.get('save', 0))
            per_day_allowance, projected_expenses = calc(balance, days_gone, expenses, days_left, save)
            result = {
                'per_day_allowance': round(per_day_allowance, 2),
                'projected_expenses': round(projected_expenses, 2)
            }
        except Exception as e:
            error = f"Error: {e}"
    else:
        days_gone = datetime.now().date().day
        days_left = 30 - days_gone

    return render_template(
        'calculate.html',
        username=current_user.username,
        result=result,
        error=error,
        days_left=days_left,
        balance=balance,
        expenses=expenses
    )

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
