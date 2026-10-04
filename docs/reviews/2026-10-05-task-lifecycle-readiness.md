# Task lifecycle: wait for closed detail before opening history

The final mobile Firefox failure was a test-readiness race. The server had
already closed the workflow, but the visible detail still showed the previous
review state when the test clicked the history disclosure. The close update
then removed the review actions and moved that disclosure before the click
completed, leaving it closed.

The lifecycle test now waits for the rendered success status `Закрыта` in the
issue detail before opening the conversation. This observes the authoritative
detail snapshot without adding a sleep or retrying a click. It does not change
production workflow, cache, or service-worker behavior.
