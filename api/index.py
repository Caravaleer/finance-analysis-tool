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

def get_database_uri():
    raw_url = os.getenv('DATABASE_URL', '')
    if not raw_url:
        return 'sqlite:///finances.db'

    # Fix legacy postgres:// dialect prefix used by older services
    if raw_url.startswith('postgres://'):
        raw_url = raw_url.replace('postgres://', 'postgresql://', 1)

    # For remote PostgreSQL (e.g. Supabase), ensure sslmode=require if not already present
    if 'postgresql://' in raw_url and 'sslmode=' not in raw_url:
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
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.date.desc()).all()
    balance = sum(t.amount if t.type == 'income' else -t.amount for t in transactions)
    return render_template('index.html', date=date, transactions=transactions, balance=balance, username=current_user.username)

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

# Route to delete a transaction
@app.route('/delete/<int:id>')
@login_required
def delete_transaction(id):
    transaction = db.session.get(Transaction, id)
    if transaction and transaction.user_id == current_user.id:
        db.session.delete(transaction)
        db.session.commit()
    return redirect(url_for('index'))

# Route to edit a transaction (including editing the date)
@app.route('/edit/<int:id>', methods=['POST'])
@login_required
def edit_transaction(id):
    transaction = db.session.get(Transaction, id)
    if not transaction or transaction.user_id != current_user.id:
        return redirect(url_for('index'))
    transaction.category = request.form['category']
    transaction.amount = float(request.form['amount'])
    transaction.type = request.form['type']
    date_str = request.form.get('date')
    if date_str:
        try:
            transaction.date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            pass
    db.session.commit()
    return redirect(url_for('index'))

# Analysis Route for transaction graphs
@app.route('/analysis', endpoint='analysis')
@login_required
def analysis():
    graph_type = request.args.get('graph', 'overall')
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    
    if not transactions:
        return render_template('analysis.html', msg="No transactions available for analysis", username=current_user.username)
    
    if graph_type == 'month':
        data = [{
            'date': t.date,
            'amount': t.amount,
            'type': t.type,
            'category': t.category
        } for t in transactions]
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df['month'] = df['date'].dt.strftime('%Y-%m')
        
        # Get selected month; default to current month if not provided.
        selected_month = request.args.get('selected_month', datetime.now().strftime('%Y-%m'))
        # Filter expense transactions for the selected month.
        df_month = df[(df['type'] == 'expense') & (df['month'] == selected_month)]
        monthly_category_data = df_month.groupby('category')['amount'].sum().to_dict()
        
        # Determine selected category for the yearly line graph.
        selected_category = request.args.get('selected_category')
        if not selected_category and monthly_category_data:
            selected_category = list(monthly_category_data.keys())[0]
        
        # For the line graph: get expense transactions for the selected category in the current year.
        current_year = datetime.now().year
        df_year = df[(df['type'] == 'expense') & (df['date'].dt.year == current_year)]
        if selected_category:
            df_category = df_year[df_year['category'] == selected_category].copy()
            df_category['month'] = df_category['date'].dt.strftime('%Y-%m')
            all_months = pd.date_range(f'{current_year}-01-01', f'{current_year}-12-01', freq='MS').strftime('%Y-%m')
            yearly_data = df_category.groupby('month')['amount'].sum().reindex(all_months, fill_value=0)
            monthly_labels = list(yearly_data.index)
            monthly_values = list(yearly_data.values)
        else:
            monthly_labels = []
            monthly_values = []
        
        return render_template('analysis.html',
                            username=current_user.username,
                            graph_type='month',
                            selected_month=selected_month,
                            monthly_category_data=monthly_category_data,
                            selected_category=selected_category,
                            monthly_labels=monthly_labels,
                            monthly_values=monthly_values)
    else:
        # Overall analysis
        total_income = sum(t.amount for t in transactions if t.type == 'income')
        total_expense = sum(t.amount for t in transactions if t.type == 'expense')
        expense_categories = {}
        for t in transactions:
            if t.type == 'expense':
                expense_categories[t.category] = expense_categories.get(t.category, 0) + t.amount
        
        return render_template('analysis.html',
                               username=current_user.username,
                               graph_type='overall',
                               total_income=total_income,
                               total_expense=total_expense,
                               expense_categories=expense_categories)

                               
# API Route for transactions
@app.route('/api/transactions', methods=['GET'])
@login_required
def get_transactions():
    transactions = db.session.execute(
        text('SELECT * FROM "transactions" WHERE user_id = :user_id ORDER BY date DESC'),
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
