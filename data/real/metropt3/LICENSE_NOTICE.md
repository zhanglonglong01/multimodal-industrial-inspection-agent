# MetroPT-3 Derived Sensor Windows

The CSV files in `windows/` are derived from the **MetroPT-3 Dataset**:

- Creators: Narjes Davari, Bruno Veloso, Rita P. Ribeiro, and Joao Gama
- Dataset DOI: <https://doi.org/10.24432/C5VW3R>
- UCI record: <https://archive.ics.uci.edu/dataset/791/metropt%2B3%2B>
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/) (CC BY 4.0)

Project modifications are limited to selecting two six-hour windows, renaming four columns, preserving the source clock values with a documented timezone assumption, and calculating one-minute arithmetic means. The original 208 MB archive is not redistributed by this repository. Exact source and derived-file hashes are recorded in `manifest.json`.

These files are real operational railway Air Production Unit sensor measurements. They are not factory production-line data, synchronized visual inspection data, or verified point-level anomaly labels.
