# Search eval results

> Written by `python -m mind_dump.eval` (M4). Do not edit by hand: run it again.
> How it works: `backend/src/mind_dump/eval.py`. Corpus and questions: `backend/eval/`.

- Date: 2026-11-25 01:16 UTC
- Commit: 93706fe
- Corpus: 12 documents, 30 chunks, 20 questions
- Embedding model: none (no VOYAGE_API_KEY)
- A hit: a chunk of the expected document that contains the expected passage. recall@5: share of questions with a hit in the top 5. MRR@10: mean of 1/rank of the first hit (0 if none in the top 10).

| Mode | recall@5 | MRR@10 | Hits in top 5 |
| --- | --- | --- | --- |
| keyword | 0.80 | 0.71 | 16/20 |
| vector | pending | pending | needs a run with `VOYAGE_API_KEY` |
| hybrid | pending | pending | needs a run with `VOYAGE_API_KEY` |

The vector and hybrid rows are pending: this run had no embedding model, so only keyword search could be measured. Run the eval with `VOYAGE_API_KEY` set to fill them in. No number here is estimated.

## Rank of the right passage, per question

A number is the rank of the first right chunk (top 10). miss: not in the top 10; miss (other chunk at r): the right document came back at rank r, but with a chunk that doesn't hold the passage.

| # | Kind | Question | keyword |
| --- | --- | --- | --- |
| 1 | words | What does a credit's serial number encode? | 1 |
| 2 | paraphrase | Once a company has used up an offset, can it sell it on to someone else? | 1 |
| 3 | identifier | How many credits from project MD-2291 went to the buffer pool? | 1 |
| 4 | words | What is the baseline scenario? | 1 |
| 5 | identifier | MD-AM-0042 flow meter calibration | 1 |
| 6 | paraphrase | If the rulebook changes, do schemes already under way have to follow the new edition straight away? | 4 |
| 7 | words | investment analysis benchmark for the internal rate of return | 1 |
| 8 | paraphrase | Can a scheme count as extra when a shortage of trained staff held it back rather than cost? | 7 |
| 9 | identifier | common practice threshold of twenty percent | 1 |
| 10 | words | How many times can a renewable crediting period be renewed? | 2 |
| 11 | paraphrase | Why do some schemes earn fewer offsets when their window comes up for extension? | miss (other chunk at 1) |
| 12 | paraphrase | What happens to the carbon held in woodland if it catches fire? | 1 |
| 13 | words | What share of credits goes into the buffer pool? | 1 |
| 14 | paraphrase | If a long rerun of old data dies halfway, how do I avoid redoing everything? | 3 |
| 15 | paraphrase | Does guarding one woodland just push the tree felling somewhere else? | miss |
| 16 | words | delete then insert inside one transaction | 1 |
| 17 | paraphrase | How does the system find out that a worker crashed while holding a job? | 1 |
| 18 | identifier | FOR UPDATE SKIP LOCKED | 1 |
| 19 | paraphrase | Which approach still lets me see where a client used to live after they move house? | miss |
| 20 | identifier | What does alert DQ-117 mean? | 1 |
