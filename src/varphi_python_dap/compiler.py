import os
from typing import List, Set, Optional
from varphi_devkit import VarphiCompiler, VarphiSyntaxError, VarphiTransition

# Updated template to accept raw source path
TEMPLATE = """\
import argparse
from varphi_python.lib import State, Instruction
from varphi_python_dap.lib import DAPServer

# --- State Definitions ---
{state_definitions}

# --- Instruction Definitions ---
{instruction_definitions}

# --- Runtime Setup ---
initial_state = {initial_state}
k = {num_tapes}
# The absolute path to the original .vp file for source mapping
ORIGINAL_SOURCE_PATH = r"{original_source_path}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--tapes', nargs='*', help='Initial values for tapes', default=[])
    args, unknown = parser.parse_known_args()
    
    input_tapes = args.tapes
    while len(input_tapes) < k:
        input_tapes.append("")

    server = DAPServer(k, initial_state, input_tapes, ORIGINAL_SOURCE_PATH)
    server.run_event_loop()
"""


class VarphiToPythonDAPCompiler(VarphiCompiler):
    _initial_state: Optional[str]
    _seen_states: Set[str]
    _instructions_code: List[str]
    _number_of_tapes: Optional[int]
    _source_path: str

    def __init__(self):
        super().__init__()
        self._initial_state = None
        self._seen_states = set()
        self._instructions_code = []
        self._number_of_tapes = None
        self._source_path = "unknown.vp"

    def set_source_path(self, path: str) -> None:
        """
        Sets the path of the source file being compiled.
        This path is embedded into the generated debugger to support source mapping.
        """
        self._source_path = path

    def handle_transition(self, t: VarphiTransition):
        if self._number_of_tapes is None:
            self._number_of_tapes = len(t.read_symbols)
        elif len(t.read_symbols) != self._number_of_tapes:
            raise VarphiSyntaxError("Tape count mismatch.", t.line_number, 0)

        self._seen_states.add(t.current_state)
        self._seen_states.add(t.next_state)
        if self._initial_state is None:
            self._initial_state = t.current_state

        read_str = "(" + ", ".join(f"'{s}'" for s in t.read_symbols)
        read_str += ",)" if len(t.read_symbols) == 1 else ")"

        write_str = "(" + ", ".join(f"'{s}'" for s in t.write_symbols)
        write_str += ",)" if len(t.write_symbols) == 1 else ")"

        code = (
            f"{t.current_state}.add_instruction(\n"
            f"    read_symbols={read_str},\n"
            f"    instruction=Instruction(\n"
            f"    next_state={t.next_state},\n"
            f"    write_symbols={write_str},\n"
            f"    shift_directions={t.shift_directions},\n"
            f"    line_number={t.line_number}\n"
            f"))\n"
        )
        self._instructions_code.append(code)

    def generate_compiled_program(self) -> str:
        state_defs = "\n".join(
            f"{name} = State('{name}')" for name in self._seen_states
        )
        instr_defs = "\n".join(self._instructions_code)

        # Escape backslashes for Windows path compatibility in the Python string literal
        sanitized_path = os.path.abspath(self._source_path).replace("\\", "\\\\")

        return TEMPLATE.format(
            state_definitions=state_defs,
            instruction_definitions=instr_defs,
            initial_state=self._initial_state,
            num_tapes=self._number_of_tapes,
            original_source_path=sanitized_path,
        )
