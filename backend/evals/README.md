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

## Real conversations (Feature 019, C-11)

`evals/test_real_conversations.py`: the sales flows above check the final stage and the tools used, which let a bad
conversation pass (a real one, from the simulator, repeated «Sí, contamos con…» in every message and asked again for what the
customer had said). This eval reads the conversation the way the customer does. 9 scenarios copied from those captures
(`SCENARIOS`, written by the owner) and 9 written afterwards, not tuned against (`HELD_OUT`). Turn by turn, against what each
customer message actually gave, it checks that the assistant: does not ask again for something already given, does not say the
product is available twice, does not make up sales reasons no tool or catalogue holds, goes to a person in the same turn the last
datum arrives (with what was said written back) and not before, and takes a correction («ya te dije…») instead of repeating the
question. `JUDGE=1` adds a second model that reads each conversation as a customer would (`JUDGE=gate` makes its verdict count).
85 % of the scenarios must pass (measured: 16 to 18 of 18 on almost every run). `REAL_SET=owner|new` picks a set, `REAL_ONLY=<name>` one scenario, `REAL_CONVERSATIONS_REPORT=<file>` writes the transcripts. Model
answers vary: run it more than once before trusting a change, and when you find a bad conversation, add it here before fixing it.
