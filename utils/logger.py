from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

def log_info(msg: str):
    console.print(f"[cyan]ℹ[/cyan] {msg}")

def log_success(msg: str):
    console.print(f"[green]✓[/green] {msg}")

def log_error(msg: str):
    console.print(f"[red]✗[/red] {msg}")

def log_phase(phase: int, title: str):
    console.print(f"\n[bold magenta]━━━ PHASE {phase} : {title} ━━━[/bold magenta]\n")

def make_spinner(label: str) -> Progress:
    return Progress(SpinnerColumn(), TextColumn(label), transient=True)
