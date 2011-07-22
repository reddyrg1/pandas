import numpy as np

from cStringIO import StringIO

from pandas.core.frame import DataFrame
from pandas.core.series import Series
from pandas.core.panel import WidePanel
import pandas._tseries as _tseries

def groupby(obj, grouper, **kwds):
    """
    Intercepts creation and dispatches to the appropriate class based
    on type.
    """
    if isinstance(obj, Series):
        klass = SeriesGroupBy
    elif isinstance(obj, DataFrame):
        klass = DataFrameGroupBy
    else: # pragma: no cover
        raise TypeError('invalid type: %s' % type(obj))

    return klass(obj, grouper, **kwds)

class Grouping(object):

    def __init__(self, labels, grouper):
        self.labels = labels
        self.grouper = grouper

        # eager beaver
        self.indices = _tseries.groupby_indices(labels, grouper)

    _groups = None
    @property
    def groups(self):
        if self._groups is None:
            self._groups = _tseries.groupby(self.labels, self.grouper)
        return self._groups

class GroupBy(object):
    """
    Class for grouping and aggregating relational data.

    Supported classes: Series, DataFrame
    """
    def __init__(self, obj, grouper, axis=0):
        self.obj = obj
        if hasattr(grouper, 'get'):
            grouper = grouper.get
        self.grouper = grouper

        self.axis = axis
        self._group_axis = np.asarray(obj._get_axis(axis))
        self._group_axis_name = obj._get_axis_name(axis)
        self.groupings = [Grouping(self._group_axis, grouper)]
        self.primary = self.groupings[0]

    def getGroup(self, name):
        inds = self.primary.indices[name]
        group_labels = self._group_axis.take(inds)
        return self.obj.reindex(**{self._group_axis_name : group_labels})

    def __iter__(self):
        """
        Groupby iterator

        Returns
        -------
        Generator yielding sequence of (groupName, subsetted object)
        for each group
        """
        groups = self.primary.indices.keys()
        try:
            groupNames = sorted(groups)
        except Exception: # pragma: no cover
            pass

        for name in groups:
            yield name, self[name]

    def __getitem__(self, key):
        return self.getGroup(self.primary.indices[key])

    def aggregate(self, func):
        raise NotImplementedError

    def agg(self, func):
        return self.aggregate(func)

    def _aggregate_generic(self, agger, axis=0):
        result = {}
        for name  in self.primary.indices.iteritems():
            data = self.getGroup(name)
            try:
                result[name] = agger(data)
            except Exception:
                result[name] = data.apply(agger, axis=axis)

        return result

    def transform(self, func):
        raise NotImplementedError

    def mean(self):
        """
        Compute mean of groups, excluding missing values
        """
        return self.aggregate(np.mean)

    def sum(self):
        """
        Compute sum of values, excluding missing values
        """
        # TODO: make NaN-friendly
        return self.aggregate(np.sum)

def multi_groupby(obj, op, *columns):
    cur = columns[0]
    grouped = obj.groupby(cur)
    if len(columns) == 1:
        return grouped.aggregate(op)
    else:
        result = {}
        for key, value in grouped:
            result[key] = multi_groupby(value, op, columns[1:])
    return result

class SeriesGroupBy(GroupBy):

    def aggregate(self, applyfunc):
        """
        See doc for DataFrame.groupby, group series using mapper (dict or key
        function, apply given function to group, return result as series).

        Main difference here is that applyfunc must return a value, so that the
        result is a sensible series.

        Parameters
        ----------
        mapper : function
            Called on each element of the Series index to determine the groups
        applyfunc : function
            Function to use to aggregate each group

        Returns
        -------
        Series or DataFrame
        """
        if hasattr(applyfunc,'__iter__'):
            retVal = self._aggregate_multiple(applyfunc)
        else:
            try:
                result = self._aggregate_simple(applyfunc)
            except Exception:
                result = self._aggregate_named(applyfunc)

            retVal = Series(result)

        return retVal

    def _aggregate_multiple(self, applyfunc):
        if not isinstance(applyfunc, dict):
            applyfunc = dict((func.__name__, func) for func in applyfunc)

        results = {}

        for name, func in applyfunc.iteritems():
            result = self.aggregate(func)
            results[name] = result

        return DataFrame(results)

    def _aggregate_simple(self, applyfunc):
        values = self.obj.values
        result = {}
        for k, v in self.primary.indices.iteritems():
            result[k] = applyfunc(values.take(v))

        return result

    def _aggregate_named(self, applyfunc):
        result = {}
        for k, v in self.primary.indices.iteritems():
            grp = self[k]
            grp.groupName = k
            output = applyfunc(grp)

            if isinstance(output, Series):
                raise Exception('Given applyfunc did not return a '
                                'value from the subseries as expected!')

            result[k] = output

        return result

    def transform(self, applyfunc):
        """
        For given Series, group index by given mapper function or dict, take
        the sub-Series (reindex) for this group and call apply(applyfunc)
        on this sub-Series. Return a Series of the results for each
        key.

        Parameters
        ----------
        mapper : function
            on being called on each element of the Series
            index, determines the groups.

        applyfunc : function to apply to each group

        Note
        ----
        This function does not aggregate like groupby/tgroupby,
        the results of the given function on the subSeries should be another
        Series.

        Example
        -------
        series.fgroupby(lambda x: mapping[x],
                        lambda x: (x - mean(x)) / std(x))

        Returns
        -------
        Series standardized by each unique value of mapping
        """
        result = self.obj.copy()

        for name, group in self:
            # XXX
            group.groupName = name
            res = applyfunc(group)

            indexer, _ = self.obj.index.get_indexer(group.index)
            np.put(result, indexer, res)

        return result

