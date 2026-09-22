import os
import re


def check_hello_exists():
    return os.path.exists('hello.py')


class Bank:
    def __init__(self, name):
        self.name = name
        self.accounts = {}


class Account:
    def __init__(self, account_id, owner):
        self.account_id = account_id
        self.owner = owner
        self.balance = 0.0

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError("Deposit amount must be positive")
        self.balance += amount

    def withdraw(self, amount):
        if amount <= 0:
            raise ValueError("Withdrawal amount must be positive")
        if amount > self.balance:
            raise ValueError("Insufficient funds")
        self.balance -= amount


def h():
    print('hello')

def is_palindrome(s):
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', s).lower()
    return cleaned == cleaned[::-1]

def main():
    bank = Bank("Test Bank")
    account = Account(1, "Alice")
    
    # Test deposit
    account.deposit(100)
    assert account.balance == 100.0
    
    # Test withdrawal
    account.withdraw(40)
    assert account.balance == 60.0
    
    print("All tests passed.")


if __name__ == "__main__":
    main()
    main()



