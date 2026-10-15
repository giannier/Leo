# Agent evals

Placeholder. The golden-conversation dataset and eval runner (TASK-016)
land once `agent-core` v0 exists (TASK-014/015). Per the constitution's
quality standard, any change to prompts, tools, or the underlying model
must run `make evals` in CI with zero regressions before merge.

## Sales flows (Feature 004, TASK-447)

`evals/test_sales_flows.py`: 24 scripted conversations (8 per process template)
through the whole turn pipeline in the simulator. Each turn is checked for at most
3 outbound messages, no repeated file, never "confirmed" before an operator, and
only real amounts in soles; each case for its final stage and the tools it needed.
A template passes with 90 % of its cases. `SALES_FLOWS_ONLY=productos` runs one
template; the report goes to `$SALES_FLOWS_REPORT`.

## Knowledge retrieval (Feature 006, TASK-604)

`evals/test_knowledge_recall.py`: two demo businesses (`evals/knowledge_recall.py`), their
documents chunked and embedded as the worker does, and customer-style questions. Each
question passes when the answering passage is among the 5 that `retrieve` returns. A
business passes with recall@5 ≥ 0.8 and recall@1 ≥ 0.7 (the corpus is small, so the top 5
alone says little). Needs the real embedding model.
