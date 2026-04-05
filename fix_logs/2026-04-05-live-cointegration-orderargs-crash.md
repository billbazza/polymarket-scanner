# 2026-04-05 live cointegration order-args crash

- Fixed the live Polymarket order path in `execution.py` so cointegration orders no longer pass raw dict payloads into `py_clob_client.create_and_post_order()`. The client expects `OrderArgs` objects with `token_id`, which was the source of the `dict object has no attribute token_id` penny/live crash.
- Added a shared live-order adapter that normalizes token/price/size/side inputs, constructs `OrderArgs`, and logs the exact failing leg plus normalized order input when exchange submission fails.
- Applied the same adapter to the other live order call sites in `execution.py` so the object-vs-dict assumption is removed consistently across open/close/manual live orders.
- Added regression coverage in `tests/test_live_cointegration_execution.py` to verify live cointegration uses token ids rather than market-question strings and that a leg-specific exchange failure reports the failing leg and input payload.
