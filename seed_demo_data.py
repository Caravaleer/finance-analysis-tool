"""
Seed script to create realistic sample data for local testing across 12+ months and 8+ categories.
"""
import random
from datetime import datetime, timedelta
from api.index import app, db, User, Transaction

CATEGORIES_EXPENSE = [
    'Groceries & Food', 'Rent & Housing', 'Dining Out', 'Utilities & Bills',
    'Transport & Fuel', 'Shopping & Clothing', 'Entertainment', 'Healthcare'
]

CATEGORIES_INCOME = [
    'Salary & Bonus', 'Freelance & Consulting', 'Dividends & Investments'
]

def seed_data(username='analysistest'):
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            print(f"User {username} not found.")
            return

        # Clear existing
        Transaction.query.filter_by(user_id=user.id).delete()
        db.session.commit()

        start_date = datetime.now() - timedelta(days=365)
        txs = []

        # Generate ~250 transactions over 12 months
        for month_offset in range(13):
            month_date = start_date + timedelta(days=month_offset * 30)

            # Monthly Salary (1st of month)
            txs.append(Transaction(
                user_id=user.id,
                date=month_date.replace(day=1),
                category='Salary & Bonus',
                amount=round(random.uniform(45000, 55000), 2),
                type='income'
            ))

            # Occasional Freelance
            if random.random() > 0.4:
                txs.append(Transaction(
                    user_id=user.id,
                    date=month_date.replace(day=min(random.randint(10, 20), 28)),
                    category='Freelance & Consulting',
                    amount=round(random.uniform(8000, 20000), 2),
                    type='income'
                ))

            # Monthly Rent
            txs.append(Transaction(
                user_id=user.id,
                date=month_date.replace(day=2),
                category='Rent & Housing',
                amount=15000.0,
                type='expense'
            ))

            # Utilities
            txs.append(Transaction(
                user_id=user.id,
                date=month_date.replace(day=5),
                category='Utilities & Bills',
                amount=round(random.uniform(2200, 4500), 2),
                type='expense'
            ))

            # 15-20 varied transactions in the month
            for _ in range(random.randint(15, 20)):
                day_num = random.randint(1, 28)
                tx_date = month_date.replace(day=day_num, hour=random.randint(9, 21), minute=random.randint(0, 59))
                cat = random.choice(CATEGORIES_EXPENSE)
                if cat == 'Groceries & Food':
                    amt = round(random.uniform(400, 2500), 2)
                elif cat == 'Dining Out':
                    amt = round(random.uniform(300, 1800), 2)
                elif cat == 'Transport & Fuel':
                    amt = round(random.uniform(150, 1200), 2)
                elif cat == 'Shopping & Clothing':
                    amt = round(random.uniform(800, 4500), 2)
                elif cat == 'Entertainment':
                    amt = round(random.uniform(250, 1500), 2)
                elif cat == 'Healthcare':
                    amt = round(random.uniform(500, 3000), 2)
                else:
                    amt = round(random.uniform(200, 1000), 2)

                txs.append(Transaction(
                    user_id=user.id,
                    date=tx_date,
                    category=cat,
                    amount=amt,
                    type='expense'
                ))

        db.session.add_all(txs)
        db.session.commit()
        print(f"Successfully seeded {len(txs)} transactions across 12+ months for {username}!")

if __name__ == '__main__':
    seed_data()
