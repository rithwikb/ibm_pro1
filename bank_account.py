class BankAccount:
    def __init__(self, holder, account_number, balance):
        self.holder = holder
        self.account_number = account_number
        self.balance = balance

    def deposit(self, amount):
        if amount <= 0:
            print(f"Error: Deposit amount must be positive. Attempted to deposit {amount} into {self.holder}'s account.")
            return
        self.balance += amount
        print(f"{self.holder} deposited ${amount}. New balance: ${self.balance}.")

    def withdraw(self, amount):
        if amount <= 0:
            print(f"Error: Withdrawal amount must be positive. Attempted to withdraw {amount} from {self.holder}'s account.")
            return
        if amount > self.balance:
            print(f"Error: Insufficient funds for withdrawal. {self.holder} has ${self.balance}, attempted to withdraw ${amount}.")
            return
        self.balance -= amount
        print(f"{self.holder} withdrew ${amount}. New balance: ${self.balance}.")

    def transfer(self, other_account, amount):
        if amount <= 0:
            print(f"Error: Transfer amount must be positive. Attempted to transfer {amount} from {self.holder} to {other_account.holder}.")
            return
        if amount > self.balance:
            print(f"Error: Insufficient funds for transfer. {self.holder} has ${self.balance}, attempted to transfer ${amount}.")
            return
        self.balance -= amount
        other_account.balance += amount
        print(f"{self.holder} transferred ${amount} to {other_account.holder}. New balance: ${self.balance}.")
        print(f"{other_account.holder} received ${amount} from {self.holder}. New balance: ${other_account.balance}.")


