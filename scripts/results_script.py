#!/usr/bin/env python

import analysis
import matplotlib.pyplot as plt
from numpy import mean
import sys

try:
    folder = sys.argv[1]
except:
    folder = './'

results = analysis.foldersearch(folder)

plt.hold(False)

for r in results:
    plotname = 'plot_' + str(r.id)
    r.plot(plotname, 'png')

lls = [r.likelihood for r in results]
times = [r.cputime for r in results]

print 'Mean Likelihood:' + str(mean(lls))
print 'Mean time:' + str(mean(times))

plt.scatter(times, lls)
plt.savefig('scatterplot')