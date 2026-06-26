# Makefile — run the Amsterdam street-view pipeline notebooks in order.
#
# Each notebook is executed top-to-bottom with `jupyter nbconvert --execute`.
# If any cell raises, nbconvert exits non-zero and make stops there, so the
# pipeline halts at the first failure (the half-run notebook keeps the outputs
# it produced up to the failing cell, which is handy for debugging).
#
#   make            # run notebooks 1 -> 8 in order, in place
#   make nb4        # run a single notebook (here 4); see targets below
#   make satellite  # run 9a -> 9b (needs GEE auth + a manual GeoTIFF; see CLAUDE.md)
#   make install-hooks  # one-off: enable the pre-commit notebook-map stripper
#
# Every run is fresh: the targets are phony, so `make` always re-executes the
# whole chain (notebooks have no stable file-timestamp dependencies to track).
#
# Override on the command line, e.g.:
#   make KERNEL=amsterdam-verify        # use the registered verify kernel
#   make TIMEOUT=3600                   # 1h per-cell cap instead of unlimited
#
# Notes:
#  * Execution is IN PLACE: it rewrites the .ipynb files with fresh outputs.
#  * Activate the conda env first: `conda activate streetview-deprivation-amsterdam`.
#  * Before a real full-scale run, see the "Start here" steps in CLAUDE.md
#    (notebook 1 just drives panorama_acquisition.py; pick global_k after nb5).

NB_DIR  ?= notebooks
JUPYTER ?= jupyter
KERNEL  ?= python3
TIMEOUT ?= -1          # seconds per cell; -1 = no limit

# Reusable execution command.
RUN = $(JUPYTER) nbconvert --to notebook --execute --inplace \
        --ExecutePreprocessor.timeout=$(TIMEOUT) \
        --ExecutePreprocessor.kernel_name=$(KERNEL)

# Core pipeline, in order.
NB1 = $(NB_DIR)/1-SampleStreetNetwork.ipynb
NB2 = $(NB_DIR)/2-CalculateEmbeddings.ipynb
NB3 = $(NB_DIR)/3-ProcessEmbeddings+FindMedianEmbeddingPerLSOA.ipynb
NB4 = $(NB_DIR)/4-RunModelsWithMedianEmbedding.ipynb
NB5 = $(NB_DIR)/5-IdentifyOptimalClusterNumber.ipynb
NB6 = $(NB_DIR)/6-TestModelOverClusters_ControlledForSampleSize.ipynb
NB7 = $(NB_DIR)/7-RunModels_ForEachOfNClusters.ipynb
NB8 = $(NB_DIR)/8-Prediction_Deprivation_domains.ipynb

# Satellite branch (manual prerequisites — not part of the default run).
NB9A = $(NB_DIR)/9a-DownloadAlphaEarthEmbeddings.ipynb
NB9B = $(NB_DIR)/9b-RunModelWithAlphaEarthEmbeddings.ipynb

.PHONY: all satellite install-hooks nb1 nb2 nb3 nb4 nb5 nb6 nb7 nb8 nb9a nb9b

# One-off per clone: point git at the version-controlled hooks in .githooks, so
# the pre-commit hook strips bulky folium/leaflet map outputs from notebooks
# before they are committed (keeps matplotlib plots; see tools/strip_map_outputs.py).
install-hooks:
	git config core.hooksPath .githooks
	@echo "Git hooks enabled (core.hooksPath -> .githooks)."
	@echo "Interactive-map outputs will be stripped from notebooks on commit."

# Default: run 1 -> 9b in order, stopping at the first failure.
# The phony targets are chained as prerequisites so they always run fresh and
# in sequence; make aborts as soon as one returns non-zero. 9a/9b come last, so
# if they fail (e.g. GEE auth / the manual GeoTIFF isn't there yet) everything
# up to and including notebook 8 has already run.
all: nb1 nb2 nb3 nb4 nb5 nb6 nb7 nb8 nb9a nb9b
	@echo "=== Pipeline complete (notebooks 1-9b) ==="

nb1:
	@echo "=== Running $(NB1) ===" && $(RUN) $(NB1)
nb2: nb1
	@echo "=== Running $(NB2) ===" && $(RUN) $(NB2)
nb3: nb2
	@echo "=== Running $(NB3) ===" && $(RUN) $(NB3)
nb4: nb3
	@echo "=== Running $(NB4) ===" && $(RUN) $(NB4)
nb5: nb4
	@echo "=== Running $(NB5) ===" && $(RUN) $(NB5)
nb6: nb5
	@echo "=== Running $(NB6) ===" && $(RUN) $(NB6)
nb7: nb6
	@echo "=== Running $(NB7) ===" && $(RUN) $(NB7)
nb8: nb7
	@echo "=== Running $(NB8) ===" && $(RUN) $(NB8)

# Satellite branch: run on its own, after GEE auth + the manual GeoTIFF download.
satellite: nb9a nb9b
	@echo "=== Satellite branch complete (9a-9b) ==="
nb9a:
	@echo "=== Running $(NB9A) ===" && $(RUN) $(NB9A)
nb9b: nb9a
	@echo "=== Running $(NB9B) ===" && $(RUN) $(NB9B)
