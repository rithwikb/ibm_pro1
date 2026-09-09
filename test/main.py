from bank_account import BankAccount

def main():
    # Create four bank accounts with initial balances
    alice = BankAccount('Alice', '1001', 500)
    bob = BankAccount('Bob', '1002', 300)
    charlie = BankAccount('Charlie', '1003', 800)
    dana = BankAccount('Dana', '1004', 200)

    # Perform five deposits
    alice.deposit(200)
    bob.deposit(150)
    charlie.deposit(100)
    dana.deposit(250)
    alice.deposit(50)

    # Perform four withdrawals
    charlie.withdraw(100)
    bob.withdraw(50)
    dana.withdraw(100)
    alice.withdraw(30)

    # Perform three transfers
    alice.transfer(bob, 150)
    charlie.transfer(dana, 200)
    bob.transfer(charlie, 50)

    # Print final balances
    print("\nFinal account balances:")
    print(f"Alice: ${alice.balance}")
    print(f"Bob: ${bob.balance}")
    print(f"Charlie: ${charlie.balance}")
    print(f"Dana: ${dana.balance}")

if __name__ == "__main__":
    main()
