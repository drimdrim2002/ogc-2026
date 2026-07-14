OGC 2026 Optimization Challenge
================================

Contents
--------
  alg_tester/    Algorithm testing tool.
                 See alg_tester/README.txt for details.
  baseline/      Baseline algorithm template.
                 See baseline/README.txt for details.
  ogc2026_env.yml  Conda environment definition.

Environment setup (once)
------------------------
  conda env create -f ogc2026_env.yml

  We recommend Miniforge as the conda distribution:
    https://github.com/conda-forge/miniforge

Quick start
-----------
  Step 1  Set up the conda environment (see above).
  Step 2  Open baseline/ and edit myalgorithm.py.
  Step 3  Test your algorithm with the Algorithm Tester:
            conda activate ogc2026
            cd alg_tester
            python alg_tester_app.py

Create the submission ZIP
-------------------------
  From the repository root, run:

      python scripts/build_submission_zip.py

  This creates dist/ogc2026_submission.zip.  It contains only the required
  top-level myalgorithm.py, the unmodified top-level utils.py, and the
  baseline/solver Python package; it excludes docs, tests, training data,
  experiments, and artifacts.  The script verifies utils.py against the
  organizer-provided checksum, then validates the ZIP layout, integrity, and
  the 15 MB submission-size limit before printing its SHA-256 checksum.  To
  choose another destination:

      python scripts/build_submission_zip.py --output /path/to/submission.zip
