import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 2, 100)
y = np.log(x) + 10*x

plt.plot(x, y)
plt.show()

