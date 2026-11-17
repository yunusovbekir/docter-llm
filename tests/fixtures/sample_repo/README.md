# mylib

A tiny client library. It is test data for Docter: it mixes documented, undocumented, and
drifted symbols, and `src/mylib/broken.py` deliberately does not parse.

The MkDocs site in `docs/` deliberately contains a broken link and a reference to a
removed symbol. Tests initialize Git only in temporary copies of this fixture.
