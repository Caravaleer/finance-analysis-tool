import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import datetime
from sqlalchemy import text
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

# For month-wise analysis
import pandas as pd

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
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
    transaction.date = datetime.datetime.strptime(request.form['date'], "%Y-%m-%d")
    db.session.commit()
    return redirect(url_for('index'))

# Analysis Route for transaction graphs
@app.route('/analysis')
@login_required
def analysis():
    graph_type = request.args.get('graph', 'overall')
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    
    if not transactions:
        return render_template('analysis.html', msg="No transactions available for analysis", username=current_user.username)
    
    if graph_type == 'month':
        # Group transactions by month using pandas
        data = [{
            'date': t.date,
            'amount': t.amount,
            'type': t.type
        } for t in transactions]
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        df['month'] = df['date'].dt.strftime('%Y-%m')
        
        # Group income and expense by month
        income_df = df[df['type'] == 'income'].groupby('month')['amount'].sum().reset_index()
        expense_df = df[df['type'] == 'expense'].groupby('month')['amount'].sum().reset_index()
        months = sorted(set(df['month']))
        
        income_dict = income_df.set_index('month')['amount'].to_dict()
        expense_dict = expense_df.set_index('month')['amount'].to_dict()
        
        income_values = [income_dict.get(month, 0) for month in months]
        expense_values = [expense_dict.get(month, 0) for month in months]
        
        return render_template('analysis.html',
                               username=current_user.username,
                               graph_type='month',
                               months=months,
                               income_values=income_values,
                               expense_values=expense_values)
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
