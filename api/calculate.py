def calc(curr_bal, days_gone, expenses, days_left, save):
    """
    Calculate the per day allowance and projected expenses.
    
    Parameters:
    curr_bal (int): Current balance.
    days_gone (int): Number of effective days gone in the month.
    expenses (int): Total expenses so far.
    days_left (int): Number of effective days left in the month.
    save (int): Amount to save.

    Returns:
    tuple: Per day allowance and projected expenses.
    """
    per_day_allowance = (curr_bal - save) / days_left
    projected_expenses = (expenses / days_gone) * days_left
    return per_day_allowance, projected_expenses