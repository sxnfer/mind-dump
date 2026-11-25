"""The two kinds of failure a job can meet (D35). They live here, not in worker.py, so the
model clients (embeddings.py, describe.py) and the PDF reader (filetext.py) can raise them
without importing the worker.
"""


class TemporaryError(Exception):
    """Asking again may help (D35): the job is retried."""


class PermanentError(Exception):
    """Asking again gives the same answer (D35): the job is dead at once."""
