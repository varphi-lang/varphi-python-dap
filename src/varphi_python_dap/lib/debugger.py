from __future__ import annotations
import sys
import json
import threading
import queue
import os
import io
from typing import Dict, Any, List, Optional
from varphi_python.lib import TuringMachine, State, Tape
from varphi_devkit import BLANK


class DAPStdout(io.TextIOBase):
    """Redirects stdout to the VS Code Debug Console (Output category)."""
    server: DAPServer
    buffer: io.StringIO

    def __init__(self, server_instance):
        self.server = server_instance
        self.buffer = io.StringIO()

    def write(self, s: str):
        if self.server:
            self.server._send_event("output", {"category": "stdout", "output": s})
        return len(s)


class DAPStderr(io.TextIOBase):
    """Redirects stderr to the VS Code Debug Console (Stderr category, usually red)."""
    server: DAPServer
    buffer: io.StringIO

    def __init__(self, server_instance):
        self.server = server_instance
        self.buffer = io.StringIO()

    def write(self, s: str):
        if self.server:
            self.server._send_event("output", {"category": "stderr", "output": s})
        return len(s)


class DAPServer:
    tm: TuringMachine
    breakpoints: dict[int, bool]
    running: bool
    step_granularity: Optional[str]
    steps_count: int
    input_queue: queue.Queue
    original_source_path: str

    _seq: int = 0
    _write_lock: threading.Lock

    def __init__(
        self,
        k: int,
        initial_state: State,
        input_tapes: List[str],
        original_source_path: str,
    ):
        self.original_source_path = os.path.abspath(original_source_path)
        self._write_lock = threading.Lock()

        tapes = tuple(Tape(t) for t in input_tapes)
        self.tm = TuringMachine(k, tapes, initial_state)

        self.breakpoints = {}
        self.running = False
        self.step_granularity = "instruction"
        self.steps_count = 0

        self.input_queue = queue.Queue()

        # Redirect stdout and stderr to DAP console
        sys.stdout = DAPStdout(self)
        sys.stderr = DAPStderr(self)

        self.reader_thread = threading.Thread(target=self._read_input_loop, daemon=True)
        self.reader_thread.start()

        # Initialize the machine view
        try:
            self.tm.peek()
        except Exception:
            # If peek fails immediately (e.g. invalid initial state), we catch it later or let it slide
            pass

    def _send_message(self, msg: Dict[str, Any]):
        """Sends a DAP-compliant message to the real stdout."""
        with self._write_lock:
            self._seq += 1
            msg["seq"] = self._seq
            json_msg = json.dumps(msg)
            encoded = json_msg.encode("utf-8")
            # We write to __stdout__ because sys.stdout is redirected
            sys.__stdout__.buffer.write(
                f"Content-Length: {len(encoded)}\r\n\r\n".encode("utf-8")
            )
            sys.__stdout__.buffer.write(encoded)
            sys.__stdout__.buffer.flush()

    def _read_input_loop(self):
        buffer = sys.stdin.buffer
        while True:
            try:
                content_length = 0
                while True:
                    line = buffer.readline()
                    if not line:
                        return

                    line_str = line.decode("utf-8").strip()
                    if not line_str:
                        break

                    if line_str.startswith("Content-Length:"):
                        content_length = int(line_str.split(":", 1)[1].strip())

                if content_length > 0:
                    content = buffer.read(content_length)
                    self.input_queue.put(json.loads(content))
            except Exception:
                break

    def run_event_loop(self):
        while True:
            try:
                timeout = 0.0 if self.running else None
                req = self.input_queue.get(timeout=timeout)
                self._handle_request(req)
            except queue.Empty:
                pass

            if self.running:
                self._step_machine()

    def _print_halt_report(self):
        """Calculates statistics and prints the halt report to the Debug Console."""
        total_space = sum(h.space_complexity() for h in self.tm.heads)
        
        report = []
        report.append(f"HALTED at state '{self.tm.state.name}'")
        report.append(f"Time taken: {self.steps_count} steps")
        report.append(f"Space used: {total_space} cells")
        report.append(f"Number of tapes: {len(self.tm.heads)}")

        for i, head in enumerate(self.tm.tapes):
            # Extract tape content from min_index to max_index
            tape_dict = head.tape._tape
            if not tape_dict:
                content = ""
            else:
                min_idx = min(tape_dict.keys())
                max_idx = max(tape_dict.keys())
                # Construct string, replacing missing spots with BLANK if necessary
                content = "".join(tape_dict.get(k, BLANK) for k in range(min_idx, max_idx + 1))
            
            report.append(f"Tape {i + 1}: {content.strip("_")}")

        # Join with newlines and print. This goes to DAPStdout -> VS Code Debug Console
        print("\n" + "\n".join(report) + "\n")

    def _step_machine(self):
        # Check termination
        if self.tm._next_instruction is None:
            self.running = False
            self._print_halt_report()
            self._send_event("terminated")
            self._send_event("exited", {"exitCode": 0})
            return

        # Check breakpoints (unless single-stepping)
        if self.step_granularity != "step":
            current_line = self.tm._next_instruction.line_number
            if self.breakpoints.get(current_line, False):
                self.running = False
                self._send_event(
                    "stopped",
                    {"reason": "breakpoint", "threadId": 1, "allThreadsStopped": True},
                )
                return

        # Execute Step (with Error Handling)
        try:
            self.tm.step()
            self.steps_count += 1
            has_next = self.tm.peek()
        except Exception as e:
            self.running = False
            # Print error to Debug Console (red)
            sys.stderr.write(f"\nRUNTIME ERROR: {str(e)}\n")
            # Notify VS Code to pause on exception
            self._send_event("stopped", {
                "reason": "exception",
                "description": "Paused on exception",
                "text": str(e),
                "threadId": 1
            })
            return

        # Handle Step Pause
        if self.step_granularity == "step":
            self.running = False
            self.step_granularity = None
            reason = "step" if has_next else "pause"
            self._send_event("stopped", {"reason": reason, "threadId": 1})

    def _send_response(
        self, req: Dict, success: bool, body: Any = None, message: str = None
    ):
        resp = {
            "type": "response",
            "request_seq": req["seq"],
            "command": req["command"],
            "success": success,
        }
        if body:
            resp["body"] = body
        if message:
            resp["message"] = message
        self._send_message(resp)

    def _send_event(self, event: str, body: Any = None):
        msg = {"type": "event", "event": event}
        if body:
            msg["body"] = body
        self._send_message(msg)

    def _handle_request(self, req: Dict[str, Any]):
        try:
            if req["type"] == "request":
                cmd = req["command"]
                handler = getattr(self, f"handle_{cmd}", None)
                if handler:
                    handler(req)
                else:
                    self._send_response(
                        req, False, message=f"Command '{cmd}' not implemented"
                    )
        except Exception as e:
            self._send_response(req, False, message=str(e))

    # --- Handlers ---

    def handle_initialize(self, req):
        self._send_response(
            req,
            True,
            {
                "supportsConfigurationDoneRequest": True,
                "supportsSetVariable": False,
                "supportsValueFormattingOptions": False,
                "supportsExceptionInfoRequest": True,
            },
        )
        self._send_event("initialized")

    def handle_launch(self, req):
        self._send_response(req, True)

    def handle_setBreakpoints(self, req):
        lines = req["arguments"].get("lines", [])
        self.breakpoints = {ln: True for ln in lines}
        verified_bps = [{"verified": True, "line": ln} for ln in lines]
        self._send_response(req, True, {"breakpoints": verified_bps})

    def handle_configurationDone(self, req):
        self._send_response(req, True)
        self._send_event("stopped", {"reason": "entry", "threadId": 1})

    def handle_threads(self, req):
        self._send_response(req, True, {"threads": [{"id": 1, "name": "Main Thread"}]})

    def handle_stackTrace(self, req):
        if self.tm._next_instruction:
            line = self.tm._next_instruction.line_number
            name = f"State: {self.tm.state.name}"
        else:
            line = 0
            name = f"HALTED (State: {self.tm.state.name})"

        self._send_response(
            req,
            True,
            {
                "stackFrames": [
                    {
                        "id": 1,
                        "name": name,
                        "line": line,
                        "column": 1,
                        "source": {
                            "name": os.path.basename(self.original_source_path),
                            "path": self.original_source_path,
                            "sourceReference": 0,
                        },
                        "presentationHint": "normal",
                    }
                ],
                "totalFrames": 1,
            },
        )

    def handle_scopes(self, req):
        # Unified scope "Machine State"
        self._send_response(
            req,
            True,
            {
                "scopes": [
                    {
                        "name": "Machine State",
                        "variablesReference": 1,
                        "expensive": False,
                    }
                ]
            },
        )

    def handle_variables(self, req):
        vars_list = []

        # Machine Metrics
        vars_list.append(
            {
                "name": "Current State",
                "value": str(self.tm.state.name),
                "type": "string",
                "variablesReference": 0,
            }
        )
        vars_list.append(
            {
                "name": "Time taken",
                "value": str(self.steps_count),
                "type": "integer",
                "variablesReference": 0,
            }
        )

        total_space = sum(h.space_complexity() for h in self.tm.heads)
        vars_list.append(
            {
                "name": "Space used",
                "value": str(total_space),
                "type": "integer",
                "variablesReference": 0,
            }
        )

        # Tape Visualizations
        for i, head in enumerate(self.tm.heads):
            center = head.index
            raw_dict = head.tape._tape

            # Using .get() for non-mutating access
            left_ctx = "".join(
                raw_dict.get(x, BLANK) for x in range(center - 5, center)
            )
            curr_val = raw_dict.get(center, BLANK)
            right_ctx = "".join(
                raw_dict.get(x, BLANK) for x in range(center + 1, center + 6)
            )

            tape_display = f"{left_ctx}[{curr_val}]{right_ctx}"

            vars_list.append(
                {
                    "name": f"Tape {i + 1}",
                    "value": tape_display,
                    "type": "string",
                    "variablesReference": 0,
                }
            )

        self._send_response(req, True, {"variables": vars_list})

    def handle_next(self, req):
        self.step_granularity = "step"
        self.running = True
        self._send_response(req, True)

    def handle_stepIn(self, req):
        self.handle_next(req)

    def handle_continue(self, req):
        self.step_granularity = None
        self.running = True
        self._send_response(req, True)

    def handle_pause(self, req):
        self.running = False
        self._send_response(req, True)
        self._send_event("stopped", {"reason": "pause", "threadId": 1})

    def handle_disconnect(self, req):
        self.running = False
        self._send_response(req, True)
        sys.exit(0)

    def handle_exceptionInfo(self, req):
        self._send_response(req, True, {
            "exceptionId": "runtime_error",
            "description": "An error occurred during execution",
            "breakMode": "always"
        })