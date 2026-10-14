# Development holdout: Cooperatives registered in ACT

The final runtime was frozen before native records or field definitions were fetched. See [provenance](provenance.json) for download times, original URLs, licences, hashes and the runtime binding.

- Publisher: Access Canberra; CC BY 4.0 International.
- [Official dataset](https://www.data.act.gov.au/d/758m-5a8y).
- `evidence/native.csv`: the complete original CSV, unchanged (17 rows).
- `catalogue_metadata.json`: original official metadata response.
- `evidence/official-metadata.txt`: a mechanical projection of the dataset description, licence and every original non-system column definition. It adds no mapping hints.
- `request.json`: native inputs, original metadata, fixed ontology and empty required-fields list; the harness and model must discover the mappings.

Dataset-level catalogue metadata had been seen previously; no retained native development/model-test history was found before selection. This is one development holdout, not a claim about model pretraining or broad unseen-source performance. Source-agreement scoring labels are kept outside the inference evidence directory and frozen before the model run. They are not human gold.

The earlier NoWaste holdout belongs to the first attempted runtime; its input, results and full trace are retained in the evaluation archive and are not reused as a holdout for the final runtime.
