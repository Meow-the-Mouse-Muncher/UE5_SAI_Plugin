"""Cancel a worker through MRQ/PIE teardown before closing the editor."""
import unreal


STOP_COMMAND = "MatrixCity Cancel"
_handler = None


def is_requested():
    return _handler is not None and _handler.requested


def begin_queue():
    """Record startup before Execute: is_rendering() is false until PIE starts."""
    if _handler is None:
        return True
    if _handler.requested:
        return False
    _handler.awaiting_completion = True
    return True


class _CancellationHandler:
    def __init__(self, executor):
        self.executor = executor
        self.requested = False
        self.closing = False
        self.awaiting_completion = False
        self.cancel_sent = False
        executor.socket_message_recieved_delegate.add_callable_unique(self.on_message)
        executor.on_executor_finished_delegate.add_callable_unique(self.on_queue_finished)
        self.tick_handle = unreal.register_slate_pre_tick_callback(self.on_tick)

    def on_queue_finished(self, _executor, _success):
        self.awaiting_completion = False

    def on_message(self, message):
        if message != STOP_COMMAND or self.requested:
            return
        self.requested = True
        unreal.log_warning("[WorkerStop] Cancelling MRQ before editor shutdown")
        self.executor.send_socket_message("Pipeline Cancelling")
        self.cancel_when_ready()

    def cancel_when_ready(self):
        if not self.requested or self.cancel_sent:
            return
        # Execute queues PIE startup before is_rendering() becomes true. A
        # cancellation at that point cannot shut down the not-yet-created
        # pipeline; defer until it exists and then wait for executor completion.
        if self.awaiting_completion and not self.executor.is_rendering():
            return
        self.cancel_sent = True
        # The engine cancellation fix uses RequestShutdown(true), allowing any
        # reentrant Slate callback to return before MRQ tears down the shot.
        # Keep waiting for executor completion and PIE teardown on later ticks.
        self.executor.cancel_all_jobs()

    def on_tick(self, _delta_seconds=0.0):
        if self.closing:
            return
        # UE 5.6 PIE removes all executor OnBeginFrame delegates when a queue
        # finishes, including its socket reader. Keep polling across OCC/GT
        # queues. The native reader is nonblocking and consumes each packet
        # once, so an additional engine-frame poll does not duplicate commands.
        self.executor.on_begin_frame()
        self.cancel_when_ready()
        if self.requested and not self.awaiting_completion and not self.executor.is_rendering():
            self.closing = True
            unreal.unregister_slate_pre_tick_callback(self.tick_handle)
            self.executor.send_socket_message("Pipeline Cancelled")
            unreal.log_warning("[WorkerStop] MRQ stopped; closing editor through EndPlayMap")
            unreal.SystemLibrary.quit_editor()


def install(executor):
    global _handler
    if _handler is not None:
        raise RuntimeError("Worker cancellation handler already installed")
    _handler = _CancellationHandler(executor)
