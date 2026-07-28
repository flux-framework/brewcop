# Thin task-runner over the real tools -- it delegates, it does not
# reimplement.  pybuild (via debian/rules + pyproject.toml) does the actual
# build; pytest runs the suite.  There is nothing to compile, so `all` is a
# deliberate no-op default.

.PHONY: all check deb clean

all:
	@:

check:
	python3 -m pytest -q

# Strict build: no -d, so a clean builder (or the target) must have the
# apt build-deps.  dpkg-buildpackage drops artifacts in the PARENT dir (the
# Debian convention), so report the resolved path once it is done.
#
# Depend on clean so every deb is built from scratch: a stale debian/brewcop
# staging tree once shipped old code in a "rebuilt" package, which is nearly
# impossible to diagnose from the running unit.  A from-scratch build is cheap
# here (pure Python, nothing to compile), so always pay it.
deb: clean
	dpkg-buildpackage -b -us -uc
	@# _all matches debian/control's Architecture: all (pure Python).
	@echo "deb built: $(abspath ..)/$$(dpkg-parsechangelog -S Source)_$$(dpkg-parsechangelog -S Version)_all.deb"

clean:
	debian/rules clean
	rm -rf build dist *.egg-info
