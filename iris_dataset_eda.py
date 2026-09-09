import pandas as pd
from sklearn.datasets import load_iris

def iris_eda():
    """
    Performs exploratory data analysis (EDA) on the Iris dataset.
    """
    iris = load_iris()
    df = pd.DataFrame(iris.data, columns=iris.feature_names)
    df['species'] = iris.target

    print("Number of observations:", df.shape[0])
    print("Number of features:", df.shape[1])
    print("Maximum values for each feature:")
    print(df.max())
    print("Minimum values for each feature:")
    print(df.min())

    df['species'].value_counts().plot(kind='bar')
