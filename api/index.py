import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
from flask_sqlalchemy import SQLAlchemy
import datetime
from sqlalchemy import text
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
# For month-wise analysis
import pandas as pd
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', '').replace("postgres://", "postgresql://") + "?sslmode=require"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your_secret_key')

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# User model
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

# Transactions model
class Transaction(db.Model):
    __tablename__ = "transactions"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Route to register a new user
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = bcrypt.generate_password_hash(request.form['password']).decode('utf-8')
        new_user = User(username=username, password=password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

# Route to log in a user
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
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
    transactions = db.session.execute(
        text('SELECT * FROM "transactions" WHERE user_id = :user_id ORDER BY date DESC'),
        {'user_id': current_user.id}
    ).fetchall()
    balance = sum(t.amount if t.type == 'income' else -t.amount for t in transactions)
    return render_template('index.html', transactions=transactions, balance=balance, username=current_user.username)

# Route to add a transaction
@app.route('/add', methods=['POST'])
@login_required
def add_transaction():
    category = request.form['category']
    amount = float(request.form['amount'])
    type = request.form['type']
    new_transaction = Transaction(category=category, amount=amount, type=type, user_id=current_user.id)
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
    # Update date from form (format YYYY-MM-DD)
    transaction.date = datetime.strptime(request.form['date'], "%Y-%m-%d")
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
            # Ensure we have all months of the current year.
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
        # Overall analysis (unchanged)
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

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'xls', 'xlsx'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_xlsx(file_path):
    # Read the Excel file with the correct header
    df = pd.read_excel(file_path, header=0)  # Ensure header is on the first row

    # Strip spaces from column names
    df.columns = df.columns.str.strip()

    # Debugging: Print column names to verify
    print("Column Names:", df.columns)

    # Convert the 'Date' column to datetime format
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce', format='%d/%m/%y')

    # Drop any rows where the Date is NaT (invalid)
    df = df.dropna(subset=['Date'])

    transactions = []
    for _, row in df.iterrows():
        try:
            date = row['Date']
            category = row['Narration']
            withdrawal = row['Withdrawal Amt.'] if not pd.isna(row['Withdrawal Amt.']) else 0
            deposit = row['Deposit Amt.'] if not pd.isna(row['Deposit Amt.']) else 0

            # Determine transaction type
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
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(request.url)

    if file:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
        file.save(file_path)

        try:
            df = pd.read_excel(file_path)

            # Ensure column names are clean
            df.columns = df.columns.str.strip()
            print("Columns:", repr(df.columns))  # Debugging output

            transactions = []

            for _, row in df.iterrows():
                try:
                    date = pd.to_datetime(row['Date'], format='%d/%m/%y', errors='coerce')

                    # Ensure date is valid
                    if pd.isna(date):
                        print(f"Skipping invalid date in row: {row}")
                        continue

                    narration = row.get('Narration', '').strip()
                    withdrawal = row.get('Withdrawal Amt.', 0) if not pd.isna(row.get('Withdrawal Amt.', 0)) else 0
                    deposit = row.get('Deposit Amt.', 0) if not pd.isna(row.get('Deposit Amt.', 0)) else 0

                    # Determine transaction type and amount
                    amount = deposit if deposit > 0 else withdrawal
                    trans_type = "income" if deposit > 0 else "expense"

                    transaction = Transaction(
                        date=date,
                        category=narration[:50],
                        amount=amount,
                        type=trans_type,
                        user_id=current_user.id
                    )

                    db.session.add(transaction)  # Add each transaction separately
                    db.session.flush()  # Ensure it's written to the DB buffer

                    transactions.append(transaction)

                except Exception as e:
                    print(f"Error processing row: {row}\nError: {e}")

            if not transactions:
                flash("No valid transactions found in the file.", "warning")
                return redirect(url_for('index'))

            db.session.commit()  # Commit all transactions at once
            flash("Transactions uploaded successfully!", "success")
            return redirect(url_for('index'))

        except Exception as e:
            db.session.rollback()  # Rollback in case of failure
            print(f"Error during upload: {e}")
            flash(f"An error occurred: {e}", "danger")
            return redirect(url_for('index'))

    return redirect(url_for('index'))

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
