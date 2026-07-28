# NOMO Doomscroller

See SPEC.md for the full build spec — implement it precisely; ask before deviating.

- Never commit secrets or API keys; all config comes from env vars (see SPEC.md §11).
- notion_source.py only ever reads the Partners DB — never write to it.
- Run tests before considering any stage complete.