"""Domain exceptions shared by the queue worker and pipeline."""


class PipelineCancelled(Exception):
    """Raised when an operator cancels a job at a safe pipeline checkpoint."""
