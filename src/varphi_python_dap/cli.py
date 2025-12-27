from pathlib import Path
import typer


def varphi_python_dap(
    input_file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to input Varphi source file",
    ),
):
    """
    Compile a Varphi source code file to a Python Debug Adapter Protocol (DAP) server.

    The output of this command is a Python script that, when run, listens
    for DAP JSON-RPC messages on stdin/stdout.
    """
    from .compiler import VarphiToPythonDAPCompiler

    compiler = VarphiToPythonDAPCompiler()
    compiler.set_source_path(str(input_file))
    source_code = input_file.read_text(encoding="utf-8")
    compiled_code = compiler.compile(source_code)
    typer.echo(compiled_code)


def main():
    typer.run(varphi_python_dap)


if __name__ == "__main__":
    main()
