import numpy as np
import matplotlib.pyplot as plt
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier
from sklearn.metrics import confusion_matrix


def print_hello_world():
    """Prints exact string 'Hello World' to standard output."""
    print('Hello World')


def load_data():
    """Loads the Iris dataset and returns features, target, and target names."""
    iris = load_iris()
    X = iris.data  # shape (150, 4)
    y = iris.target
    target_names = iris.target_names
    feature_names = iris.feature_names
    return X, y, target_names, feature_names


def build_classifier(X, y):
    """Splits the data and trains a KNN classifier."""
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, stratify=y, random_state=42
    )

    knn = KNeighborsClassifier(n_neighbors=5)
    knn.fit(X_train, y_train)
    y_pred = knn.predict(X_test)

    return y_test, y_pred, knn, X_train, y_test


def print_confusion_matrix(y_true, y_pred, target_names):
    """Prints a formatted confusion matrix to standard output."""
    cm = confusion_matrix(y_true, y_pred)

    width = max(len(str(name)) for name in target_names) + 2

    print("\nConfusion Matrix")
    print((width + 1) * "---------")
    header = " " * (width - 1) + " " * 10
    print(" " * 4, " " * 4, " " + 8 * " ".ljust(0))
    # Simple generic formatting
    max_label_width = max(len(str(target_names[i])) for i in range(len(target_names)))
    # Build header
    top_sep = " " * (max_label_width + 2) + " |" + " ".join(
        (str(num).rjust(max_label_width)) for num in range(len(target_names))
    ) + " |"
    label_row = " " * (max_label_width) + "  |" + " ".join(
        (str(name)[:max_label_width].rjust(max_label_width)) for name in target_names
    ) + " |"

    # Build rows
    print(" " * 2 + " " * 4 + " " * 6)
    print(" " * 4, top_sep)
    print(" " * 4, label_row)

    for i, row in enumerate(cm):
        right = []
        for j in range(len(target_names)):
            if j == 0:
                actual_name = str(target_names[i]).rjust(max_label_width)
                right.append(f"{actual_name}: {row[j]}")
            else:
                right.append(f"{row[j]:{max_label_width}}")
        print(" " * 4, " | ".join(right).ljust(max_label_width * 3 + max_label_width))


def plot_plots(X, y, feature_names):
    """Generates and shows matplotlib plots for EDA of the dataset."""

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # Scatter plot - first two features
    ax = axes[0, 0]
    for i in range(3):
        mask = y == i

