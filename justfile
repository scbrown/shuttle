# shuttle — quiet by default; you see errors and warnings.

# Parse every source file and run the size ratchet.
check:
    @python3 -m py_compile shuttle/*.py tests/*.py
    @bash scripts/check-file-size.sh

# check, then the unit suite.
test: check
    @python3 -m unittest discover -s tests -q

# The cross-repo acceptance: a live quipu, a full run, a freeze.
e2e quipu_bin="quipu":
    @bash scripts/e2e_slice.sh {{quipu_bin}}
