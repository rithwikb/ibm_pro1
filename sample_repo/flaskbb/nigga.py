import matplotlib.pyplot as plt
import numpy as np

def main():
    print("Hello Srikar!")
    has_girlfriend = input("Do you have a girlfriend? (yes/no): ").strip().lower()
    
    if has_girlfriend == 'yes':
        x = np.linspace(-10, 10, 1000)
        y = x * np.sin(x) + x * np.cos(x)
        plt.plot(x, y)
        plt.title('xsinx + xcosx')
        plt.xlabel('x')
        plt.ylabel('y')
        plt.grid(True)
        plt.show()
    else:
        from sklearn.datasets import load_iris
        iris = load_iris()
        print(f"Number of datasets: {len(iris.data)}")

if __name__ == "__main__":
    main()
