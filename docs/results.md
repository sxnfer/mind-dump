# Search eval results

> Written by `python -m mind_dump.eval` (M4). Do not edit by hand: run it again.
> How it works: `backend/src/mind_dump/eval.py`. Corpus and questions: `backend/eval/`.

- Date: 2026-11-25 10:38 UTC
- Commit: 080b292
- Corpus: 12 documents, 30 chunks, 20 questions
- Embedding model: text-embedding-3-small
- A hit: a chunk of the expected document that contains the expected passage. recall@5: share of questions with a hit in the top 5. MRR@10: mean of 1/rank of the first hit (0 if none in the top 10).

| Mode | recall@5 | MRR@10 | Hits in top 5 |
| --- | --- | --- | --- |
| keyword | 0.80 | 0.71 | 16/20 |
| vector | 1.00 | 0.89 | 20/20 |
| hybrid | 1.00 | 0.86 | 20/20 |

## Rank of the right passage, per question

A number is the rank of the first right chunk (top 10). miss: not in the top 10; miss (other chunk at r): the right document came back at rank r, but with a chunk that doesn't hold the passage.

| # | Kind | Question | keyword | vector | hybrid |
| --- | --- | --- | --- | --- | --- |
| 1 | words | What does a credit's serial number encode? | 1 | 1 | 1 |
| 2 | paraphrase | Once a company has used up an offset, can it sell it on to someone else? | 1 | 1 | 1 |
| 3 | identifier | How many credits from project MD-2291 went to the buffer pool? | 1 | 1 | 1 |
| 4 | words | What is the baseline scenario? | 1 | 1 | 1 |
| 5 | identifier | MD-AM-0042 flow meter calibration | 1 | 1 | 1 |
| 6 | paraphrase | If the rulebook changes, do schemes already under way have to follow the new edition straight away? | 4 | 2 | 2 |
| 7 | words | investment analysis benchmark for the internal rate of return | 1 | 1 | 1 |
| 8 | paraphrase | Can a scheme count as extra when a shortage of trained staff held it back rather than cost? | 7 | 1 | 1 |
| 9 | identifier | common practice threshold of twenty percent | 1 | 1 | 1 |
| 10 | words | How many times can a renewable crediting period be renewed? | 2 | 1 | 2 |
| 11 | paraphrase | Why do some schemes earn fewer offsets when their window comes up for extension? | miss (other chunk at 1) | 5 | 5 |
| 12 | paraphrase | What happens to the carbon held in woodland if it catches fire? | 1 | 1 | 1 |
| 13 | words | What share of credits goes into the buffer pool? | 1 | 1 | 1 |
| 14 | paraphrase | If a long rerun of old data dies halfway, how do I avoid redoing everything? | 3 | 1 | 1 |
| 15 | paraphrase | Does guarding one woodland just push the tree felling somewhere else? | miss | 2 | 2 |
| 16 | words | delete then insert inside one transaction | 1 | 1 | 1 |
| 17 | paraphrase | How does the system find out that a worker crashed while holding a job? | 1 | 2 | 2 |
| 18 | identifier | FOR UPDATE SKIP LOCKED | 1 | 1 | 1 |
| 19 | paraphrase | Which approach still lets me see where a client used to live after they move house? | miss | 1 | 1 |
| 20 | identifier | What does alert DQ-117 mean? | 1 | 1 | 1 |

## Answers (M5)

Pending: the answers eval needs a run with `ANTHROPIC_API_KEY` set
(`uv run python -m mind_dump.eval_answers` from `backend/`). No number here is estimated.
