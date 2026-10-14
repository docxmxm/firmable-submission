# Data sources and attribution

The source data remains subject to the publishers’ licences. No publisher endorsement is implied. Original source notices and official documentation are preserved alongside the native input snapshots in [the six source directories](.). Generated observations, links and profiles are derived outputs; the agent-generated configs record their field-level transformations and omissions.

| Source | Publisher / attribution | Declared licence | Snapshot selection |
| --- | --- | --- | --- |
| [ASIC Company Dataset](https://data.gov.au/data/dataset/7b8656f9-606d-4337-af29-66b89b2eeefb) | Australian Securities and Investments Commission (ASIC) | [CC BY 3.0 Australia](http://creativecommons.org/licenses/by/3.0/au/) | Native records from nine frozen targeted DataStore responses, serialized into one array without changing fields/values; deliberately overlap-enriched, not random. |
| [ASIC Business Names Dataset](https://data.gov.au/data/dataset/bc515135-4bb6-4d50-957a-3713709a76d3) | Australian Securities and Investments Commission (ASIC) | [CC BY 3.0 Australia](http://creativecommons.org/licenses/by/3.0/au/) | First 1,000 complete rows of a bounded download prefix; original field strings retained in TSV. |
| [QBCC Licensed Contractors Register](https://www.data.qld.gov.au/dataset/980b6499-c0b4-491b-ba9c-1c7506368a50) | Queensland Building and Construction Commission | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | First 1,000 complete rows of a bounded download prefix; original field strings retained in CSV. |
| [Corporate Tax Transparency](https://data.gov.au/data/dataset/c2524c87-cea4-4636-acac-599a82048a26) | Australian Taxation Office | [CC BY 3.0 Australia](http://creativecommons.org/licenses/by/3.0/au/) | Original 2024–25 XLSX snapshot; no flattened replacement. |
| [Suitable Operator Register](https://www.data.qld.gov.au/dataset/42bad3df-f2e0-4c9c-bef0-0a4328927718) | Environment, Tourism, Science and Innovation | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | Original XLSX snapshot; no flattened replacement. |
| [Contracts Register — 2000 to 2016](https://data.gov.au/data/dataset/05495e07-b6c6-4c82-8be1-b468204e10c3) | ACT Government | [CC BY 4.0 International](https://creativecommons.org/licenses/by/4.0/) | Original native JSON download from ACT view `8wge-3sy9`. |

The six frozen snapshots were acquired in October 2026. The exact URLs, retrieval declarations, original selection notices and hashes are retained with each source. This is an intentionally overlapping demonstration cohort, not a representative random sample of Australian businesses. Source update/retrieval times are not automatically the observation dates of individual business facts.

ASIC’s official PDF snapshots, acquisition receipts and the exact text used by the agent are retained in [source-notices/asic-official-documents](source-notices/asic-official-documents/). PDF bytes match the saved acquisition receipts, and text bytes match the frozen source evidence. Packaging did not re-run PDF extraction or modify the document text. The Company help PDF receipt records 10 October 2026; the Business Names receipt records 11 October 2026.

The catalogue and native resource have some different declarations. ACT’s harvested CKAN entry reported “Other”; the native ACT view and saved source notice specify CC BY 4.0 and “ACT Government” attribution, which is retained here. A later QBCC metadata update does not replace the frozen input. Later Suitable Operators metadata reports 737.5 KiB, while the retained original snapshot is 902,657 bytes; URL equality is not used as proof of identical data. ASIC’s initial CSV metadata time and subsequent targeted-query interval are distinct, so no single acquisition time is assigned to every company row.

The original files retain any embedded notices. Source licences are separate from the code. The full catalogue pool, shortlist and human review evidence preserve the source references used in discovery and review.

## Later evaluation inputs

The current pest/weed holdout and its frozen provenance are retained under [data/unseen-source](../unseen-source/). Later ORIC/APRA experiments and EPA/CER/CRICOS/Aged Care holdouts are separate from the saved six-source outputs; their original inputs, metadata, acquisition provenance and any publisher licence notices are retained with their experiment directories under [runs](../../runs/). These later datasets do not establish generalisation merely by being present in the repository. Earlier ACNC evidence is historical and is not presented as the current holdout.
