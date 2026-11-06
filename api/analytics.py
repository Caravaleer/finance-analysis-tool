"""
Analytics & Aggregation Engine for Personal Finance Tracker.
Performs server-side and database-level aggregations scoped strictly by user_id.
Supports PostgreSQL (Supabase) with compatibility fallback for SQLite.
"""

from datetime import datetime, timedelta
from sqlalchemy import text


def get_month_expr(dialect_name):
    """Returns database-specific SQL expression to extract YYYY-MM."""
    if dialect_name == 'postgresql':
        return "to_char(date, 'YYYY-MM')"
    return "strftime('%Y-%m', date)"


def get_date_str_expr(dialect_name):
    """Returns database-specific SQL expression to extract YYYY-MM-DD."""
    if dialect_name == 'postgresql':
        return "to_char(date, 'YYYY-MM-DD')"
    return "strftime('%Y-%m-%d', date)"


def get_dow_expr(dialect_name):
    """
    Returns day-of-week integer (1=Monday ... 7=Sunday).
    PostgreSQL: extract(isodow from date) -> 1..7
    SQLite: (cast(strftime('%w', date) as integer) + 6) % 7 + 1 -> 1..7
    """
    if dialect_name == 'postgresql':
        return "EXTRACT(ISODOW FROM date)::INTEGER"
    return "(((CAST(strftime('%w', date) AS INTEGER) + 6) % 7) + 1)"


def get_analytics_categories(db, user_id):
    """Returns sorted list of distinct categories logged by the user."""
    query = text("""
        SELECT DISTINCT category
        FROM transactions
        WHERE user_id = :user_id AND category IS NOT NULL AND TRIM(category) != ''
        ORDER BY category ASC
    """)
    rows = db.session.execute(query, {'user_id': user_id}).fetchall()
    return [r[0] for r in rows]


