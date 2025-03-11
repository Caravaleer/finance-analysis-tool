import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
import datetime
from flask.cli import with_appcontext
import click
from sqlalchemy import text

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', '').replace("postgres://", "postgresql://") + "?sslmode=require"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database model for transactions
class Transaction(db.Model):
    __tablename__ = "transaction"  # Ensure correct case sensitivity
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    category = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'income' or 'expense'

# Route to display transactions and balance
@app.route('/')
def index():
    transactions = db.session.execute(text('SELECT * FROM "transaction" ORDER BY date DESC')).fetchall()
    balance = sum(t.amount if t.type == 'income' else -t.amount for t in transactions)
    return render_template('index.html', transactions=transactions, balance=balance)

# Route to add a transaction
@app.route('/add', methods=['POST'])
def add_transaction():
    category = request.form['category']
    amount = float(request.form['amount'])
    type = request.form['type']
    new_transaction = Transaction(category=category, amount=amount, type=type)
    db.session.add(new_transaction)
    db.session.commit()
    return redirect(url_for('index'))

# Route to delete a transaction
@app.route('/delete/<int:id>')
def delete_transaction(id):
    transaction = db.session.get(Transaction, id)
    if transaction:
        db.session.delete(transaction)
        db.session.commit()
    return redirect(url_for('index'))

# API Route for transactions
@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    transactions = db.session.execute(text('SELECT * FROM "transaction" ORDER BY date DESC')).fetchall()
    transactions_list = [{
        'id': t.id, 'date': t.date.strftime('%Y-%m-%d'), 'category': t.category,
        'amount': t.amount, 'type': t.type
    } for t in transactions]
    return jsonify(transactions_list)

if __name__ == '__main__':
    app.run(debug=True)
