import os
from varphi_devkit import (
    VarphiCompiler,
    BuiltinSymbol,
    Variable,
    Direction,
    Character,
    ReadWriteTupleElement,
)

TEMPLATE = """\
import argparse
from vp2py.lib import State
from varphi_devkit import BuiltinSymbol, Direction, Variable, Character, VarphiTransition
from vp2pydap.lib import DAPServer

# --- State Registry ---
state_registry = {{
{state_registry_entries}
}}

# --- Transition Definitions ---
{instruction_definitions}

# --- Runtime Setup ---
initial_state = state_registry['{initial_state}']
k = {num_tapes}
ORIGINAL_SOURCE_PATH = "{original_source_path}"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--tapes', nargs='*', help='Initial values for tapes', default=[])
    args, unknown = parser.parse_known_args()
    
    input_tapes = args.tapes
    while len(input_tapes) < k:
        input_tapes.append("")

    server = DAPServer(k, initial_state, state_registry, input_tapes, ORIGINAL_SOURCE_PATH)
    server.run_event_loop()
"""


class VarphiToPythonDAPCompiler(VarphiCompiler):
    _source_path: str

    def __init__(self):
        super().__init__()
        self._source_path = "unknown.vp"

    def set_source_path(self, path: str) -> None:
        """
        Sets the path of the source file being compiled.
        This path is embedded into the generated debugger to support source mapping.
        """
        self._source_path = path

    def _format_symbol(self, s: ReadWriteTupleElement) -> str:
        if s == BuiltinSymbol.BLANK:
            return "BuiltinSymbol.BLANK"
        if isinstance(s, Variable):
            return f"Variable({s.id})"
        if isinstance(s, Character):
            return f"Character({repr(s.value)})"
        raise ValueError(f"Unknown symbol type: {type(s)}")

    def _format_direction(self, d: Direction) -> str:
        return f"Direction.{d.name}"

    def _generate_compiled_program(self) -> str:
        all_states = self.states

        registry_entries = "\n".join(
            f"    '{name}': State('{name}')," for name in all_states
        )

        instructions_code = []
        for transitions in self.ir.values():
            for t in transitions:
                read_str = "(" + ", ".join(self._format_symbol(s) for s in t.read_symbols)
                read_str += ",)" if len(t.read_symbols) == 1 else ")"

                write_str = "(" + ", ".join(self._format_symbol(s) for s in t.write_symbols)
                write_str += ",)" if len(t.write_symbols) == 1 else ")"

                shift_str = "(" + ", ".join(self._format_direction(d) for d in t.shift_directions)
                shift_str += ",)" if len(t.shift_directions) == 1 else ")"

                code = (
                    f"state_registry['{t.current_state}'].add_transition(\n"
                    f"    VarphiTransition(\n"
                    f"        current_state='{t.current_state}',\n"
                    f"        read_symbols={read_str},\n"
                    f"        next_state='{t.next_state}',\n"
                    f"        write_symbols={write_str},\n"
                    f"        shift_directions={shift_str},\n"
                    f"        line_number={t.line_number}\n"
                    f"    )\n"
                    f")"
                )
                instructions_code.append(code)

        sanitized_path = os.path.abspath(self._source_path).replace("\\", "\\\\")

        return TEMPLATE.format(
            state_registry_entries=registry_entries,
            instruction_definitions="\n\n".join(instructions_code),
            initial_state=self.initial_state,
            num_tapes=self._tape_count,
            original_source_path=sanitized_path,
        )