class FrameMetaGroupBy(object):

    def __init__(self, grouped, context):
        pass

class DataFrameGroupBy(GroupBy):

    def __init__(self, obj, grouper=None, column=None, groupings=None, axis=0):
        self.obj = obj

        if isinstance(grouper, basestring):
            grouper = obj[grouper].get
        if hasattr(grouper, 'get'):
            grouper = grouper.get

        self.grouper = grouper
        self.axis = axis
        self._group_axis = np.asarray(obj._get_axis(axis))
        self._group_axis_name = obj._get_axis_name(axis)

        if groupings is not None:
            self.groupings = groupings
        elif isinstance(grouper, (tuple, list)):
            if axis != 0:
                raise NotImplementedError('multi-grouping only done for '
                                          'valid with axis=0')
            if len(grouper) > 2:
                raise NotImplementedError('Only group with <= 2 columns '
                                          'for now...')
            mappers = [obj[s] for s in grouper]
            self.groupings = [Grouping(obj.index, mapper) for mapper in mappers]
        else:
            self.groupings = [Grouping(self._group_axis, grouper)]

        self.primary = self.groupings[0]

    def __getitem__(self, key):
        if key not in self.obj:
            raise KeyError('column %s not found' % key)
        return DataFrameGroupBy(self.obj, groupings=self.groupings,
                                column=key)

    def aggregate(self, applyfunc):
        """
        For given DataFrame, group index by given mapper function or dict, take
        the sub-DataFrame (reindex) for this group and call apply(applyfunc)
        on this sub-DataFrame. Return a DataFrame of the results for each
        key.

        Parameters
        ----------
        mapper : function, dict-like, or string
            Mapping or mapping function. If string given, must be a column
            name in the framep
        applyfunc : function
            Function to use for aggregating groups

        N.B.: applyfunc must produce one value from a Series, otherwise
        an error will occur.

        Optional: provide set mapping as dictionary
        """
        result_d = self._aggregate_generic(applyfunc, axis=self.axis)
        result = DataFrame(result_d)
        if self.axis == 0:
            result = result.T
        return result

    def transform(self, func):
        """
        For given DataFrame, group index by given mapper function or dict, take
        the sub-DataFrame (reindex) for this group and call apply(func)
        on this sub-DataFrame. Return a DataFrame of the results for each
        key.

        Note: this function does not aggregate like groupby/tgroupby,
        the results of the given function on the subDataFrame should be another
        DataFrame.

        Parameters
        ----------
        mapper : function, dict-like, or string
            Mapping or mapping function. If string given, must be a column
            name in the frame
        func : function
            Function to apply to each subframe

        Note
        ----
        Each subframe is endowed the attribute 'groupName' in case
        you need to know which group you are working on.

        Example
        --------
        >>> grouped = df.groupby(lambda x: mapping[x])
        >>> grouped.transform(lambda x: (x - x.mean()) / x.std())
        """
        # DataFrame objects?
        result_values = np.empty_like(self.obj.values)

        if self.axis == 0:
            trans = lambda x: x
        elif self.axis == 1:
            trans = lambda x: x.T

        result_values = trans(result_values)

        for val, group in self.primary.groups.iteritems():
            if not isinstance(group, list): # pragma: no cover
                group = list(group)

            if self.axis == 0:
                subframe = self.obj.reindex(group)
                indexer, _ = self.obj.index.get_indexer(subframe.index)
            else:
                subframe = self.obj.reindex(columns=group)
                indexer, _ = self.obj.columns.get_indexer(subframe.columns)
            subframe.groupName = val

            try:
                res = subframe.apply(func, axis=self.axis)
            except Exception: # pragma: no cover
                res = func(subframe)

            result_values[indexer] = trans(res.values)

        result_values = trans(result_values)

        return DataFrame(result_values, index=self.obj.index,
                         columns=self.obj.columns)


class WidePanelGroupBy(GroupBy):

    def __init__(self, obj, grouper, axis=0):
        GroupBy.__init__(self, obj, grouper, axis=axis)

    def aggregate(self, func):
        """
        For given DataFrame, group index by given mapper function or dict, take
        the sub-DataFrame (reindex) for this group and call apply(func)
        on this sub-DataFrame. Return a DataFrame of the results for each
        key.

        Parameters
        ----------
        mapper : function, dict-like, or string
            Mapping or mapping function. If string given, must be a column
            name in the frame
        func : function
            Function to use for aggregating groups

        N.B.: func must produce one value from a Series, otherwise
        an error will occur.

        Optional: provide set mapping as dictionary
        """
        axis_name = self.obj._get_axis_name(self.axis)
        result_d = self._aggregate_generic(func, axis=self.axis)
        result = WidePanel.fromDict(result_d, intersect=False)

        if self.axis > 0:
            result = result.swapaxes(0, self.axis)

        return result

class LongPanelGroupBy(GroupBy):
    pass

