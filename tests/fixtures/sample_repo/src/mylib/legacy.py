"""Legacy statistics helpers."""


def mean(values: list[float]) -> float:
    """Compute the arithmetic mean.

    Parameters
    ----------
    values : list of float
        Numbers to average.

    Returns
    -------
    float
        The mean of ``values``.

    Raises
    ------
    ZeroDivisionError
        If ``values`` is empty.
    """
    return sum(values) / len(values)
