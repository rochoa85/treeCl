#!/usr/bin/env python

import numpy as np
from distance_matrix import DistanceMatrix
from scipy.cluster.hierarchy import linkage, fcluster, dendrogram
try:
    from Bio.Cluster import kmedoids
    Biopython_Unavailable = False
except ImportError:
    print "Biopython unavailable - kmedoids clustering disabled"
    Biopython_Unavailable = True
import matplotlib.pyplot as plt
try:
    from sklearn.cluster import KMeans
except ImportError:
    print "sklearn unavailable: KMeans disabled"
from collections import defaultdict
try:
    import evrot  # evrot not currently in use
except ImportError:
    # print 'evrot is not currently in use'
    pass
from copy import deepcopy
from lib.remote.utils import fileIO
import uuid
import os


class Clustering(object):

    """ Apply clustering methods to distance matrix

    = Hierarchical clustering - single-linkage - complete-linkage - average-
    linkage (UPGMA) - Ward's method

    = k-medoids

    = Multidimensional Scaling (Principal Coordinate Analysis) + k-means

    = Spectral Clustering + k-means - NJW method - Shi-Malik method - Zelnik-
    Manor and Perona Local Scaling - Local Scaling with eigenvector rotation as
    stopping criterion

    """

    def __init__(self, distance_matrix):

        self.distance_matrix = distance_matrix

    def __str__(self):
        return str(self.distance_matrix)

    def kmedoids(self, nclusters, noise=False):

        if Biopython_Unavailable:
            print 'kmedoids not available without Biopython'
            return

        if noise:
            matrix = self.distance_matrix.add_noise()
        else:
            matrix = self.distance_matrix

        p = [kmedoids(matrix, nclusters=nclusters, npass=100) for _ in
             range(100)]
        p.sort(key=lambda x: x[1])
        return Partition(p[0][0])

    def hierarchical(
        self,
        nclusters,
        linkage_method,
        noise=False,
        ):

        if noise:
            matrix = self.distance_matrix.add_noise()
        else:
            matrix = self.distance_matrix

        linkmat = linkage(matrix, linkage_method)
        linkmat_size = len(linkmat)
        if nclusters <= 1:
            br_top = linkmat[linkmat_size - nclusters][2]
        else:
            br_top = linkmat[linkmat_size - nclusters + 1][2]
        if nclusters >= len(linkmat):
            br_bottom = 0
        else:
            br_bottom = linkmat[linkmat_size - nclusters][2]
        threshold = 0.5 * (br_top + br_bottom)
        T = fcluster(linkmat, threshold, criterion='distance')
        return Partition(T)

    def spectral_decomp(
        self,
        prune='estimate',
        local_scale=None,
        noise=False,
        verbosity=0,
        logic='or',
        **kwargs):
        """ Use prune to remove links between distant points:

        prune='estimate' searches for the smallest value that retains a fully
        connected graph

        """

        if noise:
            matrix = self.distance_matrix.add_noise()
        else:
            matrix = self.distance_matrix

        kp, mask, est_scale = matrix.binsearch_mask(logic=logic) # prune anyway,
                                                    # get local scale estimate
        
        ks = kp # ks and kp log the scaling and pruning parameters
        est_ks = ks

        # ADJUST MASK
        if prune == -1: # change mask to all
            kp   = len(matrix) - 1 
            mask = np.ones(matrix.shape, dtype=bool)
        elif isinstance(prune, int) and prune > 0:
            kp   = prune
            mask = matrix.kmask(prune, logic=logic)

        # ADJUST SCALE
        if local_scale == 'estimate': # deprecated option
            local_scale = None
        if local_scale is not None:
            if local_scale == 'median':
                ks    = 'median'
                dist  = np.median(matrix, axis=1)
                scale = np.outer(dist, dist)
            elif isinstance(local_scale, int):
                ks    = local_scale
                scale = matrix.kscale(local_scale)
        else: 
            scale = est_scale

        # ZeroDivisionError safety check
        if not (scale > 1e-5).all():
            if verbosity > 0:
                print 'Rescaling to avoid zero-div error'
            scale = est_scale
            ks = est_ks
            assert (scale > 1e-5).all()

        if verbosity > 0:
            print 'Pruning parameter: {0}'.format(kp)
            print 'Scaling parameter: {0}'.format(ks)

        aff = matrix.affinity(mask, scale)
        
        # ZeroDivisionError triggers pickle dump
        try: 
            laplace = matrix.laplace(aff, **kwargs)
        except ZeroDivisionError:
            dump = str(uuid.uuid4()).split('-')[0]
            home = os.getenv('HOME')
            if home:
                dumpfile = '{0}/dm_{1}.pkl.gz'.format(home, dump)
                fileIO.gpickle(self.distance_matrix, dumpfile)
                print 'ZeroDivisionError detected on constructing laplacian.'
                print 'Distance matrix dumped to {0}'.format(dumpfile)
                print 'prune and local scale arguments: {0}, {1}'.format(
                    prune, local_scale)
            raise

        return laplace.eigen()  # vectors are in columns

    def spectral_cluster(self, nclusters, decomp, verbosity=0):

        if nclusters == 1:
            return Partition([1]*len(self.distance_matrix))
            
        (coords, cve) = decomp.coords_by_dimension(nclusters)
        if verbosity > 0:
            print '{0} dimensions explain {1:.2f}% of the variance'.format(nclusters,
                cve * 100)
        coords = coords.normalise_rows()  # scale all rows to unit length
        P = self.kmeans(nclusters, coords)
        return P

    def MDS_decomp(self, noise=False):

        if noise:
            matrix = self.distance_matrix.add_noise()
        else:
            matrix = self.distance_matrix

        dbc = matrix.double_centre()
        return dbc.eigen()

    def MDS_cluster(
        self,
        nclusters,
        decomp,
        cutoff=None,
        verbosity=0,
        ):

        (coords, cve) = \
            (decomp.coords_by_cutoff(cutoff) if cutoff else decomp.coords_by_dimension(nclusters))
        if verbosity > 0:
            print '{0} dimensions explain {1:.2f}% of the variance'.format(
                coords.shape[1], cve * 100)
        P = self.kmeans(nclusters, coords)
        return P

    def kmeans(self, nclusters, coords=None, noise=False):
        coords = coords if coords is not None else self.distance_matrix
        if noise:
            coords.add_noise()
        est = KMeans(n_clusters=nclusters, n_init=50, max_iter=500)
        est.fit(coords)
        return Partition(est.labels_)

    def plot_dendrogram(self, compound_key):
        """ Extracts data from clustering to plot dendrogram """

        partition = self.partitions[compound_key]
        (linkmat, names, threshold) = self.plotting_info[compound_key]
        fig = plt.figure(figsize=(11.7, 8.3))
        dendrogram(
            linkmat,
            color_threshold=threshold,
            leaf_font_size=8,
            leaf_rotation=90,
            leaf_label_func=lambda leaf: names[leaf] + '_' \
                + str(partition[leaf]),
            count_sort=True,
            )
        plt.suptitle('Dendrogram', fontsize=16)
        plt.title('Distance metric: {0}    Linkage method: {1}    Number of classes: {2}'.format(compound_key[0],
                  compound_key[1], compound_key[2]), fontsize=12)
        plt.axhline(threshold, color='grey', ls='dashed')
        plt.xlabel('Gene')
        plt.ylabel('Distance')
        return fig

    def translate_evrot_clustering(self, clustering):
        translation = [None] * len(self.distance_matrix)
        no_of_empty_clusters = 0
        for (group_number, group_membership) in enumerate(clustering):
            if len(group_membership) == 0:
                no_of_empty_clusters += 1
            for index in group_membership:
                translation[index - 1] = group_number
        if no_of_empty_clusters > 0:
            print 'Found {0} empty clusters'.format(
                no_of_empty_clusters)   
        p = Partition(tuple(translation))
        return p

    def spectral_rotate(
        self,
        decomp=None,
        KMeans=True,
        max_groups=None,
        min_groups=2,
        verbose=True,
        tolerance=0.0025,
        **kwargs
        ):

        if decomp is None:
            decomp = self.spectral_decomp(**kwargs)

        # ######################
        # CLUSTER_ROTATE STUFF HERE

        M = decomp.matrix
        if not max_groups:
            max_groups = max(int(np.sqrt(M.shape[0]) + np.power(M.shape[0], 1.0
                             / 3)), min_groups)
        (groups, clusters, quality_scores, rotated_vectors) = \
            self.cluster_rotate(decomp.vecs, max_groups=max_groups,
                                min_groups=min_groups, tolerance=tolerance)

        corrected_groups = [None] * len(groups)
        for i, cluster in enumerate(clusters):
            p = self.translate_evrot_clustering(cluster)
            clusters[i] = p
            corrected_groups[i] = max(p.partition_vector)
         
        # ######################

        index = self.best_evrot_clustering(quality_scores)
        optimum_nclusters = corrected_groups[index]

        if verbose:
            print 'Discovered {0} clusters'.format(optimum_nclusters)
            print 'Quality scores: {0}'.format(quality_scores)
            
        if KMeans:
            KMeans_clusters = [self.kmeans(groups[i], rotated_vectors[i])
                                for i in range(len(groups))]
            return groups, KMeans_clusters, quality_scores, index

        return groups, clusters, quality_scores, index

    def cluster_rotate(
        self,
        eigenvectors,
        max_groups,
        min_groups=2,
        tolerance=0.0025,
        ):

        groups = range(min_groups, max_groups + 1)
        vector_length = eigenvectors.shape[0]
        current_vector = eigenvectors[:, :groups[0]]
        n = max_groups - min_groups + 1

        quality_scores = [None] * n
        clusters = [None] * n
        rotated_vectors = [None] * n

        for g in range(n):
            if g > 0:
                current_vector = np.concatenate((rotated_vectors[g - 1],
                        eigenvectors[:, groups[g] - 1:groups[g]]), axis=1)

            (clusters[g], quality_scores[g], rotated_vectors[g]) = \
                evrot.main(current_vector)

        return (groups, clusters, quality_scores, rotated_vectors)

    def best_evrot_clustering(self, quality_scores, tolerance=0.0025):

        # Find the highest index of quality scores where the
        # score is within 0.0025 of the maximum:
        # this is our chosen number of groups

        max_score = max(quality_scores)
        index = quality_scores.index(max_score)
        start = index + 1
        for (i, score) in enumerate(quality_scores[index + 1:], start=start):
            if abs(score - max_score) < tolerance:
                index = i
        return index

        


