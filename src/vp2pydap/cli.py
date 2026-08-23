import sys
from pathlib import Path
from typing import Optional
import typer


def vp2pydap(
    input_file: Optional[Path] = typer.Argument(
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to input Varphi source file. If omitted, reads from standard input.",
    ),
):
    """
    Compile a Varphi source code file to a Python Debug Adapter Protocol (DAP) server.

    The output of this command is a Python script that, when run, listens
    for DAP JSON-RPC messages on stdin/stdout.
    """
    from .compiler import VarphiToPythonDAPCompiler

    compiler = VarphiToPythonDAPCompiler()
    
    if input_file:
        compiler.set_source_path(str(input_file))
        source_code = input_file.read_text(encoding="utf-8")
    else:
        # Fallback to stdin if no file is provided
        compiler.set_source_path("<stdin>")
        source_code = sys.stdin.read()

    compiled_code = compiler.compile(source_code)
    typer.echo(compiled_code)


def main():
    typer.run(vp2pydap)


if __name__ == "__main__":
    main()