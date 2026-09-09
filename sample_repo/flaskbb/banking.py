class BankingAccount:
    """A banking account that manages deposits, withdrawals, and monthly interest."""

    def __init__(self, initial_balance=0.0):
        if initial_balance < 0:
            raise ValueError("Initial balance cannot be negative.")
        self.balance = float(initial_balance)

    def deposit(self, amount):
        """Deposit funds into the account.

        Args:
            amount (float): The amount to deposit.

        Returns:
            float: The new balance after deposit.

        Raises:
            ValueError: If the deposit amount is negative.
        """
        if amount < 0:
            raise ValueError("Deposit amount cannot be negative.")
        self.balance += float(amount)
        return self.balance

    def withdraw(self, amount):
        """Withdraw funds from the account if sufficient balance exists.

        Args:
            amount (float): The amount to withdraw.

        Returns:
            float: The new balance after withdrawal.

        Raises:
            ValueError: If the withdrawal amount is negative or exceeds the balance.
        """
        if amount < 0:
            raise ValueError("Withdrawal amount cannot be negative.")
        if amount > self.balance:
            raise ValueError("Insufficient funds for withdrawal.")
        self.balance -= float(amount)
        return self.balance

    def apply_monthly_interest(self, rate):
        """Apply monthly interest to the account balance.

        Args:
            rate (float): The monthly interest rate (e.g., 0.01 for 1%).

        Returns:
            float: The new balance after interest is applied.
        """
        if rate < 0:
            raise ValueError("Interest rate cannot be negative.")
        interest = self.balance * float(rate)
        self.balance += interest
        return self.balance