def get_analytics_data(db, user_id, start_date=None, end_date=None, categories=None, tx_type='all', compare_m1=None, compare_m2=None):
    """
    Computes all analytical aggregations in the database scoped to current user.
    Returns a consolidated payload of KPIs, charts data, and rule-based insights.
    """
    dialect = db.session.get_bind().dialect.name
    month_expr = get_month_expr(dialect)
    date_str_expr = get_date_str_expr(dialect)
    dow_expr = get_dow_expr(dialect)

    # 1. Base Filter Conditions
    conditions = ["user_id = :user_id"]
    params = {'user_id': user_id}

    # Date boundaries
    parsed_start = None
    parsed_end = None
    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, '%Y-%m-%d')
            conditions.append("date >= :start_date")
            params['start_date'] = parsed_start
        except (ValueError, TypeError):
            pass

    if end_date:
        try:
            # End of day 23:59:59
            parsed_end = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
            conditions.append("date <= :end_date")
            params['end_date'] = parsed_end
        except (ValueError, TypeError):
            pass

    # Category filter
    if categories and isinstance(categories, list) and len(categories) > 0:
        cat_placeholders = []
        for idx, cat in enumerate(categories):
            p_name = f"cat_{idx}"
            cat_placeholders.append(f":{p_name}")
            params[p_name] = cat
        conditions.append(f"category IN ({', '.join(cat_placeholders)})")

    # Transaction type filter
    if tx_type in ['income', 'expense']:
        conditions.append("type = :tx_type")
        params['tx_type'] = tx_type

    where_clause = " AND ".join(conditions)

    # =========================================================================
    # A. Summary KPIs in Filtered Range
    # =========================================================================
    kpi_query = text(f"""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS total_income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS total_expense,
            COUNT(*) AS total_count,
            COUNT(DISTINCT {month_expr}) AS active_months,
            MIN(date) AS min_date,
            MAX(date) AS max_date
        FROM transactions
        WHERE {where_clause}
    """)
    kpi_row = db.session.execute(kpi_query, params).fetchone()

    total_income = float(kpi_row[0] or 0)
    total_expense = float(kpi_row[1] or 0)
    total_tx_count = int(kpi_row[2] or 0)
    active_months = max(int(kpi_row[3] or 1), 1)
    min_date = kpi_row[4]
    max_date = kpi_row[5]

    net_savings = total_income - total_expense
    savings_rate = round((net_savings / total_income * 100), 1) if total_income > 0 else 0.0
    avg_monthly_spend = round(total_expense / active_months, 2)

    # Highest spend category
    top_cat_query = text(f"""
        SELECT category, SUM(amount) AS total
        FROM transactions
        WHERE {where_clause} AND type = 'expense'
        GROUP BY category
        ORDER BY total DESC
        LIMIT 1
    """)
    top_cat_row = db.session.execute(top_cat_query, params).fetchone()
    highest_spend_category = top_cat_row[0] if top_cat_row else "None"
    highest_spend_amount = float(top_cat_row[1]) if top_cat_row else 0.0

    # Period-over-period comparison (matching previous time duration)
    prev_change_expense_pct = None
    prev_change_income_pct = None
    prev_total_expense = 0.0
    prev_total_income = 0.0

    if parsed_start and parsed_end:
        duration_days = (parsed_end.date() - parsed_start.date()).days + 1
        prev_end_date = parsed_start - timedelta(seconds=1)
        prev_start_date = prev_end_date - timedelta(days=duration_days)

        prev_kpi_query = text("""
            SELECT
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS prev_income,
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS prev_expense
            FROM transactions
            WHERE user_id = :user_id AND date >= :p_start AND date <= :p_end
        """)
        prev_row = db.session.execute(prev_kpi_query, {
            'user_id': user_id,
            'p_start': prev_start_date,
            'p_end': prev_end_date
        }).fetchone()

        if prev_row:
            prev_total_income = float(prev_row[0] or 0)
            prev_total_expense = float(prev_row[1] or 0)
            if prev_total_expense > 0:
                prev_change_expense_pct = round(((total_expense - prev_total_expense) / prev_total_expense) * 100, 1)
            if prev_total_income > 0:
                prev_change_income_pct = round(((total_income - prev_total_income) / prev_total_income) * 100, 1)

    # =========================================================================
    # B. Chart 1: Monthly Income vs Expenses & Net Savings
    # =========================================================================
    monthly_query = text(f"""
        SELECT
            {month_expr} AS month,
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense
        FROM transactions
        WHERE {where_clause}
        GROUP BY {month_expr}
        ORDER BY month ASC
    """)
    monthly_rows = db.session.execute(monthly_query, params).fetchall()

    monthly_labels = []
    monthly_income = []
    monthly_expenses = []
    monthly_net = []

    for r in monthly_rows:
        m = r[0]
        inc = float(r[1])
        exp = float(r[2])
        monthly_labels.append(m)
        monthly_income.append(inc)
        monthly_expenses.append(exp)
        monthly_net.append(round(inc - exp, 2))

    # =========================================================================
    # C. Chart 2: Spending by Category (Donut with percentages)
    # =========================================================================
    by_cat_query = text(f"""
        SELECT category, SUM(amount) AS total, COUNT(*) AS count
        FROM transactions
        WHERE {where_clause} AND type = 'expense'
        GROUP BY category
        ORDER BY total DESC
    """)
    by_cat_rows = db.session.execute(by_cat_query, params).fetchall()

    category_labels = []
    category_amounts = []
    category_percentages = []
    category_counts = []

    for r in by_cat_rows:
        cat = r[0]
        amt = float(r[1])
        cnt = int(r[2])
        pct = round((amt / total_expense * 100), 1) if total_expense > 0 else 0
        category_labels.append(cat)
        category_amounts.append(amt)
        category_percentages.append(pct)
        category_counts.append(cnt)

    # =========================================================================
    # D. Chart 3: Category x Month (Stacked Bar & Heatmap Matrix)
    # =========================================================================
    cat_month_query = text(f"""
        SELECT
            {month_expr} AS month,
            category,
            SUM(amount) AS total
        FROM transactions
        WHERE {where_clause} AND type = 'expense'
        GROUP BY {month_expr}, category
        ORDER BY month ASC, total DESC
    """)
    cat_month_rows = db.session.execute(cat_month_query, params).fetchall()

    # Build unique sets
    all_months = sorted(list(set(r[0] for r in cat_month_rows)))
    if not all_months and monthly_labels:
        all_months = monthly_labels

    all_categories = sorted(list(set(r[1] for r in cat_month_rows)))

    # Matrix for heatmap: { category: { month: amount } }
    heatmap_matrix = {cat: {m: 0.0 for m in all_months} for cat in all_categories}
    heatmap_max = 0.0
    heatmap_min = 0.0

    for r in cat_month_rows:
        m, cat, amt = r[0], r[1], float(r[2])
        if cat in heatmap_matrix and m in heatmap_matrix[cat]:
            heatmap_matrix[cat][m] = amt
            if amt > heatmap_max:
                heatmap_max = amt

    # Format for Stacked Bar datasets: [{ label: cat, data: [...] }]
    stacked_datasets = []
    for cat in all_categories:
        data_points = [heatmap_matrix[cat][m] for m in all_months]
        stacked_datasets.append({
            'label': cat,
            'data': data_points
        })

    # =========================================================================
    # E. Chart 4: Category Trends Over Time (Multi-line)
    # =========================================================================
    trends_datasets = []
    for cat in all_categories[:8]:  # Top 8 categories for clear readability
        data_points = [heatmap_matrix[cat][m] for m in all_months]
        trends_datasets.append({
            'label': cat,
            'data': data_points
        })

    # =========================================================================
    # F. Chart 5: Cumulative Balance / Running Net Worth
    # =========================================================================
    # Pull daily net changes to compute running balance cleanly
    daily_net_query = text(f"""
        SELECT
            {date_str_expr} AS day,
            SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END) AS net_change
        FROM transactions
        WHERE {where_clause}
        GROUP BY {date_str_expr}
        ORDER BY day ASC
    """)
    daily_rows = db.session.execute(daily_net_query, params).fetchall()

    cumulative_labels = []
    cumulative_values = []
    running_total = 0.0

    for r in daily_rows:
        day = r[0]
        net = float(r[1])
        running_total += net
        cumulative_labels.append(day)
        cumulative_values.append(round(running_total, 2))

    # =========================================================================
    # G. Chart 6: Top 10 Expenses in Range
    # =========================================================================
    top_expenses_query = text(f"""
        SELECT
            id,
            {date_str_expr} AS tx_date,
            category,
            amount
        FROM transactions
        WHERE {where_clause} AND type = 'expense'
        ORDER BY amount DESC, date DESC
        LIMIT 10
    """)
    top_expense_rows = db.session.execute(top_expenses_query, params).fetchall()

    top_expenses_list = [{
        'id': r[0],
        'date': str(r[1]),
        'category': r[2],
        'amount': float(r[3])
    } for r in top_expense_rows]

    # =========================================================================
    # H. Chart 7: Month-over-Month Comparison
    # =========================================================================
    # Default to last two available months if not specified
    if not compare_m1 or not compare_m2:
        if len(all_months) >= 2:
            compare_m1 = all_months[-2]
            compare_m2 = all_months[-1]
        elif len(all_months) == 1:
            compare_m1 = all_months[0]
            compare_m2 = all_months[0]
        else:
            now_m = datetime.now().strftime('%Y-%m')
            compare_m1 = now_m
            compare_m2 = now_m

    mom_query = text(f"""
        SELECT
            category,
            COALESCE(SUM(CASE WHEN {month_expr} = :m1 THEN amount ELSE 0 END), 0) AS amt_m1,
            COALESCE(SUM(CASE WHEN {month_expr} = :m2 THEN amount ELSE 0 END), 0) AS amt_m2
        FROM transactions
        WHERE user_id = :user_id AND type = 'expense' AND ({month_expr} = :m1 OR {month_expr} = :m2)
        GROUP BY category
        ORDER BY (COALESCE(SUM(CASE WHEN {month_expr} = :m2 THEN amount ELSE 0 END), 0) +
                  COALESCE(SUM(CASE WHEN {month_expr} = :m1 THEN amount ELSE 0 END), 0)) DESC
    """)
    mom_rows = db.session.execute(mom_query, {
        'user_id': user_id,
        'm1': compare_m1,
        'm2': compare_m2
    }).fetchall()

    mom_categories = []
    mom_m1_values = []
    mom_m2_values = []
    mom_diff_values = []

    for r in mom_rows:
        cat = r[0]
        m1_val = float(r[1])
        m2_val = float(r[2])
        diff = m2_val - m1_val
        mom_categories.append(cat)
        mom_m1_values.append(m1_val)
        mom_m2_values.append(m2_val)
        mom_diff_values.append(round(diff, 2))

    # =========================================================================
    # I. Chart 8: Day of Week & Transaction Metrics
    # =========================================================================
    dow_query = text(f"""
        SELECT
            {dow_expr} AS dow,
            COALESCE(SUM(amount), 0) AS total,
            COUNT(*) AS count
        FROM transactions
        WHERE {where_clause} AND type = 'expense'
        GROUP BY {dow_expr}
        ORDER BY dow ASC
    """)
    dow_rows = db.session.execute(dow_query, params).fetchall()

    dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    dow_totals = [0.0] * 7
    dow_counts = [0] * 7

    for r in dow_rows:
        d_idx = int(r[0]) - 1
        if 0 <= d_idx < 7:
            dow_totals[d_idx] = float(r[1])
            dow_counts[d_idx] = int(r[2])

    # Average transaction size by category
    avg_tx_query = text(f"""
        SELECT
            category,
            ROUND(CAST(AVG(amount) AS NUMERIC), 2) AS avg_amount,
            COUNT(*) AS count
        FROM transactions
        WHERE {where_clause} AND type = 'expense'
        GROUP BY category
        ORDER BY avg_amount DESC
        LIMIT 10
    """)
    avg_tx_rows = db.session.execute(avg_tx_query, params).fetchall()
    avg_tx_data = [{
        'category': r[0],
        'avg_amount': float(r[1]),
        'count': int(r[2])
    } for r in avg_tx_rows]

    # =========================================================================
    # J. Rule-Based Insights Generation (Server-Side, Zero External API)
    # =========================================================================
    insights = []

    # 1. Top Category Burn Insight
    if highest_spend_category != "None" and total_expense > 0:
        top_cat_share = round((highest_spend_amount / total_expense) * 100, 1)
        insights.append({
            'icon': 'bi-pie-chart-fill',
            'type': 'primary',
            'title': f'Dominant Expense: {highest_spend_category}',
            'text': f'{highest_spend_category} accounts for {top_cat_share}% of all expenses (₹{highest_spend_amount:,.2f}) in this period.'
        })

    # 2. Savings Rate & Health
    if total_income > 0:
        if savings_rate >= 30:
            health_badge = 'Excellent'
            badge_color = 'success'
        elif savings_rate >= 15:
            health_badge = 'Healthy'
            badge_color = 'info'
        elif savings_rate > 0:
            health_badge = 'Modest'
            badge_color = 'warning'
        else:
            health_badge = 'Negative (Deficit)'
            badge_color = 'danger'

        insights.append({
            'icon': 'bi-piggy-bank-fill',
            'type': badge_color,
            'title': f'Savings Rate: {savings_rate}% ({health_badge})',
            'text': f'You netted ₹{net_savings:,.2f} in savings from ₹{total_income:,.2f} total income.'
        })

    # 3. Best Savings Month
    if monthly_labels:
        best_month_idx = max(range(len(monthly_net)), key=lambda i: monthly_net[i])
        best_month_name = monthly_labels[best_month_idx]
        best_month_val = monthly_net[best_month_idx]
        if best_month_val > 0:
            insights.append({
                'icon': 'bi-trophy-fill',
                'type': 'success',
                'title': f'Strongest Month: {best_month_name}',
                'text': f'Your highest net savings occurred in {best_month_name} with +₹{best_month_val:,.2f} saved.'
            })

    # 4. Period Comparison
    if prev_change_expense_pct is not None:
        direction = 'decreased' if prev_change_expense_pct < 0 else 'increased'
        color = 'success' if prev_change_expense_pct < 0 else 'warning'
        insights.append({
            'icon': 'bi-graph-up-arrow' if prev_change_expense_pct > 0 else 'bi-graph-down-arrow',
            'type': color,
            'title': f'Spending Trend: {abs(prev_change_expense_pct)}% {direction}',
            'text': f'Expenses {direction} compared to the preceding period (from ₹{prev_total_expense:,.2f} to ₹{total_expense:,.2f}).'
        })

    # 5. Peak Spending Day of Week
    max_dow_val = max(dow_totals) if dow_totals else 0
    if max_dow_val > 0:
        peak_dow_idx = dow_totals.index(max_dow_val)
        peak_day_name = dow_names[peak_dow_idx]
        insights.append({
            'icon': 'bi-calendar-week',
            'type': 'secondary',
            'title': f'Peak Spending Day: {peak_day_name}',
            'text': f'{peak_day_name}s see your highest spend (₹{max_dow_val:,.2f} total across {dow_counts[peak_dow_idx]} transactions).'
        })

    # 6. Single Largest Transaction
    if top_expenses_list:
        largest = top_expenses_list[0]
        insights.append({
            'icon': 'bi-receipt',
            'type': 'dark',
            'title': f'Largest Ticket: ₹{largest["amount"]:,.2f}',
            'text': f'Single largest expense was {largest["category"]} on {largest["date"]}.'
        })

    return {
        'summary': {
            'total_income': total_income,
            'total_expense': total_expense,
            'net_savings': net_savings,
            'savings_rate': savings_rate,
            'avg_monthly_spend': avg_monthly_spend,
            'highest_spend_category': highest_spend_category,
            'highest_spend_amount': highest_spend_amount,
            'prev_change_expense_pct': prev_change_expense_pct,
            'prev_change_income_pct': prev_change_income_pct,
            'total_tx_count': total_tx_count,
            'active_months': active_months
        },
        'monthly': {
            'labels': monthly_labels,
            'income': monthly_income,
            'expenses': monthly_expenses,
            'net_savings': monthly_net
        },
        'by_category': {
            'labels': category_labels,
            'amounts': category_amounts,
            'percentages': category_percentages,
            'counts': category_counts
        },
        'category_monthly': {
            'months': all_months,
            'categories': all_categories,
            'stacked_datasets': stacked_datasets,
            'heatmap': heatmap_matrix,
            'heatmap_max': heatmap_max
        },
        'category_trends': {
            'months': all_months,
            'datasets': trends_datasets
        },
        'cumulative': {
            'labels': cumulative_labels,
            'values': cumulative_values
        },
        'top_expenses': top_expenses_list,
        'mom_comparison': {
            'm1': compare_m1,
            'm2': compare_m2,
            'all_months': all_months,
            'categories': mom_categories,
            'm1_values': mom_m1_values,
            'm2_values': mom_m2_values,
            'diff_values': mom_diff_values
        },
        'day_of_week': {
            'days': dow_names,
            'totals': dow_totals,
            'counts': dow_counts
        },
        'avg_transaction': avg_tx_data,
        'insights': insights
    }
