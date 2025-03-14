import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_sqlalchemy import SQLAlchemy
import datetime
from flask.cli import with_appcontext
import click
from sqlalchemy import text
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import io, base64
import matplotlib.pyplot as plt
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

# Route to edit a transaction (using modal form, expects POST)
@app.route('/edit/<int:id>', methods=['POST'])
@login_required
def edit_transaction(id):
    transaction = db.session.get(Transaction, id)
    if not transaction or transaction.user_id != current_user.id:
        return redirect(url_for('index'))
    transaction.category = request.form['category']
    transaction.amount = float(request.form['amount'])
    transaction.type = request.form['type']
    db.session.commit()
    return redirect(url_for('index'))

# New Analysis Route to display graphs
@app.route('/analysis')
@login_required
def analysis():
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    if not transactions:
        return render_template('analysis.html', msg="No transactions available for analysis", username=current_user.username)
    
    # Convert transactions to a DataFrame
    df = pd.DataFrame([{
        'date': t.date, 'category': t.category, 'amount': t.amount, 'type': t.type
    } for t in transactions])
    
    # Income vs Expense Pie Chart
    income_total = df[df['type'] == 'income']['amount'].sum()
    expense_total = df[df['type'] == 'expense']['amount'].sum()
    fig1, ax1 = plt.subplots()
    ax1.pie([income_total, expense_total], labels=['Income', 'Expense'], autopct='%1.1f%%', colors=['#28a745', '#dc3545'], startangle=90)
    ax1.axis('equal')
    buf1 = io.BytesIO()
    fig1.savefig(buf1, format='png', bbox_inches="tight")
    buf1.seek(0)
    img_income_vs_expense = base64.b64encode(buf1.getvalue()).decode('utf-8')
    plt.close(fig1)
    
    # Bar Chart for Expense by Category (if expenses exist)
    expense_df = df[df['type'] == 'expense']
    img_category = None
    if not expense_df.empty:
        category_totals = expense_df.groupby('category')['amount'].sum().sort_values(ascending=False)
        fig2, ax2 = plt.subplots()
        category_totals.plot(kind='bar', color='#007BFF', ax=ax2)
        ax2.set_title('Expense by Category')
        ax2.set_ylabel('Total Amount (₹)')
        plt.xticks(rotation=45, ha='right')
        buf2 = io.BytesIO()
        fig2.savefig(buf2, format='png', bbox_inches="tight")
        buf2.seek(0)
        img_category = base64.b64encode(buf2.getvalue()).decode('utf-8')
        plt.close(fig2)
    
    return render_template('analysis.html', username=current_user.username,
                           img_income_vs_expense="data:image/png;base64," + img_income_vs_expense,
                           img_category= "data:image/png;base64," + img_category if img_category else None)

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