class Partition(object):

    """ Class to store clustering information """

    # score = 0
    # concats = []
    # partition_vector = None

    def __init__(self, partition_vector):
        self.partition_vector = partition_vector

    def __str__(self):
        return str(self.partition_vector)

    def __repr__(self):
        return self.__class__.__name__ + '({0})'.format(str(self))

    def __len__(self):
        """
        This gives the number of groups in the partition, rather than
        the number of elements in the data
        """
        return max(self.partition_vector)

    @property
    def partition_vector(self):
        return self._partition_vector

    @partition_vector.setter
    def partition_vector(self, vec):
        self._partition_vector = self.order(vec)

    @classmethod
    def order(self, l):
        """ The clustering returned by the hcluster module gives group
        membership without regard for numerical order This function preserves
        the group membership, but sorts the labelling into numerical order """

        list_length = len(l)

        d = defaultdict(list)
        for (i, element) in enumerate(l):
            d[element].append(i)

        l2 = [None] * list_length

        for (name, index_list) in enumerate(sorted(d.values(), key=min),
                start=1):
            for index in index_list:
                l2[index] = name

        return tuple(l2)

    def entropies(self, partition_1, partition_2):
        """ parameters: partition_1 (list / array) - a partitioning of a dataset
        according to some clustering method. Cluster labels are arbitrary.
        partition_2 (list / array) - another partitioning of the same dataset.
        Labels don't need to match, nor do the number of clusters.

        subfunctions: get_membership( parameter partition (list / array) )
        returns a list of length equal to the number of clusters found in the
        partition. Each element is the set of members of the cluster. Ordering
        is arbitrary.

        variables used: t = total number of points in the dataset m1 = cluster
        memberships from partition_1 m2 = cluster memberships from partition_2
        l1 = length (i.e. number of clusters) of m1 l2 = length of m2 entropy_1
        = Shannon entropy of partition_1 entropy_2 = Shannon entropy of
        partition_2 mut_inf = mutual information of partitions prob1 =
        probability distribution of partition 1 - i.e. the probability that a
        randomly chosen datapoint belongs to each cluster (size of cluster /
        size of dataset) prob2 = as above, for partition 2 intersect = number of
        common elements in partition 1 [i] and partition 2 [j] """

        if len(partition_1) != len(partition_2):
            print 'Partition lists are not the same length'
            return 0
        else:
            total = float(len(partition_1))  # Ensure float division later

        m1 = self.get_membership(partition_1)
        m2 = self.get_membership(partition_2)
        l1 = len(m1)
        l2 = len(m2)
        entropy_1 = 0
        entropy_2 = 0
        mut_inf = 0
        for i in range(l1):
            prob1 = len(m1[i]) / total
            entropy_1 -= prob1 * np.log2(prob1)
            for j in range(l2):
                if i == 0:  # only calculate these once
                    prob2 = len(m2[j]) / total
                    entropy_2 -= prob2 * np.log2(prob2)
                intersect = len(set(m1[i]) & set(m2[j]))
                if intersect == 0:
                    continue  # because 0 * log(0) = 0 (lim x->0: xlog(x)->0)
                else:
                    mut_inf += intersect / total * np.log2(total * intersect
                            / (len(m1[i]) * len(m2[j])))

        return (entropy_1, entropy_2, mut_inf)

    def flatten(self, list_of_lists):
        """ This is faster than the one-liner version:-

        def(flatten): return list(itertools.chain(*list_of_lists))

        """

        res = []
        x = res.extend
        for sublist in list_of_lists:
            x(sublist)
        return res

    def get_membership(self, partition_vector=None, flatten=False):

        pvec = partition_vector or self.partition_vector
        result = defaultdict(list)
        for (position, value) in enumerate(pvec):
            result[value].append(position)
        result = [tuple(x) for x in sorted(result.values(), key=len,
                  reverse=True)]
        return (self.flatten(result) if flatten else result)

    def normalised_mutual_information(self, other):
        partition_1 = self.partition_vector
        partition_2 = other.partition_vector

        (entropy_1, entropy_2, mut_inf) = self.entropies(partition_1,
                partition_2)

        return 2 * mut_inf / (entropy_1 + entropy_2)

    def variation_of_information(self, other):
        """ calculates Variation of Information Metric between two clusterings
        of the same data - SEE Meila, M. (2007). Comparing clusterings: an
        information based distance. Journal of Multivariate Analysis, 98(5),
        873-895. doi:10.1016/j.jmva.2006.11.013"""
        partition_1 = self.partition_vector
        partition_2 = other.partition_vector

        (entropy_1, entropy_2, mut_inf) = self.entropies(partition_1,
                partition_2)

        return entropy_1 + entropy_2 - 2 * mut_inf


def get_partition(clusters):
    global seq
    seq = clusters if isinstance(clusters, dict) else range(len(clusters))
    length = sum((len(clusters[i]) for i in seq))
    global pvec
    pvec = [0] * length
    print 'seq: {0}'.format(seq)
    print clusters
    for k in seq:
        for i in clusters[k]:
            print k, i
            pvec[i] = k
    print pvec
    return(Partition(tuple(pvec)))
